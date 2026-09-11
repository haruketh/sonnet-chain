# Sonnet Chain

Participant runtime for Saruku in FLOP Labs' official Technocore Sonnet Challenge.

This repository is intentionally separate from Saruku / Second Session runtime code. It does not modify State & Growth, History, Room Discovery, `ss-engine`, or `ss-web`.

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
