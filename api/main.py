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
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from pipeline.config import database_url

from . import qa
from .access import LinkAccessMiddleware
from .programs import dashboard, get_program, program_detail, set_watch, watchlist
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
    conn.execute(text("SELECT 1"))
    return {"ok": True}


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


def _watch(conn: Connection, key: str, watched: bool) -> dict:
    program = get_program(conn, key)
    if program is None:
        raise HTTPException(404, f"no program {key!r}")
    set_watch(conn, program["id"], watched)
    conn.commit()
    return {"program_key": key, "watched": watched}


@app.put("/api/programs/{key}/watch")
def watch_endpoint(conn: Conn, key: str) -> dict:
    """Pin a program to the team watchlist (personal watchlists arrive with accounts)."""
    return _watch(conn, key, True)


@app.delete("/api/programs/{key}/watch")
def unwatch_endpoint(conn: Conn, key: str) -> dict:
    return _watch(conn, key, False)


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
    "budget_year_quantity", "change_pct", "source_pdf_link",
]


@app.get("/api/search.csv")
def export_endpoint(conn: Conn, params: Params) -> StreamingResponse:
    """The same search as a CSV file ($ thousands), up to 20,000 rows."""
    rows = export_rows(conn, params)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({**r, "change_pct": "" if r["change_pct"] is None else round(r["change_pct"] * 100, 1)})
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="budget-search.csv"'})


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
