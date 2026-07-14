"""Unit tests for OpenAlex paper fetching (W3.2)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from thinktank.discovery.openalex_papers import _reconstruct_abstract, fetch_author_papers

pytestmark = pytest.mark.anyio


class TestReconstructAbstract:
    def test_rebuilds_in_order(self):
        inverted = {"Rapamycin": [0], "extends": [1], "lifespan": [2, 5], "in": [3], "mice": [4], "greatly": [6]}
        assert _reconstruct_abstract(inverted) == "Rapamycin extends lifespan in mice lifespan greatly"

    def test_empty(self):
        assert _reconstruct_abstract(None) == ""
        assert _reconstruct_abstract({}) == ""


def _resp(json_body):
    r = MagicMock()
    r.json.return_value = json_body
    r.raise_for_status = MagicMock()
    return r


def _client_returning(*responses):
    client = MagicMock()
    client.__aenter__.return_value.get = AsyncMock(side_effect=list(responses))
    return client


class TestFetchAuthorPapers:
    async def test_resolves_author_then_works(self):
        author_resp = _resp({"results": [{"id": "https://openalex.org/A123"}]})
        works_resp = _resp(
            {
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "title": "Rapamycin and aging",
                        "publication_date": "2023-05-01",
                        "doi": "https://doi.org/10.1/x",
                        # >200 chars so it clears the abstract floor
                        "abstract_inverted_index": {f"word{i}": [i] for i in range(50)},
                    },
                    {"id": "https://openalex.org/W2", "title": "No abstract paper", "abstract_inverted_index": None},
                ]
            }
        )
        with patch(
            "thinktank.discovery.openalex_papers.httpx.AsyncClient",
            return_value=_client_returning(author_resp, works_resp),
        ):
            papers = await fetch_author_papers("Dr. Test")

        assert len(papers) == 1  # the no-abstract paper is skipped
        p = papers[0]
        assert p.openalex_id == "W1"
        assert p.abstract == " ".join(f"word{i}" for i in range(50))
        assert p.published_at.year == 2023
        assert p.landing_url == "https://doi.org/10.1/x"

    async def test_no_author_match_returns_empty(self):
        with patch(
            "thinktank.discovery.openalex_papers.httpx.AsyncClient",
            return_value=_client_returning(_resp({"results": []})),
        ):
            assert await fetch_author_papers("Nobody") == []

    async def test_http_failure_degrades(self):
        client = MagicMock()
        client.__aenter__.return_value.get = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("thinktank.discovery.openalex_papers.httpx.AsyncClient", return_value=client):
            assert await fetch_author_papers("Dr. Test") == []


class TestNormalizeTitle:
    def test_collapses_version_and_editorial_variants(self):
        from thinktank.discovery.openalex_papers import normalize_title

        base = "A mathematical model that predicts human biological age"
        variants = [base, "Author response: " + base, "Correction: " + base, base.upper() + "!!!"]
        assert len({normalize_title(v) for v in variants}) == 1


class TestDedupAndFloor:
    async def test_dedupes_versions_and_drops_stub_abstracts(self):
        from unittest.mock import MagicMock, patch

        from thinktank.discovery.openalex_papers import fetch_author_papers

        long_abs = {f"word{i}": [i] for i in range(50)}  # >200 chars reconstructed
        short_abs = {"tiny": [0]}
        author_resp = _resp({"results": [{"id": "https://openalex.org/A1"}]})
        works_resp = _resp(
            {
                "results": [
                    # same work, 3 versions -> 1 kept (richest abstract)
                    {
                        "id": "https://openalex.org/W1",
                        "title": "Rapamycin and aging",
                        "abstract_inverted_index": {"a": [0]},
                    },
                    {
                        "id": "https://openalex.org/W1v2",
                        "title": "Rapamycin and aging",
                        "abstract_inverted_index": long_abs,
                    },
                    {
                        "id": "https://openalex.org/W1v3",
                        "title": "Author response: Rapamycin and aging",
                        "abstract_inverted_index": {"b": [0]},
                    },
                    # a distinct paper with a stub abstract -> dropped
                    {"id": "https://openalex.org/W2", "title": "Short note", "abstract_inverted_index": short_abs},
                ]
            }
        )
        client = MagicMock()
        client.__aenter__.return_value.get = AsyncMock(side_effect=[author_resp, works_resp])
        with patch("thinktank.discovery.openalex_papers.httpx.AsyncClient", return_value=client):
            papers = await fetch_author_papers("Dr. Test")

        assert len(papers) == 1  # one deduped work, stub dropped
        assert papers[0].abstract == " ".join(f"word{i}" for i in range(50))  # richest kept
