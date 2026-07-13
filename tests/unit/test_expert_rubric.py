"""Unit tests for the expert qualification rubric.

The rubric IS the "not just anyone" gate policy -- every archetype that
must pass or fail is pinned here.
"""

from thinktank.discovery.rubric import GateThresholds, gate_decision, score_dossier

T = GateThresholds()  # code defaults


def _dossier(
    h_index=0,
    citations=0,
    enwiki=False,
    sitelinks=0,
    books=0,
    podcast_feeds=0,
    youtube=False,
    substack=False,
):
    return {
        "openalex": {
            "ok": True,
            "found": h_index > 0 or citations > 0,
            "h_index": h_index,
            "cited_by_count": citations,
        },
        "wikidata": {
            "ok": True,
            "found": enwiki or sitelinks > 0,
            "has_enwiki": enwiki,
            "sitelink_count": sitelinks,
        },
        "openlibrary": {"ok": True, "found": books > 0, "work_count": books},
        "podcastindex": {"ok": True, "found": podcast_feeds > 0, "appearance_feed_count": podcast_feeds},
        "youtube": {"ok": True, "checked": youtube, "reachable": youtube},
        "substack": {"ok": True, "checked": substack, "reachable": substack},
    }


class TestArchetypes:
    def test_eminent_academic_with_content_shortlists(self):
        """h-index 55, Wikipedia, books, podcast circuit -> obvious yes."""
        total, breakdown = score_dossier(_dossier(h_index=55, enwiki=True, sitelinks=30, books=5, podcast_feeds=10))
        assert gate_decision(total, breakdown, T) == "shortlisted"

    def test_content_only_celebrity_rejected(self):
        """Huge platform, zero scholarship/notability/books -> the exact
        'not just anyone' case. High content score cannot save them."""
        total, breakdown = score_dossier(_dossier(podcast_feeds=50, youtube=True, substack=True))
        assert breakdown["qualification_legs"] == 0
        assert gate_decision(total, breakdown, T) == "auto_rejected"

    def test_contentless_academic_rejected(self):
        """Stellar citations, zero findable content -> nothing to ingest."""
        total, breakdown = score_dossier(_dossier(h_index=60, citations=80000, enwiki=True, books=4))
        assert breakdown["content"] == 0
        assert gate_decision(total, breakdown, T) == "auto_rejected"

    def test_public_intellectual_without_academia_passes(self):
        """The 'credentialed public intellectual' bar: real Wikipedia
        notability + books + podcast presence qualifies without h-index
        (Amir listed notoriety as a qualifying dimension)."""
        total, breakdown = score_dossier(_dossier(enwiki=True, sitelinks=45, books=8, podcast_feeds=12))
        assert breakdown["scholarship"] == 0
        assert breakdown["qualification_legs"] >= T.min_qualification
        assert gate_decision(total, breakdown, T) == "shortlisted"

    def test_marginal_candidate_is_borderline_not_llm(self):
        """Between floor and shortlist -> pending_human, not an LLM call.

        h-index 16 (15) + Wikipedia (12) + 3 podcasts (10) = 37: clears
        both leg minimums and the floor, misses the shortlist bar.
        """
        total, breakdown = score_dossier(_dossier(h_index=16, enwiki=True, podcast_feeds=3))
        assert T.floor <= total < T.shortlist
        assert gate_decision(total, breakdown, T) == "borderline"

    def test_nobody_rejected(self):
        total, breakdown = score_dossier(_dossier())
        assert total == 0
        assert gate_decision(total, breakdown, T) == "auto_rejected"

    def test_wikidata_entity_alone_is_not_notability(self):
        """A bare Wikidata entity (4 pts) must not clear min_qualification."""
        total, breakdown = score_dossier(_dossier(sitelinks=1, podcast_feeds=10))
        assert breakdown["notability"] == 4
        assert gate_decision(total, breakdown, T) == "auto_rejected"


class TestScoringMechanics:
    def test_citations_can_substitute_for_h_index(self):
        _, by_h = score_dossier(_dossier(h_index=50, podcast_feeds=1))
        _, by_cites = score_dossier(_dossier(citations=60000, podcast_feeds=1))
        assert by_h["scholarship"] == by_cites["scholarship"] == 30

    def test_content_capped_at_25(self):
        _, breakdown = score_dossier(_dossier(h_index=50, podcast_feeds=100, youtube=True, substack=True))
        assert breakdown["content"] == 25

    def test_peer_signal_bands(self):
        _, b0 = score_dossier(_dossier(h_index=50, podcast_feeds=5), peer_coappearances=0)
        _, b6 = score_dossier(_dossier(h_index=50, podcast_feeds=5), peer_coappearances=6)
        assert b0["peer_signal"] == 0
        assert b6["peer_signal"] == 10

    def test_failed_evidence_block_scores_zero(self):
        """{"ok": False} blocks contribute nothing rather than crashing."""
        d = _dossier(h_index=50, podcast_feeds=5)
        d["openalex"] = {"ok": False, "error": "timeout"}
        total, breakdown = score_dossier(d)
        assert breakdown["scholarship"] == 0

    def test_unreachable_hint_scores_zero(self):
        d = _dossier(h_index=50, podcast_feeds=5)
        d["youtube"] = {"ok": True, "checked": True, "reachable": False}
        _, breakdown = score_dossier(d)
        _, base = score_dossier(_dossier(h_index=50, podcast_feeds=5))
        assert breakdown["content"] == base["content"]


class TestPractitionerPath:
    """Non-academic experts: no scholarship, real notability + strong
    content -> route to the LLM judge, not auto-reject (Amir 2026-07-12)."""

    def test_practitioner_with_notability_and_content_goes_to_judge(self):
        """Scott Brinker archetype: Wikipedia + big podcast presence, no
        citations -> practitioner_review (judge decides)."""
        total, breakdown = score_dossier(_dossier(enwiki=True, podcast_feeds=10))
        assert breakdown["scholarship"] == 0
        assert gate_decision(total, breakdown, T) == "practitioner_review"

    def test_practitioner_without_any_notability_still_rejected(self):
        """Christopher Penn archetype: strong content but ZERO notability
        and zero scholarship -> still auto-rejected (content alone never
        qualifies)."""
        total, breakdown = score_dossier(_dossier(podcast_feeds=20, youtube=True))
        assert breakdown["notability"] == 0
        assert gate_decision(total, breakdown, T) == "auto_rejected"

    def test_practitioner_needs_content(self):
        """Notability but no content -> auto_rejected (nothing to ingest)."""
        total, breakdown = score_dossier(_dossier(enwiki=True))
        assert gate_decision(total, breakdown, T) == "auto_rejected"

    def test_academic_unaffected_by_practitioner_path(self):
        """Scholarship>0 candidates never hit the practitioner branch."""
        total, breakdown = score_dossier(_dossier(h_index=55, enwiki=True, podcast_feeds=10))
        assert gate_decision(total, breakdown, T) == "shortlisted"
