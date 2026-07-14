"""Speaker-turn chunker for diarized transcripts.

Splits content.body_text ("Speaker A: ...\\n" lines) into retrieval
chunks that respect speaker turns: consecutive lines from the same
speaker merge, long turns split on word budget, and every chunk carries
exact char offsets into body_text so downstream grounding can verify
quotes against the original.

Pure functions -- exhaustively unit-testable, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Word budget per chunk. bge-base truncates ~512 tokens; ~350 words of
# conversational English stays comfortably inside it.
TARGET_WORDS = 350
# Turns shorter than this merge into the running chunk even across a
# speaker change (rapid back-and-forth stays contextual).
MIN_TURN_WORDS = 25


@dataclass
class Chunk:
    index: int
    speaker_label: str | None
    text: str
    char_start: int
    char_end: int


def _split_line(line: str) -> tuple[str | None, str]:
    """Split 'Speaker A: text' into (label, text); (None, line) if unlabeled."""
    if ": " in line:
        prefix, rest = line.split(": ", 1)
        if prefix.startswith("Speaker ") and len(prefix) <= 24:
            return prefix, rest
    return None, line


def chunk_transcript(body_text: str) -> list[Chunk]:
    """Chunk a diarized transcript into speaker-coherent segments.

    Returns chunks with char offsets into the ORIGINAL body_text (the
    chunk text is body_text[char_start:char_end] verbatim -- offsets are
    load-bearing for grounding).
    """
    if not body_text or not body_text.strip():
        return []

    # Line spans with offsets into body_text.
    spans: list[tuple[int, int, str | None, str]] = []  # (start, end, label, line)
    pos = 0
    for line in body_text.split("\n"):
        end = pos + len(line)
        if line.strip():
            label, _ = _split_line(line)
            spans.append((pos, end, label, line))
        pos = end + 1  # the split newline

    chunks: list[Chunk] = []
    cur_start: int | None = None
    cur_end = 0
    cur_label: str | None = None
    cur_words = 0

    def _flush() -> None:
        nonlocal cur_start, cur_words
        if cur_start is None:
            return
        text = body_text[cur_start:cur_end]
        chunks.append(
            Chunk(
                index=len(chunks),
                speaker_label=cur_label,
                text=text,
                char_start=cur_start,
                char_end=cur_end,
            )
        )
        cur_start = None
        cur_words = 0

    for start, end, label, line in spans:
        words = len(line.split())
        speaker_changed = cur_start is not None and label != cur_label and words >= MIN_TURN_WORDS
        over_budget = cur_words + words > TARGET_WORDS and cur_words > 0

        if speaker_changed or over_budget:
            _flush()

        if cur_start is None:
            cur_start = start
            cur_label = label
        elif label is not None and cur_label is None:
            cur_label = label
        cur_end = end
        cur_words += words

    _flush()
    return chunks


def _word_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """(start, end) char offsets of each whitespace-delimited word in
    text[start:end]. Offsets are absolute into `text`."""
    return [(m.start(), m.end()) for m in re.finditer(r"\S+", text[start:end])]


def chunk_document(body_text: str) -> list[Chunk]:
    """Chunk prose (papers, articles) into ~TARGET_WORDS retrieval chunks.

    Unlike chunk_transcript, this splits WITHIN a long paragraph -- an
    academic paragraph can run 400-600 words, which would overflow the
    embed model's window and be silently truncated. Paragraph boundaries
    (blank lines) are preferred split points; a paragraph over budget is
    split further on word boundaries. Char offsets are exact
    (chunk.text == body_text[char_start:char_end]) -- load-bearing for
    grounding.
    """
    if not body_text or not body_text.strip():
        return []

    chunks: list[Chunk] = []

    def _emit(start: int, end: int) -> None:
        text = body_text[start:end]
        if text.strip():
            chunks.append(Chunk(index=len(chunks), speaker_label=None, text=text, char_start=start, char_end=end))

    # Paragraphs: spans between blank-line separators, with offsets.
    para_spans: list[tuple[int, int]] = []
    pos = 0
    for para in re.split(r"\n\s*\n", body_text):
        p_start = body_text.index(para, pos) if para else pos
        para_spans.append((p_start, p_start + len(para)))
        pos = p_start + len(para)

    cur_start: int | None = None
    cur_end = 0
    cur_words = 0
    for p_start, p_end in para_spans:
        words = _word_spans(body_text, p_start, p_end)
        if not words:
            continue
        n = len(words)
        if n > TARGET_WORDS:
            # Paragraph over budget on its own: flush the running chunk,
            # then split this paragraph into TARGET_WORDS-word slices.
            if cur_start is not None:
                _emit(cur_start, cur_end)
                cur_start, cur_words = None, 0
            for i in range(0, n, TARGET_WORDS):
                seg = words[i : i + TARGET_WORDS]
                _emit(p_start + seg[0][0], p_start + seg[-1][1])
            continue
        if cur_start is not None and cur_words + n > TARGET_WORDS:
            _emit(cur_start, cur_end)
            cur_start, cur_words = None, 0
        if cur_start is None:
            cur_start = p_start
        cur_end = p_end
        cur_words += n

    if cur_start is not None:
        _emit(cur_start, cur_end)
    return chunks
