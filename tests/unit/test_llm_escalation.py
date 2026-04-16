"""Unit tests for LLM escalation module.

Verifies the function signature and basic import. Full DB-backed
escalation tests live in tests/integration/test_llm_escalation.py.
"""

import inspect

from thinktank.llm.escalation import escalate_timed_out_reviews


class TestEscalationSignature:
    """Verify function exists and has expected async signature."""

    def test_is_coroutine_function(self):
        """escalate_timed_out_reviews is an async function."""
        assert inspect.iscoroutinefunction(escalate_timed_out_reviews)

    def test_accepts_session_parameter(self):
        """Function accepts an AsyncSession parameter."""
        sig = inspect.signature(escalate_timed_out_reviews)
        assert "session" in sig.parameters

    def test_returns_int_annotation(self):
        """Function return annotation is int."""
        sig = inspect.signature(escalate_timed_out_reviews)
        assert sig.return_annotation is int
