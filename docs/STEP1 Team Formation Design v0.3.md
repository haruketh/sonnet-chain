# STEP1 — Team Formation Design v0.3

Status: **DESIGN PASS / FROZEN / PRODUCTION ACTIVE**  
Date: 2026-09-15  
Supersedes: `STEP1 Team Formation Design v0.2.md`

---

# 1. Purpose

STEP1の目的は、Sonnet-2 Discovery上の検証済みstructured eventをdeterministicに追跡し、Sarukuが安全性を維持しながらlivenessを失わずteamを形成することである。

STEP1 v0.3では、v0.2で導入したevent-driven Team Formationを維持しつつ、Sarukuのroster consent timingを改善する。

v0.2ではSarukuを事実上のlast signerとして扱い、Saruku以外の全memberがexact canonical rosterへcurrent consentするまでSaruku自身のconsentを送らなかった。production観測では、この保守的policyが他agentのroster churn / withdrawalと組み合わさり、teamが形成途中でhard stallし続けるcoordination deadlockを生み得ることが確認された。

v0.3ではmandatory last-signer policyを廃止し、**Progressive Countersign**を導入する。

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

v0.3の基本原則は次のとおり。

> Safetyを維持するためにteam形成を停止させるのではなく、検証済みの十分なcoordination evidenceが得られた時点でSaruku自身もrosterへcommitし、team completionを前進させる。

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
docs/STEP1 Team Formation Design v0.3.md
```

としてSTEP1のnormative detailed designとする。

`STEP1 Team Formation Design v0.2.md` はhistorical designとし、v0.3と競合する場合はv0.3を優先する。

`PARTICIPANT_DESIGN.md` / `PARTICIPANT_ARCHITECTURE.md` はinitial participant architecture / historical baselineとして扱う。

競合時:

```text
STEP1 Team Formation Design v0.3
>
STEP1 Team Formation Design v0.2
>
PARTICIPANT_DESIGN / PARTICIPANT_ARCHITECTURE
```

とする。

## 2.2 v0.3 change boundary

v0.3が変更するのは主に以下である。

```text
1. Saruku mandatory last-signer policy removal
2. Progressive Countersign introduction
3. READY_TO_COUNTERSIGN semantics
4. fresh re-application after historical terminal epoch semantics
```

以下はv0.2から維持する。

```text
history completeness
latest-action consent semantics
trusted recruiter verification
application reconciliation
protocol mutation write-ahead safety
consent uncertainty handling
withdrawal safety
replacement lineage / recovery
candidate comparator
authoritative referee precedence
restart / replay safety
```

---

# 3. Protocol Boundary

Sonnet protocol上のteam membership truthは、

```text
4–8 members
+
all applicable members currently consenting to
the exact same canonical roster
```

によって成立する。

**Progressive Countersignはofficial team membership truthを変更しない。**

Sarukuが早期にconsentしただけではTEAM_READYではない。

Saruku独自のteam leader authorityは作らない。

以下はSaruku local policyである。

```text
SOFT_STALL = 20 min
HARD_STALL = 60 min
APPLICATION_RECONCILE_WINDOW = 10 min
REPLACEMENT_RECOVERY_GRACE = 20 min
MIN_EXTERNAL_COUNTERSIGNERS = 2

candidate comparison policy
formation anchor policy
progressive countersign policy
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

v0.3でcountersign timingを早めても、このSafety invariantは弱めない。

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

v0.3でrecruiterとして扱うsenderは、

```text
trusted accepted registration role = writer
```

のみ。

organizer-originated recruitmentは将来versionの対象とする。

Progressive Countersignでも、inviterはprotocol authorityではない。inviterはlocal application anchorとしてのみ使用する。

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

v0.3 local convention:

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

もv0.3では使用しない。

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

Progressive Countersignもhistory completeを必須条件とする。

explicit trusted referee terminal evidenceはhistory gap中でも使用できる。

---

# 8. Formation Opportunity and Application Epoch

Formation Opportunity:

```text
game_id
inviter_did
source_seq
observed_at
poem_room / generation when known
opportunity_kind
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

consent delivery state
consent request/fingerprint

terminal_seq
terminal_at
status
```

active application epochは最大1つ。

---

# 9. Re-invite and Fresh Re-application Semantics

## 9.1 Re-invite during active epoch

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

active epochへのre-inviteだけでprogress扱いしない。

## 9.2 Terminal epoch is immutable

terminal epochをhistorical eventから復活させない。

```text
hard_stalled
abandoned
abandoned_uncertain_history
abandoned_unconfirmed
invalid
closed
```

等のterminal/local-exit stateはhistorical factとして残す。

## 9.3 Fresh re-application after historical terminal epoch

historical terminal epochが存在しても、**後から新しいtrusted opportunityが到来し、fresh application epochを開始することは許可する。**

重要:

> historical hard stall must not permanently poison the same game.

fresh epochでは必ず新しいapplication anchorを持つ。

```text
old epoch terminal
        ↓
new trusted invite/opportunity
        ↓
new application_request_id
new starting_invite_seq
        ↓
new epoch
```

current fresh applicationが存在する場合、過去の`expired_games` / historical hard-stalled epochだけを理由にそのgameのProgressive Countersignを拒否してはならない。

ただしfreshness条件は弱めない。

```text
proposal.game_id == current application game_id
consensus evidence is after fresh invite anchor
inviter current exact-roster consent seq > starting_invite_seq
history complete
team room verified/open
all current-consent gates pass
```

current applicationが存在しないunanchored roster discoveryでは、historical expired-game filteringを維持してよい。

old rosterをfresh epochへ自動継承してはならない。

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

Progressive Countersignでは**current exact-roster signerのみ**を数える。

過去にsignしていても、その後withdrawまたは別rosterへsignしたDIDはthresholdへcountしない。

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

# 16. Progressive Countersign and Write-Ahead Sequence

## 16.1 Progressive Countersign definition

v0.3ではmandatory last-signer policyを廃止する。

```text
MIN_EXTERNAL_COUNTERSIGNERS = 2
```

Sarukuは、以下を**すべて満たすexact canonical roster**へ自身のconsentを送信してよい。

1. valid `sonnet.roster.v1`である。
2. member countが4–8である。
3. Sarukuがmembersに含まれる。
4. active applicationとexact same `game_id`である。
5. application inviterがそのexact rosterの**current signer**である。
6. inviterを含むnon-Saruku current signerが合計2名以上である。
7. `min_anchor_seq`が存在する場合、inviterのcurrent exact-roster consent seqが`min_anchor_seq`よりstrictly greaterである。
8. required Discovery historyがcompleteである。
9. team room ownerがpinned refereeと一致する。
10. team room generationがroster記載generationと一致する。
11. team roomがopenであり、word acceptance等によりfreeze/closeされていない。
12. Sarukuにunresolved prior roster consentがない。
13. Sarukuにunresolved withdrawalがない。
14. roster identityがexactに一致し、別variantのsignatureを混合していない。

inviterだけでは不足する。

```text
inviter signed only
→ NOT READY
```

inviter以外の2名だけでも不足する。

```text
peer A + peer B signed
inviter not current signer
→ NOT READY
```

### 4-member example

```text
A = inviter  ✓
B            ✓
C            -
Saruku       -

external current signers = 2
→ READY_TO_COUNTERSIGN
→ Saruku may sign as third signer
```

### 5-member example

```text
A = inviter  ✓
B            ✓
C            -
D            -
Saruku       -

→ READY_TO_COUNTERSIGN
```

### 8-member example

```text
A = inviter  ✓
B            ✓
C..G         -
Saruku       -

→ READY_TO_COUNTERSIGN
```

Team sizeによってthresholdは増やさない。

Progressive Countersignはteam completionそのものではなく、**Saruku local action readiness**である。

## 16.2 Exact canonical roster identity

以下が異なるrosterは同一consensusとして集約してはならない。

```text
game_id
poem_room
room_generation
members exact ordered tuple
```

request_idはroster identityそのものには含めない。

## 16.3 COUNTERSIGN write-ahead

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
history complete over relevant interval
team not frozen
```

v0.3では、actual countersign eligibilityについてさらに以下を要求する。

```text
at least 2 non-Saruku current signers total
including inviter

inviter current exact-roster consent seq
> fresh application anchor seq
```

multiple compatible current roster variantsが同時に成立する場合はambiguityとして扱う。

```text
ENGAGED
WAIT
```

とする。

## 22.1 Fresh epoch boundary

fresh application epochが存在する場合、historical terminal epochの存在だけでcurrent proposalを拒否しない。

一方、fresh epochより前に完成していたstale rosterを復活させてはならない。

```text
completed_at_seq <= fresh starting_invite_seq
→ reject
```

fresh inviter signature:

```text
inviter_current_seq > starting_invite_seq
```

を必須とする。

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

## 23.1 READY_TO_COUNTERSIGN semantics

v0.3における`READY_TO_COUNTERSIGN`は、

> all other members already signed

を意味しない。

次を意味する。

```text
valid exact roster
+
room verified/open
+
Saruku included
+
inviter current exact consent
+
minimum 2 external current signers
+
fresh application anchor satisfied
+
history complete
+
no unresolved Saruku consent/withdrawal
```

したがって、4-member rosterで2 external signersが揃った状態は`ROSTER_PROGRESSING`ではなく`READY_TO_COUNTERSIGN`となり得る。

`TEAM_READY`は引き続きauthoritative full team completionである。

---

# 24. Structural Progress

progressとは、

> Sarukuがteam成立へ近づいたことをdeterministically証明できるstructural improvement

である。

activityだけではstall timerをresetしない。

Progressive Countersign thresholdへの到達はstructural progressである。

ただし単なるre-invite、note、同一state churnはprogressではない。

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

`READY_TO_COUNTERSIGN`のstage rankはv0.3 Progressive Countersign semanticsに従う。

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

READY_TO_COUNTERSIGNが同cycleで成立している場合はSection 34のprecedenceに従いCOUNTERSIGNを優先する。

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

NOT READY_TO_COUNTERSIGN under v0.3 rules
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

v0.3ではSarukuが3rd signer等として早期consentするため、`WAIT_ROSTER_READY`中に未署名memberが残ることは正常である。

ordinary challenger switchingを停止する。

remaining memberのcurrent consentを観測する。

新しいsigner追加はprogressとして記録できる。

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

単一memberのwithdraw/churnを見ただけで即withdrawしてはならない。

全memberのexact canonical roster current consentが揃い、refereeがteam readinessをauthoritatively認めるまでWRITINGへ移行しない。

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

`READY_TO_COUNTERSIGN`判定自体はSection 16のProgressive Countersign条件に従う。

candidate comparatorはofficial consensusを推測しない。

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
progressive inviter-anchored readiness
team room verified/open
no unresolved prior consent intent
no unresolved withdrawal
all safety gates pass
```

が必要。

## 34.3 Readiness consistency invariant

以下は同じProgressive Countersign eligibilityを使わなければならない。

```text
formation reducer
candidate reducer
epoch structural stage
hard-stall boundary check
actual countersign executor
```

observability上はREADYだがexecutorが拒否する、またはその逆、というsemantic splitを作ってはならない。

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

fresh re-application epochのanchorもrestart後に維持する。

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

Fresh re-applicationではnew application request / anchorを使用し、old epochのdeadlineやHWMを再利用しない。

---

# 39. Design Invariants

1. active application epochは最大1つ。
2. active teamは最大1つ。
3. reducerはexplicit allowlistのみ使用する。
4. recruitment roleはtrusted registration stateから検証する。
5. self-declared roleをauthorityにしない。
6. active epoch中のre-inviteはepoch/progressをresetしない。
7. historical eventからterminal epochを復活させない。
8. terminal historical epochはfresh later applicationを永久禁止しない。
9. fresh later applicationはnew anchorを持つnew epochである。
10. fresh epochより前のstale rosterをcurrent readinessへ流用しない。
11. absence inferenceにはcomplete historyが必要。
12. gap中はunsafe countersign/switch/withdrawをしない。
13. signer consentはlatest applicable actionで決定する。
14. withdrawalはprior consentを失効させる。
15. fresh re-signはconsentを再成立できる。
16. roster variantsのsignatureを混合しない。
17. activityとprogressを分離する。
18. epoch HWMはstrict improvementのみ。
19. replacement recoveryは1 epoch 1回・fixed 20 min。
20. challenger存在だけではswitchしない。
21. recency単独ではmaterial superiorityにならない。
22. application formation timerはtrusted server read-backから開始する。
23. application reconcile deadlineはrestartで延長しない。
24. application `DELIVERY_UNKNOWN`はlocal slotを解放できる。
25. roster consent uncertaintyはapplication uncertaintyより強いSafety stateである。
26. roster consent send intentをdurably persistした時点で`POSSIBLY_CONSENTED`として扱う。
27. `CONSENT_POSTED_UNCONFIRMED`をpre-consent扱いしない。
28. `CONSENT_DELIVERY_UNKNOWN`をpre-consent扱いしない。
29. consent uncertainty中にteam switchしない。
30. consent uncertainty中に別teamへapplyしない。
31. consent uncertainty中に別rosterへcountersignしない。
32. consent uncertaintyはsame request IDでreconcileする。
33. unresolved roster consentにbounded abandonmentを設けない。
34. pre-consent HARD_STALLはconsentがdefinitely absentの場合のみ利用する。
35. countersign済みまたはpossibly-consentedならpre-consent escapeを使わない。
36. WAIT_ROSTER_READYではsafe withdrawalまたはfail-closed reconcileのみ。
37. withdrawal POST成功だけではepoch終了しない。
38. authoritative referee stateは全local heuristicより優先する。
39. decision precedenceはnormativeである。
40. official protocol state mutationのnetwork POST前にrequest ID・exact target/payload・pending/uncertain stateをdurably persistする。
41. crash/restartによって、送信済みかもしれないprotocol actionを`NOT_SENT`またはそれ以前のstateへ戻してはならない。
42. unresolved `withdraw_pending`もrestart後にreconcileする。
43. durable protocol mutation intentの存在はlocal pre-consent escapeより優先する。
44. official team truthは4–8 members全員のexact current consentである。
45. Progressive Countersignはofficial full consensusではない。
46. Saruku mandatory last-signer policyは使用しない。
47. `MIN_EXTERNAL_COUNTERSIGNERS = 2`とする。
48. inviterはProgressive Countersign external signerの1名として必須である。
49. inviter以外に最低1名のadditional non-Saruku current signerを必須とする。
50. inviter current consentはfresh application anchorより新しくなければならない。
51. 4/5/6/7/8 membersで同じProgressive Countersign thresholdを使用する。
52. READY_TO_COUNTERSIGNはProgressive Countersign readinessを意味する。
53. Progressive Countersign後はWAIT_ROSTER_READYへ進み、残りmemberを待つ。
54. early Saruku consentだけでWRITINGへ進まない。
55. historical expired game filterはcurrent fresh anchored applicationをpoisonしてはならない。
56. no-current-applicationのunanchored rosterではhistorical expired filteringを維持できる。
57. reducer / boundary / executorのreadiness semanticsは一致しなければならない。
58. STEP1 coreのcountersign decisionにLLM authorityを導入しない。

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

### Progressive Countersign

```text
4-member roster:
  inviter + one additional current signer
  → READY_TO_COUNTERSIGN

5-member roster:
  inviter + one additional current signer
  → READY_TO_COUNTERSIGN

8-member roster:
  inviter + one additional current signer
  → READY_TO_COUNTERSIGN

inviter only
  → NOT READY

two peers without inviter
  → NOT READY

inviter signature at/before application anchor
  → NOT READY

inviter signature after application anchor
  + one peer
  → READY

second signer later withdraws
  → NOT READY

second signer changes to roster variant B
  → NOT READY for roster A

signatures split across roster variants
  → MUST NOT combine

incomplete Discovery history
  → no countersign

wrong owner / wrong generation / frozen room
  → no countersign
```

### Early consent post-state

```text
Progressive Countersign
→ Saruku roster intent persisted
→ Saruku roster confirmed
→ WAIT_ROSTER_READY
→ NOT WRITING yet

remaining members later converge
→ authoritative team readiness
→ existing WRITING transition
```

### Fresh re-application

```text
old epoch game X hard_stalled
→ later fresh trusted invite for X
→ new application epoch
→ inviter + peer sign exact roster after fresh anchor
→ countersign allowed
```

and:

```text
old hard-stalled X
+ stale roster completed before fresh invite
→ rejected
```

and:

```text
historical expired X
+ no current fresh application
→ unanchored roster remains blocked
```

and:

```text
fresh X application
+ insufficient external signers
→ no countersign
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

Progressive READY_TO_COUNTERSIGN at 60:00
→ COUNTERSIGN wins

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
restart with fresh re-application anchor
```

---

# 41. Success Criteria

STEP1 v0.3は以下を満たすこと。

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
- STEP1 coreにLLM authorityなし
- mandatory last-signer policyなし
- Progressive Countersignが4–8 membersで一貫して動作
- inviter + additional current signerでSarukuがteam formationへ能動commit可能
- early consent後もfull team readinessまでWAIT_ROSTER_READYを維持
- historical hard stallがfresh re-applicationを永久poisonしない
- stale pre-reinvite rosterをfresh epochへ流用しない
- reducer / boundary / executor readiness semanticsが一致

---

# 42. Final Design Decisions

```text
D1  Team Formation is event-driven.

D2  History completeness is explicit state.

D3  Absence inference requires complete history.

D4  20 min soft stall / 60 min hard stall.

D5  Re-invite during an active epoch does not reset epoch/progress.

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

D41 Saruku mandatory last-signer policy is removed.

D42 Progressive Countersign uses
    MIN_EXTERNAL_COUNTERSIGNERS = 2.

D43 Progressive Countersign requires the inviter
    plus at least one additional non-Saruku
    current signer on the exact canonical roster.

D44 The inviter current consent must be newer
    than the active application anchor when one exists.

D45 Progressive Countersign applies equally
    to 4, 5, 6, 7 and 8-member rosters.

D46 Progressive Countersign is local action readiness,
    not official full team consensus.

D47 Early Saruku consent enters WAIT_ROSTER_READY;
    it does not directly enter WRITING.

D48 Full team readiness remains authoritative and
    requires all applicable members on the exact roster.

D49 Historical terminal epochs do not permanently
    poison a later fresh application for the same game.

D50 A fresh re-application uses a new epoch and anchor;
    old terminal epochs are never revived.

D51 Roster evidence completed before the fresh anchor
    cannot satisfy the fresh epoch.

D52 Historical expired-game filtering may remain for
    unanchored/no-current-application discovery.

D53 Reducer, watchdog boundary and actual executor
    use the same Progressive Countersign eligibility.
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
(Progressive Countersign)
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

STEP1 v0.3は本版でDesign Freezeとする。

v0.3はv0.2のSafety / restart / reconciliation architectureを継承し、production観測で確認されたmandatory last-signer coordination deadlockを解消するためのProgressive Countersignを正式仕様化した版である。

設計を再openする条件は以下に限定する。

```text
official protocol contradiction

Safety invariant violation

implementation/replay testで
現在設計では解決不能なstate discovered

production observation showing
systemic formation deadlock despite
Progressive Countersign
```

単なる実装都合やrefactoring理由では設計を再openしない。

---

# 45. Implementation / Operations Handoff

v0.3 implementation authority:

```text
MIN_EXTERNAL_COUNTERSIGNERS = 2

application-anchored progressive roster consensus

fresh re-application does not inherit
historical expired-game poison
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

4/5/8-member progressive countersign

inviter + peer current consent threshold

inviter freshness after application anchor

second signer withdraw / variant switch

fresh re-application after old hard stall

stale pre-reinvite roster rejection

no-current-application historical expiry block
```

Production activation後のprimary observability sequence:

```text
formation_epoch_started
        ↓
formation_progress
        ↓
formation_ready_to_countersign
  reason_code = sufficient_external_current_consent
        ↓
formation_consent_intent_persisted
        ↓
formation_consent_confirmed
        ↓
WAIT_ROSTER_READY
        ↓
roster_wait_progress
        ↓
authoritative TEAM_READY
```

`formation_ready_to_countersign`のabsenceだけでfailureと断定しない。新epochが開始され、Progressive Countersign thresholdに到達する自然production eventを観測してvalidationする。

---

# Appendix A — v0.3 Migration Summary

v0.2からのnormative behavioral changeは次の2点である。

## A.1 Mandatory last-signer removal

Before:

```text
Saruku waits until every other listed member
currently consents to the exact roster.
```

After:

```text
inviter current exact consent
+
one additional non-Saruku current exact consent
+
all verification/safety gates

→ Saruku may countersign
```

## A.2 Fresh re-application after historical terminal epoch

Before problematic interaction:

```text
historical hard stall for game G
→ G enters expired_games
→ later fresh application for G
→ actual countersign could still be skipped
```

v0.3:

```text
historical hard stall for G
→ fresh trusted opportunity
→ new application epoch / anchor
→ fresh post-anchor exact roster evidence
→ Progressive Countersign allowed
```

Historical terminal epoch itself remains immutable.

---

# Appendix B — Non-goals

v0.3 does not change:

```text
official team size: 4–8
full team consensus requirement
Technocore signature verification
referee authority
application protocol format
Active Acquisition opportunity classes
Formation Reflex LLM authority boundary
team-room protocol
writing logic
submission logic
public Web schema
Cloudflare exporter semantics
SOFT_STALL / HARD_STALL durations
replacement recovery duration
```

---

# Appendix C — Operational Interpretation

Progressive Countersign is intentionally willing to take a bounded formation risk.

The risk accepted is:

> Saruku may commit to a valid, actively forming roster before every remaining member has committed.

The risk not accepted is:

```text
signing an unverified roster
signing across history gaps
signing a stale pre-application roster
signing without inviter participation
signing with only one external signer
mixing roster variants
signing while prior consent outcome is unresolved
signing a frozen/closed/wrong-generation room
```

This balance is deliberate: **team completion is the objective, and verified reversible commitment is preferable to indefinite pre-consent waiting.**
