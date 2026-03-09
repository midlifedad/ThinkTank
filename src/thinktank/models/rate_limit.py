"""RateLimitUsage model.

Spec reference: Section 3.13 (rate_limit_usage).
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.thinktank.models.base import Base, uuid_pk


class RateLimitUsage(Base):
    """Sliding-window rate limit coordination between concurrent workers."""

    __tablename__ = "rate_limit_usage"
    __table_args__ = (
        sa.Index("ix_rate_limit_usage_window", "api_name", "called_at"),
    )

    id: Mapped[uuid_pk]
    api_name: Mapped[str] = mapped_column(sa.Text)
    worker_id: Mapped[str] = mapped_column(sa.Text)
    called_at: Mapped[datetime] = mapped_column(server_default=sa.text("NOW()"))

    def __repr__(self) -> str:
        return f"<RateLimitUsage(api={self.api_name!r}, worker={self.worker_id!r})>"
