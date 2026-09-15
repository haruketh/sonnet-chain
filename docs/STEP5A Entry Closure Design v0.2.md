# STEP5A — Entry Closure Design v0.2

Status: **DESIGN PASS / FROZEN**
Parent Design: `STEP5 Endgame Design v0.2`
Scope: **Frozen poem → Publication → Submission → Accepted Receipt → Game-Scoped Participant Release**
Implementation: **Authorized only through separate Codex implementation instructions**

---

## 1. Purpose

STEP5Aは、poem完成後、そのentryをofficial contest上で有効なsubmissionへ到達させるcompletion-critical lifecycleである。

```text
trusted frozen poem
→ authoritative reconciliation
→ correct final contributor
→ publication
→ submission
→ trusted submission_accepted receipt
→ game-scoped participant commitment release
```

最重要原則は、**local completionをglobal completionと誤認しないこと**。

---

## 2. Completion Definition

STEP5A completionは次のみ。

```text
trusted referee submission_accepted receipt
```

`ParticipantRelease`は別のprotocol eventではなく、このauthoritative receiptから導出するdeterministic local consequenceである。

以下はcompletionではない。

```text
line 14 locally complete
canonical poem reconstructed
X publication success
submission POST success
submission receipt pending
```

---

## 3. Non-Goals

STEP5Aは以下を担当しない。

```text
team formation
poem writing
word selection
campaign optimization
voting
shortlist/result tracking
prize claim
```

---

## 4. Authority Model

protocol truthとして信頼できるのは既存trust hierarchyに従う。

```text
trusted referee DID
trusted referee-signed receipts
accepted word history
current/final version
final state hash
final contributor DID
submission acceptance
entry ID
```

以下はprotocol truthにしない。

```text
peer natural-language statement
X display name
team room self-claim
LLM output
local inference alone
```

---

## 5. STEP5A Entry Trigger

STEP4がline 14を書いたこと自体をentry conditionにしない。

trusted referee stateから以下がauthoritatively観測されたときのみ開始する。

```text
poem mechanically complete
14 lines closed
final accepted word known
final_version known
final_state_hash known
final_contributor_did known
```

conceptual state:

```text
FROZEN_POEM_OBSERVED
```

---

## 6. Frozen Poem Input Contract

```yaml
FrozenPoemInput:
  contest_id:
  game_id:
  poem_room:
  room_generation:
  final_version:
  final_state_hash:
  final_contributor_did:
  accepted_word_ledger:
  trusted_final_receipt:
```

`accepted_word_ledger`はaccepted referee historyから再構成されたもの。

---

## 7. `final_state_hash` and `poem_sha256` Are Independent

### final_state_hash

用途:

```text
authoritative accepted-state identity
state-chain consistency
final version validation
final contributor validation
```

### poem_sha256

用途:

```text
canonical poem UTF-8 bytes identity
X publication integrity
sonnet.submit.v1
```

明示的なofficial protocol evidenceがない限り、次を要求してはならない。

```text
final_state_hash == poem_sha256
```

---

## 8. Two-Track Frozen Reconciliation

### Track A — Authoritative State-Chain Reconciliation

```text
trusted referee accepted history
→ complete accepted-word ledger through final_version
→ line 14 closed
→ final_version
→ final_state_hash
→ final contributor
```

最低確認:

```text
game_id equal
poem_room equal
room_generation equal
final_version equal
final_state_hash matches trusted final accepted state
final contributor equal
accepted ledger complete through final_version
line 14 closed
```

### Track B — Canonical Poem Reconstruction

同じauthoritative accepted ledgerのみから:

```text
accepted words
→ canonical lines/stanzas
→ canonical UTF-8 bytes
→ SHA-256
→ local poem_sha256
```

を独立して計算する。

---

## 9. Canonical Poem Format

```text
one ASCII space between accepted words
LF between lines
one blank line between 4/4/4/2 stanzas
no terminal newline
UTF-8
```

以下はcanonical poem本文へ含めない。

```text
title
contest label
game ID
DID attribution
X URL
hashtags
commentary
```

---

## 10. Optional Authoritative Poem Hash

trusted final receipt等に明示的なofficial fieldとして:

```text
poem_sha256
canonical_poem_sha256
```

等が実在する場合のみ、local `poem_sha256`と比較する。

存在しない場合、`final_state_hash`を代替してはならない。

---

## 11. Frozen Reconciliation Result

```yaml
FrozenReconciliation:
  state_chain_status:
    MATCH | INCOMPLETE | CONFLICT | UNRESOLVED
  canonical_reconstruction_status:
    COMPLETE | INCOMPLETE | INVALID
  final_version:
  final_state_hash:
  final_contributor_did:
  local_poem_sha256:
  authoritative_poem_sha256:
    value_or_null
  authoritative_poem_hash_match:
    true | false | not_available
```

publicationへ進める最低条件:

```text
state_chain_status == MATCH
canonical_reconstruction_status == COMPLETE
AND
if authoritative_poem_sha256 exists:
    authoritative_poem_hash_match == true
```

---

## 12. Reconciliation Failure

以下ではpublication/submission禁止。

```text
INCOMPLETE_LOCAL_HISTORY
AUTHORITATIVE_CONFLICT
UNRESOLVED
FROZEN_POEM_RECONCILIATION_FAILED
```

PASS前は:

```text
MUST NOT publish
MUST NOT submit
```

---

## 13. Final Contributor Determination

final contributorはtrusted referee historyにおけるfinal accepted wordの`contributor_did`から決める。

LLM、team claim、X identity等で上書きしない。

---

## 14. Final Contributor Branch

```text
if final_contributor_did == SARUKU_DID:
    SARUKU_FINAL
else:
    PEER_FINAL
```

---

## 15. Publication Authority

### SARUKU_FINAL

Sarukuのみpublication/submissionを実行する。

### PEER_FINAL

```text
MUST NOT publish
MUST NOT submit
```

Sarukuはobserve / coordinate / remind / reconcileのみ行う。

---

## 16. Saruku Terminal Readiness

SARUKU_FINALではpublication前にlocal deterministic readinessを確認する。

```yaml
TerminalReadiness:
  correct_registered_x_account:
  publisher_configured:
  credential_source_safe:
  auth_currently_usable:
  x_identity_verified:
  publication_transport_available:
  submission_transport_available:
```

これはcurrent local FACTであり、将来成功保証ではない。

---

## 17. Terminal Readiness Outcomes

```text
READY
REPAIRABLE_NOT_READY
UNSAFE_CONFIGURATION
AUTH_REQUIRED
ACCOUNT_MISMATCH
```

publication可能なのは`READY`のみ。

---

## 18. Account Identity Hard Gate

publication前に:

```text
authenticated X user
==
registered Sonnet X identity
```

を検証する。

不一致は:

```text
X_ACCOUNT_MISMATCH
```

としてHard Stop。

main Saruku運用用credentialへのfallbackは禁止。

---

## 19. Credential Isolation

禁止:

```text
read main Saruku X credential
search arbitrary credential files
fallback to another account
persist access token into SQLite
log OAuth token
log client secret
```

---

## 20. Publication Planning

publicationはcanonical poem本文とattributionを分離する。

```yaml
PublicationPlan:
  poem_sha256:
  canonical_poem:
  attribution:
  parts:
```

attribution最低要素:

```text
contest_id
game_id
final contributor DID
```

---

## 21. Thread Split Rule

X制限でsingle postに収まらない場合のみthread化する。

```text
only between complete poem lines
never split a word
never rewrite line
never omit line
never reorder line
```

thread全体でexact frozen poemを再構成できなければならない。

---

## 22. Publication Part Identity

```yaml
PublicationPart:
  publication_id:
  game_id:
  final_version:
  poem_sha256:
  part_index:
  part_count:
  exact_content:
  content_sha256:
  parent_part_index:
  parent_post_id:
  state:
  x_post_id:
```

---

## 23. Publication State Machine

```text
PLANNED
↓
SEND_INTENT_PERSISTED
↓
POST_ATTEMPT

├─ successful response with post ID
│    → CONFIRMED
│
├─ transport proves request was never transmitted
│    → DEFINITELY_NOT_SENT
│
└─ request may have reached X
     → DELIVERY_UNKNOWN
```

---

## 24. Publication Write-Ahead Rule

network side effectより先に必ずintentをdurably persistする。

```text
freeze exact part
→ compute content hash
→ persist SEND_INTENT_PERSISTED
→ commit local transaction
→ network POST
```

逆順は禁止。

---

## 25. Publication Confirmation

X Create Postが明確に成功しpost IDを取得した場合:

```text
CONFIRMED
```

保存するもの:

```text
x_post_id
confirmed account/user ID
confirmed_at
returned/persisted text
expected parent relation
```

---

## 26. `DEFINITELY_NOT_SENT`

transport layerがrequest bytesをXへ送信していないことを確実に判定できる場合のみ使用する。

候補:

```text
local DNS resolution failure before connection
connection establishment failure before request transmission
local failure before network send
```

送信有無を保証できないfailureはここへ分類しない。

---

## 27. Retry from `DEFINITELY_NOT_SENT`

以下を満たす場合のみretry可能。

```text
state == DEFINITELY_NOT_SENT
deadline D not passed
same exact publication part
same account
same parent post ID
same frozen poem
```

---

## 28. `DELIVERY_UNKNOWN`

requestがXへ到達した可能性が少しでもある場合:

```text
DELIVERY_UNKNOWN
```

例:

```text
response timeout
connection reset after body transmission may have begun
process crash during request
response lost
malformed response after possible successful create
```

---

## 29. Unknown Is Not Absent

Hard principle:

```text
unknown delivery
!=
publication absent
```

また:

```text
not found
!=
not created
```

である。

---

## 30. Publication Reconciliation

`DELIVERY_UNKNOWN`では自分のX timeline/account history等から:

```text
author_id
exact rendered content
expected parent post ID
created_at within plausible attempt window
```

を照合する。

### Exact match found

```text
DELIVERY_UNKNOWN
→ CONFIRMED
```

post IDをrecover。

### Match not found

```text
DELIVERY_UNKNOWN
→ DELIVERY_UNKNOWN
```

のまま。

X API検索結果のabsenceから`ABSENCE_CONFIRMED`を自動生成しない。

---

## 31. No Automatic Re-POST from DELIVERY_UNKNOWN

`DELIVERY_UNKNOWN`からautomatic X POST retryは禁止。

deadline前でも同じ。

---

## 32. Publication Deadline Rule

contest deadline D以後:

```text
new X publication
new publication retry
```

は禁止。

existing-post reconciliation / receipt observationは継続可能。

---

## 33. Thread Dependency

part N+1はpart Nのconfirmed post IDに依存する。

parentが`DELIVERY_UNKNOWN`なら後続partを送らない。

全required partsがconfirmedして初めて:

```text
PUBLICATION_CONFIRMED
```

---

## 34. No Edit Rule

STEP5A MUST NOT edit a published Sonnet poem post.

X Edit Postを使用しない。

submission-time lookupでedit historyがoriginally confirmed publicationと不整合なら:

```text
PUBLICATION_INTEGRITY_CONFLICT
```

特にpre-deadlineの誤投稿をpost-deadline editでcorrect poemへ変えてvalid扱いしてはならない。

---

## 35. Submission-Time Publication Integrity Revalidation

submission packetをfreezeする直前にknown `x_post_id`を可能な範囲で再取得・再検証する。

最低確認:

```text
post exists
author_id == registered Sonnet X account
rendered text == exact expected content
expected thread parent relationship
all required parts present
ordering valid
publication timestamp <= D
no inconsistent edit history
```

outcome:

```text
PUBLICATION_INTEGRITY_MATCH
PUBLICATION_INTEGRITY_CONFLICT
PUBLICATION_INTEGRITY_UNRESOLVED
```

---

## 36. PUBLICATION_INTEGRITY_MATCH

current X stateがexpected publicationと一致。

submission allowed。

---

## 37. PUBLICATION_INTEGRITY_CONFLICT

明確な矛盾がある場合。

例:

```text
wrong author
wrong text
missing required thread part
wrong parent relationship
known post deleted
edit history inconsistent with originally confirmed publication
publication timestamp after D
```

submission MUST be blocked。

過去のsuccessful Create responseで上書きしない。

---

## 38. PUBLICATION_INTEGRITY_UNRESOLVED

X read API timeout、temporary outage、rate/service failure等によりcurrent stateを再確認できない場合。

```text
revalidation unavailable
!=
publication invalid
```

まずdeadline-aware bounded retryを行う。

---

## 39. Prior Strong Publication Confirmation

still UNRESOLVEDでもdegraded submission pathを許可できるのは、各required publication partについて少なくとも:

```text
Create Post returned a successful response with known x_post_id
returned/persisted text matched exact expected content
authenticated X account identity matched registered Sonnet account
thread parent relationship was known and correct at creation time
all required parts had previously reached CONFIRMED
no local edit/delete action was performed
no known integrity conflict exists
```

が成立する場合。

これを:

```text
PRIOR_STRONG_CONFIRMATION
```

とする。

---

## 40. Integrity Decision

```text
PUBLICATION_INTEGRITY_MATCH
→ submit

PUBLICATION_INTEGRITY_CONFLICT
→ do not submit

PUBLICATION_INTEGRITY_UNRESOLVED
→ bounded retry while time permits

still UNRESOLVED
AND PRIOR_STRONG_CONFIRMATION
AND no known conflict
→ degraded submission allowed

still UNRESOLVED
AND no PRIOR_STRONG_CONFIRMATION
→ submission blocked
```

このdegraded pathはX read API availabilityをcompletion-critical Hard dependencyにしないためのliveness mechanismであり、publication validation ruleを弱めるものではない。

---

## 41. Submission Preconditions

Sarukuがsubmission intentをfreezeできる条件:

```text
SARUKU_FINAL
state-chain reconciliation == MATCH
canonical poem reconstruction == COMPLETE
all publication parts previously CONFIRMED
AND one of:
  publication_integrity == MATCH
  OR
  publication_integrity == UNRESOLVED
  AND PRIOR_STRONG_CONFIRMATION
  AND bounded revalidation attempted as time permits
  AND no known conflict exists
correct registered X account identity established
all required x_post_ids known
no accepted submission already observed
deadline semantics permit submission
```

---

## 42. Submission Packet

```json
{
  "type": "sonnet.submit.v1",
  "contest_id": "sonnet-2",
  "game_id": "<game>",
  "poem_room": "<room>",
  "room_generation": 0,
  "final_version": 0,
  "poem_sha256": "<canonical hash>",
  "x_post_ids": ["..."],
  "request_id": "<stable request id>"
}
```

---

## 43. Submission Intent Schema

```yaml
SubmissionIntent:
  game_id:
  poem_room:
  room_generation:
  final_version:
  final_state_hash:
  poem_sha256:
  x_post_ids:
  request_id:
  packet_json:
  packet_sha256:
  state:
  created_at:
  last_attempt_at:
```

一度pending intentとしてpersistしたpacketは変更しない。

---

## 44. Submission State Machine

```text
NOT_PREPARED
↓
SEND_INTENT_PERSISTED
↓
SENDING
├→ RECEIPT_ACCEPTED
├→ RECEIPT_REJECTED
└→ DELIVERY_UNKNOWN
```

---

## 45. Submission Write-Ahead

必ず:

```text
construct exact packet
→ fix request_id
→ canonical serialize
→ compute packet hash
→ persist SEND_INTENT_PERSISTED
→ COMMIT
→ POST
```

---

## 46. Same Request Retry

同一logical submissionでoutcomeが不明な場合:

```text
same request_id
same exact payload bytes/semantics
```

を維持する。

pending same actionにnew request IDを作らない。

---

## 47. Submission Rejection

authoritative referee rejectionが明確な場合:

```text
RECEIPT_REJECTED
```

reasonをclassifyする。

```text
correctable transport/protocol field
authoritative frozen-state mismatch
wrong final contributor
publication invalid
deadline late
already submitted
```

correctable before Dのみnew packet + new request IDを許可する。

---

## 48. Submission Delivery Unknown

POST deliveryが不明なら:

```text
SUBMISSION_DELIVERY_UNKNOWN
```

network failureだけで「送れていない」と判断しない。

---

## 49. Deadline Semantics

判定基準は:

```text
referee durable intake
```

local response arrival時刻だけでacceptabilityを決めない。

例:

```text
11:59:59 request sent
12:00:00 deadline
12:00:01 client timeout
```

この場合:

```text
SUBMISSION_DELIVERY_UNKNOWN
```

であり`DEADLINE_EXPIRED`ではない。

---

## 50. Post-Deadline Reconciliation

D後でもpossibly-on-time pending requestについてはsame request IDでreceipt reconciliationを継続する。

目的は:

```text
was the original request durably received before D?
```

の確認。

D後にnew request_id / new packet / new publicationでdeadlineを回避してはならない。

---

## 51. Submission Receipt Authentication

receiptは最低限:

```text
pinned referee DID
valid signature
matching contest_id
matching game_id
matching request_id where applicable
matching frozen entry
```

を確認する。

POST成功だけではcompletionではない。

---

## 52. Accepted Submission

trusted receiptがacceptedを示したら:

```text
SUBMISSION_ACCEPTED
```

へ遷移。

記録:

```text
entry_id
accepted request_id
receipt seq
referee DID
accepted state metadata
```

---

## 53. Participant Release Is Game-Scoped

accepted submission receiptそのものがrelease authority。

別のrelease receiptを待たない。

```yaml
ParticipantCommitment:
  contest_id:
  game_id:
  roster_fingerprint:
  state:
    ACTIVE | RELEASED
  release_entry_id:
  release_submission_request_id:
  release_receipt_seq:
  release_referee_did:
```

---

## 54. Release Transition

trusted:

```text
submission_accepted(game=A)
```

を観測したら:

```text
commitment[A]: ACTIVE → RELEASED
```

idempotentに実行する。

---

## 55. Replay Safety

```text
game A RELEASED
game B ACTIVE
restart/replay old game A receipt
```

でも:

```text
A remains RELEASED
B remains ACTIVE
```

old releaseでglobal phaseをDISCOVERYへ戻してgame Bを壊してはならない。

---

## 56. STEP1 Eligibility Derivation

STEP1 eligibilityはglobal resetではなくcurrent commitment stateからderiveする。

```text
no ACTIVE unresolved poem commitment
→ may form/join next team
```

ただしSTEP1自身のactive application / unresolved roster consent / team formation Safety gatesは継続する。

---

## 57. ParticipantRelease Interface

```yaml
ParticipantRelease:
  contest_id:
  released_game_id:
  roster_fingerprint:
  entry_id:
  accepted_submission_request_id:
  submission_receipt_seq:
  referee_did:
```

---

## 58. Phase Ownership

`phase`がruntime表示上必要でもgame-scoped commitment/application/current active workからderiveされるpresentation/runtime control stateとする。

古いreceipt単独でglobal phaseを書き換えない。

---

## 59. Release and Portfolio Fork

release後:

```text
participant lifecycle
→ STEP1 / DISCOVERY eligibility

submitted entry lifecycle
→ STEP5B portfolio
```

両方を独立管理する。

---

## 60. PEER_FINAL Detailed Path

```text
FROZEN_POEM_MATCHED
↓
WAIT_PEER_PUBLICATION
↓
WAIT_PEER_SUBMISSION
↓
SUBMISSION_ACCEPTED
↓
commitment[game] = RELEASED
```

peer claimだけでsubmission completionにはしない。

---

## 61. Peer Reminder State

```yaml
PeerEndgameCoordination:
  game_id:
  final_contributor_did:
  frozen_at:
  last_observed_progress_at:
  last_reminder_at:
  reminder_stage:
  dedupe_key:
```

---

## 62. Reminder Principle

```text
cannot substitute
!=
must remain passive
```

Sarukuはbounded coordinationを行ってよい。

conceptual stages:

```text
STAGE_0_WAIT
STAGE_1_REMIND
STAGE_2_URGENT_REMIND
STAGE_3_DEADLINE_OBSERVE
```

meaningful progressのみtimer reset可能。

```text
peer publication evidence
submission observed
trusted receipt observed
```

team chatterではresetしない。

---

## 63. Reminder Dedupe

key例:

```text
game_id
final_version
final_contributor_did
reminder_stage
```

同一state/stageでspamしない。

---

## 64. PEER_FINAL Deadline Reconciliation

PEER_FINALではSarukuはpeerがdeadline直前にsubmitしてresponseを失ったかどうかを知れない。

したがってD到達時にreceipt未観測だけで:

```text
UNSUBMITTED_AT_DEADLINE
```

へ確定しない。

代わりに:

```text
PEER_DEADLINE_RECONCILING
```

へ進む。

---

## 65. Post-Deadline Peer Observation

D後も:

```text
submission room history
referee receipts
authoritative result / eligibility records
```

を観測し、D以前のvalid submission intake有無をauthority側から確定する。

terminal outcomes:

```text
PEER_SUBMISSION_ACCEPTED
UNSUBMITTED_AT_DEADLINE
PEER_SUBMISSION_STATUS_UNRESOLVED
```

`UNSUBMITTED_AT_DEADLINE`はauthoritative complete history/resultからno on-time valid submission existedと確定した場合のみ。

---

## 66. Global Outbound Arbitration

STEP5 Overviewのpriorityを継承する。

```text
1. safety / reconciliation
2. completion-critical active participant lifecycle
   including STEP5A
3. optional STEP5B campaign
```

STEP5Aは5B campaignより優先。

既存one-intentional-Technocore-write-per-cycle policyを維持する場合、STEP5A peer reminder / submission retry等も同じglobal arbitrationに参加する。

---

## 67. X Writes Are Separate Side Effects

X publicationとTechnocore writeは別transportだが、uncontrolled simultaneous side effectsを避ける。

publication confirmationをpersistしてからsubmission intentへ進む。

---

## 68. Restart Semantics

process restart後はpersistent stateから再構築する。

禁止:

```text
assume unsent because process restarted
generate new submission request ID
restart publication from part 1 blindly
forget DELIVERY_UNKNOWN
reset peer reminder timer blindly
```

recovery ordering:

```text
load persistent STEP5A state
→ reconcile trusted referee state
→ reconcile pending submission
→ reconcile publication DELIVERY_UNKNOWN
→ resume safe next action
```

---

## 69. Persistence Domains

最低限永続化:

```text
game-scoped participant commitment
frozen entry identity
final state-chain identity
canonical poem hash
optional authoritative poem hash
reconciliation result
publication plan
publication part state
submission intent
submission receipt
peer reminder state
PEER_DEADLINE_RECONCILING state
```

---

## 70. Secrets Must Not Be Persisted

保存禁止:

```text
X access token
refresh token
client secret
private DID key
OpenAI API key
```

ログにも出さない。

---

## 71. Failure Taxonomy

最低限区別:

```text
FROZEN_POEM_RECONCILIATION_FAILED
SARUKU_FINAL_NOT_READY
X_AUTH_REQUIRED
X_ACCOUNT_MISMATCH
PUBLICATION_DELIVERY_UNKNOWN
PUBLICATION_FAILED_CONFIRMED
PUBLICATION_INTEGRITY_CONFLICT
PUBLICATION_INTEGRITY_UNRESOLVED
SUBMISSION_DELIVERY_UNKNOWN
SUBMISSION_REJECTED
PEER_FINAL_STALLED
PEER_DEADLINE_RECONCILING
UNSUBMITTED_AT_DEADLINE
```

単一`ERROR`へ潰さない。

---

## 72. Safety vs Liveness

Safety:

```text
do not publish wrong poem
do not publish from wrong account
do not duplicate ambiguous X post
do not submit as wrong contributor
do not mutate frozen poem
do not release wrong game commitment
```

Liveness:

```text
repair auth
reconcile ambiguous X delivery
retry DEFINITELY_NOT_SENT publication before D
same-ID submission retry
deadline-crossing reconciliation
bounded peer reminder
allow degraded submission on X read outage only with strong prior confirmation
```

Safetyを壊してlivenessを作らない。

---

## 73. Hard Invariants

```text
H1 final_state_hash and poem_sha256 are independent identities unless protocol explicitly defines otherwise.
H2 No publication before authoritative state-chain reconciliation succeeds.
H3 Canonical poem SHA-256 is computed independently from canonical UTF-8 bytes.
H4 If an authoritative canonical-poem hash exists, local poem_sha256 must match it.
H5 Only authoritative final contributor may publish/submit.
H6 Saruku never acts on behalf of peer final contributor.
H7 Every X side effect has durable intent before network send.
H8 Transport-proven DEFINITELY_NOT_SENT may retry before D.
H9 DELIVERY_UNKNOWN never permits automatic re-POST.
H10 Timeline/post lookup "not found" never proves non-creation.
H11 No new X publication after D.
H12 STEP5A never edits a published poem post.
H13 A confirmed publication integrity conflict always blocks submission.
H14 Temporary inability to re-read X does not by itself invalidate a previously strongly confirmed publication.
H15 The degraded UNRESOLVED path requires prior strong publication confirmation and absence of any known conflict.
H16 Every submission has durable exact packet and stable request_id before send.
H17 Same logical ambiguous submission reuses the same request_id.
H18 D crossing does not invalidate a possibly-on-time unresolved submission.
H19 Accepted submission receipt itself is the authoritative release event.
H20 Participant commitment release is game-scoped and idempotent.
H21 A late/replayed release for an old game cannot alter a newer ACTIVE commitment.
H22 PEER_FINAL is not declared unsubmited merely because D passed without a receipt.
H23 Optional STEP5B activity cannot starve STEP5A.
H24 Secrets never enter persisted contest state/logs.
H25 An edit history inconsistent with the originally confirmed publication is a publication integrity conflict.
```

---

## 74. Required Interfaces to Existing Steps

### STEP1

game-scoped `ParticipantRelease` / commitment stateを使いnew team formation eligibilityを判断する。

### STEP2

peer publication/submission claims、reminder responseをsemantic observationsとして扱う。protocol truthにはしない。

### STEP3

endgame proximityのcoordinationは可能だがprotocol authorityとは分離する。

### STEP4

```text
peer publisher risk = soft
Saruku terminal readiness = local deterministic fact
```

を維持する。

---

## 75. STEP4 → STEP5A Handoff

STEP4は直接publicationをtriggerしない。

```text
final word proposal
→ trusted acceptance
→ final frozen state observed
→ STEP5A reconciliation
```

---

## 76. STEP5A → STEP5B Handoff

accepted submission後:

```yaml
SubmittedEntry:
  entry_id:
  game_id:
  frozen_roster:
  final_contributor:
  poem_sha256:
  x_post_ids:
  submission_receipt:
```

を5B portfolioへ追加する。

---

## 77. Synthetic Test Requirements

### Frozen reconciliation

```text
matching ledger/state
missing accepted word
wrong final version
wrong state hash
wrong final contributor
final_state_hash != poem_sha256 is allowed
no authoritative poem hash → no invalid equality requirement
explicit authoritative poem hash mismatch → publication blocked
```

### Publication

```text
single post success
multi-part thread success
crash after intent before POST
DEFINITELY_NOT_SENT → retry allowed before D
timeout after possible send → DELIVERY_UNKNOWN
post found after ambiguity → recover
timeline no match after ambiguity → remains DELIVERY_UNKNOWN / no retry
deadline crossed → no new X post
parent unknown → no child post
wrong X account → hard stop
STEP5A never edits post
```

### Publication integrity

```text
revalidation MATCH → submit
revalidation CONFLICT → block
Create Post success + revalidation timeout + strong confirmation + bounded retry fails → degraded submit allowed
ambiguous original publish + revalidation unavailable + no strong confirmation → block
edit history conflict → block
```

### Submission

```text
intent persisted before POST
success receipt
timeout after possibly-on-time POST
restart uses same request_id
same-ID retry
rejected correction uses new request_id
deadline passes with unresolved send
late new send blocked
accepted receipt releases correct game commitment
```

### Commitment release

```text
submission_accepted game A → commitment[A] RELEASED
release derived from accepted receipt
receipt replay idempotent
game B ACTIVE + old game A replay → game B unchanged
```

### Peer final

```text
bounded wait
reminder dedupe
meaningful progress resets escalation
chatter does not reset
Saruku never publishes/submits for peer
D with no receipt → PEER_DEADLINE_RECONCILING
later referee on-time acceptance → release succeeds
later authoritative no-submission result → UNSUBMITTED_AT_DEADLINE
```

### Integration

```text
STEP5A priority beats STEP5B campaign
release returns STEP1 eligibility
submitted entry persists into STEP5B
restart preserves ambiguity states
```

---

## 78. Implementation Boundary

概念的には:

```text
EntryClosureEngine
├─ FrozenPoemReconciler
├─ PublicationPlanner
├─ PublicationJournal
├─ SubmissionJournal
├─ PeerFinalCoordinator
└─ ParticipantReleaseManager
```

程度に責務分割する。

ただしmodule/class構成は既存codebase architectureとの整合を優先する。

---

## 79. Final Design Status

All blocking review issues are closed.

```text
Q1 final_state_hash vs poem_sha256 → CLOSED
Q2 ambiguous X delivery / absence proof → CLOSED
Q8 game-scoped participant release → CLOSED
PEER_FINAL deadline ambiguity → CLOSED
submission-time publication revalidation liveness → CLOSED
```

**STEP5A Entry Closure Design v0.2: DESIGN PASS / FROZEN**

Next phase:

```text
Codex implementation instructions
→ implementation
→ tests
→ patch review
```
