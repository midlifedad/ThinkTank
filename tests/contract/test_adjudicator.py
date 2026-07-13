"""Contract tests for the LLM adjudicator and its vet_candidate wiring.

The adjudicator adds LLM judgment only on ambiguity. These tests pin:
- resolve_entity picks / rejects an option and records cost
- review_rejection verdict, and fail-toward-deterministic on LLM error
- vet_candidate fires the rejection review ONLY on the strong-claim +
  empty-evidence pattern, and overturns a suspicious auto-reject to
  pending_human
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_candidate_thinker, create_job
from thinktank.discovery.adjudicator import EntityChoice, RejectionVerdict, resolve_entity, review_rejection
from thinktank.handlers.vet_candidate import _rejection_is_suspicious, handle_vet_candidate
from thinktank.llm.client import LLMUsage
from thinktank.models.api_usage import ApiUsage

pytestmark = pytest.mark.anyio

USAGE = LLMUsage(input_tokens=300, output_tokens=60)


class TestSuspiciousTrigger:
    def test_strong_claim_empty_evidence_is_suspicious(self):
        assert _rejection_is_suspicious(
            {"basis": "Defined the field"},
            {"openalex": {"found": False}, "wikidata": {"found": False}},
        )

    def test_no_seed_basis_never_suspicious(self):
        assert not _rejection_is_suspicious({}, {"openalex": {"found": False}, "wikidata": {"found": False}})

    def test_any_evidence_found_not_suspicious(self):
        assert not _rejection_is_suspicious({"basis": "x"}, {"openalex": {"found": True}, "wikidata": {"found": False}})


class TestResolveEntity:
    async def test_picks_option_and_records_cost(self, session: AsyncSession):
        session.add = MagicMock()
        choice = EntityChoice(choice_index=1, confidence=0.9, reasoning="institution matches")
        with patch("thinktank.discovery.adjudicator._client") as client:
            client.review = AsyncMock(return_value=(choice, USAGE, 100))
            idx, meta = await resolve_entity(
                session,
                "Jane Expert",
                "genomics",
                "Landmark work",
                "MIT",
                "OpenAlex author",
                [{"name": "Jane Expert", "institution": "State U"}, {"name": "Jane Expert", "institution": "MIT"}],
            )
        assert idx == 1 and meta["adjudicated"]
        row = session.add.call_args.args[0]
        assert isinstance(row, ApiUsage) and row.endpoint == "adjudicator"

    async def test_out_of_range_choice_becomes_none(self, session: AsyncSession):
        session.add = MagicMock()
        choice = EntityChoice(choice_index=9, confidence=0.5, reasoning="?")
        with patch("thinktank.discovery.adjudicator._client") as client:
            client.review = AsyncMock(return_value=(choice, USAGE, 100))
            idx, _ = await resolve_entity(session, "X", "y", None, None, "src", [{"name": "X"}])
        assert idx is None

    async def test_llm_failure_returns_none(self, session: AsyncSession):
        with patch("thinktank.discovery.adjudicator._client") as client:
            client.review = AsyncMock(side_effect=RuntimeError("api down"))
            idx, meta = await resolve_entity(session, "X", "y", None, None, "src", [{"name": "X"}])
        assert idx is None and "error" in meta


class TestReviewRejection:
    async def test_illegitimate_rejection_flagged(self, session: AsyncSession):
        session.add = MagicMock()
        verdict = RejectionVerdict(legitimate=False, reasoning="claimed authority, empty evidence = lookup failure")
        with patch("thinktank.discovery.adjudicator._client") as client:
            client.review = AsyncMock(return_value=(verdict, USAGE, 100))
            legit, meta = await review_rejection(
                session,
                "Belmonte",
                "longevity",
                "Reprogramming pioneer",
                {"openalex": {"found": False}, "wikidata": {"found": False}},
                15,
            )
        assert legit is False and meta["adjudicated"]

    async def test_llm_failure_keeps_rejection(self, session: AsyncSession):
        """Fail toward the deterministic decision -- never silently promote."""
        with patch("thinktank.discovery.adjudicator._client") as client:
            client.review = AsyncMock(side_effect=RuntimeError("down"))
            legit, _ = await review_rejection(session, "X", "y", "z", {}, 10)
        assert legit is True


class TestVetCandidateRejectionOverturn:
    async def _vet(self, session, candidate, dossier):
        job = await create_job(session, job_type="vet_candidate", payload={"candidate_id": str(candidate.id)})
        with patch("thinktank.handlers.vet_candidate.gather_evidence", new_callable=AsyncMock, return_value=dossier):
            await handle_vet_candidate(session, job)

    async def test_suspicious_reject_overturned_to_human(self, session: AsyncSession):
        candidate = await create_candidate_thinker(
            session,
            name="Izpisua Belmonte",
            normalized_name="izpisua belmonte",
            status="pending_llm",
            search_area="longevity",
            evidence={"seed_claim": {"basis": "Reprogramming pioneer at Salk"}},
        )
        empty = {
            "openalex": {"ok": True, "found": False},
            "wikidata": {"ok": True, "found": False},
            "openlibrary": {"ok": True, "found": False},
            "podcastindex": {"ok": True, "found": False},
            "youtube": {"ok": True, "checked": False},
            "substack": {"ok": True, "checked": False},
        }
        with patch(
            "thinktank.handlers.vet_candidate.review_rejection",
            new_callable=AsyncMock,
            return_value=(False, {"adjudicated": True}),
        ):
            await self._vet(session, candidate, empty)

        await session.refresh(candidate)
        assert candidate.status == "pending_human"
        assert candidate.evidence["rejection_review"]["adjudicated"]

    async def test_legitimate_weak_candidate_stays_rejected(self, session: AsyncSession):
        """No seed basis -> review never fires, deterministic reject stands."""
        candidate = await create_candidate_thinker(
            session, name="Nobody", normalized_name="nobody", status="pending_llm", search_area="longevity"
        )
        empty = {
            "openalex": {"ok": True, "found": False},
            "wikidata": {"ok": True, "found": False},
            "openlibrary": {"ok": True, "found": False},
            "podcastindex": {"ok": True, "found": False},
            "youtube": {"ok": True, "checked": False},
            "substack": {"ok": True, "checked": False},
        }
        with patch("thinktank.handlers.vet_candidate.review_rejection", new_callable=AsyncMock) as rr:
            await self._vet(session, candidate, empty)
            rr.assert_not_called()

        await session.refresh(candidate)
        assert candidate.status == "auto_rejected"
