"""P-1: cost-element grouping, rotated/split PDF pages, block linking and P-1 checks."""

from dataclasses import replace

import pytest

from pipeline.config import load_accounts, load_column_map, load_expected_totals
from pipeline.exhibits import EXHIBITS
from pipeline.parse_xlsx import parse_workbook
from pipeline.validate import (
    check_expected_totals,
    check_p1_department_summary,
    check_p1_section_totals,
)
from tests.conftest import FIXTURES, by_key

P1 = EXHIBITS["P-1"]


def _load(fy):
    cols = load_column_map("P-1", f"PB{fy}")
    name = f"p1_{fy}_sample"
    records = P1.normalize(parse_workbook(FIXTURES / f"{name}.xlsx", cols).rows, cols, load_accounts(), "P-1")
    pages = P1.parse_pdf(FIXTURES / f"{name}.pdf", cols)
    return cols, records, pages, P1.link(records, pages, cols)


@pytest.fixture(scope="module")
def p27():
    return _load(2027)


@pytest.fixture(scope="module")
def p24():
    return _load(2024)


# --- normalize


def test_cost_type_rows_group_into_one_line_with_net_amounts(p27):
    _, records, _, _ = p27
    ah64 = by_key(records, "2031A", "01", "7")
    assert ah64.line_item_number == "5757A05111"
    assert ah64.program_key == "2031A:5757A05111"   # BLIs repeat across accounts
    assert [(e.cost_type, e.cost_type_title, e.is_add) for e in ah64.cost_elements] == [
        ("A", "Weapon System Cost", True),
        ("B", "Less: Advance Procurement (PY)", True),
    ]
    # net = weapon system cost 667,759 + advance procurement credit -110,360
    assert ah64.amount("FY 2025 Total Amount") == 557399
    assert ah64.quantity("FY 2025 Total Amount") == 31


def test_non_add_rows_do_not_count_toward_the_line(p24):
    _, records, _, _ = p24
    ap_line = by_key(records, "2031A", "01", "7")      # AH-64 advance procurement line
    memo = [e for e in ap_line.cost_elements if not e.is_add]
    assert memo and all(e.cost_type_title.startswith("C (FY") for e in memo)
    adds = [e for e in ap_line.cost_elements if e.is_add]
    header = "FY 2024 Request Amount"
    assert ap_line.amount(header) == sum(e.amount(header) or 0 for e in adds)


def test_include_in_toa_comes_from_config_for_p1(p27):
    _, records, _, _ = p27
    assert all(r.include_in_toa for r in records)


# --- PDF


def test_rotated_pages_read_in_display_orientation(p27):
    _, _, pages, _ = p27
    detail = pages[4]
    assert (detail.kind, detail.account, detail.section, detail.printed_label) == (
        "detail", "2031A", "Department of the Army", "Page 9",
    )


def test_columns_split_across_a_page_pair(p27):
    cols, _, pages, _ = p27
    a, b = pages[4], pages[5]
    assert a.headers + b.headers == list(cols.pdf_columns.values())
    # page A ends with the Qty of FY 2027 Discretionary Request; its Cost is on page B
    assert "FY 2027 Discretionary Request Amount" in a.qty_headers
    assert "FY 2027 Discretionary Request Amount" in b.headers


def test_line_block_rows(p27):
    _, _, pages, _ = p27
    rows = [r for r in pages[4].rows if r.key == ("2031A", "01", "7")]
    assert [r.role for r in rows] == ["line", "component", "net"]
    line, less, net = rows
    assert line.amounts["FY 2025 Total Amount"] == 667759      # printed '(667,759)'
    assert less.amounts["FY 2025 Total Amount"] == -110360     # printed '(-110,360)'
    assert net.amounts["FY 2025 Total Amount"] == 557399
    assert line.quantities["FY 2025 Total Amount"] == 31


def test_wrapped_title_is_joined_upward(p27):
    _, _, pages, _ = p27
    hades = next(r for r in pages[4].rows if r.role == "line" and r.line_number == "5")
    assert hades.label == "HADES PLATFORM, PAYLOADS/PED, AND INTEGRATION"


# --- linking


@pytest.mark.parametrize("fixture", ["p27", "p24"])
def test_every_fixture_line_links_and_verifies(fixture, request):
    _, records, _, links = request.getfixturevalue(fixture)
    for r in records:
        assert links.refs[r.key], r.key
        assert all(ref.amount_verified for ref in links.refs[r.key]), links.refs[r.key][0].mismatches
    assert not links.unmatched_pdf_rows


def test_page_pair_gives_two_refs_first_is_primary(p27):
    _, records, _, links = p27
    refs = links.refs[by_key(records, "2031A", "01", "7").key]
    assert [(r.page_number, r.is_primary) for r in refs] == [(5, True), (6, False)]


def test_line_without_a_printed_line_row_is_rehomed_by_title_and_amount(p27):
    _, records, _, links = p27
    # 1612N line 1 prints only its 'Subsequent Full Funding for FY 2024' row, above line 2
    refs = links.refs[by_key(records, "1612N", "01", "1").key]
    assert refs and refs[0].amount_verified


def test_advance_procurement_cy_row_moves_to_its_own_line(p24):
    _, records, _, links = p24
    # printed under the weapon line (6) / after a heading (3020F 3); filed under the AP line
    for key in [("2031A", "01", "6"), ("2031A", "01", "7"), ("3020F", "01", "3")]:
        assert links.refs[key][0].amount_verified, links.refs[key][0].mismatches


def test_cost_element_mismatch_is_caught(p27):
    cols, records, pages, _ = p27
    rec = by_key(records, "2031A", "01", "7")
    less = rec.cost_elements[1]
    bad_less = replace(less, amounts=[replace(a, amount_thousands=a.amount_thousands - 1) for a in less.amounts])
    bad = replace(rec, cost_elements=[rec.cost_elements[0], bad_less])
    ref = P1.link([bad], pages, cols).refs[bad.key][0]
    assert not ref.amount_verified
    assert any("less: advance procurement (py)" in m for m in ref.mismatches)


def test_quantity_mismatch_is_caught(p27):
    cols, records, pages, _ = p27
    rec = by_key(records, "2031A", "01", "1")
    weapon = rec.cost_elements[0]
    bad_weapon = replace(weapon, amounts=[replace(a, quantity=(a.quantity or 0) + 1) for a in weapon.amounts])
    bad = replace(rec, cost_elements=[bad_weapon],
                  amounts=[replace(a, quantity=(a.quantity or 0) + 1) for a in rec.amounts])
    ref = P1.link([bad], pages, cols).refs[bad.key][0]
    assert any(m.startswith("qty") for m in ref.mismatches)


# --- checks


def test_section_totals_on_fixture(p27):
    _, records, pages, _ = p27
    result = check_p1_section_totals(records, pages)
    # 1612N's totals are fully covered by the fixture; 2031A's BA total is not (partial lines)
    assert not any("1612N" in d for d in result.details), result.details


def test_department_summary_merges_page_pairs(p27):
    _, records, pages, _ = p27
    result = check_p1_department_summary(records, pages)
    assert not result.passed               # partial fixture: 2031A holds only 7 of its lines
    assert any(d.startswith("2031A:") for d in result.details)
    assert not any("no row" in d for d in result.details)   # every account and the grand total found


def test_expected_totals_config_matches_full_release_total():
    [grand] = load_expected_totals("PB2027", "P-1")
    assert grand.amounts["FY 2027 Total Amount"] == 413095785
    assert check_expected_totals([], [grand]).passed is False
