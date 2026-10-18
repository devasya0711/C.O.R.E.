"""Create the PostgreSQL system-of-record tables.

Revision ID: 0001_postgresql
Revises: None
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_postgresql"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create all five tables with server defaults, constraints, and indexes."""

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("clerk_id", sa.String(128), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("encrypted_github_pat", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_clerk_id", "users", ["clerk_id"], unique=True)
    op.create_table(
        "repositories",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("repo_url", sa.String(512), nullable=False),
        sa.Column(
            "default_branch", sa.String(128), nullable=False, server_default="main"
        ),
        sa.Column("last_commit_sha", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column(
            "node_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "edge_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'INGESTING', 'READY', 'FAILED')",
            name=op.f("ck_repositories_status"),
        ),
        sa.CheckConstraint("node_count >= 0", name=op.f("ck_repositories_node_count")),
        sa.CheckConstraint("edge_count >= 0", name=op.f("ck_repositories_edge_count")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_repositories_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_repositories"),
    )
    op.create_index("ix_repositories_user_id", "repositories", ["user_id"])
    op.create_table(
        "seam_clusters",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("repo_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("community_id", sa.Integer(), nullable=False),
        sa.Column("suggested_name", sa.String(128), nullable=False),
        sa.Column("cohesion_score", sa.Float(), nullable=False),
        sa.Column(
            "shared_db_tables",
            postgresql.JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "node_ids",
            postgresql.JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.CheckConstraint(
            "cohesion_score >= 0.0 AND cohesion_score <= 1.0",
            name=op.f("ck_seam_clusters_cohesion_score"),
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"],
            ["repositories.id"],
            name="fk_seam_clusters_repo_id_repositories",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_seam_clusters"),
    )
    op.create_index("ix_seam_clusters_repo_id", "seam_clusters", ["repo_id"])
    op.create_table(
        "ingestion_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("repo_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="QUEUED"),
        sa.Column("stage", sa.String(32), nullable=False, server_default="QUEUED"),
        sa.Column(
            "progress", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')",
            name=op.f("ck_ingestion_jobs_status"),
        ),
        sa.CheckConstraint(
            "stage IN ('QUEUED', 'CLONING', 'AST_PARSING', 'VECTOR_EMBEDDING', "
            "'GRAPH_INDEXING', 'COMPLETED', 'FAILED')",
            name=op.f("ck_ingestion_jobs_stage"),
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100", name=op.f("ck_ingestion_jobs_progress")
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"],
            ["repositories.id"],
            name="fk_ingestion_jobs_repo_id_repositories",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_jobs"),
    )
    op.create_index("ix_ingestion_jobs_repo_id", "ingestion_jobs", ["repo_id"])
    op.create_table(
        "migration_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("repo_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seam_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="QUEUED"),
        sa.Column(
            "crag_status", sa.String(32), nullable=False, server_default="PENDING"
        ),
        sa.Column(
            "retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "generated_artifacts",
            postgresql.JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("sandbox_logs", sa.Text(), nullable=True),
        sa.Column("pr_url", sa.String(512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'CRAG_EVAL', 'CODING', 'TESTING', "
            "'COMPLETED', 'FAILED')",
            name=op.f("ck_migration_jobs_status"),
        ),
        sa.CheckConstraint(
            "crag_status IN ('PENDING', 'SUFFICIENT', 'AUGMENTED')",
            name=op.f("ck_migration_jobs_crag_status"),
        ),
        sa.CheckConstraint(
            "retry_count >= 0 AND retry_count <= 3",
            name=op.f("ck_migration_jobs_retry_count"),
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"],
            ["repositories.id"],
            name="fk_migration_jobs_repo_id_repositories",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["seam_id"],
            ["seam_clusters.id"],
            name="fk_migration_jobs_seam_id_seam_clusters",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_migration_jobs"),
    )
    op.create_index("ix_migration_jobs_repo_id", "migration_jobs", ["repo_id"])


def downgrade() -> None:
    """Remove dependent tables first, including every explicit index."""

    op.drop_index("ix_migration_jobs_repo_id", table_name="migration_jobs")
    op.drop_table("migration_jobs")
    op.drop_index("ix_ingestion_jobs_repo_id", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
    op.drop_index("ix_seam_clusters_repo_id", table_name="seam_clusters")
    op.drop_table("seam_clusters")
    op.drop_index("ix_repositories_user_id", table_name="repositories")
    op.drop_table("repositories")
    op.drop_index("ix_users_clerk_id", table_name="users")
    op.drop_table("users")
