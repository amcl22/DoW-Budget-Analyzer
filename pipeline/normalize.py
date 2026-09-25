"""Turn raw string rows into typed line-item records."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import Account, ExhibitColumns
from .parse_r1 import RawRow

PE_RE = re.compile(r"^\d{7}[A-Z0-9]{1,3}$")
CLASSIFIED_PE = "9999999999"
AMOUNT_RE = re.compile(r"^-?\d+$")


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


@dataclass
class LineItemRecord:
    source_row_number: int
    appropriation_account: str
    appropriation_title: str
    service_branch: str
    organization: str | None
    budget_activity: str
    budget_activity_title: str
    line_number: str
    program_element: str
    program_title: str
    include_in_toa: bool
    classification: str
    amounts: list[Amount] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str, str]:
        """Unique within one release: (account, budget activity, line number)."""
        return (self.appropriation_account, self.budget_activity, self.line_number)

    @property
    def is_classified(self) -> bool:
        return self.program_element == CLASSIFIED_PE

    @property
    def program_key(self) -> str:
        if self.is_classified:
            return f"{CLASSIFIED_PE}:{self.appropriation_account}"
        return self.program_element

    def amount(self, header: str) -> int | None:
        """Amount for an Excel column header; None when the cell was blank."""
        for a in self.amounts:
            if a.source_column == header:
                return a.amount_thousands
        return None


def normalize_pe(value: str) -> str:
    return re.sub(r"[\s\-]", "", value).upper()


def _collapse(value: str) -> str:
    return " ".join(value.split())


def normalize_rows(
    rows: list[RawRow], cols: ExhibitColumns, accounts: dict[str, Account]
) -> list[LineItemRecord]:
    problems: list[str] = []
    records: list[LineItemRecord] = []

    for row in rows:
        f = row.fields
        where = f"row {row.row_number}"
        row_problems: list[str] = []

        account_code = f["appropriation_account"].upper()
        account = accounts.get(account_code)
        if account is None:
            row_problems.append(
                f"{where}: account {account_code!r} is not in config/appropriation_accounts.yaml"
            )

        pe = normalize_pe(f["program_element"])
        if not (PE_RE.match(pe) or pe == CLASSIFIED_PE):
            row_problems.append(f"{where}: PE {f['program_element']!r} is not a valid program element")

        toa = f["include_in_toa"].upper()
        if toa not in ("Y", "N"):
            row_problems.append(f"{where}: Include In TOA {f['include_in_toa']!r} is not Y or N")

        for name in ("budget_activity", "line_number", "program_title", "classification"):
            if not f[name]:
                row_problems.append(f"{where}: {name} is blank")

        amounts = []
        for col in cols.amounts:
            text = row.amounts[col.header].replace(",", "")
            if text == "":
                continue
            if not AMOUNT_RE.match(text):
                row_problems.append(f"{where}: {col.header} value {row.amounts[col.header]!r} is not a whole number")
                continue
            amounts.append(
                Amount(col.funds_fiscal_year, col.amount_type, col.funding_category, int(text), col.header)
            )

        if row_problems:
            problems.extend(row_problems)
            continue

        records.append(
            LineItemRecord(
                source_row_number=row.row_number,
                appropriation_account=account_code,
                appropriation_title=_collapse(f["appropriation_title"]),
                service_branch=account.service_branch,
                organization=f["organization"] or None,
                budget_activity=f["budget_activity"],
                budget_activity_title=_collapse(f["budget_activity_title"]),
                line_number=f["line_number"],
                program_element=pe,
                program_title=_collapse(f["program_title"]),
                include_in_toa=toa == "Y",
                classification=f["classification"],
                amounts=amounts,
            )
        )

    if problems:
        raise NormalizeError(problems)
    return records
