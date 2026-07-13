"""Integration tests for the Expert Discovery admin page.

Covers: search launch creates the job, area sections group and order
candidates with funnel counts, and the dossier partial renders evidence.
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_candidate_thinker
from thinktank.models.job import Job

pytestmark = pytest.mark.anyio

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test"
)


@pytest.fixture
async def admin_client() -> AsyncClient:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    from thinktank.config import get_settings

    get_settings.cache_clear()
    from thinktank.admin.main import app as admin_app

    transport = ASGITransport(app=admin_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    get_settings.cache_clear()


class TestSearchLaunch:
    async def test_search_creates_expert_search_job(self, admin_client, session: AsyncSession):
        resp = await admin_client.post("/admin/experts/search", data={"area": "Age reversal/longevity"})
        assert resp.status_code == 200
        assert "Expert search launched" in resp.text

        jobs = (await session.execute(select(Job).where(Job.job_type == "expert_search"))).scalars().all()
        assert len(jobs) == 1
        assert jobs[0].payload["area"] == "Age reversal/longevity"

    async def test_blank_area_rejected(self, admin_client):
        resp = await admin_client.post("/admin/experts/search", data={"area": "   "})
        assert resp.status_code == 422


class TestAreaSections:
    async def test_groups_by_area_with_funnel_and_ordering(self, admin_client, session: AsyncSession):
        await create_candidate_thinker(
            session,
            name="Promoted Pro",
            normalized_name="promoted pro",
            status="promoted",
            search_area="longevity",
            qualification_score=72,
        )
        await create_candidate_thinker(
            session,
            name="Reject Ed",
            normalized_name="reject ed",
            status="auto_rejected",
            search_area="longevity",
            qualification_score=12,
        )
        await create_candidate_thinker(
            session,
            name="Other Area",
            normalized_name="other area",
            status="vetting",
            search_area="quantum computing",
        )
        await session.commit()

        resp = await admin_client.get("/admin/experts/partials/areas")
        assert resp.status_code == 200
        body = resp.text
        assert "longevity" in body and "quantum computing" in body
        assert "Promoted Pro" in body and "Reject Ed" in body
        # Funnel counts for the longevity section
        assert "2 surfaced" in body
        assert "1 promoted" in body
        # Promoted sorts above rejected within the section
        assert body.index("Promoted Pro") < body.index("Reject Ed")

    async def test_non_area_candidates_excluded(self, admin_client, session: AsyncSession):
        await create_candidate_thinker(
            session, name="Cascade Carl", normalized_name="cascade carl", status="pending_llm"
        )
        await session.commit()

        resp = await admin_client.get("/admin/experts/partials/areas")
        assert "Cascade Carl" not in resp.text


class TestDossier:
    async def test_dossier_renders_evidence(self, admin_client, session: AsyncSession):
        candidate = await create_candidate_thinker(
            session,
            name="Dossier Dan",
            normalized_name="dossier dan",
            status="pending_human",
            search_area="longevity",
            qualification_score=44,
            score_breakdown={
                "scholarship": 15,
                "notability": 12,
                "authorship": 6,
                "content": 8,
                "peer_signal": 3,
                "qualification_legs": 33,
            },
            evidence={
                "openalex": {"ok": True, "found": True, "h_index": 22, "cited_by_count": 9000, "works_count": 120},
                "wikidata": {"ok": True, "found": True, "qid": "Q123", "has_enwiki": True, "sitelink_count": 9},
                "openlibrary": {"ok": True, "found": True, "work_count": 2, "top_work": "The Long Life"},
                "podcastindex": {"ok": True, "found": True, "appearance_feed_count": 4, "sample_feeds": ["ShowX"]},
                "youtube": {"ok": True, "checked": True, "reachable": True, "url": "https://youtube.com/@dan"},
                "substack": {"ok": True, "checked": False},
                "seed_claim": {"basis": "Wrote the book", "affiliation": "Stanford"},
            },
        )
        await session.commit()

        resp = await admin_client.get(f"/admin/experts/candidates/{candidate.id}/dossier")
        assert resp.status_code == 200
        body = resp.text
        assert "44/100" in body
        assert "h-index 22" in body
        assert "Q123" in body
        assert "The Long Life" in body
        assert "YouTube" in body
        assert "Wrote the book" in body

    async def test_dossier_404_for_unknown(self, admin_client):
        resp = await admin_client.get("/admin/experts/candidates/00000000-0000-0000-0000-000000000000/dossier")
        assert resp.status_code == 404
