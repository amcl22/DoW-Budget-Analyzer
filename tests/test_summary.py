"""1-page summaries: figures, the takeaways' self-check, and the AI narrative gate (a scripted
stand-in for Claude; no API calls). Needs TEST_DATABASE_URL, like test_api."""

import json
from types import SimpleNamespace as NS

import pytest

from api import summary
from tests.test_api import URL, client  # noqa: F401


def _conn():
    from sqlalchemy import create_engine
    return create_engine(URL).connect()


needs_db = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


def test_verify_accepts_rounded_facts_and_rejects_others():
    facts = {"total": {"from": 434_200_000, "to": 756_800_000, "change": 322_600_000, "change_pct": 0.743}}
    assert summary.verify("Requests total $756.8 billion, up 74.3% (+$322.6 billion).", facts) == []
    assert summary.verify("Requests total $760 billion, up 80%.", facts) == ["$760 billion", "80%"]


@needs_db
def test_trends_endpoint_and_takeaways_check_out(client):  # noqa: F811
    s = client.get("/api/summary", params={"kind": "trends"}).json()
    assert s["cycle"] == "PB2027" and s["fiscal_year"] == 2027
    with _conn() as conn:
        want = conn.exec_driver_sql(
            "SELECT sum(budget_year_amount) FROM line_item_flat WHERE include_in_toa").scalar()
    assert s["total"]["to"] == want
    assert s["takeaways"] and summary.verify(" ".join(s["takeaways"]), s) == []
    assert sum(x["to"] for x in s["services"]) == want
    assert "ai" not in s
    assert client.get("/api/summary", params={"kind": "nope"}).status_code == 422


@needs_db
def test_execution_needs_three_releases(client):  # noqa: F811
    s = client.get("/api/summary", params={"kind": "execution", "fy": 2025}).json()
    # the fixtures hold only PB2027, so FY2025 has an actual but no request or enacted figure
    assert s["available"] == {"requested": False, "enacted": False, "actual": True}
    assert s["total"]["congress"] is None and s["total"]["execution"] is None


class FakeClient:
    def __init__(self, reply):
        self.beta = NS(messages=NS(create=lambda **kw: NS(
            content=[NS(type="text", text=reply)], stop_reason="end_turn", model="fake-model")))


@needs_db
def test_ai_narrative_shown_only_when_every_figure_matches():
    with _conn() as conn:
        s = summary.trends(conn)
    good = f"The FY2027 request is ${s['total']['to'] / 1e6:.1f} billion."
    shown = summary.ai_narrative(s, client=FakeClient(good))
    assert shown["text"] == good and shown["verified"] == 1
    withheld = summary.ai_narrative(s, client=FakeClient("The request is $999.9 billion, up 12.3%."))
    assert "text" not in withheld and withheld["unverified"] == ["$999.9 billion", "12.3%"]
    assert "withheld" in summary.ai_narrative(s)        # no API key in tests: off, with a reason


def test_facts_skip_counts_and_years():
    a, p = summary.facts_of({"fiscal_year": 2027, "lines": 50, "x": {"to": 1234, "change_pct": 0.5}})
    assert a == {1234} and p == {50.0}
    json.dumps({"ok": True})
