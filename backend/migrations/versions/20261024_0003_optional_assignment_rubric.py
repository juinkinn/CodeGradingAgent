"""Allow assignments to be created without a rubric.

Revision ID: 20261024_0003
Revises: 20261024_0002
"""
from alembic import op
import sqlalchemy as sa


revision = "20261024_0003"
down_revision = "20261024_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("assignments") as batch_op:
        batch_op.alter_column(
            "rubric",
            existing_type=sa.JSON(),
            nullable=True,
        )


def downgrade() -> None:
    op.execute("UPDATE assignments SET rubric = '{}' WHERE rubric IS NULL")
    with op.batch_alter_table("assignments") as batch_op:
        batch_op.alter_column(
            "rubric",
            existing_type=sa.JSON(),
            nullable=False,
        )
