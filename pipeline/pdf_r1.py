"""Parse the R-1 summary PDF into positioned table rows.

The R-1 PDF is a text PDF with a fixed layout (checked against FY2027_r1.pdf):
  - page header: 'UNCLASSIFIED', a section name ('Department of the Army', 'Defense-Wide',
    'Defense Health Agency', ...), and on detail pages 'Appropriation: <code> <title>'
  - amount column headers stacked over 2-3 lines ('FY 2026' / 'Discretionary' / 'Enacted')
  - amounts right-aligned under their headers; blank cells are simply absent
  - line rows: line number at x~20, PE at x~46, title from x~100, budget activity at x~295
  - budget activity subtotals: label at x~46, no line number
  - appropriation totals and summary-page rows: label at x~20
We read words with coordinates and assign each amount to the column whose header's right
edge is nearest, so every amount on every row can be compared with the Excel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .config import normalize_header

AMOUNT_AREA_X0 = 335.0        # amount columns sit right of this; row labels left of it
LINE_NO_MAX_X0 = 35.0
PE_X0_RANGE = (40.0, 60.0)
TITLE_MIN_X0 = 95.0
BA_X0_RANGE = (285.0, 312.0)
SAME_LINE_TOLERANCE = 2.5     # words within this many points vertically share a text line
HEADER_DEPTH = 30.0           # header labels span at most this far below the first 'FY'
COLUMN_TOLERANCE = 30.0       # max distance between an amount's right edge and its header's
WRAP_MAX_GAP = 12.0           # a label line this close below a row continues that row's label

NUMBER_RE = re.compile(r"^-?\d{1,3}(,\d{3})*$")
PAGE_LABEL_RE = re.compile(r"^Page \d+$")


class PdfFormatError(Exception):
    pass


@dataclass(frozen=True)
class Word:
    x0: float
    y0: float
    x1: float
    text: str


@dataclass
class PdfRow:
    page_number: int
    kind: str                      # 'line' | 'subtotal' | 'total' | 'summary'
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
    # A section is a run of consecutive pages with the same header and appropriation. Header
    # text alone is not unique: the DEFW agency detail is also headed 'Defense-Wide'.
    section_id: str | None = None


@dataclass(frozen=True)
class _Column:
    label: str
    x1: float


def _group_lines(words: list[Word]) -> list[list[Word]]:
    lines: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.y0, w.x0)):
        if lines and w.y0 - lines[-1][0].y0 <= SAME_LINE_TOLERANCE:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w.x0) for line in lines]


def _text(words: list[Word]) -> str:
    return " ".join(w.text for w in words)


def _header_columns(words: list[Word]) -> tuple[list[_Column], float] | None:
    fy = [w for w in words if w.text == "FY" and w.x0 >= AMOUNT_AREA_X0 and 90 < w.y0 < 220]
    if not fy:
        return None
    top = min(w.y0 for w in fy)
    header = [
        w for w in words
        if w.x0 >= AMOUNT_AREA_X0 and top - 1 <= w.y0 <= top + HEADER_DEPTH and not NUMBER_RE.match(w.text)
    ]
    # Cluster header words whose horizontal extents overlap: one cluster per column.
    clusters: list[list[Word]] = []
    for w in sorted(header, key=lambda w: w.x0):
        if clusters and w.x0 <= max(c.x1 for c in clusters[-1]) + 1:
            clusters[-1].append(w)
        else:
            clusters.append([w])
    columns = [
        _Column(
            label=" ".join(w.text for w in sorted(c, key=lambda w: (round(w.y0), w.x0))),
            x1=max(w.x1 for w in c),
        )
        for c in clusters
    ]
    return columns, max(w.y0 for w in header)


def _parse_page(page_number: int, raw_words, pdf_columns: dict[str, str]) -> PdfPage:
    words = [Word(w[0], w[1], w[2], w[4]) for w in raw_words]
    lines = _group_lines(words)

    section = next(
        (_text(l) for l in lines if l[0].y0 < 60 and _text(l) != "UNCLASSIFIED"), None
    )
    account = account_title = None
    for line in lines:
        if line[0].text == "Appropriation:" and len(line) > 1:
            account, account_title = line[1].text, _text(line[2:]) or None
            break
    printed_label = None
    for line in lines:
        texts = [w.text for w in line]
        for i in range(len(texts) - 1):
            if texts[i] == "Page" and texts[i + 1].isdigit() and line[0].y0 > 500:
                printed_label = f"Page {texts[i + 1]}"

    page = PdfPage(page_number, section, account, account_title, printed_label)
    header = _header_columns(words)
    if header is None:
        return page
    columns, header_bottom = header

    label_to_header = {normalize_header(k): v for k, v in pdf_columns.items()}
    col_headers = []
    for col in columns:
        # footnote markers ('FY 2026 Total*') don't change which column it is
        excel_header = label_to_header.get(normalize_header(col.label.replace("*", "")))
        if excel_header is None:
            raise PdfFormatError(
                f"page {page_number}: amount column {col.label!r} is not in the pdf_columns map"
            )
        col_headers.append(excel_header)

    def assign(nums: list[Word]) -> dict[str, int]:
        out: dict[str, int] = {}
        for w in nums:
            i = min(range(len(columns)), key=lambda i: abs(columns[i].x1 - w.x1))
            if abs(columns[i].x1 - w.x1) > COLUMN_TOLERANCE:
                raise PdfFormatError(f"page {page_number}: amount {w.text} at x={w.x1:.1f} fits no column")
            if col_headers[i] in out:
                raise PdfFormatError(f"page {page_number}: two amounts in column {columns[i].label!r} on one row")
            out[col_headers[i]] = int(w.text.replace(",", ""))
        return out

    kind_for_label = "total" if account else "summary"
    last: PdfRow | None = None
    pending_label: tuple[list[Word], float] | None = None  # a label line with no amounts yet

    for line in lines:
        y = line[0].y0
        if y <= header_bottom + 1:
            continue
        line = [w for w in line if w.text != "UNCLASSIFIED"]
        if not line or PAGE_LABEL_RE.match(_text(line)):
            continue
        left = [w for w in line if w.x0 < AMOUNT_AREA_X0]
        nums = [w for w in line if w.x0 >= AMOUNT_AREA_X0 and NUMBER_RE.match(w.text)]

        is_line_row = (
            len(left) >= 2
            and left[0].x0 < LINE_NO_MAX_X0
            and left[0].text.isdigit()
            and PE_X0_RANGE[0] <= left[1].x0 <= PE_X0_RANGE[1]
        )
        if is_line_row:
            ba = next(
                (w.text for w in left if BA_X0_RANGE[0] <= w.x0 <= BA_X0_RANGE[1] and w.text.isdigit()),
                None,
            )
            title = [w for w in left[2:] if TITLE_MIN_X0 <= w.x0 < BA_X0_RANGE[0]]
            last = PdfRow(page_number, "line", _text(title), y, assign(nums),
                          line_number=left[0].text, pe=left[1].text, budget_activity=ba)
            page.rows.append(last)
            pending_label = None
        elif left and nums:
            kind = "subtotal" if left[0].x0 >= PE_X0_RANGE[0] else kind_for_label
            last = PdfRow(page_number, kind, _text(left), y, assign(nums))
            page.rows.append(last)
            pending_label = None
        elif nums and pending_label and y - pending_label[1] <= WRAP_MAX_GAP:
            # label wrapped onto several lines with the amounts on its last line
            lw = pending_label[0]
            kind = "subtotal" if lw[0].x0 >= PE_X0_RANGE[0] else kind_for_label
            last = PdfRow(page_number, kind, _text(lw), y, assign(nums))
            page.rows.append(last)
            pending_label = None
        elif left and not nums:
            if last and last.kind == "line" and left[0].x0 >= TITLE_MIN_X0 and y - last.y <= 2 * WRAP_MAX_GAP:
                last.label = f"{last.label} {_text(left)}"
            elif pending_label and y - pending_label[1] <= WRAP_MAX_GAP and abs(left[0].x0 - pending_label[0][0].x0) < 2:
                pending_label = (pending_label[0] + left, y)
            else:
                pending_label = (left, y)
        elif nums:
            raise PdfFormatError(f"page {page_number}: amounts at y={y:.1f} have no row label")

    return page


def assign_section_ids(pages: list[PdfPage]) -> None:
    """Consecutive pages with the same header and appropriation form one section."""
    prev, start = None, None
    for page in pages:
        key = (page.section, page.account)
        if key != prev:
            prev, start = key, page.page_number
        page.section_id = f"{page.section} @p{start}"


def parse_r1_pdf(path: Path, pdf_columns: dict[str, str]) -> list[PdfPage]:
    with pymupdf.open(path) as doc:
        pages = [
            _parse_page(i + 1, doc[i].get_text("words"), pdf_columns) for i in range(doc.page_count)
        ]
    assign_section_ids(pages)
    return pages
