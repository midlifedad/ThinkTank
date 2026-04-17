"""Contract tests for scan_episodes_for_thinkers and rescan_cataloged_for_thinker handlers.

Each test documents the handler's external contract:
    - Given specific input payloads and database state
    - What side effects are produced (status changes, rows created)

Contract tests run against real PostgreSQL with factory-generated test data.

Handlers tested:
    - scan_episodes_for_thinkers: Episode scanning, host/guest promotion logic
    - rescan_cataloged_for_thinker: Retroactive scanning when new thinkers approved
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.handlers.rescan_cataloged_for_thinker import (
    handle_rescan_cataloged_for_thinker,
)
from thinktank.handlers.scan_episodes_for_thinkers import (
    handle_scan_episodes_for_thinkers,
)
from thinktank.models.content import Content, ContentThinker
from tests.factories import (
    create_content,
    create_content_thinker,
    create_job,
    create_source,
    create_source_thinker,
    create_thinker,
)

pytestmark = pytest.mark.anyio


class TestScanEpisodesForThinkersContract:
    """Contract: scan_episodes_for_thinkers handler.

    Given: job with payload {content_ids, source_id, descriptions}
    Then: promotes matching cataloged episodes to pending, creates ContentThinker rows
    """

    async def test_scan_promotes_host_source_all_episodes(
        self, session: AsyncSession
    ):
        """Host-owned source promotes ALL cataloged episodes regardless of title match."""
        thinker = await create_thinker(session, name="Host Thinker")
        source = await create_source(session)
        await create_source_thinker(
            session,
            source_id=source.id,
            thinker_id=thinker.id,
            relationship_type="host",
        )

        # Create 3 cataloged episodes with unrelated titles
        contents = []
        for i in range(3):
            c = await create_content(
                session,
                source_id=source.id,
                title=f"Unrelated Episode {i}",
                status="cataloged",
            )
            contents.append(c)

        job = await create_job(
            session,
            job_type="scan_episodes_for_thinkers",
            payload={
                "content_ids": [str(c.id) for c in contents],
                "source_id": str(source.id),
                "descriptions": {str(c.id): "No thinker names here" for c in contents},
            },
        )
        await session.commit()

        await handle_scan_episodes_for_thinkers(session, job)

        # All 3 should be promoted to pending
        for c in contents:
            await session.refresh(c)
            assert c.status == "pending", f"Content {c.title} should be pending"

        # ContentThinker rows should exist for each with role=primary
        result = await session.execute(
            select(ContentThinker).where(
                ContentThinker.thinker_id == thinker.id,
            )
        )
        attributions = result.scalars().all()
        assert len(attributions) == 3
        for attr in attributions:
            assert attr.role == "primary"
            assert attr.confidence == 10

    async def test_scan_host_source_also_tags_guest_thinkers_in_title(
        self, session: AsyncSession
    ):
        """Host source: episodes should tag BOTH the host (primary) AND any guest
        thinkers mentioned in the title (role='guest').

        HANDLERS-REVIEW ME-02: previously the handler `continue`d after primary
        attribution, so Lex (host) interviewing Jensen Huang (tracked thinker)
        never produced a guest junction row for Jensen.
        """
        host = await create_thinker(session, name="Lex Fridman")
        guest = await create_thinker(session, name="Jensen Huang")
        source = await create_source(session)
        await create_source_thinker(
            session,
            source_id=source.id,
            thinker_id=host.id,
            relationship_type="host",
        )

        episode = await create_content(
            session,
            source_id=source.id,
            title="Jensen Huang: NVIDIA and the Future of AI",
            status="cataloged",
        )
        unrelated = await create_content(
            session,
            source_id=source.id,
            title="Solo ramble about life",
            status="cataloged",
        )

        job = await create_job(
            session,
            job_type="scan_episodes_for_thinkers",
            payload={
                "content_ids": [str(episode.id), str(unrelated.id)],
                "source_id": str(source.id),
                "descriptions": {
                    str(episode.id): "A conversation about accelerated computing.",
                    str(unrelated.id): "Just thoughts.",
                },
            },
        )
        await session.commit()

        await handle_scan_episodes_for_thinkers(session, job)

        # Both episodes promoted (host source promotes everything)
        await session.refresh(episode)
        await session.refresh(unrelated)
        assert episode.status == "pending"
        assert unrelated.status == "pending"

        # Episode featuring Jensen has BOTH a host primary row and a guest row
        host_ct = await session.get(ContentThinker, (episode.id, host.id))
        assert host_ct is not None
        assert host_ct.role == "primary"
        assert host_ct.confidence == 10

        guest_ct = await session.get(ContentThinker, (episode.id, guest.id))
        assert guest_ct is not None, (
            "Guest thinker mentioned in title should get a junction row "
            "even on host-owned sources"
        )
        assert guest_ct.role == "guest"
        assert guest_ct.confidence == 9

        # Unrelated episode only has host row (no spurious guest attribution)
        unrelated_guest = await session.get(
            ContentThinker, (unrelated.id, guest.id)
        )
        assert unrelated_guest is None

    async def test_scan_host_source_does_not_tag_host_as_guest(
        self, session: AsyncSession
    ):
        """Host source: if an episode title contains the HOST's name, the host
        still appears as 'primary' only -- no duplicate 'guest' row."""
        host = await create_thinker(session, name="Lex Fridman")
        source = await create_source(session)
        await create_source_thinker(
            session,
            source_id=source.id,
            thinker_id=host.id,
            relationship_type="host",
        )

        episode = await create_content(
            session,
            source_id=source.id,
            title="Lex Fridman reflects on 500 episodes",
            status="cataloged",
        )

        job = await create_job(
            session,
            job_type="scan_episodes_for_thinkers",
            payload={
                "content_ids": [str(episode.id)],
                "source_id": str(source.id),
                "descriptions": {str(episode.id): "Milestone reflection."},
            },
        )
        await session.commit()

        await handle_scan_episodes_for_thinkers(session, job)

        result = await session.execute(
            select(ContentThinker).where(
                ContentThinker.content_id == episode.id,
                ContentThinker.thinker_id == host.id,
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1
        assert rows[0].role == "primary"

    async def test_scan_promotes_guest_source_matching_only(
        self, session: AsyncSession
    ):
        """Guest source only promotes episodes whose title matches a thinker name."""
        thinker = await create_thinker(session, name="Sam Harris")
        source = await create_source(session)
        # No host SourceThinker -> guest source

        matching = await create_content(
            session,
            source_id=source.id,
            title="Interview with Sam Harris",
            status="cataloged",
        )
        non_matching_1 = await create_content(
            session,
            source_id=source.id,
            title="Random Episode About Cooking",
            status="cataloged",
        )
        non_matching_2 = await create_content(
            session,
            source_id=source.id,
            title="Another Unrelated Episode",
            status="cataloged",
        )

        job = await create_job(
            session,
            job_type="scan_episodes_for_thinkers",
            payload={
                "content_ids": [str(matching.id), str(non_matching_1.id), str(non_matching_2.id)],
                "source_id": str(source.id),
                "descriptions": {
                    str(matching.id): "A deep conversation.",
                    str(non_matching_1.id): "Cooking tips and tricks.",
                    str(non_matching_2.id): "Just an episode.",
                },
            },
        )
        await session.commit()

        await handle_scan_episodes_for_thinkers(session, job)

        # Only the matching episode should be promoted
        await session.refresh(matching)
        assert matching.status == "pending"

        await session.refresh(non_matching_1)
        assert non_matching_1.status == "cataloged"

        await session.refresh(non_matching_2)
        assert non_matching_2.status == "cataloged"

    async def test_scan_leaves_non_cataloged_alone(
        self, session: AsyncSession
    ):
        """Content with status != 'cataloged' is not modified by scan handler."""
        thinker = await create_thinker(session, name="Some Thinker")
        source = await create_source(session)
        await create_source_thinker(
            session,
            source_id=source.id,
            thinker_id=thinker.id,
            relationship_type="host",
        )

        pending_content = await create_content(
            session,
            source_id=source.id,
            title="Already Pending",
            status="pending",
        )
        done_content = await create_content(
            session,
            source_id=source.id,
            title="Already Done",
            status="done",
        )

        job = await create_job(
            session,
            job_type="scan_episodes_for_thinkers",
            payload={
                "content_ids": [str(pending_content.id), str(done_content.id)],
                "source_id": str(source.id),
                "descriptions": {},
            },
        )
        await session.commit()

        await handle_scan_episodes_for_thinkers(session, job)

        await session.refresh(pending_content)
        assert pending_content.status == "pending"

        await session.refresh(done_content)
        assert done_content.status == "done"

    async def test_scan_creates_content_thinker_attribution(
        self, session: AsyncSession
    ):
        """Scan handler creates ContentThinker with correct role and confidence for guest match."""
        thinker = await create_thinker(session, name="Jordan Peterson")
        source = await create_source(session)

        content = await create_content(
            session,
            source_id=source.id,
            title="Jordan Peterson on Meaning",
            status="cataloged",
        )

        job = await create_job(
            session,
            job_type="scan_episodes_for_thinkers",
            payload={
                "content_ids": [str(content.id)],
                "source_id": str(source.id),
                "descriptions": {str(content.id): "A deep discussion."},
            },
        )
        await session.commit()

        await handle_scan_episodes_for_thinkers(session, job)

        ct = await session.get(ContentThinker, (content.id, thinker.id))
        assert ct is not None
        assert ct.role == "guest"
        assert ct.confidence == 9  # Title match confidence

    async def test_scan_does_not_duplicate_attribution(
        self, session: AsyncSession
    ):
        """Scan handler does not create duplicate ContentThinker rows."""
        thinker = await create_thinker(session, name="Naval Ravikant")
        source = await create_source(session)

        content = await create_content(
            session,
            source_id=source.id,
            title="Naval Ravikant on Wealth",
            status="cataloged",
        )

        # Pre-existing attribution
        await create_content_thinker(
            session,
            content_id=content.id,
            thinker_id=thinker.id,
            role="guest",
            confidence=9,
        )

        job = await create_job(
            session,
            job_type="scan_episodes_for_thinkers",
            payload={
                "content_ids": [str(content.id)],
                "source_id": str(source.id),
                "descriptions": {str(content.id): "Interview about wealth."},
            },
        )
        await session.commit()

        await handle_scan_episodes_for_thinkers(session, job)

        result = await session.execute(
            select(ContentThinker).where(
                ContentThinker.content_id == content.id,
                ContentThinker.thinker_id == thinker.id,
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1  # No duplicate

    async def test_scan_skips_content_not_cataloged(
        self, session: AsyncSession
    ):
        """Scan handler skips content that is not status='cataloged'."""
        thinker = await create_thinker(session, name="Test Thinker Skip")
        source = await create_source(session)
        await create_source_thinker(
            session,
            source_id=source.id,
            thinker_id=thinker.id,
            relationship_type="host",
        )

        # Create content with various non-cataloged statuses
        pending = await create_content(
            session,
            source_id=source.id,
            title="Pending Content",
            status="pending",
        )
        skipped = await create_content(
            session,
            source_id=source.id,
            title="Skipped Content",
            status="skipped",
        )

        job = await create_job(
            session,
            job_type="scan_episodes_for_thinkers",
            payload={
                "content_ids": [str(pending.id), str(skipped.id)],
                "source_id": str(source.id),
                "descriptions": {},
            },
        )
        await session.commit()

        await handle_scan_episodes_for_thinkers(session, job)

        # No ContentThinker rows should be created for non-cataloged content
        result = await session.execute(
            select(ContentThinker).where(
                ContentThinker.thinker_id == thinker.id,
            )
        )
        assert len(result.scalars().all()) == 0


class TestRescanCatalogedForThinkerContract:
    """Contract: rescan_cataloged_for_thinker handler.

    Given: job with payload {thinker_id, thinker_name}
    Then: promotes cataloged episodes matching thinker name in title to pending
    """

    async def test_rescan_promotes_matching_title(
        self, session: AsyncSession
    ):
        """Rescan promotes cataloged content whose title contains the thinker name."""
        thinker = await create_thinker(session, name="Eric Weinstein")
        source = await create_source(session)

        matching = await create_content(
            session,
            source_id=source.id,
            title="The Portal: Eric Weinstein Explains Geometric Unity",
            status="cataloged",
        )
        non_matching = await create_content(
            session,
            source_id=source.id,
            title="Random Episode About Gardening",
            status="cataloged",
        )

        job = await create_job(
            session,
            job_type="rescan_cataloged_for_thinker",
            payload={
                "thinker_id": str(thinker.id),
                "thinker_name": "Eric Weinstein",
            },
        )
        await session.commit()

        await handle_rescan_cataloged_for_thinker(session, job)

        await session.refresh(matching)
        assert matching.status == "pending"

        await session.refresh(non_matching)
        assert non_matching.status == "cataloged"

        # ContentThinker should exist for the match
        ct = await session.get(ContentThinker, (matching.id, thinker.id))
        assert ct is not None
        assert ct.role == "guest"
        assert ct.confidence == 7  # Retroactive match confidence

    async def test_rescan_skips_non_cataloged(
        self, session: AsyncSession
    ):
        """Rescan does not modify content that is already pending or done."""
        thinker = await create_thinker(session, name="Bret Weinstein")
        source = await create_source(session)

        pending_content = await create_content(
            session,
            source_id=source.id,
            title="Bret Weinstein on Evolution",
            status="pending",
        )

        job = await create_job(
            session,
            job_type="rescan_cataloged_for_thinker",
            payload={
                "thinker_id": str(thinker.id),
                "thinker_name": "Bret Weinstein",
            },
        )
        await session.commit()

        await handle_rescan_cataloged_for_thinker(session, job)

        await session.refresh(pending_content)
        assert pending_content.status == "pending"  # Unchanged

        # No ContentThinker should be created for non-cataloged content
        ct = await session.get(ContentThinker, (pending_content.id, thinker.id))
        assert ct is None

    async def test_rescan_skips_already_attributed(
        self, session: AsyncSession
    ):
        """Rescan does not create duplicate ContentThinker for already-attributed content."""
        thinker = await create_thinker(session, name="Tyler Cowen")
        source = await create_source(session)

        content = await create_content(
            session,
            source_id=source.id,
            title="Tyler Cowen on Progress",
            status="cataloged",
        )

        # Pre-existing attribution
        await create_content_thinker(
            session,
            content_id=content.id,
            thinker_id=thinker.id,
            role="guest",
            confidence=9,
        )
        await session.commit()

        job = await create_job(
            session,
            job_type="rescan_cataloged_for_thinker",
            payload={
                "thinker_id": str(thinker.id),
                "thinker_name": "Tyler Cowen",
            },
        )
        await session.commit()

        await handle_rescan_cataloged_for_thinker(session, job)

        # Still only 1 ContentThinker row
        result = await session.execute(
            select(ContentThinker).where(
                ContentThinker.content_id == content.id,
                ContentThinker.thinker_id == thinker.id,
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1
