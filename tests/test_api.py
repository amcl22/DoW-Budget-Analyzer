"""Search API against a real Postgres loaded with the R-1 and P-1 fixtures. Set
TEST_DATABASE_URL to run; the database's public schema is dropped and recreated."""

import os
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from pipeline.config import ROOT, load_accounts, load_column_map
from pipeline.db.models import IngestionRun
from pipeline.exhibits import EXHIBITS
from pipeline.fetch import FetchedFile
from pipeline.load import load_line_items, publish_run, seed_accounts, upsert_source_documents
from pipeline.parse_xlsx import parse_workbook
from tests.conftest import FIXTURES

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

DESCRIPTION = "Develops directed energy and hypersonic defense research for the Army."


def _load(session, exhibit, fixture, publish=True):
    ex = EXHIBITS[exhibit]
    cols = load_column_map(exhibit, "PB2027")
    accounts = load_accounts()
    records = ex.normalize(parse_workbook(FIXTURES / f"{fixture}.xlsx", cols).rows, cols, accounts, exhibit)
    links = ex.link(records, ex.parse_pdf(FIXTURES / f"{fixture}.pdf", cols), cols)
    if exhibit == "R-1":
        next(r for r in records if r.program_element == "0601102A").raw_description_text = DESCRIPTION
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    fetched = [
        FetchedFile("data", "xlsx", f"https://example.test/{fixture}.xlsx", "a" * 63 + exhibit[0], FIXTURES / f"{fixture}.xlsx", now, None),
        FetchedFile("summary_pdf", "pdf", f"https://example.test/{fixture}.pdf", "b" * 63 + exhibit[0], FIXTURES / f"{fixture}.pdf", now, 1),
    ]
    seed_accounts(session, accounts)
    docs = upsert_source_documents(session, fetched, 2027, "PB2027", exhibit)
    run = IngestionRun(budget_cycle="PB2027", exhibit_type=exhibit, status="running", parser_version="t", input_fingerprint="f")
    session.add(run)
    session.flush()
    load_line_items(session, run, records, links, docs["data"], docs["summary_pdf"], 2027,
                    ref_kind=ex.ref_kind, match_method=ex.match_method)
    run.status = "validated"
    if publish:
        publish_run(session, run.id)
    session.commit()
    return records


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from api.main import app, get_conn

    os.environ["DATABASE_URL"] = URL
    engine = create_engine(URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    with Session(engine) as s:
        _load(s, "R-1", "r1_sample")
        _load(s, "P-1", "p1_2027_sample")

    def conn_override():
        with engine.connect() as conn:
            yield conn

    app.dependency_overrides[get_conn] = conn_override
    yield TestClient(app)
    app.dependency_overrides.clear()
    engine.dispose()


def _search(client, **params):
    resp = client.get("/api/search", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_browse_everything_sorted_by_budget_year(client):
    data = _search(client, sort="budget", page_size=200)
    assert data["total"] == 50 + 9          # R-1 and P-1 fixture lines
    amounts = [r["budget_year_amount"] or 0 for r in data["results"]]
    assert amounts == sorted(amounts, reverse=True)
    assert data["budget_year_total"] == sum(amounts)


def test_exact_pe_ranks_first(client):
    top = _search(client, q="0601102A")["results"][0]
    assert top["program_element"] == "0601102A"
    assert top["program_title"] == "Defense Research Sciences"
    assert top["source_pdf_link"].endswith("r1_sample.pdf#page=3")
    assert top["source_kind"] == "r1_summary"


def test_bli_prefix_and_procurement_fields(client):
    [ah64] = _search(client, q="5757A05")["results"]
    assert (ah64["exhibit_type"], ah64["program_key"], ah64["current_year_quantity"]) == ("P-1", "2031A:5757A05111", 7)
    assert ah64["budget_subactivity_title"] == "Rotary"


def test_keyword_in_description_returns_marked_snippet(client):
    rows = _search(client, q="directed energy")["results"]
    row = next(r for r in rows if r["program_element"] == "0601102A")
    assert "\u0002directed\u0003 \u0002energy\u0003" in row["snippet"]
    assert "<" not in row["snippet"]              # never HTML


def test_title_word_match(client):
    titles = [r["program_title"] for r in _search(client, q="helicopter")["results"]]
    assert titles == ["UH-72 LAKOTA LIGHT UTILITY HELICOPTER"]


@pytest.mark.parametrize("params,expected", [
    ({"exhibit": "P-1"}, 9),
    ({"service": "Navy"}, 2),                   # 1612N lines
    ({"account": "3007D"}, 16),
    ({"budget_activity": "Basic research"}, 7),   # 6 Army + the DEFW line
    ({"exhibit": "R-1", "service": ["Army", "Defense-Wide"]}, 50),
    ({"min_amount": 1_000_000}, None),
])
def test_filters(client, params, expected):
    data = _search(client, **params, page_size=200)
    if expected is not None:
        assert data["total"] == expected
    for r in data["results"]:
        if "exhibit" in params and isinstance(params["exhibit"], str):
            assert r["exhibit_type"] == params["exhibit"]
        if "min_amount" in params:
            assert (r["budget_year_amount"] or 0) >= params["min_amount"]


def test_pagination(client):
    first = _search(client, sort="title", order="asc", page_size=10, page=1)
    second = _search(client, sort="title", order="asc", page_size=10, page=2)
    assert first["total"] == second["total"] == 59
    assert not {r["id"] for r in first["results"]} & {r["id"] for r in second["results"]}
    titles = [r["program_title"] for r in first["results"] + second["results"]]
    assert titles == sorted(titles)


def test_change_pct(client):
    [row] = _search(client, q="0601102A")["results"][:1]
    assert row["change_pct"] == pytest.approx((215322 - 259178) / 259178)


def test_cycle_filter_and_unpublished_runs_hidden(client):
    assert _search(client, cycle="PB2026")["total"] == 0
    assert _search(client, cycle="PB2027")["total"] == 59


def test_facets(client):
    f = client.get("/api/facets", params={"cycle": "PB2027"}).json()
    assert [c["budget_cycle"] for c in f["cycles"]] == ["PB2027"]
    assert {e["value"] for e in f["exhibits"]} == {"R-1", "P-1"}
    assert {"value": "Army", "lines": 40} in f["services"]
    assert any(a["value"] == "2031A" and a["title"] == "Aircraft Procurement, Army" for a in f["accounts"])


def test_csv_export(client):
    resp = client.get("/api/search.csv", params={"exhibit": "P-1", "sort": "budget"})
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.strip().splitlines()
    assert lines[0].startswith("budget_cycle,exhibit_type,service_branch")
    assert len(lines) == 1 + 9


@pytest.mark.parametrize("params", [{"sort": "id; drop table program"}, {"page_size": 1000}, {"page": 0}])
def test_invalid_parameters_rejected(client, params):
    assert client.get("/api/search", params=params).status_code == 422


def test_hostile_query_text_is_just_text(client):
    for q in ["'; DROP TABLE program; --", "100%", "a_b", "\\", "or 1=1 --", "!!!"]:
        _search(client, q=q)
    assert _search(client)["total"] == 59


# --- program detail and watch (step 5)


def test_program_detail_procurement(client):
    d = client.get("/api/programs/2031A:5757A05111").json()
    assert d["program"]["exhibit_family"] == "PROC"
    assert d["latest_cycle"] == "PB2027" and d["releases"] == ["PB2027"]
    series = {s["fiscal_year"]: s for s in d["series"]}
    assert (series[2025]["amount_thousands"], series[2025]["quantity"], series[2025]["amount_type"]) == (557399, 31, "actual")
    assert series[2026]["change_pct"] == pytest.approx((361669 - 557399) / 557399)
    assert series[2026]["flagged"] and series[2027]["flagged"]
    assert {s["page_number"] for s in d["sources"]} == {5, 6}          # the page pair
    assert [e["cost_type_title"] for e in d["cost_elements"]] == ["Weapon System Cost", "Less: Advance Procurement (PY)"]
    assert d["watched"] is False


def test_program_detail_rdte_with_description(client):
    d = client.get("/api/programs/0601102A").json()
    assert d["program"]["exhibit_family"] == "RDTE" and d["cost_elements"] == []
    assert d["description"]["raw_description_text"] == DESCRIPTION
    assert [s["fiscal_year"] for s in d["series"]] == [2025, 2026, 2027]
    assert all(isinstance(s["amount_thousands"], int) for s in d["series"])


def test_program_not_found(client):
    assert client.get("/api/programs/NOPE").status_code == 404
    assert client.put("/api/programs/NOPE/watch").status_code == 404


def test_watch_toggle_is_idempotent_and_listed(client):
    key = "0601102A"
    assert client.put(f"/api/programs/{key}/watch").json()["watched"] is True
    assert client.put(f"/api/programs/{key}/watch").json()["watched"] is True     # no duplicate
    watches = client.get("/api/watches").json()
    assert [w["program_key"] for w in watches] == [key]
    assert watches[0]["budget_year_amount"] == 215322
    assert client.get(f"/api/programs/{key}").json()["watched"] is True
    [row] = [r for r in _search(client, q=key)["results"] if r["program_element"] == key]
    assert row["watched"] is True
    client.delete(f"/api/programs/{key}/watch")
    assert client.get("/api/watches").json() == []
    assert client.delete(f"/api/programs/{key}/watch").status_code == 200       # already gone: fine
