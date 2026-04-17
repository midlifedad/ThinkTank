"""make sources first-class entities

Revision ID: 004_sources_first_class
Revises: 003_add_pg_trgm
Create Date: 2026-04-12 18:00:00.000000

Decouples sources from thinkers by introducing a source_thinkers junction table
for the many-to-many relationship. Adds source_categories for independent source
categorization. Makes source.thinker_id and content.source_owner_id nullable
(deprecated in favor of junction tables). Adds tier, slug, description, and
host_name columns to sources.

Existing source→thinker relationships are preserved in the junction table via
data migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004_sources_first_class"
down_revision: str | Sequence[str] | None = "003_add_pg_trgm"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add junction tables, new columns, and migrate data."""
    # 1. Create source_thinkers junction table
    op.create_table(
        "source_thinkers",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("thinker_id", sa.Uuid(), nullable=False),
        sa.Column("relationship_type", sa.Text(), nullable=False),
        sa.Column(
            "added_at",
            sa.DateTime(),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.ForeignKeyConstraint(["thinker_id"], ["thinkers.id"]),
        sa.PrimaryKeyConstraint("source_id", "thinker_id"),
    )

    # 2. Create source_categories junction table
    op.create_table(
        "source_categories",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("relevance", sa.SmallInteger(), nullable=False),
        sa.Column(
            "added_at",
            sa.DateTime(),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"]),
        sa.PrimaryKeyConstraint("source_id", "category_id"),
    )

    # 3. Add new columns to sources
    op.add_column(
        "sources",
        sa.Column(
            "tier",
            sa.SmallInteger(),
            server_default=sa.text("2"),
            nullable=False,
        ),
    )
    op.add_column(
        "sources",
        sa.Column("slug", sa.Text(), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column("host_name", sa.Text(), nullable=True),
    )

    # 4. Make sources.thinker_id nullable (deprecated)
    op.alter_column(
        "sources",
        "thinker_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )

    # 5. Make content.source_owner_id nullable (deprecated)
    op.alter_column(
        "content",
        "source_owner_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )

    # 6. Data migration: copy existing source→thinker relationships into junction
    op.execute("""
        INSERT INTO source_thinkers (source_id, thinker_id, relationship_type, added_at)
        SELECT
            id,
            thinker_id,
            CASE
                WHEN config->>'is_guest_source' = 'true' THEN 'guest_appearance'
                ELSE 'host'
            END,
            created_at
        FROM sources
        WHERE thinker_id IS NOT NULL
        ON CONFLICT DO NOTHING
    """)

    # 7. Generate slugs for existing sources.
    #
    # DATA-REVIEW L3: the original migration generated the slug directly from
    # name with REGEXP_REPLACE. Two sources whose names collapsed to the same
    # slug (e.g. "Lex Fridman" and "lex-fridman!") would silently share a slug
    # and then the unique constraint created in step 8 would fail to apply.
    # The ROW_NUMBER() window below deduplicates by appending -2, -3, ... to
    # collisions (stable order: created_at then id), so the unique constraint
    # always succeeds. On databases with no collisions this produces the same
    # slugs as the original migration.
    op.execute("""
        UPDATE sources s
        SET slug = CASE
            WHEN sub.rn > 1 THEN sub.base_slug || '-' || sub.rn
            ELSE sub.base_slug
        END
        FROM (
            SELECT
                id,
                LOWER(
                    TRIM(BOTH '-' FROM
                        REGEXP_REPLACE(
                            REGEXP_REPLACE(name, '[^a-zA-Z0-9]+', '-', 'g'),
                            '-+', '-', 'g'
                        )
                    )
                ) AS base_slug,
                ROW_NUMBER() OVER (
                    PARTITION BY LOWER(
                        TRIM(BOTH '-' FROM
                            REGEXP_REPLACE(
                                REGEXP_REPLACE(name, '[^a-zA-Z0-9]+', '-', 'g'),
                                '-+', '-', 'g'
                            )
                        )
                    ) ORDER BY created_at, id
                ) AS rn
            FROM sources
            WHERE slug IS NULL
        ) sub
        WHERE s.id = sub.id
    """)

    # 8. Create unique index on slug
    op.create_unique_constraint("uq_sources_slug", "sources", ["slug"])


def downgrade() -> None:
    """Remove junction tables and revert column changes."""
    op.drop_constraint("uq_sources_slug", "sources", type_="unique")
    op.drop_column("sources", "host_name")
    op.drop_column("sources", "description")
    op.drop_column("sources", "slug")
    op.drop_column("sources", "tier")
    op.alter_column(
        "content",
        "source_owner_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.alter_column(
        "sources",
        "thinker_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.drop_table("source_categories")
    op.drop_table("source_thinkers")
