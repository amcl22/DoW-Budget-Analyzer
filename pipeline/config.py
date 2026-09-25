"""Paths, settings and typed loaders for the YAML config files."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
SOURCES_DIR = ROOT / "sources"

DEFAULT_DATABASE_URL = "postgresql+psycopg://budget:budget@localhost:5432/budget"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def raw_dir() -> Path:
    return Path(os.environ.get("BUDGET_RAW_DIR", ROOT / "data" / "raw"))


def normalize_header(text: str) -> str:
    """Case- and whitespace-insensitive form used to compare column headers and labels."""
    return " ".join(str(text).split()).casefold()


def normalize_exhibit(exhibit: str) -> str:
    """'r1', 'R1', 'r-1' -> 'R-1'."""
    e = exhibit.strip().upper().replace("-", "")
    if len(e) < 2 or not e[0].isalpha() or not e[1:].isdigit():
        raise ValueError(f"Unrecognized exhibit type: {exhibit!r}")
    return f"{e[0]}-{e[1:]}"


def _load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


@dataclass(frozen=True)
class AmountColumn:
    header: str
    funds_fiscal_year: int
    amount_type: str
    funding_category: str
    quantity_header: str | None = None   # P-1: the paired 'Quantity' column


@dataclass(frozen=True)
class ExhibitColumns:
    sheet: str
    header_row_contains: list[str]
    fields: dict[str, str]          # canonical field -> Excel header
    amounts: list[AmountColumn]
    pdf_columns: dict[str, str]     # PDF column label -> Excel header, in PDF column order
    defaults: dict[str, str]        # canonical field -> value, for fields the sheet lacks

    def quantity_header(self, amount_header: str) -> str | None:
        return next((a.quantity_header for a in self.amounts if a.header == amount_header), None)


def load_column_map(exhibit: str, cycle: str) -> ExhibitColumns:
    exhibit = normalize_exhibit(exhibit)
    path = CONFIG_DIR / f"{exhibit.replace('-', '').lower()}_columns.yaml"
    data = _load_yaml(path)
    if cycle not in data:
        raise KeyError(f"{path.name} has no column map for budget cycle {cycle!r}")
    c = data[cycle]
    return ExhibitColumns(
        sheet=c["sheet"],
        header_row_contains=list(c["header_row_contains"]),
        fields=dict(c["fields"]),
        amounts=[
            AmountColumn(a["header"], int(a["fy"]), a["type"], a["category"], a.get("quantity"))
            for a in c["amounts"]
        ],
        pdf_columns=dict(c.get("pdf_columns", {})),
        defaults=dict(c.get("defaults", {})),
    )


@dataclass(frozen=True)
class Account:
    code: str
    title: str                              # current title
    service_branch: str
    outside_title: tuple[str, ...] = ()     # exhibits listing it outside their title (R-1: 'Not in RDT&E')
    former_titles: tuple[str, ...] = ()

    @property
    def titles(self) -> tuple[str, ...]:
        return (self.title, *self.former_titles)

    def in_title(self, exhibit: str) -> bool:
        return normalize_exhibit(exhibit) not in self.outside_title


def load_accounts() -> dict[str, Account]:
    data = _load_yaml(CONFIG_DIR / "appropriation_accounts.yaml")
    return {
        code: Account(
            code, a["title"], a["service_branch"],
            tuple(a.get("outside_title", ())), tuple(a.get("former_titles", ())),
        )
        for code, a in data["accounts"].items()
    }


@dataclass(frozen=True)
class SourceFile:
    role: str      # 'data' (structured xlsx) | 'summary_pdf'
    format: str    # 'xlsx' | 'pdf'
    url: str


@dataclass(frozen=True)
class SourceList:
    fiscal_year: int
    budget_cycle: str
    files: list[SourceFile]


def load_sources(fiscal_year: int, exhibit: str) -> SourceList:
    exhibit = normalize_exhibit(exhibit)
    data = _load_yaml(SOURCES_DIR / f"fy{fiscal_year}.yaml")
    files = data["exhibits"].get(exhibit)
    if not files:
        raise KeyError(f"sources/fy{fiscal_year}.yaml lists no files for {exhibit}")
    return SourceList(
        fiscal_year=int(data["fiscal_year"]),
        budget_cycle=data["budget_cycle"],
        files=[SourceFile(f["role"], f["format"], f["url"]) for f in files],
    )


@dataclass(frozen=True)
class ExpectedTotal:
    label: str
    include_in_toa: bool
    amounts: dict[str, int]  # Excel header -> $K


def load_expected_totals(cycle: str, exhibit: str) -> list[ExpectedTotal]:
    path = CONFIG_DIR / "expected_totals" / f"{cycle}_{normalize_exhibit(exhibit)}.yaml"
    if not path.exists():
        return []
    data = _load_yaml(path)
    return [
        ExpectedTotal(g["label"], bool(g["include_in_toa"]), {k: int(v) for k, v in g["amounts"].items()})
        for g in data["groups"]
    ]


def config_fingerprint() -> str:
    """Hash of every config file, so a config change forces a fresh ingestion run."""
    h = hashlib.sha256()
    for path in sorted(CONFIG_DIR.rglob("*.yaml")):
        h.update(path.relative_to(CONFIG_DIR).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()
