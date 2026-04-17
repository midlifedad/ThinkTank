"""LLMReview model.

Spec reference: Section 3.11 (llm_reviews).
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from thinktank.models.base import Base, uuid_pk


class LLMReview(Base):
    """Full audit trail of every LLM Supervisor decision."""

    __tablename__ = "llm_reviews"

    id: Mapped[uuid_pk]
    review_type: Mapped[str] = mapped_column(sa.Text)
    trigger: Mapped[str] = mapped_column(sa.Text)
    context_snapshot: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    prompt_used: Mapped[str] = mapped_column(sa.Text)
    llm_response: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    decision: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    decision_reasoning: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    modifications: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    flagged_items: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    overridden_by: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    overridden_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    override_reasoning: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    model: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("NOW()"))

    def __repr__(self) -> str:
        return f"<LLMReview(type={self.review_type!r}, decision={self.decision!r})>"
