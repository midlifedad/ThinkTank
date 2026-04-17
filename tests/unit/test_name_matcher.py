"""Unit tests for thinker name matching in text.

Tests content attribution logic per spec Section 6.6.
"""

import uuid

from thinktank.ingestion.name_matcher import match_thinkers_in_text


def _thinker(name: str) -> dict:
    """Helper to create a thinker dict for testing."""
    return {"id": uuid.uuid4(), "name": name}


class TestExactMatching:
    def test_exact_match_in_title(self):
        thinker = _thinker("John Smith")
        results = match_thinkers_in_text(
            title="Interview with John Smith",
            description="A great conversation.",
            thinker_names=[thinker],
            source_owner_name=None,
        )
        matches = [r for r in results if r["thinker_id"] == thinker["id"]]
        assert len(matches) == 1
        assert matches[0]["confidence"] == 9
        assert matches[0]["role"] == "guest"

    def test_exact_match_in_description(self):
        thinker = _thinker("Jane Doe")
        results = match_thinkers_in_text(
            title="A Great Episode",
            description="Featuring Jane Doe on AI topics.",
            thinker_names=[thinker],
            source_owner_name=None,
        )
        matches = [r for r in results if r["thinker_id"] == thinker["id"]]
        assert len(matches) == 1
        assert matches[0]["confidence"] == 6
        assert matches[0]["role"] == "guest"

    def test_no_match(self):
        thinker = _thinker("Nobody Here")
        results = match_thinkers_in_text(
            title="Random Episode Title",
            description="Nothing relevant in this description.",
            thinker_names=[thinker],
            source_owner_name=None,
        )
        assert len(results) == 0


class TestCaseInsensitive:
    def test_case_insensitive(self):
        thinker = _thinker("John Smith")
        results = match_thinkers_in_text(
            title="JOHN SMITH joins the show",
            description="",
            thinker_names=[thinker],
            source_owner_name=None,
        )
        matches = [r for r in results if r["thinker_id"] == thinker["id"]]
        assert len(matches) == 1


class TestMultipleMatches:
    def test_multiple_matches(self):
        t1 = _thinker("Alice Johnson")
        t2 = _thinker("Bob Williams")
        results = match_thinkers_in_text(
            title="Alice Johnson and Bob Williams discuss AI",
            description="",
            thinker_names=[t1, t2],
            source_owner_name=None,
        )
        ids = {r["thinker_id"] for r in results}
        assert t1["id"] in ids
        assert t2["id"] in ids


class TestSourceOwner:
    def test_source_owner_tagged_primary(self):
        owner = _thinker("Source Owner")
        results = match_thinkers_in_text(
            title="Random Episode",
            description="Random description",
            thinker_names=[owner],
            source_owner_name="Source Owner",
        )
        owner_matches = [r for r in results if r["thinker_id"] == owner["id"] and r["role"] == "primary"]
        assert len(owner_matches) == 1
        assert owner_matches[0]["confidence"] == 10


class TestPartialNameNotMatched:
    def test_partial_name_not_matched(self):
        """'John' alone should NOT match 'John Smith' (full name required)."""
        thinker = _thinker("John Smith")
        results = match_thinkers_in_text(
            title="John went to the store",
            description="",
            thinker_names=[thinker],
            source_owner_name=None,
        )
        assert len(results) == 0

    def test_word_boundary_rejects_substring_title(self):
        """'Sam Harris' must NOT match 'Scam Harrison' (ME-01 false-positive)."""
        thinker = _thinker("Sam Harris")
        results = match_thinkers_in_text(
            title="Scam Harrison investigates podcast fraud",
            description="",
            thinker_names=[thinker],
            source_owner_name=None,
        )
        assert len(results) == 0

    def test_word_boundary_rejects_substring_description(self):
        """Substring appearing inside another word in description -> no match."""
        thinker = _thinker("Dan Carlin")
        results = match_thinkers_in_text(
            title="",
            description="Dangerfield Carlington hosts the episode",
            thinker_names=[thinker],
            source_owner_name=None,
        )
        assert len(results) == 0

    def test_word_boundary_still_matches_adjacent_punctuation(self):
        """Name followed by punctuation (comma, period, colon) still matches."""
        thinker = _thinker("Sam Harris")
        results = match_thinkers_in_text(
            title="Guest: Sam Harris, on meditation.",
            description="",
            thinker_names=[thinker],
            source_owner_name=None,
        )
        assert len(results) == 1
        assert results[0]["role"] == "guest"
        assert results[0]["confidence"] == 9


class TestTitleMatchTakesPrecedence:
    def test_title_match_over_description(self):
        """If a thinker is found in both title and description, title match (confidence 9) wins."""
        thinker = _thinker("John Smith")
        results = match_thinkers_in_text(
            title="Interview with John Smith",
            description="John Smith talks about AI.",
            thinker_names=[thinker],
            source_owner_name=None,
        )
        matches = [r for r in results if r["thinker_id"] == thinker["id"]]
        # Should only have one entry, with the higher confidence
        assert len(matches) == 1
        assert matches[0]["confidence"] == 9
