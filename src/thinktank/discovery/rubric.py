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


@dataclass
class GateThresholds:
    floor: int = DEFAULT_FLOOR
    shortlist: int = DEFAULT_SHORTLIST
    min_qualification: int = DEFAULT_MIN_QUALIFICATION
    min_content: int = DEFAULT_MIN_CONTENT


async def load_thresholds(session: AsyncSession) -> GateThresholds:
    """Read gate thresholds from system_config (code defaults otherwise)."""
    keys = {
        "expert_gate_floor": "floor",
        "expert_gate_shortlist": "shortlist",
        "expert_gate_min_qualification": "min_qualification",
        "expert_gate_min_content": "min_content",
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


def gate_decision(total: int, breakdown: dict, thresholds: GateThresholds) -> str:
    """Map a score to a gate outcome: auto_rejected | borderline | shortlisted.

    The two leg-minimums enforce the "both legs" rule regardless of
    total: content-only celebrities and content-less academics both fail
    the gate even with high totals.
    """
    if breakdown.get("qualification_legs", 0) < thresholds.min_qualification:
        return "auto_rejected"
    if breakdown.get("content", 0) < thresholds.min_content:
        return "auto_rejected"
    if total < thresholds.floor:
        return "auto_rejected"
    if total >= thresholds.shortlist:
        return "shortlisted"
    return "borderline"
