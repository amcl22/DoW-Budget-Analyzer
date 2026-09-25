"""Match Excel line items to the PDF pages they appear on, and verify every amount."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace

from .config import ExhibitColumns
from .normalize import CLASSIFIED_PE, LineItemRecord, normalize_pe
from .pdf_r1 import PdfPage, PdfRow

PDF_CLASSIFIED_PE = "999999999"  # the PDF prints the classified sentinel with 9 digits


@dataclass(frozen=True)
class PageRef:
    page_number: int
    printed_label: str | None
    section: str | None
    section_id: str | None
    amount_verified: bool
    mismatches: tuple[str, ...]
    is_primary: bool = False
    document_url: str | None = None   # None: the exhibit's summary PDF; else a justification book


@dataclass
class LinkResult:
    refs: dict[tuple[str, str, str], list[PageRef]] = field(default_factory=dict)
    pe_mismatches: list[str] = field(default_factory=list)   # key matched, PE did not
    unmatched_pdf_rows: list[str] = field(default_factory=list)


def _pdf_pe(row: PdfRow) -> str:
    pe = normalize_pe(row.pe or "")
    return CLASSIFIED_PE if pe == PDF_CLASSIFIED_PE else pe


def compare_amounts(record: LineItemRecord, row: PdfRow, headers: list[str]) -> list[str]:
    """Differences between the Excel record and the PDF row, over the PDF's columns."""
    out = []
    for h in headers:
        excel = record.amount(h) or 0
        pdf = row.amounts.get(h, 0)
        if excel != pdf:
            out.append(f"{h}: excel {excel:,} vs pdf {pdf:,}")
    return out


def link_line_items(
    records: list[LineItemRecord], pages: list[PdfPage], cols: ExhibitColumns
) -> LinkResult:
    headers = list(cols.pdf_columns.values())
    index: dict[tuple[str, str, str], list[tuple[PdfPage, PdfRow]]] = defaultdict(list)
    section_size: Counter = Counter()
    for page in pages:
        if page.account is None:
            continue
        for row in page.rows:
            if row.kind == "line":
                index[(page.account, row.budget_activity, row.line_number)].append((page, row))
                section_size[(page.account, page.section_id)] += 1

    result = LinkResult()
    used: set[tuple[int, float]] = set()
    for rec in records:
        refs = []
        for page, row in index.get(rec.key, []):
            if _pdf_pe(row) != rec.program_element:
                result.pe_mismatches.append(
                    f"{rec.key}: excel PE {rec.program_element} vs pdf PE {row.pe} on page {page.page_number}"
                )
                continue
            used.add((page.page_number, row.y))
            mismatches = tuple(compare_amounts(rec, row, headers))
            refs.append(
                PageRef(page.page_number, page.printed_label, page.section, page.section_id,
                        not mismatches, mismatches)
            )
        if refs:
            # Prefer the most specific section (e.g. the DARPA detail over the combined
            # Defense-Wide detail): the one with the fewest lines for this account.
            best = min(
                range(len(refs)),
                key=lambda i: (section_size[(rec.appropriation_account, refs[i].section_id)], refs[i].page_number),
            )
            refs[best] = replace(refs[best], is_primary=True)
        result.refs[rec.key] = refs

    for page in pages:
        for row in page.rows:
            if row.kind == "line" and page.account and (page.page_number, row.y) not in used:
                result.unmatched_pdf_rows.append(
                    f"page {page.page_number}: {page.account} BA {row.budget_activity} line {row.line_number} {row.pe}"
                )
    return result


# ---------------------------------------------------------------------------------------------
# P-1


@dataclass
class _P1Block:
    pages: list = field(default_factory=list)            # P1Page, in page order
    headers: set = field(default_factory=set)             # Excel headers printed across the pages
    qty_headers: set = field(default_factory=set)         # ... of those, with a Qty column
    line: dict = field(default_factory=dict)              # line-row amounts
    line_qty: dict = field(default_factory=dict)
    components: dict = field(default_factory=dict)        # normalized label -> amounts
    before_net: set = field(default_factory=set)          # labels printed above the net row
    net: dict | None = None
    conflicts: list = field(default_factory=list)


def _merge(target: dict, values: dict, what: str, conflicts: list) -> None:
    for h, v in values.items():
        if h in target and target[h] != v:
            conflicts.append(f"{what} {h}: {target[h]:,} and {v:,} on different pages")
        target[h] = v


def _diff(label: str, expected: dict, actual: dict, headers) -> list[str]:
    return [
        f"{label} {h}: excel {expected.get(h, 0):,} vs pdf {actual.get(h, 0):,}"
        for h in sorted(headers)
        if expected.get(h, 0) != actual.get(h, 0)
    ]


def _match_title(label: str, titles) -> str | None:
    """Exact normalized match, else the unique title a wrapped/truncated label is a prefix of."""
    if label in titles:
        return label
    hits = [t for t in titles if len(label) >= 10 and t.startswith(label)]
    return hits[0] if len(hits) == 1 else None


def _element_titles(rec: LineItemRecord) -> dict[str, list]:
    from .pdf_p1 import _norm_title

    out: dict[str, list] = {}
    for e in rec.cost_elements:
        if e.cost_type != "A":
            out.setdefault(_norm_title(e.cost_type_title), []).append(e)
    return out


def compare_p1_block(rec: LineItemRecord, b: _P1Block) -> list[str]:
    """Differences between a P-1 line's Excel cost elements and its printed block.

    The line row is the gross 'Weapon System Cost' (cost type A) when other rows follow, or the
    whole net when it stands alone. Each labeled row is the Excel cost element with that title.
    A printed net row is a running subtotal: gross plus the rows printed above it (shipbuilding
    blocks list 'Subsequent Full Funding' / 'Completion PY' rows after it)."""
    headers = b.headers
    problems = list(b.conflicts)
    if not b.components and b.net is None:
        problems += _diff("line", {h: rec.amount(h) or 0 for h in headers}, b.line, headers)
        problems += _diff("qty", {h: rec.quantity(h) or 0 for h in b.qty_headers}, b.line_qty, b.qty_headers)
        return problems

    weapon = [e for e in rec.cost_elements if e.cost_type == "A"]
    gross = {h: sum(e.amount(h) or 0 for e in weapon) for h in headers}
    problems += _diff("gross", gross, b.line, headers)
    problems += _diff("qty", {h: sum(e.quantity(h) or 0 for e in weapon) for h in b.qty_headers},
                      b.line_qty, b.qty_headers)
    by_title = _element_titles(rec)
    running = dict(gross)
    for label, amounts in b.components.items():
        title = _match_title(label, by_title)
        if title is None:
            problems.append(f"pdf row {label!r} has no Excel cost element")
            continue
        elems = by_title.pop(title)
        expected = {h: sum(e.amount(h) or 0 for e in elems) for h in headers}
        problems += _diff(label, expected, amounts, headers)
        if label in b.before_net:
            running = {h: running[h] + expected[h] for h in headers}
    for title, elems in by_title.items():
        # an unprinted Add row changes the line's money; unprinted memo (Non-Add) rows do not
        if any(e.is_add and e.amount(h) for e in elems for h in headers):
            problems.append(f"excel cost element {title!r} has amounts but no PDF row")
    if b.net is not None:
        # FY2024 prints the net only for some columns (the budget year), so compare the columns
        # it prints; every column is still covered by the row-by-row checks above
        problems += _diff("net", running, b.net, set(b.net) & headers)
    return problems


def _rehome(label: str, amounts: dict, candidates: list[LineItemRecord]) -> LineItemRecord | None:
    """The one line whose cost element has this title and exactly these amounts."""
    hits = []
    for rec in candidates:
        titles = _element_titles(rec)
        t = _match_title(label, titles)
        if t and all(sum(e.amount(h) or 0 for e in titles[t]) == v for h, v in amounts.items()):
            hits.append(rec)
    return hits[0] if len(hits) == 1 else None


def link_p1_line_items(records: list[LineItemRecord], pages: list, cols: ExhibitColumns) -> LinkResult:
    from .pdf_p1 import _norm_title

    blocks: dict[tuple, _P1Block] = defaultdict(_P1Block)
    net_seen: set = set()      # (key, column group): a block's net row can precede a page break
    line_seen: set = set()     # (key, column group)
    orphans: list = []         # (page, row) cost rows not inside a printed line block
    for page in pages:
        if page.kind != "detail":
            continue
        group = tuple(page.headers)
        for row in page.rows:
            if row.role == "component" and row.key is None:
                orphans.append((page, row))
                continue
            if row.key is None:
                continue
            b = blocks[row.key]
            if row.role == "line":
                if page not in b.pages:
                    b.pages.append(page)
                b.headers |= set(page.headers)
                b.qty_headers |= set(page.qty_headers)
                if (row.key, group) in line_seen:
                    # the PDF can split one Excel line over several rows with the same line number
                    # (FY2024 0300D classified: three '999' rows adding up to the Excel line)
                    for h, v in row.amounts.items():
                        b.line[h] = b.line.get(h, 0) + v
                    for h, v in row.quantities.items():
                        b.line_qty[h] = b.line_qty.get(h, 0) + v
                else:
                    line_seen.add((row.key, group))
                    _merge(b.line, row.amounts, "line", b.conflicts)
                    _merge(b.line_qty, row.quantities, "qty", b.conflicts)
            elif row.role == "component":
                label = _norm_title(row.label)
                _merge(b.components.setdefault(label, {}), row.amounts, row.label, b.conflicts)
                if (row.key, group) not in net_seen:
                    b.before_net.add(label)
            elif row.role == "net":
                net_seen.add((row.key, group))
                b.net = b.net or {}
                _merge(b.net, row.amounts, "net", b.conflicts)

    by_key = {r.key: r for r in records}
    by_ba: dict[tuple, list] = defaultdict(list)
    for r in records:
        by_ba[(r.appropriation_account, r.budget_activity)].append(r)

    # Cost rows that are not their own line's: FY2024 prints a line's 'Advance Procurement (CY)'
    # above it (under the previous line or a heading), and a line can lack a printed line row
    # (FY2027 1612N line 1). Re-home each to the line whose element matches title and amounts.
    for key, b in list(blocks.items()):
        rec = by_key.get(key)
        if rec is None:
            continue
        mine = _element_titles(rec)
        for label in list(b.components):
            if _match_title(label, mine) is None:
                orphans.append((b.pages[0] if b.pages else None, key, label, b.components.pop(label)))
                b.before_net.discard(label)
    for item in orphans:
        if len(item) == 2:
            page, row = item
            account, ba, label, amounts = page.account, row.budget_activity, _norm_title(row.label), row.amounts
        else:
            page, key, label, amounts = item
            account, ba = key[0], key[1]
        target = _rehome(label, amounts, by_ba.get((account, ba), []))
        if target is None:
            if len(item) == 4:   # leave it where it was, so the mismatch is reported
                blocks[item[1]].components[label] = amounts
            continue
        tb = blocks[target.key]
        if page is not None and page not in tb.pages:
            tb.pages.append(page)
            tb.headers |= set(page.headers)
            tb.qty_headers |= set(page.qty_headers)
        _merge(tb.components.setdefault(label, {}), amounts, label, tb.conflicts)

    result = LinkResult()
    for rec in records:
        b = blocks.pop(rec.key, None)
        if b is None:
            result.refs[rec.key] = []
            continue
        mismatches = tuple(compare_p1_block(rec, b))
        result.refs[rec.key] = [
            PageRef(p.page_number, p.printed_label, p.section, p.section_id, not mismatches, mismatches,
                    is_primary=(i == 0))
            for i, p in enumerate(sorted(b.pages, key=lambda p: p.page_number))
        ]
    result.unmatched_pdf_rows = [
        f"pages {[p.page_number for p in b.pages]}: {key}" for key, b in blocks.items() if key not in by_key
    ]
    return result
