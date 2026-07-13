"""Integration tests for the Inquiries admin pages.

Covers: launch creates the Inquiry + run_inquiry job, the list partial
shows position progress, the detail page renders the headline, the
stance matrix orders and colors positions with a distribution strip,
and the receipts partial renders quotes with provenance badges.
"""

import os
import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    create_claim,
    create_claim_observation,
    create_content,
    create_document,
    create_inquiry,
    create_inquiry_position,
    create_source,
    create_thinker,
)
from thinktank.models.claim import Inquiry
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


class TestLaunch:
    async def test_launch_creates_inquiry_and_job(self, admin_client, session: AsyncSession):
        resp = await admin_client.post(
            "/admin/inquiries/launch",
            data={"question": "Does rapamycin extend healthy human lifespan?", "area": "longevity"},
        )
        assert resp.status_code == 200
        assert "Inquiry launched" in resp.text

        inquiry = (await session.execute(select(Inquiry))).scalars().one()
        assert inquiry.question == "Does rapamycin extend healthy human lifespan?"
        assert inquiry.area == "longevity"

        job = (await session.execute(select(Job).where(Job.job_type == "run_inquiry"))).scalars().one()
        assert job.payload["inquiry_id"] == str(inquiry.id)

    async def test_blank_question_rejected(self, admin_client, session: AsyncSession):
        resp = await admin_client.post("/admin/inquiries/launch", data={"question": "   ", "area": ""})
        assert resp.status_code == 422
        assert (await session.execute(select(Inquiry))).scalars().all() == []

    async def test_blank_area_stored_as_null(self, admin_client, session: AsyncSession):
        await admin_client.post("/admin/inquiries/launch", data={"question": "A question?", "area": "  "})
        inquiry = (await session.execute(select(Inquiry))).scalars().one()
        assert inquiry.area is None


class TestList:
    async def test_list_shows_position_progress(self, admin_client, session: AsyncSession):
        inquiry = await create_inquiry(session, question="Q1?", status="complete")
        t1 = await create_thinker(session)
        t2 = await create_thinker(session)
        await create_inquiry_position(session, inquiry_id=inquiry.id, thinker_id=t1.id, stance="asserts")
        await create_inquiry_position(session, inquiry_id=inquiry.id, thinker_id=t2.id, stance="unknown")
        await session.commit()

        resp = await admin_client.get("/admin/inquiries/partials/list")
        assert resp.status_code == 200
        assert "Q1?" in resp.text
        assert "1/2 with a stance" in resp.text
        assert f"/admin/inquiries/{inquiry.id}" in resp.text


class TestDetail:
    async def test_detail_renders_headline(self, admin_client, session: AsyncSession):
        headline = await create_claim(session, proposition="Rapamycin extends healthy human lifespan")
        inquiry = await create_inquiry(
            session, question="Does rapamycin work?", canonical_claim_id=headline.id, status="complete"
        )
        await session.commit()

        resp = await admin_client.get(f"/admin/inquiries/{inquiry.id}")
        assert resp.status_code == 200
        assert "Does rapamycin work?" in resp.text
        assert "Rapamycin extends healthy human lifespan" in resp.text

    async def test_detail_404_for_unknown(self, admin_client):
        resp = await admin_client.get(f"/admin/inquiries/{uuid.uuid4()}")
        assert resp.status_code == 404


class TestStanceMatrix:
    async def test_matrix_orders_and_summarizes(self, admin_client, session: AsyncSession):
        inquiry = await create_inquiry(session, question="Q?", status="complete")
        unknown = await create_thinker(session, name="Zed Unknown")
        asserter = await create_thinker(session, name="Amy Asserter")
        await create_inquiry_position(
            session, inquiry_id=inquiry.id, thinker_id=unknown.id, stance="unknown", observation_count=0
        )
        await create_inquiry_position(
            session,
            inquiry_id=inquiry.id,
            thinker_id=asserter.id,
            stance="asserts",
            position_summary="Strongly supports it.",
            observation_count=3,
        )
        await session.commit()

        resp = await admin_client.get(f"/admin/inquiries/{inquiry.id}/partials/matrix")
        assert resp.status_code == 200
        # Firm stances sort above unknown.
        assert resp.text.index("Amy Asserter") < resp.text.index("Zed Unknown")
        assert "Strongly supports it." in resp.text
        assert "asserts: 1" in resp.text
        assert "unknown: 1" in resp.text
        assert "3 receipts" in resp.text

    async def test_matrix_empty_state(self, admin_client, session: AsyncSession):
        inquiry = await create_inquiry(session, question="Q?", status="running")
        await session.commit()
        resp = await admin_client.get(f"/admin/inquiries/{inquiry.id}/partials/matrix")
        assert resp.status_code == 200
        assert "No expert positions yet" in resp.text


class TestReceipts:
    async def test_receipts_render_quotes_and_provenance(self, admin_client, session: AsyncSession):
        inquiry = await create_inquiry(session, question="Q?")
        thinker = await create_thinker(session)
        source = await create_source(session)
        content = await create_content(session, source_id=source.id, title="Longevity Podcast #12", status="done")
        document = await create_document(session, url="https://example.com/rapa", domain="example.com")

        await create_claim_observation(
            session,
            inquiry_id=inquiry.id,
            thinker_id=thinker.id,
            content_id=content.id,
            quote="rapamycin extends lifespan in mice",
            claim_text="Rapamycin extends lifespan in mice",
            stance="asserts",
            grounded=True,
            asserted_at=datetime(2026, 2, 14, tzinfo=UTC),
        )
        await create_claim_observation(
            session,
            inquiry_id=inquiry.id,
            thinker_id=thinker.id,
            document_id=document.id,
            quote="it lowers mTOR",
            claim_text="Rapamycin lowers mTOR",
            stance="hedges",
            grounded=False,
        )
        await session.commit()

        resp = await admin_client.get(f"/admin/inquiries/{inquiry.id}/experts/{thinker.id}/observations")
        assert resp.status_code == 200
        assert "rapamycin extends lifespan in mice" in resp.text
        assert "Longevity Podcast #12" in resp.text
        assert "example.com" in resp.text
        assert "said 2026-02-14" in resp.text
        assert "grounded" in resp.text
        assert "unverified quote" in resp.text

    async def test_receipts_empty_state(self, admin_client, session: AsyncSession):
        inquiry = await create_inquiry(session, question="Q?")
        thinker = await create_thinker(session)
        await session.commit()
        resp = await admin_client.get(f"/admin/inquiries/{inquiry.id}/experts/{thinker.id}/observations")
        assert resp.status_code == 200
        assert "No stored observations" in resp.text
