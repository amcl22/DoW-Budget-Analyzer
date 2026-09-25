"""Parse the R-1 summary PDF into positioned table rows.

R-1 pages carry a header ('UNCLASSIFIED', a section name such as 'Department of the Army' or
'Defense Health Agency', and on detail pages 'Appropriation: <code> <title>'), then a table:
  - line rows: line number, PE, title, budget activity ('Item Act'), 'U', amounts
  - budget activity subtotals: label in the PE column, no line number
  - appropriation/agency totals and summary-page rows: label in the line-number column
Column x-positions differ between years, so each page's layout is read from its own header
('Line', 'Program', 'Act', and the 'FY ...' amount columns).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .pdf_common import (
    COLUMN_TOLERANCE,
    Column,
    PdfFormatError,
    Word,
    amount_columns,
    assign_amounts,
    assign_section_ids,
    group_lines,
    header_band,
    is_number,
    map_columns,
    page_header_meta,
    page_words,
    text_of,
)

WRAP_MAX_GAP = 12.0   # a label line this close below a row continues that row's label
PE_LIKE_RE = re.compile(r"^\d{6,}[A-Z0-9]*$")

__all__ = ["PdfFormatError", "PdfPage", "PdfRow", "assign_section_ids", "parse_r1_pdf", "parse_r1_page"]


@dataclass
class PdfRow:
    page_number: int
    kind: str                      # 'line' | 'subtotal' | 'total' | 'summary' | 'unlabeled'
    label: str                     # program title for line rows
    y: float
    amounts: dict[str, int]        # Excel header -> $K (absent = blank)
    line_number: str | None = None
    pe: str | None = None
    budget_activity: str | None = None


@dataclass
class PdfPage:
    page_number: int               # physical, 1-based
    section: str | None
    account: str | None            # appropriation code on detail pages
    account_title: str | None
    printed_label: str | None      # 'Page 5'
    rows: list[PdfRow] = field(default_factory=list)
    section_id: str | None = None  # see assign_section_ids


@dataclass(frozen=True)
class _Layout:
    amount_x0: float
    first_amount_x1: float
    line_no_max_x0: float | None   # None on summary pages (no line-number column)
    pe_max_x0: float | None
    title_min_x0: float | None
    ba_range: tuple[float, float] | None


def _layout(band: list[Word], cols: list[Column]) -> _Layout:
    def first(text):
        return min((w for w in band if w.text == text), key=lambda w: w.x0, default=None)

    line_h, prog_h, act_h = first("Line"), first("Program"), first("Act")
    amount_x0 = min(c.x0 for c in cols) - 3
    first_x1 = min(c.x1 for c in cols)
    if not (line_h and prog_h):
        return _Layout(amount_x0, first_x1, None, None, None, None)
    return _Layout(
        amount_x0=amount_x0,
        first_amount_x1=first_x1,
        line_no_max_x0=line_h.x1 - 5,
        pe_max_x0=prog_h.x1,
        title_min_x0=prog_h.x1 + 3,
        ba_range=(act_h.x0 - 12, act_h.x1 + 12) if act_h else None,
    )


def parse_r1_page(page_number: int, words: list[Word], page_height: float, pdf_columns: dict[str, str]) -> PdfPage:
    lines = group_lines(words)
    meta = page_header_meta(page_number, lines, page_height)
    account = account_title = None
    for line in lines:
        if line[0].text == "Appropriation:" and len(line) > 1:
            account, account_title = line[1].text, text_of(line[2:]) or None
            break
    page = PdfPage(page_number, meta.section, account, account_title, meta.printed_label)

    hb = header_band(words)
    if hb is None:
        return page
    band, top = hb
    cols = amount_columns(band, top)
    if not cols:
        return page
    headers = map_columns(cols, pdf_columns, page_number)
    lay = _layout(band, cols)
    header_bottom = max(w.y0 for w in band)
    footer_y = page_height * 0.9

    last: PdfRow | None = None
    pending: tuple[list[Word], float] | None = None   # a label line waiting for its amounts

    def label_kind(first_word: Word) -> str:
        if lay.line_no_max_x0 is None:
            return "summary" if account is None else "total"
        return "total" if first_word.x0 < lay.line_no_max_x0 else "subtotal"

    for line in lines:
        y = line[0].y0
        if y <= header_bottom + 1 or y >= footer_y:
            continue
        line = [w for w in line if w.text != "UNCLASSIFIED"]
        if not line:
            continue
        # amounts are right-aligned, so classify them by right edge: a wide number can start
        # left of its column header
        nums = [w for w in line if is_number(w.text) and w.x1 >= lay.first_amount_x1 - COLUMN_TOLERANCE]
        left = [w for w in line if w.x0 < lay.amount_x0 and w not in nums]

        starts_with_number = (
            lay.line_no_max_x0 is not None
            and len(left) >= 2
            and left[0].x0 < lay.line_no_max_x0
            and left[0].text.isdigit()
        )
        is_line_row = starts_with_number and left[1].x0 < lay.pe_max_x0 and PE_LIKE_RE.match(left[1].text)
        if starts_with_number and not is_line_row:
            # a stray line-number fragment on a subtotal row (FY2024 p10 '28 Applied Research')
            left = left[1:]
        if is_line_row:
            ba = None
            if lay.ba_range:
                ba = next(
                    (w.text for w in left if lay.ba_range[0] <= w.x0 <= lay.ba_range[1] and w.text.isdigit()),
                    None,
                )
            title_max = lay.ba_range[0] if lay.ba_range else lay.amount_x0
            title = [w for w in left[2:] if lay.title_min_x0 <= w.x0 < title_max]
            last = PdfRow(page_number, "line", text_of(title), y, assign_amounts(nums, cols, headers, page_number),
                          line_number=left[0].text, pe=left[1].text, budget_activity=ba)
            page.rows.append(last)
            pending = None
        elif left and nums:
            last = PdfRow(page_number, label_kind(left[0]), text_of(left), y,
                          assign_amounts(nums, cols, headers, page_number))
            page.rows.append(last)
            pending = None
        elif nums and pending and y - pending[1] <= WRAP_MAX_GAP:
            # label wrapped over several lines, amounts on the last one
            last = PdfRow(page_number, label_kind(pending[0][0]), text_of(pending[0]), y,
                          assign_amounts(nums, cols, headers, page_number))
            page.rows.append(last)
            pending = None
        elif left and not nums:
            if (last and last.kind == "line" and lay.title_min_x0 is not None
                    and left[0].x0 >= lay.title_min_x0 and y - last.y <= 2 * WRAP_MAX_GAP):
                last.label = f"{last.label} {text_of(left)}"
            elif pending and y - pending[1] <= WRAP_MAX_GAP and abs(left[0].x0 - pending[0][0].x0) < 2:
                pending = (pending[0] + left, y)
            else:
                pending = (left, y)
        elif nums:
            # amounts whose label is missing from the text layer (seen on a FY2024 FYDP recap
            # page); kept so validation can report them
            last = PdfRow(page_number, "unlabeled", "", y, assign_amounts(nums, cols, headers, page_number))
            page.rows.append(last)
            pending = None
    return page


def parse_r1_pdf(path: Path, pdf_columns: dict[str, str]) -> list[PdfPage]:
    with pymupdf.open(path) as doc:
        pages = [
            parse_r1_page(i + 1, page_words(doc[i]), doc[i].rect.height, pdf_columns)
            for i in range(doc.page_count)
        ]
    assign_section_ids(pages)
    _attach_subtotal_budget_activities(pages)
    return pages


def _attach_subtotal_budget_activities(pages: list[PdfPage]) -> None:
    """A BA subtotal closes the run of line rows above it, possibly spanning pages. Tie it to
    that BA by position: subtotal labels are not reliably the Excel BA titles ('&' vs 'and',
    capitalization, the occasional typo such as 'Portfolion')."""
    last_ba: dict[str | None, str | None] = {}
    for page in pages:
        for row in page.rows:
            if row.kind == "line":
                last_ba[page.section_id] = row.budget_activity
            elif row.kind == "subtotal":
                row.budget_activity = last_ba.get(page.section_id)
