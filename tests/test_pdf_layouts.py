"""Layout variations between releases (FY2024 R-1) and the shared PDF helpers."""

import pytest

from pipeline.config import load_column_map
from pipeline.pdf_common import is_number, parse_number
from pipeline.pdf_r1 import parse_r1_pdf
from tests.conftest import FIXTURES


@pytest.fixture(scope="module")
def pages24():
    cols = load_column_map("R-1", "PB2024")
    return parse_r1_pdf(FIXTURES / "r1_2024_sample.pdf", cols.pdf_columns)


def test_adjacent_header_phrases_stay_separate_columns(pages24):
    summary = pages24[0]   # physical p5: 'Supplementals' headers only ~9pt apart
    army = next(r for r in summary.rows if r.label == "Research, Development, Test and Evaluation, Army")
    assert set(army.amounts) >= {"FY 2023 Less Supplementals Enacted", "FY 2023 Total Enacted"}


def test_unlabeled_amount_row_is_recorded_not_fatal(pages24):
    assert [r.kind for r in pages24[1].rows].count("unlabeled") == 1


def test_first_data_row_near_the_header_is_kept(pages24):
    detail = pages24[3]    # physical p10: line 19 sits within the header band's depth
    assert any(r.kind == "line" and r.line_number == "19" for r in detail.rows)


def test_stray_line_number_on_a_subtotal_is_dropped(pages24):
    detail = pages24[3]
    applied = [r for r in detail.rows if r.label == "Applied Research"]
    assert [r.kind for r in applied] == ["subtotal"]
    assert not any(r.kind == "line" and r.pe == "Applied" for r in detail.rows)


def test_subtotals_carry_the_budget_activity_of_the_lines_above(pages24):
    applied = next(r for r in pages24[3].rows if r.label == "Applied Research")
    assert applied.budget_activity == "02"


@pytest.mark.parametrize("text,value", [
    ("1,234", 1234), ("(667,759)", 667759), ("(-110,360)", -110360), ("-595,699", -595699), ("7", 7),
])
def test_parse_number(text, value):
    assert is_number(text) and parse_number(text) == value


@pytest.mark.parametrize("text", ["2024", "(UAS)", "6.5", "FY"])
def test_not_numbers(text):
    assert not is_number(text)
