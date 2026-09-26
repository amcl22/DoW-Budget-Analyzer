"""Per-exhibit pieces of the pipeline: how to normalize rows, parse the summary PDF and link
line items to pages. Everything else (fetch, workbook parsing, loading, validation dispatch)
is shared."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import ExhibitColumns
from .link_pages import link_line_items, link_p1_line_items
from .normalize import normalize_p1_rows, normalize_rows
from .pdf_p1 import parse_p1_pdf
from .pdf_r1 import parse_r1_pdf


@dataclass(frozen=True)
class Exhibit:
    code: str                 # 'R-1'
    family: str               # 'RDTE' | 'PROC'
    normalize: Callable
    parse_pdf: Callable[[Path, ExhibitColumns], list]
    link: Callable
    ref_kind: str             # line_item_source_ref.ref_kind for summary-PDF pages
    match_method: str


EXHIBITS: dict[str, Exhibit] = {
    "R-1": Exhibit(
        "R-1", "RDTE", normalize_rows, lambda path, cols: parse_r1_pdf(path, cols.pdf_columns),
        link_line_items, "r1_summary", "acct+ba+line+pe",
    ),
    "P-1": Exhibit(
        "P-1", "PROC", normalize_p1_rows, parse_p1_pdf,
        link_p1_line_items, "p1_summary", "acct+ba+line+cost-elements",
    ),
}
