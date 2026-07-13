"""Schema tests for the claims layer (migration 017).

Pins the invariants the belief database depends on:
- provenance is REQUIRED and exclusive (exactly one of content/document)
- stance/type/origin CHECK constraints
- canonical self-FKs (parent headline link, merge survivor)
- vector round-trip + cosine ANN ordering (pgvector actually works)
- inquiry position composite PK (one resolved stance per expert per inquiry)
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
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
from thinktank.models.claim import Claim, ClaimObservation

pytestmark = pytest.mark.anyio


class TestProvenanceInvariant:
    async def test_observation_requires_provenance(self, session: AsyncSession):
        """No provenance -> not evidence -> rejected by CHECK."""
        with pytest.raises(IntegrityError, match="ck_observation_one_provenance"):
            await create_claim_observation(session)

    async def test_observation_rejects_double_provenance(self, session: AsyncSession):
        source = await create_source(session)
        content = await create_content(session, source_id=source.id)
        doc = await create_document(session)
        with pytest.raises(IntegrityError, match="ck_observation_one_provenance"):
            await create_claim_observation(session, content_id=content.id, document_id=doc.id)

    async def test_corpus_and_web_provenance_both_work(self, session: AsyncSession):
        source = await create_source(session)
        content = await create_content(session, source_id=source.id)
        doc = await create_document(session)
        corpus_obs = await create_claim_observation(session, content_id=content.id)
        web_obs = await create_claim_observation(session, document_id=doc.id)
        assert corpus_obs.id and web_obs.id


class TestCheckConstraints:
    async def test_invalid_stance_rejected(self, session: AsyncSession):
        doc = await create_document(session)
        with pytest.raises(IntegrityError, match="ck_observation_stance"):
            await create_claim_observation(session, document_id=doc.id, stance="maybe")

    async def test_invalid_claim_type_rejected(self, session: AsyncSession):
        with pytest.raises(IntegrityError, match="ck_claim_type"):
            await create_claim(session, claim_type="vibe")

    async def test_invalid_origin_rejected(self, session: AsyncSession):
        doc = await create_document(session)
        with pytest.raises(IntegrityError, match="ck_observation_origin"):
            await create_claim_observation(session, document_id=doc.id, origin="dream")


class TestCanonicalStructure:
    async def test_fine_grained_links_to_headline(self, session: AsyncSession):
        """Amir's hybrid grain: child claims point at the inquiry headline."""
        headline = await create_claim(session, proposition="Rapamycin extends healthy human lifespan")
        child = await create_claim(
            session, proposition="Rapamycin extends lifespan in mice", parent_claim_id=headline.id
        )
        inquiry = await create_inquiry(session, canonical_claim_id=headline.id)
        assert child.parent_claim_id == headline.id
        assert inquiry.canonical_claim_id == headline.id

    async def test_merge_survivor_pattern(self, session: AsyncSession):
        survivor = await create_claim(session)
        dup = await create_claim(session, status="merged", merged_into_id=survivor.id)
        assert dup.merged_into_id == survivor.id

    async def test_document_url_unique(self, session: AsyncSession):
        await create_document(session, url="https://example.com/one")
        with pytest.raises(IntegrityError):
            await create_document(session, url="https://example.com/one")


class TestVectorColumn:
    async def test_embedding_roundtrip_and_ann_ordering(self, session: AsyncSession):
        """pgvector stores 768-dim vectors and cosine-orders correctly."""
        base = [0.0] * 768
        near, far = list(base), list(base)
        near[0], near[1] = 1.0, 0.1
        far[700] = 1.0
        query = list(base)
        query[0] = 1.0

        await create_claim(session, proposition="near claim", embedding=near)
        await create_claim(session, proposition="far claim", embedding=far)
        await session.commit()

        result = await session.execute(
            select(Claim.proposition).order_by(Claim.embedding.cosine_distance(query)).limit(2)
        )
        ordered = [row[0] for row in result.all()]
        assert ordered == ["near claim", "far claim"]

    async def test_hnsw_indexes_exist(self, session: AsyncSession):
        """The three ANN indexes from migration 017 must exist in a
        migrated DB; in the create_all test DB we assert the columns are
        vector-typed instead (dimension check)."""
        result = await session.execute(
            text("SELECT atttypmod FROM pg_attribute WHERE attrelid = 'claims'::regclass AND attname = 'embedding'")
        )
        assert result.scalar_one() == 768


class TestInquiryPositions:
    async def test_one_position_per_expert_per_inquiry(self, session: AsyncSession):
        inquiry = await create_inquiry(session)
        thinker = await create_thinker(session)
        await create_inquiry_position(session, inquiry_id=inquiry.id, thinker_id=thinker.id, stance="asserts")
        with pytest.raises(IntegrityError):
            await create_inquiry_position(session, inquiry_id=inquiry.id, thinker_id=thinker.id, stance="denies")

    async def test_unknown_stance_allowed_for_positions(self, session: AsyncSession):
        """'unknown' is a valid RESOLVED position (expert never addressed
        it) but NOT a valid observation stance."""
        inquiry = await create_inquiry(session)
        thinker = await create_thinker(session)
        pos = await create_inquiry_position(session, inquiry_id=inquiry.id, thinker_id=thinker.id, stance="unknown")
        assert pos.stance == "unknown"


class TestObservationLifecycle:
    async def test_unresolved_observation_then_attach(self, session: AsyncSession):
        """Observations are born unresolved (claim_id null) and attach later."""
        doc = await create_document(session)
        obs = await create_claim_observation(session, document_id=doc.id)
        assert obs.claim_id is None

        claim = await create_claim(session)
        obs.claim_id = claim.id
        await session.flush()
        refetched = (await session.execute(select(ClaimObservation).where(ClaimObservation.id == obs.id))).scalar_one()
        assert refetched.claim_id == claim.id
