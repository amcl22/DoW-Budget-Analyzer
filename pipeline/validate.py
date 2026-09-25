"""Validation checks run before a run can be published.

Hard checks block publishing; soft checks are reported only. The Excel has no subtotal rows,
so the independent totals come from the PDF (department summary, appropriation totals and
budget-activity subtotals) and from hand-entered expected totals in config/.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

from .config import Account, ExhibitColumns, ExpectedTotal, normalize_header
from .link_pages import LinkResult
from .normalize import LineItemRecord
from .pdf_r1 import PdfPage, PdfRow

MIN_AMOUNT_VERIFIED = 0.99
MAX_DETAILS = 50
DEPARTMENT_SECTIONS = ("Department of War", "Department of Defense")  # renamed in 2025


@dataclass
class CheckResult:
    name: str
    hard: bool
    passed: bool
    summary: str
    details: list[str] = field(default_factory=list)


def _sum(records: list[LineItemRecord], headers: list[str]) -> dict[str, int]:
    return {h: sum(r.amount(h) or 0 for r in records) for h in headers}


def _diff(expected: dict[str, int], actual: dict[str, int], headers: list[str]) -> list[str]:
    return [
        f"{h}: pdf {expected.get(h, 0):,} vs excel {actual.get(h, 0):,}"
        for h in headers
        if expected.get(h, 0) != actual.get(h, 0)
    ]


def _check(name: str, hard: bool, problems: list[str], ok_summary: str, compared: int | None = None) -> CheckResult:
    if problems:
        summary = f"{len(problems)} problem(s)" + (f" in {compared} comparisons" if compared else "")
    else:
        summary = ok_summary
    return CheckResult(name, hard, not problems, summary, problems[:MAX_DETAILS])


def check_unique_keys(records: list[LineItemRecord]) -> CheckResult:
    dupes = [f"{k} appears {n} times" for k, n in Counter(r.key for r in records).items() if n > 1]
    return _check("unique_keys", True, dupes, f"{len(records)} unique (account, BA, line) keys")


def check_row_arithmetic(records: list[LineItemRecord]) -> CheckResult:
    """Within each fiscal year, the component columns add up to the total column."""
    problems = []
    for r in records:
        by_fy: dict[int, dict[str, int]] = defaultdict(dict)
        for a in r.amounts:
            by_fy[a.funds_fiscal_year][a.funding_category] = a.amount_thousands
        for fy, cats in by_fy.items():
            if set(cats) <= {"total"}:
                continue  # a year reported as a single column has nothing to add up
            parts = sum(v for c, v in cats.items() if c != "total")
            if cats.get("total", 0) != parts:
                problems.append(f"row {r.source_row_number} FY{fy}: components {parts:,} != total {cats.get('total', 0):,}")
    return _check("row_arithmetic", True, problems, "components = total on every row, every year")


def check_negative_amounts(records: list[LineItemRecord]) -> CheckResult:
    problems = [
        f"row {r.source_row_number} {a.source_column}: {a.amount_thousands:,}"
        for r in records for a in r.amounts if a.amount_thousands < 0
    ]
    # Negative amounts are legitimate in some releases (CR adjustments, advance-procurement
    # credits), so this is reported, not blocking.
    return _check("negative_amounts", False, problems, "no negative amounts")


R1_GRAND_TOTALS = (
    ("Total Research, Development, Test, & Evaluation", True),
    ("Total Not in Research, Development, Test, & Evaluation", False),
)


def check_department_summary(
    records: list[LineItemRecord], pages: list[PdfPage], accounts: dict[str, Account], headers: list[str]
) -> CheckResult:
    """Each account's sum, and the two grand totals, vs the PDF's department summary rows."""
    rows: dict[str, PdfRow] = {}
    for page in pages:
        if page.account is None and page.section in DEPARTMENT_SECTIONS:
            for row in page.rows:
                rows.setdefault(normalize_header(row.label), row)  # first occurrence = component summary
    if not rows:
        return CheckResult("department_summary", True, False, "no department summary page found in the PDF")

    problems, compared = [], 0
    by_account = defaultdict(list)
    for r in records:
        by_account[r.appropriation_account].append(r)
    for code, recs in sorted(by_account.items()):
        # match on the title this release uses (titles change, e.g. 0130D Defense Health Program)
        titles = {recs[0].appropriation_title, *accounts[code].titles}
        row = next((rows[normalize_header(t)] for t in titles if normalize_header(t) in rows), None)
        if row is None:
            problems.append(f"{code} {recs[0].appropriation_title!r}: no row on the department summary page")
            continue
        compared += 1
        problems += [f"{code}: {d}" for d in _diff(row.amounts, _sum(recs, headers), headers)]

    for label, in_title in R1_GRAND_TOTALS:
        row = rows.get(normalize_header(label))
        recs = [r for r in records if accounts[r.appropriation_account].in_title("R-1") == in_title]
        if row is None:
            problems.append(f"{label!r}: no row on the department summary page")
            continue
        compared += 1
        problems += [f"{label}: {d}" for d in _diff(row.amounts, _sum(recs, headers), headers)]
    return _check(
        "department_summary", True, problems,
        f"{compared} appropriation/grand totals match across {len(headers)} columns", compared,
    )


def check_section_subtotals(
    records: list[LineItemRecord], pages: list[PdfPage], links: LinkResult, headers: list[str]
) -> CheckResult:
    """BA subtotals and appropriation/agency totals on detail pages vs the Excel lines
    linked to that same section of the PDF."""
    in_section: dict[tuple[str, str | None], list[LineItemRecord]] = defaultdict(list)
    for r in records:
        for sec in {ref.section_id for ref in links.refs.get(r.key, [])}:
            in_section[(r.appropriation_account, sec)].append(r)

    problems, compared = [], 0
    for page in pages:
        if page.account is None:
            continue
        recs = in_section.get((page.account, page.section_id), [])
        for row in page.rows:
            if row.kind == "subtotal":
                subset = [r for r in recs if r.budget_activity == row.budget_activity]
                if not subset:
                    problems.append(
                        f"page {page.page_number}: subtotal {row.label!r} (BA {row.budget_activity}) "
                        f"has no lines in {page.account} [{page.section_id}]"
                    )
                    continue
            elif row.kind == "total":
                subset = recs
            else:
                continue
            compared += 1
            where = f"page {page.page_number} {page.account} [{page.section_id}] {row.label!r}"
            problems += [f"{where}: {d}" for d in _diff(row.amounts, _sum(subset, headers), headers)]
    return _check(
        "section_subtotals", True, problems,
        f"{compared} BA subtotals and section totals match", compared,
    )


def check_expected_totals(
    records: list[LineItemRecord], expected: list[ExpectedTotal]
) -> CheckResult:
    if not expected:
        return CheckResult("expected_totals", True, False, "no expected totals configured for this cycle")
    problems = []
    for e in expected:
        recs = [r for r in records if r.include_in_toa == e.include_in_toa]
        hs = list(e.amounts)
        problems += [f"{e.label}: {d.replace('pdf', 'expected')}" for d in _diff(e.amounts, _sum(recs, hs), hs)]
    return _check("expected_totals", True, problems, f"{len(expected)} hand-entered totals match")


def check_page_coverage(records: list[LineItemRecord], links: LinkResult) -> CheckResult:
    problems = [
        f"row {r.source_row_number} {r.key} {r.program_element} {r.program_title!r}: no PDF page"
        for r in records if not links.refs.get(r.key)
    ]
    return _check("page_coverage", True, problems, f"all {len(records)} line items linked to a PDF page")


def check_amounts_verified(records: list[LineItemRecord], links: LinkResult) -> CheckResult:
    verified, problems = 0, []
    for r in records:
        refs = links.refs.get(r.key, [])
        if refs and all(ref.amount_verified for ref in refs):
            verified += 1
        for ref in refs:
            if not ref.amount_verified:
                problems.append(f"{r.key} page {ref.page_number}: {'; '.join(ref.mismatches)}")
    share = verified / len(records) if records else 0.0
    passed = share >= MIN_AMOUNT_VERIFIED
    summary = f"{verified}/{len(records)} line items ({share:.2%}) match the PDF on every amount"
    return CheckResult("amounts_verified", True, passed, summary, problems[:MAX_DETAILS])


def check_pe_consistency(links: LinkResult) -> CheckResult:
    return _check("pe_consistency", False, links.pe_mismatches, "PDF and Excel agree on every PE")


def check_unmatched_pdf_rows(links: LinkResult) -> CheckResult:
    return _check("unmatched_pdf_rows", False, links.unmatched_pdf_rows, "every PDF line row matched an Excel line")


def check_unlabeled_pdf_rows(pages: list[PdfPage]) -> CheckResult:
    problems = [
        f"page {p.page_number}: amounts {r.amounts} with no label" for p in pages for r in p.rows if r.kind == "unlabeled"
    ]
    return _check("unlabeled_pdf_rows", False, problems, "every PDF amount row has a label")


def check_account_config(records: list[LineItemRecord], accounts: dict[str, Account], exhibit: str = "R-1") -> CheckResult:
    problems = set()
    for r in records:
        a = accounts[r.appropriation_account]
        if normalize_header(r.appropriation_title) not in {normalize_header(t) for t in a.titles}:
            problems.add(f"{a.code}: Excel title {r.appropriation_title!r} is not a known title {a.titles}")
        if r.include_in_toa != a.in_title(exhibit):
            problems.add(f"{a.code}: Include In TOA={'Y' if r.include_in_toa else 'N'} but config says "
                         f"{'inside' if a.in_title(exhibit) else 'outside'} the {exhibit} title")
    return _check("account_config", False, sorted(problems), "account titles and TOA flags agree with config")


# ---------------------------------------------------------------------------------------------
# P-1


def check_p1_section_totals(records: list[LineItemRecord], pages: list) -> CheckResult:
    """BA subtotals and appropriation totals on P-1 detail pages vs Excel line sums, over the
    columns printed on that page (wide tables split columns across page pairs)."""
    by_account: dict[str, list] = defaultdict(list)
    for r in records:
        by_account[r.appropriation_account].append(r)
    problems, compared = [], 0
    for page in pages:
        if page.kind != "detail" or not page.account:
            continue
        recs = by_account.get(page.account, [])
        for row in page.rows:
            if row.role == "subtotal":
                subset = [r for r in recs if r.budget_activity == row.budget_activity]
            elif row.role == "total":
                subset = recs
            else:
                continue
            compared += 1
            where = f"page {page.page_number} {page.account} {row.label!r}"
            problems += [f"{where}: {d}" for d in _diff(row.amounts, _sum(subset, page.headers), page.headers)]
    return _check("section_subtotals", True, problems, f"{compared} BA subtotals and appropriation totals match", compared)


P1_GRAND_TOTAL_LABELS = ("Grand Total Department of War", "Grand Total Department of Defense")


def check_p1_department_summary(records: list[LineItemRecord], pages: list) -> CheckResult:
    """Account rows and the grand total on the 'Component Summary' pages, merged across the page
    pairs that split the columns."""
    merged: dict[str, tuple[dict, set]] = {}
    for page in pages:
        if page.kind == "summary" and page.title and "Component Summary" in page.title:
            for row in page.rows:
                amounts, headers = merged.setdefault(normalize_header(row.label), ({}, set()))
                amounts.update(row.amounts)
                headers.update(page.headers)
    if not merged:
        return CheckResult("department_summary", True, False, "no Component Summary page found in the PDF")
    problems, compared = [], 0
    by_account: dict[str, list] = defaultdict(list)
    for r in records:
        by_account[r.appropriation_account].append(r)
    for code, recs in sorted(by_account.items()):
        found = merged.get(normalize_header(recs[0].appropriation_title))
        if found is None:
            problems.append(f"{code} {recs[0].appropriation_title!r}: no row on the Component Summary")
            continue
        compared += 1
        amounts, headers = found
        problems += [f"{code}: {d}" for d in _diff(amounts, _sum(recs, sorted(headers)), sorted(headers))]
    grand = next((merged[normalize_header(l)] for l in P1_GRAND_TOTAL_LABELS if normalize_header(l) in merged), None)
    if grand is None:
        problems.append("no 'Grand Total Department of ...' row on the Component Summary")
    else:
        compared += 1
        amounts, headers = grand
        included = [r for r in records if r.include_in_toa]
        problems += [f"Grand Total: {d}" for d in _diff(amounts, _sum(included, sorted(headers)), sorted(headers))]
    return _check("department_summary", True, problems, f"{compared} appropriation and grand totals match", compared)


def run_checks(
    records: list[LineItemRecord],
    pages: list,
    links: LinkResult,
    cols: ExhibitColumns,
    accounts: dict[str, Account],
    expected: list[ExpectedTotal],
    exhibit: str = "R-1",
) -> list[CheckResult]:
    headers = list(cols.pdf_columns.values())
    common_head = [
        check_unique_keys(records),
        check_row_arithmetic(records),
        check_negative_amounts(records),
    ]
    common_tail = [
        check_expected_totals(records, expected),
        check_page_coverage(records, links),
        check_amounts_verified(records, links),
        check_unmatched_pdf_rows(links),
        check_account_config(records, accounts, exhibit),
    ]
    if exhibit == "P-1":
        return common_head + [
            check_p1_department_summary(records, pages),
            check_p1_section_totals(records, pages),
        ] + common_tail
    return common_head + [
        check_department_summary(records, pages, accounts, headers),
        check_section_subtotals(records, pages, links, headers),
    ] + common_tail + [
        check_pe_consistency(links),
        check_unlabeled_pdf_rows(pages),
    ]


def build_report(checks: list[CheckResult], stats: dict) -> dict:
    return {
        "passed": all(c.passed for c in checks if c.hard),
        "stats": stats,
        "checks": [asdict(c) for c in checks],
    }
