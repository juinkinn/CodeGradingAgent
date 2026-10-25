"""Add users and assignment ownership.

Revision ID: 20261025_0004
Revises: 20261024_0003
"""
from alembic import op
import sqlalchemy as sa


revision = "20261025_0004"
down_revision = "20261024_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("google_subject", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("google_subject"),
    )
    op.create_index("ix_users_id", "users", ["id"], unique=False)
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_google_subject", "users", ["google_subject"], unique=True)
    with op.batch_alter_table("assignments") as batch_op:
        batch_op.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_assignments_owner_id_users",
            "users",
            ["owner_id"],
            ["id"],
        )
        batch_op.create_index("ix_assignments_owner_id", ["owner_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("assignments") as batch_op:
        batch_op.drop_index("ix_assignments_owner_id")
        batch_op.drop_constraint("fk_assignments_owner_id_users", type_="foreignkey")
        batch_op.drop_column("owner_id")
    op.drop_index("ix_users_google_subject", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")
