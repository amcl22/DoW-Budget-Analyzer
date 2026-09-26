"""Parse the P-1 summary PDF.

Differences from the R-1 that shape this parser (checked against FY2024-FY2027):
  - FY2027 pages are stored rotated; words are read in displayed coordinates.
  - Detail pages label amount columns only as 'Qty' / 'Cost' under stacked FY headers, and a
    table too wide for one page continues on the next page with the same rows (FY2027: page A
    has FY2025-FY2026 plus the Qty of FY2027 Discretionary; page B starts with its Cost). So
    detail columns are matched by position to the ordered pdf_columns list: every 'Cost' is one
    column, and a 'Qty' belongs to the next 'Cost', possibly on the continuation page.
  - Some columns have no Qty (FY2024 'Less Supplementals' / 'Supplementals').
  - A line prints as a block: the line row (gross 'Weapon System Cost', in parentheses when
    other rows follow), labeled cost-type rows ('Less: Advance Procurement (PY)', 'C (FY 2026
    for FY 2027) (M)', ...), and from FY2026 an unlabeled net row. Titles wrap upward: the
    line number sits on the last line of a wrapped title.
  - 'Budget Activity 01: Aircraft' headings give the BA; 'Total <BA title>' rows close a BA,
    'Total <appropriation>' closes the account.
  - Summary pages print full column labels and are read like the R-1 summaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .config import ExhibitColumns, normalize_header
from .pdf_common import (
    COLUMN_TOLERANCE,
    PdfFormatError,
    Word,
    amount_columns,
    assign_amounts,
    assign_section_ids,
    group_lines,
    header_band,
    header_bottom,
    is_number,
    map_columns,
    page_header_meta,
    page_words,
    parse_number,
    text_of,
)

ACCOUNT_RE = re.compile(r"^\d{4}[A-Z]$")
BA_HEADING_RE = re.compile(r"^Budget Activity (\d+):?\s*(.*)$")
WRAP_MAX_GAP = 14.0
# sub-column headers under each FY column; FY2025 prints 'Quantity' and 'Cost*'
SUB_HEADERS = {"Qty": "Qty", "Quantity": "Qty", "Cost": "Cost", "Cost*": "Cost"}


@dataclass
class P1Row:
    page_number: int
    role: str                  # 'line' | 'component' | 'net' | 'subtotal' | 'total' | 'summary' | 'unlabeled'
    label: str
    y: float
    amounts: dict[str, int]    # Excel amount header -> $K
    quantities: dict[str, int] = field(default_factory=dict)   # Excel amount header -> quantity
    line_number: str | None = None
    budget_activity: str | None = None
    key: tuple[str, str, str] | None = None                    # (account, BA, line) for line blocks


@dataclass
class P1Page:
    page_number: int
    section: str | None
    account: str | None
    title: str | None            # '2031A Detail', 'DoW Component Summary', ...
    printed_label: str | None
    kind: str | None             # 'detail' | 'summary' | None
    headers: list[str] = field(default_factory=list)   # Excel headers of the columns on this page
    qty_headers: list[str] = field(default_factory=list)   # ... of those, the ones with a Qty column
    rows: list[P1Row] = field(default_factory=list)
    section_id: str | None = None


@dataclass
class _Sub:
    kind: str       # 'Qty' | 'Cost'
    x0: float
    column: int = -1


def _norm_title(text: str) -> str:
    return normalize_header(text.replace("&", "and").replace("*", ""))


def _title_match(label: str, title: str, wrapped: bool = False) -> bool:
    """Normalized equality; a label that wrapped onto the next line may match as a prefix."""
    return bool(title) and (label == title or (wrapped and title.startswith(label)))


def _page_title(lines: list[list[Word]]) -> str | None:
    for line in lines:
        if 60 < line[0].y0 < 100:
            t = text_of([w for w in line if w.x0 < 700])
            if "Detail" in t or "Summary" in t:
                return t
    return None


class _ColumnCursor:
    """Assigns logical PDF column indices to Qty/Cost sub-columns across page pairs."""

    def __init__(self, ncols: int):
        self.ncols = ncols
        self.next = 0

    def assign(self, subs: list[_Sub], page_number: int) -> None:
        if self.next >= self.ncols:
            self.next = 0      # previous page completed the column set: a new page pair starts
        for s in subs:
            s.column = self.next
            if s.kind == "Cost":
                self.next += 1
        if self.next > self.ncols:
            raise PdfFormatError(
                f"page {page_number}: {self.next} Qty/Cost columns but the column map has {self.ncols}"
            )


def _detail_page(page: P1Page, words: list[Word], lines, page_height: float,
                 cols: ExhibitColumns, cursor: _ColumnCursor, carry: dict,
                 cols_account_titles: dict[str, tuple[str, ...]]) -> None:
    sub_line = next((l for l in lines if sum(w.text in SUB_HEADERS for w in l) >= 2), None)
    if sub_line is None:
        return
    subs = [_Sub(SUB_HEADERS[w.text], w.x0) for w in sub_line if w.text in SUB_HEADERS]
    cursor.assign(subs, page.page_number)
    pdf_headers = list(cols.pdf_columns.values())
    page.headers = sorted({pdf_headers[s.column] for s in subs if s.kind == "Cost"}, key=pdf_headers.index)
    page.qty_headers = sorted({pdf_headers[s.column] for s in subs if s.kind == "Qty"}, key=pdf_headers.index)
    sub_y = sub_line[0].y0
    near = [w for w in words if sub_y - 20 <= w.y0 <= sub_y + 2]
    item = min((w.x0 for w in near if w.text == "Item"), default=None)
    ident = min((w.x0 for w in near if w.text in ("Ident", "Code")), default=None)
    if item is None:
        raise PdfFormatError(f"page {page.page_number}: detail page without an 'Item Nomenclature' header")
    first_amount_x = subs[0].x0
    label_max_x = (ident - 3) if ident else first_amount_x

    def split(nums: list[Word]) -> tuple[dict[str, int], dict[str, int]]:
        amounts, qtys = {}, {}
        for w in nums:
            i = max(j for j, s in enumerate(subs) if s.x0 <= w.x1 - 1) if w.x1 - 1 >= subs[0].x0 else None
            if i is None:
                raise PdfFormatError(f"page {page.page_number}: amount {w.text} left of the first column")
            s = subs[i]
            header = pdf_headers[s.column]
            target = qtys if s.kind == "Qty" else amounts
            if header in target:
                raise PdfFormatError(f"page {page.page_number}: two values in one {s.kind} column on one row")
            target[header] = parse_number(w.text)
        return amounts, qtys

    account_titles = {_norm_title(t) for t in cols_account_titles.get(page.account, ())}
    group = tuple(page.headers)            # A pages and B pages carry their own state
    state = carry.setdefault((page.account, group), {"ba": None, "ba_title": None, "ba_closed": False, "key": None})
    pending: tuple[list[Word], float] | None = None
    footer_y = page_height * 0.9
    # Footnote text ('*Includes enacted funding in ...' and its wrapped lines) starts left of the
    # line-number column; it can sit mid-page between account sections.
    line_hdr_x0 = min((w.x0 for w in near if w.text == "Line"), default=item)
    footnote_max_x0 = line_hdr_x0 - 8
    seen_line = False

    for line in lines:
        y = line[0].y0
        if y <= sub_y + 2 or y >= footer_y:
            continue
        line = [w for w in line if w.text != "UNCLASSIFIED"]
        if not line or line[0].x0 < footnote_max_x0:
            continue
        nums = [w for w in line if is_number(w.text) and w.x1 - 1 >= first_amount_x]
        left = [w for w in line if w not in nums and w.x0 < label_max_x]
        if not line:
            continue
        first = left[0] if left else None
        in_line_col = first is not None and first.x0 < item - 3

        if in_line_col and first.text.isdigit():
            title = [w for w in left[1:] if w.x0 >= item - 3]
            if pending and y - pending[1] <= WRAP_MAX_GAP:
                title = pending[0] + title
            amounts, qtys = split(nums)
            seen_line = True
            state["key"] = (page.account, state["ba"], first.text)
            page.rows.append(P1Row(page.page_number, "line", text_of(title), y, amounts, qtys,
                                   line_number=first.text, budget_activity=state["ba"], key=state["key"]))
            pending = None
        elif in_line_col and not nums:
            m = BA_HEADING_RE.match(text_of(left))
            new_ba = m.group(1).zfill(2) if m else state["ba"]
            # Headings end the current line block, except the ones repeated at the top of a page
            # when a block continues from the previous page.
            if seen_line or new_ba != state["ba"]:
                state["key"] = None
            if m:
                state["ba"], state["ba_title"], state["ba_closed"] = new_ba, m.group(2), False
            pending = None
        elif in_line_col:
            # A total that repeats the open BA's title closes the BA (0360D's BA 01 has the account's
            # own title); else 'Total <appropriation>' closes the account (long titles are cut
            # off: 'Total Procurement of Weapons and Tracked'); any other total closes the open BA
            # (labels do not reliably repeat the heading: 'Total Reserve Equiment').
            # Total rows have no Ident/Sec values, so their label may run across those columns;
            # one that does is long enough to have wrapped, and may match a title as a prefix.
            full = [w for w in line if w not in nums and w.x1 < first_amount_x]
            label = text_of(full)
            wrapped = full[-1].x1 > label_max_x
            bare = _norm_title(re.sub(r"^(Grand )?Total\s+", "", label))
            if not state["ba_closed"] and _title_match(bare, _norm_title(state["ba_title"] or ""), wrapped):
                is_ba_total = True
            else:
                is_ba_total = not any(_title_match(bare, t, wrapped) for t in account_titles)
            if is_ba_total:
                state["ba_closed"] = True
            amounts, qtys = split(nums)
            page.rows.append(P1Row(page.page_number, "subtotal" if is_ba_total else "total", label, y,
                                   amounts, qtys, budget_activity=state["ba"] if is_ba_total else None))
            state["key"] = None
            pending = None
        elif left and nums:
            amounts, qtys = split(nums)
            page.rows.append(P1Row(page.page_number, "component", text_of(left), y, amounts, qtys,
                                   budget_activity=state["ba"], key=state["key"]))
            pending = None
        elif nums:
            amounts, qtys = split(nums)
            role = "net" if state["key"] else "unlabeled"
            page.rows.append(P1Row(page.page_number, role, "", y, amounts, qtys,
                                   budget_activity=state["ba"], key=state["key"]))
            pending = None
        elif left:
            if pending and y - pending[1] <= WRAP_MAX_GAP:
                pending = (pending[0] + left, y)
            else:
                pending = (left, y)


def _summary_page(page: P1Page, words: list[Word], lines, page_height: float, cols: ExhibitColumns) -> None:
    hb = header_band(words, min_y=100, max_y=260)
    if hb is None:
        return
    band, top = hb
    columns = amount_columns(band, top)
    if not columns:
        return
    headers = map_columns(columns, cols.pdf_columns, page.page_number)
    page.headers = headers
    bottom = header_bottom(columns)
    first_x1 = min(c.x1 for c in columns)
    footer_y = page_height * 0.9
    pending: tuple[list[Word], float] | None = None
    for line in lines:
        y = line[0].y0
        if y <= bottom + 1 or y >= footer_y:
            continue
        nums = [w for w in line if is_number(w.text) and w.x1 >= first_x1 - COLUMN_TOLERANCE]
        left = [w for w in line if w not in nums and w.x1 < first_x1 - COLUMN_TOLERANCE]
        if left and nums:
            page.rows.append(P1Row(page.page_number, "summary", text_of(left), y,
                                   assign_amounts(nums, columns, headers, page.page_number)))
            pending = None
        elif nums and pending and y - pending[1] <= WRAP_MAX_GAP:
            page.rows.append(P1Row(page.page_number, "summary", text_of(pending[0]), y,
                                   assign_amounts(nums, columns, headers, page.page_number)))
            pending = None
        elif left:
            pending = (left, y)


def parse_p1_pdf(path: Path, cols: ExhibitColumns, account_titles: dict[str, tuple[str, ...]] | None = None) -> list[P1Page]:
    """account_titles: account code -> known titles, to tell appropriation totals from BA totals."""
    if account_titles is None:
        from .config import load_accounts
        account_titles = {c: a.titles for c, a in load_accounts().items()}
    pages: list[P1Page] = []
    cursor = _ColumnCursor(len(cols.pdf_columns))
    carry: dict = {}
    with pymupdf.open(path) as doc:
        for i in range(doc.page_count):
            pg = doc[i]
            words = page_words(pg)
            height = pg.rect.height   # already in displayed (rotated) orientation
            lines = group_lines(words)
            meta = page_header_meta(i + 1, lines, height)
            title = _page_title(lines)
            account = None
            kind = None
            if title:
                tok = title.split()[0]
                account = tok if ACCOUNT_RE.match(tok) else None
                kind = "detail" if "Detail" in title else "summary"
            page = P1Page(i + 1, meta.section, account, title, meta.printed_label, kind)
            if kind == "detail" and account:
                _detail_page(page, words, lines, height, cols, cursor, carry, account_titles)
            elif kind == "summary":
                _summary_page(page, words, lines, height, cols)
            pages.append(page)
    assign_section_ids(pages)
    return pages
