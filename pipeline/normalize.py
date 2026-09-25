"""Turn raw string rows into typed line-item records.

R-1: one Excel row is one line item.
P-1: a line item (account, budget activity, line number -> one BLI) spans several Excel rows,
one per cost type: 'Weapon System Cost', 'Less: Advance Procurement (PY)', 'Advance
Procurement (CY)', and 'Non-Add' memo rows such as 'C (FY 2026 for FY 2027) (M)'. Every row
is kept as a cost element; the line's amounts are the sums of its 'Add' rows, which is the
net the P-1 prints for the line.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .config import Account, ExhibitColumns
from .parse_xlsx import RawRow

PE_RE = re.compile(r"^\d{7}[A-Z0-9]{1,3}$")
BLI_RE = re.compile(r"^[A-Z0-9&]{1,12}$")   # includes Chem Demil's 'O&M' and 'RDT&E'
CLASSIFIED_PE = "9999999999"      # R-1 PE and P-1 BLI of the 'Classified Programs' lines
AMOUNT_RE = re.compile(r"^-?\d+$")
WEAPON_SYSTEM_COST = "A"


class NormalizeError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = problems
        shown = "\n  ".join(problems[:25])
        more = f"\n  ... and {len(problems) - 25} more" if len(problems) > 25 else ""
        super().__init__(f"{len(problems)} row problem(s):\n  {shown}{more}")


@dataclass(frozen=True)
class Amount:
    funds_fiscal_year: int
    amount_type: str
    funding_category: str
    amount_thousands: int
    source_column: str
    quantity: int | None = None


@dataclass
class CostElement:
    """One P-1 Excel row: a cost type within a line."""

    source_row_number: int
    cost_type: str
    cost_type_title: str
    is_add: bool
    amounts: list[Amount] = field(default_factory=list)

    def amount(self, header: str) -> int | None:
        return next((a.amount_thousands for a in self.amounts if a.source_column == header), None)

    def quantity(self, header: str) -> int | None:
        return next((a.quantity for a in self.amounts if a.source_column == header), None)


@dataclass
class LineItemRecord:
    source_row_number: int
    exhibit_family: str               # 'RDTE' | 'PROC'
    appropriation_account: str
    appropriation_title: str
    service_branch: str
    organization: str | None
    budget_activity: str
    budget_activity_title: str
    line_number: str
    program_title: str
    include_in_toa: bool
    classification: str
    program_element: str | None = None        # R-1
    line_item_number: str | None = None       # P-1 BLI
    budget_subactivity: str | None = None     # P-1 BSA
    budget_subactivity_title: str | None = None
    amounts: list[Amount] = field(default_factory=list)
    cost_elements: list[CostElement] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str, str]:
        """Unique within one release: (account, budget activity, line number)."""
        return (self.appropriation_account, self.budget_activity, self.line_number)

    @property
    def is_classified(self) -> bool:
        return CLASSIFIED_PE in (self.program_element, self.line_item_number)

    @property
    def program_key(self) -> str:
        """Stable identity across releases. P-1 BLI numbers repeat across accounts ('0145' is
        both F/A-18E/F and General Purpose Bombs), so procurement keys include the account."""
        if self.is_classified:
            return f"{CLASSIFIED_PE}:{self.appropriation_account}"
        if self.exhibit_family == "PROC":
            return f"{self.appropriation_account}:{self.line_item_number}"
        return self.program_element

    def amount(self, header: str) -> int | None:
        """Amount for an Excel column header; None when the cell was blank."""
        return next((a.amount_thousands for a in self.amounts if a.source_column == header), None)

    def quantity(self, header: str) -> int | None:
        return next((a.quantity for a in self.amounts if a.source_column == header), None)


def normalize_pe(value: str) -> str:
    return re.sub(r"[\s\-]", "", value).upper()


def _collapse(value: str) -> str:
    return " ".join(value.split())


def _int(text: str, where: str, header: str, problems: list[str]) -> int | None:
    text = text.replace(",", "")
    if text == "":
        return None
    if not AMOUNT_RE.match(text):
        problems.append(f"{where}: {header} value {text!r} is not a whole number")
        return None
    return int(text)


def _amounts(row: RawRow, cols: ExhibitColumns, where: str, problems: list[str]) -> list[Amount]:
    out = []
    for col in cols.amounts:
        value = _int(row.amounts[col.header], where, col.header, problems)
        qty = _int(row.amounts[col.quantity_header], where, col.quantity_header, problems) if col.quantity_header else None
        if value is None and not qty:
            continue
        out.append(Amount(col.funds_fiscal_year, col.amount_type, col.funding_category, value or 0, col.header,
                          qty or None))
    return out


def _common(row: RawRow, cols: ExhibitColumns, accounts: dict[str, Account], exhibit: str,
            problems: list[str]) -> dict | None:
    f = {**cols.defaults, **{k: v for k, v in row.fields.items() if v != "" or k not in cols.defaults}}
    where = f"row {row.row_number}"
    before = len(problems)

    account_code = f["appropriation_account"].upper()
    account = accounts.get(account_code)
    if account is None:
        problems.append(f"{where}: account {account_code!r} is not in config/appropriation_accounts.yaml")

    if "include_in_toa" in f:
        toa = f["include_in_toa"].upper()
        if toa not in ("Y", "N"):
            problems.append(f"{where}: Include In TOA {f['include_in_toa']!r} is not Y or N")
        include = toa == "Y"
    else:
        include = account.in_title(exhibit) if account else True

    for name in ("budget_activity", "line_number", "program_title", "classification"):
        if not f.get(name):
            problems.append(f"{where}: {name} is blank")
    if len(problems) > before:
        return None
    return {
        "source_row_number": row.row_number,
        "appropriation_account": account_code,
        "appropriation_title": _collapse(f["appropriation_title"]),
        "service_branch": account.service_branch,
        "organization": f.get("organization") or None,
        "budget_activity": f["budget_activity"],
        "budget_activity_title": _collapse(f["budget_activity_title"]),
        "line_number": f["line_number"],
        "program_title": _collapse(f["program_title"]),
        "include_in_toa": include,
        "classification": f["classification"],
    }


def normalize_rows(
    rows: list[RawRow], cols: ExhibitColumns, accounts: dict[str, Account], exhibit: str = "R-1"
) -> list[LineItemRecord]:
    """R-1: one record per row."""
    problems: list[str] = []
    records: list[LineItemRecord] = []
    for row in rows:
        before = len(problems)
        base = _common(row, cols, accounts, exhibit, problems)
        pe = normalize_pe(row.fields["program_element"])
        if not (PE_RE.match(pe) or pe == CLASSIFIED_PE):
            problems.append(f"row {row.row_number}: PE {row.fields['program_element']!r} is not a valid program element")
        amounts = _amounts(row, cols, f"row {row.row_number}", problems)
        if base is None or len(problems) > before:
            continue
        records.append(LineItemRecord(exhibit_family="RDTE", program_element=pe, amounts=amounts, **base))
    if problems:
        raise NormalizeError(problems)
    return records


def normalize_p1_rows(
    rows: list[RawRow], cols: ExhibitColumns, accounts: dict[str, Account], exhibit: str = "P-1"
) -> list[LineItemRecord]:
    """P-1: group cost-type rows into one record per line."""
    problems: list[str] = []
    groups: dict[tuple, list[tuple[RawRow, dict]]] = defaultdict(list)
    for row in rows:
        base = _common(row, cols, accounts, exhibit, problems)
        if base is None:
            continue
        groups[(base["appropriation_account"], base["budget_activity"], base["line_number"])].append((row, base))

    records = []
    for key, members in groups.items():
        first_row, base = members[0]
        blis = {"".join(r.fields["line_item_number"].split()).upper() for r, _ in members}
        if len(blis) != 1:
            problems.append(f"line {key}: rows carry different BLIs {sorted(blis)}")
            continue
        bli = blis.pop()
        if not BLI_RE.match(bli):
            problems.append(f"row {first_row.row_number}: BLI {bli!r} is not a valid budget line item")
            continue

        elements = []
        for r, _ in members:
            flag = r.fields["add_non_add"].strip().lower()
            if flag not in ("add", "non-add"):
                problems.append(f"row {r.row_number}: Add/Non-Add {r.fields['add_non_add']!r} is not Add or Non-Add")
                continue
            elements.append(CostElement(
                source_row_number=r.row_number,
                cost_type=r.fields["cost_type"],
                cost_type_title=_collapse(r.fields["cost_type_title"]),
                is_add=flag == "add",
                amounts=_amounts(r, cols, f"row {r.row_number}", problems),
            ))

        # line amounts: per column, the sum over Add rows that have a value
        line_amounts = []
        for col in cols.amounts:
            parts = [a for e in elements if e.is_add for a in e.amounts if a.source_column == col.header]
            if not parts:
                continue
            qtys = [a.quantity for a in parts if a.quantity is not None]
            line_amounts.append(Amount(col.funds_fiscal_year, col.amount_type, col.funding_category,
                                       sum(a.amount_thousands for a in parts), col.header,
                                       sum(qtys) if qtys else None))

        records.append(LineItemRecord(
            exhibit_family="PROC",
            line_item_number=bli,
            budget_subactivity=first_row.fields.get("budget_subactivity") or None,
            budget_subactivity_title=_collapse(first_row.fields.get("budget_subactivity_title", "")) or None,
            amounts=line_amounts,
            cost_elements=elements,
            **base,
        ))
    if problems:
        raise NormalizeError(problems)
    return records
