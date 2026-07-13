"""Unit tests for the LLM domain-fit assessment (fail-open contract)."""

from unittest.mock import AsyncMock, patch

import pytest

from thinktank.discovery.domain_fit import DomainFitAssessment, _dossier_facts, assess_domain_fit
from thinktank.llm.client import LLMUsage

pytestmark = pytest.mark.anyio


def _usage():
    return LLMUsage(input_tokens=200, output_tokens=80)


class TestAssessDomainFit:
    async def test_returns_storable_dict(self):
        verdict = DomainFitAssessment(
            centrality="core",
            fit_score=18,
            reasoning="Authored the canonical agent-systems essays.",
        )
        with (
            patch(
                "thinktank.discovery.domain_fit._client.review",
                new=AsyncMock(return_value=(verdict, _usage(), 10)),
            ),
            patch("thinktank.discovery.domain_fit._record_cost", new=AsyncMock()),
        ):
            fit = await assess_domain_fit(None, "Lilian Weng", "AI coding and agentic engineering", {})

        assert fit["centrality"] == "core"
        assert fit["fit_score"] == 18
        assert "canonical" in fit["reasoning"]
        assert fit["assessed_at"]  # ISO timestamp for the standing time-series later

    async def test_fail_open_returns_none(self):
        """Any LLM failure leaves vetting exactly as it was: no fit, no crash."""
        with patch(
            "thinktank.discovery.domain_fit._client.review",
            new=AsyncMock(side_effect=RuntimeError("api down")),
        ):
            fit = await assess_domain_fit(None, "Anyone", "any area", {})
        assert fit is None


class TestDossierFacts:
    def test_compacts_key_evidence(self):
        dossier = {
            "seed_claim": {"basis": "Created BabyAGI", "affiliation": "Untapped VC"},
            "wikidata": {"description": "Japanese-American entrepreneur"},
            "openalex": {"found": True, "h_index": 3, "works_count": 5, "topics": ["AI agents"]},
            "openlibrary": {"books": [{"title": "Agents at Work"}]},
            "podcastindex": {"items": [{"title": "Ep 12: BabyAGI and beyond"}]},
        }
        facts = _dossier_facts(dossier)
        assert "Created BabyAGI" in facts
        assert "Japanese-American entrepreneur" in facts
        assert "Agents at Work" in facts
        assert "BabyAGI and beyond" in facts

    def test_empty_dossier(self):
        assert _dossier_facts({}) == "(no structured evidence)"
