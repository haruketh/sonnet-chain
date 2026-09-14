# Sonnet Mission Web v2 Design v0.1

Status: DESIGN READY

Scope: Sonnet Mission public Web only

Runtime impact: Sonnet daemon behavior must not change

## 1. Purpose

Sonnet Mission Web v2 extends the existing public mission tracker so that, once Saruku reaches WRITING, observers can watch the poem being created and understand the collaboration occurring inside the active team.

The primary experience changes from:

```text
team formation progress
```

to:

```text
team formation progress
        ↓
current accepted poem
        ↓
live public-safe team activity
        ↓
completion / submission
```

The Web is an observer only. It must never influence team formation, writing decisions, protocol behavior, timing, retries, or submission.

---

## 2. Safety and Architecture Boundary

Keep the existing one-way architecture.

```text
sonnet-chain production SQLite
        ↓ read-only
deterministic public exporter
        ↓
local public-safe JSON
        ↓ outbound only
Cloudflare Workers KV
        ↓
read-only Worker /api/sonnet
        ↓
browser UI
```

The Web must not create any inbound path to the Mac mini.

The following must remain unchanged:

- Sonnet daemon does not depend on the exporter.
- Export failure does not affect contest execution.
- Web failure does not affect contest execution.
- No browser or Worker access to the production SQLite DB.
- No Cloudflare credential enters browser code.
- No LLM is used by the Web exporter.
- No runtime daemon logic is changed for v2.

---

## 3. Update Frequency

v2 target:

```text
Exporter → every 60 seconds
Browser polling → every 60 seconds
```

The browser already polls `/api/sonnet` every 60 seconds.

Change the independent public exporter schedule from 300 seconds to 60 seconds during production activation.

Do not modify the Sonnet daemon polling cadence.

The exporter must remain bounded and read-only because the production DB may be hundreds of MB.

---

## 4. Public Schema Version

Introduce:

```text
schema_version: 2
```

All existing v1 fields remain semantically compatible.

Add one optional top-level object:

```json
"writing": null
```

before Writing, and a populated object from WRITING onward.

Conceptual schema:

```json
{
  "schema_version": 2,
  "checked_at": "...",
  "tracking_started_at": "...",

  "mission": {
    "title": "...",
    "goal": "...",
    "deadline": "...",
    "status": "...",
    "current_stage": "...",
    "current_game": "..."
  },

  "counts": {
    "...": 0
  },

  "recent_activity": [],

  "writing": {
    "version": 18,
    "line_number": 4,
    "lines": [
      "...",
      "..."
    ],
    "previous_contributor": "Teammate B",
    "team": [
      "Saruku",
      "Teammate A",
      "Teammate B",
      "Teammate C"
    ],
    "activity": []
  }
}
```

`writing` is:

```text
null
```

for pre-WRITING phases.

It remains available through:

```text
WRITING
POEM_COMPLETE
submission_pending
SUBMITTED
```

so the finished poem remains visible.

---

## 5. Current Poem Authority

The displayed poem must represent only authoritative accepted state.

Use only state derived from trusted referee acceptance, such as the runtime's durable:

- `poem_lines`
- `poem_version`
- current line information
- `previous_contributor`
- `final_contributor`

Do not construct the public poem from proposals in the team room.

Do not show an unaccepted word inside the poem.

The public distinction is:

```text
PROPOSED WORD ≠ POEM

ACCEPTED WORD → may become part of POEM
```

If exporter evidence is incomplete or inconsistent, fail closed by omitting the affected Writing field rather than reconstructing or guessing.

---

## 6. Team Member Display Names

Do not publish full DID values in Web v2.

Use:

```text
Saruku
Teammate A
Teammate B
Teammate C
...
Teammate G
```

Maximum team size is eight including Saruku.

Alias assignment must be deterministic and stable.

Use the canonical active roster ordering.

Mapping rule:

```text
Saruku DID → Saruku

other canonical roster members:
first  → Teammate A
second → Teammate B
...
```

Do not assign names based on message arrival order.

Refreshing the page must never change identities.

A future version may expose a short DID, but v2 initial release should not.

---

## 7. Writing Activity

### 7.1 Public API retention

Keep up to:

```text
40 Writing activity items
```

inside the exported JSON.

This permits future history UI without changing the producer again.

### 7.2 UI display

Show the latest:

```text
10 activity items
```

on the Writing page.

The newest item appears first.

No pagination is required in v2.

### 7.3 Required activity types

Support at least:

```text
writing_started
message
word_proposed
word_accepted
line_completed
poem_completed
```

`word_rejected` may be added only when an authoritative durable rejection record exists and can be deterministically attributed. Do not invent it from absence of acceptance.

---

## 8. Public Activity Authority

### 8.1 Team messages

A teammate message may be published only when all are true:

```text
current active game
AND
current poem/team room
AND
current room generation
AND
sender belongs to canonical active roster
AND
message signature was verified
```

Only public, human-readable team-room speech is exposed.

For structured `sonnet.note.v1`, publish only its bounded public `text`.

For other verified protocol messages, do not dump raw JSON into the activity feed.

Machine protocol envelopes should instead become typed public activities where appropriate.

Maximum public message length:

```text
500 characters
```

Longer source text must be safely truncated.

React/HTML output must remain escaped as ordinary text.

### 8.2 Saruku speech

Saruku's displayed speech must also come from a real verified/persisted team-room message.

Never publish:

- draft LLM text that was never posted
- rejected generated notes
- private reasoning
- prompts
- hidden planner state

### 8.3 Word proposals

`word_proposed` may be shown only from an actual persisted word proposal associated with the current game/room/generation.

Example:

```text
SARUKU
Proposed “drifts”
```

A proposal must never be inserted into the displayed poem until accepted.

### 8.4 Accepted words

`word_accepted` authority is the trusted referee acceptance path.

Example:

```text
WORD ACCEPTED
Teammate B → “shadow”
```

When contributor attribution is unavailable, omit actor rather than infer it.

### 8.5 Line completion

`line_completed` may be derived deterministically from consecutive authoritative accepted poem states.

Do not use an LLM.

### 8.6 Poem completion

`poem_completed` comes only from authoritative completion state / receipt.

---

## 9. Information That Must Remain Private

Web v2 must not publish:

- full DIDs
- signatures
- request IDs
- raw internal DB rows
- source sequence numbers
- filesystem paths
- API credentials
- Cloudflare credentials
- X credentials
- prompts
- LLM candidate arrays
- rejected internal candidate words unless an official protocol rejection is intentionally public
- candidate validation reasons
- branch counts
- qualification calculations
- confidence values
- internal semantic extraction metadata
- anti-abuse thresholds
- stall thresholds
- private planning state
- raw model output
- hidden chain-of-thought or reasoning
- internal exception details

The Web exposes what happened publicly, not why the internal engine mathematically chose it.

---

## 10. UI

Before WRITING, preserve the current mission view:

```text
Current state
Mission progress
Counters
Recent activity
```

Once status becomes `writing`, make Current Poem the visual focus.

Recommended hierarchy:

```text
SONNET MISSION

WRITING
Team · <game_id>

CURRENT POEM

Line 1
Line 2
Line 3
...

Line 4 of 14
Version 18
Last contribution · Teammate B

LIVE ACTIVITY

[latest 10 events]
```

Example:

```text
23:54  WORD ACCEPTED
       Teammate B → “shadow”

23:53  Teammate A
       “Saruku, can you close this line?”

23:52  SARUKU
       “I can take the next word.”

23:51  WORD ACCEPTED
       Saruku → “drifts”

23:50  SARUKU
       Proposed “drifts”
```

The Current Poem must visually dominate the page.

Mission counters should remain available but become secondary during Writing.

---

## 11. Existing Recent Activity Bug

Current v1 behavior queries a mixed set of:

```text
TARGETED_RECRUITMENT_NOTE
ROSTER_CONSENT
APPLICATION_READBACK
```

with a bounded LIMIT before discarding unrelated rosters.

Because Discovery contains a very high volume of unrelated `ROSTER_CONSENT` events, a valid older Saruku `APPLICATION_READBACK` can be pushed outside the candidate window.

Observed effect:

```text
Applications counter = 2
Recent Activity Applied entries = 1
```

v2 must fix this.

Do not solve it by simply increasing the mixed LIMIT.

Preferred approach:

- query Saruku-relevant applications independently;
- query targeted invitations independently;
- query only rosters containing Saruku or otherwise make the roster query relevant before applying its bound;
- merge normalized public activities afterward;
- sort by authoritative timestamp;
- then apply `MAX_ACTIVITY`.

Add a regression test containing more than 160 unrelated roster events between two valid Saruku application readbacks.

Both applications must remain present in public activity.

---

## 12. Performance Requirements

The 60-second exporter must remain cheap.

Requirements:

- SQLite opened read-only.
- No DB mutation.
- No full-table repeated history scans.
- Use existing indexed room/generation/game/seq paths where possible.
- Writing team-room reads restricted to the current room and generation.
- Public activity remains bounded.
- No network reads from Technocore by exporter.
- No LLM calls.
- Cloudflare publish remains independent of state generation.
- Failed publish must leave last valid KV value intact.

If a new SQLite index is genuinely required, stop and request review before adding it. Prefer existing indexes.

---

## 13. Schema Rollout Compatibility

Avoid an exporter/Worker race.

Production rollout order:

```text
1. Deploy Worker/UI capable of accepting schema v1 AND v2.
2. Verify existing v1 production JSON still renders.
3. Activate 60-second exporter publishing schema v2.
4. Verify /api/sonnet returns schema_version 2.
5. Verify Writing/non-Writing UI behavior.
6. Only later consider removing v1 compatibility.
```

Do not publish v2 before the Worker accepts it.

---

## 14. Testing Requirements

Required exporter tests:

- pre-WRITING → `writing = null`
- WRITING → writing object populated
- COMPLETE/SUBMITTED → poem remains visible
- accepted poem only comes from authoritative state
- proposal does not alter poem
- alias mapping is deterministic
- Saruku is always `Saruku`
- teammates become A/B/C... deterministically
- full DID never leaks
- signature never leaks
- request ID never leaks
- internal candidate/reason data never leaks
- wrong room rejected
- wrong generation rejected
- non-roster sender rejected
- unverified source rejected
- verified active-roster message included
- word proposal typed correctly
- accepted word typed correctly
- maximum Writing activity 40
- current mixed-LIMIT Application bug regression
- 160+ unrelated rosters cannot hide Saruku applications

Required Web tests:

- schema v1 accepted during transition
- schema v2 accepted
- invalid schema rejected
- malformed writing rejected
- `writing:null` renders existing Mission UI
- WRITING renders Current Poem
- latest 10 Writing activities displayed
- >10 Writing activities are not all rendered
- HTML/message text safely escaped
- teammate aliases displayed instead of DID
- poem remains visible at COMPLETE/SUBMITTED
- stale/fetch-error behavior remains safe

---

## 15. Production Success Criteria

Web v2 is PASS when:

```text
Sonnet daemon unchanged
Sonnet daemon restart unnecessary
Exporter reads production DB read-only
Exporter cycle = 60 sec
Browser poll = 60 sec
/api/sonnet schema = 2
team aliases contain no DID
Current Poem equals accepted runtime poem
proposed word never appears as accepted poem prematurely
latest 10 Writing activities render
two known applications both remain visible in activity history
no private/internal fields leak
export/publish failure cannot affect contest execution
```

No production Sonnet behavior change is part of this project.
