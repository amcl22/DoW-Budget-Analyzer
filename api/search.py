"""Search and browse queries over published line items (the `line_item_flat` view).

Every user value is a bound parameter; sort keys and filters come from fixed whitelists.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.engine import Connection

MAX_PAGE_SIZE = 200
MAX_EXPORT_ROWS = 20000
# ts_headline markers: control characters, so the snippet stays plain text and the client
# highlights it without ever rendering HTML from the data
MARK_START, MARK_END = "\u0002", "\u0003"

CHANGE_PCT = """CASE WHEN coalesce(f.current_year_amount, 0) > 0
    THEN (coalesce(f.budget_year_amount, 0) - f.current_year_amount)::float / f.current_year_amount END"""

SORTS = {
    "relevance": "rank",
    "title": "f.program_title",
    "number": "coalesce(f.program_element, f.line_item_number)",
    "service": "f.service_branch",
    "exhibit": "f.exhibit_type",
    "account": "f.appropriation_account",
    "budget_activity": "f.budget_activity_title",
    "cycle": "f.fiscal_year",
    "prior": "f.prior_year_amount",
    "current": "f.current_year_amount",
    "budget": "f.budget_year_amount",
    "change": "change_pct",
}


@dataclass
class SearchParams:
    q: str = ""
    cycle: str | None = None            # 'PB2027'; None = every published release
    exhibit: list[str] = field(default_factory=list)
    service: list[str] = field(default_factory=list)
    account: list[str] = field(default_factory=list)
    budget_activity: list[str] = field(default_factory=list)   # BA titles
    min_amount: int | None = None       # budget-year $K
    max_amount: int | None = None
    sort: str = "relevance"
    order: str = "desc"
    page: int = 1
    page_size: int = 50


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _where(p: SearchParams) -> tuple[str, dict, str]:
    """(WHERE clause, bind params, rank expression)."""
    clauses, params = [], {}
    rank = "0"
    q = p.q.strip()
    if q:
        params.update(q=q, q_prefix=_like_escape(q) + "%", q_like="%" + _like_escape(q) + "%")
        clauses.append(
            "(li.search_tsv @@ websearch_to_tsquery('english', :q)"
            " OR f.program_element ILIKE :q_prefix OR f.line_item_number ILIKE :q_prefix"
            " OR f.program_title ILIKE :q_like)"
        )
        rank = (
            "ts_rank_cd(li.search_tsv, websearch_to_tsquery('english', :q))"
            " + CASE WHEN upper(coalesce(f.program_element, f.line_item_number)) = upper(:q) THEN 10 ELSE 0 END"
            # a title match outranks lines that only mention the words in their description
            " + CASE WHEN f.program_title ILIKE :q_like"
            " OR to_tsvector('english', f.program_title) @@ websearch_to_tsquery('english', :q) THEN 5 ELSE 0 END"
        )
    if p.cycle:
        clauses.append("f.budget_cycle = :cycle")
        params["cycle"] = p.cycle
    for name, column in (("exhibit", "f.exhibit_type"), ("service", "f.service_branch"),
                         ("account", "f.appropriation_account"), ("budget_activity", "f.budget_activity_title")):
        values = getattr(p, name)
        if values:
            clauses.append(f"{column} = ANY(:{name})")
            params[name] = list(values)
    if p.min_amount is not None:
        clauses.append("coalesce(f.budget_year_amount, 0) >= :min_amount")
        params["min_amount"] = p.min_amount
    if p.max_amount is not None:
        clauses.append("coalesce(f.budget_year_amount, 0) <= :max_amount")
        params["max_amount"] = p.max_amount
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params, rank


def _select(p: SearchParams) -> tuple[str, dict]:
    where, params, rank = _where(p)
    snippet = "NULL"
    if p.q.strip():
        snippet = (
            "ts_headline('english', f.raw_description_text, websearch_to_tsquery('english', :q),"
            f" 'MaxWords=28, MinWords=10, MaxFragments=1, StartSel={MARK_START}, StopSel={MARK_END}')"
        )
    sort = SORTS.get(p.sort, "rank")
    direction = "ASC" if p.order == "asc" else "DESC"
    sql = f"""
        SELECT f.id, f.fiscal_year, f.budget_cycle, f.exhibit_type, f.service_branch,
               f.appropriation_account, acc.title AS appropriation_title, f.organization,
               f.budget_activity, f.budget_activity_title, f.budget_subactivity_title, f.line_number,
               f.program_element, f.line_item_number, pr.program_key, f.program_id, f.program_title,
               f.prior_year_amount, f.current_year_amount, f.budget_year_amount,
               f.budget_year_discretionary, f.budget_year_mandatory,
               f.prior_year_quantity, f.current_year_quantity, f.budget_year_quantity,
               f.source_pdf_link, f.source_page_number, f.include_in_toa,
               (SELECT s.ref_kind FROM line_item_source_ref s WHERE s.line_item_id = f.id
                ORDER BY (s.ref_kind IN ('r2_justification', 'p40_justification')) DESC, s.is_primary DESC,
                         s.page_number LIMIT 1) AS source_kind,
               (f.raw_description_text IS NOT NULL) AS has_description,
               EXISTS (SELECT 1 FROM program_watch w WHERE w.program_id = f.program_id AND w.user_id IS NULL)
                   AS watched,
               {CHANGE_PCT} AS change_pct,
               ({rank}) AS rank,
               CASE WHEN f.raw_description_text IS NULL THEN NULL ELSE {snippet} END AS snippet
        FROM line_item_flat f
        JOIN budget_line_item li ON li.id = f.id
        JOIN appropriation_account acc ON acc.code = f.appropriation_account
        JOIN program pr ON pr.id = f.program_id
        {where}
        ORDER BY {sort} {direction} NULLS LAST, f.budget_year_amount DESC NULLS LAST, f.id
    """
    return sql, params


def search(conn: Connection, p: SearchParams) -> dict:
    where, params, _ = _where(p)
    totals = conn.execute(text(f"""
        SELECT count(*) AS total, sum(f.budget_year_amount)::bigint AS budget_year_total,
               sum(f.current_year_amount)::bigint AS current_year_total
        FROM line_item_flat f JOIN budget_line_item li ON li.id = f.id {where}
    """), params).mappings().one()
    page_size = max(1, min(p.page_size, MAX_PAGE_SIZE))
    page = max(1, p.page)
    sql, params = _select(p)
    rows = conn.execute(text(sql + " LIMIT :limit OFFSET :offset"),
                        {**params, "limit": page_size, "offset": (page - 1) * page_size}).mappings().all()
    return {
        "total": totals["total"],
        "budget_year_total": totals["budget_year_total"],
        "current_year_total": totals["current_year_total"],
        "page": page,
        "page_size": page_size,
        "results": [dict(r) for r in rows],
    }


def export_rows(conn: Connection, p: SearchParams) -> list[dict]:
    sql, params = _select(p)
    return [dict(r) for r in conn.execute(text(sql + " LIMIT :limit"), {**params, "limit": MAX_EXPORT_ROWS}).mappings()]


def facets(conn: Connection, cycle: str | None) -> dict:
    cycles = conn.execute(text("""
        SELECT budget_cycle, fiscal_year, count(*) AS lines, array_agg(DISTINCT exhibit_type) AS exhibits
        FROM line_item_flat GROUP BY budget_cycle, fiscal_year ORDER BY fiscal_year DESC
    """)).mappings().all()
    where, params = ("WHERE f.budget_cycle = :cycle", {"cycle": cycle}) if cycle else ("", {})

    def grouped(select: str, group: str) -> list[dict]:
        return [dict(r) for r in conn.execute(text(
            f"SELECT {select}, count(*) AS lines FROM line_item_flat f "
            f"JOIN appropriation_account acc ON acc.code = f.appropriation_account {where} "
            f"GROUP BY {group} ORDER BY {group}"
        ), params).mappings()]

    return {
        "cycles": [dict(c) for c in cycles],
        "exhibits": grouped("f.exhibit_type AS value", "f.exhibit_type"),
        "services": grouped("f.service_branch AS value", "f.service_branch"),
        "accounts": grouped("f.appropriation_account AS value, acc.title, f.exhibit_type AS exhibit",
                            "f.appropriation_account, acc.title, f.exhibit_type"),
        "budget_activities": grouped("f.budget_activity_title AS value, f.exhibit_type AS exhibit",
                                     "f.exhibit_type, f.budget_activity_title"),
    }
