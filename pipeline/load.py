"""Database writes: source documents, runs, line items, amounts, page refs, publishing."""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone

from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .config import ROOT, Account, config_fingerprint, database_url
from .db.models import (
    AppropriationAccount,
    BudgetLineItem,
    IngestionRun,
    LineItemAmount,
    LineItemCostElement,
    LineItemSourceRef,
    Program,
    SourceDocument,
)
from .fetch import FetchedFile
from .link_pages import LinkResult
from .normalize import LineItemRecord


class PublishError(Exception):
    pass


def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url())


def parser_version() -> str:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "pipeline", "config"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        return f"{sha}+dirty" if dirty else sha
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def input_fingerprint(fetched: list[FetchedFile], version: str) -> str:
    h = hashlib.sha256()
    for f in sorted(fetched, key=lambda f: f.url):
        h.update(f"{f.url}\0{f.sha256}\n".encode())
    h.update(version.encode())
    h.update(config_fingerprint().encode())
    return h.hexdigest()


def seed_accounts(session: Session, accounts: dict[str, Account]) -> None:
    stmt = pg_insert(AppropriationAccount).values(
        [
            {"code": a.code, "title": a.title, "service_branch": a.service_branch}
            for a in accounts.values()
        ]
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["code"],
            set_={c: stmt.excluded[c] for c in ("title", "service_branch")},
        )
    )


def upsert_source_documents(
    session: Session, fetched: list[FetchedFile], fiscal_year: int, cycle: str, exhibit: str
) -> dict[str, SourceDocument]:
    """Return {role: SourceDocument}, creating rows for files not seen before."""
    out = {}
    for f in fetched:
        doc = session.scalar(
            select(SourceDocument).where(SourceDocument.url == f.url, SourceDocument.sha256 == f.sha256)
        )
        if doc is None:
            doc = SourceDocument(
                url=f.url, sha256=f.sha256, fiscal_year=fiscal_year, budget_cycle=cycle,
                exhibit_type=exhibit, format=f.format, storage_path=str(f.path),
                page_count=f.page_count, fetched_at=f.fetched_at,
            )
            session.add(doc)
            session.flush()
        out[f.role] = doc
    return out


def find_reusable_run(session: Session, cycle: str, exhibit: str, fingerprint: str) -> IngestionRun | None:
    """A validated or published run built from exactly these inputs, parser and config."""
    return session.scalar(
        select(IngestionRun)
        .where(
            IngestionRun.budget_cycle == cycle,
            IngestionRun.exhibit_type == exhibit,
            IngestionRun.input_fingerprint == fingerprint,
            IngestionRun.status.in_(("validated", "published")),
        )
        .order_by(IngestionRun.id.desc())
        .limit(1)
    )


def _upsert_programs(session: Session, records: list[LineItemRecord]) -> dict[str, int]:
    rows = {}
    for r in records:  # last title wins within the release
        rows[(r.exhibit_family, r.program_key)] = {
            "exhibit_family": r.exhibit_family,
            "program_key": r.program_key,
            "latest_title": f"Classified Programs – {r.service_branch} ({r.appropriation_account})"
            if r.is_classified
            else r.program_title,
            "is_classified_rollup": r.is_classified,
        }
    stmt = pg_insert(Program).values(list(rows.values()))
    stmt = stmt.on_conflict_do_update(
        index_elements=["exhibit_family", "program_key"],
        set_={"latest_title": stmt.excluded.latest_title},
    ).returning(Program.exhibit_family, Program.program_key, Program.id)
    return {(fam, key): pid for fam, key, pid in session.execute(stmt)}


def load_line_items(
    session: Session,
    run: IngestionRun,
    records: list[LineItemRecord],
    links: LinkResult,
    xlsx_doc: SourceDocument,
    pdf_doc: SourceDocument,
    fiscal_year: int,
    ref_kind: str = "r1_summary",
    match_method: str = "acct+ba+line+pe",
) -> None:
    program_ids = _upsert_programs(session, records)
    item_rows = [
        {
            "ingestion_run_id": run.id,
            "source_document_id": xlsx_doc.id,
            "source_row_number": r.source_row_number,
            "program_id": program_ids[(r.exhibit_family, r.program_key)],
            "fiscal_year": fiscal_year,
            "budget_cycle": run.budget_cycle,
            "exhibit_type": run.exhibit_type,
            "appropriation_account": r.appropriation_account,
            "service_branch": r.service_branch,
            "organization": r.organization,
            "budget_activity": r.budget_activity,
            "budget_activity_title": r.budget_activity_title,
            "budget_subactivity": r.budget_subactivity,
            "budget_subactivity_title": r.budget_subactivity_title,
            "line_number": r.line_number,
            "program_element": r.program_element,
            "line_item_number": r.line_item_number,
            "program_title": r.program_title,
            "include_in_toa": r.include_in_toa,
            "classification": r.classification,
            "raw_description_text": None,
        }
        for r in records
    ]
    ids = session.scalars(
        insert(BudgetLineItem).returning(BudgetLineItem.id, sort_by_parameter_order=True), item_rows
    ).all()

    amount_rows, element_rows, ref_rows = [], [], []
    for item_id, r in zip(ids, records, strict=True):
        amount_rows += [
            {
                "line_item_id": item_id,
                "funds_fiscal_year": a.funds_fiscal_year,
                "amount_type": a.amount_type,
                "funding_category": a.funding_category,
                "amount_thousands": a.amount_thousands,
                "source_column": a.source_column,
                "quantity": a.quantity,
            }
            for a in r.amounts
        ]
        element_rows += [
            {
                "line_item_id": item_id,
                "source_row_number": e.source_row_number,
                "source_column": a.source_column,
                "cost_type": e.cost_type,
                "cost_type_title": e.cost_type_title,
                "is_add": e.is_add,
                "funds_fiscal_year": a.funds_fiscal_year,
                "amount_type": a.amount_type,
                "funding_category": a.funding_category,
                "amount_thousands": a.amount_thousands,
                "quantity": a.quantity,
            }
            for e in r.cost_elements
            for a in e.amounts
        ]
        ref_rows += [
            {
                "line_item_id": item_id,
                "source_document_id": pdf_doc.id,
                "page_number": ref.page_number,
                "printed_page_label": ref.printed_label,
                "ref_kind": ref_kind,
                "section": ref.section,
                "match_method": match_method,
                "amount_verified": ref.amount_verified,
                "is_primary": ref.is_primary,
            }
            for ref in links.refs.get(r.key, [])
        ]
    if amount_rows:
        session.execute(insert(LineItemAmount), amount_rows)
    if element_rows:
        session.execute(insert(LineItemCostElement), element_rows)
    if ref_rows:
        session.execute(insert(LineItemSourceRef), ref_rows)


def publish_run(session: Session, run_id: int) -> IngestionRun:
    run = session.get(IngestionRun, run_id, with_for_update=True)
    if run is None:
        raise PublishError(f"run {run_id} does not exist")
    if run.status == "published":
        return run
    if run.status != "validated":
        raise PublishError(f"run {run_id} is {run.status!r}; only a validated run can be published")
    session.execute(
        update(IngestionRun)
        .where(
            IngestionRun.budget_cycle == run.budget_cycle,
            IngestionRun.exhibit_type == run.exhibit_type,
            IngestionRun.status == "published",
        )
        .values(status="superseded")
    )
    session.flush()
    run.status = "published"
    run.finished_at = run.finished_at or datetime.now(timezone.utc)
    return run
