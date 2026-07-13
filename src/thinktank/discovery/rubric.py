"""Deterministic qualification rubric for expert candidates.

Turns an evidence dossier (discovery/evidence.py) into a transparent
score with per-leg breakdown -- zero LLM tokens. The gate's character
(Amir spec 2026-07-12): "not just anyone" -- a candidate must earn BOTH
a qualification leg (scholarship and/or notability and/or authorship)
AND a content-availability leg. A big-audience pseudo-expert can't pass
on content alone; an eminent recluse with no findable content can't
pass either (the whole point is ingestable material).

Bands (max 100):
    scholarship   0-30   OpenAlex h-index / citations
    notability    0-20   Wikipedia/Wikidata presence and reach
    authorship    0-15   published books
    content       0-25   podcast appearances, verified YouTube/Substack
    peer_signal   0-10   co-appearance with already-tracked thinkers

Thresholds live in system_config so tightening the gate is an admin
config edit, not a deploy:
    expert_gate_floor          below -> auto_rejected (default 35)
    expert_gate_shortlist      at/above -> LLM judge (default 50)
    expert_gate_min_qualification  minimum combined scholarship+
                               notability+authorship (default 20)
    expert_gate_min_content    minimum content leg (default 8)

Default bar = "credentialed public intellectual": strong scholarship OR
real notability qualifies (plus content); notability alone at Wikipedia
level clears the qualification minimum, per Amir's inclusion of
notoriety as a qualifying dimension.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.config_table import SystemConfig

DEFAULT_FLOOR = 35
DEFAULT_SHORTLIST = 50
DEFAULT_MIN_QUALIFICATION = 20
DEFAULT_MIN_CONTENT = 8
# Practitioner path (Amir 2026-07-12): in non-academic domains (marketing,
# business, creative) real experts lack citations, so the academic
# qualification floor rejects them despite strong content + notability.
# When scholarship is absent but notability clears this bar and content is
# strong, route to the LLM judge instead of auto-rejecting -- let it decide
# if they're a genuine practitioner authority. Default 12 = an actual
# English Wikipedia article (a bare Wikidata stub, 4, is not enough).
DEFAULT_PRACTITIONER_MIN_NOTABILITY = 12
# Domain-fit rescue (design doc 2026-07-13-dynamic-expert-standing.md,
# Phase 1): a candidate the LLM assesses as CORE to the search area is
# routed to the judge even below the shortlist bar, provided content is
# ingestable and the total clears this floor. Fixes the class of miss
# where the rubric's general-eminence bands auto-reject the people who
# created the area's defining artifacts (Weng/Nakajima/Bubeck on
# "AI coding and agentic engineering", 2026-07-13). Kept above zero so
# a core-fit assessment can't rescue a candidate with NO evidence at all.
DEFAULT_FIT_RESCUE_FLOOR = 15


@dataclass
class GateThresholds:
    floor: int = DEFAULT_FLOOR
    shortlist: int = DEFAULT_SHORTLIST
    min_qualification: int = DEFAULT_MIN_QUALIFICATION
    min_content: int = DEFAULT_MIN_CONTENT
    practitioner_min_notability: int = DEFAULT_PRACTITIONER_MIN_NOTABILITY
    fit_rescue_floor: int = DEFAULT_FIT_RESCUE_FLOOR


async def load_thresholds(session: AsyncSession) -> GateThresholds:
    """Read gate thresholds from system_config (code defaults otherwise)."""
    keys = {
        "expert_gate_floor": "floor",
        "expert_gate_shortlist": "shortlist",
        "expert_gate_min_qualification": "min_qualification",
        "expert_gate_min_content": "min_content",
        "expert_gate_practitioner_min_notability": "practitioner_min_notability",
        "expert_gate_fit_rescue_floor": "fit_rescue_floor",
    }
    thresholds = GateThresholds()
    result = await session.execute(select(SystemConfig.key, SystemConfig.value).where(SystemConfig.key.in_(keys)))
    for key, raw in result.all():
        value = raw.get("value", None) if isinstance(raw, dict) else raw
        try:
            setattr(thresholds, keys[key], int(value))
        except (TypeError, ValueError):
            pass
    return thresholds


def _band(value: float, bands: list[tuple[float, int]]) -> int:
    """Score by threshold bands: highest band whose threshold value meets."""
    score = 0
    for threshold, points in bands:
        if value >= threshold:
            score = points
    return score


def score_dossier(dossier: dict, peer_coappearances: int = 0) -> tuple[int, dict]:
    """Compute (qualification_score, breakdown) from an evidence dossier.

    Pure function -- exhaustively unit-tested; the entire gate policy
    lives here and in the thresholds.
    """
    openalex = dossier.get("openalex", {})
    wikidata = dossier.get("wikidata", {})
    books = dossier.get("openlibrary", {})
    podcasts = dossier.get("podcastindex", {})
    youtube = dossier.get("youtube", {})
    substack = dossier.get("substack", {})

    # Scholarship 0-30: h-index bands, boosted by raw citations for
    # fields where h-index lags.
    scholarship = 0
    if openalex.get("found"):
        h_index = openalex.get("h_index") or 0
        citations = openalex.get("cited_by_count") or 0
        scholarship = _band(h_index, [(5, 8), (15, 15), (30, 22), (50, 30)])
        scholarship = max(scholarship, _band(citations, [(500, 8), (5000, 15), (20000, 22), (50000, 30)]))

    # Notability 0-20: a Wikipedia article is the meaningful line;
    # sitelink breadth measures international reach.
    notability = 0
    if wikidata.get("found"):
        notability = 4  # Wikidata entity exists at all
        if wikidata.get("has_enwiki"):
            notability = 12
        notability = max(notability, _band(wikidata.get("sitelink_count") or 0, [(5, 12), (15, 16), (40, 20)]))

    # Authorship 0-15
    authorship = 0
    if books.get("found"):
        authorship = _band(books.get("work_count") or 0, [(1, 6), (3, 10), (8, 15)])

    # Content availability 0-25: podcast appearances are the strongest
    # ingestable signal today; verified YouTube/Substack add platforms.
    content = 0
    if podcasts.get("ok") and podcasts.get("found"):
        content += _band(podcasts.get("appearance_feed_count") or 0, [(1, 6), (3, 10), (8, 15)])
    if youtube.get("checked") and youtube.get("reachable"):
        content += 5
    if substack.get("checked") and substack.get("reachable"):
        content += 5
    content = min(content, 25)

    # Peer signal 0-10: co-appearances with thinkers we already track.
    peer = _band(peer_coappearances, [(1, 4), (3, 7), (6, 10)])

    breakdown = {
        "scholarship": scholarship,
        "notability": notability,
        "authorship": authorship,
        "content": content,
        "peer_signal": peer,
        "qualification_legs": scholarship + notability + authorship,
    }
    total = scholarship + notability + authorship + content + peer
    return total, breakdown


def gate_decision(
    total: int,
    breakdown: dict,
    thresholds: GateThresholds,
    centrality: str | None = None,
) -> str:
    """Map a score to a gate outcome.

    Returns: auto_rejected | borderline | shortlisted |
    practitioner_review | fit_rescue (the last two route to the judge).

    The two leg-minimums enforce the "both legs" rule regardless of
    total: content-only celebrities and content-less academics both fail
    the gate even with high totals. Content is never waivable -- not by
    the practitioner path, not by domain fit -- because without findable
    content there is nothing to ingest.

    Args:
        centrality: the LLM domain-fit verdict ("core" / "adjacent" /
            "peripheral"), or None when no fit assessment ran. Only
            "core" changes routing; the judge sees the full assessment
            either way via the vetting block.
    """
    content = breakdown.get("content", 0)
    # Content is always required -- nothing to ingest without it.
    if content < thresholds.min_content:
        return "auto_rejected"

    # Academic path: normal qualification-floor gating.
    if breakdown.get("qualification_legs", 0) >= thresholds.min_qualification and total >= thresholds.floor:
        outcome = "shortlisted" if total >= thresholds.shortlist else "borderline"
    # Practitioner path: no scholarship, but real notability + strong content
    # -> let the LLM judge decide rather than auto-reject on the academic bar.
    elif (
        breakdown.get("scholarship", 0) == 0
        and breakdown.get("notability", 0) >= thresholds.practitioner_min_notability
    ):
        outcome = "practitioner_review"
    else:
        outcome = "auto_rejected"

    # Domain-fit rescue: CORE to the area beats a middling general-eminence
    # score. Applies only where the deterministic gate stalled (borderline)
    # or rejected; a shortlisted/practitioner candidate already reaches the
    # judge on its own.
    if centrality == "core" and outcome in ("auto_rejected", "borderline") and total >= thresholds.fit_rescue_floor:
        return "fit_rescue"

    # Adjacent softening: a genuinely arguable case (ADJACENT fit, real
    # evidence) must not die in a silent auto-reject -- route it to a human
    # instead. First observed on Lilian Weng (2026-07-13): score 34 vs
    # floor 35, fit "adjacent ... writing shaped the field's vocabulary".
    # Core -> judge; adjacent -> human; peripheral -> the verdict stands.
    if centrality == "adjacent" and outcome == "auto_rejected" and total >= thresholds.fit_rescue_floor:
        return "borderline"

    return outcome
