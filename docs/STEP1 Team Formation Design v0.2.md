# STEP1 — Team Formation Design v0.2

Status: **DESIGN PASS / FROZEN**  
Date: 2026-09-13

## 1. Purpose

STEP1の目的は、Sonnet-2 Discovery上の検証済みstructured eventをdeterministicに追跡し、Sarukuが安全かつlivenessを失わずteamを形成することである。

STEP1 v0.2ではTeam Formationの主判断をelapsed-time-drivenからevent-drivenへ変更する。

```text
Verified Discovery Events
        ↓
History Continuity / Verification
        ↓
Formation Reducer
        ↓
Current Formation State
        ↓
Structural Progress / Regression
        ↓
Deterministic Decision
        ↓
APPLY / STAY / COUNTERSIGN / SWITCH / WITHDRAW / WAIT
```

時間はTeam Formationのauthorityではない。

時間は、

> meaningful structural progressが停止したteamへ永久拘束されないためのwatchdog

としてのみ使用する。

---

# 2. Scope and Document Authority

対象lifecycle:

```text
REGISTERED
    ↓
DISCOVERY
    ↓
WAIT_ROSTER_READY
    ↓
WRITING
```

STEP1は主に、

```text
DISCOVERY
WAIT_ROSTER_READY
```

を担当する。

STEP2 Team Intelligence、STEP3 Decision Engine、STEP4 Writing Plannerは対象外である。

## 2.1 Normative authority

本設計を、

```text
docs/STEP1 Team Formation Design v0.2.md
```

としてSTEP1のnormative detailed designとする。

`PARTICIPANT_DESIGN.md` はinitial participant architecture / historical baselineとする。

競合時:

```text
STEP1 Team Formation Design v0.2
>
PARTICIPANT_DESIGN.md
```

とする。

---

# 3. Protocol Boundary

Sonnet protocol上のteam membership truthは、

```text
4–8 members
+
all applicable members consenting to
the exact same canonical roster
```

によって成立する。

Saruku独自のteam leader authorityは作らない。

以下はSaruku local policyである。

```text
SOFT_STALL = 20 min
HARD_STALL = 60 min

APPLICATION_RECONCILE_WINDOW = 10 min

candidate comparison policy
formation anchor policy
replacement recovery policy
transport reconciliation policy
```

---

# 4. Core Safety Principle

Saruku自身のroster consent状態を次の3分類で扱う。

```text
DEFINITELY_PRE_CONSENT
POSSIBLY_CONSENTED
CONFIRMED_CONSENTED
```

最重要invariant:

> `POSSIBLY_CONSENTED`を`DEFINITELY_PRE_CONSENT`として扱ってはならない。

Saruku自身の`sonnet.roster.v1`をPOSTした可能性がある場合、そのdelivery resultがauthoritatively解決するまで、

```text
pre-consent HARD_STALL
pre-consent uncertain-abandon
candidate switching
another teamへのapplication
another roster countersign
```

を禁止する。

---

# 5. Trust Model

Reducerへ入力できる情報:

```text
PROTOCOL FACT
TRUSTED REGISTRATION FACT
LOCAL FORMATION SIGNAL
ACTIVITY
UNKNOWN / IGNORED
```

sender roleはsigned note内の自己申告から決めない。

v0.2でrecruiterとして扱うsenderは、

```text
trusted accepted registration role = writer
```

のみ。

organizer-originated recruitmentは将来versionの対象とする。

---

# 6. Team Formation Event Contract

## 6.1 Official protocol events

| Reducer event | Source | Required verification | Effect |
|---|---|---|---|
| `TEAM_ROOM_REQUEST` | Discovery | valid `sonnet.team-request.v1` | room activity |
| `ROSTER_CONSENT` | Discovery | valid signed `sonnet.roster.v1` | signer current consent |
| `ROSTER_WITHDRAWAL` | Discovery | valid signed `sonnet.withdraw.v1` | invalidate signer consent |
| `REFEREE_RECEIPT` | relevant room | pinned referee | authoritative transition |
| `FIRST_WORD_ACCEPTED` | team room | pinned referee | roster freeze |

## 6.2 TARGETED_RECRUITMENT_NOTE

v0.2 local convention:

```text
type = sonnet.note.v1
room = mb-sonnet-2-discovery
contest_id = sonnet-2
valid game_id
target_did = SARUKU_DID
sender != SARUKU_DID
trusted sender role = writer
```

signed payload内の`role:"writer"`自体はauthorityではない。

`TARGETED_RECRUITMENT_NOTE`は、

```text
candidate opportunity
local inviter affirmation
```

にのみ利用する。

membership truthにはしない。

## 6.3 `sonnet.application.v1`

Saruku-local recruitment envelope。

作用:

```text
application lifecycle only
```

official roster consentではない。

## 6.4 Excluded invitation types

official vote/campaign用:

```text
sonnet.invite.v1
```

はTeam Formationには使用しない。

未定義:

```text
sonnet.invite.v2
```

もv0.2では使用しない。

allowlist外eventはTeam Formation stateを変更しない。

---

# 7. History Completeness

Discovery generationごとに以下を管理する。

```text
history_complete_from_seq
history_complete_through_seq
highest_observed_seq

gap_detected
gap_ranges

reconciliation_required
```

absence-of-event inferenceには、

```text
is_complete(start_seq, end_seq)
```

がtrueであることを必須とする。

gap中は、

```text
do not affirm pre-existing roster
do not countersign
do not conclude no withdrawal
do not perform stall-based switch
do not perform SAFE_WITHDRAW
```

を守る。

explicit trusted referee terminal evidenceはhistory gap中でも使用できる。

---

# 8. Formation Opportunity and Application Epoch

Formation Opportunity:

```text
game_id
inviter_did
poem_room / generation when known
```

Application Epoch:

```text
epoch_id
game_id
inviter_did

application_request_id

starting_invite_seq
application_source_seq
application_observed_at

application_delivery_state
application_reconcile_started_at
application_reconcile_deadline

terminal_seq
terminal_at
status
```

active application epochは最大1つ。

---

# 9. Re-invite Semantics

active epoch中、

```text
same game
same inviter
same underlying opportunity
```

へのre-inviteは:

```text
ACTIVITY / AFFIRMATION
```

として扱う。

以下は行わない。

```text
new epoch
new application
stall reset
high-watermark reset
hard-stall reset
```

terminal epochをhistorical eventから復活させない。

---

# 10. Application Delivery State

```text
NOT_SENT
POSTED_UNCONFIRMED
CONFIRMED
EXPLICITLY_NOT_PERSISTED
DELIVERY_UNKNOWN
```

## 10.1 POSTED_UNCONFIRMED

application POST後、Discoveryでsame request IDをまだ確認できない状態。

formation 20/60分timerは開始しない。

ただし無期限待機もしない。

---

# 11. Bounded Application Reconciliation

```text
APPLICATION_RECONCILE_WINDOW = 10 min
```

same request IDでのみreconcileする。

新しいrequest IDを連発しない。

以下をpersistする。

```text
application_reconcile_started_at
application_reconcile_deadline
```

deadlineは最初のsend intentで一度だけ設定する。

normative invariant:

> restart/replay does not extend the application reconciliation deadline.

通常runtime中のelapsed measurementにはmonotonic clockを使用してよい。

restart後はpersistent absolute deadlineから残時間を再構築する。

## 11.1 Confirmed

Discovery read-backでsame request IDを確認:

```text
application_delivery_state = CONFIRMED
application_source_seq = observed seq
application_observed_at = trusted server timestamp
```

ここからformation timerを開始する。

## 11.2 Explicit failure

persistされていないことがauthoritatively判明:

```text
EXPLICITLY_NOT_PERSISTED
→ application_failed
→ active_application = none
→ DISCOVERY
```

## 11.3 Indeterminate after deadline

deadlineまで解決不能:

```text
DELIVERY_UNKNOWN
→ abandoned_unconfirmed
→ active_application = none
→ DISCOVERY
```

これはteam/application failureを断定するものではない。

late appearanceしてもold epochを自動復活させない。

---

# 12. Application Timestamp

formation watchdogには、

```text
application_observed_at
```

のみを使用する。

source:

```text
Discovery read-back
Technocore server timestamp
```

local POST time、HTTP response time、journal write timeをformation authorityにはしない。

---

# 13. Per-Signer Consent Semantics

DID X / game Gについてlatest applicable actionをreduceする。

```text
latest = roster signature R
→ current consent = R

latest = withdraw
→ current consent = NONE
```

例:

```text
sign A
withdraw
→ NONE
```

```text
sign A
withdraw
sign A
→ A
```

```text
sign A
sign B
→ B only
```

history incompleteなら:

```text
UNKNOWN
```

としてcountしない。

---

# 14. Saruku Roster Consent Delivery State

Saruku自身のofficial `sonnet.roster.v1`にはapplicationとは別のdelivery stateを持つ。

```text
NOT_SENT

CONSENT_POSTED_UNCONFIRMED

CONSENT_CONFIRMED

CONSENT_EXPLICITLY_NOT_PERSISTED

CONSENT_DELIVERY_UNKNOWN
```

関連state:

```text
consent_request_id
consent_roster_fingerprint

consent_post_attempted_at

consent_source_seq
consent_observed_at

consent_reconciliation_required
```

---

# 15. Protocol Mutation Write-Ahead Safety

official Sonnet protocol stateを変更し得るoutbound actionは、**network side effectより前にdurable pending/uncertain stateをpersistしなければならない。**

normative sequence:

```text
derive exact protocol action
        ↓
generate/fix request_id
        ↓
freeze exact target payload / fingerprint
        ↓
DURABLY PERSIST SEND INTENT
        ↓
transaction commit / durable local state
        ↓
perform network POST
        ↓
authoritative reconciliation
```

## 15.1 Mandatory actions

少なくとも以下に必須:

```text
sonnet.roster.v1
sonnet.withdraw.v1
```

推奨:

```text
sonnet.application.v1
```

applicationはofficial membership mutationではないためSafety severityは低いが、同じdurable-outbox patternへ統一することを推奨する。

---

# 16. COUNTERSIGN Write-Ahead Sequence

`READY_TO_COUNTERSIGN`でSarukuがroster consentを実行する場合:

```text
READY_TO_COUNTERSIGN
        ↓
generate consent_request_id
        ↓
freeze exact canonical roster fingerprint
        ↓
DURABLY PERSIST:
  consent_request_id
  consent_roster_fingerprint
  delivery_state =
      CONSENT_POSTED_UNCONFIRMED
  reconciliation_required = true
        ↓
COMMIT
        ↓
POST sonnet.roster.v1
        ↓
RECONCILE
```

重要:

> local durable stateが`CONSENT_POSTED_UNCONFIRMED`になる前にnetwork POSTしてはならない。

---

# 17. Crash Before vs After Network Send

## 17.1 Intent persisted → crash before HTTP send

```text
durable consent intent exists
HTTP not yet sent
process crashes
```

restart後:

```text
CONSENT_POSTED_UNCONFIRMED
```

として復帰する。

`NOT_SENT`や`DEFINITELY_PRE_CONSENT`へ戻してはならない。

まずsame request IDをreconcileする。

server上に存在しないことを確認した後、same request ID / exact same payloadによるsafe retryが許される場合のみretryする。

## 17.2 Server persisted → crash before local response processing

```text
durable consent intent
↓
POST
↓
Technocore persists action
↓
process crashes
```

restart後もlocal stateは:

```text
CONSENT_POSTED_UNCONFIRMED
```

である。

したがって:

```text
no switch
no second roster
no pre-consent hard stall
same request reconciliation
```

となる。

これによりprotocol consent済みの可能性を失わない。

---

# 18. Consent Delivery Safety Rule

以下のどちらかである間:

```text
CONSENT_POSTED_UNCONFIRMED
CONSENT_DELIVERY_UNKNOWN
```

Sarukuは、

```text
pre-consent HARD_STALL
pre-consent uncertain-abandon
candidate switching
another teamへのapplication
another roster countersign
```

を行ってはならない。

normative invariant:

> If Saruku has durably recorded an intent to submit roster consent and the authoritative result is unresolved, Saruku MUST behave as potentially consented.

したがって:

```text
CONSENT_DELIVERY_UNKNOWN
!=
no roster consent
```

---

# 19. Consent Reconciliation

same:

```text
request_id
exact payload
roster fingerprint
```

を用いてreconcileする。

## 19.1 Confirmed

exact Saruku roster recordをDiscoveryで確認:

```text
CONSENT_CONFIRMED

consent_source_seq = observed seq
consent_observed_at = trusted server timestamp

→ WAIT_ROSTER_READY
```

## 19.2 Explicitly not persisted / rejected

authoritative evidenceによりactionが存在しない、または拒否されたことを確認:

```text
CONSENT_EXPLICITLY_NOT_PERSISTED
```

pre-consent stateへ戻れる。

## 19.3 Still indeterminate

authoritatively解決不能:

```text
CONSENT_DELIVERY_UNKNOWN
```

action:

```text
FAIL_CLOSED_WAIT_AND_RECONCILE
```

applicationと異なりlocal bounded escapeを設けない。

---

# 20. No Bounded Escape for Unknown Consent

application:

```text
local recruitment envelope
```

roster consent:

```text
official protocol state mutation
```

である。

したがって:

```text
application unknown
→ bounded local abandonment possible

roster consent unknown
→ bounded abandonment forbidden
```

contest/global terminal stateはauthoritative precedenceで別途処理する。

---

# 21. Withdrawal Write-Ahead Safety

`sonnet.withdraw.v1`にも同じwrite-ahead ruleを適用する。

sequence:

```text
determine SAFE_WITHDRAW
        ↓
generate/fix withdraw request_id
        ↓
DURABLY PERSIST:
  withdraw_pending
  withdraw_request_id
  target game/roster
        ↓
COMMIT
        ↓
POST sonnet.withdraw.v1
        ↓
authoritative reconciliation
```

network POST後のクラッシュで、

```text
withdraw not attempted
```

へ戻してはならない。

POST成功だけではepoch終了しない。

authoritative completionを待つ。

---

# 22. Formation Anchor and Pre-existing Roster

inviterはprotocol authorityではない。

local roster-tracking anchorのみ。

later targeted recruitment noteはpre-existing inviter-consented rosterをaffirmできる。

必要条件:

```text
trusted recruiter = writer
same game
same valid room/generation
Saruku included
inviter currently consents
exactly one compatible current roster
history complete over relevant interval
team not frozen
```

ambiguity時:

```text
ENGAGED
WAIT
```

とする。

---

# 23. Formation Stage

```text
INVITED
APPLIED
ENGAGED
ROSTER_PROPOSED
ROSTER_PROGRESSING
READY_TO_COUNTERSIGN

CONSENT_RECONCILING

WAIT_ROSTER_READY

TEAM_READY
```

terminal/local exit:

```text
INVALID
ABANDONED
CLOSED
HARD_STALLED
ABANDONED_UNCONFIRMED
ABANDONED_UNCERTAIN_HISTORY
```

---

# 24. Structural Progress

progressとは、

> Sarukuがteam成立へ近づいたことをdeterministically証明できるstructural improvement

である。

activityだけではstall timerをresetしない。

---

# 25. Epoch High-Watermark

概念key:

```text
(
  formation_stage_rank,
  room_verified,
  saruku_in_roster,
  inviter_signed,
  -missing_signers,
  signer_count
)
```

strict improvement時のみepoch high-watermarkを更新する。

---

# 26. Active Roster Lineage

roster fingerprintごとに:

```text
lineage_best_key
lineage_last_progress_at
lineage_last_progress_seq
replacement_of_fingerprint
```

を持つ。

same-lineage churnではepoch HWMを超えない限りstall clockをresetしない。

---

# 27. Bounded Replacement Recovery

```text
replacement_recovery_budget = 1
REPLACEMENT_RECOVERY_GRACE = 20 min
```

legitimate replacement lineageに限り1回だけ使用可能。

grace deadlineはfixed。

activity / re-invite / churnでは延長しない。

---

# 28. Stall Policy

```text
SOFT_STALL = 20 min
HARD_STALL = 60 min
```

anchor:

```text
epoch_high_watermark_at
```

初期は:

```text
application_observed_at
```

---

# 29. Pre-consent HARD_STALL

以下の場合のみ使用可能:

```text
stall >= 60 min

replacement recovery not applicable

RosterConsentDeliveryState =
    NOT_SENT
or
    CONSENT_EXPLICITLY_NOT_PERSISTED

Saruku has no confirmed roster consent

no durable unresolved roster-consent send intent exists
```

action:

```text
epoch.status = hard_stalled
application = terminal
active_application = none

do NOT send sonnet.withdraw.v1

return DISCOVERY
```

---

# 30. Pre-consent History-Gap Exit

history gap解消不能でも、

```text
Saruku never durably recorded
a roster consent send intent
```

または、

```text
consent explicitly not persisted
```

を確認できる場合のみ、

hard stallで:

```text
ABANDONED_UNCERTAIN_HISTORY
```

としてlocal epochを解放できる。

以下が存在する場合は禁止:

```text
CONSENT_POSTED_UNCONFIRMED
CONSENT_DELIVERY_UNKNOWN
durable unresolved consent send intent
```

---

# 31. WAIT_ROSTER_READY

`CONSENT_CONFIRMED`後:

```text
WAIT_ROSTER_READY
```

へ入る。

ordinary challenger switchingを停止する。

soft stall:

```text
OBSERVE
RECONCILE
```

hard stall:

```text
if authoritative checks prove
    withdrawal legal:

    SAFE_WITHDRAW

else:

    FAIL_CLOSED_WAIT_AND_RECONCILE
```

withdrawはSection 21のwrite-ahead ruleに従う。

---

# 32. Consent-Uncertainty Handling

`CONSENT_RECONCILING`では:

```text
do not switch
do not apply elsewhere
do not countersign another roster
do not use pre-consent exit
continue same-request reconciliation
```

explicit authoritative terminal factは最優先する。

---

# 33. Candidate Comparator

primary:

```text
formation_stage_rank
```

same-stage:

```text
room_verified
saruku_in_roster
inviter_signed
missing_signers ASC
signer_count DESC
```

recency単独ではmaterial superiorityにならない。

---

# 34. Decision Precedence

Normative order:

```text
1. AUTHORITATIVE TERMINAL / TEAM_READY

2. REQUIRED HISTORY RECONCILIATION

3. RECONCILE PENDING / UNKNOWN
   SARUKU ROSTER CONSENT

4. WAIT_ROSTER_READY HANDLING

5. READY_TO_COUNTERSIGN

6. IMMEDIATE INVALIDATION / REGRESSION

7. HARD_STALL / REPLACEMENT RECOVERY

8. SOFT_STALL CANDIDATE COMPARISON

9. NORMAL STAY / APPLY
```

## 34.1 Consent uncertainty priority

```text
CONSENT_POSTED_UNCONFIRMED
CONSENT_DELIVERY_UNKNOWN
```

ならpriority 3で停止する。

後続のswitch/stall/applyへ進まない。

## 34.2 READY_TO_COUNTERSIGN beats HARD_STALL

同cycleで、

```text
READY_TO_COUNTERSIGN
AND
hard-stall reached
```

なら、

```text
COUNTERSIGN
```

を優先する。

ただし、

```text
history complete
no unresolved prior consent intent
all safety gates pass
```

が必要。

---

# 35. Protocol Mutation Durable-Outbox Invariant

official protocol state mutationは、

```text
durable local intent
BEFORE
network side effect
```

でなければならない。

最低対象:

```text
sonnet.roster.v1
sonnet.withdraw.v1
```

推奨対象:

```text
sonnet.application.v1
```

durable intentには最低限以下を含む。

```text
request_id
action kind
exact target identity
exact payload or payload fingerprint
pending/uncertain delivery state
created/reconciliation timestamps
```

transaction commit前にHTTP POSTしてはならない。

---

# 36. Restart Safety

persist対象:

```text
active epoch

application delivery state
application_reconcile_started_at
application_reconcile_deadline
application_observed_at

roster consent delivery state
consent_request_id
consent_roster_fingerprint
consent_post_attempted_at
consent_source_seq
consent_observed_at

withdraw pending state
withdraw request_id

history continuity state

per-signer consent

epoch high-watermark
active roster lineage

replacement recovery budget/deadline

stall state
```

restartは以下を行ってはならない。

```text
extend application reconciliation deadline

forget a durable protocol mutation intent

turn CONSENT_POSTED_UNCONFIRMED
into NOT_SENT

turn CONSENT_DELIVERY_UNKNOWN
into pre-consent

turn withdraw_pending into not-withdrawn

reset replacement recovery budget

reset stall clocks
```

normative invariant:

> Crash/restart MUST recover unresolved protocol mutations into reconciliation, never into an earlier no-action state.

---

# 37. Crash-Safety Examples

## 37.1 Consent persisted, HTTP delivered, crash before response processing

```text
persist consent intent
COMMIT
POST roster
server persists
crash
```

restart:

```text
POSSIBLY_CONSENTED
CONSENT_POSTED_UNCONFIRMED
```

must:

```text
no switch
no second roster
no pre-consent exit
same request reconciliation
```

## 37.2 Consent persisted, crash before HTTP send

```text
persist consent intent
COMMIT
crash
HTTP never sent
```

restart:

```text
CONSENT_POSTED_UNCONFIRMED
```

not:

```text
NOT_SENT
```

first reconcile same request ID.

if absent and retry semantics permit:

```text
retry exact same request
with same request_id
```

until authoritative result is obtained.

## 37.3 Withdrawal persisted, server accepts, crash

```text
persist withdraw_pending
COMMIT
POST withdraw
server accepts
crash
```

restart:

```text
withdraw_pending
```

reconcile authoritative result.

do not assume roster consent still active solely because local response handling was lost.

---

# 38. Application Restart Example

```text
12:00 application send intent
deadline = 12:10

12:05 restart
```

after restart:

```text
deadline remains 12:10
```

not:

```text
12:15
```

---

# 39. Design Invariants

1. active application epochは最大1つ。
2. active teamは最大1つ。
3. reducerはexplicit allowlistのみ使用する。
4. recruitment roleはtrusted registration stateから検証する。
5. self-declared roleをauthorityにしない。
6. re-inviteはepoch/progressをresetしない。
7. historical eventからterminal epochを復活させない。
8. absence inferenceにはcomplete historyが必要。
9. gap中はunsafe countersign/switch/withdrawをしない。
10. signer consentはlatest applicable actionで決定する。
11. withdrawalはprior consentを失効させる。
12. fresh re-signはconsentを再成立できる。
13. activityとprogressを分離する。
14. epoch HWMはstrict improvementのみ。
15. replacement recoveryは1 epoch 1回・fixed 20 min。
16. challenger存在だけではswitchしない。
17. recency単独ではmaterial superiorityにならない。
18. application formation timerはtrusted server read-backから開始する。
19. application reconcile deadlineはrestartで延長しない。
20. application `DELIVERY_UNKNOWN`はlocal slotを解放できる。
21. roster consent uncertaintyはapplication uncertaintyより強いSafety stateである。
22. roster consent send intentをdurably persistした時点で`POSSIBLY_CONSENTED`として扱う。
23. `CONSENT_POSTED_UNCONFIRMED`をpre-consent扱いしない。
24. `CONSENT_DELIVERY_UNKNOWN`をpre-consent扱いしない。
25. consent uncertainty中にteam switchしない。
26. consent uncertainty中に別teamへapplyしない。
27. consent uncertainty中に別rosterへcountersignしない。
28. consent uncertaintyはsame request IDでreconcileする。
29. unresolved roster consentにbounded abandonmentを設けない。
30. pre-consent HARD_STALLはconsentがdefinitely absentの場合のみ利用する。
31. countersign済みまたはpossibly-consentedならpre-consent escapeを使わない。
32. WAIT_ROSTER_READYではsafe withdrawalまたはfail-closed reconcileのみ。
33. withdrawal POST成功だけではepoch終了しない。
34. authoritative referee stateは全local heuristicより優先する。
35. decision precedenceはnormativeである。
36. **Official protocol state mutationのnetwork POST前にrequest ID・exact target/payload・pending/uncertain stateをdurably persistする。**
37. **Crash/restartによって、送信済みかもしれないprotocol actionを`NOT_SENT`またはそれ以前のstateへ戻してはならない。**
38. unresolved `withdraw_pending`もrestart後にreconcileする。
39. durable protocol mutation intentの存在はlocal pre-consent escapeより優先する。

---

# 40. Required Synthetic / Replay Tests

Implementationでは最低限以下を必須testとする。

### Event history

```text
continuous history
single gap
multiple gaps
export reconciliation success
reconciliation failure
generation boundary
```

### Consent semantics

```text
sign
sign → withdraw
sign → withdraw → sign
sign roster A → sign roster B
history gap after sign
```

### Application uncertainty

```text
POST persisted / read-back delayed
POST not persisted
DELIVERY_UNKNOWN
restart before deadline
restart after deadline
late historical appearance
```

### COUNTERSIGN uncertainty

```text
persist consent intent
→ HTTP delivered
→ crash before response/local processing
→ restart
→ POSSIBLY_CONSENTED
→ no switch
→ no second roster
→ same-request reconcile
```

and:

```text
persist consent intent
→ crash before HTTP send
→ restart
→ POSSIBLY_CONSENTED
→ same-request reconcile/retry
→ never DEFINITELY_PRE_CONSENT
  until authoritative resolution
```

### Withdrawal crash safety

```text
persist withdraw_pending
→ server accepts
→ crash
→ restart
→ reconcile
→ no duplicate/conflicting mutation
```

### Stall boundaries

```text
19:59 normal stay
20:00 soft stall
59:59 normal
60:00 hard stall

READY_TO_COUNTERSIGN at 60:00

history gap at hard stall

possible consent at hard stall
```

### Replacement recovery

```text
same-lineage churn

legitimate replacement

one 20-minute grace

second replacement no new grace

recovery exceeds old epoch HWM

recovery fails before fixed deadline
```

### Restart

```text
restart with active epoch
restart with soft stall
restart with replacement grace
restart with POSTED_UNCONFIRMED
restart with CONSENT_POSTED_UNCONFIRMED
restart with CONSENT_DELIVERY_UNKNOWN
restart with withdraw_pending
```

---

# 41. Success Criteria

STEP1 v0.2は以下を満たすこと。

- event-driven Team Formation
- 20/60分watchdog
- history-gap safety
- latest-action consent semantics
- explicit event contract
- trusted recruiter role
- bounded application transport reconciliation
- restart-safe application deadline
- deterministic pre-consent exit
- bounded uncertain-history exit
- possible roster consentをpre-consent扱いしない
- explicit roster-consent delivery state machine
- protocol mutation write-ahead safety
- crash-safe countersign
- crash-safe withdrawal
- no conflicting second countersign
- bounded replacement recovery
- deterministic candidate comparison
- restart/replay safety
- authoritative completion semantics
- auditability
- STEP1 coreにLLM dependencyなし

---

# 42. Final Design Decisions

```text
D1  Team Formation is event-driven.

D2  History completeness is explicit state.

D3  Absence inference requires complete history.

D4  20 min soft stall / 60 min hard stall.

D5  Re-invite does not reset epoch/progress.

D6  Consent uses latest applicable event semantics.

D7  Withdrawal invalidates prior consent.

D8  Event-type allowlist is normative.

D9  Vote invitation types are excluded.

D10 TARGETED_RECRUITMENT_NOTE is local convention.

D11 Recruitment sender requires trusted writer registration.

D12 Inviter is local anchor, not protocol authority.

D13 Pre-existing roster affirmation requires
    complete and unambiguous history.

D14 Epoch progress uses strict high-watermark.

D15 Legitimate replacements use separate lineage.

D16 Replacement recovery is one fixed 20-minute grace.

D17 Candidate comparison is deterministic.

D18 Recency alone cannot cause switch.

D19 Application formation time begins at
    trusted server read-back.

D20 Application transport reconciliation
    is bounded to 10 minutes.

D21 Application reconciliation deadline
    persists across restart.

D22 Application DELIVERY_UNKNOWN may
    release local application slot.

D23 Pre-consent HARD_STALL terminalizes local epoch.

D24 Pre-consent HARD_STALL sends no withdrawal.

D25 Unrecoverable pre-consent history gap
    may release local commitment without
    declaring the team dead.

D26 Saruku roster consent has its own
    delivery state machine.

D27 Attempted-but-unresolved roster consent
    means POSSIBLY_CONSENTED.

D28 POSSIBLY_CONSENTED forbids pre-consent
    exit, switching, conflicting application
    and another roster countersign.

D29 CONSENT_CONFIRMED enters WAIT_ROSTER_READY.

D30 CONSENT_EXPLICITLY_NOT_PERSISTED may
    return to pre-consent policy.

D31 CONSENT_DELIVERY_UNKNOWN is fail-closed
    and has no bounded abandonment.

D32 Roster-consent uncertainty uses
    same-request reconciliation.

D33 Consent reconciliation precedes
    stall/switch/countersign logic.

D34 READY_TO_COUNTERSIGN beats HARD_STALL
    when all safety prerequisites pass.

D35 WAIT_ROSTER_READY hard stall uses
    SAFE_WITHDRAW or FAIL_CLOSED.

D36 Withdrawal requires authoritative completion.

D37 All official protocol mutations use
    durable write-ahead intent before POST.

D38 Crash/restart never rolls an uncertain
    protocol mutation back to NOT_SENT.

D39 sonnet.roster.v1 and sonnet.withdraw.v1
    MUST use the durable-outbox pattern.

D40 STEP1 core contains no LLM authority.
```

---

# 43. Decision Precedence — Final

```text
AUTHORITATIVE TERMINAL / TEAM_READY
        ↓
RECONCILE HISTORY IF REQUIRED
        ↓
RECONCILE PENDING / UNKNOWN
SARUKU ROSTER CONSENT
        ↓
WAIT_ROSTER_READY POLICY
        ↓
READY_TO_COUNTERSIGN
        ↓
IMMEDIATE INVALIDATION
        ↓
HARD_STALL / REPLACEMENT RECOVERY
        ↓
SOFT_STALL COMPARISON
        ↓
NORMAL STAY / APPLY
```

---

# 44. Design Freeze

STEP1 v0.2は本版でDesign Freezeとする。

以後は、

```text
implementation
migration
synthetic tests
replay tests
activation
rollback
```

へ移行する。

設計を再openする条件は以下に限定する。

```text
official protocol contradiction

Safety invariant violation

implementation/replay testで
現在設計では解決不能なstate discovered
```

単なる実装都合やrefactoring理由では設計を再openしない。

---

# 45. Implementation Handoff

次成果物:

```text
STEP1 v0.2 Implementation Plan

Codex Implementation Instructions

DB / state migration plan

durable outbox design

event-history reconciliation implementation

synthetic/replay regression test suite

production activation procedure

rollback procedure

PARTICIPANT_DESIGN supersession notice
```

Implementationでは、特に以下をSafety-critical test gateとする。

```text
ambiguous roster POST + crash

crash before roster HTTP send

crash after server persistence

unknown roster delivery + restart

withdraw accepted + crash

gap + countersign boundary

possible consent + hard-stall boundary
```
