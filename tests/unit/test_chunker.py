"""Unit tests for the speaker-turn transcript chunker.

Offsets are load-bearing (grounding verifies quotes via
body_text[char_start:char_end]), so every test asserts the offset
invariant, not just the text content.
"""

from thinktank.ingestion.chunker import MIN_TURN_WORDS, TARGET_WORDS, Chunk, chunk_transcript


def _long_turn(speaker: str, words: int) -> str:
    return f"{speaker}: " + " ".join(f"word{i}" for i in range(words))


def _assert_offsets(body: str, chunks: list[Chunk]) -> None:
    """The offset invariant: chunk.text IS body[start:end], chunks ordered."""
    for c in chunks:
        assert body[c.char_start : c.char_end] == c.text
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert b.char_start >= a.char_end


class TestChunkTranscript:
    def test_empty_and_blank(self):
        assert chunk_transcript("") == []
        assert chunk_transcript("   \n  \n") == []

    def test_single_turn_single_chunk(self):
        body = _long_turn("Speaker A", 50)
        chunks = chunk_transcript(body)
        assert len(chunks) == 1
        assert chunks[0].speaker_label == "Speaker A"
        _assert_offsets(body, chunks)

    def test_speaker_change_splits(self):
        """Two substantial turns from different speakers -> two chunks."""
        a = _long_turn("Speaker A", MIN_TURN_WORDS + 10)
        b = _long_turn("Speaker B", MIN_TURN_WORDS + 10)
        body = f"{a}\n{b}"
        chunks = chunk_transcript(body)
        assert len(chunks) == 2
        assert [c.speaker_label for c in chunks] == ["Speaker A", "Speaker B"]
        _assert_offsets(body, chunks)

    def test_short_interjections_merge(self):
        """Rapid back-and-forth (short turns) stays in one chunk."""
        body = "\n".join(
            [
                _long_turn("Speaker A", 30),
                "Speaker B: Right.",
                "Speaker A: And furthermore the point continues here.",
            ]
        )
        chunks = chunk_transcript(body)
        assert len(chunks) == 1
        _assert_offsets(body, chunks)

    def test_word_budget_splits_long_monologue(self):
        """A monologue over budget splits into multiple chunks."""
        lines = [_long_turn("Speaker A", 100) for _ in range(6)]  # 600 words
        body = "\n".join(lines)
        chunks = chunk_transcript(body)
        assert len(chunks) >= 2
        assert all(c.speaker_label == "Speaker A" for c in chunks)
        _assert_offsets(body, chunks)
        # No chunk wildly exceeds budget (one line of slack allowed)
        for c in chunks:
            assert len(c.text.split()) <= TARGET_WORDS + 110

    def test_unlabeled_text_survives(self):
        """Text without Speaker prefixes (web docs, legacy) still chunks."""
        body = " ".join(f"w{i}" for i in range(80))
        chunks = chunk_transcript(body)
        assert len(chunks) == 1
        assert chunks[0].speaker_label is None
        _assert_offsets(body, chunks)

    def test_indices_sequential(self):
        lines = [_long_turn(f"Speaker {s}", 60) for s in "ABABAB"]
        body = "\n".join(lines)
        chunks = chunk_transcript(body)
        assert [c.index for c in chunks] == list(range(len(chunks)))
