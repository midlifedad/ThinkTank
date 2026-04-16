"""SystemConfig model.

Spec reference: Section 3.12 (system_config).
Note: This table uses a TEXT primary key (the config key), NOT a UUID.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from thinktank.models.base import Base


class SystemConfig(Base):
    """Global operational parameters. Workers read on each job claim."""

    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
    set_by: Mapped[str] = mapped_column(sa.Text)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.text("NOW()")
    )

    def __repr__(self) -> str:
        return f"<SystemConfig(key={self.key!r}, set_by={self.set_by!r})>"
