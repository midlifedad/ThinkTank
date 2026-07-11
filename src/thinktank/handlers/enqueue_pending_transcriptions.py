"""Handler: enqueue_pending_transcriptions -- transcription backlog sweep.

Source: ARCH-REVIEW 2026-05-28 (A1). Content promoted to ``status='pending'``
is transcribed by a ``process_content`` job. The promotion sites
(scan_episodes_for_thinkers, rescan_cataloged_for_thinker) enqueue those jobs
directly; this sweep is the safety net that picks up any pending content
without an in-flight transcription job -- the pre-A1 backlog, content whose
job was lost, or desyncs where a job completed but the content row was never
updated.

Flood control: the sweep never pushes the ``process_content`` queue past the
``max_pending_transcriptions`` threshold (the same system_config value the
backpressure module demotes discovery against), so a large backlog drains at
a bounded rate instead of triggering a GPU scale-up spike.

Skipped content (any process_content job in a non-``done`` status):
    - pending / running / retrying / awaiting_llm: already in flight
    - failed: all transcription passes failed permanently -- auto-re-enqueueing
      would loop the failure forever; the operator can retry from the admin
      queue view
    - cancelled: an operator explicitly stopped it; don't silently undo that
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.content import Content
from thinktank.models.job import Job
from thinktank.queue.backpressure import get_max_pending_transcriptions, get_queue_depth
from thinktank.queue.claim import _now
from thinktank.queue.retry import get_max_attempts
from thinktank.transcription.policy import get_transcription_age_cutoff

logger = structlog.get_logger(__name__)


async def handle_enqueue_pending_transcriptions(session: AsyncSession, job: Job) -> None:
    """Enqueue process_content jobs for pending content with no job coverage.

    Job payload schema: {} (no required fields; ``triggered_by`` optional).

    Args:
        session: Active database session.
        job: The enqueue_pending_transcriptions job.
    """
    log = logger.bind(job_id=str(job.id))

    # Flood control: only enqueue up to the backpressure threshold.
    depth = await get_queue_depth(session, "process_content")
    threshold = await get_max_pending_transcriptions(session)
    budget = threshold - depth
    if budget <= 0:
        log.info(
            "transcription_sweep_at_capacity",
            queue_depth=depth,
            threshold=threshold,
        )
        return

    # Content ids already covered by a process_content job in any non-done
    # status (see module docstring for why each status is excluded).
    covered_result = await session.execute(
        select(Job.payload["content_id"].astext).where(
            Job.job_type == "process_content",
            Job.status != "done",
        )
    )
    covered_ids = {row for (row,) in covered_result.all() if row}

    # Age policy (Amir 2026-07-11): episodes older than the configured
    # cutoff are not transcribed. NULL published_at passes (fail-open --
    # a missing date is a parse artifact, not evidence of age).
    cutoff = await get_transcription_age_cutoff(session)
    age_filter = true() if cutoff is None else or_(Content.published_at.is_(None), Content.published_at >= cutoff)

    # Oldest-discovered pending content first, so the backlog drains in order.
    pending_result = await session.execute(
        select(Content.id).where(Content.status == "pending", age_filter).order_by(Content.discovered_at)
    )
    pending_ids = [cid for (cid,) in pending_result.all() if str(cid) not in covered_ids]

    to_enqueue = pending_ids[:budget]
    now = _now()
    for content_id in to_enqueue:
        session.add(
            Job(
                id=uuid.uuid4(),
                job_type="process_content",
                payload={"content_id": str(content_id)},
                priority=5,
                status="pending",
                attempts=0,
                max_attempts=get_max_attempts("process_content"),
                created_at=now,
            )
        )

    await session.commit()

    log.info(
        "transcription_sweep_complete",
        enqueued=len(to_enqueue),
        pending_uncovered=len(pending_ids),
        queue_depth=depth,
        threshold=threshold,
        age_cutoff=cutoff.isoformat() if cutoff else None,
    )
