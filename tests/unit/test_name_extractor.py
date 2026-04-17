"""Tests for discovery.name_extractor -- regex-based person name extraction.

Covers all behavior cases from PLAN 06-01 Task 1:
- Pattern matching (with, feat., interview, name-on-topic, episode-number, pipe)
- Blocklist filtering
- Single-char word filtering
- All-caps word filtering
- Description scanning
- Deduplication across title + description
- Sorted deterministic output
"""

import pytest

from thinktank.discovery.name_extractor import extract_names


class TestExtractNamesPatterns:
    """Test each regex pattern matches expected guest name formats."""

    @pytest.mark.parametrize(
        "title,expected",
        [
            ("with John Smith", ["john smith"]),
            ("w/ John Smith", ["john smith"]),
            ("Podcast with John Smith about AI", ["john smith"]),
        ],
        ids=["with-basic", "w/-shorthand", "with-in-sentence"],
    )
    def test_with_pattern(self, title, expected):
        assert extract_names(title, "") == expected

    @pytest.mark.parametrize(
        "title,expected",
        [
            ("feat. Jane Doe", ["jane doe"]),
            ("feat Jane Doe", ["jane doe"]),
            ("featuring Jane Doe", ["jane doe"]),
        ],
        ids=["feat-dot", "feat-no-dot", "featuring"],
    )
    def test_feat_pattern(self, title, expected):
        assert extract_names(title, "") == expected

    @pytest.mark.parametrize(
        "title,expected",
        [
            ("Interview: Bob Jones", ["bob jones"]),
            ("Guest: Bob Jones", ["bob jones"]),
            ("Conversation with Bob Jones", ["bob jones"]),
        ],
        ids=["interview-colon", "guest-colon", "conversation-with"],
    )
    def test_interview_pattern(self, title, expected):
        # "Conversation with Bob Jones" should match the "with" pattern
        assert extract_names(title, "") == expected

    def test_interview_with_title_stripped(self):
        """Dr. title should be stripped by normalize_name."""
        result = extract_names("Interview: Dr. Bob Jones", "")
        assert result == ["bob jones"]

    @pytest.mark.parametrize(
        "title,expected",
        [
            ("#123 - Alice Walker", ["alice walker"]),
            ("123 - Alice Walker", ["alice walker"]),
            ("#5 -- Alice Walker", []),  # em-dash not in pattern
        ],
        ids=["hash-number", "no-hash", "em-dash"],
    )
    def test_episode_number_pattern(self, title, expected):
        assert extract_names(title, "") == expected

    def test_episode_number_with_endash(self):
        """EN-dash should be supported in episode number pattern."""
        result = extract_names("#123 \u2013 Alice Walker", "")
        assert result == ["alice walker"]

    def test_episode_number_with_emdash(self):
        """EM-dash should be supported in episode number pattern."""
        result = extract_names("#123 \u2014 Alice Walker", "")
        assert result == ["alice walker"]

    def test_pipe_pattern(self):
        result = extract_names("| Sam Harris", "")
        assert result == ["sam harris"]

    def test_name_on_topic_pattern(self):
        result = extract_names("Jordan Peterson on Free Will", "")
        assert result == ["jordan peterson"]

    def test_name_talks_pattern(self):
        result = extract_names("Jordan Peterson talks about Free Will", "")
        assert result == ["jordan peterson"]


class TestExtractNamesValidation:
    """Test structural validation and blocklist filtering."""

    def test_no_names_matched(self):
        assert extract_names("Some Random Episode", "") == []

    def test_not_a_person_name(self):
        assert extract_names("The Machine Learning Show", "") == []

    def test_single_char_words_filtered(self):
        """Names with single-character words should be rejected."""
        assert extract_names("with A B", "") == []

    def test_all_caps_words_filtered(self):
        """All-caps words (likely acronyms/titles) should cause rejection."""
        assert extract_names("with JOHN SMITH", "") == []

    def test_blocklist_the(self):
        """Blocklist word 'The' should cause rejection."""
        assert extract_names("with The University", "") == []

    def test_blocklist_foundation(self):
        """Blocklist word 'Foundation' should cause rejection."""
        assert extract_names("with Acme Foundation", "") == []

    def test_blocklist_inc(self):
        """Blocklist word 'Inc' should cause rejection."""
        assert extract_names("with Acme Inc", "") == []

    def test_blocklist_university(self):
        """Blocklist word 'University' should cause rejection."""
        assert extract_names("with Stanford University", "") == []

    def test_blocklist_institute(self):
        """Blocklist word 'Institute' should cause rejection."""
        assert extract_names("with Tech Institute", "") == []

    def test_five_word_name_rejected(self):
        """Names with more than 4 words should be rejected."""
        assert extract_names("with John James Robert William Smith", "") == []

    @pytest.mark.parametrize(
        "title",
        [
            "with John Of Smith",
            "feat. Talks And Interviews",
            "Interview: Advice Or Opinion",
            "with Tips For Founders",
            "feat. Letters From Home",
            "Interview: Life In Boston",
            "with Thoughts On Purpose",
        ],
        ids=["of", "and", "or", "for", "from", "in", "on"],
    )
    def test_preposition_conjunction_blocklist(self, title):
        """Title Case phrases with 'of/and/or/for/from/in/on' are not names (M-03)."""
        assert extract_names(title, "") == []


class TestExtractNamesDescription:
    """Test scanning descriptions and deduplication."""

    def test_description_scanned_guest_pattern(self):
        """Names in description should be extracted via guest pattern."""
        result = extract_names(
            "Episode Title",
            "Guest: John Smith discusses artificial intelligence",
        )
        assert result == ["john smith"]

    def test_description_with_pattern(self):
        """Names in description using 'with' pattern should be extracted."""
        result = extract_names(
            "Episode Title",
            "A great episode with John Smith about technology",
        )
        assert result == ["john smith"]

    def test_multiple_matches(self):
        """Multiple names from same title should all be extracted."""
        result = extract_names("with John Smith and feat. Jane Doe", "")
        assert sorted(result) == ["jane doe", "john smith"]

    def test_deduplication(self):
        """Same name in title and description should appear once."""
        result = extract_names(
            "with John Smith",
            "A conversation with John Smith about AI",
        )
        assert result == ["john smith"]

    def test_sorted_output(self):
        """Output should be sorted for deterministic results."""
        result = extract_names("with Zara Adams and feat. Alice Brown", "")
        assert result == sorted(result)
