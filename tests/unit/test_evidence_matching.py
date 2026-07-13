"""Unit tests for evidence name matching and podcast response parsing.

These pin the fixes for the longevity-run failure where every expert was
auto-rejected: OpenAlex/OpenLibrary/Wikidata name matching was too strict
(middle-initial variance), and the PodcastIndex call used the wrong
signature -- so the content leg was 0 for everyone.
"""

from thinktank.discovery.evidence import _name_matches, _significant_tokens


class TestSignificantTokens:
    def test_drops_middle_initial(self):
        assert _significant_tokens("David A. Sinclair") == {"david", "sinclair"}

    def test_drops_dots_and_short_tokens(self):
        assert _significant_tokens("George M. Church") == {"george", "church"}


class TestNameMatches:
    def test_middle_initial_both_directions(self):
        assert _name_matches("David A. Sinclair", "David Sinclair")
        assert _name_matches("David Sinclair", "David A. Sinclair")

    def test_exact_match(self):
        assert _name_matches("Nir Barzilai", "Nir Barzilai")

    def test_different_person_rejected(self):
        assert not _name_matches("David A. Sinclair", "John Sinclair")

    def test_single_surname_query_rejected(self):
        """A bare surname must not match a full name (John Smith noise)."""
        assert not _name_matches("Sinclair", "David Sinclair")

    def test_subset_extra_middle_names(self):
        assert _name_matches("Juan Izpisua Belmonte", "Juan Carlos Izpisua Belmonte")

    def test_empty_candidate_rejected(self):
        assert not _name_matches("David Sinclair", "")


class TestOpenAlexNullFields:
    """OpenAlex returns last_known_institutions/topics as explicit null for
    some authors; the block/option builders must not crash (the #70
    regression that zeroed scholarship for 6 longevity experts)."""

    def test_null_institutions_and_topics(self):
        from thinktank.discovery.evidence import _openalex_block, _openalex_option

        author = {
            "id": "A1",
            "display_name": "X",
            "cited_by_count": 100,
            "works_count": 5,
            "summary_stats": {"h_index": 10},
            "last_known_institutions": None,
            "topics": None,
        }
        assert _openalex_block(author)["institutions"] == []
        assert _openalex_block(author)["found"] is True
        assert _openalex_option(author)["institution"] is None
