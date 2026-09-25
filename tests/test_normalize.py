from dataclasses import replace

import pytest

from pipeline.normalize import CLASSIFIED_PE, NormalizeError, normalize_pe, normalize_rows
from tests.conftest import by_key


def test_amounts_are_integers_and_blanks_are_dropped(records):
    r = by_key(records, "2040A", "01", "1")
    assert r.amount("FY 2027 Total") == 215322
    assert r.amount("FY 2027 Mandatory Request") is None
    headers = {a.source_column for a in r.amounts}
    assert "FY 2025 Reconciliation" not in headers


def test_amounts_carry_year_type_and_category(records):
    r = by_key(records, "2040A", "01", "1")
    spend = next(a for a in r.amounts if a.source_column == "FY 2026 PL 119-21 Spend Plan")
    assert (spend.funds_fiscal_year, spend.amount_type, spend.funding_category, spend.amount_thousands) == (
        2026, "enacted", "spend_plan", 1000,
    )


def test_service_branch_comes_from_the_account(records):
    assert by_key(records, "2040A", "01", "1").service_branch == "Army"
    golden_dome = [r for r in records if r.appropriation_account == "3007D"]
    assert golden_dome and {r.service_branch for r in golden_dome} == {"Defense-Wide"}


def test_classified_lines_get_a_per_account_program(records):
    r = by_key(records, "2040A", "02", "999")
    assert r.is_classified
    assert r.program_element == CLASSIFIED_PE
    assert r.program_key == f"{CLASSIFIED_PE}:2040A"
    assert r.organization is None


def test_same_pe_on_two_lines_is_two_records(records):
    mda = [r for r in records if r.program_element == "0604139D8Z"]
    assert len({r.key for r in mda}) == len(mda) >= 2


@pytest.mark.parametrize("raw,expected", [("0601102A", "0601102A"), (" 0601 102-a ", "0601102A")])
def test_normalize_pe(raw, expected):
    assert normalize_pe(raw) == expected


def _with(raw_rows, index, **fields):
    rows = list(raw_rows)
    rows[index] = replace(rows[index], fields={**rows[index].fields, **fields})
    return rows


def test_unknown_account_fails(raw_rows, cols, accounts):
    with pytest.raises(NormalizeError, match="9999X"):
        normalize_rows(_with(raw_rows, 0, appropriation_account="9999X"), cols, accounts)


def test_invalid_pe_fails(raw_rows, cols, accounts):
    with pytest.raises(NormalizeError, match="not a valid program element"):
        normalize_rows(_with(raw_rows, 0, program_element="ABC"), cols, accounts)


def test_non_numeric_amount_fails(raw_rows, cols, accounts):
    rows = list(raw_rows)
    rows[0] = replace(rows[0], amounts={**rows[0].amounts, "FY 2027 Total": "12.5"})
    with pytest.raises(NormalizeError, match="not a whole number"):
        normalize_rows(rows, cols, accounts)


def test_all_problems_are_reported_together(raw_rows, cols, accounts):
    rows = _with(_with(raw_rows, 0, include_in_toa="maybe"), 1, appropriation_account="0000Z")
    with pytest.raises(NormalizeError) as exc:
        normalize_rows(rows, cols, accounts)
    assert len(exc.value.problems) == 2
