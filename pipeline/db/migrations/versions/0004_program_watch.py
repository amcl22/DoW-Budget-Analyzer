"""Watched programs.

A watch pins a program to the team dashboard. user_id is NULL for the shared team watchlist;
personal watches get a user id once accounts exist (build step 6).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-25
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "program_watch",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("program_id", sa.BigInteger(), sa.ForeignKey("program.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Text()),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # one watch per program per watcher; NULL (team) counts as one watcher
    op.execute("CREATE UNIQUE INDEX uq_program_watch ON program_watch (program_id, coalesce(user_id, ''))")


def downgrade() -> None:
    op.drop_table("program_watch")
