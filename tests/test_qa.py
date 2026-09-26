"""Q&A: citation numbering, figure checks, and the tool loop with a scripted stand-in for
Claude (no API calls). The loop tests need TEST_DATABASE_URL, like test_api."""

import json
from types import SimpleNamespace as NS

import pytest

from api import qa
from tests.test_api import URL, client  # noqa: F401  (module fixture: loads the R-1/P-1 samples)


def _cite(ref, *amounts, kind="line_item"):
    return qa.Citation(ref, kind, {"title": ref}, list(amounts))


@pytest.mark.parametrize("written, value, tol", [
    ("$1.2 billion", 1_200_000, 50_000),
    ("$412.6 million", 412_600, 50),
    ("$1,792,457 thousand", 1_792_457, 0.5),
    ("$250M", 250_000, 500),
    ("$3.25B", 3_250_000, 5_000),
    ("$5,000", 5, 0.5),
])
def test_money_parsing(written, value, tol):
    m = qa.MONEY_RE.search(written)
    v, t = qa._money_k(*m.groups())
    assert v == pytest.approx(value)
    assert t == pytest.approx(tol, rel=0.01)


def test_figures_match_amounts_sums_and_differences():
    cited = [_cite("L1", 1_792_457, 763_394), _cite("L2", 874_781)]
    answer = ("The request is $1.79 billion [L1], up $1,029.1 million from $763.4 million [L1]; "
              "together with L2 that is $2.67 billion. It was $874.8 million in FY2025 [L2].")
    assert qa.check_figures(answer, cited) == []
    assert qa.check_figures("A made-up $999.9 million [L1].", cited) == ["$999.9 million"]


def test_rounding_tolerance_follows_the_written_precision():
    cited = [_cite("L1", 1_792_457)]
    assert qa.check_figures("$1.8 billion", cited) == []          # 1.75 to 1.85 accepted
    assert qa.check_figures("$1.7 billion", cited) == ["$1.7 billion"]


def test_citations_are_numbered_in_order_and_unknown_refs_dropped():
    session = qa.Session(conn=None)
    session.cites = {"L5": _cite("L5", 1), "T1": _cite("T1", 2, kind="total")}
    out, cards, unknown = qa.resolve_citations(
        "Total $1 thousand [T1]. Line [L5, T1]. Bogus [L9]. Both [L9; L5].", session)
    assert out == "Total $1 thousand [1]. Line [2][1]. Bogus. Both [2]."
    assert [(c["n"], c["ref"], c["kind"]) for c in cards] == [(1, "T1", "total"), (2, "L5", "line_item")]
    assert unknown == ["L9"]


# ---------------------------------------------------------------- the loop, with a fake Claude

def _text(s):
    return NS(type="text", text=s)


def _tool(id_, name, args):
    return NS(type="tool_use", id=id_, name=name, input=args)


def _response(content, stop):
    return NS(content=content, stop_reason=stop, model="fake-model",
              usage=NS(input_tokens=10, output_tokens=5, iterations=None))


class FakeClient:
    """Plays Claude from a script: each step sees the messages so far and returns a response."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.steps.pop(0)(kwargs["messages"])


def _last_tool_result(messages):
    return json.loads(messages[-1]["content"][0]["content"])


SEARCH = {"query": "0601102A", "cycle": None, "exhibit": None, "service": None, "sort": "relevance", "limit": 5}


def _conn():
    from sqlalchemy import create_engine
    return create_engine(URL).connect()


needs_db = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@needs_db
def test_loop_searches_then_answers_with_verified_citations(client):  # noqa: F811
    def answer(messages):
        row = _last_tool_result(messages)["rows"][0]
        by = row["FY2027_request_k"]
        return _response([_text(json.dumps({
            "status": "answered",
            "answer": f"{row['title']} requests ${by / 1000:.1f} million for FY2027 [{row['ref']}].",
        }))], "end_turn")

    fake = FakeClient(lambda m: _response([_tool("t1", "search_line_items", SEARCH)], "tool_use"), answer)
    with _conn() as conn:
        result = qa.ask(conn, "How much is requested for 0601102A?", client=fake)
    assert result["status"] == "answered"
    assert result["answer"].endswith("[1].")
    [card] = result["citations"]
    assert card["kind"] == "line_item" and card["number"] == "0601102A"
    assert card["source_pdf_link"].startswith("https://example.test/r1_sample.pdf#page=")
    assert result["unverified_figures"] == [] and result["warnings"] == []
    assert result["lookups"] == [{"tool": "search_line_items", "input": SEARCH}]
    req = fake.requests[0]
    assert req["model"] == qa.DEFAULT_MODEL and req["fallbacks"] == "default"
    assert req["betas"] == [qa.FALLBACK_BETA] and req["thinking"] == {"type": "adaptive"}
    assert all(t["strict"] and t["input_schema"]["additionalProperties"] is False for t in req["tools"])
    assert "PB2027" in req["system"]
    # the tool result went back paired with its call
    assert fake.requests[1]["messages"][-1]["content"][0]["tool_use_id"] == "t1"


@needs_db
def test_invented_figures_and_refs_are_flagged(client):  # noqa: F811
    def answer(messages):
        ref = _last_tool_result(messages)["rows"][0]["ref"]
        return _response([_text(json.dumps({
            "status": "answered", "answer": f"It gets $123.4 million [{ref}] and $9 billion [L999999999].",
        }))], "end_turn")

    fake = FakeClient(lambda m: _response([_tool("t1", "search_line_items", SEARCH)], "tool_use"), answer)
    with _conn() as conn:
        result = qa.ask(conn, "q", client=fake)
    assert result["unverified_figures"] == ["$123.4 million", "$9 billion"]
    assert len(result["citations"]) == 1
    assert any("L999999999" in w for w in result["warnings"])


@needs_db
def test_totals_and_tool_errors(client):  # noqa: F811
    totals = {"query": "", "cycle": "PB2027", "exhibit": "R-1", "service": None, "account": None,
              "budget_activity": None, "group_by": None, "include_outside_title": False}
    seen = {}

    def after_totals(messages):
        seen["totals"] = _last_tool_result(messages)
        return _response([_tool("t2", "get_line_item", {"ref": "L0"})], "tool_use")

    def after_error(messages):
        seen["error"] = messages[-1]["content"][0]
        t = seen["totals"]["totals"][0]
        return _response([_text(json.dumps({"status": "answered",
                                            "answer": f"R-1 totals ${t['FY2027_request_k'] / 1e6:.2f} billion [{t['ref']}]."}))],
                         "end_turn")

    fake = FakeClient(lambda m: _response([_tool("t1", "total_line_items", totals)], "tool_use"),
                      after_totals, after_error)
    with _conn() as conn:
        result = qa.ask(conn, "What is the RDT&E total?", client=fake)
        expected = conn.exec_driver_sql(
            "SELECT sum(budget_year_amount) FROM line_item_flat WHERE exhibit_type = 'R-1' AND include_in_toa"
        ).scalar()
    assert seen["totals"]["totals"][0]["FY2027_request_k"] == expected
    assert seen["error"]["is_error"] is True and "no published line item" in seen["error"]["content"]
    [card] = result["citations"]
    assert card["kind"] == "total" and card["search_url"] == "/?cycle=PB2027&exhibit=R-1"
    assert card["top_lines"] and card["top_lines"][0]["source_pdf_link"]
    assert result["unverified_figures"] == []


@needs_db
def test_no_match_and_refusal(client):  # noqa: F811
    nothing = _response([_text(json.dumps({"status": "no_match",
                                            "answer": "The data has no program matching 'warp drive'."}))], "end_turn")
    with _conn() as conn:
        result = qa.ask(conn, "Warp drive funding?", client=FakeClient(lambda m: nothing))
        assert result["status"] == "no_match" and result["citations"] == []
        refused = qa.ask(conn, "q", client=FakeClient(lambda m: _response([], "refusal")))
        assert refused["status"] == "refused"


@needs_db
def test_history_is_capped_and_question_validated(client):  # noqa: F811
    done = _response([_text(json.dumps({"status": "no_match", "answer": "No."}))], "end_turn")
    fake = FakeClient(lambda m: done)
    history = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(10)]
    with _conn() as conn:
        qa.ask(conn, "next", history=history, client=fake)
        msgs = fake.requests[0]["messages"]
        assert len(msgs) == 2 * qa.MAX_HISTORY_TURNS + 1 and msgs[0]["content"] == "q4"
        with pytest.raises(qa.QAError):
            qa.ask(conn, "  ", client=fake)
        with pytest.raises(qa.QAError):
            qa.ask(conn, "x" * (qa.MAX_QUESTION_CHARS + 1), client=fake)


@needs_db
def test_endpoint_off_without_api_key(client, monkeypatch):  # noqa: F811
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert client.get("/api/ask").json()["enabled"] is False
    resp = client.post("/api/ask", json={"question": "hi"})
    assert resp.status_code == 503


@needs_db
def test_endpoint_answers(client, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    done = _response([_text(json.dumps({"status": "no_match", "answer": "Nothing found."}))], "end_turn")
    monkeypatch.setattr(qa, "_client", lambda: FakeClient(lambda m: done))
    resp = client.post("/api/ask", json={"question": "hi", "history": []})
    assert resp.status_code == 200 and resp.json()["answer"] == "Nothing found."
    assert client.post("/api/ask", json={"question": "x" * 2000}).status_code == 422
