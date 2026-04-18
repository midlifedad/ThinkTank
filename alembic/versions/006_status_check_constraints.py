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
would reject live rows at migration time.

Deploy hardening (per Troy's review):

1. **Pre-flight DISTINCT check.** Before adding each constraint we query
   ``SELECT DISTINCT <col>`` to catch rows that would violate the allowed
   set. If any offender is found we raise a ``RuntimeError`` with the
   specific table, column, and offending values -- this halts the
   migration cleanly with a remediation hint rather than failing halfway
   through with an opaque psycopg error. Without this guard a row written
   by a future worker with a typoed status ("complete" vs "done") would
   tank the entire migration on production.

2. **NOT VALID + separate VALIDATE.** Large tables like ``content`` and
   ``jobs`` take an AccessExclusiveLock for the full-scan validation
   that ``ADD CONSTRAINT ... CHECK`` performs by default. Adding the
   constraint as ``NOT VALID`` first makes it apply to new rows
   immediately (cheap, AccessExclusiveLock only for the catalog update)
   and defers the full-scan validation to a separate ``VALIDATE
   CONSTRAINT`` statement that takes only a ShareUpdateExclusiveLock
   (concurrent writes still run). The downgrade path drops both
   constraints symmetrically.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from thinktank.models.constants import (
    ALLOWED_CONTENT_STATUSES,
    ALLOWED_JOB_STATUSES,
    ALLOWED_SOURCE_APPROVAL_STATUSES,
)

# revision identifiers, used by Alembic.
revision: str = "006_status_check"
down_revision: str | Sequence[str] | None = "005_fk_ondelete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def _preflight(table: str, column: str, values: tuple[str, ...]) -> None:
    """Raise if any row in ``table`` has a ``column`` value not in ``values``.

    Rows with NULL are ignored (CHECK allows NULL unless explicitly
    ``CHECK (col IS NOT NULL AND col IN (...))``). This matches Postgres
    CHECK semantics so the pre-flight and the constraint agree.
    """
    bind = op.get_bind()
    allowed_set = set(values)
    offenders = bind.execute(text(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL")).scalars().all()
    bad = [v for v in offenders if v not in allowed_set]
    if bad:
        raise RuntimeError(
            f"006_status_check: {table}.{column} contains values that would "
            f"violate the new CHECK constraint: {sorted(bad)!r}. "
            f"Allowed set: {sorted(allowed_set)!r}. "
            f"Fix the offending rows (UPDATE or DELETE) before re-running."
        )


def _add_check_not_valid(name: str, table: str, expr: str) -> None:
    """Add a CHECK constraint as NOT VALID then run VALIDATE CONSTRAINT.

    Splitting the operation avoids holding AccessExclusiveLock for the
    full table scan; VALIDATE CONSTRAINT only needs ShareUpdateExclusive,
    so concurrent writers keep running.
    """
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expr}) NOT VALID")
    op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")


def upgrade() -> None:
    # Pre-flight: fail fast with a helpful error if any row would violate.
    _preflight("content", "status", ALLOWED_CONTENT_STATUSES)
    _preflight("sources", "approval_status", ALLOWED_SOURCE_APPROVAL_STATUSES)
    _preflight("jobs", "status", ALLOWED_JOB_STATUSES)

    _add_check_not_valid(
        "ck_content_status",
        "content",
        _in_list("status", ALLOWED_CONTENT_STATUSES),
    )
    _add_check_not_valid(
        "ck_source_approval_status",
        "sources",
        _in_list("approval_status", ALLOWED_SOURCE_APPROVAL_STATUSES),
    )
    _add_check_not_valid(
        "ck_job_status",
        "jobs",
        _in_list("status", ALLOWED_JOB_STATUSES),
    )


def downgrade() -> None:
    op.drop_constraint("ck_job_status", "jobs", type_="check")
    op.drop_constraint("ck_source_approval_status", "sources", type_="check")
    op.drop_constraint("ck_content_status", "content", type_="check")
