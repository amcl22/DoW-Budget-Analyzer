"""HTTP API for search and browse, plus the built web app.

    uvicorn api.main:app            # API at /api, web app at / (after `npm run build` in web/)
"""

from __future__ import annotations

import csv
import io
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from pipeline.config import database_url

from .search import MAX_PAGE_SIZE, SORTS, SearchParams, export_rows, facets, search

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"

app = FastAPI(title="DoW Budget Search", docs_url="/api/docs", openapi_url="/api/openapi.json")


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
        """The single-page app; client-side routes fall back to index.html."""
        candidate = (WEB_DIST / path).resolve()
        if path and candidate.is_file() and WEB_DIST in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(WEB_DIST / "index.html")
