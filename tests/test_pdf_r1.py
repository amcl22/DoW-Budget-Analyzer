from pipeline.pdf_r1 import PdfPage, assign_section_ids


def test_page_header(pages):
    army = pages[2]  # physical page 10
    assert army.section == "Department of the Army"
    assert army.account == "2040A"
    assert army.account_title == "Research, Development, Test and Evaluation, Army"
    assert army.printed_label == "Page 5"


def test_line_row_amounts_land_in_the_right_columns(pages):
    row = next(r for r in pages[2].rows if r.line_number == "1")
    assert (row.pe, row.budget_activity, row.label) == ("0601102A", "01", "Defense Research Sciences")
    assert row.amounts == {
        "FY 2025 Total": 290464,
        "FY 2026 Discretionary Enacted": 258178,
        "FY 2026 PL 119-21 Spend Plan": 1000,
        "FY 2026 Total": 259178,
        "FY 2027 Discretionary Request": 215322,
        "FY 2027 Total": 215322,
    }


def test_wrapped_titles_are_joined(pages):
    row = next(r for r in pages[2].rows if r.line_number == "3")
    assert row.label == "University and Industry Research Centers"


def test_budget_activity_subtotal(pages):
    sub = next(r for r in pages[2].rows if r.kind == "subtotal")
    assert sub.label == "Basic research"
    assert sub.amounts["FY 2027 Total"] == 396053


def test_classified_line_prints_nine_digit_pe(pages):
    row = next(r for p in pages for r in p.rows if r.line_number == "999")
    assert row.pe == "999999999"


def test_department_summary_rows(pages):
    summary = pages[0]
    assert summary.account is None and summary.section == "Department of War"
    army = next(r for r in summary.rows if r.label == "Research, Development, Test and Evaluation, Army")
    assert army.kind == "summary"
    assert army.amounts["FY 2027 Mandatory Request"] == 40000
    assert army.amounts["FY 2027 Total"] == 18748826


def test_footnoted_column_header_is_recognized(pages):
    # physical page 7 labels its column 'FY 2026 Total*'
    total = next(r for r in pages[1].rows if r.label == "Total Research, Development, Test, & Evaluation")
    assert total.amounts["FY 2026 Total"] == 210385030


def test_amounts_wider_than_their_header_are_assigned_correctly(pages):
    # '55,736,724 100,507,225' share one text line on the summary page
    dw = next(r for r in pages[0].rows if r.label == "Research, Development, Test and Evaluation, Defense-Wide")
    assert dw.amounts["FY 2027 Discretionary Request"] == 55736724
    assert dw.amounts["FY 2027 Mandatory Request"] == 100507225


def test_sections_are_runs_of_pages_not_header_text():
    def page(n, section, account):
        return PdfPage(n, section, account, None, None)

    pages = [
        page(1, "Defense-Wide", "0400D"),
        page(2, "Defense-Wide", "0400D"),
        page(3, "Missile Defense Agency", "0400D"),
        page(4, "Defense-Wide", "0400D"),  # the DEFW agency detail reuses the header text
    ]
    assign_section_ids(pages)
    assert pages[0].section_id == pages[1].section_id
    assert len({p.section_id for p in pages}) == 3
    assert pages[3].section_id != pages[0].section_id
