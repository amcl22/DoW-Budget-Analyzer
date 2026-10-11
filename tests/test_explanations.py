"""Official funding-change explanations quoted from an R-2 section (a synthetic book)."""

import pymupdf

from pipeline.explanations import section_explanations

R2 = """UNCLASSIFIED
Exhibit R-2, RDT&E Budget Item Justification: PB 2027 Navy
Date: April 2026
Appropriation/Budget Activity
1319: Research, Development, Test & Evaluation, Navy / BA 4
R-1 Program Element (Number/Name)
PE 0603128N / Unmanned Aerial System
B. Program Change Summary ($ in Millions)
Change Summary Explanation
Funding: The FY 2027 request was reduced by $18.634 for higher USMC
priorities.
Schedule:
Quantities and deliveries updated.
Volume 2 - 1
PE 0603128N: Unmanned Aerial System
Navy
Page 1 of 3
"""
R2A_1 = """UNCLASSIFIED
Exhibit R-2A, RDT&E Project Justification: PB 2027 Navy
Date: April 2026
Appropriation/Budget Activity
1319 / 4
R-1 Program Element (Number/Name)
PE 0603128N / Unmanned Aerial System
Project (Number/Name)
3448 / Marine Group 5 UAS Development
B. Accomplishments/Planned Programs ($ in Millions, Article Quantities in Each)
FY 2025
FY 2026
Title: MALE Primary Hardware
FY 2027 Base Plans:
Design work.
FY 2026 to FY 2027 Increase/Decrease Statement:
An increase of $9.222 million supports integration
20.474
-
and testing.
Volume 2 - 2
Page 2 of 3
"""
R2A_2 = """UNCLASSIFIED
Exhibit R-2A, RDT&E Project Justification: PB 2027 Navy
Project (Number/Name)
3448 / Marine Group 5 UAS Development
FY 2026 to FY 2027 Increase/Decrease Statement:
N/A
Title: Test Support
FY 2026 to FY 2027 Increase/Decrease Statement: A decrease of $2.355 million as testing completes.
Accomplishments/Planned Programs Subtotals
COST ($ in Millions)
Volume 2 - 3
Page 3 of 3
"""
NEXT = """UNCLASSIFIED
Exhibit R-2, RDT&E Budget Item Justification: PB 2027 Navy
PE 0603129N: Next Program
Change Summary Explanation
Funding: Belongs to the next section.
Page 1 of 1
"""


def _book(tmp_path):
    doc = pymupdf.open()
    for body in (R2, R2A_1, R2A_2, NEXT):
        page = doc.new_page()
        y = 30
        for line in body.splitlines():
            page.insert_text((40, y), line, fontsize=7)
            y += 9
    path = tmp_path / "book.pdf"
    doc.save(path)
    return pymupdf.open(path)


def test_quotes_change_summary_and_statements_with_titles_and_pages(tmp_path):
    out = section_explanations(_book(tmp_path), 1)
    assert [(e.kind, e.page) for e in out] == [("change_summary", 1), ("increase_decrease", 2), ("increase_decrease", 3)]
    summary, first, second = out
    assert summary.body == "The FY 2027 request was reduced by $18.634 for higher USMC priorities."
    # amount columns interleaved in the text are dropped; the passage stops at the next heading
    assert first.body == "An increase of $9.222 million supports integration and testing."
    assert (first.title, first.project, first.from_fy, first.to_fy) == (
        "MALE Primary Hardware", "3448 / Marine Group 5 UAS Development", 2026, 2027)
    # 'N/A' statements are skipped; text on the same line as the label is kept
    assert second.title == "Test Support"
    assert second.body == "A decrease of $2.355 million as testing completes."


def test_stops_at_the_next_program_section(tmp_path):
    out = section_explanations(_book(tmp_path), 1)
    assert all("next section" not in e.body for e in out)
    [nxt] = section_explanations(_book(tmp_path), 4)
    assert nxt.body == "Belongs to the next section."
