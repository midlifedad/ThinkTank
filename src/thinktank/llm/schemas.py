"""Pydantic response models for all LLM review types.

Each model defines the expected JSON structure that the LLM returns
via tool_use structured output. Literal types enforce valid decision values.

Spec reference: Section 8.1 (review types and decision fields).
"""

from typing import Literal

from pydantic import BaseModel


class ThinkerApprovalResponse(BaseModel):
    """Response schema for thinker approval reviews."""

    decision: Literal["approved", "rejected", "approved_with_modifications", "escalate_to_human"]
    reasoning: str
    modifications: dict | None = None
    flagged_items: list[str] | None = None


class SourceApprovalResponse(BaseModel):
    """Response schema for source approval reviews."""

    decision: Literal["approved", "rejected", "approved_with_modifications", "escalate_to_human"]
    reasoning: str
    approved_backfill_days: int | None = None
    modifications: dict | None = None


class CandidateReviewResponse(BaseModel):
    """Response schema for candidate thinker batch reviews."""

    decision: Literal["approved", "rejected", "duplicate", "need_more_appearances", "escalate_to_human"]
    reasoning: str
    tier: int | None = None
    categories: list[str] | None = None
    initial_sources: list[str] | None = None
    duplicate_of: str | None = None


class HealthCheckResponse(BaseModel):
    """Response schema for system health check reviews."""

    status: Literal["healthy", "issues_detected"]
    findings: list[str]
    recommended_actions: list[dict] | None = None
    config_adjustments: dict | None = None


class DailyDigestResponse(BaseModel):
    """Response schema for daily digest reviews."""

    summary: str
    highlights: list[str]
    flagged_items: list[str] | None = None
    recommendations: list[str] | None = None


class WeeklyAuditResponse(BaseModel):
    """Response schema for weekly audit reviews."""

    summary: str
    thinkers_to_deactivate: list[str] | None = None
    sources_to_retire: list[str] | None = None
    config_recommendations: dict | None = None
    structural_observations: list[str] | None = None
