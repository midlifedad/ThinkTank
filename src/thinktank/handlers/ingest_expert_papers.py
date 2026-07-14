"""Handler: ingest_expert_papers -- an academic expert's papers into the corpus.

Web-Lane Hardening W3.2, the academic-unknowns fix. Resolves the expert on
OpenAlex, pulls their recent papers' abstracts, and stores each as authored
Content (role='author', status='done') under a per-expert 'openalex' source.
The embed sweep then chunks + embeds them like any transcript, so the
scientists who publish rather than podcast finally get real corpus coverage.

Bounded: PAPER_LIMIT most-recent works within the age window, so one
prolific author can't flood the queue.

Job payload schema: {"thinker_id": "uuid-str"}
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.discovery.openalex_papers import fetch_author_papers, normalize_title
from thinktank.ingestion.fulltext import fetch_paper_fulltext
from thinktank.ingestion.text_content import create_author_content
from thinktank.models.content import Content, ContentThinker
from thinktank.models.job import Job
from thinktank.models.source import Source, SourceThinker
from thinktank.models.thinker import Thinker

logger = structlog.get_logger(__name__)

# Per-expert paper cap (backfill bound) and recency window.
PAPER_LIMIT = 30
SINCE_YEAR = 2015


async def handle_ingest_expert_papers(session: AsyncSession, job: Job) -> None:
    """Ingest an expert's recent OpenAlex papers as authored corpus content."""
    thinker_id = job.payload.get("thinker_id")
    if not thinker_id:
        raise ValueError("thinker_id missing from ingest_expert_papers payload")
    thinker = await session.get(Thinker, uuid.UUID(thinker_id) if isinstance(thinker_id, str) else thinker_id)
    if thinker is None:
        logger.warning("ingest_expert_papers_thinker_not_found", thinker_id=str(thinker_id))
        return

    log = logger.bind(job_id=str(job.id), thinker=thinker.slug)
    papers = await fetch_author_papers(thinker.name, limit=PAPER_LIMIT, since_year=SINCE_YEAR)
    if not papers:
        log.info("ingest_expert_papers_none")
        return

    source_id = await _get_or_create_papers_source(session, thinker)

    # Cross-run dedup: a paper already ingested for this thinker (under any
    # DOI/version) must not be re-added on a later run. canonical_url dedup
    # can't catch it -- a preprint and its published version have different
    # DOIs -- so match on normalized title.
    existing_titles = await _existing_paper_titles(session, thinker.id)

    created = 0
    with_fulltext = 0
    for paper in papers:
        if normalize_title(paper.title) in existing_titles:
            continue
        # W3.3: the abstract is ALWAYS the base; OA full text (when
        # available and materially richer) is appended after it, so a
        # paper's headline abstract claim stays a distinct chunk and full
        # text only adds grounding depth.
        body_text = paper.abstract
        if paper.oa_url:
            fulltext = await fetch_paper_fulltext(session, paper.oa_url, paper.abstract)
            if fulltext:
                body_text = f"{paper.abstract}\n\n{fulltext}"
                with_fulltext += 1
        if await create_author_content(
            session,
            thinker=thinker,
            source_id=source_id,
            content_type="paper",
            title=paper.title,
            url=paper.landing_url or f"https://openalex.org/{paper.openalex_id}",
            body_text=body_text,
            published_at=paper.published_at,
        ):
            existing_titles.add(normalize_title(paper.title))
            created += 1

    await session.commit()
    log.info("ingest_expert_papers_complete", fetched=len(papers), created=created, with_fulltext=with_fulltext)


async def _existing_paper_titles(session: AsyncSession, thinker_id: uuid.UUID) -> set[str]:
    """Normalized titles of papers already ingested for this thinker."""
    rows = await session.execute(
        select(Content.title)
        .join(ContentThinker, ContentThinker.content_id == Content.id)
        .where(ContentThinker.thinker_id == thinker_id, Content.content_type == "paper")
    )
    return {normalize_title(t) for (t,) in rows.all()}


async def _get_or_create_papers_source(session: AsyncSession, thinker: Thinker) -> uuid.UUID:
    """One 'openalex' owned source per expert -- papers need a home source
    (content.source_id is NOT NULL). Auto-approved: OpenAlex works matched
    to an author are their own publications, not a channel needing an
    identity gate."""
    url = f"https://openalex.org/author/{thinker.slug}"
    source_id = uuid.uuid4()
    inserted_id = await session.scalar(
        pg_insert(Source)
        .values(
            id=source_id,
            source_type="openalex",
            name=f"{thinker.name} — papers (OpenAlex)",
            url=url,
            approval_status="approved",
            config={"owned_by_thinker": str(thinker.id)},
        )
        .on_conflict_do_nothing(index_elements=["url"])
        .returning(Source.id)
    )
    if inserted_id is None:
        existing = await session.scalar(select(Source).where(Source.url == url))
        return existing.id

    link = await session.scalar(
        select(SourceThinker).where(SourceThinker.source_id == inserted_id, SourceThinker.thinker_id == thinker.id)
    )
    if link is None:
        session.add(SourceThinker(source_id=inserted_id, thinker_id=thinker.id, relationship_type="owns"))
    return inserted_id
