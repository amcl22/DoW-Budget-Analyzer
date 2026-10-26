"""One-page summaries: where funding is growing or shrinking, and how budgets were executed.

Two kinds:

  trends     one release's budget year vs. current year: totals, services, sectors (budget
             activities, merged across spellings), the largest program moves, new starts, and
             the multi-year trend (best available figure per fiscal year).
  execution  one fiscal year's request (its own President's Budget), enacted amount or CR level
             (next year's release) and actual (the release two years later): how Congress changed
             the request and whether programs spent above or below what was enacted.

Every number is computed here from the published data; the takeaways are written from those
numbers by code. If ANTHROPIC_API_KEY is set and the caller asks (ai=1), Claude may also write a
short narrative from the same facts. That narrative is shown only if every dollar figure and
percentage in it matches a computed fact; otherwise it is withheld and the reason shown.
Only exhibit-title lines (include_in_toa) are counted, as in the official totals.
"""

from __future__ import annotations

import json
import logging
import os
import re

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .search import BA_KEY

log = logging.getLogger(__name__)

MIN_SECTOR_K = 500_000          # sectors under $500M in both years are left out of the movers
TOP = 8
EXECUTION_YEARS = (2024, 2025, 2026)


def _where(exhibit: str | None, service: str | None) -> tuple[str, dict]:
    clauses, params = ["f.include_in_toa"], {}
    if exhibit:
        clauses.append("f.exhibit_type = :exhibit")
        params["exhibit"] = exhibit
    if service:
        clauses.append("f.service_branch = :service")
        params["service"] = service
    return " AND ".join(clauses), params


def _chg(a: int | None, b: int | None) -> dict:
    a, b = a or 0, b or 0
    return {"from": a, "to": b, "change": b - a, "change_pct": (b - a) / abs(a) if a else None}


def latest_cycle(conn: Connection) -> str:
    return conn.execute(text("SELECT budget_cycle FROM line_item_flat ORDER BY fiscal_year DESC LIMIT 1")).scalar_one()


def trends(conn: Connection, cycle: str | None = None, exhibit: str | None = None, service: str | None = None) -> dict:
    cycle = cycle or latest_cycle(conn)
    where, params = _where(exhibit, service)
    params["cycle"] = cycle
    fy = conn.execute(text("SELECT fiscal_year FROM line_item_flat WHERE budget_cycle = :c LIMIT 1"), {"c": cycle}).scalar_one()
    cyc = f"{where} AND f.budget_cycle = :cycle"

    def grouped(key: str, label: str) -> list[dict]:
        rows = conn.execute(text(f"""
            SELECT {key} AS key, {label} AS label, count(*) AS lines,
                   sum(f.current_year_amount)::bigint AS cy, sum(f.budget_year_amount)::bigint AS by
            FROM line_item_flat f WHERE {cyc} GROUP BY 1 ORDER BY by DESC NULLS LAST
        """), params).mappings()
        return [{"key": r["key"], "label": r["label"], "lines": r["lines"], **_chg(r["cy"], r["by"])} for r in rows]

    total = grouped("'all'", "'All lines'")
    exhibits = grouped("f.exhibit_type", "f.exhibit_type")
    services = grouped("f.service_branch", "f.service_branch")
    sectors = grouped(f"f.exhibit_type || ':' || ({BA_KEY})",
                      "min(f.exhibit_type) || ' · ' || min(f.budget_activity_title)")
    sectors = [s for s in sectors if s["label"] and not s["label"].endswith("· ")
               and max(abs(s["from"]), abs(s["to"])) >= MIN_SECTOR_K]

    programs = [dict(r) for r in conn.execute(text(f"""
        SELECT p.program_key, min(f.program_title) AS title, min(f.service_branch) AS service,
               min(f.exhibit_type) AS exhibit, sum(f.current_year_amount)::bigint AS cy,
               sum(f.budget_year_amount)::bigint AS by, min(f.source_pdf_link) AS source
        FROM line_item_flat f JOIN program p ON p.id = f.program_id WHERE {cyc}
        GROUP BY p.program_key
    """), params).mappings()]
    for p in programs:
        p.update(_chg(p["cy"], p["by"]))
    movers = sorted(programs, key=lambda p: p["change"])

    # best available figure per fiscal year across releases, as on the search page's trend chart
    by_release = conn.execute(text(f"""
        SELECT f.budget_cycle, f.fiscal_year, sum(f.prior_year_amount)::bigint AS py,
               sum(f.current_year_amount)::bigint AS cy, sum(f.budget_year_amount)::bigint AS by
        FROM line_item_flat f WHERE {where} GROUP BY 1, 2 ORDER BY 2
    """), {k: v for k, v in params.items() if k != "cycle"}).mappings().all()
    best: dict[int, dict] = {}
    for r in by_release:
        for year, value, kind in ((r["fiscal_year"] - 2, r["py"], "actual"), (r["fiscal_year"] - 1, r["cy"], "enacted"),
                                  (r["fiscal_year"], r["by"], "request")):
            if value is not None:
                best[year] = {"fiscal_year": year, "amount_thousands": value, "amount_type": kind, "source_cycle": r["budget_cycle"]}
    series = [best[y] for y in sorted(best)]

    out = {
        "kind": "trends", "cycle": cycle, "fiscal_year": fy, "exhibit": exhibit, "service": service,
        "total": total[0] if total else _chg(0, 0), "exhibits": exhibits, "services": services,
        "growing_sectors": sorted([s for s in sectors if s["change"] > 0], key=lambda s: -s["change"])[:5],
        "shrinking_sectors": sorted([s for s in sectors if s["change"] < 0], key=lambda s: s["change"])[:5],
        "top_increases": [p for p in reversed(movers) if p["change"] > 0 and (p["cy"] or 0) > 0][:TOP],
        "top_decreases": [p for p in movers if p["change"] < 0][:TOP],
        "new_starts": sorted([p for p in programs if not p["cy"] and (p["by"] or 0) > 0], key=lambda p: -p["by"])[:5],
        "series": series,
    }
    out["takeaways"] = _trend_takeaways(out)
    return out


def execution(conn: Connection, fy: int = 2025, exhibit: str | None = None, service: str | None = None) -> dict:
    where, params = _where(exhibit, service)
    params.update(req=f"PB{fy}", enc=f"PB{fy + 1}", act=f"PB{fy + 2}")
    rows = [dict(r) for r in conn.execute(text(f"""
        WITH per AS (
            SELECT p.program_key, f.budget_cycle, min(f.program_title) AS title, min(f.service_branch) AS service,
                   min(f.exhibit_type) AS exhibit,
                   sum(f.prior_year_amount)::bigint AS py, sum(f.current_year_amount)::bigint AS cy,
                   sum(f.budget_year_amount)::bigint AS by
            FROM line_item_flat f JOIN program p ON p.id = f.program_id
            WHERE {where} AND f.budget_cycle IN (:req, :enc, :act)
            GROUP BY p.program_key, f.budget_cycle
        )
        SELECT program_key, min(title) AS title, min(service) AS service, min(exhibit) AS exhibit,
               max(by) FILTER (WHERE budget_cycle = :req) AS requested,
               max(cy) FILTER (WHERE budget_cycle = :enc) AS enacted,
               max(py) FILTER (WHERE budget_cycle = :act) AS actual
        FROM per GROUP BY program_key
    """), params).mappings()]
    enacted_kind = conn.execute(text("""
        SELECT a.amount_type FROM line_item_amount a JOIN budget_line_item li ON li.id = a.line_item_id
        JOIN ingestion_run r ON r.id = li.ingestion_run_id AND r.status = 'published'
        WHERE li.budget_cycle = :enc AND a.funds_fiscal_year = :fy AND a.funding_category = 'total' LIMIT 1
    """), {"enc": f"PB{fy + 1}", "fy": fy}).scalar()
    have = {k: any(r[k] is not None for r in rows) for k in ("requested", "enacted", "actual")}

    def tot(rs, k):
        return sum(r[k] or 0 for r in rs if r[k] is not None)

    complete = [r for r in rows if all(r[k] is not None for k in ("requested", "enacted", "actual") if have[k])]
    by_service = {}
    for r in complete:
        s = by_service.setdefault(r["service"], {"label": r["service"], "requested": 0, "enacted": 0, "actual": 0})
        for k in ("requested", "enacted", "actual"):
            s[k] += r[k] or 0
    for r in complete + list(by_service.values()):
        r["congress"] = _chg(r["requested"], r["enacted"]) if have["requested"] and have["enacted"] else None
        r["execution"] = _chg(r["enacted"], r["actual"]) if have["enacted"] and have["actual"] else None
    out = {
        "kind": "execution", "fiscal_year": fy, "exhibit": exhibit, "service": service,
        "releases": {"requested": f"PB{fy}", "enacted": f"PB{fy + 1}", "actual": f"PB{fy + 2}"},
        "enacted_kind": "cr" if enacted_kind == "cr" else "enacted", "available": have,
        "programs_compared": len(complete),
        "total": {"requested": tot(complete, "requested"), "enacted": tot(complete, "enacted"), "actual": tot(complete, "actual")},
        "services": sorted(by_service.values(), key=lambda s: -(s["enacted"] or s["requested"])),
    }
    t = out["total"]
    out["total"]["congress"] = _chg(t["requested"], t["enacted"]) if have["requested"] and have["enacted"] else None
    out["total"]["execution"] = _chg(t["enacted"], t["actual"]) if have["enacted"] and have["actual"] else None
    key_c = [r for r in complete if r["congress"]]
    key_e = [r for r in complete if r["execution"]]
    out["congress_adds"] = sorted(key_c, key=lambda r: -r["congress"]["change"])[:TOP] if key_c else []
    out["congress_cuts"] = sorted(key_c, key=lambda r: r["congress"]["change"])[:TOP] if key_c else []
    out["over_enacted"] = sorted(key_e, key=lambda r: -r["execution"]["change"])[:TOP] if key_e else []
    out["under_enacted"] = sorted(key_e, key=lambda r: r["execution"]["change"])[:TOP] if key_e else []
    out["takeaways"] = _execution_takeaways(out)
    return out


# ---------------------------------------------------------------- takeaways written by code

def _m(k: int) -> str:
    v = k / 1000
    if abs(v) >= 1000:
        return f"${v / 1000:,.1f} billion"
    return f"${v:,.1f} million"


def _p(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def _trend_takeaways(s: dict) -> list[str]:
    fy, t = s["fiscal_year"], s["total"]
    out = [f"The FY{fy} request totals {_m(t['to'])}, {_p(t['change_pct'])} ({'+' if t['change'] >= 0 else '−'}"
           f"{_m(abs(t['change']))}) against FY{fy - 1}."]
    if s["services"]:
        g = max(s["services"], key=lambda x: x["change"])
        out.append(f"{g['label']} grows the most: {_m(g['from'])} to {_m(g['to'])} ({_p(g['change_pct'])}).")
    if s["growing_sectors"]:
        g = s["growing_sectors"][0]
        out.append(f"Fastest-growing sector by dollars: {g['label']}, up {_m(g['change'])} ({_p(g['change_pct'])}).")
    if s["shrinking_sectors"]:
        d = s["shrinking_sectors"][0]
        out.append(f"Largest sector decline: {d['label']}, down {_m(-d['change'])} ({_p(d['change_pct'])}).")
    if s["top_increases"]:
        p = s["top_increases"][0]
        out.append(f"Largest program increase: {p['title']} ({p['service']}), {_m(p['from'])} to {_m(p['to'])}.")
    if s["new_starts"]:
        n = s["new_starts"][0]
        out.append(f"Largest new start: {n['title']} ({n['service']}) at {_m(n['to'])}.")
    return out


def _execution_takeaways(s: dict) -> list[str]:
    fy, t = s["fiscal_year"], s["total"]
    word = "the continuing-resolution level" if s["enacted_kind"] == "cr" else "enacted"
    out = [f"{s['programs_compared']:,} programs have every figure for FY{fy}."]
    if t["congress"] and s["enacted_kind"] == "cr":
        out.append(f"FY{fy} was under a continuing resolution when {s['releases']['enacted']} was prepared, so there is no "
                   f"enacted figure to compare with the request; the CR-level total is {_m(t['enacted'])}.")
    elif t["congress"]:
        c = t["congress"]
        out.append(f"Congress {'added' if c['change'] >= 0 else 'cut'} {_m(abs(c['change']))} ({_p(c['change_pct'])}) to the "
                   f"{_m(c['from'])} request ({word}: {_m(c['to'])}).")
    if t["execution"]:
        e = t["execution"]
        out.append(f"Actuals came in {'above' if e['change'] >= 0 else 'below'} {word} by {_m(abs(e['change']))} "
                   f"({_p(e['change_pct'])}): {_m(e['to'])}.")
    if s["under_enacted"] and s["under_enacted"][0]["execution"]["change"] < 0:
        u = s["under_enacted"][0]
        out.append(f"Furthest below budget: {u['title']} ({u['service']}), {_m(-u['execution']['change'])} under.")
    if s["over_enacted"] and s["over_enacted"][0]["execution"]["change"] > 0:
        o = s["over_enacted"][0]
        out.append(f"Furthest above budget: {o['title']} ({o['service']}), {_m(o['execution']['change'])} over.")
    if s["enacted_kind"] != "cr" and s["congress_adds"] and s["congress_adds"][0]["congress"]["change"] > 0:
        a = s["congress_adds"][0]
        out.append(f"Largest congressional add: {a['title']} ({a['service']}), +{_m(a['congress']['change'])}.")
    return out


# ---------------------------------------------------------------- optional AI narrative

MONEY_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(billion|million|thousand|B|M|K)?", re.I)
PCT_RE = re.compile(r"([+-−]?\d+(?:\.\d+)?)\s?%")
UNIT = {"billion": 1_000_000, "b": 1_000_000, "million": 1000, "m": 1000, "thousand": 1, "k": 1}


def facts_of(summary: dict) -> tuple[set[int], set[float]]:
    """Every $K amount and percentage (in points) anywhere in the summary."""
    amounts, pcts = set(), set()

    def walk(x, key=""):
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, k)
        elif isinstance(x, list):
            for v in x:
                walk(v, key)
        elif isinstance(x, bool) or x is None:
            return
        elif isinstance(x, float) and key.endswith("pct"):
            pcts.add(round(x * 100, 1))
        elif isinstance(x, int) and key not in ("fiscal_year", "lines", "programs_compared"):
            amounts.add(abs(x))
    walk(summary)
    return amounts, pcts


def verify(textv: str, summary: dict) -> list[str]:
    """Figures in the text that no computed fact supports (to the written precision)."""
    amounts, pcts = facts_of(summary)
    bad = []
    for m in MONEY_RE.finditer(textv):
        whole, frac, unit = m.groups()
        scale = UNIT[unit.lower()] if unit else 0.001
        v = float(whole.replace(",", "") + ("." + frac if frac else "")) * scale
        tol = (scale / 10 ** len(frac) if frac else scale) / 2 + 0.5
        if not any(abs(a - v) <= tol for a in amounts):
            bad.append(m.group(0))
    for m in PCT_RE.finditer(textv):
        v = abs(float(m.group(1).replace("−", "-")))
        if not any(abs(abs(p) - v) <= 0.051 + (0.5 if "." not in m.group(1) else 0) for p in pcts):
            bad.append(m.group(0))
    return bad


def ai_narrative(summary: dict, client=None) -> dict:
    """{'text', 'model', 'verified', 'unverified'} or {'withheld': reason}."""
    if client is None and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return {"withheld": "AI narrative is off on this server (no Anthropic API key)."}
    import anthropic

    client = client or anthropic.Anthropic(max_retries=2, timeout=90)
    facts = {k: v for k, v in summary.items() if k not in ("takeaways",)}
    try:
        response = client.beta.messages.create(
            model=os.environ.get("QA_MODEL", "claude-opus-5"),
            max_tokens=4000,
            thinking={"type": "adaptive"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=("You write a short executive summary of U.S. defense budget data for a business development team. "
                    "Use only the facts given (amounts in $ thousands; write them as $ millions or billions, rounded to one "
                    "decimal). Every dollar figure and percentage you write must come from the facts. Say where funding is "
                    "growing or shrinking and which programs stand out. 90 to 130 words, plain prose, no headings."),
            messages=[{"role": "user", "content": json.dumps(facts, default=str)}],
        )
    except anthropic.APIError as e:
        log.warning("summary narrative failed: %s", e)
        return {"withheld": "The AI narrative couldn't be generated right now."}
    if response.stop_reason == "refusal":
        return {"withheld": "The model declined to write this narrative."}
    textv = "".join(b.text for b in response.content if b.type == "text").strip()
    bad = verify(textv, summary)
    if bad:
        return {"withheld": f"An AI draft was withheld: {len(bad)} figure(s) didn't match the data ({', '.join(bad[:4])}).",
                "unverified": bad}
    figures = len(MONEY_RE.findall(textv)) + len(PCT_RE.findall(textv))
    return {"text": textv, "model": getattr(response, "model", None), "verified": figures}
