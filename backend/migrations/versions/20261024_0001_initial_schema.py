"""Create the initial application schema.

Revision ID: 20261024_0001
Revises:
"""
from alembic import op
import sqlalchemy as sa


revision = "20261024_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=50), nullable=False),
        sa.Column("min_score", sa.Float(), nullable=True),
        sa.Column("max_score", sa.Float(), nullable=True),
        sa.Column("rubric", sa.JSON(), nullable=False),
    )
    op.create_table(
        "submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "assignment_id",
            sa.Integer(),
            sa.ForeignKey("assignments.id"),
            nullable=False,
        ),
        sa.Column("source_code", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=True),
    )
    op.create_table(
        "grading_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "assignment_id",
            sa.Integer(),
            sa.ForeignKey("assignments.id"),
            nullable=False,
        ),
        sa.Column("type", sa.String(length=30), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=True),
    )
    op.create_table(
        "session_submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("grading_sessions.id"),
            nullable=False,
        ),
        sa.Column(
            "submission_id",
            sa.Integer(),
            sa.ForeignKey("submissions.id"),
            nullable=False,
        ),
        sa.Column("proposed_score", sa.Float(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=True),
        sa.UniqueConstraint("session_id", "submission_id"),
    )
    op.create_table(
        "grading_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("grading_sessions.id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
    )
    op.create_table(
        "submission_upload_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "assignment_id",
            sa.Integer(),
            sa.ForeignKey("assignments.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_table(
        "submission_upload_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("submission_upload_batches.id"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("source_code", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "submission_id",
            sa.Integer(),
            sa.ForeignKey("submissions.id"),
            nullable=True,
        ),
    )
    for table_name in (
        "assignments",
        "submissions",
        "grading_sessions",
        "session_submissions",
        "grading_messages",
        "submission_upload_batches",
        "submission_upload_items",
    ):
        op.create_index(
            f"ix_{table_name}_id",
            table_name,
            ["id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("submission_upload_items")
    op.drop_table("submission_upload_batches")
    op.drop_table("grading_messages")
    op.drop_table("session_submissions")
    op.drop_table("grading_sessions")
    op.drop_table("submissions")
    op.drop_table("assignments")
