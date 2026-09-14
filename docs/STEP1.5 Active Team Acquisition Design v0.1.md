# STEP1.5 — Active Team Acquisition Design v0.1

Status: **DESIGN PASS / FROZEN**

Date: 2026-09-14

## 1. Purpose

STEP1.5は、SarukuがSonnet-2 Team Formationにおいてdirect invitationを永久に待つだけのparticipantになることを防ぐ。

既存STEP1 v0.2のSafety Envelopeを変更せず、verifiedなformation stateからSaruku自身が参加可能なvacancyを発見し、applicationまで進める能力を追加する。

STEP1.5はSTEP1 v0.2へのsupplementであり、Team Formation authorityを置き換えない。

## 2. v0.1 Scope

v0.1でapplication opportunityになれるentry sourceは2種類のみとする。

```text
1. TARGETED_INVITE
2. ACTIVE_VACANCY
```

`TARGETED_INVITE`は既存STEP1 behavior。

`ACTIVE_VACANCY`のみ今回新規実装する。

以下はv0.1対象外とする。

```text
ACTIVE_TEAM_REQUEST
organizer-originated acquisition
arbitrary incomplete roster joining
natural-language recruitment mining
LLM candidate extraction
self-created team allocation
```

`sonnet.team-request.v1`をActive Acquisition authorityとして使用しない。

理由:

- team requestだけではroom allocationは成立しない
- authoritative setup/current binding schemaが現runtimeに存在しない
- live room generationは後からchange/rebindされ得る
- setup currentnessを推測してはならない

`ACTIVE_TEAM_REQUEST`は将来STEP1.5 v0.2で、authoritative setup/current-binding pathを設計した後に追加する。

## 3. Existing STEP1 Safety Invariants

以下を変更しない。

- active application epochは最大1
- applicationはroster consentではない
- POSSIBLY_CONSENTEDをpre-consent扱いしない
- unresolved consent中は別teamへapplicationしない
- unresolved consent中はswitchしない
- application transport reconciliationは既存STEP1を使用
- roster consentはfail-closed
- canonical rosterだけがmembership authority
- Sarukuは最後のsigner
- history gap中のunsafe absence inferenceは禁止
- terminal/stale opportunityを復活させない
- protocol mutation前にdurable write-ahead stateをpersistする
- active applicationからのswitchはSTEP1 safe-switch semanticsだけを使用する

## 4. ACTIVE_VACANCY Definition

verified `ROSTER_WITHDRAWAL`だけではcandidateにならない。

withdrawal直前のreduced formation stateで、次をすべて証明する。

```text
1. withdrawerのcurrent consentがexact roster Rに解決する

2. Rのmembersはexactly 4

3. withdrawerはRのmember

4. withdrawal後も少なくとも1人のsurviving memberが
   exact same Rへのcurrent consentを保持している

5. surviving current signerの中に
   trusted registered writerが少なくとも1人存在する

6. relevant consent / withdrawal historyがcomplete

7. Rのpoem room / generationが現在もverifiedかつopen

8. first accepted word等によってteamがfreezeしていない

9. SarukuはRに含まれていない
```

つまり、

> 過去に4人rosterへ所属していたparticipantがwithdrawした

だけではvacancyではない。

必要なのは、

> withdrawal直前にwithdrawer自身がexact roster Rへcurrent consentしていた

というverified current-state factである。

## 5. Formation Anchor

surviving current signersのうちtrusted registered writerを1人local formation anchorとして選択する。

これはprotocol上のleader authorityではない。

persisted compatibilityのため既存`inviter_did` fieldをanchor格納に利用してよい。

ただしACTIVE_VACANCYでは`inviter_did`はliteral invitationを意味しない。

可能ならbackward-compatibleに:

```text
opportunity_kind:
  TARGETED_INVITE
  ACTIVE_VACANCY
```

を追加する。

legacy opportunityは`TARGETED_INVITE`としてdecodeする。

destructive migrationやlegacy persisted field renameは禁止する。

## 6. Anchor Safety

Sarukuがapplicationした後に生成された新しいSaruku-containing exact rosterをcountersignするには、既存STEP1条件に加えて、

```text
selected anchor has current consent
to that exact new roster
after the Active Vacancy source boundary
```

を要求する。

したがって:

```text
A / B / C / D
D withdraw

Saruku application

A / B / C / Saruku
```

になってもapplicationだけではmembershipにならない。

A/B/Cのexact new roster consentが進み、anchorを含む他全memberがcurrent consentした後にだけSarukuは最後にcountersignする。

## 7. Freshness

explicit constants:

```text
ACTIVE_SEARCH_FRESHNESS = 60 minutes

ACTIVE_SEARCH_BOOTSTRAP_LOOKBACK = 60 minutes

ACTIVE_SEARCH_BOOTSTRAP_MAX_EVENTS = 256
```

freshness authorityは:

```text
trusted Technocore/server timestamp
of the qualifying ROSTER_WITHDRAWAL
```

のみ。

local processing time、journal time、observation timeを使わない。

selectionまたはrevalidation時に:

```text
now - withdrawal_timestamp > 60 minutes
```

ならcandidateを使用しない。

## 8. Candidate Priority

new opportunity selection:

```text
TARGETED_INVITE
>
ACTIVE_VACANCY
```

ただしpriorityはnew opportunityを選べる状態にだけ適用する。

```text
priority != switch authority
```

active application中にfresh TARGETED_INVITEを見つけても、既存applicationを即座に破棄しない。

active epochからのswitchは既存STEP1 v0.2の:

- structural comparator
- SOFT_STALL reevaluation
- history completeness
- consent uncertainty handling
- HARD_STALL semantics

だけを使用する。

## 9. Candidate Revalidation

ACTIVE_VACANCYは検出時だけでなくapplication直前にもauthoritative current stateを再検証する。

次の場合はcandidateをinvalidateする。

- newer roster structureがvacancyを解消
- room generationがRと一致しなくなった
- pinned refereeがroom ownerでなくなった
- roomがclosed/frozen/started
- historyがunresolved
- active applicationが発生
- consent outcomeがunresolved
- active teamが成立
- candidateが60分を超えた

特にroom generation mismatchはfail-closedとする。

これによりreferee-side rebind後の古いroster/withdrawalをvacancyとして使用しない。

## 10. Application

既存:

```text
sonnet.application.v1
```

を使用する。

LLMは使わない。

deterministic wording:

```text
Saruku is available to join <game_id> if you're still forming.
Registered Sonnet-2 writer. No live roster consent.
Ready to countersign the exact canonical roster when posted.
```

以下をclaimしない。

- invited
- joined
- member
- consented
- team ready

applicationはinterest signalに過ぎない。

## 11. Existing Application Lifecycle Reuse

Active Vacancy applicationはその後既存STEP1 lifecycleへ完全合流する。

```text
ACTIVE_VACANCY
↓
durable application intent
↓
POST sonnet.application.v1
↓
verified APPLICATION_READBACK
↓
APPLIED
↓
new Saruku-containing canonical roster
↓
anchor + other members current-consent
↓
READY_TO_COUNTERSIGN
↓
Saruku last countersign
```

新しい独自application state machineを作らない。

## 12. Replay / Idempotency

既存:

```text
formation_opportunities
consumed_at
consumed_request_id
```

を利用する。

同じwithdrawal structural opportunityから複数applicationを作らない。

restart後もconsumed stateを保持する。

terminal/expired application後、同じold withdrawal/source_seqを再利用しない。

再applicationにはstrictly newer qualifying structural evidenceを必要とする。

## 13. Incremental Performance

full history scanは禁止。

durable frontier:

```text
formation_active_search_frontier:<generation>
```

normal cycle:

```text
seq > frontier
```

のみ。

no-new-event warm pathはeffectively O(1)とする。

vacancy reconstructionが必要な場合も:

```text
game-scoped
indexed
bounded
```

queryのみを利用する。

## 14. First Activation Bootstrap

初回activationだけ:

```text
trusted source timestamp >= now - 60 minutes
AND
maximum 256 relevant events
```

とする。

historical vacancy miningは禁止。

60分より古いwithdrawalをretroactively applicationへ使用しない。

## 15. One Consequential Action

基本sequence:

```text
OBSERVE
↓
derive current vacancy candidates
↓
select one
↓
REVALIDATE
↓
durably persist application intent
↓
POST one application
↓
OBSERVE AGAIN
```

1 cycleで複数applicationしない。

## 16. Web Semantics

Web変更不要。

ACTIVE_VACANCY candidateを見つけただけではpublic statusを変えない。

local POST成功だけでも`APPLIED`にしない。

既存どおり:

```text
verified APPLICATION_READBACK
→ APPLIED
```

のみ。

ACTIVE_VACANCYを`targeted_invites`へcountしない。

## 17. Deferred v0.2

将来の`ACTIVE_TEAM_REQUEST`には最低限:

- authoritative team request fact
- authoritative accepted allocation
- exact game_id/request correlation
- actual poem_room
- current actual room_generation
- pinned referee ownership
- rebind/currentness handling
- rejected allocation handling

が必要。

generic receipt形状やsetup recordを推測して実装してはならない。

## 18. Final Rule

STEP1.5 v0.1は、

> any available teamへ積極的に飛び込む仕組み

ではない。

目的は、

> verified current four-person formationに本物の空席が生じたとき、STEP1 Safety Envelopeの内側でSarukuが自分から手を挙げられるようにすること

である。
