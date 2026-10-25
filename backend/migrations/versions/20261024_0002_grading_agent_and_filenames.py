"""Add submission filenames and grading-agent session fields.

Revision ID: 20261024_0002
Revises: 20261024_0001
"""
from alembic import op
import sqlalchemy as sa


revision = "20261024_0002"
down_revision = "20261024_0001"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    submission_columns = _column_names("submissions")
    if "filename" not in submission_columns:
        op.add_column("submissions", sa.Column("filename", sa.String(length=255)))

    session_columns = _column_names("grading_sessions")
    if "auto_approve" not in session_columns:
        op.add_column(
            "grading_sessions",
            sa.Column(
                "auto_approve",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if "proposal" not in session_columns:
        op.add_column("grading_sessions", sa.Column("proposal", sa.JSON()))
    if "grading_rubric" not in session_columns:
        op.add_column("grading_sessions", sa.Column("grading_rubric", sa.JSON()))


def downgrade() -> None:
    session_columns = _column_names("grading_sessions")
    if "grading_rubric" in session_columns:
        op.drop_column("grading_sessions", "grading_rubric")
    if "proposal" in session_columns:
        op.drop_column("grading_sessions", "proposal")
    if "auto_approve" in session_columns:
        op.drop_column("grading_sessions", "auto_approve")

    if "filename" in _column_names("submissions"):
        op.drop_column("submissions", "filename")
