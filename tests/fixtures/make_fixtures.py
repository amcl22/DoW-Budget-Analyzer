"""Rebuild the small offline test fixtures from the real FY2027 R-1 files.

    python tests/fixtures/make_fixtures.py path/to/r1_display.xlsx path/to/FY2027_r1.pdf

Writes r1_sample.pdf (a handful of pages) and r1_sample.xlsx (the Excel rows that appear on
those pages). Both source files are public DoW Comptroller releases.
"""

import sys
from pathlib import Path

import openpyxl
import pymupdf

from pipeline.config import load_accounts, load_column_map
from pipeline.link_pages import link_line_items
from pipeline.normalize import normalize_rows
from pipeline.parse_r1 import parse_r1_workbook
from pipeline.pdf_r1 import parse_r1_pdf

# 6-7: department summaries (7 has 'FY 2026 Total*'); 10-11: Army BA 01-03 detail incl. a
# classified line; 87: Golden Dome fund; 97: the DEFW agency section headed 'Defense-Wide'.
PAGES = [6, 7, 10, 11, 87, 97]
HERE = Path(__file__).parent


def main(xlsx: Path, pdf: Path) -> None:
    cols = load_column_map("R-1", "PB2027")
    records = normalize_rows(parse_r1_workbook(xlsx, cols).rows, cols, load_accounts())
    pages = parse_r1_pdf(pdf, cols.pdf_columns)
    links = link_line_items(records, pages, cols)
    keep_rows = {
        r.source_row_number
        for r in records
        if any(ref.page_number in PAGES for ref in links.refs.get(r.key, []))
    }

    # Copy pages into a fresh document so resources used only by other pages (the 1.8 MB
    # cover seal) are not carried along.
    with pymupdf.open(pdf) as doc, pymupdf.open() as sample:
        for p in PAGES:
            sample.insert_pdf(doc, from_page=p - 1, to_page=p - 1)
        sample.save(HERE / "r1_sample.pdf", garbage=4, deflate=True)

    src = openpyxl.load_workbook(xlsx)[cols.sheet]
    out = openpyxl.Workbook()
    ws = out.active
    ws.title = cols.sheet
    for i, row in enumerate(src.iter_rows(values_only=True), start=1):
        if i <= 2 or i in keep_rows:
            ws.append(row)
    out.save(HERE / "r1_sample.xlsx")
    print(f"wrote {len(PAGES)} pages and {len(keep_rows)} rows")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
