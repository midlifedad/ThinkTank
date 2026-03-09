"""Prompt template builders for all LLM review types.

Each builder returns a (system_prompt, user_prompt) tuple.
The system prompt is constant; the user prompt includes serialized
context and a task description with expected response fields.

Spec reference: Section 8.3 (LLM Supervisor prompts).
"""

import json


SYSTEM_PROMPT = """You are the ThinkTank Supervisor, responsible for governing a content ingestion pipeline.

Your role is to make decisions about which thinkers, sources, and content to include in the corpus.
You evaluate candidates based on their expertise, relevance, credibility, and alignment with the
corpus's mission of capturing expert knowledge from recognized thought leaders.

Guidelines:
- Be thorough in your reasoning before making a decision.
- When approving with modifications, specify exact changes needed.
- Flag any concerns even when approving.
- When in doubt, escalate to human review rather than making risky approvals.
- Consider the overall health and balance of the corpus.
- Prioritize quality over quantity."""


def build_thinker_approval_prompt(context: dict) -> tuple[str, str]:
    """Build prompt pair for thinker approval review.

    Args:
        context: Dict with proposed_thinker info and corpus stats.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    user_prompt = f"""## CONTEXT

{json.dumps(context, default=str, indent=2)}

## TASK

Review the proposed thinker for inclusion in the ThinkTank corpus.
Evaluate their expertise, credibility, and relevance.

Respond with your decision and reasoning. Valid decisions:
- "approved": Thinker meets all criteria for inclusion
- "rejected": Thinker does not meet criteria
- "approved_with_modifications": Approved but with changes (specify in modifications dict)
- "escalate_to_human": Uncertain, needs human review

Include your reasoning for the decision. If approving with modifications,
include a modifications dict (e.g., {{"approved_backfill_days": 90}}).
Flag any concerns in the flagged_items list."""

    return SYSTEM_PROMPT, user_prompt


def build_source_approval_prompt(context: dict) -> tuple[str, str]:
    """Build prompt pair for source approval review.

    Args:
        context: Dict with source info and thinker details.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    user_prompt = f"""## CONTEXT

{json.dumps(context, default=str, indent=2)}

## TASK

Review the proposed content source for inclusion in the ThinkTank corpus.
Evaluate the source quality, relevance to its thinker, and content value.

Respond with your decision and reasoning. Valid decisions:
- "approved": Source meets criteria, specify approved_backfill_days if applicable
- "rejected": Source does not meet criteria
- "approved_with_modifications": Approved with changes (specify in modifications dict)
- "escalate_to_human": Uncertain, needs human review

Include your reasoning. If approving, recommend approved_backfill_days (number of days
of historical content to ingest)."""

    return SYSTEM_PROMPT, user_prompt


def build_candidate_review_prompt(context: dict) -> tuple[str, str]:
    """Build prompt pair for candidate thinker batch review.

    Args:
        context: Dict with candidate list and corpus stats.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    user_prompt = f"""## CONTEXT

{json.dumps(context, default=str, indent=2)}

## TASK

Review the batch of candidate thinkers surfaced by cascade discovery.
For each candidate, evaluate whether they should be promoted to a full thinker.

Respond with your decision and reasoning for each candidate. Valid decisions:
- "approved": Candidate should become a thinker (specify tier, categories, initial_sources)
- "rejected": Candidate does not meet criteria
- "duplicate": Candidate is a duplicate of an existing thinker (specify duplicate_of slug)
- "need_more_appearances": Not enough evidence yet, wait for more appearances
- "escalate_to_human": Uncertain, needs human review

Include your reasoning for the decision."""

    return SYSTEM_PROMPT, user_prompt


def build_health_check_prompt(context: dict) -> tuple[str, str]:
    """Build prompt pair for system health check review.

    Args:
        context: Dict with jobs summary, error log, source health, queue depth.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    user_prompt = f"""## CONTEXT

{json.dumps(context, default=str, indent=2)}

## TASK

Perform a health check on the ThinkTank content ingestion system.
Analyze the job queue status, error patterns, source health, and overall system performance.

Respond with:
- status: "healthy" if everything looks normal, "issues_detected" if problems found
- findings: List of observations about the system state
- recommended_actions: List of recommended actions (each as a dict with action details)
- config_adjustments: Dict of suggested config changes if needed"""

    return SYSTEM_PROMPT, user_prompt


def build_daily_digest_prompt(context: dict) -> tuple[str, str]:
    """Build prompt pair for daily digest review.

    Args:
        context: Dict with 24h content stats, thinker activity, source health.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    user_prompt = f"""## CONTEXT

{json.dumps(context, default=str, indent=2)}

## TASK

Generate a daily digest summarizing the last 24 hours of ThinkTank activity.

Respond with:
- summary: Brief overall summary of the day
- highlights: List of notable events or achievements
- flagged_items: List of items needing attention (if any)
- recommendations: List of suggested actions or improvements (if any)"""

    return SYSTEM_PROMPT, user_prompt


def build_weekly_audit_prompt(context: dict) -> tuple[str, str]:
    """Build prompt pair for weekly audit review.

    Args:
        context: Dict with weekly summary, growth rate, inactive thinkers, error rates.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    user_prompt = f"""## CONTEXT

{json.dumps(context, default=str, indent=2)}

## TASK

Perform a weekly audit of the ThinkTank corpus and system.
Evaluate growth, identify inactive thinkers, flag problematic sources,
and provide structural_observations about the corpus.

Respond with:
- summary: Overall week summary
- thinkers_to_deactivate: List of thinker slugs with no activity (if any)
- sources_to_retire: List of source names with persistent errors (if any)
- config_recommendations: Dict of suggested config changes (if any)
- structural_observations: List of observations about corpus structure and health"""

    return SYSTEM_PROMPT, user_prompt
