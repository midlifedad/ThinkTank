"""Unit tests for claims extraction: quote grounding + the drop discipline.

ground_quote is the anti-hallucination gate -- an observation whose quote
can't be located in its provenance text never gets stored -- so it is
tested exhaustively here without any LLM.
"""

from unittest.mock import AsyncMock, patch

import pytest

from thinktank.llm.claims_extraction import (
    ExtractedClaim,
    ExtractionResponse,
    extract_observations,
    ground_quote,
)


class TestGroundQuote:
    def test_exact_match_offsets(self):
        text = "Speaker A: Rapamycin extends lifespan in mice by up to 25 percent."
        quote = "Rapamycin extends lifespan in mice"
        offsets = ground_quote(quote, text)
        assert offsets is not None
        start, end = offsets
        assert text[start:end] == quote

    def test_whitespace_normalized_fallback(self):
        """Transcript has a newline where the LLM wrote a space."""
        text = "Speaker A: Rapamycin extends\nlifespan   in mice."
        quote = "Rapamycin extends lifespan in mice."
        offsets = ground_quote(quote, text)
        assert offsets is not None
        start, end = offsets
        # Slice spans from the first to the last quoted word.
        assert text[start:end].split() == ["Rapamycin", "extends", "lifespan", "in", "mice."]

    def test_case_insensitive_fallback(self):
        text = "he said rapamycin works in mice"
        assert ground_quote("Rapamycin works in mice", text) is not None

    def test_fabricated_quote_returns_none(self):
        text = "Speaker A: We discussed exercise and diet today."
        assert ground_quote("Rapamycin extends lifespan", text) is None

    def test_empty_quote_returns_none(self):
        assert ground_quote("", "some evidence text") is None
        assert ground_quote("   ", "some evidence text") is None

    def test_partial_word_does_not_match(self):
        """Token fallback matches whole words, not substrings."""
        text = "the rapamycins were discussed"
        assert ground_quote("the rapamycin was", text) is None


@pytest.mark.anyio
class TestExtractObservations:
    async def test_ungrounded_claims_dropped(self):
        evidence = "Speaker A: Rapamycin extends lifespan in mice. That is the finding."
        grounded_claim = ExtractedClaim(
            claim_text="Rapamycin extends lifespan in mice",
            claim_type="factual",
            stance_on_question="asserts",
            confidence="asserted",
            quote="Rapamycin extends lifespan in mice",
        )
        fabricated = ExtractedClaim(
            claim_text="Rapamycin is FDA-approved for longevity",
            claim_type="factual",
            stance_on_question="asserts",
            confidence="asserted",
            quote="Rapamycin is approved by the FDA for human longevity",
        )
        response = ExtractionResponse(claims=[grounded_claim, fabricated])
        with (
            patch(
                "thinktank.llm.claims_extraction._client.review",
                new=AsyncMock(return_value=(response, _usage(), 10)),
            ),
            patch("thinktank.llm.claims_extraction._record_cost", new=AsyncMock()),
        ):
            kept, dropped = await extract_observations(None, "Does rapamycin work?", "Dr. Test", evidence, "transcript")

        assert [c.claim_text for c in kept] == ["Rapamycin extends lifespan in mice"]
        assert dropped == 1


def _usage():
    from thinktank.llm.client import LLMUsage

    return LLMUsage(input_tokens=100, output_tokens=50)
