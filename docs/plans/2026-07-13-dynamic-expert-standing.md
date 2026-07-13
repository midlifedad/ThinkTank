# Dynamic Expert Standing

**Status:** Phase 1 in progress · Phases 2–4 designed, not started
**Approved:** Amir, 2026-07-13 ("I like this. Let's proceed.")

## The problem, in one screenshot

The 2026-07-13 area search for *"AI coding and agentic engineering"* produced:

| Candidate | Rubric verdict | Reality |
|---|---|---|
| Yoshua Bengio (75), M. Jordan (71), Schmidhuber (71) | promoted | eminent in ML *generally*; peripheral to agentic engineering |
| Lilian Weng (27) | auto_rejected | author of the canonical agent-systems essays |
| Yohei Nakajima (15) | auto_rejected | created BabyAGI |
| Sebastian Bubeck (10) | auto_rejected | "Sparks of AGI" lead |

The deterministic rubric measures **general eminence** (citations, Wikipedia,
books, content) — signals that transfer across domains. **Domain centrality
does not transfer**, and it is not present in any countable evidence. It
requires judgment. Separately, the score is a point-in-time vetting snapshot:
nothing recomputes it as reality changes, and it captures none of what an
expert *does after admission*.

## Design principles (carried from the vetting + claims work)

1. **Deterministic where evidence is countable, LLM where the question is
   judgment.** Never ask an LLM to emit the whole number; never ask regex to
   assess centrality.
2. **Every LLM output is persisted with its reasoning** (auditable, revisable).
3. **Gate ≠ standing.** Admission is a one-time decision and stays immutable
   on the candidate row. Standing is periodic, append-only, time-series.
4. **Provenance and dates on everything** — the claims layer's `asserted_at`
   discipline is what makes standing computable as a function of time.

## Phase 1 — Domain fit + roster critic (this PR)

Fixes the observed mis-ranking. Two LLM surfaces added to vetting:

### 1a. Domain-fit assessment (per candidate)

`discovery/domain_fit.py` — one schema-enforced LLM call when the candidate
has a `search_area`:

```
{centrality: core|adjacent|peripheral, fit_score: 0-20, reasoning: str}
```

- Persisted into the evidence dossier (`dossier["domain_fit"]`) and
  `score_breakdown["domain_fit"]`; reasoning stored alongside.
- **Routing, not just addition** (additive alone fixes neither end):
  - *Rescue path:* `centrality=core` + content leg met + total ≥ rescue floor
    → shortlist to the LLM judge even below the shortlist threshold
    (the Weng/Nakajima fix — same pattern as the practitioner path).
  - *Peripheral flag:* `centrality=peripheral` on a shortlist-passing scorer →
    the judge prompt receives the fit assessment and is instructed that
    area-centrality is a promotion criterion (the Bengio fix — those
    candidates already pass through the judge; it just never knew the area
    mattered).
- Config knobs (system_config): `expert_gate_fit_rescue_floor` (default 15
  total), fit call on/off.

### 1b. Roster critic (per area)

New job `critique_roster` (+ `roster_critiques` table, migration 018):

- Input: the full vetted slate for an area (names, verdicts, breakdowns).
- One LLM call → `{misranked: [{name, issue}], missing: [{name, why}]}`.
- `missing` names are inserted as new candidates and vetted through the
  normal pipeline (they get dossiers + the same gate — the critic nominates,
  it never promotes).
- `misranked` entries are stored and rendered on the Experts admin page
  under the area section; flagged candidates can be re-vetted from there.
- Trigger: admin button per area + auto-enqueue when an area's last
  `vet_candidate` completes.

**Validation:** re-vet the AI-coding area (`force`); expected outcome is
Weng/Nakajima/Bubeck reaching the judge via rescue, Bengio-class candidates
receiving centrality scrutiny, and the critic surfacing missing names.

## Phase 2 — Standing snapshots (time series)

- `thinker_standing` table modeled on `thinker_metrics`' pattern (append-only,
  one snapshot per thinker per day max, unique functional index).
- Snapshot components: refreshed rubric bands (same evidence probes),
  domain-fit per tracked area, clout (Phase 3), recency factor.
- Recurring task (existing scheduler) recomputes weekly per active thinker;
  evidence probes are the cheap public APIs already used by vetting.
- Recency decay: no new content/citations/appearances → standing drifts, on
  the argument that silence is data.
- Admin: trajectory sparkline on the thinker detail page.

## Phase 3 — Endorsement graph (clout)

- `thinker_endorsements(endorser, endorsee, kind, signed_weight, quote,
  content_id|document_id, asserted_at)` — append-only, provenance-required,
  same CHECK discipline as claim_observations. Signed: critique edges count.
- Cheap edges first (no LLM): co-appearance from `content_thinkers`,
  host-invited-guest, OpenAlex citation edges.
- Strong edges: an extraction lane harvesting expert-about-expert statements
  from transcripts (the corpus embedding backfill makes these findable).
- **Clout = personalized PageRank** over the graph, seeded by rubric scores,
  damped against mutual-admiration clusters. Because edges carry
  `asserted_at`, clout-at-time-T is computable for any T.
- Clout becomes a standing component (Phase 2 snapshot) and, later, the
  weighting for stance-matrix consensus views.

## Phase 4 — Track record (the differentiated signal)

Requires accumulated inquiry data; the substrate already exists:

- **Prediction scoring:** `claim_type='prediction'` observations carry
  verbatim quotes + `asserted_at`. Periodically resolve aged predictions
  (LLM-assisted with web verification) → right/wrong/unresolved →
  reliability component of standing. Standing earned by being *right*.
- **Updating behavior:** observations resolve onto canonical claims, so an
  expert's stance on the *same proposition* is visible across years.
  Updating on evidence ↑; erratic flip-flops ↓; never updating against
  overwhelming evidence ↓.

## Cost posture

Phase 1: ~1 fit call per candidate (~$0.02) + 1 critic call per area search.
Phase 2: zero LLM (evidence probes are free public APIs).
Phase 3: extraction lane batched over already-embedded chunks.
Phase 4: bounded by prediction volume; runs on schedule, not per-request.

All calls cost-tracked via `api_usage` (A2 pattern), as everywhere else.
