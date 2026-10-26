"""HTTP API for search and browse, plus the built web app.

    ACCESS_TOKEN=... uvicorn api.main:app   # API at /api, web app at / (after `npm run build` in web/)

Access is by team link only (api/access.py): `budget share-link --base-url https://host`.
"""

from __future__ import annotations

import csv
import io
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Body, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine

from pipeline.config import database_url

from . import qa, summary
from .access import LinkAccessMiddleware
from .programs import (dashboard, data_status, get_program, open_issues, program_detail, report_issue, set_watch,
                       watchlist)
from .search import MAX_PAGE_SIZE, SORTS, SearchParams, export_rows, facets, search

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"

app = FastAPI(title="DoW Budget Search", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(LinkAccessMiddleware)


@lru_cache
def get_engine() -> Engine:
    return create_engine(database_url(), pool_pre_ping=True)


def get_conn():
    with get_engine().connect() as conn:
        yield conn


Conn = Annotated[Connection, Depends(get_conn)]


def search_params(
    q: str = "",
    cycle: str | None = Query(None, description="Budget cycle, e.g. PB2027; omit for all releases"),
    exhibit: list[str] = Query([]),
    service: list[str] = Query([]),
    account: list[str] = Query([]),
    budget_activity: list[str] = Query([]),
    min_amount: int | None = Query(None, description="Budget-year amount floor, $ thousands"),
    max_amount: int | None = Query(None, description="Budget-year amount ceiling, $ thousands"),
    sort: Literal[tuple(SORTS)] = "relevance",  # type: ignore[valid-type]
    order: Literal["asc", "desc"] = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
) -> SearchParams:
    return SearchParams(q=q[:200], cycle=cycle or None, exhibit=exhibit, service=service, account=account,
                        budget_activity=budget_activity, min_amount=min_amount, max_amount=max_amount,
                        sort=sort, order=order, page=page, page_size=page_size)


Params = Annotated[SearchParams, Depends(search_params)]


@app.get("/api/health")
def health(conn: Conn) -> dict:
    """Open (no team link needed) for the host and the daily monitor: up, and how much is loaded.
    Counts only, never data."""
    s = data_status(conn)
    return {"ok": True, "releases": len(s["releases"]), "lines": s["lines"], "explanations": s["explanations"]}


@app.get("/api/status")
def status_endpoint(conn: Conn) -> dict:
    """When each release was loaded, totals, and open problem reports."""
    return data_status(conn)


@app.get("/api/summary")
def summary_endpoint(conn: Conn, kind: Literal["trends", "execution"] = "trends", cycle: str | None = None,
                     fy: int = Query(2025, ge=2024, le=2026), exhibit: Literal["R-1", "P-1"] | None = None,
                     service: str | None = None, ai: bool = False) -> dict:
    """One-page summary: 'trends' (one release's budget year vs current year: services, sectors,
    largest moves) or 'execution' (one fiscal year's request vs enacted vs actual). Computed from
    the data; ai=1 adds a Claude-written narrative only if every figure in it checks out."""
    s = summary.trends(conn, cycle or None, exhibit, service or None) if kind == "trends" \
        else summary.execution(conn, fy, exhibit, service or None)
    if ai:
        s["ai"] = summary.ai_narrative(s)
    s["ai_available"] = qa.configured()
    return s


EVAL_RESULTS = Path(__file__).resolve().parent.parent / "docs" / "eval" / "results.json"


@app.get("/api/validation")
def validation_endpoint(conn: Conn) -> dict:
    """The latest evaluation run (scripts/evaluate.py) with the live data status, for the
    Validation Studio page."""
    import json

    if not EVAL_RESULTS.exists():
        raise HTTPException(404, "no evaluation results yet: run scripts/evaluate.py")
    results = json.loads(EVAL_RESULTS.read_text())
    ids = [f["id"] for f in results.get("e1_lines", {}).get("failures", [])]
    keys = {}
    if ids:
        from sqlalchemy import text as sql

        keys = {str(r.id): r.program_key for r in conn.execute(sql(
            "SELECT li.id, p.program_key FROM budget_line_item li JOIN program p ON p.id = li.program_id WHERE li.id = ANY(:ids)"
        ), {"ids": ids})}
    return {"results": results, "status": data_status(conn), "program_keys": keys}


@app.post("/api/programs/{key}/issues")
def report_issue_endpoint(conn: Conn, key: str,
                          message: Annotated[str, Body(min_length=3, max_length=2000)],
                          name: Annotated[str | None, Body(max_length=60)] = None) -> dict:
    """Report a problem with a program's figures or explanations; the data owner reviews it."""
    program = get_program(conn, key)
    if program is None:
        raise HTTPException(404, f"no program {key!r}")
    issue_id = report_issue(conn, program["id"], message.strip(), (name or "").strip() or None)
    conn.commit()
    return {"id": issue_id, "status": "open"}


@app.get("/api/issues")
def issues_endpoint(conn: Conn) -> list[dict]:
    """Open problem reports, newest first."""
    return open_issues(conn)


@app.get("/api/search")
def search_endpoint(conn: Conn, params: Params) -> dict:
    """Keyword search and filtered browse over published line items, $ thousands."""
    return search(conn, params)


@app.get("/api/facets")
def facets_endpoint(conn: Conn, cycle: str | None = None) -> dict:
    """Filter choices with line counts, for one release or all."""
    return facets(conn, cycle or None)


@app.get("/api/programs/{key}")
def program_endpoint(conn: Conn, key: str) -> dict:
    """One program across every published release: funding history (best-available figure per
    fiscal year, with year-over-year change), every release's figures, source pages, watch state.
    key: PE number for RDT&E, 'account:BLI' for procurement."""
    detail = program_detail(conn, key)
    if detail is None:
        raise HTTPException(404, f"no program {key!r}")
    return detail


def _watch(conn: Connection, key: str, watched: bool, name: str | None) -> dict:
    program = get_program(conn, key)
    if program is None:
        raise HTTPException(404, f"no program {key!r}")
    set_watch(conn, program["id"], watched, actor=(name or "").strip()[:60] or None)
    conn.commit()
    return {"program_key": key, "watched": watched}


WatchName = Annotated[str | None, Body(embed=True, max_length=60,
                                       description="Optional: who is starring, shown on the watchlist")]


@app.put("/api/programs/{key}/watch")
def watch_endpoint(conn: Conn, key: str, name: WatchName = None) -> dict:
    """Star a program on the shared team watchlist. Anyone with the team link can star or
    unstar; each change is logged with the name given (GET /api/dashboard lists them)."""
    return _watch(conn, key, True, name)


@app.delete("/api/programs/{key}/watch")
def unwatch_endpoint(conn: Conn, key: str, name: WatchName = None) -> dict:
    """Unstar. Requires a name: anyone with the team link can remove a star, so removals are
    always attributed in the history (and can be undone from the watchlist)."""
    if not (name or "").strip():
        raise HTTPException(400, "Give your name to remove a star; removals are shown in the watchlist history.")
    return _watch(conn, key, False, name)


@app.get("/api/watches")
def watches_endpoint(conn: Conn) -> list[dict]:
    """The team watchlist with each program's latest-release totals."""
    return watchlist(conn)


@app.get("/api/dashboard")
def dashboard_endpoint(conn: Conn) -> dict:
    """Team dashboard (spec 4.4): watched programs with their funding trend and year-over-year
    change, largest moves first."""
    return dashboard(conn)


@app.get("/api/ask")
def ask_status() -> dict:
    """Whether Q&A is switched on (it needs ANTHROPIC_API_KEY)."""
    return {"enabled": qa.configured(), "max_question_chars": qa.MAX_QUESTION_CHARS}


@app.post("/api/ask")
def ask_endpoint(conn: Conn, question: Annotated[str, Body(max_length=qa.MAX_QUESTION_CHARS)],
                 history: Annotated[list[dict], Body(max_length=20)] = []) -> dict:  # noqa: B006
    """Ask a question in plain English. The answer cites a line item, program or total for every
    dollar figure ([1], [2], ... matching `citations`, each with its source PDF page), lists any
    figure it couldn't match to its sources, and says so when nothing in the data answers it.
    history: earlier turns as [{question, answer}] for follow-ups."""
    if not qa.configured():
        raise HTTPException(503, "Q&A isn't set up on this server: set ANTHROPIC_API_KEY.")
    if not qa.limiter.allow():
        raise HTTPException(429, "The team's hourly question limit is used up; try again later.")
    try:
        return qa.ask(conn, question, history)
    except qa.QAError as e:
        raise HTTPException(e.status, str(e)) from e


EXPORT_COLUMNS = [
    "budget_cycle", "exhibit_type", "service_branch", "appropriation_account", "appropriation_title",
    "organization", "budget_activity", "budget_activity_title", "line_number", "program_element",
    "line_item_number", "program_title", "prior_year_amount", "current_year_amount", "budget_year_amount",
    "budget_year_discretionary", "budget_year_mandatory", "prior_year_quantity", "current_year_quantity",
    "budget_year_quantity", "change_amount", "change_pct", "source_pdf_link",
]


MONEY_COLUMNS = {"prior_year_amount", "current_year_amount", "budget_year_amount", "budget_year_discretionary",
                 "budget_year_mandatory", "change_amount"}


@app.get("/api/search.csv")
def export_endpoint(conn: Conn, params: Params,
                    units: Literal["k", "m"] = Query("m", description="m = $ millions (as on screen), k = $ thousands (as published)")) -> StreamingResponse:
    """The same search as a CSV file, up to 20,000 rows. Money columns say their unit in the
    header ($M by default, matching the screen; $K as published)."""
    rows = export_rows(conn, params)
    suffix = "_$M" if units == "m" else "_$K"
    header = {c: f"{c}{suffix}" if c in MONEY_COLUMNS else c for c in EXPORT_COLUMNS}
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([header[c] for c in EXPORT_COLUMNS])
    for r in rows:
        out = []
        for c in EXPORT_COLUMNS:
            v = r.get(c)
            if c == "change_pct":
                v = "" if v is None else round(v * 100, 1)
            elif c in MONEY_COLUMNS and v is not None and units == "m":
                v = round(v / 1000, 3)
            out.append("" if v is None else v)
        writer.writerow(out)
    buf.seek(0)
    name = f"budget-search-{'millions' if units == 'm' else 'thousands'}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def web_app(path: str) -> FileResponse:
        """The single-page app; client-side routes fall back to index.html (never API paths)."""
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "no such API endpoint")
        candidate = (WEB_DIST / path).resolve()
        if path and candidate.is_file() and WEB_DIST in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(WEB_DIST / "index.html")
