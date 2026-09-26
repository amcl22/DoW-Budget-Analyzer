"""Integration tests against a real Postgres. Set TEST_DATABASE_URL to run them; the
database's public schema is dropped and recreated."""

import os
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from pipeline import cli
from pipeline.config import ROOT, SourceFile, SourceList
from pipeline.db.models import BudgetLineItem, IngestionRun, LineItemSourceRef
from pipeline.fetch import FetchedFile
from pipeline.load import (
    PublishError,
    load_line_items,
    publish_run,
    seed_accounts,
    upsert_source_documents,
)
from pipeline.validate import CheckResult
from tests.conftest import SAMPLE_PDF, SAMPLE_XLSX

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

XLSX_URL = "https://example.test/r1_display.xlsx"
PDF_URL = "https://example.test/FY2027_r1.pdf"


def _fetched():
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    return [
        FetchedFile("data", "xlsx", XLSX_URL, "a" * 64, SAMPLE_XLSX, now, None),
        FetchedFile("summary_pdf", "pdf", PDF_URL, "b" * 64, SAMPLE_PDF, now, 6),
    ]


@pytest.fixture()
def engine(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    eng = create_engine(URL)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    yield eng
    eng.dispose()


def _load_run(session, records, links, accounts, status="validated"):
    seed_accounts(session, accounts)
    docs = upsert_source_documents(session, _fetched(), 2027, "PB2027", "R-1")
    run = IngestionRun(budget_cycle="PB2027", exhibit_type="R-1", status="running",
                       parser_version="test", input_fingerprint="fp")
    session.add(run)
    session.flush()
    load_line_items(session, run, records, links, docs["data"], docs["summary_pdf"], 2027)
    run.status = status
    session.commit()
    return run


def test_load_publish_and_flat_view(engine, records, links, accounts):
    with Session(engine) as s:
        run = _load_run(s, records, links, accounts)
        assert s.scalar(select(func.count()).select_from(BudgetLineItem)) == len(records)
        assert s.scalar(select(func.count()).select_from(LineItemSourceRef)) == len(records)

        # nothing is visible until published
        assert s.execute(text("SELECT count(*) FROM line_item_flat")).scalar() == 0
        publish_run(s, run.id)
        s.commit()

        row = s.execute(text(
            "SELECT prior_year_amount, current_year_amount, budget_year_amount, budget_year_mandatory,"
            " source_page_number, source_pdf_link, service_branch"
            " FROM line_item_flat WHERE program_element = '0601102A'"
        )).one()
        assert row == (290464, 259178, 215322, None, 3, f"{PDF_URL}#page=3", "Army")


def test_publishing_supersedes_the_previous_run(engine, records, links, accounts):
    with Session(engine) as s:
        first = _load_run(s, records, links, accounts)
        publish_run(s, first.id)
        s.commit()
        second = _load_run(s, records, links, accounts)
        publish_run(s, second.id)
        s.commit()
        s.refresh(first)
        assert first.status == "superseded"
        assert s.execute(text("SELECT count(DISTINCT ingestion_run_id), count(*) FROM line_item_flat")).one() == (
            1, len(records),
        )


def test_failed_run_cannot_be_published(engine, records, links, accounts):
    with Session(engine) as s:
        run = _load_run(s, records, links, accounts, status="failed")
        with pytest.raises(PublishError, match="only a validated run"):
            publish_run(s, run.id)


@pytest.fixture()
def fake_sources(monkeypatch):
    monkeypatch.setattr(cli, "load_sources", lambda fy, exhibit: SourceList(2027, "PB2027", [
        SourceFile("data", "xlsx", XLSX_URL), SourceFile("summary_pdf", "pdf", PDF_URL),
    ]))
    monkeypatch.setattr(cli, "fetch_sources", lambda sources, exhibit, refresh=False: _fetched())
    monkeypatch.setattr(cli, "load_books", lambda fy, exhibit: [])   # no justification-book downloads
    monkeypatch.setattr(cli, "parser_version", lambda: "test")


def _runs(engine):
    with Session(engine) as s:
        return [(r.id, r.status) for r in s.scalars(select(IngestionRun).order_by(IngestionRun.id))]


def test_cli_records_a_failed_run(engine, fake_sources):
    # the fixture is a partial release, so the department totals cannot match
    result = CliRunner().invoke(cli.app, ["ingest", "--fy", "2027", "--exhibit", "r1"])
    assert result.exit_code == 1
    assert "FAIL  department_summary" in result.output
    assert _runs(engine) == [(1, "failed")]
    # a failed run is never reused: the next attempt starts a new run
    CliRunner().invoke(cli.app, ["ingest", "--fy", "2027", "--exhibit", "r1"])
    assert _runs(engine) == [(1, "failed"), (2, "failed")]


def test_cli_rerun_with_identical_inputs_is_a_no_op(engine, fake_sources, monkeypatch):
    monkeypatch.setattr(cli, "run_checks", lambda *a: [CheckResult("stub", True, True, "ok")])
    runner = CliRunner()
    first = runner.invoke(cli.app, ["ingest", "--fy", "2027", "--exhibit", "r1"])
    assert first.exit_code == 0, first.output
    second = runner.invoke(cli.app, ["ingest", "--fy", "2027", "--exhibit", "r1", "--publish"])
    assert "unchanged" in second.output
    assert _runs(engine) == [(1, "published")]
    forced = runner.invoke(cli.app, ["ingest", "--fy", "2027", "--exhibit", "r1", "--force"])
    assert forced.exit_code == 0, forced.output
    assert _runs(engine) == [(1, "published"), (2, "validated")]


def test_p1_load_cost_elements_quantities_and_history(engine, accounts):
    from pipeline.config import load_column_map
    from pipeline.exhibits import EXHIBITS
    from pipeline.parse_xlsx import parse_workbook
    from tests.conftest import FIXTURES

    ex = EXHIBITS["P-1"]
    cols = load_column_map("P-1", "PB2027")
    records = ex.normalize(parse_workbook(FIXTURES / "p1_2027_sample.xlsx", cols).rows, cols, accounts, "P-1")
    links = ex.link(records, ex.parse_pdf(FIXTURES / "p1_2027_sample.pdf", cols), cols)
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    fetched = [
        FetchedFile("data", "xlsx", "https://example.test/p1_display.xlsx", "c" * 64, FIXTURES / "p1_2027_sample.xlsx", now, None),
        FetchedFile("summary_pdf", "pdf", "https://example.test/FY2027_p1.pdf", "d" * 64, FIXTURES / "p1_2027_sample.pdf", now, 8),
    ]
    with Session(engine) as s:
        seed_accounts(s, accounts)
        docs = upsert_source_documents(s, fetched, 2027, "PB2027", "P-1")
        run = IngestionRun(budget_cycle="PB2027", exhibit_type="P-1", status="running",
                           parser_version="test", input_fingerprint="fp")
        s.add(run)
        s.flush()
        load_line_items(s, run, records, links, docs["data"], docs["summary_pdf"], 2027,
                        ref_kind=ex.ref_kind, match_method=ex.match_method)
        run.status = "validated"
        publish_run(s, run.id)
        s.commit()

        row = s.execute(text(
            "SELECT line_item_number, budget_subactivity_title, prior_year_amount, prior_year_quantity,"
            " current_year_quantity, source_page_number FROM line_item_flat"
            " WHERE line_item_number = '5757A05111'"
        )).one()
        assert row == ("5757A05111", "Rotary", 557399, 31, 7, 5)
        elements = s.execute(text(
            "SELECT count(DISTINCT e.source_row_number) FROM line_item_cost_element e"
            " JOIN budget_line_item li ON li.id = e.line_item_id WHERE li.line_item_number = '5757A05111'"
        )).scalar()
        assert elements == 2
        history = s.execute(text(
            "SELECT funds_fiscal_year, amount_type, amount_thousands, quantity, is_latest"
            " FROM program_funding_history WHERE program_key = '2031A:5757A05111' ORDER BY funds_fiscal_year"
        )).all()
        assert history == [(2025, "actual", 557399, 31, True), (2026, "enacted", 361669, 7, True),
                           (2027, "request", 1552, None, True)]
