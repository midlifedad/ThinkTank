"""Add CHECK constraints on enum-like status columns.

Revision ID: 006_status_check
Revises: 005_fk_ondelete
Create Date: 2026-04-16

Source: DATA-REVIEW H3. `Content.status`, `Source.approval_status`, and
`Job.status` are used as closed-set enums throughout the codebase, but the
underlying columns are plain TEXT with no validation -- a typo in handler
code silently inserts an invalid state.

This migration adds Postgres CHECK constraints whose allowed-value lists
are imported from `src/thinktank/models/constants.py` so the migration and
ORM mirror each other.

NOTE on allowed sets: the plan's brief only listed {approved, pending,
rejected} for `source.approval_status`, but production code actively writes
{pending_llm, awaiting_llm, rejected_by_llm, pending_human} via the LLM
approval pipeline (admin/routers, llm/decisions, agent/tools). The constant
`ALLOWED_SOURCE_APPROVAL_STATUSES` therefore encodes the superset; narrowing
would reject live rows at migration time. See commit message for details.
"""

from typing import Sequence, Union

from alembic import op

from thinktank.models.constants import (
    ALLOWED_CONTENT_STATUSES,
    ALLOWED_JOB_STATUSES,
    ALLOWED_SOURCE_APPROVAL_STATUSES,
)

# revision identifiers, used by Alembic.
revision: str = "006_status_check"
down_revision: Union[str, Sequence[str], None] = "005_fk_ondelete"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    op.create_check_constraint(
        "ck_content_status",
        "content",
        _in_list("status", ALLOWED_CONTENT_STATUSES),
    )
    op.create_check_constraint(
        "ck_source_approval_status",
        "sources",
        _in_list("approval_status", ALLOWED_SOURCE_APPROVAL_STATUSES),
    )
    op.create_check_constraint(
        "ck_job_status",
        "jobs",
        _in_list("status", ALLOWED_JOB_STATUSES),
    )


def downgrade() -> None:
    op.drop_constraint("ck_job_status", "jobs", type_="check")
    op.drop_constraint(
        "ck_source_approval_status", "sources", type_="check"
    )
    op.drop_constraint("ck_content_status", "content", type_="check")
