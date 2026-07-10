"""Split LLM token accounting into input/output columns on llm_reviews.

Revision ID: 015_llm_token_split
Revises: 014_drop_deprecated_cols
Create Date: 2026-05-28

Source: ARCH-REVIEW 2026-05-28 (A2, cost tracking).

``llm_reviews.tokens_used`` stores a single combined count, which cannot
be priced accurately -- Anthropic bills input and output tokens at very
different rates (~5x). The client now reports the split and callers
persist it here; the ``rollup_api_usage`` handler aggregates these
columns into hourly ``api_usage`` rows with real token-based cost.

``tokens_used`` is retained as the combined total (existing dashboards
read it) and as the only signal for pre-A2 rows, which the rollup prices
at a blended rate.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "015_llm_token_split"
down_revision: str | Sequence[str] | None = "014_drop_deprecated_cols"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("llm_reviews", sa.Column("input_tokens", sa.Integer(), nullable=True))
    op.add_column("llm_reviews", sa.Column("output_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_reviews", "output_tokens")
    op.drop_column("llm_reviews", "input_tokens")
