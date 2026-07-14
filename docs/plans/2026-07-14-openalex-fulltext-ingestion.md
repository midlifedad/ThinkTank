# OpenAlex Full-Text Ingestion (W3.3) — Scoping

**Status:** Scoping for review (not started). Follows W3.2a (paper abstracts, merged).

## Why

Today the academic-expert corpus is paper **abstracts** — avg ~1,500 chars (~200 words), one chunk each. The abstract states *what* a paper found; the **methods, results, and discussion** — where the quotable, nuanced, dose/population-specific claims live — are in the body. For a grounding-first system, that's the difference between "rapamycin extended lifespan" and "rapamycin extended median lifespan 23% in male UM-HET3 mice at 42 ppm started at 9 months."

**Availability makes it worth doing.** Measured live for the roster (2026-07-14):

| Expert (sample) | Works | Open-access | With PDF URL | Landing-only |
|---|---|---|---|---|
| Matt Kaeberlein | 30 | 27 (90%) | 16 | 11 |
| Cynthia Kenyon | 30 | 27 (90%) | 20 | 7 |

**~90% OA** across the sample (biomedical/aging research is heavily OA-mandated — NIH, eLife, PLOS). So full text is *fetchable* for the large majority of the corpus, not a rare bonus.

## Approach: OA-only, reuse W1 infra, abstract fallback

Ingest OA full text where available; keep the abstract otherwise. No paywalled scraping (legal + technical non-starter, same call as the X decision).

1. **Resolve the OA text location.** Extend the `fetch_author_papers` works query to request `open_access` + `best_oa_location` (already returned; just read them). Prefer `best_oa_location.pdf_url`, then `best_oa_location.landing_page_url`, then `open_access.oa_url`.
2. **Fetch + extract via Jina Reader (reuse W1).** Jina already handles **PDF URLs** (returns clean markdown) and HTML landing pages — so PDF extraction needs **no new Python dependency** (no pypdf/pdfplumber). The W1 `fetch_document` fallback chain (Exa → Jina → bs4) is the existing seam; add a "fetch this specific OA URL as text" path.
3. **Strip boilerplate.** Trim reference lists, acknowledgments, author-affiliation blocks, and figure/table captions — they pollute extraction and grounding. Heuristic: cut everything from a "References"/"Bibliography" heading onward; drop lines that are mostly citations.
4. **Document-aware chunking.** The transcript chunker is speaker-turn oriented; add `chunk_document(text)` — a section/paragraph-aware sliding window at the existing ~350-word target with overlap, so a claim and its context land in one chunk. Falls back cleanly on unstructured text.
5. **Store as the paper's body_text** (replacing the abstract when full text succeeds), same `create_author_content` path → same embed rails. Abstract stays the fallback when OA fetch/extract fails.

## What changes vs. stays

- **Reuses:** `create_author_content`, the embed sweep + immediate-enqueue, Jina, the paper source/attribution model, the #92 title-dedup + abstract floor.
- **New:** OA-URL resolution (read 2 fields), an OA-text fetch path, boilerplate stripping, `chunk_document`. All small, no migration, no new dep.

## Cost & volume

- Full text is ~20–40× abstract size → chunk count for papers grows from ~1/paper to ~10–15/paper. Bounded by the existing `PAPER_LIMIT=30`/expert; a 15-expert area is ~4,500 chunks — fine for pgvector HNSW.
- Embedding is free (Mac). Jina fetches are metered but modest (≤30 papers/expert, one-time per paper, cached as the Document/content row). Cost-tracked via `api_usage`.
- Extraction (inquiry-time) already caps evidence per expert (`CORPUS_TOP_K=8` chunks), so richer corpus improves *retrieval quality* without inflating per-inquiry LLM cost.

## Risks & mitigations

- **PDF extraction noise** (columns, running heads, ligatures). Mitigation: Jina's markdown is generally clean; boilerplate stripping; and the grounding gate already drops any quote that isn't a verbatim substring — so garbage extraction fails safe (drops), it doesn't corrupt.
- **Landing-page-only OA** (~1/3 of OA): may be an HTML abstract page, not full text. Mitigation: length check — if fetched text isn't materially longer than the abstract, keep the abstract.
- **Re-ingestion churn:** full-text upgrade of already-abstract-ingested papers needs an idempotent "upgrade in place" (match by normalized title, replace body_text, re-chunk) rather than a new row. One-time backfill for the existing 314.

## Phasing

- **W3.3a — forward path:** new paper ingestion fetches OA full text (abstract fallback). Small PR.
- **W3.3b — backfill:** an idempotent job that upgrades the existing 314 abstract-only papers to full text where OA. Reuses the extraction path; re-chunks in place.

## Recommendation

Worth doing — 90% OA availability + grounding-first design make the depth gain real, and it reuses W1/W3.2 infra with no new dependency or migration. Suggest W3.3a first (forward path, contained), validate on a re-run of the rapamycin inquiry (corpus lane should carry more, with sharper dosed/population-specific quotes), then W3.3b backfill.

**Effort:** ~1 focused PR for W3.3a, ~1 for W3.3b. Medium, not large — the reuse is the reason.
