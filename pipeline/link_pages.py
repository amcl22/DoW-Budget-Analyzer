"""Match Excel line items to the PDF pages they appear on, and verify every amount."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace

from .config import ExhibitColumns
from .normalize import CLASSIFIED_PE, LineItemRecord, normalize_pe
from .pdf_r1 import PdfPage, PdfRow

PDF_CLASSIFIED_PE = "999999999"  # the PDF prints the classified sentinel with 9 digits
MATCH_METHOD = "acct+ba+line+pe"


@dataclass(frozen=True)
class PageRef:
    page_number: int
    printed_label: str | None
    section: str | None
    section_id: str | None
    amount_verified: bool
    mismatches: tuple[str, ...]
    is_primary: bool = False


@dataclass
class LinkResult:
    refs: dict[tuple[str, str, str], list[PageRef]] = field(default_factory=dict)
    pe_mismatches: list[str] = field(default_factory=list)   # key matched, PE did not
    unmatched_pdf_rows: list[str] = field(default_factory=list)


def _pdf_pe(row: PdfRow) -> str:
    pe = normalize_pe(row.pe or "")
    return CLASSIFIED_PE if pe == PDF_CLASSIFIED_PE else pe


def compare_amounts(record: LineItemRecord, row: PdfRow, headers: list[str]) -> list[str]:
    """Differences between the Excel record and the PDF row, over the PDF's columns."""
    out = []
    for h in headers:
        excel = record.amount(h) or 0
        pdf = row.amounts.get(h, 0)
        if excel != pdf:
            out.append(f"{h}: excel {excel:,} vs pdf {pdf:,}")
    return out


def link_line_items(
    records: list[LineItemRecord], pages: list[PdfPage], cols: ExhibitColumns
) -> LinkResult:
    headers = list(cols.pdf_columns.values())
    index: dict[tuple[str, str, str], list[tuple[PdfPage, PdfRow]]] = defaultdict(list)
    section_size: Counter = Counter()
    for page in pages:
        if page.account is None:
            continue
        for row in page.rows:
            if row.kind == "line":
                index[(page.account, row.budget_activity, row.line_number)].append((page, row))
                section_size[(page.account, page.section_id)] += 1

    result = LinkResult()
    used: set[tuple[int, float]] = set()
    for rec in records:
        refs = []
        for page, row in index.get(rec.key, []):
            if _pdf_pe(row) != rec.program_element:
                result.pe_mismatches.append(
                    f"{rec.key}: excel PE {rec.program_element} vs pdf PE {row.pe} on page {page.page_number}"
                )
                continue
            used.add((page.page_number, row.y))
            mismatches = tuple(compare_amounts(rec, row, headers))
            refs.append(
                PageRef(page.page_number, page.printed_label, page.section, page.section_id,
                        not mismatches, mismatches)
            )
        if refs:
            # Prefer the most specific section (e.g. the DARPA detail over the combined
            # Defense-Wide detail): the one with the fewest lines for this account.
            best = min(
                range(len(refs)),
                key=lambda i: (section_size[(rec.appropriation_account, refs[i].section_id)], refs[i].page_number),
            )
            refs[best] = replace(refs[best], is_primary=True)
        result.refs[rec.key] = refs

    for page in pages:
        for row in page.rows:
            if row.kind == "line" and page.account and (page.page_number, row.y) not in used:
                result.unmatched_pdf_rows.append(
                    f"page {page.page_number}: {page.account} BA {row.budget_activity} line {row.line_number} {row.pe}"
                )
    return result
