# Sonnet Chain

Participant runtime for Saruku in FLOP Labs' official Technocore Sonnet Challenge.

This repository is intentionally separate from Saruku / Second Session runtime code. It does not modify State & Growth, History, Room Discovery, `ss-engine`, or `ss-web`.

Codex is not the runtime. `sonnet-chain run` starts a local Python daemon which
long-polls Technocore and persists its own state. OpenAI is contacted only when a
team or poetry decision actually needs model judgment.

## Official source of truth

Rules and validation come from `flop-labs/technocore-sonnet-challange`.

Current pre-launch candidate observed on 2026-09-11:

- commit: `624fe936212e865b128047c5c4c1c21bfa80454b`
- package version: `0.5.0-draft`
- contest: `sonnet-1`
- opening: `2026-09-11T12:00:00Z`
- deadline: `2026-09-18T12:00:00Z`

The candidate commit is **not treated as the trusted live launch**. Before live automation, pin the manifest SHA-256 and referee DID from FLOP Labs' signed launch announcement.

## Saruku identity

Expected writer DID:

`did:key:z6MkpbdDcpSyuxmuivLEiYYoLWqn3wD433rPYBa7oG8EAVX8`

The signing seed is never stored here. The runtime derives the DID from the configured local seed and refuses live writes if it differs from the expected DID.

## Safety model

- dry-run by default;
- every Technocore write requires `--live`;
- no secret values in Git;
- no copied Saruku state/history/personality;
- no assumption that a chat post was accepted by the referee;
- no trust in a referee receipt until the referee DID is pinned from the launch announcement.

## Setup

```bash
git clone https://github.com/haruketh/sonnet-chain.git
cd sonnet-chain
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Prepare a local copy of the official package:

```bash
sonnet-chain prepare-package
```

Point at Saruku's existing signing seed file:

```bash
export SONNET_SEED_FILE=/absolute/path/to/saruku-ed25519-seed
```

Writer registration also needs Saruku's public X URL:

```bash
export SONNET_X_ACCOUNT_URL=https://x.com/sarukubt
```

Run local checks:

```bash
sonnet-chain doctor
pytest
```

`doctor` still verifies the candidate package when launch secrets are absent, but
exits nonzero with `NOT READY` until the seed path, referee DID and trusted
manifest hash are configured. It never searches for or copies those values.

## Daemon

Read-only operation is the default:

```bash
sonnet-chain run
sonnet-chain run --max-cycles 3
sonnet-chain status
```

Every Technocore POST is gated by `--live`. The daemon begins in `WAIT_LAUNCH`
and does not register merely because the rules room exists or contains messages.
It requires a `did:key:` owner note and an owner-signed launch record naming
`sonnet-1`, an HTTPS manifest URL, a SHA-256, and a matching referee DID. The
downloaded package, manifest and contest configuration must then verify locally.

State is stored atomically in ignored `state/sonnet-chain.sqlite3`. It includes
room cursors/generations, processed events, request IDs, receipts, team selection,
poem state and publication/submission state. Restarting reuses a pending request
ID, allowing the referee's idempotency rule to resolve an ambiguous POST.

The full phase chain is:

```text
WAIT_LAUNCH -> VERIFY_LAUNCH -> REGISTER -> WAIT_REGISTRATION_RECEIPT
-> DISCOVERY -> SELECT_TEAM -> NEGOTIATE -> WAIT_TEAM_SETUP
-> ROSTER_CONSENT -> WAIT_ROSTER_READY -> WRITING -> POEM_COMPLETE
-> PUBLISH_IF_FINAL_CONTRIBUTOR -> SUBMIT -> WAIT_SUBMISSION_RECEIPT -> DONE
```

Unknown receipt shapes do not advance this chain. Only a locally verified
signature from the pinned referee DID can become a receipt candidate, and its
request/team/room generation/version fields must also match local pending state.

Watch live rooms without writing:

```bash
sonnet-chain watch d-sonnet-1-rules
sonnet-chain watch mb-sonnet-1-registration
sonnet-chain watch mb-sonnet-1-discovery
```

Generate, but do not post, the registration record:

```bash
sonnet-chain register
```

Live post, only after the official contest launch:

```bash
sonnet-chain register --live
```

## Launch trust

After FLOP Labs publishes the launch announcement, configure:

```bash
export SONNET_REFEREE_DID='did:key:...'
export SONNET_MANIFEST_SHA256='...'
```

Then rerun:

```bash
sonnet-chain doctor
```

The rules room is configurable with `SONNET_RULES_ROOM`; this allows a repaired
official room to be selected without a code change. GitHub HEAD may help discover
a new package but is never itself the live trust anchor.

## OpenAI decision adapter

Configure only a path to a key file and a model name:

```bash
export SONNET_OPENAI_API_KEY_FILE=/absolute/path/to/openai-api-key
export SONNET_MODEL=gpt-5.1
```

Connection and injection-handling check (no Technocore write):

```bash
sonnet-chain llm-check
```

The adapter uses the Responses API with strict structured output. Technocore
text is labeled untrusted data. The model cannot invoke tools: Python accepts
only allowlisted decision values and deterministically rechecks every proposed
action and word. API failures leave state unchanged and cause no write.

## Publisher adapter

If Saruku is the final contributor, configure an external executable:

```bash
export SONNET_X_PUBLISH_CMD=/absolute/path/to/publisher-adapter
```

It receives JSON containing line-safe post chunks on stdin and must return
`{"post_ids":["..."]}` on stdout. It is launched without a shell. Credentials
remain external. Without the adapter the daemon persists the poem and stops in
`WAIT_PUBLISHER`; it never attempts to discover X credentials.

The bundled `sonnet-chain-x-publisher` adapter reads an existing X token JSON
through `SONNET_X_TOKEN_FILE`. It never refreshes or modifies that shared token.
It records each part as pending in a separate SQLite database before a request;
an ambiguous response therefore stops instead of risking a duplicate. Completed
post IDs are reused after restart, and replies are chained in line order.

Exercise only its dry-run path with:

```bash
printf '%s' '{"posts":["first line","second line"]}' \
  | sonnet-chain-x-publisher --dry-run
```

## Local runtime environment

`runtime.env` is optional, automatically loaded without shell evaluation, must
be owned by the current user with mode 600, and is ignored by Git. Keep secret
values out of it; use absolute paths such as:

```text
SONNET_SEED_FILE=/absolute/path/to/existing-seed
SONNET_OPENAI_API_KEY_FILE=/absolute/path/to/existing-key-file
SONNET_X_TOKEN_FILE=/absolute/path/to/existing-token-json
```

Environment variables already present in the process take precedence. Secret
files must be owner-only regular files (mode 600); symlinks and oversized files
are rejected.

## Recovery

Stop and restart the process with the same `SONNET_STATE_DB`. Inspect it through
`sonnet-chain status`; do not edit the database while the daemon is running.
Network failures use bounded exponential backoff, normal long-poll expiry is not
an error, replayed room records are ignored, and deadline-expired writes are
suppressed. Runtime DBs, logs and generated word indexes remain ignored by Git.

## Official protocol helpers

The CLI can construct and optionally post the documented actions:

```bash
sonnet-chain team-request --game-id saruku1
sonnet-chain roster --game-id saruku1 --poem-room d-sonnet-1-team-saruku1 \
  --room-generation 0 --member did:key:... --member did:key:... --member did:key:... --member did:key:...
sonnet-chain withdraw --game-id saruku1
sonnet-chain word --game-id saruku1 --poem-room d-sonnet-1-team-saruku1 \
  --room-generation 0 --version 3 --previous-state-hash HASH --word "dream"
```

`word` invokes the official package's `scripts/check_word.py` before a proposal is posted.

## High-frequency monitoring

Technocore supports `since=<seq>&wait=10`. The server returns immediately when a message arrives, so this is preferable to fixed 5-second polling and avoids unnecessary requests.

## What remains launch-dependent

The public rules describe that the referee will return signed receipts, but do not freeze every live receipt JSON shape in the public package. We therefore do **not** invent receipt fields.

At launch:

1. observe the signed rules/launch record;
2. pin referee DID + manifest hash;
3. capture one registration receipt and one discovery/team receipt;
4. implement/test the exact receipt reducer;
5. enable autonomous recruitment and composition on top of that reducer.

See `docs/PARTICIPANT_DESIGN.md`.
