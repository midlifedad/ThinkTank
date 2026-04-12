"""Add partial index on content.status = 'cataloged' for efficient scanning.

Revision ID: phase13_cataloged_idx
Revises: 004_sources_first_class
Create Date: 2026-04-12

The catalog-then-promote pipeline stores all new episodes as 'cataloged' before
scanning for thinker matches. This partial index makes queries like
SELECT ... WHERE status = 'cataloged' efficient for the rescan handler.
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "phase13_cataloged_idx"
down_revision: Union[str, Sequence[str]] = "004_sources_first_class"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_content_status_cataloged",
        "content",
        ["status"],
        postgresql_where=text("status = 'cataloged'"),
    )


def downgrade() -> None:
    op.drop_index("ix_content_status_cataloged", table_name="content")
