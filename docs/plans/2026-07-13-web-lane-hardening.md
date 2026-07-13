# Web-Lane Hardening & Expert Content Ingestion

**Status:** Design approved (Amir, 2026-07-13). **Priority: ahead of Dynamic Expert Standing Phases 2–4.**

**Origin:** the first live rapamycin inquiry carried 45 of 58 observations on the web lane, but that lane is currently thin: PMC/PubMed pages fetched as 165 chars, YouTube as ~220, Nature/aging-us failed outright, and **all 45 web observations have `asserted_at = NULL`** because no publication date is extracted. The web lane is doing the heavy lifting on the weakest fetcher.

## What Amir asked for (verbatim intent)

1. Harden the fetcher — likely via an **external API/service** (JS render, primary literature, video).
2. **Keep receipts** for every source (as today) **and, when the content specifically relates to the expert — and definitely when written by the expert — ingest it into the corpus** (chunked, embedded, searchable, attributed).
3. **Per expert, proactively find their owned channels** — personal website, X handle, YouTube channel, Substack, etc. — **and ingest that content too.**

## The core reframe: a three-rung ingestion ladder

Today there is one tier (transient receipt). Amir's ask is really three, distinguished by **who authored the content** and **how durably relevant it is**:

| Rung | Trigger | Where it lands | Searchable later? |
|---|---|---|---|
| **Receipt** | any web-search citation that grounded a claim | `documents` (text + provenance), as today | no — evidence only |
| **Attributed content** | fetched page is substantively *about* the expert | `content` + `content_thinkers.role='subject'`, chunked + embedded | yes |
| **Owned corpus** | content *by* the expert (their essay / video / post) | registered `source` (`relationship_type='owns'`) → `content` (`role='author'`) → embed | yes, and refreshed on a cadence |

The good news from the codebase audit: rungs 2–3 mostly **reuse existing rails** — `sources → content → embed_content` is a working pipeline (today only `podcast_rss`), `content_thinkers.role` already carries author-vs-guest attribution, and a YouTube video can route through the **same Mac transcription** we already run. We are wiring, not inventing.

## Phase W1 — Fetch hardening (unblocks everything)

**Goal:** real text + a real date from the sources that currently fail.

- **Adopt an external extraction service.** Recommendation:
  - **Exa** (`/search` + `/contents`) as the primary web lane — it is search-native and returns **clean text + `publishedDate` + `author` in one call**, which simultaneously fixes the null-date gap *and* could replace the Perplexity `sonar` search (one vendor, structured output, dates included). Evaluate head-to-head with the current Perplexity lane on the rapamycin question before committing.
  - **Jina Reader (`r.jina.ai`)** as a cheap per-URL fallback fetcher for arbitrary links Exa doesn't cover (generous free tier, returns markdown).
  - **OpenAlex (already integrated)** for primary literature: use `open_access.oa_url` / Unpaywall for OA full text; fall back to the always-free abstract. Stop trying to scrape paywalled publisher HTML.
  - **YouTube:** pull captions via `youtube-transcript-api`, or when absent route the video through the **existing Mac transcription pipeline** (we already have `yt-dlp` + the diarized ASR service). A video by an expert is just another `process_content` job.
- **Extract `published_at`** from service metadata or, for the fallback fetcher, page meta (`article:published_time`, JSON-LD `datePublished`). Web observations become time-indexed like corpus observations — closes the provenance-date gap.
- Keep `web_fetch.py`'s receipt contract; swap its innards from raw `httpx` + BeautifulSoup to the service client with a graceful fallback chain (Exa contents → Jina → current extractor). Cost-tracked via `api_usage`.

**Exit check:** re-run the rapamycin inquiry; expect PMC/YouTube/Nature to yield real text and every web observation to carry a date.

## Phase W2 — Evidence → corpus promotion (the ladder)

**Goal:** stop throwing away substantive expert content after one extraction.

- After a document is fetched for the web lane, classify **authorship/relevance** (cheap LLM call, or metadata `author` match + heuristics): *by the expert* / *substantively about the expert* / *transient*.
- *By* or *about* → promote from `documents` receipt to a `content` row attributed via `content_thinkers.role` (`author` / `subject`), which auto-flows into `embed_content` → becomes searchable corpus for future inquiries. *Transient* stays a receipt.
- Dedup against existing content (URL + trigram title) so re-citation doesn't double-ingest.
- Net effect: the corpus compounds. An expert cited once for rapamycin contributes their essay to *every* future inquiry that touches their work.

## Phase W3 — Proactive per-expert source discovery & ingestion

**Goal:** the corpus becomes "everything the expert has published," not "podcasts we happened to catch them on."

- New `discover_expert_sources` job, enqueued when a candidate is promoted (and re-runnable from admin). For the expert, find owned channels: **personal website, X/Twitter, YouTube channel, Substack, podcast feed, OpenAlex/Scholar author page.** Sources: Exa/Perplexity + deterministic probes (the vetting evidence dossier already gathers some hints — extend, don't duplicate).
- Register each discovered channel as a `source` with `relationship_type='owns'`, `approval_status` gated (owned channels are high-trust but confirm identity to avoid impersonators), and enqueue a backfill.
- Owned content ingests through the type-appropriate existing handler: YouTube → Mac transcription; Substack/RSS → feed fetch; website → crawl (Exa/Jina). All land as `content` with `role='author'` → embed.
- Refresh on the existing `refresh_due_sources` cadence so new posts/videos flow in continuously.
- Admin: an "Owned sources" panel on the thinker detail page (discovered channels, ingestion status, backfill progress).

**This is the highest-leverage rung.** It directly fixes the rapamycin run's five `unknown` experts (Kenyon, Church, Kirkland, Olshansky, Gladyshev — zero attributed content today): discover their channels, ingest their writing/talks, and they gain real positions.

## External-service decision (Amir's "we may need an external API")

Recommended stack, favoring reuse:
- **Exa** — primary search + content + dates (evaluate as Perplexity replacement).
- **Jina Reader** — cheap arbitrary-URL fallback.
- **OpenAlex + Unpaywall** — primary literature (already integrated).
- **Existing Mac transcription** — all video/audio, zero marginal cost.

Keys stored via `swarmify keys` / `system_config` secrets, never committed. One or two new paid vendors, both metered and cost-tracked.

## Sequencing & cost

W1 first (tactical, unblocks the live lane), then W3 (highest corpus leverage), then W2 (compounding). W1 is a focused PR; W3 is a mini-project (discovery job + per-type ingestion + admin); W2 layers cleanly on both.

Cost: Exa/Jina per-fetch (metered), OpenAlex free, transcription free on the Mac. All external calls write `api_usage` rows (A2 pattern).

## Relationship to other plans

Takes priority over `2026-07-13-dynamic-expert-standing.md` Phases 2–4 (per Amir). Standing Phase 3 (endorsement graph) *benefits* directly: richer owned-content corpus = more expert-about-expert statements to extract as endorsement edges.
