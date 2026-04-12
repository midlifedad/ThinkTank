# Phase 13: Efficient Episode Cataloging and Thinker Guest Detection - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-04-12
**Phase:** 13-efficient-episode-cataloging-and-thinker-guest-detection
**Areas discussed:** Content status lifecycle, Retroactive scanning, Source type expansion, Guest detection signals

---

## Content Status Lifecycle

| Option | Description | Selected |
|--------|-------------|----------|
| All sources use 'cataloged' | Every source starts episodes as 'cataloged'. Unified scan handler promotes based on source type. | Yes |
| Only guest sources use 'cataloged' | Host-owned sources keep status='pending' immediately. | |
| You decide | Claude picks. | |

**User's choice:** All sources use 'cataloged'
**Notes:** One pipeline, one pattern. Simpler than branching behavior by source type.

---

| Option | Description | Selected |
|--------|-------------|----------|
| Stay 'cataloged' permanently | Unmatched episodes sit in DB forever, prevent re-discovery, can be promoted later. | Yes |
| Expire after N days | Delete old cataloged episodes to save space. | |
| You decide | Claude picks. | |

**User's choice:** Stay 'cataloged' permanently
**Notes:** Enables retroactive scanning when new thinkers are added.

---

| Option | Description | Selected |
|--------|-------------|----------|
| Replace tag_content_thinkers with scan_episodes_for_thinkers | New handler does attribution + status promotion. Simpler pipeline. | Yes |
| Keep both, chain them | scan promotes, then tag does attribution. More granular. | |
| You decide | Claude picks. | |

**User's choice:** Replace tag_content_thinkers with scan_episodes_for_thinkers
**Notes:** Cleaner pipeline: fetch -> scan -> process_content.

---

## Retroactive Scanning

| Option | Description | Selected |
|--------|-------------|----------|
| Auto-rescan on thinker approval | Enqueue rescan_cataloged_for_thinker job when thinker approved. DB queries only, no API calls. | Yes |
| Manual rescan only | Operator triggers from admin panel. | |
| No retroactive scanning | Past episodes stay cataloged forever. | |

**User's choice:** Auto-rescan on thinker approval
**Notes:** Cheap DB-only operation. Ensures no episodes are missed when new thinkers join.

---

| Option | Description | Selected |
|--------|-------------|----------|
| Leave existing 'pending' alone | Clean cutover. Only new episodes start as 'cataloged'. | Yes |
| Retroactively demote unattributed 'pending' | Saves costs but adds migration complexity. | |
| You decide | Claude picks. | |

**User's choice:** Leave existing 'pending' alone
**Notes:** Clean migration. No retroactive demotion of already-queued episodes.

---

## Source Type Expansion

| Option | Description | Selected |
|--------|-------------|----------|
| RSS + YouTube + Spotify | Full multi-source in one phase. | |
| RSS + YouTube only | YouTube is higher value than Spotify. Spotify deferred. | Yes |
| RSS only | Smallest scope. YouTube and Spotify later. | |
| You decide | Claude picks. | |

**User's choice:** RSS + YouTube only
**Notes:** Spotify provides no structured guest credits. YouTube channels are more valuable for tracking thinker appearances.

---

| Option | Description | Selected |
|--------|-------------|----------|
| Manual source addition only | Operator adds YouTube channels via admin form. | Yes |
| Auto-discover from thinker names | Search YouTube for channels. Risk of false positives. | |
| You decide | Claude picks. | |

**User's choice:** Manual source addition only
**Notes:** Keeps control tight. Avoids fan/tribute channel noise.

---

| Option | Description | Selected |
|--------|-------------|----------|
| Reuse existing duration + title filters | Same RSS patterns, add YouTube-specific skip patterns. | |
| YouTube-specific content type detection | Use categoryId and contentDetails from YouTube API. Smarter filtering. | Yes |
| You decide | Claude picks. | |

**User's choice:** YouTube-specific content type detection
**Notes:** Use YouTube API's structured metadata (categoryId, duration in ISO 8601) for smarter filtering than just title patterns.

---

## Guest Detection Signals

| Option | Description | Selected |
|--------|-------------|----------|
| Parse podcast:person as bonus signal | Use stdlib XML parsing for Podcast 2.0 namespace. High confidence when available. | Yes |
| Skip podcast:person | Title/description matching only. Simpler. | |
| You decide | Claude picks. | |

**User's choice:** Parse podcast:person as bonus signal
**Notes:** Low effort, high value when present (~5-10% of feeds). Falls back to title/description matching.

---

| Option | Description | Selected |
|--------|-------------|----------|
| Title + description matching | Same approach as RSS. Consistent pattern. | Yes |
| Title + description + comments | More signal but noisy and costs API quota. | |
| You decide | Claude picks. | |

**User's choice:** Video title + description matching
**Notes:** Comments too noisy. Consistent approach across source types.

---

| Option | Description | Selected |
|--------|-------------|----------|
| No, keep candidate discovery separate | scan_episodes_for_thinkers only matches known thinkers. | Yes |
| Yes, merge candidate creation | Also extract unknown names during scan. Couples concerns. | |

**User's choice:** Keep candidate discovery separate
**Notes:** Clean separation. scan_for_candidates (Phase 6) handles candidate creation from arbitrary text.

---

## Claude's Discretion

- Pipeline job priority assignment for new job types
- YouTube API quota tracking implementation details
- Content_ids batching strategy in scan job payloads

## Deferred Ideas

- Spotify show support (Phase 14+)
- YouTube comment analysis for guest detection
- Auto-discovery of YouTube channels for thinkers
