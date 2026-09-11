# AGENTS.md

Instructions for Codex/agents working in `haruketh/sonnet-chain`.

## Purpose

This repository is a standalone participant runtime for Saruku in FLOP Labs' official Technocore Sonnet Challenge. It is not part of the Saruku or Second Session product runtime.

## Hard boundaries

Do not:

- import code from `/Users/flop/flop-agent`, `ss-engine`, or `ss-web`;
- modify Saruku History, State & Growth, Room Discovery, Public State, or launchd jobs;
- copy any secret, `.env`, signing seed, X token, API key, runtime state, or private log into this repository;
- change the official contest rules locally and then treat them as official;
- invent referee receipt fields that are not observed or specified;
- assume a successful Technocore POST means a move was accepted.

It is allowed to read a secret **by local path at runtime**. Store only the path in the process environment, never the value in Git.

## Official source

Treat the signed FLOP Labs launch announcement plus its pinned challenge manifest as the live source of truth.

Pre-launch development may use candidate official commit:

`624fe936212e865b128047c5c4c1c21bfa80454b`

but do not silently promote that draft to trusted live rules.

## Saruku identity

Expected DID:

`did:key:z6MkpbdDcpSyuxmuivLEiYYoLWqn3wD433rPYBa7oG8EAVX8`

Any live-writing command must derive the DID from the configured seed and fail closed if it differs.

## Development rules

- Python 3.12+.
- Keep networking in `technocore.py`.
- Keep signature logic in `signing.py`.
- Keep official wire records in `protocol.py`.
- Use the official package's validator instead of reimplementing contest word rules.
- Long-poll Technocore with `since` + `wait<=10`; do not busy-poll.
- Append local observations under ignored `state/` or `logs/` only.
- Tests must not contact live Technocore.
- Live writes always require explicit `--live`.

## Launch activation

Before enabling autonomous behavior, capture and add tests for the exact live referee receipt shapes. Receipt parsing must authenticate the configured referee DID and reduce only signed, accepted state transitions.
