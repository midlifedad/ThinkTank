# Web-Lane Hardening & Expert Content Ingestion

**Status:** Design approved (Amir, 2026-07-13; revised after review). **Priority: ahead of Dynamic Expert Standing Phases 2–4.**

**Origin:** the first live rapamycin inquiry carried **45 of 58 observations on the web lane**, but that lane is thin. The fetcher returned 165-char PMC/PubMed pages, ~220-char YouTube shells, hard-failed Nature and aging-us, and — worst — **left all 45 web observations with `asserted_at = NULL`** because no publication date is extracted. The lane doing the heavy lifting is the weakest one.

## What Amir asked for

1. Harden the fetcher — likely via an **external API/service** (JS render, primary literature, video).
2. **Keep receipts** for every source **and, when content specifically relates to the expert — and definitely when written by the expert — ingest it** (chunked, embedded, searchable, attributed).
3. **Per expert, proactively find their owned channels** — personal website, X, YouTube, Substack, etc. — **and ingest that content.**

## The ingestion ladder (revised)

Three tiers, keyed on **authorship**. The load-bearing rule — added after review — is that **only content *authored by* the expert enters the searchable corpus.**

| Rung | Trigger | Lands in | Retrieved as the expert's own words? |
|---|---|---|---|
| **Receipt** | any web-search citation that grounded a claim | `documents` (text + provenance), as today | no — evidence for one observation |
| **Enriched receipt** | page is *about* the expert (quotes/discusses them) | `documents`, linked to the thinker, quote + date preserved | **no** — see the provenance rule below |
| **Owned corpus** | content *by* the expert (their essay / video / post / paper) | registered `source` (`relationship_type='owns'`) → `content` (`role='author'`) → embedded | **yes**, and refreshed on a cadence |

### Why "about the expert" must NOT enter the corpus (review finding)

The corpus retrieval (`_corpus_evidence`) filters only on `thinker_id` — it has **no role awareness** — and the extractor is then instructed to "extract what this expert asserted." If we promoted an *about-the-expert* article (written by a journalist who quotes them) into the corpus as a subject-tagged row, a future inquiry would retrieve it as the expert's own testimony, and a paraphrase could be stored as their verbatim claim, **dated to the article rather than to when they actually said it** — corrupting exactly the provenance this system exists to protect.

So: *about-the-expert* content stays an **enriched receipt** — we keep the link, the quote, and (once W1 lands) the date, but it is never retrieved as the expert's own words. This also matches Amir's own weighting ("*definitely* if written by the expert" vs. "*potentially* if it relates to them"). Only `role='author'` content is corpus.

The good news from the codebase audit: the owned-corpus rung **reuses existing rails** — `sources → content → embed_content` already works (today only `podcast_rss`), `content_thinkers.role` already carries attribution (`primary`/`guest` today; add `author`), and a YouTube video by an expert routes through the **same Mac transcription** we already run at zero marginal cost.

## Phase W1 — Fetch hardening (unblocks everything)

**Goal:** real text + a real date from the sources that currently fail.

- **Adopt an external extraction service for the inquiry web lane:**
  - **Exa** (`/search` + `/contents`) as primary — search-native, returns **clean text + `publishedDate` + `author` in one call**, which fixes the null-date gap *and* fits the web lane better than Perplexity (we want retrievable documents, not a synthesized answer). **Scope:** this replaces Perplexity in the *inquiry web lane only*. The expert-**seeding** lane keeps Perplexity `sonar-deep-research` — Exa doesn't do that synthesis.
  - **Jina Reader (`r.jina.ai`)** as a cheap per-URL fallback for links Exa doesn't cover.
  - **OpenAlex + Unpaywall (already integrated)** for primary literature: `open_access.oa_url` for OA full text, always-free abstract as fallback. Stop scraping paywalled publisher HTML.
  - **YouTube:** captions via `youtube-transcript-api`; when absent, route the video through the **existing Mac transcription pipeline** (we already have `yt-dlp` + diarized ASR).
- **Extract `published_at`** from service metadata or page meta (`article:published_time`, JSON-LD `datePublished`), so web observations are time-indexed like corpus observations.
- Keep `web_fetch.py`'s receipt contract; swap its internals from raw `httpx`+BeautifulSoup to the service client with a fallback chain (Exa contents → Jina → current extractor). Cost-tracked via `api_usage`.

**Exit check:** re-run the rapamycin inquiry; PMC/YouTube/Nature yield real text, every web observation carries a date.

## Phase W2 — Owned-content promotion + enriched receipts

**Goal:** stop discarding substantive expert content, without corrupting provenance.

- Classify each fetched document's **authorship** (metadata `author` + a cheap LLM confirmation): *by the expert* / *about the expert* / *transient*.
- *By the expert* → promote to `content` (`role='author'`) → auto-flows into `embed_content` → searchable corpus. **Source-id wiring** (review finding: `content.source_id` is `NOT NULL`): attach to the expert's owned-web source from W3 when one exists; otherwise a per-domain catch-all `source_type='web'` source. (Chosen over making the FK nullable — keeps every content row traceable to a source.)
- *About the expert* → enriched receipt only (link + quote + date on the `documents` row), **never corpus**.
- *Transient* → plain receipt, as today.
- Dedup on URL + trigram title so re-citation and W3 crawl don't double-ingest.

## Phase W3 — Proactive per-expert source discovery & ingestion

**Goal:** the corpus becomes "what the expert has actually published," not "podcasts we happened to catch them on."

- New `discover_expert_sources` job, enqueued on promotion (re-runnable from admin). Find owned channels — **personal website, YouTube channel, Substack, podcast feed, and (for academics) OpenAlex/Scholar author page.** Sources: Exa + deterministic probes (extend the vetting evidence dossier, which already gathers some hints).
- **Academics vs. creators (review finding):** the owned-channel model is strong for practitioners/creators but weak for pure academics. The run's `unknown` experts (Kenyon, Church, Kirkland, Gladyshev) are academics whose real output is **papers and recorded lectures** — so for them, **OpenAlex OA full-text ingestion is the primary fix**, recorded-talk discovery second, social channels a distant third. W3 branches by expert type.
- **X/Twitter is discovery + receipt-only, NOT ingestion (review finding):** the API is $100+/mo and rate-limited, scraping violates ToS and is JS/login-walled. We record the handle and may link to it as a receipt, but we do not backfill it as corpus. Revisit only if the cheaper channels prove the value.
- Register each ingestable channel as a `source` (`relationship_type='owns'`), **identity-gated** (`approval_status` — owned channels are high-trust but confirm the account is really theirs, not an impersonator or name-collision), and enqueue a **bounded** backfill:
  - **Per-source backfill caps (review finding):** cap items/hours per channel (e.g. N most-recent or most-viewed within the 5-year age window) so one prolific YouTube channel can't swamp the Mac transcription queue. Priority: recency + relevance first.
- Ingest through the type-appropriate existing handler: YouTube → Mac transcription; Substack/RSS → feed fetch; website → crawl (Exa/Jina); papers → OpenAlex OA text. All land as `content` (`role='author'`) → embed.
- Refresh on the existing `refresh_due_sources` cadence so new posts/videos flow in continuously.
- Admin: an "Owned sources" panel on the thinker detail page (discovered channels, identity status, ingestion/backfill progress).

**This is the highest-leverage rung** — it directly fixes the five `unknown` experts (zero attributed content today).

## External-service decision (Amir's "we may need an external API")

Favoring reuse:
- **Exa** — inquiry web-lane search + content + dates (metered).
- **Jina Reader** — cheap arbitrary-URL fallback.
- **OpenAlex + Unpaywall** — primary literature (already integrated, free).
- **Existing Mac transcription** — all video/audio, zero marginal cost.
- **Perplexity** — unchanged for expert *seeding* only.

Keys via `swarmify keys` / `system_config` secrets, never committed. Every external call writes an `api_usage` row (A2 pattern).

## Sequencing & cost

**W1 first** (tactical, unblocks the live lane and de-risks the rest) → **W3** (highest corpus leverage; fixes the unknowns) → **W2** (compounding, layers on both). W1 is a focused PR; W3 is a mini-project; W2 is small once W1's fetcher and W3's owned-sources exist.

Cost: Exa/Jina per-fetch (metered, and W3 backfill caps bound the volume), OpenAlex free, transcription free on the Mac. Relevance dilution from full-channel ingestion is acceptable — embeddings are cheap and retrieval filters by relevance — but the backfill caps also keep the noise bounded.

## Relationship to other plans

Takes priority over `2026-07-13-dynamic-expert-standing.md` Phases 2–4 (per Amir). Standing Phase 3 (endorsement graph) benefits directly: a richer owned-content corpus yields more expert-about-expert statements to extract as endorsement edges.

## Review changelog (2026-07-13)

- Rung 2 restricted to **author-only into corpus**; about-the-expert content is an enriched receipt, never retrieved as the expert's own words (fixes a latent provenance-corruption path via the role-blind corpus retrieval).
- **X/Twitter demoted** from ingestion to discovery + receipt-only (API cost + ToS/technical infeasibility).
- **`content.source_id NOT NULL`** wiring gap called out; resolved via owned-source attachment / per-domain web source rather than a schema change.
- **W3 backfill caps + recency/relevance priority** added (prevents one channel swamping transcription).
- **OpenAlex OA full-text promoted** to the primary fix for academic `unknown` experts; owned-channel model scoped to creators/practitioners.
- **Exa scoped** to the inquiry web lane; expert seeding stays on Perplexity deep-research.
