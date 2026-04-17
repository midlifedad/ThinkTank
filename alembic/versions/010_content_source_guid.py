"""Add content.source_guid for podcast:person per-episode matching.

Revision ID: 010_content_source_guid
Revises: 008_renormalize_content_urls
Create Date: 2026-04-16

Source: HANDLERS-REVIEW ME-02 / Phase 6B T6.7.

The scan_episodes_for_thinkers handler needs to match podcast:person tags
to the specific episode they belong to. Previously the scanner iterated
every person across every GUID in the feed and applied them to every
episode in the batch — a thinker tagged on episode A would wrongly attach
to episode B in the same fetch.

podcast_person_parser already returns a GUID-keyed dict. This migration
adds the matching key on the Content side so we can look up an episode's
persons at scan time without re-embedding the full RSS XML in the job
payload (see also T6.8).

The column is nullable because it is only populated for podcast sources
with stable GUIDs; YouTube and existing cataloged rows keep NULL.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "010_content_source_guid"
down_revision: Union[str, Sequence[str], None] = "008_renormalize_urls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "content",
        sa.Column("source_guid", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("content", "source_guid")
