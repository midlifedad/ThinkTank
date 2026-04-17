"""Job model.

Spec reference: Section 3.10 (jobs).
"""

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from thinktank.models.base import Base, uuid_pk
from thinktank.models.constants import ALLOWED_JOB_STATUSES


def _job_status_check() -> sa.CheckConstraint:
    """CHECK constraint for Job.status (DATA-REVIEW H3)."""
    values = ", ".join(f"'{s}'" for s in ALLOWED_JOB_STATUSES)
    return sa.CheckConstraint(
        f"status IN ({values})",
        name="ck_job_status",
    )


class Job(Base):
    """A unit of work in the job queue. Every operation is a job row."""

    __tablename__ = "jobs"
    __table_args__ = (
        sa.Index("ix_jobs_claim", "status", "priority", "scheduled_at"),
        _job_status_check(),
    )

    id: Mapped[uuid_pk]
    job_type: Mapped[str] = mapped_column(sa.Text)
    payload: Mapped[dict] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(sa.Text, server_default="pending")
    priority: Mapped[int] = mapped_column(sa.SmallInteger, server_default=sa.text("5"))
    attempts: Mapped[int] = mapped_column(sa.SmallInteger, server_default=sa.text("0"))
    max_attempts: Mapped[int] = mapped_column(sa.SmallInteger, server_default=sa.text("3"))
    error: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    error_category: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    last_error_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    worker_id: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    llm_review_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        sa.ForeignKey("llm_reviews.id"),
        nullable=True,
    )
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.text("NOW()")
    )

    def __repr__(self) -> str:
        return f"<Job(type={self.job_type!r}, status={self.status!r}, priority={self.priority})>"
