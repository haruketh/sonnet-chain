# STEP3 — Decision Engine Design v0.3

Status: Ready for implementation  
Date: 2026-09-12

## 1. Purpose

STEP3は、STEP2が生成したTeamContext Snapshotとverified contest stateを入力にして、Sarukuが**現在取るべき次の一手を1つだけ選択するDecision Engine**である。

STEP3は意味抽出をしない。

STEP3はwordそのものを生成しない。

```text
STEP2
Observe → Parse → Ledger → Snapshot

STEP3
Canonical Decision State
→ Hard Gates
→ Deterministic Policy
→ Ambiguous only: LLM
→ Deterministic Fallback
→ Decision Object
→ Pre-action Revalidation

STEP4
Decision = SARUKU_PROPOSE_WORD
→ Compose → Validate
```

STEP3の責務は、

> 今は待つべきか、調整すべきか、自分がwordを提案すべきか

を、現在のverified stateに対して判断することである。

未来の複数turnを固定するplannerではない。

---

## 2. Core Principles

STEP3は以下を中核原則とする。

### 2.1 WAIT is an action, not a terminal state

WAITは正常なDecisionである。

ただし、すべてのWAITには必ず出口が必要。

```text
WAIT
  ↓
reconsider_at OR verified material state change
  ↓
OBSERVE
  ↓
DECIDE AGAIN
```

無期限WAITは禁止する。

---

### 2.2 Progress is poem progress, not room activity

stall timerをリセットするのは、verified poem progressだけ。

progressとして認める:

```text
roster_ready
accepted word / poem version advancement
```

progressとして認めない:

```text
team-room message
proposal
question
claim
coordination
Saruku announcement
rejected word
LLM call
room chatter
```

他DIDが喋り続けていても、poem versionが進まなければstallは進行する。

---

### 2.3 Hard Gates remove illegal actions only

Hard Gateはaction候補を除外する。

Hard Gate自身が最終Decisionを決めない。

例:

```text
previous_contributor == SARUKU_DID
```

なら、

```text
remove SARUKU_PROPOSE_WORD
```

のみ。

WAITとCOORDINATEは残る。

---

### 2.4 Deterministic policy first

明確なケースはdeterministicに判断する。

LLMを必要とするのは曖昧な中間ケースだけ。

```text
Hard Gates
↓
Deterministic Policy
↓
clear case?
├─ yes → Decision
└─ no
     ↓
    LLM
     ↓
    valid?
    ├─ yes → Decision
    └─ no / failure
         ↓
       Deterministic Fallback
```

Decision LLMをliveness-critical pathに置かない。

---

### 2.5 Qualification safety outranks stall escape

「止まらないためにSarukuが書く」ことが、team qualificationを数学的に不可能にしてはならない。

優先順位:

```text
protocol legality
→ qualification safety
→ liveness progress at STAGE_2
→ team coordination
→ literary quality
```

通常時はcoordinationを優先できる。

ただし`STAGE_2_PROGRESS`では、qualification-preserving progressが合法ならpeer chatterよりaccepted poem progressを優先する。

---

### 2.6 Unsendable COORDINATE is not an action

semantic dedupe等により送信不能なCOORDINATEは、Decision Policy上も最終Decisionにしてはいけない。

Executorで黙って捨てるだけでは不十分。

---

## 3. Outer Loop

全体loop:

```text
OBSERVE
  ↓
STEP2 Team Intelligence
  ↓
Build Canonical Decision State
  ↓
HARD GATES
  ↓
DETERMINISTIC POLICY
  │
  ├─ clear case → Decision
  │
  └─ ambiguous
        ↓
       Decision LLM
        │
        ├─ valid → Decision
        └─ failure
             ↓
        Deterministic Fallback
  ↓
DECISION OBJECT
  ↓
PRE-ACTION REVALIDATE
  │
  ├─ state changed → discard → OBSERVE
  │
  └─ unchanged
        ↓
EXECUTE max one intentional write
  ↓
OBSERVE
```

action実行後は必ず再観測する。

---

## 4. Action Set

STEP3 v0.3のactionは3種類。

```text
WAIT
COORDINATE
SARUKU_PROPOSE_WORD
```

### 4.1 WAIT

TechnocoreへPOSTしない。

必須:

```text
wait_reason
wait_started_at
reconsider_at
```

`reconsider_at`なしのWAITは禁止。

---

### 4.2 COORDINATE

team roomへplanning messageを1件POSTする。

目的:

```text
ask
clarify
warn
request
acknowledge
propose
```

例:

```text
Bruce, can you take the next word?
```

```text
We still need an accepted contribution from DID-X.
```

```text
We should avoid leaving the final contribution to DID-Y unless X publishing is available.
```

COORDINATEはword proposalではない。

---

### 4.3 SARUKU_PROPOSE_WORD

既存legacy writer、将来のSTEP4 Writing Plannerへ処理を渡す。

STEP3自身はwordを生成しない。

---

## 5. Canonical Decision State

STEP3はraw runtime stateやpeer raw textをそのままDecision LLMへ渡さない。

まずCanonical Decision Stateを構築する。

概念:

```text
DecisionState

game_id
poem_room
room_generation

current_version
current_state_hash
current_line
current_line_syllables

last_progress_at
stall_age_seconds
escalation_stage

previous_contributor
pending_word_request

roster
members

uncovered_members
uncovered_count

remaining_syllables_total
coverage_slack
coverage_pressure

active_self_commitments
active_nominations
active_requests
active_preferences

terminal_publish_risks
relevant_open_questions

last_coordination
coordination_sendability

legal_actions
```

deterministic contest stateとSTEP2 Snapshotが矛盾した場合、deterministic contest stateを優先する。

---

## 6. Progress Clock

永続state:

```text
poem_last_progress_at
```

### 6.1 Initialize

`roster_ready` receipt受理時に初期化する。

### 6.2 Update

accepted wordによってpoem versionが前進したときだけ更新する。

可能ならtrusted referee receiptのtimestampを使用。

利用できなければlocal receipt processing UTC timestampを使う。

### 6.3 Do not update from

```text
room message
claim
proposal
question
coordination
rejected word
pending request
LLM activity
```

### 6.4 Stall age

```text
stall_age_seconds =
    now - poem_last_progress_at
```

再起動後も維持する。

---

## 7. Escalation Stages

local liveness heuristicとして明示的stageを持つ。

初期defaults:

```text
STAGE_0_OBSERVE    0–120 sec
STAGE_1_COORDINATE 120–300 sec
STAGE_2_PROGRESS   >=300 sec
```

公式turn timerではない。

定数化する。

### STAGE_0_OBSERVE

自然な進行を短時間待つ。

SELF_COMMITMENTがあればbounded WAITが適切。

### STAGE_1_COORDINATE

必要なcoordinationを行う。

特に:

```text
uncovered contributor
direct question
terminal publisher risk
```

### STAGE_2_PROGRESS

verified poem progressが300秒以上止まっている状態。

このstageでは、qualification-preservingなSaruku WRITEが合法なら、peer chatter・direct question・新しいSELF_COMMITMENTよりも**accepted poem progressを優先する**。

他DIDが発言し続けていることだけを理由にSTALLを延長してはいけない。

---

## 8. STEP2 Proposal Strength Extension

STEP3実装前にSTEP2 proposal schemaへ最小拡張を行う。

```text
proposal_mode:
  SELF_COMMITMENT
  NOMINATION
  REQUEST
  PREFERENCE
```

### SELF_COMMITMENT

verified speaker本人が自分の行動を約束。

```text
"I'll take the next word."
"I can write next."
```

最も強いWAIT signal。

ただしSTAGE_2_PROGRESSではqualification-preserving WRITEを永久にstarveする根拠にはならない。

---

### NOMINATION

第三者が別memberを推薦。

```text
"Bruce should take the next word."
```

Bruce本人のcommitmentではない。

---

### REQUEST

他者への依頼。

```text
"Bruce, can you take next?"
```

---

### PREFERENCE

弱い希望。

```text
"I'd prefer Bruce next."
```

---

## 9. Multiple Commitments

複数memberがSELF_COMMITMENTしていても、それ自体をdeadlockとみなさない。

ルール上、最初にacceptedされたvalid wordが進行を決める。

STAGE_0 / STAGE_1では:

```text
multiple SELF_COMMITMENTS
→ short bounded WAIT
→ observe accepted state
```

を基本とする。

STAGE_2_PROGRESSでは、qualification-preserving Saruku WRITEが合法なら、繰り返されるSELF_COMMITMENTによってprogressを永久に延期しない。

---

## 10. Hard Gates

初期候補:

```text
legal_actions = {
  WAIT,
  COORDINATE,
  SARUKU_PROPOSE_WORD
}
```

### Remove SARUKU_PROPOSE_WORD if

```text
not WRITING
deadline passed
poem complete
state_hash missing
previous_contributor == SARUKU_DID
pending own word request exists
```

またqualification safety gateによってもWRITEを除外する。

Hard GateはCOORDINATEを不必要に除外しない。

---

## 11. Contributor Coverage

全roster memberは最低1 accepted contributionを持つ必要がある。

STEP3は常に:

```text
uncovered_members =
  roster members with has_contributed == false
```

を把握する。

coverageは終盤だけのriskではなく、序盤から積極的に減らすqualification riskである。

---

## 12. Coverage Policy

STAGE_0 / STAGE_1の基本:

```text
uncovered exists

eligible uncovered SELF_COMMITMENT exists
→ bounded WAIT

no SELF_COMMITMENT
and sendable coordination opportunity exists
→ COORDINATE uncovered member

coordination already sent / unsendable
→ continue policy evaluation

stall continues
→ progress action only if qualification-preserving
```

STAGE_2_PROGRESSではSection 31のpolicy順序を優先する。

Sarukuだけが何度も書き続け、最後に未参加memberを残す挙動を避ける。

---

## 13. Coverage Capacity

deterministically計算:

```text
remaining_syllables_total
uncovered_count
coverage_slack =
    remaining_syllables_total - uncovered_count
```

各uncovered memberには最低1 accepted wordが必要なため、最低でも1 syllable capacityを必要とすると扱う。

---

## 14. Coverage Pressure

最低分類:

```text
coverage_slack < 0
→ IMPOSSIBLE

coverage_slack == 0
→ CRITICAL

small positive slack
→ HIGH

larger slack
→ ELEVATED / LOW
```

thresholdは定数化する。

### CRITICAL

```text
remaining_syllables_total == uncovered_count
```

remaining capacityにcoverage以外の自由度がない。

---

## 15. Qualification-Preserving WRITE Gate

### 15.1 Saruku already covered

以下の場合:

```text
coverage_pressure == CRITICAL
AND Saruku has_contributed == true
```

`SARUKU_PROPOSE_WORD`をlegal actionsから除外する。

例:

```text
remaining = 2 syllables
uncovered = Bruce, Alice
Saruku already covered
```

Sarukuが1 syllable消費すると:

```text
remaining = 1
uncovered = 2
```

となりqualification不可能。

stall timeoutはこのgateを上書きしない。

---

### 15.2 Saruku itself uncovered

Saruku自身がuncoveredならWRITEを一律禁止しない。

candidate wordのsyllable costを`S`とする。

before:

```text
remaining = R
uncovered = U
```

Saruku contribution後:

```text
remaining = R - S
uncovered = U - 1
```

必須条件:

```text
R - S >= U - 1
```

満たさないcandidateはPOST禁止。

---

## 16. Legacy Writer Boundary under CRITICAL Coverage

STEP4は未実装。

そのためCRITICAL coverage時にlegacy writerへ無条件に渡してはいけない。

### Saruku covered

WRITE禁止。

### Saruku uncovered

legacy writerでcandidate生成・通常validation後、POST前に追加qualification guardを通す。

確認:

```text
candidate syllable cost
remaining capacity after candidate
remaining uncovered members
```

qualification維持をdeterministically保証できない場合:

```text
POST nothing
→ reconsider / coordinate
```

これは文学品質ではなくqualification safety enforcement。

---

## 17. Final Contributor Risk

final contributorは事前固定しない。

STEP2に:

```text
terminal_publish_risk
```

があるmemberは、final contributor候補としてriskを持つ。

ただしCLAIM由来ならprotocol prohibitionではない。

Decision Policyのplanning signalとして扱う。

Sarukuもfinal contributor候補の一人にすぎない。

---

## 18. Coordination Sendability

semantic coordination dedupeはExecutorだけの責務にしない。

Canonical Decision Stateは候補coordinationについて少なくとも:

```text
coordination_sendable
coordination_already_sent
```

を判断可能にする。

dedupe key概念:

```text
game_id
room_generation
version
target_did
coordination_intent
escalation_stage
```

---

## 19. Dedupe-aware Decision Fallthrough

送信不能なCOORDINATEを最終Decisionとして選ばない。

悪い例:

```text
uncovered exists
→ choose COORDINATE Bruce
→ executor suppresses duplicate
→ next cycle
→ choose same COORDINATE Bruce
→ suppress
→ forever
```

禁止。

正しくは:

```text
if coordination needed:
    if coordination_sendable:
        COORDINATE
    else:
        continue evaluating later branches
```

semantic dedupeはDecision Policyにも反映する。

---

## 20. Material Coordination Opportunity

same versionでも以下は新しいcoordination opportunityになり得る。

```text
target changed
intent changed
escalation stage changed
new direct question
new terminal risk
coverage pressure changed
new SELF_COMMITMENT
previous SELF_COMMITMENT expired
```

以下ならduplicate:

```text
same target
same intent
same stage
no material change
```

---

## 21. STAGE_2 Progress Precedence

`STAGE_2_PROGRESS`では、accepted poem progressをpeer activityより優先する。

Hard Gateとqualification gateを通過した後、pending own requestがなければ、まず以下を評価する。

```text
if stage == STAGE_2_PROGRESS:

    if qualification-preserving SARUKU_PROPOSE_WORD is legal:
        SARUKU_PROPOSE_WORD
        return
```

この判定は以下より先に行う。

```text
direct question
terminal publisher risk coordination
uncovered-member coordination
fresh SELF_COMMITMENT
multiple SELF_COMMITMENTS
other peer chatter
```

つまり、

> 5分以上verified poem progressがないなら、他DIDの発言よりaccepted wordを前へ進めることを優先する。

他DIDが毎cycle新しいSELF_COMMITMENTを発言しても、poem versionが進んでいない限りqualification-preserving WRITEを永久にstarveさせない。

WRITEがprotocol / qualification理由で合法でない場合のみ、coordination / WAIT branchesへ進む。

---

## 22. WAIT Semantics

WAIT Decision:

```json
{
  "action": "WAIT",
  "reason_code": "teammate_self_committed",
  "wait_started_at": "...",
  "reconsider_at": "...",
  "decision_source": "deterministic"
}
```

`reconsider_at`経過後は、同じ非progress activityが続いていても再判断する。

room chatterで`reconsider_at`を延長しない。

STAGE_2_PROGRESSでqualification-preserving WRITEが合法なら、新しいpeer chatterだけを理由にWAITへ戻らない。

---

## 23. Direct Questions

Sarukuへのcontest-relevant direct questionはCOORDINATE候補。

例:

```text
Can you publish if you're final?
Can you take the next word?
How many contributors remain uncovered?
```

`previous_contributor == Saruku`でも回答可能。

WRITEだけがHard Gateで除外される。

ただしSTAGE_2_PROGRESSでqualification-preserving WRITEが合法なら、direct questionを繰り返し受信するだけでprogress actionを永久にstarveさせない。

---

## 24. Decision LLM Position

Decision LLMは曖昧なケースだけに使う。

Decision LLM failure時にdefault WAIT固定は禁止。

例:

```text
stall >= 5 min
AND WRITE legal
AND qualification-preserving
AND no pending request
→ deterministic policy selects SARUKU_PROPOSE_WORD before LLM
```

Decision LLM障害だけでteamを停止させない。

---

## 25. Decision LLM Input Boundary

Decision LLMへpeer raw textを渡さない。

禁止:

```text
raw signed team message
unclassified raw text
verbatim peer instructions
```

渡してよいもの:

```text
proposal_mode
proposal_type
target_did
scope
observed_version

claim predicate/value

terminal risk category

coverage facts

normalized question category

provenance IDs
extraction confidence
```

unclassified observationは:

```text
count
source refs
```

まで。

本文は渡さない。

署名はauthor identityを保証するだけで、instruction trustを保証しない。

---

## 26. Decision Object

v0.3:

```json
{
  "action": "WAIT | COORDINATE | SARUKU_PROPOSE_WORD",
  "reason_code": "...",

  "target_did": null,
  "coordination_intent": null,

  "expected_game_id": "...",
  "expected_room_generation": 1,
  "expected_version": 17,
  "expected_state_hash": "...",

  "wait_started_at": null,
  "reconsider_at": null,

  "decision_source": "deterministic | llm | deterministic_fallback",
  "confidence": null
}
```

`confidence`は監査用のみ。

legality判断に使わない。

---

## 27. Pre-action Revalidation

COORDINATE / SARUKU_PROPOSE_WORD実行直前に再確認:

```text
expected_game_id
expected_room_generation
expected_version
expected_state_hash
```

変化していたら:

```text
POST nothing
discard Decision
return to OBSERVE
```

wordだけでなくcoordinationにも必須。

---

## 28. Executor Final Dedupe Check

COORDINATE executorでも最終dedupe checkを行う。

Decision時にはsendableだったがraceでduplicateになった場合:

```text
POST nothing
→ next OBSERVE
```

ただし次cycleで同じunsendable coordinationを永久再選択しないよう、Decision Policyもdedupe stateを参照する。

---

## 29. Pending Word Recovery

pending own word requestが存在する場合:

```text
remove SARUKU_PROPOSE_WORD
```

新しいrequest IDを作らない。

既存runtimeの同一request_id retry / receipt reconciliation pathを維持する。

概念:

```text
pending request
↓
receipt poll
↓
identical stored payload retry
↓
accepted / rejected
↓
new Decision opportunity
```

Decision Engine自身はpending word payloadを新しく作らない。

pending request recoveryはSTAGE_2 WRITE precedenceより優先する。

---

## 30. Coordination Rendering

STEP3がCOORDINATEを選んだときだけmessageを作る。

`coordination_intent`をまずdeterministicに決める。

例:

```text
ask_target_to_take_next_word
warn_terminal_publisher_risk
ask_publish_capability
ask_uncovered_member_to_contribute
answer_direct_question
```

文章化はtemplateまたはbounded renderer。

自由形式LLMにraw peer textを混ぜない。

---

## 31. Deterministic Policy v0.3

最終policy順序:

```text
build legal_actions

apply protocol hard gates

apply qualification-preserving WRITE gates

if pending own word:
    preserve/reconcile existing pending request
    do not create new word request
    return

if stage == STAGE_2_PROGRESS:

    if qualification-preserving SARUKU_PROPOSE_WORD is legal:
        SARUKU_PROPOSE_WORD
        return

    # WRITEがillegal / qualification-unsafeな場合のみ
    # coordination / WAIT policyへ進む

if unanswered direct relevant question:
    if response coordination is sendable:
        COORDINATE
        return
    else:
        continue

if newly material terminal publisher risk:
    if risk coordination is sendable:
        COORDINATE
        return
    else:
        continue

if uncovered members:

    if fresh eligible uncovered SELF_COMMITMENT:
        bounded WAIT
        return

    elif uncovered-member coordination is sendable:
        COORDINATE
        return

    else:
        continue policy evaluation

if fresh eligible SELF_COMMITMENT:
    bounded WAIT
    return

if multiple SELF_COMMITMENTS:
    short bounded WAIT
    return

if clear bounded no-op condition:
    WAIT with reconsider_at
    return

otherwise:
    ambiguous → Decision LLM

if LLM failure/invalid:
    deterministic fallback
```

重要:

```text
coordination_needed
!=
coordination_sendable
```

さらに:

```text
STAGE_2_PROGRESS
+ legal qualification-preserving WRITE
```

はpeer chatterより先に評価する。

---

## 32. Decision Audit

各Decisionをjournalへ保存。

event:

```text
team_decision
```

最低保存:

```text
game_id
room_generation
version
action
reason_code
target_did
coordination_intent
decision_source
wait_started_at
reconsider_at
escalation_stage
coverage_pressure
coordination_sendable
snapshot_id / ledger watermark
expected state hash fingerprint
```

peer raw textは記録しない。

---

## 33. Intentional Write Limit

1 daemon cycleあたりintentional Technocore write最大1件。

対象:

```text
capability announcement
coordination note
word proposal
```

同cycleで複数actionを書かない。

pending identical retryが発生するcycleでは別intentional writeを追加しない。

---

## 34. Integration

現在:

```text
WRITING
→ receipts
→ STEP2 sync
→ legacy writer
```

STEP3導入後:

```text
WRITING
→ receipts
→ STEP2 sync
→ TeamContext Snapshot
→ STEP3 Decision Engine

    WAIT
      → no write

    COORDINATE
      → revalidate
      → coordination POST

    SARUKU_PROPOSE_WORD
      → revalidate
      → legacy writer
      → qualification guard
      → word POST
```

STEP4導入前はexisting legacy writerを使用する。

---

## 35. STEP3 v0.3 Scope

実装する:

```text
Canonical Decision State
progress clock
stall/escalation stage
Hard Gates
proposal-mode handling
coverage tracking
coverage pressure
qualification-preserving WRITE gates
bounded WAIT
coordination sendability
dedupe-aware fallthrough
STAGE_2 progress precedence
Decision Object
deterministic policy
ambiguous-case LLM
deterministic fallback
pre-action revalidation
audit
legacy writer routing
```

実装しない:

```text
full poem planning
rhyme planning
meter optimization
multi-turn writer schedule
campaign/voting
global personality
long-term learning
```

---

## 36. PASS Criteria

STEP3 PASSには以下すべてを満たす。

1. previous contributorがSarukuでもCOORDINATE可能。
2. previous contributorがSarukuならWRITEは禁止。
3. pending own word時に新request IDを作らない。
4. pending request recovery pathを維持する。
5. stale Snapshot/proposalを使用しない。
6. SELF_COMMITMENTとNOMINATIONを区別する。
7. REQUESTとPREFERENCEも区別可能。
8. third-party nominationだけで長時間WAITしない。
9. multiple SELF_COMMITMENTをdeadlock扱いしない。
10. uncovered membersを序盤からdecision signalにする。
11. remaining syllablesをdeterministically算出する。
12. coverage pressureを算出する。
13. terminal publisher riskをDecision inputに使える。
14. Sarukuをfinal contributorへ固定しない。
15. same semantic coordinationを連投しない。
16. timeout/escalation stage transitionでsame versionでも新coordinationが可能。
17. WAITには必ず`reconsider_at`がある。
18. non-progress room activityでstall timerをリセットしない。
19. accepted poem progressでstall timerをリセットする。
20. Decision LLMへpeer raw textを渡さない。
21. unclassified raw textをDecision LLMへ渡さない。
22. Decision LLM failureで永久WAITしない。
23. deterministic fallbackでlegal progressを選べる。
24. invalid LLM actionをdeterministic validatorが拒否する。
25. SARUKU_PROPOSE_WORD時のみlegacy writerを呼ぶ。
26. STEP3自身はwordを生成しない。
27. direct relevant questionへ回答できる。
28. action直前にgame/generation/version/hashを再確認する。
29. stale coordinationをPOSTしない。
30. stale word actionをPOSTしない。
31. CRITICAL coverageかつSaruku already coveredならWRITEしない。
32. CRITICAL coverageでSaruku uncoveredの場合、candidate後もqualification capacityを維持する。
33. qualification preservationを保証できないCRITICAL WRITEをPOSTしない。
34. deduped COORDINATEがDecision loopを占有しない。
35. unsendable COORDINATEはlower-priority policyへfall throughする。
36. STAGE_2_PROGRESSではalready-sent coordinationよりqualification-preserving progressを優先できる。
37. Executorでduplicate suppressionされても同じunsendable coordinationを永久再選択しない。
38. Decision auditがrestart後も追跡可能。
39. STEP1/STEP2 testsを維持する。
40. 1 cycleのintentional Technocore writeを最大1件に維持する。
41. STAGE_2_PROGRESSではpeer chatter、direct questions、repeated/fresh SELF_COMMITMENTによってqualification-preserving progress actionが永久にstarveされない。

---

## Final Rule

STEP3とは、

> 現在のverified stateから合法かつqualification-preservingな候補を絞り、progress clockとteam intentを使って、出口付きの次の一手だけを選ぶDecision Engine

である。

そして:

> WAITはactionだがstateではない。すべてのWAITには出口がある。

> 送れないCOORDINATEはactionではない。

> livenessのためにqualificationを破壊してはならない。

> STAGE_2_PROGRESSでは、peerの発言よりverified poem progressを優先する。