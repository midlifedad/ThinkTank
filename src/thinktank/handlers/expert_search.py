"""Handler: expert_search -- seed an area with vetted-expert candidates.

Stage 1 of the Expert Discovery & Vetting pipeline (Amir spec
2026-07-12): "find the obvious top experts in <area>".

Two seed lanes, both bounded:
    perplexity  ONE sonar-deep-research call: recognized experts with
                platform hints (YouTube/Substack/podcasts) + citations
    openalex    top-cited recently-active authors (zero tokens)

Each surfaced name is deduped against existing thinkers and candidates
(trigram similarity, same threshold as cascade discovery), then created
as a CandidateThinker carrying seed provenance + platform hints, and a
vet_candidate job is enqueued -- the deterministic gate decides who ever
reaches the LLM judge.

Job payload schema:
    {"area": "AI safety", "limit": 25, "triggered_by": ...}

Token economics per run: 1 deep-research call here + 1 judge call per
shortlisted candidate downstream. Fixed by construction.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.discovery.openalex_seed import seed_from_openalex
from thinktank.discovery.perplexity_client import search_experts
from thinktank.ingestion.name_normalizer import normalize_name
from thinktank.ingestion.trigram import find_similar_candidates, find_similar_thinkers
from thinktank.models.candidate import CandidateThinker
from thinktank.models.job import Job
from thinktank.queue.claim import _now
from thinktank.queue.retry import get_max_attempts

logger = structlog.get_logger(__name__)

_DEFAULT_LIMIT = 25
_OPENALEX_LIMIT = 15


async def handle_expert_search(session: AsyncSession, job: Job) -> None:
    """Seed one area: research -> dedup -> candidates -> vetting jobs."""
    area = (job.payload.get("area") or "").strip()
    if not area:
        raise ValueError("area missing from expert_search payload")
    limit = int(job.payload.get("limit") or _DEFAULT_LIMIT)

    log = logger.bind(job_id=str(job.id), area=area)

    # Lane 1: Perplexity deep research (claims + platform hints).
    perplexity_claims = await search_experts(session, area, limit=limit)
    for claim in perplexity_claims:
        claim["_seed_source"] = "perplexity"

    # Lane 2: OpenAlex top-cited (academic heavyweights).
    openalex_claims = await seed_from_openalex(area, limit=_OPENALEX_LIMIT)
    for claim in openalex_claims:
        claim["_seed_source"] = "openalex"

    # Merge lanes; on same-name collision keep the Perplexity claim (it
    # carries platform hints) but upgrade provenance to "both".
    merged: dict[str, dict] = {}
    for claim in perplexity_claims + openalex_claims:
        name = (claim.get("name") or "").strip()
        if not name:
            continue
        key = normalize_name(name)
        if key in merged:
            merged[key]["_seed_source"] = "both"
        else:
            merged[key] = claim

    created = 0
    skipped_existing = 0
    now = _now()

    for norm_name, claim in merged.items():
        name = claim["name"].strip()

        # Dedup against tracked thinkers and open candidates (trigram,
        # same machinery as cascade discovery).
        if await find_similar_thinkers(session, norm_name):
            skipped_existing += 1
            continue
        if await find_similar_candidates(session, norm_name):
            skipped_existing += 1
            continue

        hints = {
            key: claim[field]
            for key, field in (("youtube_url", "youtube_url"), ("substack_url", "substack_url"))
            if claim.get(field)
        }
        candidate = CandidateThinker(
            id=uuid.uuid4(),
            name=name,
            normalized_name=norm_name,
            status="vetting",
            search_area=area,
            seed_source=claim["_seed_source"],
            inferred_categories=[area],
            suggested_youtube=hints.get("youtube_url"),
            evidence={
                "hints": hints,
                "seed_claim": {
                    "basis": claim.get("basis"),
                    "affiliation": claim.get("affiliation"),
                    "notable_podcasts": claim.get("notable_podcasts") or [],
                },
            },
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
        created += 1

    await session.commit()

    log.info(
        "expert_search_complete",
        perplexity_claims=len(perplexity_claims),
        openalex_claims=len(openalex_claims),
        merged=len(merged),
        candidates_created=created,
        skipped_existing=skipped_existing,
    )
