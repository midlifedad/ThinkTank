"""Integration tests for thinker detail page, candidate queue, and discovery trigger."""

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    create_candidate_thinker,
    create_content,
    create_llm_review,
    create_source,
    create_thinker,
    create_thinker_category,
    create_category,
)

pytestmark = pytest.mark.anyio

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test",
)


@pytest.fixture
async def admin_client() -> AsyncClient:
    """HTTP client for admin integration tests."""
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

    from thinktank.config import get_settings

    get_settings.cache_clear()

    from thinktank.admin.main import app as admin_app

    transport = ASGITransport(app=admin_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    get_settings.cache_clear()


class TestThinkerDetail:
    """Test the thinker detail page."""

    async def test_detail_page_loads(self, admin_client, session: AsyncSession):
        """GET /admin/thinkers/{id} returns 200 with thinker name and Sources section."""
        thinker = await create_thinker(session, name="Detail Test", slug="detail-test")
        await session.commit()

        response = await admin_client.get(f"/admin/thinkers/{thinker.id}")
        assert response.status_code == 200
        assert "Detail Test" in response.text
        assert "Sources" in response.text

    async def test_detail_page_404(self, admin_client):
        """GET /admin/thinkers/{random_uuid} returns 404."""
        response = await admin_client.get(f"/admin/thinkers/{uuid.uuid4()}")
        assert response.status_code == 404

    async def test_detail_shows_categories(self, admin_client, session: AsyncSession):
        """Detail page shows thinker's category names."""
        thinker = await create_thinker(session, name="Cat Detail", slug="cat-detail")
        cat = await create_category(session, name="Philosophy", slug="philosophy")
        await create_thinker_category(
            session, thinker_id=thinker.id, category_id=cat.id, relevance=5
        )
        await session.commit()

        response = await admin_client.get(f"/admin/thinkers/{thinker.id}")
        assert response.status_code == 200
        assert "Philosophy" in response.text


class TestThinkerSources:
    """Test the sources partial endpoint."""

    async def test_sources_partial_empty(self, admin_client, session: AsyncSession):
        """GET sources partial with no sources returns 200 with empty message."""
        thinker = await create_thinker(session, name="No Sources", slug="no-sources")
        await session.commit()

        response = await admin_client.get(
            f"/admin/thinkers/{thinker.id}/partials/sources"
        )
        assert response.status_code == 200
        assert "No sources found" in response.text

    async def test_sources_partial_shows_sources(
        self, admin_client, session: AsyncSession
    ):
        """GET sources partial with seeded sources shows both source names."""
        thinker = await create_thinker(session, name="Sourced", slug="sourced")
        await create_source(
            session,
            thinker_id=thinker.id,
            name="Feed Alpha",
            url="https://example.com/alpha.xml",
        )
        await create_source(
            session,
            thinker_id=thinker.id,
            name="Feed Beta",
            url="https://example.com/beta.xml",
        )
        await session.commit()

        response = await admin_client.get(
            f"/admin/thinkers/{thinker.id}/partials/sources"
        )
        assert response.status_code == 200
        assert "Feed Alpha" in response.text
        assert "Feed Beta" in response.text


class TestThinkerContent:
    """Test the content partial endpoint."""

    async def test_content_partial_empty(self, admin_client, session: AsyncSession):
        """GET content partial with no content returns 200 with empty message."""
        thinker = await create_thinker(session, name="No Content", slug="no-content")
        await session.commit()

        response = await admin_client.get(
            f"/admin/thinkers/{thinker.id}/partials/content"
        )
        assert response.status_code == 200
        assert "No content found" in response.text

    async def test_content_partial_shows_content(
        self, admin_client, session: AsyncSession
    ):
        """GET content partial with seeded content shows titles."""
        thinker = await create_thinker(session, name="Has Content", slug="has-content")
        source = await create_source(
            session,
            thinker_id=thinker.id,
            name="Content Source",
            url="https://example.com/contentfeed.xml",
        )
        await create_content(
            session,
            source_id=source.id,
            source_owner_id=thinker.id,
            title="Episode Alpha",
        )
        await create_content(
            session,
            source_id=source.id,
            source_owner_id=thinker.id,
            title="Episode Beta",
        )
        await session.commit()

        response = await admin_client.get(
            f"/admin/thinkers/{thinker.id}/partials/content"
        )
        assert response.status_code == 200
        assert "Episode Alpha" in response.text
        assert "Episode Beta" in response.text


class TestThinkerReviews:
    """Test the reviews partial endpoint."""

    async def test_reviews_partial_empty(self, admin_client, session: AsyncSession):
        """GET reviews partial with no reviews returns 200 with empty message."""
        thinker = await create_thinker(session, name="No Reviews", slug="no-reviews")
        await session.commit()

        response = await admin_client.get(
            f"/admin/thinkers/{thinker.id}/partials/reviews"
        )
        assert response.status_code == 200
        assert "No LLM reviews found" in response.text

    async def test_reviews_partial_shows_reviews(
        self, admin_client, session: AsyncSession
    ):
        """GET reviews partial with review containing thinker_id shows decision."""
        thinker = await create_thinker(session, name="Reviewed", slug="reviewed")
        await create_llm_review(
            session,
            review_type="thinker_approval",
            context_snapshot={"thinker_id": str(thinker.id)},
            decision="approve",
            decision_reasoning="Excellent expert in the field",
        )
        await session.commit()

        response = await admin_client.get(
            f"/admin/thinkers/{thinker.id}/partials/reviews"
        )
        assert response.status_code == 200
        assert "approve" in response.text


class TestDiscoveryTrigger:
    """Test the PodcastIndex discovery trigger."""

    async def test_trigger_discovery_creates_job(
        self, admin_client, session: AsyncSession
    ):
        """POST discover creates a discover_guests_podcastindex job."""
        thinker = await create_thinker(
            session, name="Discover Me", slug="discover-me"
        )
        await session.commit()

        response = await admin_client.post(
            f"/admin/thinkers/{thinker.id}/discover"
        )
        assert response.status_code == 200

        from src.thinktank.models.job import Job

        result = await session.execute(
            select(Job).where(Job.job_type == "discover_guests_podcastindex")
        )
        job = result.scalar_one_or_none()
        assert job is not None
        assert job.payload["thinker_id"] == str(thinker.id)
        assert job.status == "pending"

    async def test_trigger_discovery_returns_success(
        self, admin_client, session: AsyncSession
    ):
        """POST discover returns success message with thinker name."""
        thinker = await create_thinker(
            session, name="Discovery Target", slug="discovery-target"
        )
        await session.commit()

        response = await admin_client.post(
            f"/admin/thinkers/{thinker.id}/discover"
        )
        assert response.status_code == 200
        assert "Discovery job queued" in response.text
        assert "Discovery Target" in response.text


class TestCandidateQueue:
    """Test the candidate queue page."""

    async def test_candidate_queue_page_loads(self, admin_client):
        """GET /admin/thinkers/candidates returns 200 with title."""
        response = await admin_client.get("/admin/thinkers/candidates")
        assert response.status_code == 200
        assert "Candidate Queue" in response.text

    async def test_candidate_queue_shows_candidates(
        self, admin_client, session: AsyncSession
    ):
        """Seeded candidates appear in the queue."""
        await create_candidate_thinker(
            session, name="Candidate Alpha", normalized_name="candidate alpha"
        )
        await create_candidate_thinker(
            session, name="Candidate Beta", normalized_name="candidate beta"
        )
        await session.commit()

        response = await admin_client.get("/admin/thinkers/candidates")
        assert response.status_code == 200
        assert "Candidate Alpha" in response.text
        assert "Candidate Beta" in response.text

    async def test_candidate_queue_shows_appearance_count(
        self, admin_client, session: AsyncSession
    ):
        """Candidate with appearance_count=5 shows that count."""
        await create_candidate_thinker(
            session,
            name="Frequent Candidate",
            normalized_name="frequent candidate",
            appearance_count=5,
        )
        await session.commit()

        response = await admin_client.get("/admin/thinkers/candidates")
        assert response.status_code == 200
        assert "Frequent Candidate" in response.text
        assert ">5<" in response.text.replace(" ", "").replace("\n", "")


class TestCandidatePromote:
    """Test promoting a candidate to thinker."""

    async def test_promote_creates_thinker(
        self, admin_client, session: AsyncSession
    ):
        """POST promote creates a new Thinker and updates candidate status."""
        candidate = await create_candidate_thinker(
            session,
            name="Promotable Expert",
            normalized_name="promotable expert",
            status="pending_llm",
        )
        await session.commit()

        response = await admin_client.post(
            f"/admin/thinkers/candidates/{candidate.id}/promote",
            data={"reason": "Great expert"},
        )
        assert response.status_code == 200

        from src.thinktank.models.thinker import Thinker
        from src.thinktank.models.candidate import CandidateThinker

        # Verify thinker created
        thinker_result = await session.execute(
            select(Thinker).where(Thinker.name == "Promotable Expert")
        )
        thinker = thinker_result.scalar_one_or_none()
        assert thinker is not None
        assert thinker.tier == 3
        assert thinker.approval_status == "awaiting_llm"

        # Verify candidate updated
        cand_result = await session.execute(
            select(CandidateThinker)
            .where(CandidateThinker.id == candidate.id)
            .execution_options(populate_existing=True)
        )
        updated_cand = cand_result.scalar_one()
        assert updated_cand.status == "promoted"
        assert updated_cand.thinker_id == thinker.id

    async def test_promote_creates_llm_job(
        self, admin_client, session: AsyncSession
    ):
        """After promotion, an llm_approval_check job exists for the new thinker."""
        candidate = await create_candidate_thinker(
            session,
            name="LLM Check Expert",
            normalized_name="llm check expert",
            status="pending_llm",
        )
        await session.commit()

        await admin_client.post(
            f"/admin/thinkers/candidates/{candidate.id}/promote",
            data={"reason": "Needs review"},
        )

        from src.thinktank.models.job import Job
        from src.thinktank.models.thinker import Thinker

        # Find the new thinker
        thinker_result = await session.execute(
            select(Thinker).where(Thinker.name == "LLM Check Expert")
        )
        thinker = thinker_result.scalar_one()

        # Verify job exists
        job_result = await session.execute(
            select(Job).where(Job.job_type == "llm_approval_check")
        )
        job = job_result.scalar_one_or_none()
        assert job is not None
        assert job.payload["entity_id"] == str(thinker.id)
        assert job.payload["entity_type"] == "thinker"

    async def test_promote_returns_updated_queue(
        self, admin_client, session: AsyncSession
    ):
        """Response shows candidate as promoted or success message."""
        candidate = await create_candidate_thinker(
            session,
            name="Queue Update Expert",
            normalized_name="queue update expert",
            status="pending_llm",
        )
        await session.commit()

        response = await admin_client.post(
            f"/admin/thinkers/candidates/{candidate.id}/promote",
            data={"reason": "Good fit"},
        )
        assert response.status_code == 200
        assert "promoted" in response.text.lower()


class TestCandidateReject:
    """Test rejecting a candidate."""

    async def test_reject_updates_status(
        self, admin_client, session: AsyncSession
    ):
        """POST reject updates candidate status to rejected with admin reviewer."""
        candidate = await create_candidate_thinker(
            session,
            name="Rejectable Candidate",
            normalized_name="rejectable candidate",
            status="pending_llm",
        )
        await session.commit()

        response = await admin_client.post(
            f"/admin/thinkers/candidates/{candidate.id}/reject",
            data={"reason": "Not relevant"},
        )
        assert response.status_code == 200

        from src.thinktank.models.candidate import CandidateThinker

        result = await session.execute(
            select(CandidateThinker)
            .where(CandidateThinker.id == candidate.id)
            .execution_options(populate_existing=True)
        )
        updated = result.scalar_one()
        assert updated.status == "rejected"
        assert updated.reviewed_by == "admin"

    async def test_reject_preserves_data(
        self, admin_client, session: AsyncSession
    ):
        """After rejection, candidate name, appearance_count, first_seen_at unchanged."""
        candidate = await create_candidate_thinker(
            session,
            name="Preserved Reject",
            normalized_name="preserved reject",
            appearance_count=7,
            status="pending_llm",
        )
        original_first_seen = candidate.first_seen_at
        await session.commit()

        await admin_client.post(
            f"/admin/thinkers/candidates/{candidate.id}/reject",
            data={"reason": "Not a good fit"},
        )

        from src.thinktank.models.candidate import CandidateThinker

        result = await session.execute(
            select(CandidateThinker)
            .where(CandidateThinker.id == candidate.id)
            .execution_options(populate_existing=True)
        )
        updated = result.scalar_one()
        assert updated.name == "Preserved Reject"
        assert updated.appearance_count == 7
        assert updated.first_seen_at == original_first_seen
