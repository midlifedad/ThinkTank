"""Unit tests for claims extraction: quote grounding + the drop discipline.

ground_quote is the anti-hallucination gate -- an observation whose quote
can't be located in its provenance text never gets stored -- so it is
tested exhaustively here without any LLM.
"""

import json
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

    async def test_empty_tool_input_is_no_claims_not_a_crash(self):
        """When the evidence is irrelevant the model calls the tool with
        empty input ({}). That must validate to zero claims, not raise --
        otherwise one off-topic web article fails the whole inquiry."""
        assert ExtractionResponse.model_validate({}).claims == []

        response = ExtractionResponse()  # no claims provided
        with (
            patch(
                "thinktank.llm.claims_extraction._client.review",
                new=AsyncMock(return_value=(response, _usage(), 10)),
            ),
            patch("thinktank.llm.claims_extraction._record_cost", new=AsyncMock()),
        ):
            kept, dropped = await extract_observations(None, "Q?", "Dr. Test", "off-topic text", "web article")

        assert kept == []
        assert dropped == 0

    def test_stringified_empty_claims_recovered(self):
        """Sonnet sometimes returns claims as a JSON *string*, not a list."""
        assert ExtractionResponse.model_validate({"claims": '{"claims": []}'}).claims == []
        assert ExtractionResponse.model_validate('{"claims": []}').claims == []

    def test_stringified_nonempty_claims_recovered(self):
        """A stringified NON-empty list must be recovered, not lost."""
        payload = {
            "claims": json.dumps(
                {
                    "claims": [
                        {
                            "claim_text": "Rapamycin extends lifespan in mice",
                            "claim_type": "factual",
                            "stance_on_question": "asserts",
                            "confidence": "asserted",
                            "quote": "rapamycin extends lifespan in mice",
                        }
                    ]
                }
            )
        }
        parsed = ExtractionResponse.model_validate(payload)
        assert len(parsed.claims) == 1
        assert parsed.claims[0].claim_text == "Rapamycin extends lifespan in mice"

    async def test_unparseable_bundle_degrades_not_crashes(self):
        """A residual parse failure skips the ONE bundle, never raising."""
        with (
            patch(
                "thinktank.llm.claims_extraction._client.review",
                new=AsyncMock(side_effect=ValueError("Claude response did not include a tool_use block")),
            ),
            patch("thinktank.llm.claims_extraction._record_cost", new=AsyncMock()),
        ):
            kept, dropped = await extract_observations(None, "Q?", "Dr. Test", "text", "web article")
        assert kept == []
        assert dropped == 0


def _usage():
    from thinktank.llm.client import LLMUsage

    return LLMUsage(input_tokens=100, output_tokens=50)
