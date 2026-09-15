# STEP5B — Submitted Entry Portfolio Design v0.2

Status: **DESIGN PASS / FROZEN**
Scope: **Design only**
Implementation: **Not yet authorized**

---

## 1. Purpose

STEP5Bは、accepted submission後のentryをSarukuのactive participant lifecycleとは独立して追跡する。

```text
STEP5A
SUBMISSION_ACCEPTED
        ↓
SubmittedEntry added to portfolio
        ↓
┌───────────────────────┐
│ STEP5B Portfolio      │
│                       │
│ campaign              │
│ eligibility           │
│ shortlist             │
│ result                │
│ payout observation    │
└───────────────────────┘

同時に:

SUBMISSION_ACCEPTED
        ↓
PARTICIPANT_RELEASED
        ↓
STEP1 / DISCOVERY
```

STEP5BはSarukuを一つのentryに拘束しない。

Sarukuがentry Aのcampaignを観測しながらentry Bを書き、entry B提出後にはA/B両方をportfolioで保持できる。

accepted submissionによるroster releaseはeligibility review完了を待たず発生し、後のeligibility判断も新しいprojectを取り消さない。

---

## 2. Core Principle

STEP5Bは二つのplaneに分離する。

```text
Observation Plane
    authoritative factsを読む
    ↓
    eligibility
    shortlist
    result
    payout

Campaign Plane
    optional outbound optimization
    ↓
    voter invitation
```

Observation Planeは常に動いてよい。

Campaign Planeはoptionalであり、

```text
Safety / reconciliation
    >
active participant completion
    >
STEP5B campaign
```

の優先順位を絶対に越えない。

STEP5Bは「parallel state tracking」であり、「parallel uncontrolled writes」ではない。

---

## 3. STEP5B Is Not a Global Phase

既存のglobal `phase`はactive participant lifecycleを表す。

したがって以下のようなphaseは追加しない。

```text
CAMPAIGNING
WAIT_SHORTLIST
WAIT_RESULT
```

Sarukuが、

```text
phase = WRITING
```

であっても、

```text
portfolio:
  entry-a: RESULT_PENDING
  entry-b: CAMPAIGN_AVAILABLE
```

を同時に持てる。

STEP5B stateはgame/entry scoped persistent portfolioとして保持する。

---

## 4. Entry Identity

STEP5Bのprimary identityは:

```text
entry_id
```

とする。

`entry_id`はopaque authoritative identifierとして扱う。

```text
entry_id == game_id
```

を一般ルールとして仮定しない。

5Aから以下をhandoffする。

```yaml
SubmittedEntry:
  entry_id:
  game_id:
  submission_request_id:
  submission_receipt_seq:
  submission_referee_did:
  accepted_at:

  frozen_roster:
  final_contributor_did:
  poem_sha256:
  x_post_ids:
```

これらはimmutable entry identityである。

`accepted_at`のcanonical sourceは、trusted accepted-submission receiptに含まれるauthoritative server/referee timestampとする。

```text
accepted_at
= trusted accepted-submission receipt authoritative timestamp
```

local observation timeをcanonical `accepted_at`として使用してはならない。restart、delayed observation、temporary outageによってportfolio orderingが変化しないことが必要である。local observation timestampはdiagnostics用の別fieldとして保持してよい。

5A設計でもaccepted submission後にこのentry snapshotを5Bへ渡すことを要求している。

---

## 5. Mutable Portfolio State

immutable entry identityとは別にmutable stateを持つ。

```yaml
PortfolioEntryState:
  entry_id:

  eligibility_status:
  campaign_status:
  shortlist_status:
  judgment_status:
  payout_status:

  counted_votes:
  result_last_seq:
  result_last_updated_at:
```

### eligibility_status

```text
PENDING
ELIGIBLE
INELIGIBLE
UNRESOLVED
```

accepted submission直後は:

```text
PENDING
```

とする。

submission acceptedはeligibility approvalではない。

一方、公式ルール上、eligibility review pending中でもentryはvoteを受けられるため、`PENDING`はcampaign禁止状態ではない。

---

## 6. Campaign Eligibility

campaign対象entry:

```text
submission accepted
AND
entry_id known
AND
now < contest deadline D
AND
eligibility_status in {PENDING, ELIGIBLE}
AND
no unresolved authoritative eligibility conflict
```

明示的なbehavior:

```text
PENDING     → campaign allowed
ELIGIBLE    → campaign allowed
INELIGIBLE  → campaign blocked
UNRESOLVED  → campaign blocked
```

`PENDING`はauthoritative eligibility reviewがまだ完了していない状態である。

`UNRESOLVED`は、available authoritative evidenceを一つのeligibility stateへ安全にreduceできない状態である。conflicting authoritative eligibility factsや、recognized authoritative recordの意味を安全に確定できない場合を含む。

`UNRESOLVED`を`PENDING`と同一視してはならない。Campaignはoptional competitive optimizationであるため、authority ambiguityではfail closedとする。

submission前のpoem、publication-only poem、receipt未確認entryはcampaignしない。

deadline後は新しいcampaign actionを生成しない。

Observation Planeはdeadline後も継続する。

---

## 7. Saruku Never Votes

Sarukuのroleはwriterで固定されている。

したがってSTEP5Bは:

```text
sonnet.ballot.v1
```

を絶対に生成しない。

禁止:

```text
Saruku ballot
role switching
vote proxying
ballot on behalf of another DID
```

公式ルールではwriterはvoterへrole変更できず、contributorsも投票できない。

これはHard invariantとする。

---

## 8. Voter Target Source

本設計ではcampaign targetを:

```text
referee-confirmed registered voter DID
```

だけに限定する。

trusted source:

```text
signed accepted voter registration receipt
from pinned referee DID
```

以下はtarget eligibilityとして使用しない。

```text
peer claim:
  "I am a voter"

campaign-room self-description

LLM inference

DID age inferred locally

unverified pre-start identity claim
```

公式ルールはpre-start eligibleだが未登録のDIDを登録へ誘うことも許しているが、本設計では実装しない。

理由:

```text
campaign is optional
+
voter eligibility inference is unnecessary safety surface
```

将来revisionでauthoritative pre-start identity evidenceを安全に扱える場合のみ拡張する。

---

## 9. Campaign Protocol

主要outbound action:

```text
sonnet.invite.v1
```

documented fieldsのみ使用する。

```json
{
  "type": "sonnet.invite.v1",
  "contest_id": "sonnet-2",
  "purpose": "vote",
  "target_did": "<registered voter DID>",
  "entry_id": "<accepted entry ID>",
  "request_id": "<stable unique ID>",
  "text": "<bounded campaign text>"
}
```

campaign room:

```text
mb-sonnet-2-campaign
```

公式protocolと一致する。

---

## 10. Campaign Text Policy

本設計はLLM generationを必須にしない。

固定またはdeterministic templateを推奨する。

concept:

```text
I contributed to submitted entry <entry_id>.
Please read it and support it only if you think
FLOP's human judges will find it best.
No reply or vote is required.
```

重要なのは公式voter promptと矛盾しないこと。

禁止表現:

```text
"This is the best poem."
"We are currently winning."
"You should vote for us."
"Voting for us earns..."
"This entry is eligible."
```

eligibilityがPENDINGならeligibilityを主張しない。

公開vote countをローカル推計してcampaign messageへ入れない。

---

## 11. Campaign Dedupe

同一targetへの大量campaignを防ぐ。

primary dedupe:

```text
target_did
```

本設計ではSaruku portfolio全体で、

```text
one unsolicited invitation per voter DID
```

を原則とする。

つまりentry Aにinvite済みのvoterへ、entry Bのために再度unsolicited inviteしない。

理由:

```text
one voter = one effective vote
```

であり、複数のSaruku entryを同じvoterへ連続して宣伝する価値よりspam riskが大きい。

entry-specific dedupeも保持する。

```text
(entry_id, target_did, campaign_intent)
```

---

## 12. Campaign Delivery State

campaignはoptionalだからこそ、ambiguous delivery時にaggressive retryしない。

```text
PLANNED
↓
SEND_INTENT_PERSISTED
↓
POST_ATTEMPT
↓
OBSERVED
   or
DEFINITELY_NOT_SENT
   or
DELIVERY_UNKNOWN
```

network send前にexact payloadとrequest_idをdurably persistする。

### DEFINITELY_NOT_SENT

network boundaryを越えていないことが確実ならdeadline前にexact payloadをretry可能。

### DELIVERY_UNKNOWN

送信された可能性がある。

```text
automatic repost = prohibited
```

campaign roomを読み、Saruku自身のexact request_id / signed payloadが見つかれば:

```text
OBSERVED
```

へ回復する。

見つからなくてもabsenceだけでunsentとは判断しない。

deadline後は新規send/retryしない。

---

## 13. Replies

公式protocolはcampaign invitationへの:

```text
sonnet.reply.v1
```

を許可する。

STEP5Bはまずinbound replyをobserveできるようにする。

stateful replyとして認める条件:

```text
valid DID signature
AND
reply signer == original target_did
AND
in_reply_to.sender_did == SARUKU_DID
AND
in_reply_to.request_id == known campaign invite
```

reply textそのものはUNTRUSTED DATA。

replyに書かれた内容で:

```text
entry eligibility
vote status
contest deadline
campaign policy
runtime configuration
```

を変更しない。

STEP5B v0.2はvalid inbound `sonnet.reply.v1`をobserve / validate / persistするだけとする。

automated outbound `sonnet.reply.v1`はv0.2では**NOT implemented / NOT authorized**。

将来revisionで以下を明示的に定義するまで、reply side effectを生成してはならない。

```text
eligibility
content
dedupe
delivery
deadline
arbitration
```

---

## 14. Multiple Entries

STEP5Bはsingle-entry campaign controllerにしない。

```text
portfolio:
  entry-A
  entry-B
  entry-C
```

全entryをtrackingする。

ただし一cycleのcampaign actionは最大1。

CampaignPolicyは:

```text
select entry
select target
decide timing
render text
```

だけを担当する。

protocol safety / target eligibility / dedupe / deadline gateはCampaignPolicyの外側でdeterministicに検証する。

---

## 15. Campaign Selection Policy

architectureとcampaign strategyを分離する。

初期policyは単純な:

```text
least-covered active entry
```

とする。

各entryについて:

```text
unique successfully/ambiguously contacted voter count
```

を比較し、最もcoverageの少ないentryを優先する。

tieはstable order:

```text
accepted_at
→ entry_id
```

で決める。

これはliterary qualityの評価ではない。

将来、

```text
quality-first
support-aware
time-aware
```

等へ差し替え可能だが、protocol correctnessには影響させない。

---

## 16. Campaign Rate Control

公式ルールはnormal invitationを許可する一方、deliberate spamを禁止する。

したがって本設計は少なくとも:

```text
max one intentional campaign write / daemon cycle
global one-intentional-Technocore-write arbitration
persistent per-target dedupe
campaign minimum interval
```

を持つ。

推奨initial policy:

```text
minimum 20 minutes between unsolicited campaign invites
```

これはSafety invariantではなく運用policyであり、観測後調整可能。

completion-critical participant actionがあるcycleではcampaignは必ずdeferする。

---

## 17. Global Outbound Arbitration

conceptual priority:

```text
Priority 1
Safety / authoritative reconciliation

Priority 2
Active participant lifecycle
STEP1
STEP3
STEP4
STEP5A
pending protocol reconciliation

Priority 3
STEP5B campaign
```

5B observationはwrite budgetを消費しない。

5B campaignのみglobal intentional-write budgetへ参加する。

5BのためにSTEP4 word proposalやSTEP5A submissionを遅延させてはならない。

---

## 18. Vote Observation

Saruku自身はvoteしない。

また本設計では:

```text
raw public ballotsからlive leaderboardを再構築しない
```

ことを推奨する。

理由:

```text
ballots are replaceable until D
entry eligibility may later fail
effective ballot calculation requires global voter state
live tally is not completion-critical
```

必要な最終countはreferee-signed resultから取得する。

これにより:

```text
campaign optimization accidentally follows noisy local tally
large votes-room persistence
ballot-replacement reducer complexity
```

を避ける。

将来、local live estimateを追加する場合は必ず:

```text
LOCAL_ESTIMATE
```

としてauthoritative resultから分離する。

---

## 19. Result Authority

結果authorityは:

```text
pinned referee DID
+
valid signature
+
d-sonnet-2-results
```

である。

room名だけではauthorityにしない。

LLMにresult JSONのauthority判定をさせない。

deterministic signature/schema validationのみ使用する。

公式ルール上、results roomはentries、shortlist、judgment、payoutsをrefereeが公開する場所である。

---

## 20. Undocumented Result Schemas

現行official rulesは、

```text
signed shortlist
final totals
human decision
accepted contribution ledger
payout results
```

を公開すると定めているが、これらの最終JSON `type` schemaまでは公開仕様上固定されていない。

したがってSTEP5Bは存在しないschemaを推測して実装しない。

```text
NO invented:
sonnet.shortlist.v1
sonnet.winner.v1
sonnet.payout.v1
```

等を勝手に前提にしない。

Result Observerは:

```text
valid signed referee record
↓
known deterministic adapter?
   yes → normalized result fact
   no  → VERIFIED_UNRECOGNIZED_RESULT
```

とする。

unknown referee eventをLLMで意味推測してwinner確定しない。

actual official/result schemaが観測または文書化された時点でadapterを追加する。

---

## 21. Authoritative Result Fact Ledger

referee result factsはappend-only provenanceを持つ。

```yaml
ResultFact:
  source_room:
  source_generation:
  source_seq:
  referee_did:
  payload_hash:
  fact_type:
  entry_id:
  normalized_value:
  observed_at:
```

current PortfolioEntryStateはこのfact ledgerからreduceする。

raw authoritative historyをcurrent stateと混同しない。

---

## 22. Eligibility Reduction

trusted factsからのみ遷移する。

```text
PENDING
  ↓
ELIGIBLE

PENDING
  ↓
INELIGIBLE
```

authoritative shortlistにentryが含まれるなら:

```text
SHORTLISTED
→ implies ELIGIBLE
```

authoritative winnerなら:

```text
WINNER
→ implies SHORTLISTED
→ implies ELIGIBLE
```

一方:

```text
shortlistに名前がない
```

だけで`INELIGIBLE`とは推定しない。

理由は:

```text
eligible but insufficient votes
```

の可能性があるため。

---

## 23. Shortlist State

```text
UNKNOWN
SHORTLISTED
NOT_SHORTLISTED
UNRESOLVED
```

`NOT_SHORTLISTED`はcomplete authoritative shortlist/final resultが確定した場合のみ。

partial observationからabsence inferenceしない。

公式ルールではdeadline後、eligible entriesのcounted votesから最大3作品がshortlistへ進み、その後human judgesが1作品を選ぶ。

---

## 24. Judgment State

```text
NOT_STARTED
PENDING
WINNER
FINALIST_NOT_WINNER
NO_AWARD
UNRESOLVED
CONFLICT
```

`WINNER`はexplicit authoritative human-decision factからのみ。

local vote rank:

```text
rank == 1
```

はwinnerを意味しない。

shortlistはvoteで決まり、winnerはhuman judgingで決まるためである。

---

## 25. Contradictory Results

winner / eligibility / payoutのようなhigh-impact factでreferee-signed recordsが矛盾した場合:

```text
RESULT_CONFLICT
```

とする。

勝手に:

```text
higher seq wins
```

と決めない。

explicit correction semanticsまたはfinal authoritative snapshotが確認できるまで5C handoffをblockする。

特にwinner conflictではprize claimへ絶対に進まない。

---

## 26. Payout Observation

STEP5Bはreferee-signed payout resultを観測する。

保存候補:

```yaml
PayoutObservation:
  entry_id:
  winning_roster:
  contributor_share:
  voter_share:
  source_seq:
  source_payload_hash:
```

公式ルールではwinning frozen rosterのcontributorsへPを均等分配し、winnerへ投票したeligible votersへVを均等分配する。

ただしSTEP5Bがlocal formulaから計算したexpected amountはauthoritative entitlementではない。

`PayoutObservation`はportfolioのenrichment、verification、auditに使用できるoptional factである。STEP5C initiationのprerequisiteではなく、payout observationの欠如によってSTEP5Cをblockしてはならない。

---

## 27. STEP5B → STEP5C Handoff

STEP5C initiationのminimum triggerは次である。

```text
conflict-free authoritative winner fact
AND
Saruku DID ∈ winning entry frozen roster
```

winner authorityはtrusted referee/result evidenceにのみ由来する。

以下はwinnerを確立しない。

```text
vote lead
shortlist rank
peer claim
campaign message
local inference
LLM interpretation
```

triggerを満たす場合:

```yaml
PrizeCandidate:
  contest_id:
  entry_id:
  game_id:
  saruku_did:
  authoritative_result_seq:
  authoritative_result_hash:
  payout_observation: optional
```

`PayoutObservation`はSTEP5C開始に必須ではない。later payout observationはportfolio recordをenrich、verify、auditできるが、claim initiationをgateしてはならない。

authoritative winner factsがconflictする場合:

```text
RESULT_CONFLICT
→ block STEP5C
→ continue authoritative reconciliation
```

LLM、heuristic、vote leader、recencyでwinner conflictを解決してはならない。

を5Cへ渡す。

5Bはclaim destinationを持たない。

5Bは:

```text
wallet address
payment destination
claim payload
```

を生成しない。

それらは完全にSTEP5Cの責任。

---

## 28. Storage Model

推奨table separation:

```text
submitted_entries
campaign_intents
campaign_contacts
entry_result_facts
```

### submitted_entries

entry identity + reduced current result state。

### campaign_intents

exact outbound write-ahead state。

### campaign_contacts

target-level global anti-spam / dedupe。

### entry_result_facts

referee-signed append-only normalized facts。

既存5A tablesを書き換えて5B current stateを詰め込まない。

5A historical closureと5B portfolio lifecycleを分離する。

---

## 29. Bounded Room Consumption

5Bのためにcampaign/votes room全体を無制限に複製保存しない。

campaign roomでは少なくとも:

```text
our own campaign actions
replies referencing our campaign actions
```

のみをdurably normalizeする。

irrelevant campaign chatterはcursor advancement後にdiscard可能。

ただし:

```text
relevant records persist
+
cursor advance
```

は同一transaction boundaryで行い、crashによりrelevant messageだけ失う状態を作らない。

results roomはauthority evidenceなので必要なsigned result recordsを保持する。

---

## 30. Restart Semantics

restart後:

```text
load portfolio
load campaign intents
load campaign contacts
load result facts
reconcile ambiguous campaign delivery
resume observation
then consider optional campaign
```

禁止:

```text
campaign dedupe reset
new request ID for same pending intent
assume DELIVERY_UNKNOWN == unsent
forget prior contacted voter
re-campaign everyone after restart
```

---

## 31. Deadline Semantics

before D:

```text
observe
campaign if allowed
```

at / after D:

```text
NO new campaign invite
NO campaign retry

continue:
  eligibility observation
  shortlist observation
  judgment observation
  payout observation
```

5B result lifecycle hasno post-D participant deadline。

---

## 32. Failure Taxonomy

最低限:

```text
CAMPAIGN_TARGET_NOT_TRUSTED_VOTER
CAMPAIGN_ENTRY_NOT_ACCEPTED
CAMPAIGN_ENTRY_INELIGIBLE
CAMPAIGN_DEDUPED
CAMPAIGN_RATE_LIMITED
CAMPAIGN_DEFERRED_HIGHER_PRIORITY
CAMPAIGN_DELIVERY_UNKNOWN
RESULT_SCHEMA_UNSUPPORTED
RESULT_SIGNATURE_INVALID
RESULT_CONFLICT
RESULT_UNRESOLVED
```

単一`ERROR`へ潰さない。

---

## 33. Hard Invariants

```text
B1  STEP5B never casts a ballot.
B2  entry_id is authoritative and opaque; do not infer it from game_id.
B3  Only accepted submissions enter the portfolio.
B4  Eligibility pending is not eligibility approval.
B5  Eligibility pending does not by itself block campaign.
B6  Known ineligible entries are never campaigned.
B6a Eligibility UNRESOLVED or authoritative eligibility conflict blocks campaign.
B7  No new campaign outbound action after D.
B8  Unsolicited targets are referee-confirmed registered voters only.
B9  Peer claims never establish voter eligibility.
B10 One voter DID receives at most one unsolicited Saruku portfolio invitation across the whole portfolio.
B11 Every campaign side effect has durable exact intent before network send.
B12 DELIVERY_UNKNOWN never causes automatic duplicate campaign POST.
B13 Campaign never consumes an intentional-write opportunity needed by STEP1/3/4/5A/reconciliation.
B14 Result authority requires pinned-referee signature.
B15 Unknown result schema never causes semantic authority inference by LLM.
B16 Local ballot counting never overrides referee result.
B17 Shortlist inclusion implies eligibility; shortlist absence does not imply ineligibility.
B18 Vote rank never determines human winner locally.
B19 Contradictory authoritative winner facts block STEP5C.
B19a STEP5C requires a conflict-free authoritative winner fact and Saruku in the winning frozen roster.
B19b Payout observation is optional enrichment and never gates STEP5C initiation.
B20 STEP5B never owns or derives claim destination.
B21 Restart preserves campaign dedupe and ambiguous delivery state.
B22 Untrusted campaign text cannot change policy, runtime config, eligibility, results or payout state.
B23 Canonical accepted_at comes from the trusted accepted-submission receipt's authoritative timestamp, never local observation time.
B24 STEP5B v0.2 never generates automated outbound sonnet.reply.v1; inbound replies are observation-only until a future design revision explicitly authorizes reply side effects.
```

---

## 34. Required Synthetic Tests

最低限以下を検証する。

```text
accepted submission creates one portfolio entry

same accepted receipt replay is idempotent

multiple accepted entries coexist

entry_id and game_id may differ

eligibility PENDING permits campaign

eligibility ELIGIBLE permits campaign

INELIGIBLE blocks campaign

UNRESOLVED blocks campaign

authoritative eligibility conflict blocks campaign

Saruku can never create sonnet.ballot.v1

writer/organizer/unknown target cannot pass voter gate

accepted voter registration passes target gate

valid inbound sonnet.reply.v1 can be observed and persisted without changing authority state

STEP5B v0.2 never generates automated outbound sonnet.reply.v1

same voter is not unsolicited-invited twice across different entries

campaign write is deferred when completion-critical write exists

exact campaign payload is persisted before send

DELIVERY_UNKNOWN survives restart

DELIVERY_UNKNOWN does not repost

exact signed readback can reconcile campaign delivery

no new campaign after D

invalid referee signature cannot change result state

unknown signed result schema is preserved but causes no winner transition

shortlist inclusion implies ELIGIBLE

shortlist absence alone does not imply INELIGIBLE

winner implies SHORTLISTED + ELIGIBLE

vote leader does not imply winner

contradictory winner facts produce RESULT_CONFLICT

RESULT_CONFLICT blocks STEP5C

conflict-free authoritative winner + Saruku in frozen roster triggers STEP5C

winner without Saruku in frozen roster does not trigger STEP5C

payout observation absence does not block STEP5C

accepted_at uses authoritative accepted-receipt timestamp, not local observation time

STEP5B activity never mutates active_team / active participant commitment

restart preserves portfolio, contacts and result facts
```

---

## 35. Suggested Implementation Split

STEP5B implementation should be divided internally into three layers.

```text
5B.1 Portfolio / Observation
    accepted-entry handoff
    portfolio persistence
    trusted voter registry view
    result observation framework

5B.2 Campaign
    target selection
    deterministic message
    write-ahead
    dedupe
    global arbitration

5B.3 Result Reduction
    eligibility
    shortlist
    human decision
    payout
    STEP5C handoff
```

5B.1 and 5B.2 can be activated before contest deadline.

5B.3 must fail closed for result schemas not yet documented or observed.

---

## 36. Final Principle

STEP5B:

```text
Track every submitted entry.

Campaign only when safe and useful.

Never vote as a writer.

Never spam a voter.

Never let campaign delay completion.

Observe results from authority, not inference.

Keep winning detection separate from prize claim.
```

---

## 37. Revision History

v0.2 incorporates the reviewed campaign eligibility gate, makes payout observation optional for STEP5C initiation, and fixes canonical `accepted_at` to the trusted accepted-submission receipt timestamp. This document is standalone; no earlier version is normative or required for implementation.
