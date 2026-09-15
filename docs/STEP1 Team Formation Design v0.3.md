# STEP1 — Team Formation Design v0.3

Status: **DESIGN PASS / FROZEN**
Date: 2026-09-15

## 1. Authority and scope

This document is the normative detailed STEP1 design once v0.3 is activated and
supersedes `STEP1 Team Formation Design v0.2.md` on conflict. All v0.2 safety,
history, transport, timeout, reconciliation, outbox, restart, withdrawal,
candidate-comparison, and authoritative-completion rules remain normative except
for the countersign timing policy replaced below. The v0.2 document remains an
unchanged historical design.

This version changes only when Saruku may submit its own exact canonical roster
consent. It does not change official team membership: a team still requires 4–8
members and all applicable members' current consent to the exact same canonical
roster.

## 2. Progressive Countersign

`MIN_EXTERNAL_COUNTERSIGNERS = 2`.

Saruku may countersign an exact canonical roster when every condition below is
true:

1. It is a valid `sonnet.roster.v1` with 4–8 members, including Saruku.
2. Its `game_id` is the active application game's exact `game_id`.
3. The application inviter is a current signer of this exact roster.
4. At least one additional non-Saruku member is a current signer of this exact
   roster, so there are at least two external current signers in total.
5. When an application anchor sequence exists, the inviter's current exact-roster
   consent sequence is strictly greater than that anchor.
6. Required Discovery history is complete.
7. The team room has the correct referee owner and generation, complete required
   history, and is open and not frozen or closed.
8. Saruku has no unresolved possible-consent or withdrawal state.
9. Exact canonical roster identity is preserved. Consent for different roster
   variants is never combined.

This policy applies identically to rosters of 4, 5, 6, 7, and 8 members. The
inviter alone is insufficient. Two non-Saruku signers without the inviter are
also insufficient. A withdrawal or a later consent to another roster removes a
signer from the current exact-roster set.

## 3. Protocol consensus and post-consent behavior

Progressive Countersign is local action eligibility, not full protocol consensus.
The default protocol-level consensus calculation remains all other applicable
members consenting.

Before the network side effect, Saruku durably persists the exact request ID,
payload, and roster fingerprint. Posted-but-unconfirmed or delivery-unknown
consent remains possibly consented and blocks switching, another application,
another countersign, and pre-consent escape. Confirmed consent enters
`WAIT_ROSTER_READY`; it does not enter `WRITING` until authoritative full team
completion. Existing 20-minute observation, 60-minute safe-withdraw policy,
history gates, and exact-request reconciliation remain unchanged.

## 4. Decision precedence and observability

The v0.2 decision precedence is unchanged. `READY_TO_COUNTERSIGN` continues to
precede hard stall, but readiness now means the progressive inviter-anchored rule
above. Reducer state, candidate state, hard-stall boundary checks, and the actual
countersign executor must use the same eligibility rule.

Operational reasons must describe progressive readiness (for example,
`progressive_inviter_anchored_roster` and
`sufficient_external_current_consent`) and must not claim full consensus. Signer,
member, and missing-signer counts remain observable.

## 5. Invariants

- Signature verification, latest-action consent, history completeness, strict
  roster identity, room verification, and authoritative terminal facts retain
  their v0.2 meaning.
- Full team readiness still requires authoritative complete consensus.
- Early Saruku consent cannot create an active team or writing phase.
- No LLM has authority over countersigning.
- No timeout, application, acquisition, Reflex, writing, submission, or public
  Web semantics change in v0.3.
