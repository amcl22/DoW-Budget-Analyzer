"""R-2 justification books: section discovery, cost table, description, matching to R-1 lines."""

import pytest

from pipeline.discover import HREF_RE
from pipeline.normalize import Amount, LineItemRecord
from pipeline.r2 import _millions_to_thousands, attach_r2, parse_r2_book
from tests.conftest import FIXTURES

URL = "https://example.test/RDTE_DTRA_PB_2027.pdf"


@pytest.fixture(scope="module")
def sections():
    return parse_r2_book(FIXTURES / "r2_dtra_2027_sample.pdf", URL)


def _record(line, ba, pe, amounts):
    return LineItemRecord(
        source_row_number=1, exhibit_family="RDTE", appropriation_account="0400D",
        appropriation_title="RDT&E, Defense-Wide", service_branch="Defense-Wide", organization="DTRA",
        budget_activity=ba, budget_activity_title="", line_number=line, program_title="",
        include_in_toa=True, classification="U", program_element=pe,
        amounts=[Amount(fy, t, c, v, f"FY {fy} {c}") for fy, t, c, v in amounts],
    )


def test_sections_found_by_page_header(sections):
    assert [(s.start_page, s.pe, s.line_number, s.account_prefix, s.budget_activity) for s in sections] == [
        (1, "0601000BR", "1", "0400", "01"),
        (7, "0602718BR", "29", "0400", "02"),
    ]
    assert sections[0].printed_label == "Volume 5 - 1"


def test_cost_table_in_thousands_including_out_years(sections):
    cost = sections[0].cost
    assert cost["FY 2025"] == 14933 and cost["FY 2027 Total"] == 15070
    assert [cost[f"FY {y}"] for y in (2028, 2029, 2030, 2031)] == [15385, 15722, 16004, 16325]
    assert cost["Cost To Complete"] is None    # 'Continuing'
    assert sections[1].cost["Prior Years"] == 1502638   # printed '1,502.638'


def test_description_is_section_a_without_page_furniture(sections):
    d = sections[0].description
    assert d.startswith("The Basic Research for Countering Weapons of Mass Destruction (CWMD) project")
    assert "B. Program Change Summary" not in d and "UNCLASSIFIED" not in d


@pytest.mark.parametrize("token,value", [("15.070", 15070), ("1,502.638", 1502638), ("-0.378", -378), ("0.000", 0),
                                         ("Continuing", None), ("-", None)])
def test_millions_to_thousands(token, value):
    assert _millions_to_thousands(token) == value


def test_attach_matches_line_and_collects_out_years(sections):
    rec = _record("1", "01", "0601000BR", [(2025, "actual", "total", 14933), (2026, "enacted", "total", 15481),
                                           (2027, "request", "total", 15070)])
    result = attach_r2([rec], sections, 2027, {URL: "Defense-Wide"})
    [ref] = result.refs[rec.key]
    assert (ref.page_number, ref.document_url, ref.section, ref.amount_verified) == (1, URL, "Defense-Wide", True)
    assert [(a.funds_fiscal_year, a.amount_type, a.amount_thousands) for a in result.outyears[rec.key]] == [
        (2028, "estimate", 15385), (2029, "estimate", 15722), (2030, "estimate", 16004), (2031, "estimate", 16325),
    ]
    assert result.descriptions[rec.key].startswith("The Basic Research")
    assert result.unmatched_sections   # the second section has no R-1 line in this test


def test_r2_year_may_equal_the_discretionary_amount(sections):
    # FY2026 enacted: R-1 total includes PL 119-21 spend plan money the R-2 column leaves out
    rec = _record("1", "01", "0601000BR", [(2025, "actual", "total", 14933), (2026, "enacted", "discretionary", 15481),
                                           (2026, "enacted", "total", 16481), (2027, "request", "total", 15070)])
    assert attach_r2([rec], sections, 2027, {}).refs[rec.key][0].amount_verified


def test_amount_mismatch_and_pe_mismatch(sections):
    wrong_amount = _record("1", "01", "0601000BR", [(2025, "actual", "total", 1), (2027, "request", "total", 15070)])
    result = attach_r2([wrong_amount], sections, 2027, {})
    assert not result.refs[wrong_amount.key][0].amount_verified
    assert any("FY 2025: R-2 14,933 vs R-1 total 1" in m for m in result.amount_mismatches)

    wrong_pe = _record("1", "01", "0601000XX", [])
    result = attach_r2([wrong_pe], sections, 2027, {})
    assert not result.refs.get(wrong_pe.key)
    assert any("R-1 has PE 0601000XX" in m for m in result.unmatched_sections)


def test_discovery_href_pattern():
    html = '<a href="/Portals/45/x/03_RDT_and_E/RDTE_DTRA_PB_2027.pdf">DTRA</a> <a href=\'a.PDF\'>'
    assert HREF_RE.findall(html) == ["/Portals/45/x/03_RDT_and_E/RDTE_DTRA_PB_2027.pdf", "a.PDF"]
