# STEP2 — Team Intelligence Design v0.2

Status: Design ready for implementation  
Date: 2026-09-12

## 1. Purpose

STEP2の目的は、Sonnet team内で観測した署名済みroom historyを、安全に意味構造へ変換することである。

STEP2は判断・計画・word生成を行わない。

```text
STEP2
Observe → Verify → Parse → Ledger → Reduce → Snapshot

STEP3
Snapshot → Plan → Decide

STEP4
Decision=SARUKU_PROPOSE_WORD の場合のみ
Compose → Validate
```

システム全体は以下のouter loopとして動作する。

```text
poll
 ↓
STEP2 Team Intelligence
Observe → Understand → State
 ↓
STEP3 Decision Engine
Plan → Decide
 ↓
WAIT / COORDINATE / SARUKU_PROPOSE_WORD
                      ↓
                 STEP4 Writing
                 Compose → Validate
                      ↓
                  Executor
                      ↓
                   poll again
```

`Observe → Replan` はSTEP4固有ではなくSTEP2〜4を囲むouter loopである。

---

## 2. STEP2のHard Boundary

STEP2が行うこと:

- team roomの新着観測
- signature / DID / room / generation / seqの検証
- referee receipt等からのdeterministic FACT生成
- signed planning textの意味抽出
- CLAIM / PROPOSAL / QUESTION等の記録
- bounded INFERENCE生成
- append-only Team Event Ledgerへの保存
- TeamContext Snapshotの再構築
- lifecycle上1回だけのSaruku capability announcement

STEP2が行わないこと:

- next writerを決定する
- turn順を確定する
- wordを選ぶ
- final contributorを予約する
- 他agentへ行動を強制する
- deadline/fallbackを決定する
- poem strategyを決定する
- STEP3のactionを先取りする
- Sarukuのglobal Mood / Personality / Relationship / State & Growthを変更する

既存の`_writing()`が現在直接word候補を作ってPOSTしている部分は、STEP3実装までのlegacy pathとして残してよい。

ただしSTEP2の機能として扱ってはならない。

---

## 3. Storage Architecture

既存`StateStore.events`をraw room eventのsource of truthとして継続利用する。

```text
Technocore
    ↓
StateStore.events
raw signed room records
(room, generation, seq)
    ↓
STEP2 verifier / extractor
    ↓
Team Event Ledger
append-only semantic records
    ↓
Reducer
    ↓
TeamContext Snapshot
derived / rebuildable cache
```

### 3.1 Raw events

新しいraw message archiveは作らない。

既存SQLite `events` tableにはすでにroom/generation/seq単位でraw eventが保存されているため、それを参照する。

### 3.2 Team Event Ledger

新規append-only SQLite tableを作る。

概念例:

```text
team_events

event_id
game_id
room
room_generation
source_seq
source_poem_version
evidence_class
event_type
actor_did
subject_did
scope
predicate
value_json
target_text
resolved_target_did
extraction_method
extraction_confidence
extractor_version
payload_json
created_at
```

同じsourceから複数semantic eventが出るため、

```text
source_seq + semantic ordinal + extractor_version
```

等からdeterministic event_idを作る。

再処理しても同一eventを増殖させない。

### 3.3 Processing index

LLM処理状況はLedgerとは分離する。

例:

```text
team_message_analysis

game_id
room
generation
seq
extractor_version
status
attempts
last_error_code
processed_at
```

status候補:

```text
parsed
parsed_empty
retryable_error
terminal_error
```

LLM失敗はteam runtime全体の失敗にしない。

### 3.4 TeamContext Snapshot

Snapshotはauthorityではない。

```text
team_context_snapshots

game_id
schema_version
reducer_version
ledger_high_watermark
payload_json
rebuilt_at
```

SnapshotはTeam Event Ledgerからいつでも再構築可能でなければならない。

再起動時、reducer version変更時、Snapshot欠損時にもrebuildできること。

---

## 4. Epistemic Model

情報は必ず以下へ分離する。

### FACT

Saruku側でdeterministicに検証できる情報。

例:

```text
verified speaker DID
room / generation / seq
frozen roster membership
current poem version
previous accepted contributor
accepted word
has_contributed
available DID letters
registered X account（accepted registry evidenceがある場合のみ）
```

`available_letters`は既存DID letter helperとofficial checkerのルールから計算する。

本人の自己申告を使用しない。

### CLAIM

署名者本人が述べた内容。

例:

```text
"I can publish to X."
"I cannot publish to X."
"I can take the next word."
"I want the final turn."
"I can do it after this line."
```

CLAIMが署名済みであることは、

> DID-Xがこのclaimを発言した

というFACTを作る。

しかしclaim内容そのものをFACTへ昇格させない。

### INFERENCE

FACTやCLAIMからSaruku側が導いた解釈。

v1では極力限定する。

初期実装で必須とする代表例:

```text
CLAIM:
  member X can_publish_x = false

+

RULE FACT:
  final contributor must publish and submit

→

INFERENCE:
  terminal_publish_risk
  condition = member X becomes final contributor
```

`seems_responsive`等の広い人物評価はSTEP2 v1では作らなくてよい。

---

## 5. Provenance

全semantic eventはsourceへ遡れること。

最低限:

```json
{
  "source": {
    "room": "d-sonnet-2-team-example",
    "room_generation": 3,
    "seq": 142,
    "verified_speaker_did": "did:key:...",
    "poem_version_at_observation": 17,
    "source_text_sha256": "..."
  },
  "extraction": {
    "method": "deterministic | llm",
    "extractor_version": 1,
    "confidence": 0.97
  }
}
```

`confidence`は「内容が真である確率」ではない。

LLMが本文をそのsemantic shapeとして読めた確信度に過ぎない。

deterministic FACTはconfidenceを必要としない、または1.0としてよい。

---

## 6. Speaker Identity Boundary

LLMへspeaker identityを決めさせない。

LLM入力前にdeterministicに以下を確定する。

```json
{
  "verified_speaker_did": "did:key:...",
  "room": "...",
  "room_generation": 3,
  "seq": 142,
  "current_poem_version": 17,
  "text": "Bruce should take the next word."
}
```

signature verificationに失敗したmessageはsemantic extractionへ送らない。

本文に、

```text
"I'm Bruce."
```

と書いてあってもspeaker identityは署名DIDである。

---

## 7. Alias Resolution

LLMにnatural-language aliasからDIDを推測させない。

LLMは、

```json
{
  "target_text": "Bruce"
}
```

までを返す。

DID resolutionは後段のdeterministic resolverのみが行う。

v1では原則:

```text
exact DID string → resolve
trusted explicit alias mapping → resolve
otherwise → null
```

署名済みfree-textでの、

```text
"I'm Bruce"
```

はself-declared alias CLAIMとして記録できるが、それだけで将来の `"Bruce"` を自動DID resolutionする必要はない。

曖昧なら:

```json
{
  "target_text": "Bruce",
  "resolved_target_did": null
}
```

とする。

---

## 8. LLM Message Understanding

LLMには本文の意味だけを抽出させる。

LLMが出力してよい概念:

```text
claims
proposals
questions
constraints
retractions/corrections
unclassified
```

例:

```json
{
  "claims": [
    {
      "predicate": "can_publish_x",
      "value": false,
      "scope": "poem",
      "confidence": 0.98
    }
  ],
  "proposals": [
    {
      "proposal_type": "next_writer",
      "target_text": "Bruce",
      "scope": "next_word",
      "confidence": 0.93
    }
  ],
  "questions": [],
  "constraints": [],
  "unclassified": []
}
```

LLM schemaに以下を含めない:

```text
speaker_did
resolved_target_did
room_generation
seq
current_version
```

これらはdeterministic metadataとして外側で付与する。

### Processing policy

- referee receipt → deterministic only
- `sonnet.word.v1`等のprotocol action → deterministic only
- Saruku自身の既知のstructured record → deterministic only
- roster memberのplanning text → LLM対象
- invalid signature → ignore semantic extraction
- unknown signed text → bounded LLM extraction
- LLM unavailable →記録して処理継続
- zero extracted semantics →正常

一度有効なextractをcommitしたsourceは、同じextractor versionでは再LLM処理しない。

---

## 9. Proposal Scope and Staleness

proposalは必ず観測時のpoem stateを持つ。

例:

```json
{
  "proposal_type": "next_writer",
  "target_text": "Bruce",
  "observed_at_version": 17,
  "scope": "next_word"
}
```

### next_word

```text
current_version == observed_at_version
    → active

current_version > observed_at_version
    → stale
```

### current_line

`observed_at_line`も保持し、lineが進めばstale。

### poem

poem completionまで有効候補だが、後続proposalやretractionでsupersededになり得る。

`final_contributor` proposalはpoem scopeのproposalであって予約ではない。

Snapshotは、

```text
active proposal
stale proposal
superseded proposal
```

を区別する。

Ledgerから削除はしない。

---

## 10. Conflicting Claims

同じsubjectについて矛盾するclaimが来ても古いclaimを削除しない。

例:

```text
seq 120: "I cannot post to X."
seq 190: "I fixed it; I can post now."
```

Ledgerには両方残す。

Snapshotでは:

```text
latest_claim
prior_claim_event_ids
conflicting_claim_event_ids
```

を保持できる。

Riskもbasis eventを持つ。

後続claimによりriskがinactiveになっても、過去riskの根拠はLedgerへ残る。

---

## 11. TeamContext Snapshot

概念形:

```text
TeamContextSnapshot

game_id
room
room_generation
current_version
current_line
previous_contributor

members:
  <did>:
    facts:
      available_letters
      missing_letters
      has_contributed
      last_accepted_version
      registered_x_account
    claims:
      current
      conflicting
    proposals_about_member
    unresolved_risks

active_proposals:
  [...]

stale_proposals:
  [...]

terminal_risks:
  [...]

questions:
  [...]

unresolved_mentions:
  [...]

unparsed_messages:
  count
  latest_source_refs
```

SnapshotはSTEP3が読みやすい形に整えるが、情報を破壊してはならない。

---

## 12. `can_publish_x` Risk Model

以下を区別する。

```text
registered_x_account = FACT
can_publish_x = CLAIM
publisher configured locally = FACT
future X publication success = UNKNOWN
```

他memberが、

```text
"I cannot publish to X."
```

と述べた場合:

```text
CLAIM
can_publish_x=false
```

を保存する。

さらにreducerが:

```text
INFERENCE
terminal_publish_risk
subject=<member DID>
condition=subject becomes final contributor
basis_event_ids=[...]
severity=high
```

を生成する。

STEP2はそのriskに対して誰をfinalにするか決めない。

---

## 13. STEP2-A — Saruku Capability Announcement

roster-ready後、team roomへgameごとに最大1回だけ固定文を投稿する。

```text
I can help coordinate turns, syllables, DID-letter constraints, and contributor coverage.
If I become the final contributor, I can publish the exact frozen poem from my registered X account and submit it.
```

これはDecision Engineの判断ではなく、lifecycle bootstrap announcementである。

Sarukuをfinal contributorへ予約しない。

投稿後もSnapshotでは:

```text
Saruku can_publish_x=true
```

はSaruku自身のCLAIMとして扱い、

```text
registered_x_account
```

等のローカルに検証可能なものだけFACTとする。

### Protocol note

公式Sonnet protocolはteam-room planning自体を許可しているが、planning note用のreferee action typeを明示していない。

現runtimeは既に`sonnet.note.v1`をapplication-level note envelopeとして利用しているため、v1ではteam planningにもこれを再利用してよい。

ただし:

- referee receiptを期待しない
- official accepted actionとして扱わない
- application-local planning envelopeであることをコードコメントへ明記する

---

## 14. Integration with Current Daemon

現在:

```text
WRITING
  _receipts(poem_room)
  ↓
  PoemState
  ↓
  LLM word candidate
  ↓
  POST
```

STEP2後:

```text
WRITING
  _receipts(poem_room)
      ↓
  raw room events persisted
      ↓
  ensure_team_intro()
      ↓
  STEP2 sync deterministic facts
      ↓
  STEP2 process bounded new planning messages
      ↓
  rebuild TeamContext Snapshot
      ↓
  existing legacy word path
```

introをこのcycleでPOSTした場合はそこでreturnし、同一cycleでwordまでPOSTしない。

STEP3導入時にlegacy word pathをDecision Engine配下へ移す。

---

## 15. Failure Isolation

理解不能messageは正常系。

以下はruntime停止理由にしない。

```text
unknown sentence
ambiguous alias
no recognized semantics
LLM timeout
LLM invalid output
unsupported planning style
```

必要なら、

```text
parsed_empty
retryable_error
terminal_error
unresolved
```

として保持する。

Team Intelligence failureはword/referee stateを破壊しない。

raw source eventは残っているので後から再処理可能。

---

## 16. STEP2 Implementation Order

順序:

```text
2-A
Capability announcement

2-C0
Ledger / Snapshot schema確定

2-B
Message verification + semantic extraction

2-C1
Reducer / persistence / rebuild

2-C2
Daemon integration + restart/replay tests
```

2-Bより前にLedger schemaを固定する。

---

## 17. STEP2 PASS Criteria

STEP2 PASSは単に「LLMがJSONを返した」ではない。

以下をすべて満たすこと。

1. 同じteam room historyから同じcanonical FACTを再構築できる。
2. restart後も同じTeamContext Snapshotへ戻れる。
3. FACT / CLAIM / INFERENCEが混ざらない。
4. 全semantic recordにprovenanceがある。
5. speaker DIDをLLMが決めない。
6. `"I'm Bruce"`で署名DIDがBruceへ置換されない。
7. unresolved aliasは`null`のまま保持できる。
8. conflicting claimsを失わない。
9. conflicting proposalsを失わない。
10. `next_word` proposalがpoem version進行でstaleになる。
11. `cannot_publish_x` claimからterminal riskを保持できる。
12. final contributorをSarukuへ固定しない。
13. structured protocol messageを不要にLLMへ送らない。
14. invalid signatureをsemantic sourceにしない。
15. LLM unavailableでもdaemon cycleが継続する。
16. unknown messageでもdaemon cycleが継続する。
17. same source/eventを再読してもLedgerがduplicateしない。
18. STEP2はglobal Saruku State & Growthを変更しない。
19. capability announcementはgameごとに最大1回。
20. STEP1の既存テストをすべて維持する。

## Final Rule

STEP2とは、

> 署名済みteam-room historyからprovenance付きTeam Event Ledgerを形成し、そこから判断前のTeamContext Snapshotを安全に再構築する観測層

である。

STEP2は「答えを決める層」ではない。