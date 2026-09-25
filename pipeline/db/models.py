"""SQLAlchemy models. The schema itself is owned by the Alembic migrations in db/migrations."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

RUN_STATUSES = ("running", "failed", "validated", "published", "superseded")
REF_KINDS = ("r1_summary", "r2_justification", "p1_summary", "p40_justification")


class Base(DeclarativeBase):
    pass


class IngestionRun(Base):
    """One execution of the pipeline. Nothing is visible to the app until status='published'."""

    __tablename__ = "ingestion_run"
    __table_args__ = (
        CheckConstraint(f"status IN {RUN_STATUSES}", name="ck_ingestion_run_status"),
        Index(
            "one_published_run",
            "budget_cycle",
            "exhibit_type",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    budget_cycle: Mapped[str] = mapped_column(Text)
    exhibit_type: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parser_version: Mapped[str] = mapped_column(Text)
    # sha256 over input file hashes + parser version + config; equal fingerprint = same result
    input_fingerprint: Mapped[str] = mapped_column(Text)
    row_count: Mapped[int | None] = mapped_column(Integer)
    validation_report: Mapped[dict | None] = mapped_column(JSONB)


class SourceDocument(Base):
    """A downloaded file, content-addressed so re-runs detect unchanged inputs."""

    __tablename__ = "source_document"
    __table_args__ = (
        UniqueConstraint("url", "sha256"),
        CheckConstraint("format IN ('xlsx','pdf')", name="ck_source_document_format"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text)
    fiscal_year: Mapped[int] = mapped_column(Integer)
    budget_cycle: Mapped[str] = mapped_column(Text)
    exhibit_type: Mapped[str] = mapped_column(Text)
    format: Mapped[str] = mapped_column(Text)
    storage_path: Mapped[str] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AppropriationAccount(Base):
    """Reference table seeded from config/appropriation_accounts.yaml."""

    __tablename__ = "appropriation_account"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    service_branch: Mapped[str] = mapped_column(Text)
    in_rdte_title: Mapped[bool] = mapped_column(Boolean)


class Program(Base):
    """Stable identity across releases: the PE number for RDT&E.

    Classified rollup lines all share PE 9999999999, so they get one program per account
    (program_key '9999999999:2040A') instead of one cross-service program.
    """

    __tablename__ = "program"
    __table_args__ = (
        UniqueConstraint("exhibit_family", "program_key"),
        CheckConstraint("exhibit_family IN ('RDTE','PROC')", name="ck_program_family"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    exhibit_family: Mapped[str] = mapped_column(Text)
    program_key: Mapped[str] = mapped_column(Text)
    latest_title: Mapped[str] = mapped_column(Text)
    is_classified_rollup: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class BudgetLineItem(Base):
    """One line in one release of one exhibit."""

    __tablename__ = "budget_line_item"
    __table_args__ = (
        UniqueConstraint(
            "ingestion_run_id", "appropriation_account", "budget_activity", "line_number"
        ),
        Index("ix_budget_line_item_program", "program_id"),
        Index("ix_budget_line_item_pe", "program_element"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ingestion_run_id: Mapped[int] = mapped_column(ForeignKey("ingestion_run.id"))
    source_document_id: Mapped[int] = mapped_column(ForeignKey("source_document.id"))
    source_row_number: Mapped[int] = mapped_column(Integer)
    program_id: Mapped[int] = mapped_column(ForeignKey("program.id"))
    fiscal_year: Mapped[int] = mapped_column(Integer)
    budget_cycle: Mapped[str] = mapped_column(Text)
    exhibit_type: Mapped[str] = mapped_column(Text)
    appropriation_account: Mapped[str] = mapped_column(ForeignKey("appropriation_account.code"))
    service_branch: Mapped[str] = mapped_column(Text)
    organization: Mapped[str | None] = mapped_column(Text)
    budget_activity: Mapped[str] = mapped_column(Text)
    budget_activity_title: Mapped[str] = mapped_column(Text)
    line_number: Mapped[str] = mapped_column(Text)
    program_element: Mapped[str | None] = mapped_column(Text)
    line_item_number: Mapped[str | None] = mapped_column(Text)
    program_title: Mapped[str] = mapped_column(Text)
    include_in_toa: Mapped[bool] = mapped_column(Boolean)
    classification: Mapped[str] = mapped_column(Text)
    raw_description_text: Mapped[str | None] = mapped_column(Text)

    amounts: Mapped[list[LineItemAmount]] = relationship(cascade="all, delete-orphan")
    source_refs: Mapped[list[LineItemSourceRef]] = relationship(cascade="all, delete-orphan")


class LineItemAmount(Base):
    """$ thousands as integers. A blank cell produces no row; an explicit 0 is stored."""

    __tablename__ = "line_item_amount"

    line_item_id: Mapped[int] = mapped_column(
        ForeignKey("budget_line_item.id", ondelete="CASCADE"), primary_key=True
    )
    funds_fiscal_year: Mapped[int] = mapped_column(Integer, primary_key=True)
    amount_type: Mapped[str] = mapped_column(Text, primary_key=True)
    funding_category: Mapped[str] = mapped_column(Text, primary_key=True)
    amount_thousands: Mapped[int] = mapped_column(BigInteger)
    source_column: Mapped[str] = mapped_column(Text)


class LineItemSourceRef(Base):
    """A PDF page a line item appears on. page_number is the physical page (pdf_url#page=N)."""

    __tablename__ = "line_item_source_ref"
    __table_args__ = (
        CheckConstraint(f"ref_kind IN {REF_KINDS}", name="ck_source_ref_kind"),
    )

    line_item_id: Mapped[int] = mapped_column(
        ForeignKey("budget_line_item.id", ondelete="CASCADE"), primary_key=True
    )
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("source_document.id"), primary_key=True
    )
    page_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    printed_page_label: Mapped[str | None] = mapped_column(Text)
    ref_kind: Mapped[str] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(Text)
    match_method: Mapped[str] = mapped_column(Text)
    amount_verified: Mapped[bool] = mapped_column(Boolean)
    # the preferred link when a line appears in several sections (agency detail over combined)
    is_primary: Mapped[bool] = mapped_column(Boolean)
