"""Unit tests for LLM response schemas.

Tests that all 6 Pydantic response models validate correct inputs
and reject invalid decision values via Literal types.
"""

import pytest
from pydantic import ValidationError

from thinktank.llm.schemas import (
    CandidateReviewResponse,
    DailyDigestResponse,
    HealthCheckResponse,
    SourceApprovalResponse,
    ThinkerApprovalResponse,
    WeeklyAuditResponse,
)

# ---------- ThinkerApprovalResponse ----------


class TestThinkerApprovalResponse:
    def test_valid_approved(self):
        resp = ThinkerApprovalResponse(decision="approved", reasoning="Looks good")
        assert resp.decision == "approved"
        assert resp.reasoning == "Looks good"

    def test_valid_rejected(self):
        resp = ThinkerApprovalResponse(decision="rejected", reasoning="Not relevant")
        assert resp.decision == "rejected"

    def test_valid_approved_with_modifications(self):
        resp = ThinkerApprovalResponse(
            decision="approved_with_modifications",
            reasoning="Approved with changes",
            modifications={"approved_backfill_days": 90},
            flagged_items=["check bio"],
        )
        assert resp.decision == "approved_with_modifications"
        assert resp.modifications == {"approved_backfill_days": 90}
        assert resp.flagged_items == ["check bio"]

    def test_valid_escalate_to_human(self):
        resp = ThinkerApprovalResponse(decision="escalate_to_human", reasoning="Unclear")
        assert resp.decision == "escalate_to_human"

    def test_invalid_decision_rejected(self):
        with pytest.raises(ValidationError):
            ThinkerApprovalResponse(decision="invalid_value", reasoning="test")

    def test_optional_fields_default_none(self):
        resp = ThinkerApprovalResponse(decision="approved", reasoning="OK")
        assert resp.modifications is None
        assert resp.flagged_items is None


# ---------- SourceApprovalResponse ----------


class TestSourceApprovalResponse:
    def test_valid_approved_with_backfill(self):
        resp = SourceApprovalResponse(decision="approved", reasoning="Good source", approved_backfill_days=30)
        assert resp.decision == "approved"
        assert resp.approved_backfill_days == 30

    def test_valid_rejected(self):
        resp = SourceApprovalResponse(decision="rejected", reasoning="Low quality")
        assert resp.decision == "rejected"

    def test_invalid_decision_rejected(self):
        with pytest.raises(ValidationError):
            SourceApprovalResponse(decision="maybe", reasoning="test")

    def test_optional_backfill_days(self):
        resp = SourceApprovalResponse(decision="approved", reasoning="OK")
        assert resp.approved_backfill_days is None


# ---------- CandidateReviewResponse ----------


class TestCandidateReviewResponse:
    def test_valid_approved_with_tier(self):
        resp = CandidateReviewResponse(
            decision="approved",
            reasoning="Well-known expert",
            tier=2,
            categories=["AI", "ML"],
            initial_sources=["https://example.com/rss"],
        )
        assert resp.decision == "approved"
        assert resp.tier == 2
        assert resp.categories == ["AI", "ML"]

    def test_valid_duplicate(self):
        resp = CandidateReviewResponse(
            decision="duplicate", reasoning="Already exists", duplicate_of="existing-thinker-slug"
        )
        assert resp.decision == "duplicate"
        assert resp.duplicate_of == "existing-thinker-slug"

    def test_valid_need_more_appearances(self):
        resp = CandidateReviewResponse(decision="need_more_appearances", reasoning="Only seen once")
        assert resp.decision == "need_more_appearances"

    def test_valid_escalate_to_human(self):
        resp = CandidateReviewResponse(decision="escalate_to_human", reasoning="Ambiguous")
        assert resp.decision == "escalate_to_human"

    def test_invalid_decision_rejected(self):
        with pytest.raises(ValidationError):
            CandidateReviewResponse(decision="pending", reasoning="test")


# ---------- HealthCheckResponse ----------


class TestHealthCheckResponse:
    def test_valid_healthy(self):
        resp = HealthCheckResponse(status="healthy", findings=["All systems normal"])
        assert resp.status == "healthy"
        assert resp.findings == ["All systems normal"]

    def test_valid_issues_detected(self):
        resp = HealthCheckResponse(
            status="issues_detected",
            findings=["High error rate on source X"],
            recommended_actions=[{"action": "disable source X"}],
            config_adjustments={"max_retries": 5},
        )
        assert resp.status == "issues_detected"
        assert len(resp.recommended_actions) == 1

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            HealthCheckResponse(status="unknown", findings=[])

    def test_findings_required(self):
        with pytest.raises(ValidationError):
            HealthCheckResponse(status="healthy")


# ---------- DailyDigestResponse ----------


class TestDailyDigestResponse:
    def test_valid_digest(self):
        resp = DailyDigestResponse(
            summary="Good day for ingestion",
            highlights=["100 new episodes", "2 new thinkers"],
            flagged_items=["Source X has errors"],
            recommendations=["Review source X"],
        )
        assert resp.summary == "Good day for ingestion"
        assert len(resp.highlights) == 2

    def test_minimal_valid(self):
        resp = DailyDigestResponse(summary="Normal day", highlights=["Nothing notable"])
        assert resp.flagged_items is None
        assert resp.recommendations is None


# ---------- WeeklyAuditResponse ----------


class TestWeeklyAuditResponse:
    def test_valid_audit(self):
        resp = WeeklyAuditResponse(
            summary="Week 10 audit complete",
            thinkers_to_deactivate=["inactive-slug"],
            sources_to_retire=["broken-feed"],
            config_recommendations={"min_duration": 120},
            structural_observations=["Corpus growing steadily"],
        )
        assert resp.summary == "Week 10 audit complete"
        assert resp.structural_observations == ["Corpus growing steadily"]

    def test_minimal_valid(self):
        resp = WeeklyAuditResponse(summary="All good")
        assert resp.thinkers_to_deactivate is None
        assert resp.structural_observations is None

    def test_summary_required(self):
        with pytest.raises(ValidationError):
            WeeklyAuditResponse()
