"""R-2 (RDT&E Budget Item Justification) books: find each program element's section, read its
cost table and mission description, and attach them to the R-1 lines.

Every R-2 section starts on a page whose header reads
    PE 0601000BR: DTRA BASIC RESEARCH ... Page 1 of 5 ... R-1 Line #1 ...
    Exhibit R-2, RDT&E Budget Item Justification: PB 2027 ...
    Appropriation/Budget Activity  0400: Research, ... Defense-Wide / BA 1: Basic Research
followed by 'COST ($ in Millions)' (Prior Years, FY-2, FY-1, budget-year Base/OOC/Total, four
out-years, Cost To Complete, Total Cost) and 'A. Mission Description and Budget Item
Justification'. The same layout is used by every service and every year FY2024-FY2027, so
sections are found by scanning pages, which also works for books without bookmarks.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .link_pages import PageRef
from .normalize import Amount, LineItemRecord, normalize_pe

SECTION_START_RE = re.compile(r"Exhibit R-2, RDT&E Budget Item Justification")
FIRST_PAGE_RE = re.compile(r"\bPage 1 of \d+\b")
PE_HEADER_RE = re.compile(r"^PE (\S+?):\s*(.*)$", re.M)
LINE_RE = re.compile(r"R-1 Line #\s*(\w+)")
APPROP_RE = re.compile(r"Appropriation/Budget Activity\s+(\d{4}):.*?/\s*BA\s*(\d+)", re.S)
COLUMN_RE = re.compile(
    r"Prior Years|Cost To Complete|Total Cost|"
    r"FY \d{4}(?: (?:Base|OOC|OCO|Total|Mandatory|Discretionary|Request|Reconciliation|Supplemental))*"
)
MILLIONS_RE = re.compile(r"^-?\d{1,3}(,\d{3})*\.\d{3}$")    # '1,502.638'
VALUE_RE = re.compile(r"^(-?\d{1,3}(,\d{3})*\.\d{3}|-|Continuing|TBD)$")
FOOTER_RE = re.compile(r"^(UNCLASSIFIED|Volume .{0,60}- ?\d+|PE \S+: .*|Page \d+ of \d+|.*Page \d+ of \d+)$")
DESCRIPTION_START = "A. Mission Description and Budget Item Justification"
DESCRIPTION_END = "B. Program Change Summary"
MAX_DESCRIPTION_PAGES = 6


@dataclass
class R2Section:
    book_url: str
    start_page: int                    # physical, 1-based
    pe: str
    title: str
    line_number: str
    account_prefix: str                # '0400'
    budget_activity: str               # '01'
    cost: dict[str, int | None]        # column label -> $K ('Continuing' / '-' -> None)
    description: str | None
    printed_label: str | None = None


@dataclass
class R2Result:
    refs: dict[tuple, list[PageRef]] = field(default_factory=lambda: defaultdict(list))
    descriptions: dict[tuple, str] = field(default_factory=dict)
    outyears: dict[tuple, list[Amount]] = field(default_factory=dict)
    amount_mismatches: list[str] = field(default_factory=list)
    unmatched_sections: list[str] = field(default_factory=list)
    sections: int = 0


def _millions_to_thousands(token: str) -> int | None:
    """'1,502.638' ($M, three decimals) -> 1502638 ($K), exactly."""
    if not MILLIONS_RE.match(token):
        return None
    whole, frac = token.lstrip("-").replace(",", "").split(".")
    value = int(whole) * 1000 + int(frac)
    return -value if token.startswith("-") else value


def _cost_table(text: str) -> dict[str, int | None]:
    i = text.find("COST ($ in Millions)")
    j = text.find("Total Program Element", i)
    if i < 0 or j < 0:
        return {}
    labels = COLUMN_RE.findall(" ".join(text[i + len("COST ($ in Millions)"):j].split()))
    values = []
    for line in text[j + len("Total Program Element"):].splitlines():
        token = line.strip()
        if not token:
            continue
        if not VALUE_RE.match(token):
            break
        values.append(token)
        if len(values) == len(labels):
            break
    if len(values) != len(labels):
        return {}
    return {label: _millions_to_thousands(v) for label, v in zip(labels, values, strict=True)}


def _page_body(text: str, pe: str) -> str:
    """Page text after the repeated header block ('R-1 Program Element (Number/Name) PE x / ...')."""
    m = re.search(rf"R-1 Program Element \(Number/Name\)\s*\n\s*PE {re.escape(pe)}\s*/[^\n]*\n", text)
    body = text[m.end():] if m else text
    return "\n".join(l for l in body.splitlines() if not FOOTER_RE.match(l.strip()))


def _description(doc: pymupdf.Document, start: int, pe: str) -> str | None:
    parts = []
    for p in range(start, min(start + MAX_DESCRIPTION_PAGES, doc.page_count)):
        text = doc[p].get_text()
        if p > start and FIRST_PAGE_RE.search(text) and SECTION_START_RE.search(text):
            break   # next section
        parts.append(_page_body(text, pe))
        if DESCRIPTION_END in text:
            break
    joined = "\n".join(parts)
    i = joined.find(DESCRIPTION_START)
    if i < 0:
        return None
    j = joined.find(DESCRIPTION_END, i)
    body = joined[i + len(DESCRIPTION_START): j if j > 0 else None]
    return " ".join(body.split()) or None


def parse_r2_book(path: Path, url: str) -> list[R2Section]:
    sections = []
    with pymupdf.open(path) as doc:
        for p in range(doc.page_count):
            text = doc[p].get_text()
            if not (SECTION_START_RE.search(text) and FIRST_PAGE_RE.search(text)):
                continue
            pe_m, line_m, ap_m = PE_HEADER_RE.search(text), LINE_RE.search(text), APPROP_RE.search(text)
            if not (pe_m and line_m and ap_m):
                continue
            footer = next((l.strip() for l in reversed(text.splitlines()) if re.match(r"^Volume .*- ?\d+$", l.strip())), None)
            sections.append(R2Section(
                book_url=url,
                start_page=p + 1,
                pe=normalize_pe(pe_m.group(1)),
                title=" ".join(pe_m.group(2).split()),
                line_number=line_m.group(1),
                account_prefix=ap_m.group(1),
                budget_activity=ap_m.group(2).zfill(2),
                cost=_cost_table(text),
                description=_description(doc, p, pe_m.group(1)),
                printed_label=footer,
            ))
    return sections


def _fy_of(label: str) -> tuple[int, str] | None:
    m = re.match(r"^FY (\d{4})(?: (.*))?$", label)
    return (int(m.group(1)), m.group(2) or "") if m else None


def attach_r2(records: list[LineItemRecord], sections: list[R2Section], fiscal_year: int,
              book_services: dict[str, str]) -> R2Result:
    """Match sections to R-1 lines by (account, BA, line) and PE; compare the R-2 totals for the
    fiscal years the R-1 also reports; collect descriptions and out-year estimates."""
    by_key: dict[tuple, LineItemRecord] = {}
    for r in records:
        by_key[(r.appropriation_account[:4], r.budget_activity, r.line_number)] = r
    result = R2Result(sections=len(sections))
    for s in sections:
        rec = by_key.get((s.account_prefix, s.budget_activity, s.line_number))
        where = f"{s.book_url.rsplit('/', 1)[-1]} p{s.start_page} PE {s.pe} line {s.line_number}"
        if rec is None or rec.program_element != s.pe:
            result.unmatched_sections.append(
                f"{where}: no R-1 line {s.account_prefix} BA {s.budget_activity} #{s.line_number}"
                + (f" (R-1 has PE {rec.program_element})" if rec else "")
            )
            continue
        mismatches = []
        outyears = []
        for label, value in s.cost.items():
            fy = _fy_of(label)
            if fy is None or value is None:
                continue
            year, qualifier = fy
            if year > fiscal_year and not qualifier:
                outyears.append(Amount(year, "estimate", "total", value, f"R-2 {label}"))
            elif year <= fiscal_year and qualifier in ("", "Total"):
                if year == fiscal_year and not qualifier and f"FY {year} Total" in s.cost:
                    continue
                # the R-2 year columns are the discretionary appropriation where the R-1 also
                # has mandatory / spend-plan money (FY2026 PL 119-21), else the total
                r1 = {c: sum(a.amount_thousands for a in rec.amounts
                             if a.funds_fiscal_year == year and a.funding_category == c)
                      for c in ("total", "discretionary")}
                if value not in r1.values():
                    mismatches.append(f"{label}: R-2 {value:,} vs R-1 total {r1['total']:,}")
        if mismatches:
            result.amount_mismatches.append(f"{where}: " + "; ".join(mismatches))
        result.refs[rec.key].append(PageRef(
            s.start_page, s.printed_label, book_services.get(s.book_url), None,
            not mismatches, tuple(mismatches), is_primary=not result.refs[rec.key],
            document_url=s.book_url,
        ))
        if s.description and rec.key not in result.descriptions:
            result.descriptions[rec.key] = s.description
        if outyears and rec.key not in result.outyears:
            result.outyears[rec.key] = outyears
    return result
