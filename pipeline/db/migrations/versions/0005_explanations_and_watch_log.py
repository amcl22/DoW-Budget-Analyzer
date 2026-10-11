"""Official funding-change explanations, and who changed the team watchlist.

line_item_explanation holds the government's own reasons for a funding change, quoted from
the R-2 justification books: the 'Change Summary Explanation' (vs. the previous President's
Budget) and each project's 'FY X to FY Y Increase/Decrease Statement' (year over year).

The watchlist has no accounts, so anyone with the team link can star or unstar. added_by is
the optional name a person gives when starring; watch_event is the history of every star and
unstar, so the team can see who changed what.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-11
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "line_item_explanation",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("line_item_id", sa.BigInteger(), sa.ForeignKey("budget_line_item.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("source_document_id", sa.BigInteger(), sa.ForeignKey("source_document.id"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("project", sa.Text()),
        sa.Column("title", sa.Text()),
        sa.Column("from_fy", sa.Integer()),
        sa.Column("to_fy", sa.Integer()),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.CheckConstraint("kind IN ('change_summary', 'increase_decrease')", name="ck_explanation_kind"),
    )
    op.create_index("ix_line_item_explanation_line", "line_item_explanation", ["line_item_id"])
    op.add_column("program_watch", sa.Column("added_by", sa.Text()))
    op.create_table(
        "watch_event",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("program_id", sa.BigInteger(), sa.ForeignKey("program.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("action IN ('watch', 'unwatch')", name="ck_watch_event_action"),
    )
    op.create_index("ix_watch_event_created", "watch_event", ["created_at"])


def downgrade() -> None:
    op.drop_table("watch_event")
    op.drop_column("program_watch", "added_by")
    op.drop_table("line_item_explanation")
