"""Initial schema: ingestion runs, source documents, line items, amounts, page refs.

Revision ID: 0001
Revises:
Create Date: 2026-09-25
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


LINE_ITEM_FLAT_VIEW = """
CREATE VIEW line_item_flat AS
-- Spec section 3.3 field names over the normalized tables. Published runs only.
-- Amount columns are NULL where the exhibit shows a blank.
SELECT
    li.id,
    li.fiscal_year,
    li.budget_cycle,
    li.service_branch,
    li.exhibit_type,
    li.program_element,
    li.line_item_number,
    li.program_title,
    li.budget_activity,
    li.budget_activity_title,
    li.line_number,
    li.appropriation_account,
    li.organization,
    li.include_in_toa,
    (SELECT a.amount_thousands FROM line_item_amount a
      WHERE a.line_item_id = li.id AND a.funds_fiscal_year = li.fiscal_year - 2
        AND a.funding_category = 'total') AS prior_year_amount,
    (SELECT a.amount_thousands FROM line_item_amount a
      WHERE a.line_item_id = li.id AND a.funds_fiscal_year = li.fiscal_year - 1
        AND a.funding_category = 'total') AS current_year_amount,
    (SELECT a.amount_thousands FROM line_item_amount a
      WHERE a.line_item_id = li.id AND a.funds_fiscal_year = li.fiscal_year
        AND a.funding_category = 'total') AS budget_year_amount,
    (SELECT a.amount_thousands FROM line_item_amount a
      WHERE a.line_item_id = li.id AND a.funds_fiscal_year = li.fiscal_year
        AND a.funding_category = 'discretionary') AS budget_year_discretionary,
    (SELECT a.amount_thousands FROM line_item_amount a
      WHERE a.line_item_id = li.id AND a.funds_fiscal_year = li.fiscal_year
        AND a.funding_category = 'mandatory') AS budget_year_mandatory,
    doc.url AS source_pdf_url,
    ref.page_number AS source_page_number,
    doc.url || '#page=' || ref.page_number AS source_pdf_link,
    li.raw_description_text,
    li.program_id,
    li.ingestion_run_id
FROM budget_line_item li
JOIN ingestion_run r ON r.id = li.ingestion_run_id AND r.status = 'published'
LEFT JOIN LATERAL (
    SELECT s.* FROM line_item_source_ref s
    WHERE s.line_item_id = li.id
    ORDER BY (s.ref_kind = 'r2_justification') DESC, s.is_primary DESC, s.page_number
    LIMIT 1
) ref ON true
LEFT JOIN source_document doc ON doc.id = ref.source_document_id
"""


def upgrade() -> None:
    op.create_table(
        "ingestion_run",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("budget_cycle", sa.Text(), nullable=False),
        sa.Column("exhibit_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("parser_version", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer()),
        sa.Column("validation_report", postgresql.JSONB()),
        sa.CheckConstraint(
            "status IN ('running', 'failed', 'validated', 'published', 'superseded')",
            name="ck_ingestion_run_status",
        ),
    )
    op.create_index(
        "one_published_run",
        "ingestion_run",
        ["budget_cycle", "exhibit_type"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )

    op.create_table(
        "source_document",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("budget_cycle", sa.Text(), nullable=False),
        sa.Column("exhibit_type", sa.Text(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer()),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("url", "sha256"),
        sa.CheckConstraint("format IN ('xlsx','pdf')", name="ck_source_document_format"),
    )

    op.create_table(
        "appropriation_account",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("service_branch", sa.Text(), nullable=False),
        sa.Column("in_rdte_title", sa.Boolean(), nullable=False),
    )

    op.create_table(
        "program",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("exhibit_family", sa.Text(), nullable=False),
        sa.Column("program_key", sa.Text(), nullable=False),
        sa.Column("latest_title", sa.Text(), nullable=False),
        sa.Column("is_classified_rollup", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.UniqueConstraint("exhibit_family", "program_key"),
        sa.CheckConstraint("exhibit_family IN ('RDTE','PROC')", name="ck_program_family"),
    )

    op.create_table(
        "budget_line_item",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("ingestion_run_id", sa.BigInteger(), sa.ForeignKey("ingestion_run.id"), nullable=False),
        sa.Column("source_document_id", sa.BigInteger(), sa.ForeignKey("source_document.id"), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("program_id", sa.BigInteger(), sa.ForeignKey("program.id"), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("budget_cycle", sa.Text(), nullable=False),
        sa.Column("exhibit_type", sa.Text(), nullable=False),
        sa.Column(
            "appropriation_account", sa.Text(), sa.ForeignKey("appropriation_account.code"), nullable=False
        ),
        sa.Column("service_branch", sa.Text(), nullable=False),
        sa.Column("organization", sa.Text()),
        sa.Column("budget_activity", sa.Text(), nullable=False),
        sa.Column("budget_activity_title", sa.Text(), nullable=False),
        sa.Column("line_number", sa.Text(), nullable=False),
        sa.Column("program_element", sa.Text()),
        sa.Column("line_item_number", sa.Text()),
        sa.Column("program_title", sa.Text(), nullable=False),
        sa.Column("include_in_toa", sa.Boolean(), nullable=False),
        sa.Column("classification", sa.Text(), nullable=False),
        sa.Column("raw_description_text", sa.Text()),
        sa.UniqueConstraint("ingestion_run_id", "appropriation_account", "budget_activity", "line_number"),
    )
    op.create_index("ix_budget_line_item_program", "budget_line_item", ["program_id"])
    op.create_index("ix_budget_line_item_pe", "budget_line_item", ["program_element"])

    op.create_table(
        "line_item_amount",
        sa.Column(
            "line_item_id",
            sa.BigInteger(),
            sa.ForeignKey("budget_line_item.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("funds_fiscal_year", sa.Integer(), primary_key=True),
        sa.Column("amount_type", sa.Text(), primary_key=True),
        sa.Column("funding_category", sa.Text(), primary_key=True),
        sa.Column("amount_thousands", sa.BigInteger(), nullable=False),
        sa.Column("source_column", sa.Text(), nullable=False),
    )

    op.create_table(
        "line_item_source_ref",
        sa.Column(
            "line_item_id",
            sa.BigInteger(),
            sa.ForeignKey("budget_line_item.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "source_document_id", sa.BigInteger(), sa.ForeignKey("source_document.id"), primary_key=True
        ),
        sa.Column("page_number", sa.Integer(), primary_key=True),
        sa.Column("printed_page_label", sa.Text()),
        sa.Column("ref_kind", sa.Text(), nullable=False),
        sa.Column("section", sa.Text()),
        sa.Column("match_method", sa.Text(), nullable=False),
        sa.Column("amount_verified", sa.Boolean(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "ref_kind IN ('r1_summary', 'r2_justification', 'p1_summary', 'p40_justification')",
            name="ck_source_ref_kind",
        ),
    )

    op.execute(LINE_ITEM_FLAT_VIEW)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS line_item_flat")
    op.drop_table("line_item_source_ref")
    op.drop_table("line_item_amount")
    op.drop_index("ix_budget_line_item_pe", table_name="budget_line_item")
    op.drop_index("ix_budget_line_item_program", table_name="budget_line_item")
    op.drop_table("budget_line_item")
    op.drop_table("program")
    op.drop_table("appropriation_account")
    op.drop_table("source_document")
    op.drop_index("one_published_run", table_name="ingestion_run")
    op.drop_table("ingestion_run")
