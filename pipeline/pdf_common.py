"""Shared helpers for reading the Comptroller's exhibit PDFs (R-1, P-1) by word position.

Layouts shift between years and exhibits (column x-positions, page rotation, header wording),
so nothing here assumes fixed coordinates. Each page's amount columns are discovered from its
own table header: the stacked header phrases that start with 'FY'.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf

from .config import normalize_header

SAME_LINE_TOLERANCE = 2.5   # words within this many points vertically share a text line
HEADER_DEPTH = 32.0         # stacked header labels span at most this far below the first 'FY'
PHRASE_GAP = 7.0            # words on one line closer than this belong to the same header phrase
                            # (word spacing is ~4.8pt; adjacent columns can be only ~9pt apart)
COLUMN_TOLERANCE = 30.0     # max distance between an amount's right edge and its header's

NUMBER_RE = re.compile(r"^\(?-?\(?\d{1,3}(,\d{3})*\)?$")


class PdfFormatError(Exception):
    pass


@dataclass(frozen=True)
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


@dataclass(frozen=True)
class Column:
    label: str          # header words joined top-to-bottom, e.g. 'FY 2026 PL 119-21 Spend Plan'
    x0: float
    x1: float


@dataclass
class PageMeta:
    page_number: int                # physical, 1-based
    section: str | None             # 'Department of the Army', 'Defense-Wide', ...
    printed_label: str | None       # 'Page 5'


def page_words(page: pymupdf.Page) -> list[Word]:
    """Words in displayed (rotation-applied) coordinates."""
    m = page.rotation_matrix
    out = []
    for w in page.get_text("words"):
        r = pymupdf.Rect(w[:4]) * m
        out.append(Word(r.x0, r.y0, r.x1, r.y1, w[4]))
    return out


def group_lines(words: list[Word]) -> list[list[Word]]:
    lines: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.y0, w.x0)):
        if lines and w.y0 - lines[-1][0].y0 <= SAME_LINE_TOLERANCE:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w.x0) for line in lines]


def text_of(words: list[Word]) -> str:
    return " ".join(w.text for w in words)


def is_number(text: str) -> bool:
    return bool(NUMBER_RE.match(text))


def parse_number(text: str) -> int:
    """'1,234' -> 1234; '(1,234)' and '(-1,234)' keep their sign ('(-110,360)' -> -110360)."""
    t = text.replace(",", "").strip("()")
    return int(t)


def page_header_meta(page_number: int, lines: list[list[Word]], page_height: float) -> PageMeta:
    section = next(
        (text_of(l) for l in lines if l[0].y0 < 60 and text_of(l) != "UNCLASSIFIED"), None
    )
    printed = None
    for line in lines:
        if line[0].y0 < page_height * 0.85:
            continue
        texts = [w.text for w in line]
        for i in range(len(texts) - 1):
            if texts[i] == "Page" and texts[i + 1].isdigit():
                printed = f"Page {texts[i + 1]}"
    return PageMeta(page_number, section, printed)


def header_band(words: list[Word], min_y: float = 90, max_y: float = 240) -> tuple[list[Word], float] | None:
    """Words of the table header: from the first 'FY' down HEADER_DEPTH points."""
    fy = [w for w in words if w.text == "FY" and min_y < w.y0 < max_y]
    if not fy:
        return None
    top = min(w.y0 for w in fy)
    band = [w for w in words if top - 12 <= w.y0 <= top + HEADER_DEPTH and not is_number(w.text)]
    return band, top


def amount_columns(band: list[Word], top: float) -> list[Column]:
    """Cluster header phrases into columns; keep the clusters whose label starts with 'FY'."""
    phrases: list[list[Word]] = []
    for line in group_lines([w for w in band if w.y0 >= top - 1]):
        for w in line:
            if phrases and phrases[-1][-1].y0 == w.y0 and w.x0 - phrases[-1][-1].x1 < PHRASE_GAP:
                phrases[-1].append(w)
            else:
                phrases.append([w])
    clusters: list[list[Word]] = []
    extent: list[float] = []
    for ph in sorted(phrases, key=lambda p: p[0].x0):
        x0, x1 = ph[0].x0, max(w.x1 for w in ph)
        if clusters and x0 <= extent[-1] + 1:
            clusters[-1].extend(ph)
            extent[-1] = max(extent[-1], x1)
        else:
            clusters.append(list(ph))
            extent.append(x1)
    cols = []
    for c in clusters:
        label = " ".join(text_of(l) for l in group_lines(c))
        if label.startswith("FY"):
            cols.append(Column(label, min(w.x0 for w in c), max(w.x1 for w in c)))
    return cols


def map_columns(cols: list[Column], pdf_columns: dict[str, str], page_number: int) -> list[str]:
    """PDF column labels -> Excel headers via config; unknown labels fail loudly."""
    lookup = {normalize_header(k.replace("*", "")): v for k, v in pdf_columns.items()}
    out = []
    for c in cols:
        header = lookup.get(normalize_header(c.label.replace("*", "")))
        if header is None:
            raise PdfFormatError(f"page {page_number}: amount column {c.label!r} is not in the pdf_columns map")
        out.append(header)
    return out


def assign_amounts(nums: list[Word], cols: list[Column], headers: list[str], page_number: int) -> dict[str, int]:
    """Right-aligned amounts go to the column whose header's right edge is nearest."""
    out: dict[str, int] = {}
    for w in nums:
        i = min(range(len(cols)), key=lambda i: abs(cols[i].x1 - w.x1))
        if abs(cols[i].x1 - w.x1) > COLUMN_TOLERANCE:
            raise PdfFormatError(f"page {page_number}: amount {w.text} at x={w.x1:.1f} fits no column")
        if headers[i] in out:
            raise PdfFormatError(f"page {page_number}: two amounts in column {cols[i].label!r} on one row")
        out[headers[i]] = parse_number(w.text)
    return out


def assign_section_ids(pages: list) -> None:
    """Consecutive pages with the same header and appropriation form one section. Header text
    alone is not unique (the DEFW agency detail is also headed 'Defense-Wide')."""
    prev, start = None, None
    for page in pages:
        key = (page.section, page.account)
        if key != prev:
            prev, start = key, page.page_number
        page.section_id = f"{page.section} @p{start}"
