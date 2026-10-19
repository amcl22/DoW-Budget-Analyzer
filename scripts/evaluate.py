"""Capstone II evaluation: tests the Capstone I success criteria against the source documents.

    DATABASE_URL=... python scripts/evaluate.py      # writes docs/eval/results.json and results.md

Every check reads the stored source PDFs directly (pymupdf text), a different code path from
the ingestion parser (word positions), so a parser bug doesn't grade its own homework. The
samples are seeded, so a rerun gives the same lines.

  E1 Line accuracy (S1, S2): stratified random sample of line items; is the identifier on the
     linked page, and are its amounts printed there?
  E2 Totals (S2): database totals per release vs. the totals transcribed from each exhibit.
  E3 Explanation fidelity: sampled 'Why did funding change?' quotes; does the text appear on
     the cited page, inside the right program's section?
  E4 Outliers (S2): every PB2027 line with a 500%+ change or a $100M+ new start, checked on page.
  E5 Search (S4 proxy): realistic analyst queries; is the intended program in the top results?
  E6 Coverage (S3): lines and justification detail by release and service.
"""

from __future__ import annotations

import json
import random
import re
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import pymupdf
import yaml
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.config import database_url  # noqa: E402

SEED = 20261019
PER_STRATUM = 25            # lines per release x exhibit
EXPLANATION_SAMPLE = 100
OUT = ROOT / "docs" / "eval"

# Realistic analyst searches (as typed), with what a correct result looks like. Some use words
# that appear only in descriptions or not at all, to find where keyword search falls short.
SEARCHES = [
    ("conventional prompt strike", r"prompt strike"),
    ("counterspace", r"counterspace"),
    ("F-35", r"F-35"),
    ("B-21", r"B-21"),
    ("Columbia submarine", r"COLUMBIA Class"),
    ("collaborative combat aircraft", r"Collaborative Combat Aircraft"),
    ("golden dome", r"Golden Dome"),
    ("hypersonic attack cruise missile", r"Hypersonic Attack Cruise"),
    ("AMRAAM", r"AMRAAM"),
    ("DDG-51 destroyer", r"DDG-51"),
    ("0605518N", r"PROMPT STRIKE"),
    ("narrowband satellite communications", r"Narrowband Satellite"),
    ("javelin missile", r"Javelin"),
    ("aegis missile defense", r"Aegis"),
    ("artificial intelligence army", r"Artificial Intelligence"),
    ("E-7 wedgetail", r"^E-7$"),
    ("next generation fighter", r"F-47|Next Generation (Air Dominance|Fighter)"),
]

_pages: dict[tuple[str, int], str] = {}
_docs: dict[str, pymupdf.Document] = {}


def page_text(path: str, page: int) -> str:
    key = (path, page)
    if key not in _pages:
        if path not in _docs:
            _docs[path] = pymupdf.open(path)
        doc = _docs[path]
        _pages[key] = doc[page - 1].get_text() if 1 <= page <= doc.page_count else ""
    return _pages[key]


def squash(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", s.lower())


def fmt(v: int) -> str:
    return f"{v:,}"


NUM_RE = re.compile(r"\(?-?\d{1,3}(?:,\d{3})+|\(?-?\d{1,3}\)?")


def derived(v: int, text_: str) -> bool:
    """P-1 lines carrying advance procurement print a gross amount and its adjustments; the
    line's amount is their difference (or sum) and isn't printed by itself."""
    nums = {int(re.sub(r"[^0-9]", "", n)) for n in NUM_RE.findall(text_) if re.sub(r"[^0-9]", "", n)}
    nums = {n for n in nums if n >= 1000}
    return any((a - v) in nums or (v - a) in nums for a in nums)


def title_found(title: str, text_: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-z0-9]+", title) if len(w) > 1][:3]
    t = squash(text_)
    return all(squash(w) in t for w in words)


def e1_lines(conn) -> dict:
    rows = conn.execute(text("""
        SELECT f.id, f.budget_cycle, f.exhibit_type, f.service_branch, f.program_element, f.line_item_number,
               f.program_title, f.prior_year_amount, f.current_year_amount, f.budget_year_amount
        FROM line_item_flat f ORDER BY f.id
    """)).mappings().all()
    strata = defaultdict(list)
    for r in rows:
        strata[(r["budget_cycle"], r["exhibit_type"])].append(r)
    rng = random.Random(SEED)
    sample = [r for k in sorted(strata) for r in rng.sample(strata[k], min(PER_STRATUM, len(strata[k])))]
    refs = defaultdict(list)
    for r in conn.execute(text("""
        SELECT s.line_item_id, s.page_number, d.storage_path FROM line_item_source_ref s
        JOIN source_document d ON d.id = s.source_document_id
        WHERE s.ref_kind IN ('r1_summary', 'p1_summary') AND s.line_item_id = ANY(:ids)
    """), {"ids": [r["id"] for r in sample]}).mappings():
        refs[r["line_item_id"]].append(r)
    results, failures = defaultdict(lambda: defaultdict(int)), []
    for r in sample:
        pages = " ".join(page_text(x["storage_path"], x["page_number"]) for x in refs[r["id"]])
        ident = r["program_element"] or ""
        id_ok = (ident and ident in pages) or title_found(r["program_title"], pages)
        checks = {"identifier": bool(id_ok)}
        for k in ("prior_year_amount", "current_year_amount", "budget_year_amount"):
            v = r[k]
            if v:            # zero / blank print as '-' or nothing
                printed = fmt(abs(v)) in pages
                checks[k] = printed or (r["exhibit_type"] == "P-1" and derived(abs(v), pages))
                results[f"{r['budget_cycle']} {r['exhibit_type']}"]["derived_amounts"] += (checks[k] and not printed)
        ok = all(checks.values())
        s = results[f"{r['budget_cycle']} {r['exhibit_type']}"]
        s["lines"] += 1
        s["pass"] += ok
        for k, v in checks.items():
            s[f"{k}_checked"] += 1
            s[f"{k}_ok"] += v
        if not ok:
            failures.append({"id": r["id"], "release": r["budget_cycle"], "exhibit": r["exhibit_type"],
                             "number": r["program_element"] or r["line_item_number"], "title": r["program_title"],
                             "failed": [k for k, v in checks.items() if not v],
                             "pages": [x["page_number"] for x in refs[r["id"]]]})
    total = sum(s["lines"] for s in results.values())
    passed = sum(s["pass"] for s in results.values())
    amounts_checked = sum(v for s in results.values() for k, v in s.items() if k.endswith("_amount_checked"))
    amounts_ok = sum(v for s in results.values() for k, v in s.items() if k.endswith("_amount_ok"))
    return {"sample": total, "pass": passed, "pass_rate": passed / total,
            "amounts_checked": amounts_checked, "amounts_ok": amounts_ok,
            "amounts_derived": sum(s["derived_amounts"] for s in results.values()),
            "by_stratum": {k: dict(v) for k, v in sorted(results.items())}, "failures": failures}


def _total_key(amounts: dict, fy: int) -> str | None:
    for pattern in (rf"^FY {fy} Total", rf"^FY {fy} Request"):
        for k in amounts:
            if re.match(pattern, k):
                return k
    return None


def e2_totals(conn) -> dict:
    out = []
    for path in sorted((ROOT / "config" / "expected_totals").glob("PB*_*.yaml")):
        cycle, exhibit = path.stem.split("_")
        fy = int(cycle[2:])
        spec = yaml.safe_load(path.read_text())
        for g in spec["groups"]:
            key = _total_key(g["amounts"], fy)
            if key is None:
                continue
            db = conn.execute(text("""
                SELECT coalesce(sum(budget_year_amount), 0)::bigint FROM line_item_flat
                WHERE budget_cycle = :c AND exhibit_type = :e AND include_in_toa = :t
            """), {"c": cycle, "e": exhibit, "t": g["include_in_toa"]}).scalar()
            out.append({"release": cycle, "exhibit": exhibit, "group": g["label"], "column": key,
                        "published": g["amounts"][key], "database": db, "match": db == g["amounts"][key]})
    return {"checks": len(out), "match": sum(o["match"] for o in out), "rows": out}


def e3_explanations(conn) -> dict:
    rows = conn.execute(text("""
        SELECT x.id, x.kind, x.body, x.page_number, d.storage_path, li.program_element, li.budget_cycle
        FROM line_item_explanation x JOIN budget_line_item li ON li.id = x.line_item_id
        JOIN source_document d ON d.id = x.source_document_id ORDER BY x.id
    """)).mappings().all()
    sample = random.Random(SEED).sample(list(rows), min(EXPLANATION_SAMPLE, len(rows)))
    text_ok = pe_ok = 0
    failures, prefixed = [], []
    for r in sample:
        here = page_text(r["storage_path"], r["page_number"])
        span = squash(here + page_text(r["storage_path"], r["page_number"] + 1))
        body = squash(r["body"])
        t = body[:80] in span
        after_prefix = not t and len(body) > 120 and body[40:120] in span
        t = t or after_prefix
        p = bool(r["program_element"]) and r["program_element"] in here
        text_ok += t
        pe_ok += p
        if not (t and p):
            failures.append({"id": r["id"], "release": r["budget_cycle"], "pe": r["program_element"],
                             "page": r["page_number"], "text_found": t, "pe_on_page": p, "start": r["body"][:90]})
        if after_prefix:
            prefixed.append({"id": r["id"], "start": r["body"][:90]})
    return {"population": len(rows), "sample": len(sample), "text_on_cited_page": text_ok,
            "right_program_section": pe_ok, "found_after_extra_leading_words": len(prefixed),
            "prefixed": prefixed, "failures": failures}


def e4_outliers(conn) -> dict:
    rows = conn.execute(text("""
        SELECT f.id, f.program_title, coalesce(f.program_element, f.line_item_number) AS number, f.exhibit_type,
               f.current_year_amount AS cy, f.budget_year_amount AS by
        FROM line_item_flat f WHERE f.budget_cycle = 'PB2027' AND (
          (coalesce(f.current_year_amount, 0) > 0 AND f.budget_year_amount >= 6 * f.current_year_amount)
          OR (coalesce(f.current_year_amount, 0) = 0 AND f.budget_year_amount >= 100000))
        ORDER BY f.budget_year_amount DESC
    """)).mappings().all()
    refs = defaultdict(list)
    for r in conn.execute(text("""
        SELECT s.line_item_id, s.page_number, d.storage_path FROM line_item_source_ref s
        JOIN source_document d ON d.id = s.source_document_id
        WHERE s.ref_kind IN ('r1_summary', 'p1_summary') AND s.line_item_id = ANY(:ids)
    """), {"ids": [r["id"] for r in rows]}).mappings():
        refs[r["line_item_id"]].append(r)
    checked = []
    for r in rows:
        pages = " ".join(page_text(x["storage_path"], x["page_number"]) for x in refs[r["id"]])
        checked.append({"number": r["number"], "title": r["program_title"], "exhibit": r["exhibit_type"],
                        "current": r["cy"], "budget": r["by"], "confirmed_on_page": fmt(r["by"]) in pages})
    return {"lines": len(checked), "confirmed": sum(c["confirmed_on_page"] for c in checked), "rows": checked}


def e5_search() -> dict:
    import os

    os.environ["ALLOW_OPEN_ACCESS"] = "1"
    from fastapi.testclient import TestClient

    from api.main import app

    client = TestClient(app)
    out = []
    for q, expect in SEARCHES:
        t0 = time.perf_counter()
        resp = client.get("/api/search", params={"q": q, "cycle": "PB2027", "page_size": 10})
        ms = (time.perf_counter() - t0) * 1000
        titles = [r["program_title"] for r in resp.json()["results"]]
        rank = next((i + 1 for i, t in enumerate(titles) if re.search(expect, t, re.I)), None)
        out.append({"query": q, "expected": expect, "rank": rank, "top_result": titles[0] if titles else None,
                    "ms": round(ms)})
    lat = [o["ms"] for o in out]
    return {"queries": len(out), "top1": sum(o["rank"] == 1 for o in out),
            "top5": sum(o["rank"] is not None and o["rank"] <= 5 for o in out),
            "median_ms": statistics.median(lat), "max_ms": max(lat), "rows": out}


def e6_coverage(conn) -> dict:
    return {"rows": [dict(r) for r in conn.execute(text("""
        SELECT li.budget_cycle, li.exhibit_type, li.service_branch, count(DISTINCT li.id) AS lines,
               count(DISTINCT li.id) FILTER (WHERE s.ref_kind = 'r2_justification') AS with_justification,
               count(DISTINCT x.line_item_id) AS with_explanation
        FROM budget_line_item li JOIN ingestion_run r ON r.id = li.ingestion_run_id AND r.status = 'published'
        LEFT JOIN line_item_source_ref s ON s.line_item_id = li.id
        LEFT JOIN line_item_explanation x ON x.line_item_id = li.id
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
    """)).mappings()]}


def main() -> None:
    engine = create_engine(database_url())
    with engine.connect() as conn:
        results = {"seed": SEED, "e1_lines": e1_lines(conn), "e2_totals": e2_totals(conn),
                   "e3_explanations": e3_explanations(conn), "e4_outliers": e4_outliers(conn),
                   "e6_coverage": e6_coverage(conn)}
    results["e5_search"] = e5_search()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str))
    e1, e2, e3, e4, e5 = (results[k] for k in ("e1_lines", "e2_totals", "e3_explanations", "e4_outliers", "e5_search"))
    lines = [
        "# Evaluation results", "",
        f"Seed {SEED}. Full detail in results.json.", "",
        f"- E1 line accuracy: {e1['pass']}/{e1['sample']} sampled lines pass "
        f"({e1['pass_rate']:.1%}); {e1['amounts_ok']}/{e1['amounts_checked']} amounts found on the linked page "
        f"({e1['amounts_derived']} as gross less advance procurement)",
        f"- E2 totals: {e2['match']}/{e2['checks']} published totals match the database",
        f"- E3 explanations: {e3['text_on_cited_page']}/{e3['sample']} quotes found on the cited page "
        f"({e3['found_after_extra_leading_words']} with extra leading words); "
        f"{e3['right_program_section']}/{e3['sample']} on a page of the right program",
        f"- E4 outliers: {e4['confirmed']}/{e4['lines']} PB2027 outliers confirmed on the source page",
        f"- E5 search: {e5['top1']}/{e5['queries']} top-1, {e5['top5']}/{e5['queries']} top-5; "
        f"median {e5['median_ms']:.0f} ms",
        "", "## E1 failures", *[f"- {f}" for f in e1["failures"]],
        "", "## E3 failures", *[f"- {f}" for f in e3["failures"]],
        "", "## E5 searches", *[f"- `{r['query']}` → rank {r['rank']} (top: {r['top_result']}, {r['ms']} ms)" for r in e5["rows"]],
    ]
    (OUT / "results.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:10]))


if __name__ == "__main__":
    main()
