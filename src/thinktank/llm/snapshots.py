"""Bounded context snapshot builders for each LLM review type.

Each builder produces a dict suitable for serialization into the
context_snapshot JSONB field of an LLMReview row. All queries are
bounded with explicit .limit() matching spec bounds:
- 50 thinkers max
- 100 error log entries max
- 20 candidates max
- 10 episode samples max

All datetime comparisons use timezone-naive datetimes per project convention.

Spec reference: Section 8.1 (context snapshots), 8.5 (context budgeting).
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from src.thinktank.models.candidate import CandidateThinker
from src.thinktank.models.content import Content
from src.thinktank.models.job import Job
from src.thinktank.models.source import Source
from src.thinktank.models.thinker import Thinker


def _now() -> datetime:
    """Return current UTC time as timezone-naive datetime."""
    return datetime.now(UTC).replace(tzinfo=None)


async def build_thinker_approval_context(
    session: AsyncSession,
    thinker_id: uuid.UUID,
) -> dict:
    """Build bounded context for a thinker approval review.

    Args:
        session: Active database session.
        thinker_id: UUID of the thinker to review.

    Returns:
        Dict with proposed_thinker info and corpus_stats.
    """
    thinker = await session.get(Thinker, thinker_id)

    # Corpus stats
    total_approved = await session.scalar(
        select(func.count()).select_from(Thinker).where(
            Thinker.approval_status == "approved"
        )
    ) or 0

    total_content = await session.scalar(
        select(func.count()).select_from(Content)
    ) or 0

    queue_depth = await session.scalar(
        select(func.count()).select_from(Job).where(
            Job.status.in_(["pending", "retrying"])
        )
    ) or 0

    proposed = {
        "name": thinker.name,
        "slug": thinker.slug,
        "tier": thinker.tier,
        "bio": thinker.bio,
        "approval_status": thinker.approval_status,
        "sources": [
            {"name": s.name, "source_type": s.source_type, "url": s.url}
            for s in thinker.sources
        ],
        "categories": [
            {"slug": getattr(c, "slug", str(c))}
            for c in thinker.categories
        ],
    }

    return {
        "proposed_thinker": proposed,
        "corpus_stats": {
            "total_approved_thinkers": total_approved,
            "total_content": total_content,
            "queue_depth": queue_depth,
        },
    }


async def build_source_approval_context(
    session: AsyncSession,
    source_id: uuid.UUID,
) -> dict:
    """Build bounded context for a source approval review.

    Args:
        session: Active database session.
        source_id: UUID of the source to review.

    Returns:
        Dict with source info, thinker details, and sample episodes.
    """
    source = await session.get(Source, source_id)

    # Sample episodes (bounded to 10)
    stmt = (
        select(Content)
        .where(Content.source_id == source_id)
        .order_by(Content.discovered_at.desc())
        .limit(10)
    )
    result = await session.execute(stmt)
    episodes = result.scalars().all()

    source_info = {
        "name": source.name,
        "source_type": source.source_type,
        "url": source.url,
        "approval_status": source.approval_status,
        "item_count": source.item_count,
        "error_count": source.error_count,
    }

    thinker_info = {
        "name": source.thinker.name,
        "slug": source.thinker.slug,
    }

    episode_samples = [
        {"title": e.title, "url": e.url, "status": e.status}
        for e in episodes
    ]

    return {
        "source": source_info,
        "thinker": thinker_info,
        "sample_episodes": episode_samples,
    }


async def build_candidate_review_context(
    session: AsyncSession,
    candidate_ids: list[uuid.UUID] | None = None,
) -> dict:
    """Build bounded context for candidate thinker batch review.

    Args:
        session: Active database session.
        candidate_ids: Specific candidate UUIDs to review, or None for all pending.

    Returns:
        Dict with candidate list (max 20) and corpus stats.
    """
    if candidate_ids is not None:
        stmt = (
            select(CandidateThinker)
            .where(CandidateThinker.id.in_(candidate_ids))
            .limit(20)
        )
    else:
        stmt = (
            select(CandidateThinker)
            .where(CandidateThinker.status == "pending_llm")
            .order_by(CandidateThinker.appearance_count.desc())
            .limit(20)
        )

    result = await session.execute(stmt)
    candidates = result.scalars().all()

    total_approved = await session.scalar(
        select(func.count()).select_from(Thinker).where(
            Thinker.approval_status == "approved"
        )
    ) or 0

    candidate_list = [
        {
            "id": str(c.id),
            "name": c.name,
            "normalized_name": c.normalized_name,
            "appearance_count": c.appearance_count,
            "status": c.status,
            "sample_urls": c.sample_urls or [],
            "inferred_categories": c.inferred_categories or [],
        }
        for c in candidates
    ]

    return {
        "candidates": candidate_list,
        "corpus_stats": {
            "total_approved_thinkers": total_approved,
        },
    }


async def build_health_check_context(session: AsyncSession) -> dict:
    """Build bounded context for system health check.

    Args:
        session: Active database session.

    Returns:
        Dict with jobs_summary, error_log (max 100), source health, queue depth.
    """
    now = _now()
    six_hours_ago = now - timedelta(hours=6)

    # Jobs summary by status (last 6h)
    jobs_by_status_stmt = (
        select(Job.status, func.count())
        .where(Job.created_at >= six_hours_ago)
        .group_by(Job.status)
    )
    result = await session.execute(jobs_by_status_stmt)
    jobs_summary = {row[0]: row[1] for row in result.all()}

    # Error log (bounded to 100)
    error_stmt = (
        select(Job.id, Job.job_type, Job.error, Job.error_category, Job.last_error_at)
        .where(Job.error.is_not(None), Job.last_error_at >= six_hours_ago)
        .order_by(Job.last_error_at.desc())
        .limit(100)
    )
    result = await session.execute(error_stmt)
    error_log = [
        {
            "job_id": str(row[0]),
            "job_type": row[1],
            "error": row[2],
            "error_category": row[3],
            "last_error_at": str(row[4]) if row[4] else None,
        }
        for row in result.all()
    ]

    # Sources with errors
    source_health_stmt = (
        select(Source.name, Source.error_count)
        .where(Source.error_count > 0, Source.active.is_(True))
        .order_by(Source.error_count.desc())
        .limit(50)
    )
    result = await session.execute(source_health_stmt)
    source_health = [
        {"name": row[0], "error_count": row[1]}
        for row in result.all()
    ]

    # Queue depth by type
    queue_stmt = (
        select(Job.job_type, func.count())
        .where(Job.status.in_(["pending", "retrying"]))
        .group_by(Job.job_type)
    )
    result = await session.execute(queue_stmt)
    queue_depth = {row[0]: row[1] for row in result.all()}

    return {
        "jobs_summary": jobs_summary,
        "error_log": error_log,
        "source_health": source_health,
        "queue_depth": queue_depth,
    }


async def build_daily_digest_context(session: AsyncSession) -> dict:
    """Build bounded context for daily digest.

    Args:
        session: Active database session.

    Returns:
        Dict with 24h content stats, thinker activity, source health, corpus totals.
    """
    now = _now()
    yesterday = now - timedelta(hours=24)

    # Content stats (last 24h)
    discovered = await session.scalar(
        select(func.count()).select_from(Content).where(
            Content.discovered_at >= yesterday
        )
    ) or 0

    transcribed = await session.scalar(
        select(func.count()).select_from(Content).where(
            Content.processed_at >= yesterday,
            Content.status == "done",
        )
    ) or 0

    failed = await session.scalar(
        select(func.count()).select_from(Content).where(
            Content.discovered_at >= yesterday,
            Content.status == "error",
        )
    ) or 0

    # Thinker stats (last 24h)
    new_approved = await session.scalar(
        select(func.count()).select_from(Thinker).where(
            Thinker.approval_status == "approved",
            Thinker.added_at >= yesterday,
        )
    ) or 0

    new_rejected = await session.scalar(
        select(func.count()).select_from(Thinker).where(
            Thinker.approval_status.in_(["rejected_by_llm", "rejected"]),
            Thinker.added_at >= yesterday,
        )
    ) or 0

    # Candidates surfaced
    candidates_surfaced = await session.scalar(
        select(func.count()).select_from(CandidateThinker).where(
            CandidateThinker.first_seen_at >= yesterday
        )
    ) or 0

    # Corpus totals
    total_thinkers = await session.scalar(
        select(func.count()).select_from(Thinker).where(
            Thinker.approval_status == "approved"
        )
    ) or 0

    total_content = await session.scalar(
        select(func.count()).select_from(Content)
    ) or 0

    total_sources = await session.scalar(
        select(func.count()).select_from(Source).where(
            Source.approval_status == "approved"
        )
    ) or 0

    # Top 5 active thinkers (by content added in last 24h)
    top_stmt = (
        select(Thinker.name, func.count(Content.id).label("count"))
        .join(Source, Source.thinker_id == Thinker.id)
        .join(Content, Content.source_id == Source.id)
        .where(Content.discovered_at >= yesterday)
        .group_by(Thinker.name)
        .order_by(func.count(Content.id).desc())
        .limit(5)
    )
    result = await session.execute(top_stmt)
    top_thinkers = [
        {"name": row[0], "new_content_count": row[1]}
        for row in result.all()
    ]

    return {
        "content_stats": {
            "discovered": discovered,
            "transcribed": transcribed,
            "failed": failed,
        },
        "thinker_stats": {
            "new_approved": new_approved,
            "new_rejected": new_rejected,
            "candidates_surfaced": candidates_surfaced,
        },
        "corpus_totals": {
            "total_thinkers": total_thinkers,
            "total_content": total_content,
            "total_sources": total_sources,
        },
        "top_active_thinkers": top_thinkers,
    }


async def build_weekly_audit_context(session: AsyncSession) -> dict:
    """Build bounded context for weekly audit.

    Args:
        session: Active database session.

    Returns:
        Dict with weekly summary, growth rate, inactive thinkers, error rates.
    """
    now = _now()
    week_ago = now - timedelta(days=7)

    # Weekly content counts
    content_this_week = await session.scalar(
        select(func.count()).select_from(Content).where(
            Content.discovered_at >= week_ago
        )
    ) or 0

    # Total corpus
    total_content = await session.scalar(
        select(func.count()).select_from(Content)
    ) or 0

    total_thinkers = await session.scalar(
        select(func.count()).select_from(Thinker).where(
            Thinker.approval_status == "approved"
        )
    ) or 0

    # Growth rate (content this week / total, as percentage)
    growth_rate = (content_this_week / total_content * 100) if total_content > 0 else 0.0

    # Thinkers with zero new content in 7 days (bounded to 50)
    # Find approved thinkers whose sources have no content discovered in last 7 days
    active_thinker_ids_stmt = (
        select(Thinker.id)
        .join(Source, Source.thinker_id == Thinker.id)
        .join(Content, Content.source_id == Source.id)
        .where(Content.discovered_at >= week_ago, Thinker.approval_status == "approved")
        .group_by(Thinker.id)
    )
    result = await session.execute(active_thinker_ids_stmt)
    active_ids = {row[0] for row in result.all()}

    inactive_stmt = (
        select(Thinker.name, Thinker.slug)
        .where(
            Thinker.approval_status == "approved",
            Thinker.active.is_(True),
            Thinker.id.not_in(active_ids) if active_ids else Thinker.id.is_not(None),
        )
        .limit(50)
    )
    result = await session.execute(inactive_stmt)
    inactive_thinkers = [
        {"name": row[0], "slug": row[1]}
        for row in result.all()
    ]

    # Sources with high error rates
    error_sources_stmt = (
        select(Source.name, Source.error_count, Source.item_count)
        .where(Source.error_count > 0, Source.active.is_(True))
        .order_by(Source.error_count.desc())
        .limit(20)
    )
    result = await session.execute(error_sources_stmt)
    error_sources = [
        {"name": row[0], "error_count": row[1], "item_count": row[2]}
        for row in result.all()
    ]

    # Candidate backlog
    candidate_backlog = await session.scalar(
        select(func.count()).select_from(CandidateThinker).where(
            CandidateThinker.status == "pending_llm"
        )
    ) or 0

    return {
        "weekly_summary": {
            "content_discovered": content_this_week,
        },
        "corpus_totals": {
            "total_content": total_content,
            "total_thinkers": total_thinkers,
        },
        "growth_rate": round(growth_rate, 2),
        "inactive_thinkers": inactive_thinkers,
        "error_sources": error_sources,
        "candidate_backlog": candidate_backlog,
    }
