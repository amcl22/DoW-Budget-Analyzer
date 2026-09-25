from dataclasses import replace

from pipeline.link_pages import link_line_items
from tests.conftest import by_key


def test_every_fixture_line_is_linked_and_verified(records, links):
    assert all(links.refs[r.key] for r in records)
    assert all(ref.amount_verified for refs in links.refs.values() for ref in refs)
    assert all(sum(ref.is_primary for ref in refs) == 1 for refs in links.refs.values())
    assert not links.pe_mismatches
    assert not links.unmatched_pdf_rows


def test_deep_link_uses_the_physical_page(records, links):
    ref = links.refs[by_key(records, "2040A", "01", "1").key][0]
    assert ref.page_number == 3            # fixture page 3 = physical page 10
    assert ref.printed_label == "Page 5"
    assert ref.section == "Department of the Army"


def test_classified_line_matches_despite_nine_digit_pdf_pe(records, links):
    assert links.refs[by_key(records, "2040A", "02", "999").key]


def test_amount_mismatch_is_caught(records, pages, cols):
    rec = by_key(records, "2040A", "01", "1")
    bad = replace(rec, amounts=[
        replace(a, amount_thousands=a.amount_thousands + 1) if a.source_column == "FY 2027 Total" else a
        for a in rec.amounts
    ])
    [ref] = link_line_items([bad], pages, cols).refs[bad.key]
    assert not ref.amount_verified
    assert ref.mismatches == ("FY 2027 Total: excel 215,323 vs pdf 215,322",)


def test_blank_in_excel_vs_value_in_pdf_is_a_mismatch(records, pages, cols):
    rec = by_key(records, "2040A", "01", "1")
    bad = replace(rec, amounts=[a for a in rec.amounts if a.source_column != "FY 2026 PL 119-21 Spend Plan"])
    [ref] = link_line_items([bad], pages, cols).refs[bad.key]
    assert not ref.amount_verified


def test_pe_mismatch_is_not_a_match(records, pages, cols):
    rec = replace(by_key(records, "2040A", "01", "1"), program_element="0601102X")
    result = link_line_items([rec], pages, cols)
    assert result.refs[rec.key] == []
    assert result.pe_mismatches
