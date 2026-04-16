"""Query-count regression tests for admin list builders.

Regression guard for ADMIN-REVIEW MD-01/MD-02: ``_build_thinker_list`` and
``_build_source_list`` previously issued one SELECT per association row,
producing O(N) SQL per request. After adding ``selectinload`` eager loading,
the query count must be bounded by a small constant regardless of list size.

These tests hook SQLAlchemy's ``before_cursor_execute`` event to count
SELECT statements issued while running each list builder against seeded data.

Imports of the routers are deferred into each test so that collection does
not trigger the module-level ``thinktank.database.engine`` to be built
against the wrong DATABASE_URL (the admin app engine is constructed at
import time from env state — see conftest ``admin_client`` fixture).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio


class _QueryCounter:
    """Counts SELECT statements issued against a SQLAlchemy engine."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self, conn, cursor, statement, parameters, context, executemany):
        # Only count actual SELECT work, not BEGIN/COMMIT or savepoints.
        if statement.lstrip().upper().startswith("SELECT"):
            self.count += 1


async def _run_with_query_count(session: AsyncSession, coro_fn):
    """Execute ``coro_fn`` and return (result, select_query_count).

    ``session.get_bind()`` returns a sync ``Engine`` under AsyncSession; that is
    the correct listener target — cursor-execute events fire on the sync engine
    underneath the async facade.
    """
    sync_engine = session.get_bind()
    counter = _QueryCounter()
    event.listen(sync_engine, "before_cursor_execute", counter)
    try:
        result = await coro_fn()
    finally:
        event.remove(sync_engine, "before_cursor_execute", counter)
    return result, counter.count


class TestBuildThinkerListQueryCount:
    """``_build_thinker_list`` must not scale query count with N thinkers."""

    async def test_thinker_list_is_bounded_regardless_of_count(
        self, session: AsyncSession
    ) -> None:
        from thinktank.admin.routers.thinkers import _build_thinker_list

        from tests.factories import (
            create_category,
            create_thinker,
            create_thinker_category,
        )

        # Two shared categories.
        cat_a = await create_category(session, name="Philosophy", slug="philosophy-qc")
        cat_b = await create_category(session, name="Economics", slug="economics-qc")

        # Seed 10 thinkers, each with 2 categories -> 20 junction rows.
        n_rows = 10
        for i in range(n_rows):
            t = await create_thinker(
                session, name=f"QC Thinker {i}", slug=f"qc-thinker-{i}"
            )
            await create_thinker_category(
                session, thinker_id=t.id, category_id=cat_a.id
            )
            await create_thinker_category(
                session, thinker_id=t.id, category_id=cat_b.id
            )
        await session.commit()

        result, query_count = await _run_with_query_count(
            session, lambda: _build_thinker_list(session)
        )

        assert len(result) >= n_rows
        # With selectinload we expect a small constant: the main SELECT plus
        # one extra SELECT each for categories and the nested Category rows.
        # Pre-fix this was ~1 + N * avg_cats. Generous upper bound keeps the
        # test robust across SA minor versions.
        assert query_count <= 10, (
            f"_build_thinker_list issued {query_count} SELECTs for {n_rows} thinkers; "
            f"expected a bounded constant (N+1 regression)"
        )

    async def test_thinker_list_query_count_independent_of_n(
        self, session: AsyncSession
    ) -> None:
        """Query count with 5 thinkers must equal query count with 15 thinkers."""
        from thinktank.admin.routers.thinkers import _build_thinker_list

        from tests.factories import (
            create_category,
            create_thinker,
            create_thinker_category,
        )

        cat = await create_category(session, name="Policy", slug="policy-qc-n")

        async def _seed(n: int) -> None:
            for i in range(n):
                t = await create_thinker(
                    session, name=f"NThinker {n}-{i}", slug=f"n-thinker-{n}-{i}"
                )
                await create_thinker_category(
                    session, thinker_id=t.id, category_id=cat.id
                )
            await session.commit()

        await _seed(5)
        _, count_small = await _run_with_query_count(
            session, lambda: _build_thinker_list(session)
        )

        await _seed(10)  # total now 15
        _, count_large = await _run_with_query_count(
            session, lambda: _build_thinker_list(session)
        )

        assert count_small == count_large, (
            f"Query count varies with N (5 thinkers: {count_small}, "
            f"15 thinkers: {count_large}) — N+1 regression"
        )


class TestBuildSourceListQueryCount:
    """``_build_source_list`` must not scale query count with N sources."""

    async def test_source_list_is_bounded_regardless_of_count(
        self, session: AsyncSession
    ) -> None:
        from thinktank.admin.routers.sources import _build_source_list

        from tests.factories import (
            create_source,
            create_source_thinker,
            create_thinker,
        )

        # Two shared thinkers, each linked to every source.
        thinker_a = await create_thinker(
            session, name="QC Source Thinker A", slug="qc-src-thinker-a"
        )
        thinker_b = await create_thinker(
            session, name="QC Source Thinker B", slug="qc-src-thinker-b"
        )

        n_rows = 10
        for i in range(n_rows):
            src = await create_source(
                session,
                name=f"QC Source {i}",
                url=f"https://example.com/qc-src-{i}.xml",
            )
            await create_source_thinker(
                session,
                source_id=src.id,
                thinker_id=thinker_a.id,
                relationship_type="host",
            )
            await create_source_thinker(
                session,
                source_id=src.id,
                thinker_id=thinker_b.id,
                relationship_type="guest",
            )
        await session.commit()

        result, query_count = await _run_with_query_count(
            session, lambda: _build_source_list(session)
        )

        assert len(result) >= n_rows
        assert query_count <= 10, (
            f"_build_source_list issued {query_count} SELECTs for {n_rows} sources; "
            f"expected a bounded constant (N+1 regression)"
        )

    async def test_source_list_query_count_independent_of_n(
        self, session: AsyncSession
    ) -> None:
        from thinktank.admin.routers.sources import _build_source_list

        from tests.factories import (
            create_source,
            create_source_thinker,
            create_thinker,
        )

        thinker = await create_thinker(
            session, name="QC N-Source Thinker", slug="qc-n-src-thinker"
        )

        async def _seed(n: int, tag: str) -> None:
            for i in range(n):
                src = await create_source(
                    session,
                    name=f"NSource {tag}-{i}",
                    url=f"https://example.com/n-src-{tag}-{i}.xml",
                )
                await create_source_thinker(
                    session,
                    source_id=src.id,
                    thinker_id=thinker.id,
                    relationship_type="host",
                )
            await session.commit()

        await _seed(5, "small")
        _, count_small = await _run_with_query_count(
            session, lambda: _build_source_list(session)
        )

        await _seed(10, "large")  # total now 15
        _, count_large = await _run_with_query_count(
            session, lambda: _build_source_list(session)
        )

        assert count_small == count_large, (
            f"Query count varies with N (5 sources: {count_small}, "
            f"15 sources: {count_large}) — N+1 regression"
        )
