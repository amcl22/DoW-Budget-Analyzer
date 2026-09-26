"""P-1 support (quantities, budget sub-activity, cost elements) and a funding-history view.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-25
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


LINE_ITEM_FLAT_VIEW = """
CREATE VIEW line_item_flat AS
-- Spec section 3.3 field names over the normalized tables. Published runs only.
-- Amounts are NULL where the exhibit shows a blank; quantities are P-1 only.
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
    li.budget_subactivity,
    li.budget_subactivity_title,
    li.line_number,
    li.appropriation_account,
    li.organization,
    li.include_in_toa,
    py.amount_thousands AS prior_year_amount,
    cy.amount_thousands AS current_year_amount,
    by_.amount_thousands AS budget_year_amount,
    (SELECT a.amount_thousands FROM line_item_amount a
      WHERE a.line_item_id = li.id AND a.funds_fiscal_year = li.fiscal_year
        AND a.funding_category = 'discretionary') AS budget_year_discretionary,
    (SELECT a.amount_thousands FROM line_item_amount a
      WHERE a.line_item_id = li.id AND a.funds_fiscal_year = li.fiscal_year
        AND a.funding_category IN ('mandatory', 'reconciliation')) AS budget_year_mandatory,
    py.quantity AS prior_year_quantity,
    cy.quantity AS current_year_quantity,
    by_.quantity AS budget_year_quantity,
    doc.url AS source_pdf_url,
    ref.page_number AS source_page_number,
    doc.url || '#page=' || ref.page_number AS source_pdf_link,
    li.raw_description_text,
    li.program_id,
    li.ingestion_run_id
FROM budget_line_item li
JOIN ingestion_run r ON r.id = li.ingestion_run_id AND r.status = 'published'
LEFT JOIN line_item_amount py ON py.line_item_id = li.id AND py.funds_fiscal_year = li.fiscal_year - 2
    AND py.funding_category = 'total'
LEFT JOIN line_item_amount cy ON cy.line_item_id = li.id AND cy.funds_fiscal_year = li.fiscal_year - 1
    AND cy.funding_category = 'total'
LEFT JOIN line_item_amount by_ ON by_.line_item_id = li.id AND by_.funds_fiscal_year = li.fiscal_year
    AND by_.funding_category = 'total'
LEFT JOIN LATERAL (
    SELECT s.* FROM line_item_source_ref s
    WHERE s.line_item_id = li.id
    ORDER BY (s.ref_kind IN ('r2_justification', 'p40_justification')) DESC, s.is_primary DESC, s.page_number
    LIMIT 1
) ref ON true
LEFT JOIN source_document doc ON doc.id = ref.source_document_id
"""

PROGRAM_FUNDING_HISTORY_VIEW = """
CREATE VIEW program_funding_history AS
-- One row per program, fiscal year of funds and budget release: the program's total across its
-- lines in that release (a PE can sit under several budget activities). is_latest marks the
-- most recent release reporting that fiscal year, i.e. the best available figure: a request is
-- superseded by the enacted amount, then by the actual.
SELECT
    t.*,
    t.release_fiscal_year = max(t.release_fiscal_year)
        OVER (PARTITION BY t.program_id, t.funds_fiscal_year) AS is_latest
FROM (
    SELECT
        p.id AS program_id,
        p.exhibit_family,
        p.program_key,
        p.latest_title,
        li.exhibit_type,
        li.budget_cycle,
        li.fiscal_year AS release_fiscal_year,
        a.funds_fiscal_year,
        a.amount_type,
        sum(a.amount_thousands) AS amount_thousands,
        sum(a.quantity) AS quantity,
        count(DISTINCT li.id) AS line_count
    FROM budget_line_item li
    JOIN ingestion_run r ON r.id = li.ingestion_run_id AND r.status = 'published'
    JOIN program p ON p.id = li.program_id
    JOIN line_item_amount a ON a.line_item_id = li.id AND a.funding_category = 'total'
    GROUP BY p.id, p.exhibit_family, p.program_key, p.latest_title, li.exhibit_type, li.budget_cycle,
             li.fiscal_year, a.funds_fiscal_year, a.amount_type
) t
"""


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS line_item_flat")

    # whether an account is inside an exhibit's title depends on the exhibit (0390D is outside
    # the RDT&E title but inside the procurement total); it lives in config and on each line
    op.drop_column("appropriation_account", "in_rdte_title")

    op.add_column("budget_line_item", sa.Column("budget_subactivity", sa.Text()))
    op.add_column("budget_line_item", sa.Column("budget_subactivity_title", sa.Text()))
    op.create_index("ix_budget_line_item_bli", "budget_line_item", ["line_item_number"])
    op.add_column("line_item_amount", sa.Column("quantity", sa.BigInteger()))

    op.create_table(
        "line_item_cost_element",
        sa.Column(
            "line_item_id",
            sa.BigInteger(),
            sa.ForeignKey("budget_line_item.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source_row_number", sa.Integer(), primary_key=True),
        sa.Column("source_column", sa.Text(), primary_key=True),
        sa.Column("cost_type", sa.Text(), nullable=False),
        sa.Column("cost_type_title", sa.Text(), nullable=False),
        sa.Column("is_add", sa.Boolean(), nullable=False),
        sa.Column("funds_fiscal_year", sa.Integer(), nullable=False),
        sa.Column("amount_type", sa.Text(), nullable=False),
        sa.Column("funding_category", sa.Text(), nullable=False),
        sa.Column("amount_thousands", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.BigInteger()),
    )

    op.execute(LINE_ITEM_FLAT_VIEW)
    op.execute(PROGRAM_FUNDING_HISTORY_VIEW)


def downgrade() -> None:
    from importlib import import_module

    op.execute("DROP VIEW IF EXISTS program_funding_history")
    op.execute("DROP VIEW IF EXISTS line_item_flat")
    op.drop_table("line_item_cost_element")
    op.drop_column("line_item_amount", "quantity")
    op.drop_index("ix_budget_line_item_bli", table_name="budget_line_item")
    op.drop_column("budget_line_item", "budget_subactivity_title")
    op.drop_column("budget_line_item", "budget_subactivity")
    op.add_column(
        "appropriation_account",
        sa.Column("in_rdte_title", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.execute(import_module("pipeline.db.migrations.versions.0001_initial_schema").LINE_ITEM_FLAT_VIEW)
