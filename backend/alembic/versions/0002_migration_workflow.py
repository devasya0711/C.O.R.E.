"""Persist migration workflow intent and graph snapshot identity.

Revision ID: 0002_migration_workflow
Revises: 0001_postgresql
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0002_migration_workflow"
down_revision: str | None = "0001_postgresql"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Store durable workflow choices and the validated source snapshot."""
    op.add_column(
        "seam_clusters", sa.Column("snapshot_version", sa.String(128), nullable=True)
    )
    op.add_column(
        "migration_jobs",
        sa.Column(
            "create_github_pr",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "migration_jobs",
        sa.Column("snapshot_version", sa.String(128), nullable=True),
    )
    op.add_column(
        "migration_jobs", sa.Column("source_commit_sha", sa.String(64), nullable=True)
    )
    op.add_column(
        "migration_jobs",
        sa.Column(
            "sandbox_passed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("migration_jobs", sa.Column("sandbox_isolation", sa.String(32)))
    op.add_column("migration_jobs", sa.Column("sandbox_isolation_note", sa.Text()))


def downgrade() -> None:
    """Remove Phase 5 migration metadata."""
    op.drop_column("migration_jobs", "source_commit_sha")
    op.drop_column("migration_jobs", "snapshot_version")
    op.drop_column("migration_jobs", "sandbox_passed")
    op.drop_column("migration_jobs", "sandbox_isolation_note")
    op.drop_column("migration_jobs", "sandbox_isolation")
    op.drop_column("seam_clusters", "snapshot_version")
    op.drop_column("migration_jobs", "create_github_pr")
