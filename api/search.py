"""Search and browse queries over published line items (the `line_item_flat` view).

Every user value is a bound parameter; sort keys and filters come from fixed whitelists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from sqlalchemy import text
from sqlalchemy.engine import Connection

MAX_PAGE_SIZE = 200
# one key per budget activity, whatever its spelling or capitalization in a given exhibit
BA_KEY = """CASE WHEN lower(regexp_replace(trim(f.budget_activity_title), '\\s+', ' ', 'g'))
                   IN ('spares', 'spare and repair parts') THEN 'spares and repair parts'
              ELSE lower(regexp_replace(trim(f.budget_activity_title), '\\s+', ' ', 'g')) END"""


def ba_key(title: str) -> str:
    k = " ".join(title.split()).lower()
    return "spares and repair parts" if k in ("spares", "spare and repair parts") else k
MAX_EXPORT_ROWS = 20000
# ts_headline markers: control characters, so the snippet stays plain text and the client
# highlights it without ever rendering HTML from the data
MARK_START, MARK_END = "\u0002", "\u0003"

CHANGE_PCT = """CASE WHEN coalesce(f.current_year_amount, 0) > 0
    THEN (coalesce(f.budget_year_amount, 0) - f.current_year_amount)::float / f.current_year_amount END"""
CHANGE_AMOUNT = "(coalesce(f.budget_year_amount, 0) - coalesce(f.current_year_amount, 0))"

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
    "change_amount": "change_amount",
    "execution": "CASE WHEN ex.exec_enacted > 0 AND NOT ex.exec_is_cr THEN (ex.exec_actual - ex.exec_enacted)::float / ex.exec_enacted END",
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


# Service words in a query act as the Service filter ("artificial intelligence army").
SERVICE_WORDS = {"army": "Army", "navy": "Navy", "air force": "Air Force", "space force": "Space Force",
                 "defense-wide": "Defense-Wide", "defense wide": "Defense-Wide"}
# Too common in titles to say which program is meant on their own.
WEAK_WORDS = {"the", "and", "of", "for", "a", "an", "in", "to", "program", "programs", "system", "systems",
              "development", "support", "mods", "mod", "class", "advanced", "technology"}


RARE_TITLES = 15


def interpret(p: SearchParams) -> SearchParams:
    """Move service words from the query into the Service filter, when other words remain."""
    q = p.q.strip()
    found = [name for word, name in SERVICE_WORDS.items() if re.search(rf"(?<![\w-]){word}(?![\w-])", q, re.I)]
    if not found:
        return p
    rest = q
    for word in SERVICE_WORDS:
        rest = re.sub(rf"(?<![\w-]){word}(?![\w-])", " ", rest, flags=re.I)
    rest = " ".join(rest.split())
    if not rest:
        return p
    return replace(p, q=rest, service=p.service or sorted(set(found)))


def _title_words(q: str) -> list[str]:
    """Distinctive words of a query, each of which may match a program title on its own."""
    words = [w.strip("\"'(),.") for w in q.split()]
    return [w for w in dict.fromkeys(words) if len(w) >= 3 and w.lower() not in WEAK_WORDS][:6]


def _where(p: SearchParams) -> tuple[str, dict, str]:
    """(WHERE clause, bind params, rank expression)."""
    p = interpret(p)
    clauses, params = [], {}
    rank = "0"
    q = p.q.strip()
    if q:
        params.update(q=q, q_prefix=_like_escape(q) + "%", q_like="%" + _like_escape(q) + "%")
        words = _title_words(q) if len(q.split()) > 1 else []
        word_match, word_rank = [], []
        for i, w in enumerate(words):
            params[f"w{i}"] = "%" + _like_escape(w) + "%"
            # only distinctive words widen the match, so common ones ("base", "missile") don't swell
            # the result count and totals
            word_match.append(f" OR (f.program_title ILIKE :w{i} AND (SELECT count(*) FROM program t"
                              f" WHERE t.latest_title ILIKE :w{i}) <= {RARE_TITLES})")
            # rarer words count more: "patriot" says more than "missile"
            word_rank.append(f" + CASE WHEN f.program_title ILIKE :w{i} THEN 3 + 6.0 / greatest(1,"
                             f" (SELECT count(*) FROM program t WHERE t.latest_title ILIKE :w{i})) ELSE 0 END")
        clauses.append(
            "(li.search_tsv @@ websearch_to_tsquery('english', :q)"
            " OR f.program_element ILIKE :q_prefix OR f.line_item_number ILIKE :q_prefix"
            " OR f.program_title ILIKE :q_like" + "".join(word_match) + ")"
        )
        rank = (
            "ts_rank_cd(li.search_tsv, websearch_to_tsquery('english', :q))"
            " + CASE WHEN upper(coalesce(f.program_element, f.line_item_number)) = upper(:q) THEN 10 ELSE 0 END"
            # a title match outranks lines that only mention the words in their description
            " + CASE WHEN f.program_title ILIKE :q_like"
            " OR to_tsvector('english', f.program_title) @@ websearch_to_tsquery('english', :q) THEN 5 ELSE 0 END"
            # a line matching every word in its description outranks one matching a single title word
            " + CASE WHEN li.search_tsv @@ websearch_to_tsquery('english', :q) THEN 2 ELSE 0 END"
            + "".join(word_rank)
        )
    if p.cycle:
        clauses.append("f.budget_cycle = :cycle")
        params["cycle"] = p.cycle
    for name, column in (("exhibit", "f.exhibit_type"), ("service", "f.service_branch"),
                         ("account", "f.appropriation_account")):
        values = getattr(p, name)
        if values:
            clauses.append(f"{column} = ANY(:{name})")
            params[name] = list(values)
    if p.budget_activity:
        clauses.append(f"({BA_KEY}) = ANY(:budget_activity)")
        params["budget_activity"] = [ba_key(v) for v in p.budget_activity]
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
               {CHANGE_AMOUNT} AS change_amount,
               f.fiscal_year - 2 AS exec_fiscal_year, ex.exec_enacted, ex.exec_actual, ex.exec_is_cr,
               ({rank}) AS rank,
               CASE WHEN f.raw_description_text IS NULL THEN NULL ELSE {snippet} END AS snippet
        FROM line_item_flat f
        JOIN budget_line_item li ON li.id = f.id
        JOIN appropriation_account acc ON acc.code = f.appropriation_account
        JOIN program pr ON pr.id = f.program_id
        -- above or below budget: the program's prior-year actual (this release) against what was
        -- enacted for that year (the release before), across all of the program's lines
        LEFT JOIN LATERAL (
            SELECT sum(h.amount_thousands) FILTER (WHERE h.release_fiscal_year = f.fiscal_year - 1
                                                     AND h.amount_type IN ('enacted', 'cr'))::bigint AS exec_enacted,
                   sum(h.amount_thousands) FILTER (WHERE h.release_fiscal_year = f.fiscal_year
                                                     AND h.amount_type = 'actual')::bigint AS exec_actual,
                   coalesce(bool_or(h.amount_type = 'cr' AND h.release_fiscal_year = f.fiscal_year - 1), false) AS exec_is_cr
            FROM program_funding_history h
            WHERE h.program_id = f.program_id AND h.funds_fiscal_year = f.fiscal_year - 2
        ) ex ON true
        {where}
        ORDER BY {sort} {direction} NULLS LAST, f.budget_year_amount DESC NULLS LAST, f.id
    """
    return sql, params


def search(conn: Connection, p: SearchParams) -> dict:
    where, params, _ = _where(p)
    totals = conn.execute(text(f"""
        SELECT count(*) AS total, sum(f.budget_year_amount)::bigint AS budget_year_total,
               sum(f.current_year_amount)::bigint AS current_year_total,
               sum(f.prior_year_amount)::bigint AS prior_year_total
        FROM line_item_flat f JOIN budget_line_item li ON li.id = f.id {where}
    """), params).mappings().one()
    # the same filters' totals in every release (ignoring the release filter), for the funding
    # trend above the results
    trend_where, trend_params, _ = _where(replace(p, cycle=None))
    by_cycle = conn.execute(text(f"""
        SELECT f.budget_cycle, f.fiscal_year, count(*) AS lines,
               sum(f.prior_year_amount)::bigint AS prior_year_total,
               sum(f.current_year_amount)::bigint AS current_year_total,
               sum(f.budget_year_amount)::bigint AS budget_year_total
        FROM line_item_flat f JOIN budget_line_item li ON li.id = f.id {trend_where}
        GROUP BY f.budget_cycle, f.fiscal_year ORDER BY f.fiscal_year
    """), trend_params).mappings().all()
    page_size = max(1, min(p.page_size, MAX_PAGE_SIZE))
    page = max(1, p.page)
    sql, params = _select(p)
    rows = conn.execute(text(sql + " LIMIT :limit OFFSET :offset"),
                        {**params, "limit": page_size, "offset": (page - 1) * page_size}).mappings().all()
    return {
        "total": totals["total"],
        "budget_year_total": totals["budget_year_total"],
        "current_year_total": totals["current_year_total"],
        "prior_year_total": totals["prior_year_total"],
        "totals_by_cycle": [dict(r) for r in by_cycle],
        "page": page,
        "page_size": page_size,
        "results": [dict(r) for r in rows],
        "interpreted": _interpreted(p),
    }


def _interpreted(p: SearchParams) -> dict | None:
    """How the query was read, when service words became the Service filter."""
    i = interpret(p)
    return None if i.q == p.q.strip() else {"q": i.q, "service": i.service}


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

    accounts = [dict(r) for r in conn.execute(text(f"""
        SELECT f.appropriation_account AS value, acc.title, array_agg(DISTINCT f.exhibit_type) AS exhibits,
               count(*) AS lines
        FROM line_item_flat f JOIN appropriation_account acc ON acc.code = f.appropriation_account {where}
        GROUP BY f.appropriation_account, acc.title ORDER BY f.appropriation_account
    """), params).mappings()]
    # one entry per activity: blank titles left out, spellings merged, the most common
    # non-all-caps spelling shown
    ba_where = (where + " AND " if where else "WHERE ") + "coalesce(trim(f.budget_activity_title), '') <> ''"
    activities = [dict(r) for r in conn.execute(text(f"""
        SELECT ({BA_KEY}) AS value,
               coalesce(mode() WITHIN GROUP (ORDER BY f.budget_activity_title)
                          FILTER (WHERE f.budget_activity_title <> upper(f.budget_activity_title)),
                        min(f.budget_activity_title)) AS label,
               array_agg(DISTINCT f.exhibit_type) AS exhibits, count(*) AS lines
        FROM line_item_flat f {ba_where}
        GROUP BY 1 ORDER BY 2
    """), params).mappings()]
    return {
        "cycles": [dict(c) for c in cycles],
        "exhibits": grouped("f.exhibit_type AS value", "f.exhibit_type"),
        "services": grouped("f.service_branch AS value", "f.service_branch"),
        "accounts": accounts,
        "budget_activities": activities,
    }
