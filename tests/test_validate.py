from dataclasses import replace

from pipeline.config import ExpectedTotal
from pipeline.normalize import Amount
from pipeline.validate import (
    build_report,
    check_department_summary,
    check_expected_totals,
    check_row_arithmetic,
    check_section_subtotals,
    check_unique_keys,
    run_checks,
)
from tests.conftest import by_key


def test_section_subtotals_match_on_fixture(records, pages, links, cols):
    result = check_section_subtotals(records, pages, links, list(cols.pdf_columns.values()))
    assert result.passed, result.details
    assert "match" in result.summary


def test_section_subtotal_catches_a_missing_line(records, pages, links, cols):
    missing = [r for r in records if r.key != ("2040A", "01", "2")]
    result = check_section_subtotals(missing, pages, links, list(cols.pdf_columns.values()))
    assert not result.passed
    assert any("Basic research" in d for d in result.details)


def test_department_summary_flags_partial_data(records, pages, accounts, cols):
    # the fixture holds only some Army lines, so the Army total cannot match
    result = check_department_summary(records, pages, accounts, list(cols.pdf_columns.values()))
    assert not result.passed
    assert any(d.startswith("2040A:") for d in result.details)


def test_row_arithmetic_catches_bad_total(records):
    rec = by_key(records, "2040A", "01", "1")
    bad = replace(rec, amounts=rec.amounts + [Amount(2027, "request", "mandatory", 5, "FY 2027 Mandatory Request")])
    assert check_row_arithmetic(records).passed
    assert not check_row_arithmetic([bad]).passed


def test_unique_keys_catches_duplicates(records):
    assert not check_unique_keys(records + records[:1]).passed


def test_expected_totals(records):
    army_bas = [r for r in records if r.key[:2] == ("2040A", "01")]
    total = sum(r.amount("FY 2027 Total") or 0 for r in army_bas)
    assert check_expected_totals(army_bas, [ExpectedTotal("t", True, {"FY 2027 Total": total})]).passed
    assert not check_expected_totals(army_bas, [ExpectedTotal("t", True, {"FY 2027 Total": total + 1})]).passed
    assert not check_expected_totals(army_bas, []).passed


def test_report_fails_when_a_hard_check_fails(records, pages, links, cols, accounts):
    report = build_report(run_checks(records, pages, links, cols, accounts, []), {})
    assert report["passed"] is False
    names = {c["name"]: c["passed"] for c in report["checks"]}
    assert names["page_coverage"] and names["amounts_verified"] and names["section_subtotals"]
