"""ApiUsage model.

Spec reference: Section 3.14 (api_usage).
"""

from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from thinktank.models.base import Base, uuid_pk


class ApiUsage(Base):
    """Aggregated API usage for cost monitoring and dashboard reporting."""

    __tablename__ = "api_usage"
    __table_args__ = (
        sa.Index("ix_api_usage_timeseries", "api_name", "period_start"),
    )

    id: Mapped[uuid_pk]
    api_name: Mapped[str] = mapped_column(sa.Text)
    endpoint: Mapped[str] = mapped_column(sa.Text)
    period_start: Mapped[datetime]
    call_count: Mapped[int] = mapped_column(sa.Integer)
    units_consumed: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    estimated_cost_usd: Mapped[Optional[float]] = mapped_column(
        sa.Numeric(10, 4),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<ApiUsage(api={self.api_name!r}, endpoint={self.endpoint!r})>"
