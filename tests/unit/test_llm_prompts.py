"""Unit tests for LLM prompt template builders.

Tests are pure logic -- just string building with sample context dicts.
No DB or API calls needed.
"""

import json

from thinktank.llm.prompts import (
    SYSTEM_PROMPT,
    build_candidate_review_prompt,
    build_daily_digest_prompt,
    build_health_check_prompt,
    build_source_approval_prompt,
    build_thinker_approval_prompt,
    build_weekly_audit_prompt,
)


class TestSystemPrompt:
    def test_contains_thinktank_supervisor(self):
        assert "ThinkTank Supervisor" in SYSTEM_PROMPT

    def test_contains_governance_instructions(self):
        assert "govern" in SYSTEM_PROMPT.lower() or "governance" in SYSTEM_PROMPT.lower()


class TestThinkerApprovalPrompt:
    def test_returns_system_and_user_prompt_pair(self):
        context = {"proposed_thinker": {"name": "Test Thinker", "tier": 2}}
        system, user = build_thinker_approval_prompt(context)
        assert system == SYSTEM_PROMPT
        assert isinstance(user, str)
        assert len(user) > 0

    def test_context_serialized_as_json(self):
        context = {"proposed_thinker": {"name": "Jane Doe"}}
        _, user = build_thinker_approval_prompt(context)
        # Context should appear as JSON in the prompt
        assert "Jane Doe" in user

    def test_includes_output_schema_description(self):
        context = {"proposed_thinker": {"name": "Test"}}
        _, user = build_thinker_approval_prompt(context)
        # Should mention the expected response fields
        assert "decision" in user.lower()
        assert "reasoning" in user.lower()


class TestSourceApprovalPrompt:
    def test_returns_prompt_pair(self):
        context = {"source": {"name": "Test Feed", "url": "https://example.com/rss"}}
        system, user = build_source_approval_prompt(context)
        assert system == SYSTEM_PROMPT
        assert "Test Feed" in user

    def test_includes_output_schema_description(self):
        context = {"source": {"name": "Feed"}}
        _, user = build_source_approval_prompt(context)
        assert "decision" in user.lower()


class TestCandidateReviewPrompt:
    def test_returns_prompt_pair_with_candidates(self):
        context = {"candidates": [{"name": "Candidate A"}, {"name": "Candidate B"}]}
        system, user = build_candidate_review_prompt(context)
        assert system == SYSTEM_PROMPT
        assert "Candidate A" in user
        assert "Candidate B" in user

    def test_includes_output_schema_description(self):
        context = {"candidates": []}
        _, user = build_candidate_review_prompt(context)
        assert "decision" in user.lower()


class TestHealthCheckPrompt:
    def test_returns_prompt_pair_with_health_data(self):
        context = {"jobs_summary": {"pending": 10}, "error_log": []}
        system, user = build_health_check_prompt(context)
        assert system == SYSTEM_PROMPT
        assert "pending" in user

    def test_includes_output_schema_description(self):
        context = {"jobs_summary": {}}
        _, user = build_health_check_prompt(context)
        assert "status" in user.lower()
        assert "findings" in user.lower()


class TestDailyDigestPrompt:
    def test_returns_prompt_pair_with_24h_summary(self):
        context = {"content_stats": {"discovered": 50, "transcribed": 30}}
        system, user = build_daily_digest_prompt(context)
        assert system == SYSTEM_PROMPT
        assert "discovered" in user

    def test_includes_output_schema_description(self):
        context = {"content_stats": {}}
        _, user = build_daily_digest_prompt(context)
        assert "summary" in user.lower()
        assert "highlights" in user.lower()


class TestWeeklyAuditPrompt:
    def test_returns_prompt_pair_with_weekly_data(self):
        context = {"growth_rate": 1.5, "inactive_thinkers": []}
        system, user = build_weekly_audit_prompt(context)
        assert system == SYSTEM_PROMPT
        assert "growth_rate" in user

    def test_includes_output_schema_description(self):
        context = {"growth_rate": 1.0}
        _, user = build_weekly_audit_prompt(context)
        assert "summary" in user.lower()
        assert "structural_observations" in user.lower() or "recommendations" in user.lower()
