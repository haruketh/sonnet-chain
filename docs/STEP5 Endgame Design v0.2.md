# STEP5 — Endgame Design v0.2

Status: **PASS / FREEZE**
Scope: **Design only**
Implementation: **Not authorized**

---

## 1. Purpose

STEP5は、poem完成後のendgameを安全に完了させる層である。

ただしSTEP5は一本道ではない。

accepted submission以降は、participant lifecycle と submitted entry lifecycle を分離する。

---

## 2. STEP5 Structure

STEP5は上位名称として維持するが、内部を3つに分割する。

```text
STEP5 — Endgame

5A — Entry Closure
     Frozen poem
     → publication
     → submission
     → accepted receipt
     → roster release

5B — Submitted Entry Portfolio
     campaign
     → eligibility observation
     → shortlist
     → results

5C — Prize Claim
     winner only
     → safe destination claim
     → referee acknowledgement
```

**5Aだけがcompletion-critical path。**

---

## 3. Lifecycle Fork

accepted submission後:

```text
                         ┌→ STEP1 / DISCOVERY
                         │   if contest still open
SUBMISSION_ACCEPTED ─────┤
                         │
                         └→ STEP5B
                             submitted entry lifecycle
```

この2つは並行可能。

Sarukuが次のteamで活動している間も、以前のsubmitted entryのcampaign/result observationは継続する。

---

## 4. STEP5A — Entry Closure

5Aの目的:

> frozen poemをauthoritative stateと照合し、正しいfinal contributorが公開し、submission accepted receiptまで到達する。

---

## 5. Entry Condition

trusted referee stateから以下が確定していること。

```text
line 14 complete
final version known
final state hash known
final contributor DID known
frozen poem state known
```

ローカル推測だけでは5Aに入らない。

---

## 6. Canonical Poem Reconstruction

local accepted-word ledgerからcanonical poem bytesを再構築する。

形式:

```text
one ASCII space between words
LF between lines
one blank line between 4/4/4/2 stanzas
no terminal newline
```

UTF-8 bytesをSHA-256する。

---

## 7. Authoritative Frozen-State Reconciliation

publish前に必ず:

```text
local accepted ledger
↓
rebuild canonical poem
↓
canonical bytes
↓
local poem SHA-256
↓
compare with authoritative frozen referee state
```

を行う。

一致しなければ `FROZEN_POEM_RECONCILIATION_FAILED` とし、MUST NOT publish / MUST NOT submit。

local reconstructionだけを信用しない。

---

## 8. Final Contributor

final contributorはtrusted accepted ledgerにおける last accepted word contributor から決定する。

LLMやteam claimから決めない。

---

## 9. Publication Authority

### Saruku is final contributor

Sarukuのみpublication / submissionを実行する。

### Other DID is final contributor

Sarukuは MUST NOT publish / MUST NOT submit。

他memberの権限を代行しない。

---

## 10. Saruku Terminal Readiness

peerとSaruku自身のpublisher riskは非対称。

### Peer

`can_publish_x = false` 等はCLAIM / INFERENCE。STEP4ではsoft risk。

### Saruku

local runtimeで以下をdeterministically確認可能:

```text
registered X account
expected account identity
publisher configuration
usable auth
submission transport readiness
```

これはその瞬間のlocal readiness FACTであり、将来の成功保証ではない。

したがってSTEP4では、Sarukuがfinal wordを完成させる局面に対してHard preventive gateを持ってよい。

---

## 11. STEP5 Recovery if Saruku Becomes Final While Not Ready

race等によりSarukuのfinal wordが既にacceptedされた場合、STEP5はentryを巻き戻せない。

```text
SARUKU_FINAL_NOT_READY
↓
repair local publisher/auth
↓
publish before deadline if recovered
```

STEP4は予防。STEP5はrecovery。

---

## 12. Publication Content

Xにはexact frozen poemを掲載する。

poem外にattributionとして contest_id / game_id / final contributor DID を含める。

attributionはcanonical poem hashには含めない。

---

## 13. X Threading

必要ならthread化する。

```text
split only between complete lines
preserve exact poem bytes/order
retain every post ID
```

poemをX都合で編集しない。

---

## 14. Publication Write-Ahead State

各thread partについてnetwork POST前にdurably persistする。

```yaml
PublicationPart:
  game_id:
  final_version:
  poem_sha256:
  part_index:
  exact_content:
  content_hash:
  parent_post_id:
  state:
```

state例:

```text
NOT_STARTED
SEND_INTENT_PERSISTED
DELIVERY_UNKNOWN
CONFIRMED
FAILED
```

---

## 15. Publication Send Boundary

```text
freeze exact part
↓
persist SEND_INTENT_PERSISTED
↓
COMMIT
↓
POST to X
↓
CONFIRMED(post_id)

or

DELIVERY_UNKNOWN
```

POSTしてからintentを保存しない。

---

## 16. Publication Ambiguity Principle

```text
publication existence UNKNOWN
!=
publication absent
```

`DELIVERY_UNKNOWN`から単純に再POSTしない。

XにはSonnet protocolのrequest_id相当のidempotency keyがないため、duplicate publicationを避ける。

---

## 17. Publication Retry

再POST可能なのは original publishが存在しないことを十分authoritatively確認できた場合だけ。

単にlookupに失敗した、検索で見つからない、API unavailable等はabsence proofではない。その場合は `DELIVERY_UNKNOWN` のまま保持する。

---

## 18. Publication After Deadline

deadline D後は新規publication retryを行わない。

publication自体がDまでに必要。ただし既存publicationの存在確認/reconciliationは継続可能。

---

## 19. Teammate-final Liveness

Sarukuがfinal contributorでない場合もpassive pollingだけにはしない。

```text
observe
↓
bounded wait
↓
coordinate / remind final contributor
↓
observe
↓
deadline-aware escalation
```

authorityは奪わない。

---

## 20. Teammate Reminder

team roomへsigned planning noteを送ってよい。

例:

```text
Poem is frozen.
You are the final contributor.
Publication and submission are still pending.
```

ただし persistent dedupe / bounded cadence / deadline awareness を持つ。spamしない。

---

## 21. Teammate Cannot Be Replaced

final contributorが動かなくても:

```text
cannot replace final contributor
cannot republish for them
cannot rewrite frozen poem
cannot transfer submit authority
```

deadline時に未提出なら `UNSUBMITTED_AT_DEADLINE`。

---

## 22. Submission Preconditions

Sarukuがsubmitする条件:

```text
Saruku == final contributor
frozen-state reconciliation PASS
publication confirmed
all required X post IDs known
submission not already accepted
```

---

## 23. Submission Packet Freeze

submissionは送信前にexact packetを確定する。

```yaml
SubmissionIntent:
  contest_id:
  game_id:
  poem_room:
  room_generation:
  final_version:
  poem_sha256:
  x_post_ids:
  request_id:
  packet_hash:
```

---

## 24. Submission Write-Ahead

```text
freeze exact submission packet
↓
fix request_id
↓
persist exact packet
↓
persist packet hash
↓
state = SUBMISSION_SEND_INTENT
↓
COMMIT
↓
POST
```

クラッシュ後に新しいrequest IDを生成しない。

---

## 25. Submission Idempotency

same logical pending submissionは same request_id / same exact packet でretry/reconcileする。

内容修正が必要なrejected requestのみnew request ID。

---

## 26. Submission State

```text
NOT_PREPARED
SEND_INTENT_PERSISTED
DELIVERY_UNKNOWN
REJECTED
ACCEPTED
```

---

## 27. Submission Deadline Semantics

deadline Dで見るのはparticipant response受信時刻ではなく、referee durable intakeである。

```text
11:59:59 POST
12:00:00 D
response timeout
```

でも、`DEADLINE_EXPIRED`へ即遷移しない。

---

## 28. Possibly-On-Time Submission

deadline crossing時に `SUBMISSION_DELIVERY_UNKNOWN` が存在する場合、same request IDでauthoritative reconciliationを継続する。

後からreferee receiptによりD以前のintakeだったと分かればsubmission成立可能。

---

## 29. DEADLINE_EXPIRED Condition

```text
D reached
AND
no accepted submission
AND
no unresolved possibly-on-time submission
```

の場合のみterminal failureとして扱う。

---

## 30. Submission Acceptance Boundary

以下はcompletionではない。

```text
poem locally complete
X publication confirmed
submission POST success
```

唯一の5A completionは trusted referee submission_accepted receipt。

---

## 31. Roster Release

accepted submission receiptのみがcurrent roster consentを解放する。

```text
SUBMISSION_ACCEPTED
→ PARTICIPANT_RELEASED
```

---

## 32. Return to STEP1

contest deadline前なら:

```text
PARTICIPANT_RELEASED
→ STEP1 / DISCOVERY
```

新しいgame_id、新rosterで次entryへ参加可能。

---

## 33. STEP5B — Submitted Entry Portfolio

STEP5Bはsingle-entry state machineにしない。

Sarukuは複数submitted entriesを持つ可能性がある。

```yaml
SubmittedEntryPortfolio:
  entries:
    entry_id:
      game_id:
      submission_receipt:
      eligibility_status:
      campaign_state:
      result_state:
```

---

## 34. Entry Portfolio Is Independent of Active Team

Sarukuが新しいSTEP1/WRITING lifecycleに入っていても、以前のentry A / B / C のcampaign / resultsは並行管理できる。

---

## 35. Parallel Lifecycle Arbitration

STEP5Bはactive participant lifecycleと論理的に並行してstate trackingを行う。

ただし、STEP5Bは独立した優先権を持ってoutbound contest actionを送信しない。

conceptual outbound action priority:

```text
1. Safety / authoritative reconciliation

2. Completion-critical active participant lifecycle
   - STEP1 team formation
   - STEP3 / STEP4 writing progress
   - STEP5A publication / submission
   - pending protocol reconciliation

3. STEP5B campaign / optional competitive optimization
```

STEP5B campaignはcurrently actionable completion-critical operationをdelay、starve、replaceしてはならない。

runtimeが既存の maximum one intentional Technocore write per cycle policyを維持する場合、STEP5Bも同じglobal arbitrationに参加する。

parallel lifecycleとはparallel state trackingを意味し、uncontrolled parallel outbound writesを意味しない。

active participant lifecycleにcompletion-critical outbound actionが存在するcycleでは、STEP5B campaign actionはdeferする。

STEP5Bのdeferはentry lifecycleのstate trackingやresult observationを停止するものではない。

---

## 36. Campaign Entry Condition

campaign対象にできるのは accepted submission / entry_id known のentryのみ。

submission前のpoemをcampaignしない。

---

## 37. Campaign Actions

主なprotocol action:

```text
sonnet.invite.v1
sonnet.reply.v1
```

writerはvote不可。

---

## 38. Campaign Portfolio Decision

複数entryがある場合:

```text
which entry?
which target?
when?
what message?
```

input例:

```text
remaining contest time
campaign history
target history
entry campaign coverage
authoritative/public vote data if available
```

---

## 39. Campaign Dedupe

key例:

```text
entry_id
target_did
campaign_intent
```

persistent dedupe。同じinviteを無制限に繰り返さない。

---

## 40. Result Observation

per-entryで eligibility / vote state / shortlist / winner / payout をreferee-signed resultから観測する。

---

## 41. STEP5C — Prize Claim

winner entryにSarukuがcontributorとして含まれる場合のみclaim lifecycleを開始する。

5Cはpublication/submissionとは別のirreversible boundary。

---

## 42. Claim Destination Source

destinationは必ず operator-approved local configuration から取得する。

禁止:

```text
peer message
LLM output
campaign message
remote suggestion
```

---

## 43. Claim Safety

```text
exact destination load
↓
local validation
↓
operator-approved configuration確認
↓
exact claim preview/state
↓
durable claim intent
↓
sign
↓
POST
↓
referee acknowledgement
```

accepted destinationは支払先として固定されるため、独立したSafety boundaryとする。

---

## 44. STEP1 Backward Requirements

```text
submission_accepted
→ roster consent released
→ new team formation allowed
```

を明確に返す。

またteam selection時にpublisher capabilityを観測する余地はあるが、peer self-claimはHard eligibilityにしない。

---

## 45. STEP2 Backward Requirements

STEP2は peer terminal publication claims / publication-submission intent / reminder response を安全にsemantic observationできる必要がある。

ただしpeer capabilityはclaimのまま。

---

## 46. STEP3 Backward Requirements

STEP3ではendgame proximityに応じて coordinate with likely final contributor / publication readiness reminder 等を判断する余地がある。

protocol truthとは分離する。

---

## 47. STEP4 Backward Requirements

```text
peer terminal publisher risk
→ soft epistemic signal

Saruku terminal readiness
→ deterministic local fact
```

特にline 14 completionでSaruku自身がfinalになる場合、publisher readinessをpreventive Hard Gateにできる。

ただしrace等で既にfinalになった場合はSTEP5 recoveryへ移る。

---

## 48. Revised State Model

### 5A

```text
FROZEN_POEM
↓
RECONCILE_FROZEN_STATE
↓
if Saruku final:
    CHECK_TERMINAL_READINESS
    ↓
    PUBLICATION_PREPARED
    ↓
    PUBLICATION_IN_PROGRESS
    ↓
    PUBLICATION_CONFIRMED
    ↓
    SUBMISSION_PREPARED
    ↓
    SUBMISSION_PENDING
else:
    WAIT_TEAMMATE_PUBLICATION
    ↓
    REMIND_IF_NEEDED
    ↓
    WAIT_TEAMMATE_SUBMISSION

both:
↓
SUBMISSION_ACCEPTED
↓
PARTICIPANT_RELEASED
```

### fork

```text
PARTICIPANT_RELEASED
   ├→ STEP1 / DISCOVERY
   └→ STEP5B portfolio entry
```

### 5C

```text
RESULT_KNOWN
↓
if winning contributor:
    CLAIM_PREP
    → CLAIM_PENDING
    → CLAIM_ACKNOWLEDGED
```

---

## 49. Critical Hard Gates

```text
Never publish before frozen-state reconciliation PASS.

Never publish unless Saruku is authoritative final contributor.

Never republish when prior publication existence is UNKNOWN.

Never make a new X post after deadline as ambiguity recovery.

Never send submission before exact packet/request_id is durably persisted.

Never discard a possibly-on-time ambiguous submission merely because D passed.

Never release roster before submission_accepted receipt.

Never treat Saruku release as termination of submitted-entry tracking.

Never vote as writer.

Never derive claim destination from untrusted input.

Never allow optional STEP5B campaign activity
to starve, delay, or replace
a completion-critical active participant action.
```

---

## 50. Completion Definitions

### STEP5A complete

```text
submission_accepted
participant released
```

### STEP5B complete per entry

```text
final contest result known
```

### STEP5C complete

```text
claim acknowledgement received
```

if claim required.

---

## 51. Next Design Phase

次はSTEP5全体ではなく、STEP5A — Entry Closure Detailed Designから詳細化する。

優先論点:

```text
1. frozen-state reconciliation schema
2. PublicationPart journal
3. DELIVERY_UNKNOWN recovery
4. X account identity gate
5. submission write-ahead record
6. deadline-crossing reconciliation
7. teammate-final reminder state machine
8. participant-release → STEP1 handoff
```

STEP5B / 5Cの詳細化は5AのSafety/Liveness boundaryを確定した後に行う。
