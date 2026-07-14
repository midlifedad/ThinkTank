"""Contract tests for the inquiry engine (run_inquiry handler).

LLM calls, embeddings, Perplexity, and page fetches are mocked at the
handler's imports; the contract under test is the pipeline itself:
headline claim creation, roster resolution, both evidence lanes,
hard grounding with offsets, canonical resolution with the parent link,
the REQUIRED per-expert position, retry idempotency, and completion.
"""

import uuid
from contextlib import ExitStack
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    create_candidate_thinker,
    create_content,
    create_content_chunk,
    create_content_thinker,
    create_document,
    create_inquiry,
    create_job,
    create_source,
    create_thinker,
)
from thinktank.discovery.exa_client import ExaResult
from thinktank.handlers.run_inquiry import handle_run_inquiry
from thinktank.llm.claims_extraction import ExtractedClaim, PositionResponse, Proposition
from thinktank.models.claim import Claim, ClaimObservation, Inquiry, InquiryPosition

pytestmark = pytest.mark.anyio

QUESTION = "Does rapamycin extend healthy human lifespan?"
CORPUS_QUOTE = "Rapamycin extends lifespan in mice by twenty five percent"
BODY = f"Speaker A: {CORPUS_QUOTE} and that finding replicated. Speaker B: Fascinating."
WEB_QUOTE = "rapamycin lowers mTOR activity in humans"
WEB_TEXT = f"In a recent interview, the researcher noted that {WEB_QUOTE} at tolerable doses."
PUBLISHED = datetime(2026, 2, 14, tzinfo=UTC)

CORPUS_CLAIM = ExtractedClaim(
    claim_text="Rapamycin extends lifespan in mice",
    claim_type="factual",
    stance_on_question="asserts",
    confidence="asserted",
    quote=CORPUS_QUOTE,
    topics=["rapamycin"],
)
WEB_CLAIM = ExtractedClaim(
    claim_text="Rapamycin lowers mTOR activity in humans",
    claim_type="factual",
    stance_on_question="hedges",
    confidence="speculated",
    quote=WEB_QUOTE,
)


def _embedder():
    """Deterministic per-text basis vectors (all texts mutually orthogonal)."""
    seen: dict[str, int] = {}

    async def _embed(texts):
        out = []
        for t in texts:
            index = seen.setdefault(t, len(seen))
            v = [0.0] * 768
            v[index] = 1.0
            out.append(v)
        return out

    return AsyncMock(side_effect=_embed)


def _extractor():
    async def _extract(session, question, expert_name, evidence_text, evidence_kind):
        if evidence_kind == "podcast transcript":
            return [CORPUS_CLAIM], 0
        return [WEB_CLAIM], 0

    return AsyncMock(side_effect=_extract)


def _mocks(session, *, exa_results=None, document=None, extractor=None):
    async def _store(session_, result, *, found_via, search_query=None):
        return document

    return (
        patch("thinktank.handlers.run_inquiry.embed_texts", new=_embedder()),
        patch(
            "thinktank.handlers.run_inquiry.propositionize",
            new=AsyncMock(
                return_value=Proposition(proposition="Rapamycin extends healthy human lifespan", claim_type="factual")
            ),
        ),
        patch("thinktank.handlers.run_inquiry.extract_observations", new=extractor or _extractor()),
        patch(
            "thinktank.handlers.run_inquiry.resolve_position",
            new=AsyncMock(return_value=PositionResponse(stance="asserts", summary="Supports rapamycin for longevity.")),
        ),
        patch("thinktank.handlers.run_inquiry.exa_search", new=AsyncMock(return_value=exa_results or [])),
        patch("thinktank.handlers.run_inquiry.store_exa_result", new=AsyncMock(side_effect=_store)),
        patch("thinktank.handlers.run_inquiry.fetch_document", new=AsyncMock(return_value=None)),
    )


async def _setup_expert_with_corpus(session: AsyncSession, area="Age reversal/longevity"):
    thinker = await create_thinker(session, name="Dr. Test Expert")
    await create_candidate_thinker(
        session, name=thinker.name, status="promoted", thinker_id=thinker.id, search_area=area
    )
    source = await create_source(session)
    content = await create_content(session, source_id=source.id, status="done", body_text=BODY, published_at=PUBLISHED)
    await create_content_thinker(session, content_id=content.id, thinker_id=thinker.id)
    await create_content_chunk(
        session,
        content_id=content.id,
        chunk_index=0,
        text=BODY,
        char_start=0,
        char_end=len(BODY),
        embedding=[1.0] + [0.0] * 767,
    )
    return thinker, content


async def _run(session: AsyncSession, inquiry: Inquiry, **mock_kwargs) -> None:
    job = await create_job(session, job_type="run_inquiry", payload={"inquiry_id": str(inquiry.id)})
    with ExitStack() as stack:
        for m in _mocks(session, **mock_kwargs):
            stack.enter_context(m)
        await handle_run_inquiry(session, job)


class TestRunInquiry:
    async def test_full_flow_both_lanes(self, session: AsyncSession):
        thinker, content = await _setup_expert_with_corpus(session)
        document = await create_document(session, url="https://example.com/interview", text_content=WEB_TEXT)
        inquiry = await create_inquiry(session, question=QUESTION, area="longevity")

        exa_hit = ExaResult(url=document.url, title="Interview", text=WEB_TEXT, published_at=None, author=None)
        await _run(session, inquiry, exa_results=[exa_hit], document=document)

        # Inquiry completed with a headline canonical claim.
        assert inquiry.status == "complete"
        assert inquiry.completed_at is not None
        headline = await session.get(Claim, inquiry.canonical_claim_id)
        assert headline.proposition == "Rapamycin extends healthy human lifespan"

        observations = (
            (await session.execute(select(ClaimObservation).where(ClaimObservation.inquiry_id == inquiry.id)))
            .scalars()
            .all()
        )
        assert len(observations) == 2
        by_origin = {("content" if o.content_id else "document"): o for o in observations}

        corpus_obs = by_origin["content"]
        assert corpus_obs.content_id == content.id and corpus_obs.document_id is None
        assert corpus_obs.grounded is True
        assert BODY[corpus_obs.quote_start : corpus_obs.quote_end] == CORPUS_QUOTE
        assert corpus_obs.asserted_at == PUBLISHED
        assert corpus_obs.stance == "asserts"

        web_obs = by_origin["document"]
        assert web_obs.document_id == document.id and web_obs.content_id is None
        assert web_obs.grounded is True
        assert web_obs.stance == "hedges"

        # Every observation resolved onto a canonical claim parented to the headline.
        for obs in observations:
            assert obs.claim_id is not None
            canonical = await session.get(Claim, obs.claim_id)
            assert canonical.parent_claim_id == inquiry.canonical_claim_id

        position = await session.get(InquiryPosition, (inquiry.id, thinker.id))
        assert position.stance == "asserts"
        assert position.observation_count == 2

    async def test_rerun_skips_resolved_experts(self, session: AsyncSession):
        await _setup_expert_with_corpus(session)
        inquiry = await create_inquiry(session, question=QUESTION, area="longevity")
        await _run(session, inquiry)

        extractor = _extractor()
        await _run(session, inquiry, extractor=extractor)

        extractor.assert_not_awaited()
        observations = (
            (await session.execute(select(ClaimObservation).where(ClaimObservation.inquiry_id == inquiry.id)))
            .scalars()
            .all()
        )
        assert len(observations) == 1  # corpus lane only (no web mock) -- not duplicated

    async def test_empty_roster_completes(self, session: AsyncSession):
        inquiry = await create_inquiry(session, question=QUESTION, area="nonexistent-area")
        await _run(session, inquiry)
        assert inquiry.status == "complete"
        positions = (await session.execute(select(InquiryPosition))).scalars().all()
        assert positions == []

    async def test_no_evidence_yields_unknown_position(self, session: AsyncSession):
        """resolve_position is NOT mocked here: with zero observations it
        must resolve 'unknown' without an LLM call."""
        thinker = await create_thinker(session, name="Dr. Silent")
        await create_candidate_thinker(
            session, name=thinker.name, status="promoted", thinker_id=thinker.id, search_area="longevity"
        )
        inquiry = await create_inquiry(session, question=QUESTION, area="longevity")

        job = await create_job(session, job_type="run_inquiry", payload={"inquiry_id": str(inquiry.id)})
        from thinktank.llm.claims_extraction import resolve_position as real_resolve

        with ExitStack() as stack:
            # All mocks except resolve_position, which runs for real (no
            # observations -> 'unknown' without an LLM call).
            for i, m in enumerate(_mocks(session)):
                if i == 3:  # the resolve_position patch
                    stack.enter_context(patch("thinktank.handlers.run_inquiry.resolve_position", new=real_resolve))
                else:
                    stack.enter_context(m)
            await handle_run_inquiry(session, job)

        position = await session.get(InquiryPosition, (inquiry.id, thinker.id))
        assert position.stance == "unknown"
        assert position.observation_count == 0

    async def test_missing_inquiry_raises(self, session: AsyncSession):
        job = await create_job(session, job_type="run_inquiry", payload={"inquiry_id": str(uuid.uuid4())})
        with pytest.raises(ValueError, match="not found"):
            await handle_run_inquiry(session, job)


class TestConcurrencyGuard:
    async def test_second_job_skips_while_lock_held(self, session: AsyncSession):
        """A duplicate run_inquiry job (stale-reclaim race) must skip
        cleanly while another holds the per-inquiry advisory lock --
        no extraction, no positions, no corrupted double observations."""
        from sqlalchemy import text

        from thinktank.queue.leader import stable_lock_key

        await _setup_expert_with_corpus(session)
        inquiry = await create_inquiry(session, question=QUESTION, area="longevity")
        await session.commit()

        key = stable_lock_key(f"run_inquiry:{inquiry.id}")
        async with session.bind.connect() as holder:
            got = bool((await holder.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})).scalar_one())
            assert got  # we simulate the "other" running job holding the lock
            try:
                extractor = _extractor()
                await _run(session, inquiry, extractor=extractor)
                extractor.assert_not_awaited()  # handler skipped before any work
            finally:
                await holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                await holder.commit()

        positions = (
            (await session.execute(select(InquiryPosition).where(InquiryPosition.inquiry_id == inquiry.id)))
            .scalars()
            .all()
        )
        assert positions == []

    async def test_lock_released_allows_subsequent_run(self, session: AsyncSession):
        """Once the lock is free, a run proceeds normally -- the guard
        doesn't wedge the inquiry."""
        thinker, _ = await _setup_expert_with_corpus(session)
        inquiry = await create_inquiry(session, question=QUESTION, area="longevity")
        await _run(session, inquiry)
        position = await session.get(InquiryPosition, (inquiry.id, thinker.id))
        assert position is not None


class TestPerExpertResilience:
    async def test_one_expert_failure_does_not_sink_the_rest(self, session: AsyncSession):
        """A transient failure on one expert rolls back only that expert
        and leaves no position for them; every other expert still resolves
        and the inquiry completes."""
        boom = await create_thinker(session, name="Dr. Boom")
        fine = await create_thinker(session, name="Dr. Fine")
        source = await create_source(session)
        for t in (boom, fine):
            await create_candidate_thinker(
                session, name=t.name, status="promoted", thinker_id=t.id, search_area="longevity"
            )
            content = await create_content(session, source_id=source.id, status="done", body_text=BODY)
            await create_content_thinker(session, content_id=content.id, thinker_id=t.id)
            await create_content_chunk(
                session,
                content_id=content.id,
                chunk_index=0,
                text=BODY,
                char_start=0,
                char_end=len(BODY),
                embedding=[1.0] + [0.0] * 767,
            )
        inquiry = await create_inquiry(session, question=QUESTION, area="longevity")
        # Capture PKs: the handler shares this test's session and its internal
        # per-expert rollback expires these ORM objects (in production the
        # worker session is dedicated, so this is a test-only concern).
        inquiry_id, boom_id, fine_id = inquiry.id, boom.id, fine.id

        async def _extract(session_, question, expert_name, evidence_text, evidence_kind):
            if expert_name == "Dr. Boom":
                raise RuntimeError("transient blip")
            return [CORPUS_CLAIM], 0

        await _run(session, inquiry, extractor=AsyncMock(side_effect=_extract))

        refreshed = await session.get(Inquiry, inquiry_id)
        assert refreshed.status == "complete"
        assert await session.get(InquiryPosition, (inquiry_id, fine_id)) is not None
        assert await session.get(InquiryPosition, (inquiry_id, boom_id)) is None
        # Boom's rollback left no orphan observations.
        boom_obs = (
            (await session.execute(select(ClaimObservation).where(ClaimObservation.thinker_id == boom_id)))
            .scalars()
            .all()
        )
        assert boom_obs == []
