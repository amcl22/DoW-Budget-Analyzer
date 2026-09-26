"""Full-text search over line items.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-25
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# Weights: A program title and PE/BLI number, B the R-2 mission description, C budget activity,
# sub-activity and organization. PE/BLI use the 'simple' config so '0604858F' stays one token.
SEARCH_TSV = """
    setweight(to_tsvector('english', coalesce(program_title, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(program_element, '') || ' ' || coalesce(line_item_number, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(raw_description_text, '')), 'B') ||
    setweight(to_tsvector('english', coalesce(budget_activity_title, '') || ' ' ||
                                     coalesce(budget_subactivity_title, '') || ' ' ||
                                     coalesce(organization, '')), 'C')
"""


def upgrade() -> None:
    op.execute(f"ALTER TABLE budget_line_item ADD COLUMN search_tsv tsvector GENERATED ALWAYS AS ({SEARCH_TSV}) STORED")
    op.execute("CREATE INDEX ix_budget_line_item_search ON budget_line_item USING gin (search_tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_budget_line_item_search")
    op.execute("ALTER TABLE budget_line_item DROP COLUMN search_tsv")
