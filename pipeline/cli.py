"""`budget` command-line entry point.

    budget ingest --fy 2027 --exhibit R-1 [--cycle PB2027] [--publish] [--refresh] [--force]
    budget publish RUN_ID
    budget runs
    budget report RUN_ID
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import (
    load_accounts,
    load_column_map,
    load_expected_totals,
    load_sources,
    normalize_exhibit,
)
from .db.models import IngestionRun
from .fetch import FetchError, fetch_sources
from .link_pages import link_line_items
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
from .normalize import NormalizeError, normalize_rows
from .parse_r1 import ParseError, parse_r1_workbook
from .pdf_r1 import PdfFormatError, parse_r1_pdf
from .validate import build_report, run_checks

app = typer.Typer(add_completion=False, no_args_is_help=True)

SUPPORTED_EXHIBITS = {"R-1"}


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


@app.command()
def ingest(
    fy: int = typer.Option(..., help="Fiscal year of the budget release, e.g. 2027"),
    exhibit: str = typer.Option("R-1", help="Exhibit type (only R-1 so far)"),
    cycle: str | None = typer.Option(None, help="Budget cycle; defaults to the one in sources/fy{FY}.yaml"),
    publish: bool = typer.Option(False, help="Publish the run if every hard check passes"),
    refresh: bool = typer.Option(False, help="Re-download source files even if cached"),
    force: bool = typer.Option(False, help="Ingest again even if an identical validated run exists"),
) -> None:
    """Fetch, parse, link, validate and load one exhibit of one budget release."""
    exhibit = normalize_exhibit(exhibit)
    if exhibit not in SUPPORTED_EXHIBITS:
        raise typer.BadParameter(f"{exhibit} is not implemented yet (supported: {sorted(SUPPORTED_EXHIBITS)})")
    sources = load_sources(fy, exhibit)
    cycle = cycle or sources.budget_cycle
    if cycle != sources.budget_cycle:
        raise typer.BadParameter(f"sources/fy{fy}.yaml is for {sources.budget_cycle}, not {cycle}")

    typer.echo(f"Fetching {len(sources.files)} source file(s) for {cycle} {exhibit}...")
    try:
        fetched = fetch_sources(sources, exhibit, refresh=refresh)
    except (FetchError, OSError) as e:
        typer.echo(f"Fetch failed: {e}", err=True)
        raise typer.Exit(1) from None
    by_role = {f.role: f for f in fetched}
    for f in fetched:
        typer.echo(f"  {f.role:<12} {f.path.name}  sha256 {f.sha256[:12]}")

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
            return

        run = IngestionRun(
            budget_cycle=cycle, exhibit_type=exhibit, status="running",
            parser_version=version, input_fingerprint=fingerprint,
        )
        session.add(run)
        session.commit()
        typer.echo(f"Run {run.id}: parsing...")

        try:
            sheet = parse_r1_workbook(by_role["data"].path, cols)
            records = normalize_rows(sheet.rows, cols, accounts)
            pages = parse_r1_pdf(by_role["summary_pdf"].path, cols.pdf_columns)
        except (ParseError, NormalizeError, PdfFormatError) as e:
            _finish(session, run, "failed", {"passed": False, "error": str(e)})
            typer.echo(f"Run {run.id} failed while parsing:\n{e}", err=True)
            raise typer.Exit(1) from None

        links = link_line_items(records, pages, cols)
        checks = run_checks(records, pages, links, cols, accounts, load_expected_totals(cycle, exhibit))
        stats = {
            "line_items": len(records),
            "excel_footnotes": sheet.footnotes,
            "pdf_pages": len(pages),
            "page_refs": sum(len(v) for v in links.refs.values()),
            "fy_budget_year_total_thousands": sum(r.amount(f"FY {fy} Total") or 0 for r in records),
        }
        report = build_report(checks, stats)

        # Rows are loaded even when validation fails, so a failed run can be inspected;
        # the app only reads published runs.
        load_line_items(session, run, records, links, docs["data"], docs["summary_pdf"], fy)
        run.row_count = len(records)
        _finish(session, run, "validated" if report["passed"] else "failed", report)

        typer.echo(f"Run {run.id}: {len(records)} line items, {stats['page_refs']} page refs")
        _print_report(report)
        if not report["passed"]:
            typer.echo(f"Run {run.id} FAILED validation; not publishable.", err=True)
            raise typer.Exit(1)
        if publish:
            publish_run(session, run.id)
            session.commit()
            typer.echo(f"Run {run.id} validated and published.")
        else:
            typer.echo(f"Run {run.id} validated. Publish with: budget publish {run.id}")


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
