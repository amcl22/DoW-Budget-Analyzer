"""`budget` command-line entry point.

    budget ingest --fy 2027 --exhibit R-1|P-1 [--cycle PB2027] [--publish] [--refresh] [--force]
    budget ingest-all [--publish] [--no-books]
    budget discover-books --fy 2027
    budget new-access-token | budget share-link --base-url https://host
    budget publish RUN_ID
    budget runs
    budget report RUN_ID
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone

import typer
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import (
    SOURCES_DIR,
    load_accounts,
    load_column_map,
    load_expected_totals,
    load_sources,
    normalize_exhibit,
)
from .db.models import IngestionRun
from .fetch import FetchError, fetch_books, fetch_sources
from .discover import discover_books, load_books, write_books
from .exhibits import EXHIBITS
from .load import (
    PublishError,
    find_reusable_run,
    get_engine,
    input_fingerprint,
    load_line_items,
    parser_version,
    publish_run,
    seed_accounts,
    upsert_source_documents,
)
from .normalize import NormalizeError
from .parse_xlsx import ParseError, parse_workbook
from .pdf_common import PdfFormatError
from .r2 import attach_r2, parse_r2_book
from .validate import build_report, r2_checks, run_checks

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _print_report(report: dict) -> None:
    for c in report.get("checks", []):
        mark = "PASS" if c["passed"] else ("FAIL" if c["hard"] else "WARN")
        typer.echo(f"  {mark}  {c['name']:<22} {c['summary']}")
        if not c["passed"]:
            for d in c["details"][:10]:
                typer.echo(f"          - {d}")
    if "error" in report:
        typer.echo(f"  ERROR  {report['error']}")


def _finish(session: Session, run: IngestionRun, status: str, report: dict) -> None:
    run.status = status
    run.validation_report = report
    run.finished_at = datetime.now(timezone.utc)
    session.commit()


def _budget_year_total(records, fy: int) -> int:
    return sum(
        a.amount_thousands for r in records for a in r.amounts
        if a.funds_fiscal_year == fy and a.funding_category == "total"
    )


def _ingest_one(fy: int, exhibit: str, cycle: str | None, publish: bool, refresh: bool, force: bool,
                books: bool = True) -> bool:
    """Returns True when the release ends up validated or published."""
    ex = EXHIBITS[exhibit]
    sources = load_sources(fy, exhibit)
    cycle = cycle or sources.budget_cycle
    if cycle != sources.budget_cycle:
        raise typer.BadParameter(f"sources/fy{fy}.yaml is for {sources.budget_cycle}, not {cycle}")

    typer.echo(f"Fetching {len(sources.files)} source file(s) for {cycle} {exhibit}...")
    try:
        fetched = fetch_sources(sources, exhibit, refresh=refresh)
    except (FetchError, OSError) as e:
        typer.echo(f"Fetch failed: {e}", err=True)
        return False
    by_role = {f.role: f for f in fetched}
    for f in fetched:
        typer.echo(f"  {f.role:<12} {f.path.name}  sha256 {f.sha256[:12]}")

    # R-2 justification books enrich R-1 lines (descriptions, out-years, page links). They are
    # part of the run's inputs, so they are fetched before the fingerprint is computed.
    book_list, book_files, book_notes = [], [], []
    if exhibit == "R-1" and books:
        book_list = load_books(fy, "R-2")
        if not book_list:
            book_notes.append(f"no sources/fy{fy}_books.yaml; run `budget discover-books --fy {fy}`")
        else:
            typer.echo(f"Fetching {len(book_list)} R-2 justification book(s)...")
            book_files, book_notes = fetch_books(book_list, fy, "R-2", refresh=refresh)
            typer.echo(f"  {len(book_files)} book(s) available, {len(book_notes)} note(s)")
    fetched = fetched + book_files

    cols = load_column_map(exhibit, cycle)
    accounts = load_accounts()
    version = parser_version()
    fingerprint = input_fingerprint(fetched, version)

    engine = get_engine()
    with Session(engine) as session:
        seed_accounts(session, accounts)
        docs = upsert_source_documents(session, fetched, fy, cycle, exhibit)
        session.commit()

        existing = find_reusable_run(session, cycle, exhibit, fingerprint)
        if existing and not force:
            typer.echo(f"Inputs, parser and config unchanged: run {existing.id} is already {existing.status}.")
            if publish and existing.status == "validated":
                publish_run(session, existing.id)
                session.commit()
                typer.echo(f"Published run {existing.id}.")
            return True

        run = IngestionRun(
            budget_cycle=cycle, exhibit_type=exhibit, status="running",
            parser_version=version, input_fingerprint=fingerprint,
        )
        session.add(run)
        session.commit()
        typer.echo(f"Run {run.id}: parsing...")

        try:
            sheet = parse_workbook(by_role["data"].path, cols)
            records = ex.normalize(sheet.rows, cols, accounts, exhibit)
            pages = ex.parse_pdf(by_role["summary_pdf"].path, cols)
        except (ParseError, NormalizeError, PdfFormatError) as e:
            _finish(session, run, "failed", {"passed": False, "error": str(e)})
            typer.echo(f"Run {run.id} failed while parsing:\n{e}", err=True)
            return False

        links = ex.link(records, pages, cols)
        checks = run_checks(records, pages, links, cols, accounts, load_expected_totals(cycle, exhibit), exhibit)

        r2 = None
        if exhibit == "R-1" and books:
            service_of = {b.url: b.service for b in book_list}
            sections, books_by_service = [], defaultdict(int)
            for f in book_files:
                try:
                    sections += parse_r2_book(f.path, f.url)
                    books_by_service[service_of[f.url]] += 1
                except Exception as e:  # noqa: BLE001 - one unreadable book must not stop the run
                    book_notes.append(f"{f.url}: could not be read ({e})")
            for s in [s for s in {b.service for b in book_list} if s not in books_by_service]:
                books_by_service[s] = 0
            r2 = attach_r2(records, sections, fy, service_of)
            for r in records:
                r.raw_description_text = r2.descriptions.get(r.key)
                r.amounts += r2.outyears.get(r.key, [])
            checks += r2_checks(records, r2, book_notes, dict(books_by_service))
        stats = {
            "line_items": len(records),
            "cost_elements": sum(len(r.cost_elements) for r in records),
            "excel_footnotes": sheet.footnotes,
            "pdf_pages": len(pages),
            "page_refs": sum(len(v) for v in links.refs.values()),
            "budget_year_total_thousands": _budget_year_total(records, fy),
        }
        if r2 is not None:
            stats["r2_sections"] = r2.sections
            stats["r2_linked_lines"] = sum(1 for v in r2.refs.values() if v)
            stats["with_description"] = sum(1 for r in records if r.raw_description_text)
        report = build_report(checks, stats)

        # Rows are loaded even when validation fails, so a failed run can be inspected;
        # the app only reads published runs.
        load_line_items(session, run, records, links, docs["data"], docs["summary_pdf"], fy,
                        ref_kind=ex.ref_kind, match_method=ex.match_method,
                        extra_refs=r2.refs if r2 else None, docs_by_url=docs)
        run.row_count = len(records)
        _finish(session, run, "validated" if report["passed"] else "failed", report)

        typer.echo(f"Run {run.id}: {len(records)} line items, {stats['page_refs']} page refs")
        _print_report(report)
        if not report["passed"]:
            typer.echo(f"Run {run.id} FAILED validation; not publishable.", err=True)
            return False
        if publish:
            publish_run(session, run.id)
            session.commit()
            typer.echo(f"Run {run.id} validated and published.")
        else:
            typer.echo(f"Run {run.id} validated. Publish with: budget publish {run.id}")
        return True


def _exhibit_option(value: str) -> str:
    exhibit = normalize_exhibit(value)
    if exhibit not in EXHIBITS:
        raise typer.BadParameter(f"{exhibit} is not implemented (supported: {sorted(EXHIBITS)})")
    return exhibit


@app.command()
def ingest(
    fy: int = typer.Option(..., help="Fiscal year of the budget release, e.g. 2027"),
    exhibit: str = typer.Option("R-1", help="Exhibit type: R-1 or P-1", callback=_exhibit_option),
    cycle: str | None = typer.Option(None, help="Budget cycle; defaults to the one in sources/fy{FY}.yaml"),
    publish: bool = typer.Option(False, help="Publish the run if every hard check passes"),
    refresh: bool = typer.Option(False, help="Re-download source files even if cached"),
    force: bool = typer.Option(False, help="Ingest again even if an identical validated run exists"),
    books: bool = typer.Option(True, help="R-1: read the R-2 justification books in sources/fy{FY}_books.yaml"),
) -> None:
    """Fetch, parse, link, validate and load one exhibit of one budget release."""
    if not _ingest_one(fy, exhibit, cycle, publish, refresh, force, books):
        raise typer.Exit(1)


@app.command("ingest-all")
def ingest_all(
    publish: bool = typer.Option(False, help="Publish each run that passes every hard check"),
    refresh: bool = typer.Option(False, help="Re-download source files even if cached"),
    force: bool = typer.Option(False, help="Ingest again even if an identical validated run exists"),
    books: bool = typer.Option(True, help="R-1: read the R-2 justification books"),
) -> None:
    """Every release in sources/fy*.yaml, every implemented exhibit it lists."""
    results = []
    for path in sorted(SOURCES_DIR.glob("fy[0-9][0-9][0-9][0-9].yaml")):
        fy = int(path.stem[2:])
        listed = yaml.safe_load(path.read_text())["exhibits"]
        for exhibit in [e for e in EXHIBITS if e in listed]:
            typer.echo(f"\n=== FY{fy} {exhibit}")
            results.append((fy, exhibit, _ingest_one(fy, exhibit, None, publish, refresh, force, books)))
    typer.echo("\nSummary:")
    for fy, exhibit, ok in results:
        typer.echo(f"  FY{fy} {exhibit:<4} {'ok' if ok else 'FAILED'}")
    if not all(ok for *_, ok in results):
        raise typer.Exit(1)


@app.command("discover-books")
def discover_books_cmd(fy: int = typer.Option(..., help="Fiscal year of the budget release")) -> None:
    """Read the justification index pages and write sources/fy{FY}_books.yaml."""
    books, notes = discover_books(fy)
    path = write_books(fy, books, notes)
    for n in notes:
        typer.echo(f"  {n}")
    typer.echo(f"Wrote {path.relative_to(SOURCES_DIR.parent)}")


@app.command("new-access-token")
def new_access_token() -> None:
    """Print a new random secret for the team link. Set it as ACCESS_TOKEN on the server;
    replacing the old one revokes every earlier link."""
    import secrets

    typer.echo(secrets.token_urlsafe(32))


@app.command("share-link")
def share_link_cmd(base_url: str = typer.Option(..., help="Where the app is served, e.g. https://budget.example.com")) -> None:
    """Print the team link for the ACCESS_TOKEN in this environment."""
    import os

    from api.access import share_link

    token = os.environ.get("ACCESS_TOKEN")
    if not token:
        typer.echo("ACCESS_TOKEN is not set; create one with `budget new-access-token`.", err=True)
        raise typer.Exit(1)
    typer.echo(share_link(base_url, token))


@app.command()
def publish(run_id: int) -> None:
    """Publish a validated run (supersedes the currently published run for that cycle)."""
    with Session(get_engine()) as session:
        try:
            publish_run(session, run_id)
        except PublishError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from None
        session.commit()
    typer.echo(f"Run {run_id} published.")


@app.command()
def runs(limit: int = 20) -> None:
    """List recent ingestion runs."""
    with Session(get_engine()) as session:
        for r in session.scalars(select(IngestionRun).order_by(IngestionRun.id.desc()).limit(limit)):
            started = r.started_at.strftime("%Y-%m-%d %H:%M")
            typer.echo(
                f"{r.id:>5}  {r.budget_cycle:<8} {r.exhibit_type:<5} {r.status:<11} "
                f"{(r.row_count or 0):>6} rows  {started}  parser {r.parser_version}"
            )


@app.command()
def report(run_id: int, as_json: bool = typer.Option(False, "--json")) -> None:
    """Show a run's validation report."""
    with Session(get_engine()) as session:
        run = session.get(IngestionRun, run_id)
        if run is None:
            typer.echo(f"run {run_id} does not exist", err=True)
            raise typer.Exit(1)
        if as_json:
            typer.echo(json.dumps(run.validation_report, indent=2))
        else:
            typer.echo(f"Run {run.id} ({run.budget_cycle} {run.exhibit_type}): {run.status}")
            _print_report(run.validation_report or {})


if __name__ == "__main__":
    app()
