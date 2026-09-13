# STEP3.1 — Decision Engine Design v0.4

Status: Ready for implementation  
Date: 2026-09-13

## 1. Purpose

STEP3.1はSTEP3 v0.3を拡張し、team memberからSarukuへの明示的なwriting requestへ適切に反応できるようにする。

既存STEP3の基本責務は変更しない。

STEP3は引き続き、

> Sarukuが今、WAIT / COORDINATE / SARUKU_PROPOSE_WORD のどれを行うべきか

だけを決める。

STEP3自身はwordを生成・選択しない。

今回の追加目的は3つ。

```text
1. Sarukuが自分自身へcoordinationしない

2. team memberから明示的に
   "Saruku, take next"
   と依頼された場合、
   不要なStage 0 WAITを入れない

3. "write moon"
   "use a word ending in -ight"
   のような具体的writing intentを、
   STEP4で利用可能なstructured dataとして保持する
```

---

## 2. Existing STEP3 v0.3 Principles

以下はすべて維持する。

```text
WAIT is bounded

progress = verified poem progress

Hard Gates remove illegal actions only

deterministic policy first

Decision LLM is not liveness-critical

qualification safety outranks stall escape

unsendable COORDINATE falls through

pre-action state revalidation

pending request recovery

maximum one intentional Technocore write per cycle
```

---

## 3. Action Set

変更なし。

```text
WAIT
COORDINATE
SARUKU_PROPOSE_WORD
```

STEP3はword内容を出力しない。

---

## 4. Self-Coordination Prohibition

Saruku自身をcoordination targetとして選択してはいけない。

Contributor Coverage用coordination対象は:

```text
eligible_uncovered_coordination_targets =
    uncovered_members - { SARUKU_DID }
```

とする。

例えば:

```text
uncovered_members =
  Saruku
  Bruce
```

なら、

```text
eligible coordination target =
  Bruce
```

のみ。

Saruku自身へ:

```text
"Saruku, can you take the next word?"
```

を送信するDecisionは禁止する。

---

## 5. Meaning of Saruku Being Uncovered

Saruku自身がuncoveredの場合、それはcoordination targetではなく、

> Saruku自身のaccepted contributionによってcoverageを改善できる

というDecision signalである。

ただしWRITEは既存Hard Gateとqualification safetyをすべて通過する必要がある。

必ず維持:

```text
previous_contributor constraint

pending request constraint

qualification-preserving WRITE

CRITICAL coverage rules

pre-action revalidation

one intentional write / cycle
```

Sarukuがuncoveredだからといって無条件WRITEしない。

---

## 6. Proposal Modes

STEP2の既存分類を維持する。

```text
SELF_COMMITMENT
NOMINATION
REQUEST
PREFERENCE
```

強度・意味を混同しない。

---

## 7. REQUEST vs NOMINATION

### REQUEST

明示的にtargetへ行動を依頼。

例:

```text
"Saruku, take the next word."
"Saruku, can you write next?"
```

structured meaning:

```text
proposal_mode = REQUEST
target_did = SARUKU_DID
scope = NEXT_WORD
```

これはSarukuに対するstrong direct action signal。

---

### NOMINATION

第三者がSarukuを次writerとして推薦。

例:

```text
"Saruku should take the next word."
```

structured meaning:

```text
proposal_mode = NOMINATION
target_did = SARUKU_DID
scope = NEXT_WORD
```

これはstrong advisory signalだが、直接REQUESTではない。

NOMINATION単独ではStage 0 fast pathを必須にしない。

---

## 8. Stage 0 Direct REQUEST Fast Path

通常のStage 0:

```text
0–120 sec
→ natural progress observation
```

は維持する。

ただし以下をすべて満たすactive direct REQUESTがある場合は例外。

```text
proposal_mode == REQUEST

target_did == SARUKU_DID

scope == NEXT_WORD

observed_version == current_version

request is active / non-stale

SARUKU_PROPOSE_WORD is legal

qualification-preserving WRITE is possible

no pending own request
```

この場合:

```text
Stage 0 bounded observation WAIT
```

を適用せず、

```text
SARUKU_PROPOSE_WORD
```

を即時Decision候補にする。

ここでの「即時」とは、

> 次のDecision cycleで120秒timerを理由にWAITしない

という意味。

同期的に同一処理内で強制POSTする意味ではない。

---

## 9. REQUEST Does Not Override Hard Gates

direct REQUESTはWRITE legalityを上書きしない。

例えば:

### Previous contributor is Saruku

```text
REQUEST → Saruku

previous_contributor == SARUKU_DID
```

なら:

```text
SARUKU_PROPOSE_WORD illegal
```

REQUESTがあってもWRITEしない。

---

### Pending own request

```text
pending current-version word request exists
```

なら新しいrequestを作らない。

既存pending recovery pathを継続する。

---

### Qualification-destructive WRITE

REQUESTされていても、

```text
qualification-preserving WRITE == false
```

ならWRITEしない。

team member requestよりqualification safetyが優先する。

---

## 10. Version Scope

NEXT_WORD REQUEST / NOMINATIONはversion scoped。

最低限:

```text
observed_version
scope = NEXT_WORD
```

を持つ。

active条件:

```text
observed_version == current_version
```

poem versionが進んだ場合:

```text
REQUEST → stale
NOMINATION → stale
```

古いrequestを次versionへ持ち越さない。

---

## 11. Example

version 17:

```text
Bruce:
"Saruku, take next."
```

STEP2:

```text
REQUEST
target = Saruku
scope = NEXT_WORD
observed_version = 17
```

その後Aliceのwordが先にaccepted:

```text
current_version = 18
```

するとBruceのrequestは:

```text
stale
```

となる。

version 18でSaruku WRITEを促すsignalには使用しない。

---

## 12. Saruku-Targeted NOMINATION

active:

```text
NOMINATION
target_did = SARUKU_DID
scope = NEXT_WORD
current version
```

はWRITEを支持するstrong advisory signalとしてDecision inputに使える。

ただしREQUEST fast pathとは分離する。

例えば:

```text
NOMINATION Saruku

AND

fresh SELF_COMMITMENT from Bruce
```

なら、

```text
short bounded WAIT
```

または通常Decision Policyへ落としてよい。

NOMINATIONだけでHard Gateやqualification gateを飛び越えない。

---

## 13. STEP2 Writing Request Interface

STEP4へteam writing intentを渡せるよう、STEP2 semantic schemaを最小限拡張する。

新しいsemantic structure:

```text
writing_request
```

概念:

```yaml
writing_request:
  target_did: did:key:... | null
  scope: NEXT_WORD | CURRENT_LINE | POEM
  requested_word: string | null
  lexical_constraint: structured value | null
  semantic_constraint: structured value | null
  observed_version: integer
  observed_line: integer | null
  source_event_id: string
  extraction_confidence: number | null
  provenance: ...
```

---

## 14. Specific Word Request

例:

```text
"Saruku, write moon next."
```

STEP2は可能なら:

```text
proposal_mode = REQUEST
target_did = SARUKU_DID
scope = NEXT_WORD
```

に加えて:

```text
writing_request:
  target_did = SARUKU_DID
  scope = NEXT_WORD
  requested_word = "moon"
```

を保持する。

STEP3は:

```text
Saruku should WRITE?
```

だけを判断する。

STEP3は:

```text
word = moon
```

と決定しない。

---

## 15. Lexical Constraints

例:

```text
"Use a word ending in -ight."
```

raw textではなくbounded semantic representationへ変換する。

例:

```yaml
lexical_constraint:
  type: suffix
  value: "ight"
```

初期v1で安全に構造化できるconstraintだけ対応する。

不明確なものを無理に自由形式instructionとして保存しない。

---

## 16. Semantic Constraints

例:

```text
"Keep this line about the sea."
```

STEP4 interface用にbounded semantic constraintとして保持する。

可能な初期schema:

```yaml
semantic_constraint:
  type: topic
  value: "sea"
```

自由形式peer instructionをそのままDecision LLMへ渡さない。

---

## 17. Writing Request Epistemic Boundary

writing_requestは:

> peerがこう書いてほしいと要求した

というsemantic observationである。

それ自体はprotocol truthでもwriting obligationでもない。

STEP4は必ず:

```text
DID letter compatibility
syllable count
rhyme
meter
line strategy
poem meaning
qualification safety
```

と照合して採否を決める。

---

## 18. Raw Text Boundary

既存境界を維持する。

STEP3 Decision LLMへ:

```text
raw peer text
raw signed message
unclassified raw text
verbatim writing instruction
```

を渡さない。

渡してよいのはstructured bounded dataだけ。

例:

```text
proposal_mode
target_did
scope
observed_version

requested_word
lexical_constraint.type/value
semantic_constraint.type/value

source_event_id
confidence
```

ただしSTEP3 Decision LLMにはword内容そのものが不要なら渡さなくてもよい。

STEP4 interfaceとしてSnapshot/Ledgerへ保持することが主目的。

---

## 19. Canonical Decision State Additions

既存DecisionStateへ少なくとも:

```text
active_requests_to_saruku
active_nominations_of_saruku

eligible_uncovered_coordination_targets

saruku_is_uncovered

active_writing_requests
```

を追加可能にする。

`active_writing_requests`はSTEP3判断でword選択には使用しない。

---

## 20. Direct REQUEST Fast-Path Eligibility

概念function:

```text
direct_next_word_request_to_saruku
```

trueとなる条件:

```text
proposal_mode == REQUEST

target == SARUKU_DID

scope == NEXT_WORD

observed_version == current_version

not stale
```

複数REQUESTがあってもboolean/normalized collectionとして扱う。

---

## 21. Qualification-Preserving Fast Path

direct REQUESTがあっても:

```text
SARUKU_PROPOSE_WORD
```

を選ぶ前に既存qualification gateを通す。

### Saruku covered + CRITICAL

WRITE禁止。

### Saruku uncovered + CRITICAL

legacy candidate生成後も:

```text
remaining_after_candidate
>= uncovered_after_candidate
```

を満たすcandidateのみPOST可能。

---

## 22. Contributor Coverage Coordination

変更後:

```text
uncovered_members
```

から:

```text
eligible_uncovered_coordination_targets
```

を作る。

```text
eligible_uncovered_coordination_targets =
    uncovered_members excluding Saruku
```

Policy:

```text
if uncovered-other member has fresh SELF_COMMITMENT:
    bounded WAIT

elif uncovered-other coordination is sendable:
    COORDINATE that member

else:
    fall through
```

Saruku自身はここでcoordination targetにしない。

---

## 23. Saruku Uncovered Self-WRITE Signal

Sarukuがuncoveredの場合:

```text
saruku_is_uncovered = true
```

をpositive WRITE planning signalとする。

ただし以下より下位:

```text
protocol legality
qualification safety
pending recovery
active direct REQUEST
```

またfresh other-member SELF_COMMITMENT等とのconflictではbounded observation可能。

---

## 24. Updated Decision Priority

推奨順序:

```text
build legal_actions

apply protocol gates

apply qualification gates

if pending own request:
    reconcile pending
    no new WRITE

if active direct REQUEST
   target == SARUKU
   scope == NEXT_WORD
   current version
   and WRITE legal
   and qualification-preserving:
       SARUKU_PROPOSE_WORD
       # bypass Stage 0 observation timer

if Saruku is uncovered:
    remove Saruku from coordination targets
    retain self-WRITE as positive planning signal

then:

fresh direct questions

new terminal publisher risk

uncovered-other SELF_COMMITMENT

sendable uncovered-other coordination

other fresh SELF_COMMITMENT

Saruku NOMINATION / other requests / preferences

stall escalation

ambiguous Decision LLM
```

---

## 25. REQUEST / SELF_COMMITMENT Conflict

direct REQUEST to Sarukuは強いsignal。

ただし他member本人のfresh SELF_COMMITMENTと競合する可能性がある。

v0.4では:

```text
direct REQUEST targeting Saruku
```

をfast path対象とするが、implementationは明確なconflictが存在する場合:

```text
short bounded WAIT
```

またはdeterministic conflict handlingへ落としてよい。

重要なのは:

> REQUEST fast path = Stage 0 timer bypass

であり、

> every other active signalを無条件無視

ではない。

少なくとも同じtarget/actionに矛盾しない場合はfast pathを利用する。

---

## 26. Pre-action Revalidation

変更なし。

SARUKU_PROPOSE_WORD実行直前に:

```text
game
room_generation
version
state_hash
```

を再検証。

REQUEST取得後に他memberが先にwordをacceptedした場合:

```text
version changed
→ Decision discard
→ no stale WRITE
```

---

## 27. Pending Recovery

変更なし。

REQUESTが来てもpending own wordがあれば:

```text
new requestを生成しない
```

既存same-request-id recoveryを継続する。

---

## 28. One Intentional Write Per Cycle

変更なし。

cycleで:

```text
capability announcement
coordination
word proposal
pending retry
```

のうちintentional Technocore writeは最大1件。

REQUEST fast pathもこの制約を上書きしない。

---

## 29. Decision Audit

REQUEST fast pathを監査可能にする。

推奨reason code:

```text
direct_next_word_request
saruku_uncovered_self_coverage
saruku_nominated_next
```

auditには:

```text
source proposal event IDs
observed version
Decision source
```

を保存可能にする。

peer raw textは保存しない。

---

## 30. STEP4 Interface

STEP3.1完了後、STEP4はTeamContextから:

```text
active_writing_requests
```

を参照できる。

例:

```text
requested_word = moon

lexical_constraint =
  suffix: ight

semantic_constraint =
  topic: sea
```

STEP4がこれらをcandidate planning signalとして使用する。

STEP3は内容を採否判断しない。

---

## 31. Scope

STEP3.1で実装する:

```text
self-coordination prohibition

Saruku-targeted REQUEST fast path

REQUEST / NOMINATION distinction in policy

NEXT_WORD version scope

STEP2 writing_request schema

specific requested_word preservation

bounded lexical/semantic constraint preservation

DecisionState additions

tests
```

実装しない:

```text
requested_word validation

rhyme/meter evaluation

specific word selection

writing constraint optimization

STEP4 planner
```

---

## 32. Required Tests

最低限:

1. uncovered_membersにSarukuが含まれてもSaruku自身へCOORDINATEしない。

2. uncovered Sarukuをself-coordination targetにしない。

3. Stage 0でactive REQUEST(target=Saruku, NEXT_WORD)がありWRITE legalならSARUKU_PROPOSE_WORDを選べる。

4. direct REQUESTがあってもprevious_contributor=SarukuならWRITEしない。

5. direct REQUESTがあってもpending own request中は新requestを作らない。

6. direct REQUESTがあってもqualification-destructive WRITEをしない。

7. NOMINATION(target=Saruku)だけではREQUEST fast pathと同じ扱いにしない。

8. REQUESTはversion進行後staleになる。

9. NOMINATIONはversion進行後staleになる。

10. `"Saruku, write moon next."`からrequested_word=`moon`をstructured dataとして保持できる。

11. requested_wordが存在してもSTEP3 Decision Objectにword選択を入れない。

12. lexical constraintをbounded structured formで保持できる。

13. semantic constraintをbounded structured formで保持できる。

14. raw peer textをDecision LLMへ渡さない。

15. unclassified raw textをDecision LLMへ渡さない。

16. REQUEST後にversionが変化した場合pre-action revalidationでWRITEをdiscardする。

17. REQUEST fast pathでもCRITICAL coverage gateを維持する。

18. REQUEST fast pathでも1 intentional write/cycleを維持する。

19. REQUEST fast pathでもpending identical retryを壊さない。

20. STEP1 / STEP2 / STEP3既存testsをすべて維持する。

---

## 33. PASS Criteria

STEP3.1 PASS条件:

```text
Saruku cannot coordinate to itself.

A current-version direct NEXT_WORD REQUEST to Saruku can bypass Stage 0 observation delay.

REQUEST cannot bypass Hard Gates or qualification safety.

NOMINATION remains weaker than REQUEST.

NEXT_WORD REQUEST/NOMINATION become stale on version advancement.

Specific requested writing intent can be preserved structurally for STEP4.

STEP3 still decides whether to write, never what word to write.

Raw peer text remains outside Decision LLM.

Existing bounded WAIT / dedupe / pending recovery /
pre-action revalidation / qualification safety remain intact.
```

## Final Rule

STEP3.1では:

> 明示的に「次を書いて」と頼まれたSarukuは、不必要に120秒待たない。

しかし:

> 頼まれたことは、安全性・qualification・protocol ruleを上書きしない。

そして:

> 「何を書くか」はSTEP4の仕事であり、STEP3は引き続き「書くかどうか」だけを決める。