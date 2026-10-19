"""Data problem reports.

Anyone with the team link can report a problem with a program's figures or explanations
("Report a problem" on the program page). Reports stay open until the data owner resolves
them (`budget issues`, `budget resolve-issue ID`); open ones are listed on the watchlist page.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_issue",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("program_id", sa.BigInteger(), sa.ForeignKey("program.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reporter", sa.Text()),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("resolution", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('open', 'resolved')", name="ck_data_issue_status"),
    )


def downgrade() -> None:
    op.drop_table("data_issue")
