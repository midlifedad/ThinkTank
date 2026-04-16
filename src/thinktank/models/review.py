"""LLMReview model.

Spec reference: Section 3.11 (llm_reviews).
"""

from datetime import datetime
from typing import Optional

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
    llm_response: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    decision: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    decision_reasoning: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    modifications: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    flagged_items: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    overridden_by: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    overridden_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    override_reasoning: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    tokens_used: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=sa.text("NOW()"))

    def __repr__(self) -> str:
        return f"<LLMReview(type={self.review_type!r}, decision={self.decision!r})>"
