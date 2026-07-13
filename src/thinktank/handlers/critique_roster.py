"""Handler: critique_roster -- LLM comparative review of a vetted area slate.

Dynamic Expert Standing Phase 1b (docs/plans/
2026-07-13-dynamic-expert-standing.md). Per-candidate vetting judges each
person in isolation; it structurally cannot see that the slate promoted
adjacent-field eminence over the area's actual creators, or that an
obvious name never surfaced at all. One roster-level LLM call catches
both:

    misranked -- verdicts that look wrong RELATIVE to the slate; stored
                 and rendered on the Experts admin page for re-vetting.
    missing   -- names conspicuously absent; inserted as candidates
                 (deduped by the same trigram machinery as discovery)
                 and vetted through the NORMAL gate. The critic
                 nominates; it never promotes.

Triggered by an admin button per area, and auto-enqueued once per area
when its vetting fan-out completes (the one-critique-per-area guard
prevents nominee vetting from re-triggering the critic forever).

Job payload schema: {"area": str, "triggered_by": str?}
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.ingestion.name_normalizer import normalize_name
from thinktank.ingestion.trigram import find_similar_candidates, find_similar_thinkers
from thinktank.llm.client import LLMClient
from thinktank.models.api_usage import ApiUsage
from thinktank.models.candidate import CandidateThinker, RosterCritique
from thinktank.models.job import Job
from thinktank.queue.retry import get_max_attempts

logger = structlog.get_logger(__name__)

_client = LLMClient()

# Bound the slate in the prompt; areas produce ~15-30 candidates today.
MAX_ROSTER = 60
MAX_NOMINATIONS = 10


class MisrankedEntry(BaseModel):
    name: str
    issue: str = Field(description="What looks wrong about this verdict relative to the slate")


class MissingEntry(BaseModel):
    name: str
    why: str = Field(description="Why this person belongs on this area's roster")


class RosterVerdict(BaseModel):
    misranked: list[MisrankedEntry] = Field(default_factory=list)
    missing: list[MissingEntry] = Field(default_factory=list)


async def _record_cost(session: AsyncSession, usage) -> None:
    from thinktank.config import get_settings

    settings = get_settings()
    cost = (
        usage.input_tokens * settings.llm_input_cost_per_mtok + usage.output_tokens * settings.llm_output_cost_per_mtok
    ) / 1_000_000.0
    session.add(
        ApiUsage(
            id=uuid.uuid4(),
            api_name="anthropic",
            endpoint="roster_critic",
            period_start=datetime.now(UTC),
            call_count=1,
            units_consumed=usage.total,
            estimated_cost_usd=cost,
        )
    )


def _slate_lines(candidates: list[CandidateThinker]) -> str:
    lines = []
    for c in candidates:
        fit = (c.evidence or {}).get("domain_fit") or {}
        parts = [f"- {c.name}: {c.status}, score={c.qualification_score}"]
        if c.score_breakdown:
            b = c.score_breakdown
            parts.append(
                f"(sch={b.get('scholarship')}, not={b.get('notability')}, "
                f"auth={b.get('authorship')}, content={b.get('content')})"
            )
        if fit:
            parts.append(f"fit={fit.get('centrality')}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


async def handle_critique_roster(session: AsyncSession, job: Job) -> None:
    """Run one roster critique for an area; nominate missing candidates."""
    area = (job.payload.get("area") or "").strip()
    if not area:
        raise ValueError("area missing from critique_roster payload")
    log = logger.bind(job_id=str(job.id), area=area)

    candidates = list(
        (
            await session.execute(
                select(CandidateThinker)
                .where(CandidateThinker.search_area == area)
                .order_by(CandidateThinker.qualification_score.desc().nulls_last())
                .limit(MAX_ROSTER)
            )
        )
        .scalars()
        .all()
    )
    if not candidates:
        log.info("critique_roster_skipped", reason="no candidates for area")
        return

    # Prompt lesson (first live run, 2026-07-13): the critic's ONLY source
    # for "missing" names and for judging centrality is its own knowledge of
    # the domain -- the slate data cannot contain either. The first prompt
    # said "only flag what you have real grounds for", which the model read
    # as "grounds within the provided data" and returned empty against a
    # slate whose top promotions were all eminent-elsewhere figures.
    system = (
        "You review a fully-vetted roster of expert candidates for a specific "
        "domain, using YOUR OWN knowledge of who these people are and who "
        "shapes this domain. The vetting rubric measures general eminence "
        "(citations, Wikipedia, books), which transfers across domains; "
        "domain centrality does not -- so a high score proves nothing about "
        "fit, and entries without a fit= annotation were never "
        "centrality-assessed at all: scrutinize those hardest.\n\n"
        "MISRANKED: any promoted/shortlisted name whose renown lies in an "
        "adjacent discipline rather than this domain, and any rejected name "
        "who created this domain's defining tools, frameworks, essays, or "
        "practices.\n"
        "MISSING: genuinely prominent figures in THIS domain absent from the "
        "slate -- builders of its defining tools, authors of its canonical "
        "writing, its prominent educators and practitioners. Recall them "
        "from your knowledge of the field; the slate cannot tell you who is "
        "absent.\n\n"
        "Empty lists are only for a slate that genuinely survives this "
        "scrutiny -- never a default out of caution."
    )
    prompt = (
        f"Domain: {area}\n\nVetted slate (name: verdict, rubric score, band breakdown, LLM fit):\n"
        f"{_slate_lines(candidates)}\n\n"
        f"Statuses: promoted/shortlisted = in; pending_human = borderline; "
        f"auto_rejected/rejected = out.\n"
        f"Return misranked entries and up to {MAX_NOMINATIONS} missing names."
    )
    verdict, usage, _ = await _client.review(system, prompt, RosterVerdict, max_tokens=1500, session=session)
    await _record_cost(session, usage)

    # Nominate missing names as normal candidates (dedup like discovery).
    nominated = 0
    now = datetime.now(UTC)
    for entry in verdict.missing[:MAX_NOMINATIONS]:
        name = entry.name.strip()
        if not name:
            continue
        norm = normalize_name(name)
        if await find_similar_thinkers(session, norm) or await find_similar_candidates(session, norm):
            continue
        candidate = CandidateThinker(
            id=uuid.uuid4(),
            name=name,
            normalized_name=norm,
            status="vetting",
            search_area=area,
            seed_source="roster_critic",
            inferred_categories=[area],
            evidence={"seed_claim": {"basis": entry.why}},
        )
        session.add(candidate)
        session.add(
            Job(
                id=uuid.uuid4(),
                job_type="vet_candidate",
                payload={"candidate_id": str(candidate.id)},
                priority=5,
                status="pending",
                attempts=0,
                max_attempts=get_max_attempts("vet_candidate"),
                created_at=now,
            )
        )
        nominated += 1

    session.add(
        RosterCritique(
            id=uuid.uuid4(),
            search_area=area,
            critique=verdict.model_dump(),
            model=_client.model,
            candidates_reviewed=len(candidates),
            nominated=nominated,
        )
    )
    await session.commit()

    log.info(
        "critique_roster_complete",
        reviewed=len(candidates),
        misranked=len(verdict.misranked),
        missing=len(verdict.missing),
        nominated=nominated,
    )
