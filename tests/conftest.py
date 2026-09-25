from pathlib import Path

import pytest

from pipeline.config import load_accounts, load_column_map
from pipeline.link_pages import link_line_items
from pipeline.normalize import normalize_rows
from pipeline.parse_r1 import parse_r1_workbook
from pipeline.pdf_r1 import parse_r1_pdf

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_XLSX = FIXTURES / "r1_sample.xlsx"
# Fixture pages 1-6 are physical pages 6, 7, 10, 11, 87, 97 of FY2027_r1.pdf
SAMPLE_PDF = FIXTURES / "r1_sample.pdf"


@pytest.fixture(scope="session")
def cols():
    return load_column_map("R-1", "PB2027")


@pytest.fixture(scope="session")
def accounts():
    return load_accounts()


@pytest.fixture(scope="session")
def raw_rows(cols):
    return parse_r1_workbook(SAMPLE_XLSX, cols)


@pytest.fixture(scope="session")
def records(raw_rows, cols, accounts):
    return normalize_rows(raw_rows, cols, accounts)


@pytest.fixture(scope="session")
def pages(cols):
    return parse_r1_pdf(SAMPLE_PDF, cols.pdf_columns)


@pytest.fixture(scope="session")
def links(records, pages, cols):
    return link_line_items(records, pages, cols)


def by_key(records, account, ba, line):
    return next(r for r in records if r.key == (account, ba, line))
