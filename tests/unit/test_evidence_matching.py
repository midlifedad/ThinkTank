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
