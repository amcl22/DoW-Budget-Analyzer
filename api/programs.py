"""Program detail (spec 4.2): funding history across releases, year-over-year change, every
source page, and the watch toggle."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.engine import Connection

FLAG_THRESHOLD = 0.20                   # year-over-year change worth flagging (spec 4.2: ±20%)
TEAM = None                             # user_id of the shared team watchlist (access is by team
                                        # link, with no accounts, so every watch is the team's)
FIRMNESS = {"actual": 4, "enacted": 3, "cr": 2, "request": 1, "estimate": 0}


def get_program(conn: Connection, key: str) -> dict | None:
    return conn.execute(text("""
        SELECT id, exhibit_family, program_key, latest_title, is_classified_rollup
        FROM program WHERE program_key = :key ORDER BY exhibit_family LIMIT 1
    """), {"key": key}).mappings().first()


def _lines(conn: Connection, program_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(text("""
        SELECT f.id, f.budget_cycle, f.fiscal_year, f.exhibit_type, f.service_branch,
               f.appropriation_account, acc.title AS appropriation_title, f.organization,
               f.budget_activity, f.budget_activity_title, f.budget_subactivity_title, f.line_number,
               f.program_element, f.line_item_number, f.program_title, f.include_in_toa,
               f.prior_year_amount, f.current_year_amount, f.budget_year_amount,
               f.budget_year_discretionary, f.budget_year_mandatory,
               f.prior_year_quantity, f.current_year_quantity, f.budget_year_quantity,
               f.source_pdf_link, f.source_page_number
        FROM line_item_flat f JOIN appropriation_account acc ON acc.code = f.appropriation_account
        WHERE f.program_id = :pid
        ORDER BY f.fiscal_year DESC, f.appropriation_account, f.budget_activity, f.line_number
    """), {"pid": program_id}).mappings()]


def _history(conn: Connection, program_id: int) -> tuple[list[dict], list[dict]]:
    """(every release's figure for every fiscal year, the best-available series with YoY)."""
    rows = [dict(r) for r in conn.execute(text("""
        SELECT budget_cycle, release_fiscal_year, funds_fiscal_year, amount_type,
               amount_thousands::bigint AS amount_thousands, quantity::bigint AS quantity, is_latest
        FROM program_funding_history WHERE program_id = :pid
        ORDER BY funds_fiscal_year, release_fiscal_year
    """), {"pid": program_id}).mappings()]

    # the latest release reporting a year is the best figure; if a release reports a year under
    # two types (rare), keep the firmer one
    best: dict[int, dict] = {}
    for r in rows:
        if not r["is_latest"]:
            continue
        cur = best.get(r["funds_fiscal_year"])
        if cur is None or FIRMNESS.get(r["amount_type"], 0) > FIRMNESS.get(cur["amount_type"], 0):
            best[r["funds_fiscal_year"]] = r
    series, prev = [], None
    for fy in sorted(best):
        b = best[fy]
        change = None
        if prev is not None and prev["amount_thousands"]:
            change = (b["amount_thousands"] - prev["amount_thousands"]) / abs(prev["amount_thousands"])
        series.append({
            "fiscal_year": fy,
            "amount_thousands": b["amount_thousands"],
            "quantity": b["quantity"],
            "amount_type": b["amount_type"],
            "source_cycle": b["budget_cycle"],
            "change_pct": change,
            "flagged": change is not None and abs(change) >= FLAG_THRESHOLD,
        })
        prev = b
    return rows, series


def _sources(conn: Connection, program_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(text("""
        SELECT li.budget_cycle, li.fiscal_year, li.exhibit_type, li.line_number, li.appropriation_account,
               li.budget_activity, s.ref_kind, s.page_number, s.printed_page_label, s.section, s.is_primary,
               s.amount_verified, d.url || '#page=' || s.page_number AS link, d.url AS document_url
        FROM budget_line_item li
        JOIN ingestion_run r ON r.id = li.ingestion_run_id AND r.status = 'published'
        JOIN line_item_source_ref s ON s.line_item_id = li.id
        JOIN source_document d ON d.id = s.source_document_id
        WHERE li.program_id = :pid
        ORDER BY li.fiscal_year DESC, (s.ref_kind LIKE '%justification') DESC, s.is_primary DESC, s.page_number
    """), {"pid": program_id}).mappings()]


def _cost_elements(conn: Connection, line_ids: list[int]) -> list[dict]:
    """P-1: the latest release's cost-type rows (weapon system cost, advance procurement, ...)."""
    if not line_ids:
        return []
    rows = conn.execute(text("""
        SELECT e.line_item_id, e.source_row_number, e.cost_type, e.cost_type_title, e.is_add,
               e.funds_fiscal_year, e.funding_category, e.amount_thousands, e.quantity
        FROM line_item_cost_element e
        WHERE e.line_item_id = ANY(:ids) AND e.funding_category = 'total'
        ORDER BY e.line_item_id, e.source_row_number, e.funds_fiscal_year
    """), {"ids": line_ids}).mappings()
    out: dict[tuple, dict] = {}
    for r in rows:
        k = (r["line_item_id"], r["source_row_number"])
        e = out.setdefault(k, {"line_item_id": r["line_item_id"], "cost_type": r["cost_type"],
                               "cost_type_title": r["cost_type_title"], "is_add": r["is_add"], "amounts": {}})
        e["amounts"][r["funds_fiscal_year"]] = {"amount_thousands": r["amount_thousands"], "quantity": r["quantity"]}
    return list(out.values())


def is_watched(conn: Connection, program_id: int, user_id: str | None = TEAM) -> bool:
    return conn.execute(text(
        "SELECT 1 FROM program_watch WHERE program_id = :pid AND coalesce(user_id, '') = coalesce(:uid, '')"
    ), {"pid": program_id, "uid": user_id}).first() is not None


def program_detail(conn: Connection, key: str) -> dict | None:
    program = get_program(conn, key)
    if program is None:
        return None
    pid = program["id"]
    lines = _lines(conn, pid)
    history, series = _history(conn, pid)
    latest_cycle = lines[0]["budget_cycle"] if lines else None
    latest_lines = [l for l in lines if l["budget_cycle"] == latest_cycle]
    description = conn.execute(text("""
        SELECT f.raw_description_text, f.budget_cycle FROM line_item_flat f
        WHERE f.program_id = :pid AND f.raw_description_text IS NOT NULL
        ORDER BY f.fiscal_year DESC LIMIT 1
    """), {"pid": pid}).mappings().first()

    by_cycle: dict[str, list] = defaultdict(list)
    for h in history:
        by_cycle[h["budget_cycle"]].append(h)
    return {
        "program": dict(program),
        "latest_cycle": latest_cycle,
        "latest_lines": latest_lines,
        "lines": lines,
        "series": series,
        "history": history,
        "releases": sorted(by_cycle, key=lambda c: by_cycle[c][0]["release_fiscal_year"]),
        "description": dict(description) if description else None,
        "sources": _sources(conn, pid),
        "cost_elements": _cost_elements(conn, [l["id"] for l in latest_lines]) if program["exhibit_family"] == "PROC" else [],
        "watched": is_watched(conn, pid),
        "flag_threshold": FLAG_THRESHOLD,
    }


def set_watch(conn: Connection, program_id: int, watched: bool, user_id: str | None = TEAM) -> None:
    if watched:
        conn.execute(text("""
            INSERT INTO program_watch (program_id, user_id) VALUES (:pid, :uid)
            ON CONFLICT (program_id, coalesce(user_id, '')) DO NOTHING
        """), {"pid": program_id, "uid": user_id})
    else:
        conn.execute(text(
            "DELETE FROM program_watch WHERE program_id = :pid AND coalesce(user_id, '') = coalesce(:uid, '')"
        ), {"pid": program_id, "uid": user_id})


def watchlist(conn: Connection, user_id: str | None = TEAM) -> list[dict]:
    """Watched programs with their latest-release totals (the dashboard, step 6, builds on it)."""
    return [dict(r) for r in conn.execute(text("""
        SELECT p.id AS program_id, p.program_key, p.latest_title, p.exhibit_family, w.created_at,
               latest.budget_cycle, latest.service_branch, latest.current_year_amount, latest.budget_year_amount
        FROM program_watch w
        JOIN program p ON p.id = w.program_id
        LEFT JOIN LATERAL (
            SELECT f.budget_cycle, min(f.service_branch) AS service_branch,
                   sum(f.current_year_amount)::bigint AS current_year_amount,
                   sum(f.budget_year_amount)::bigint AS budget_year_amount
            FROM line_item_flat f WHERE f.program_id = p.id
            GROUP BY f.budget_cycle, f.fiscal_year ORDER BY f.fiscal_year DESC LIMIT 1
        ) latest ON true
        WHERE coalesce(w.user_id, '') = coalesce(:uid, '')
        ORDER BY w.created_at DESC
    """), {"uid": user_id}).mappings()]


def dashboard(conn: Connection) -> dict:
    """Watched programs with their best-available funding series and the change between the
    latest release's budget year and the year before; flagged moves first."""
    items = []
    for w in watchlist(conn):
        _, series = _history(conn, w["program_id"])
        by_year = {s["fiscal_year"]: s for s in series}
        latest_fy = conn.execute(text(
            "SELECT max(fiscal_year) FROM line_item_flat WHERE program_id = :pid"
        ), {"pid": w["program_id"]}).scalar()
        budget = by_year.get(latest_fy) if latest_fy else None
        items.append({
            **w,
            "fiscal_year": latest_fy,
            "change_pct": budget["change_pct"] if budget else None,
            "flagged": bool(budget and budget["flagged"]),
            "series": [{k: s[k] for k in ("fiscal_year", "amount_thousands", "amount_type")} for s in series],
        })
    items.sort(key=lambda i: (not i["flagged"], -abs(i["change_pct"] or 0)))
    return {
        "watched": items,
        "flagged": sum(1 for i in items if i["flagged"]),
        "budget_year_total": sum(i["budget_year_amount"] or 0 for i in items),
        "current_year_total": sum(i["current_year_amount"] or 0 for i in items),
        "flag_threshold": FLAG_THRESHOLD,
    }
