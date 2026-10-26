"""Record when submission upload batches are queued.

Revision ID: 20261026_0005
Revises: 20261025_0004
"""
from alembic import op
import sqlalchemy as sa


revision = "20261026_0005"
down_revision = "20261025_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "submission_upload_batches",
        sa.Column("queued_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("submission_upload_batches", "queued_at")
