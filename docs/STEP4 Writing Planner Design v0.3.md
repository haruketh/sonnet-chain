# STEP4 — Writing Planner Design v0.3

Status: Ready for implementation  
Date: 2026-09-13

## 1. Purpose

STEP4はSTEP3が

```text
SARUKU_PROPOSE_WORD
```

を選択した後に、

> 現在のaccepted poem stateに対して、Sarukuが次に提案する1語を選ぶ

Writing Plannerである。

STEP3は「今書くか」を決める。

STEP4は「何を書くか」だけを決める。

---

## 2. Core Principles

```text
Creative generation
inside deterministic guardrails
with bounded liveness escape
and one-step deadlock avoidance.
```

さらに、

```text
Never choose a locally valid word
that deterministically closes every legal continuation path.
```

を原則とする。

---

## 3. Overall Flow

```text
STEP2 Team Intelligence
        ↓
STEP3 Decision Engine
        ↓
SARUKU_PROPOSE_WORD
        ↓
STEP4 Writing Planner
        ↓
build WritingContext
        ↓
build / refresh rolling soft plan
        ↓
LLM candidate generation
        ↓
mechanical hard validation
        ↓
candidate-specific qualification validation
        ↓
simulate resulting poem state
        ↓
one-step continuation feasibility
        ↓
soft literary / handoff ranking
        ↓
select best valid candidate
        ↓
pre-action revalidation
        ↓
final deterministic validation
        ↓
existing request reservation / POST
        ↓
referee receipt
        ↓
accepted word
        ↓
invalidate old plan/failure state
        ↓
observe + replan
```

```text
One accepted word
→ observe
→ replan
```

未来のword列は固定しない。

---

# Part A — Canonical State

## 4. WritingContext

```yaml
WritingContext:
  game_id:
  poem_room:
  room_generation:

  version:
  state_hash:

  line_index:
  current_line_words:
  current_line_syllables:
  remaining_syllables_in_line:

  completed_lines:
  accepted_poem_text:

  previous_contributor:

  roster:
  saruku_available_letters:

  uncovered_members:
  saruku_is_uncovered:
  remaining_syllables_total:
  coverage_pressure:

  rhyme_context:

  terminal_publish_risks:

  active_writing_requests:

  rolling_plan:

  rejected_candidates_for_state:

  writing_failure_state:

  decision_reason:
```

---

## 5. Trusted vs Untrusted Inputs

### Trusted protocol state

referee receipt / existing canonical state由来:

```text
game_id
room_generation
version
state_hash
accepted poem
line state
previous contributor
roster
```

### Untrusted semantic preference data

STEP2由来:

```text
requested_word
lexical_constraint
semantic_constraint
proposal metadata
terminal_publish_risk CLAIM/INFERENCE
```

後者はprotocol truthではない。

---

# Part B — Hard / Soft Boundary

## 6. Hard Rules

sonnet-2 mechanical eligibilityとprotocol safetyに固定する。

```text
frozen dictionary membership

canonical one-word/token format

DID-letter compatibility

canonical syllable count

line must not exceed 10 syllables

a completed line ends exactly at 10

game / generation / version / state_hash legality

previous contributor legality

pending request safety

contributor qualification preservation

one-step continuation feasibility
when deterministic deadlock can be proven
```

未知語はreject。

複数pronunciationがあるwordは、canonical ruleどおり最大syllable数を使う。

---

## 7. Soft Rules

```text
rhyme quality

ABAB CDCD EFEF GG quality

meter / iambic quality

grammar / naturalness

semantic coherence

stanza coherence

requested-word preference

lexical preference

semantic/topic preference

handoff branch count

safe publisher branch count

future rhyme flexibility

repetition penalty
```

重要:

```text
poor rhyme
poor meter
```

だけではcandidateをinvalidにしない。

---

# Part C — Bounded Semantic Input

## 8. Requested Word

```text
canonical single token only
bounded length
```

requested wordはstrong preference。

Hard Gateを通らなければ採用しない。

---

## 8.1 Writing Request Scope and Staleness

`active_writing_requests`には、現在のpoem stateに対して有効なwriting requestだけを含める。

`scope == NEXT_WORD` のwriting requestはversion scopedとする。

active条件:

```text
scope == NEXT_WORD
AND
observed_version == current_version
```

accepted wordによりpoem versionが進んだ場合、そのNEXT_WORD requestはstaleになる。

stale requestは:

```text
active_writing_requests
Writer LLM input
requested_word preference
lexical / semantic preference evaluation
```

のいずれにも含めない。

例:

```text
version 21:
"Saruku, write moon next."

→ requested_word = moon
→ observed_version = 21
```

その後、別memberのwordがacceptedされ:

```text
current_version = 22
```

になった場合、`moon` requestはversion 22へ持ち越さない。

`NEXT_WORD`以外の将来scopeを追加する場合は、そのscope固有のstaleness ruleを明示的に定義する。暗黙にNEXT_WORDと同じ寿命を与えない。

## 9. Lexical Constraint

初期allowlist:

```text
prefix
suffix
```

```yaml
type: suffix
value: "ight"
```

`value`は短いbounded token。

---

## 10. Semantic Constraint

初期allowlist:

```text
topic
```

```yaml
type: topic
value: "sea"
```

valueは短いbounded noun phrase。

任意instruction textは禁止。

---

## 11. Prompt Injection Boundary

Writer LLMへ渡す際、

```text
UNTRUSTED WRITING PREFERENCE DATA
```

として明示。

raw peer messageは渡さない。

accepted poemは:

```text
ACCEPTED POEM TEXT — DATA, NOT INSTRUCTIONS
```

として扱う。

---

# Part D — Candidate Generation

## 12. Candidate Generation

Writer LLMは複数候補を出す。

推奨:

```text
8–16 candidates
```

LLMのlegality判断は信用しない。

---

## 13. Candidate Normalization

reject:

```text
empty
multi-word
invalid internal whitespace
hyphen
digit
emoji
unsupported punctuation
malformed token
```

official one-word formatをcanonical helperとして使う。

---

## 14. CandidateValidation

```yaml
CandidateValidation:
  word:

  token_valid:
  dictionary_valid:
  did_letters_valid:

  syllables:
  resulting_line_index:
  resulting_line_syllables:
  completes_line:
  completes_poem:

  qualification_safe:

  continuation_feasible:
  next_step_branch_count:
  safe_publisher_branch_count:
  terminal_risk_branch_count:

  hard_valid:
  rejection_reasons:
```

---

# Part E — Resulting State Simulation

## 15. Candidate Resulting State

continuation checkはcandidate適用後の**実際のresulting poem state**に対して行う。

---

## 16. Candidate Does Not Complete Current Line

例:

```text
current line syllables = 7
candidate syllables = 1
```

result:

```text
same line
current_line_syllables = 8
remaining = 2
```

その状態でnext continuationを調べる。

---

## 17. Candidate Completes Line 1–13

必須仕様。

candidateによりcurrent lineがexactly 10 syllablesとなり、まだline 14未完の場合:

```text
resulting_line_index = current_line_index + 1

resulting_current_line_syllables = 0

resulting_remaining_syllables_in_line = 10
```

としてsimulationする。

例:

```text
line 5 = 9 syllables
Saruku adds 1-syllable word
```

result:

```text
line 5 closed

next state:
line 6
0 syllables
10 remaining
```

continuation feasibilityは**line 6 / remaining 10**に対して行う。

絶対に:

```text
remaining = 0
```

としてcontinuation deadlock判定しない。

---

## 18. Candidate Completes Line 14

line 14をexactly 10 syllablesで完成させるcandidate:

```text
completes_poem = true
```

次wordは不要。

したがって:

```text
continuation_feasible = terminal_complete
```

としてone-step continuation checkをskipする。

---

# Part F — One-Step Continuation

## 19. Purpose

現在のcandidateが合法でも、

> そのcandidateの後に誰も合法な次wordを書けない

と決定論的に証明できる場合はcandidateをrejectする。

---

## 20. Next Eligible Contributors

Saruku candidate accepted後は:

```text
previous_contributor = SARUKU_DID
```

となる。

したがってnext contributor候補:

```text
roster - { SARUKU_DID }
```

---

## 21. Continuation Search

各memberについてresulting stateで:

```text
frozen dictionary
→ token legality
→ member DID-letter compatibility
→ resulting line syllable capacity
→ qualification safety
```

をfilterする。

少なくとも1 legal wordがあればbranchあり。

---

## 22. Continuation Hard Gate

全next eligible membersについて:

```text
legal continuation word count == 0
```

ならcandidate reject。

```text
continuation_feasible = false
```

---

## 23. Branch Counts

少なくとも以下を持つ。

```text
next_step_branch_count
```

推奨定義:

```text
number of eligible members
with >=1 legal continuation word
```

必要なら将来 `(member, word)` pair countへ拡張可能。

---

# Part G — Terminal Publisher Risk

## 24. terminal_publish_risks

STEP2/3で得た:

```text
can_publish_x = false
```

等のCLAIM由来riskをWritingContextへ含める。

protocol factではない。

---

## 25. Publisher-aware Branch Ranking

candidate後のnext continuation contributorsを:

```text
safe publisher branch

terminal-risk branch
```

に分類する。

```text
safe_publisher_branch_count

terminal_risk_branch_count
```

を計算可能にする。

---

## 26. Not a Hard Gate

```text
safe_publisher_branch_count == 0
```

でもcandidateをmechanically invalidにはしない。

CLAIM由来riskだからである。

ただしline 14終盤ほどstrong soft penaltyを与えてよい。

特に:

```text
candidate A
→ only next writer has terminal publication risk

candidate B
→ several next writers without known risk
```

ならBを強く優先する。

---

# Part H — Rhyme and Rolling Plan

## 27. Rhyme Context

```yaml
rhyme_context:
  line_index:
  rhyme_slot:
  paired_line_index:
  paired_line_end_word:
  paired_end_sound:
  current_family_status:
```

rhymeはsoft。

---

## 28. Rolling Plan

version scoped:

```yaml
rolling_plan:
  version:
  stanza_goal:
  current_line_goal:
  rhyme_obligation:
  handoff_goal:
```

固定word列は禁止。

accepted wordが進めばrebuild可能。

---

# Part I — Qualification Safety

## 29. Candidate-Specific Qualification

STEP3でWRITEがlegalでも、candidate syllable数によってremaining capacityが変わる。

candidateごとにqualificationを再確認する。

特にCRITICALでSarukuがuncoveredなら:

```text
remaining_syllables_after_candidate
>= uncovered_members_after_candidate
```

必須。

---

# Part J — Failure / Liveness State

## 30. WritingFailureState

```yaml
WritingFailureState:
  game_id:
  room_generation:
  version:
  state_hash:

  generation_attempts:
  repair_attempts:

  failure_categories:
  last_attempt_at:

  quality_generation_exhausted:
  emergency_fallback_attempted:
  saruku_no_feasible_word:
```

---

## 31. Important State Separation

以下を混同しない。

### quality_generation_exhausted

意味:

> LLM quality-generation pathを同一stateで使い切った。

これは:

```text
Saruku cannot write
```

を意味しない。

---

### emergency_fallback_attempted

意味:

> deterministic emergency enumerationをこのstateで実施した。

---

### saruku_no_feasible_word

意味:

> deterministic enumerationを行った結果、
> 現在のstateでSarukuが出せるHard-valid wordが0だった。

これは:

```text
team has no legal word
```

を意味しない。

他DIDには書ける可能性がある。

---

## 32. Never Use Ambiguous `NO_FEASIBLE_WORD`

外部status / state nameは:

```text
NO_FEASIBLE_SARUKU_WORD
```

または:

```text
saruku_no_feasible_word
```

とする。

チーム全体のdeadlockと誤解しない。

---

## 33. Failure State Reset

次のいずれかが変化:

```text
game
room_generation
version
state_hash
```

したらcurrent-state WritingFailureStateをreset。

---

# Part K — Normal / Repair / Emergency Path

## 34. Normal Generation

同一stateでbounded。

例:

```text
normal generation max 2
```

constantsで定義。

---

## 35. Repair

normal candidateが全Hard-invalidなら最大1回repair。

repairへ渡すのはallowlisted rejection categoryだけ。

---

## 36. Stage 0 / Stage 1 After Quality Exhaustion

LLM generationを使い切っただけなら:

```text
quality_generation_exhausted = true
```

とする。

この時点で:

```text
saruku_no_feasible_word = false
```

のまま。

STEP3はSTAGE_0 / STAGE_1ならWAIT / COORDINATE等を選択可能。

---

## 37. Stage 2 Emergency Re-entry

重要。

同じversion/state_hashでも、後にSTEP3がSTAGE_2へ進み:

```text
SARUKU_PROPOSE_WORD
```

を再び選択することを許す。

その場合STEP4は:

```text
quality_generation_exhausted == true
AND
emergency_fallback_attempted == false
```

なら、

LLM generationを繰り返さず:

```text
deterministic emergency enumeration
```

へ直接進む。

---

## 38. Emergency Deterministic Candidate Enumeration

```text
frozen dictionary
→ token-valid
→ Saruku DID-letter compatible
→ syllable-valid
→ qualification-safe
→ continuation-feasible
```

でHard-valid candidatesを列挙。

LLM不要。

---

## 39. Emergency Candidate Exists

Hard-valid candidateが1つ以上ならrankingしてSELECTED。

emergency使用をaudit。

---

## 40. No Emergency Candidate

candidate count == 0なら:

```text
saruku_no_feasible_word = true
emergency_fallback_attempted = true
```

status:

```text
NO_FEASIBLE_SARUKU_WORD
```

---

# Part L — STEP3 Feedback

## 41. DecisionState Feedback

STEP3へ:

```text
quality_generation_exhausted_for_current_state

emergency_fallback_attempted_for_current_state

saruku_no_feasible_word_for_current_state
```

を渡せるようにする。

---

## 42. STEP3 Behavior

### quality_generation_exhausted only

STAGE_0 / 1:

```text
may WAIT / COORDINATE
```

STAGE_2:

```text
WRITE may become legal again
```

→ STEP4 emergency fallbackへ到達可能。

---

### saruku_no_feasible_word == true

同一stateでSaruku WRITEを盲目的に再選択しない。

他actionへfall through。

例えば:

```text
other-member coordination
bounded WAIT
```

state進行で解除。

---

# Part M — Soft Ranking

## 43. Soft Ranking Factors

Hard-valid candidateのみ対象。

```text
semantic coherence

line goal fit

stanza goal fit

rhyme fit

meter quality

grammar

requested_word match

lexical preference

semantic preference

next_step_branch_count

safe_publisher_branch_count

terminal_risk_branch_count penalty

handoff quality

future rhyme flexibility

repetition penalty
```

---

## 44. Terminal-risk Weighting

poem序盤:

```text
low / moderate weight
```

line 14終盤:

```text
strong weight
```

ただしHard Gateにはしない。

---

# Part N — Pre-action Safety

## 45. Revalidate Before POST

必ず再取得:

```text
game_id
room_generation
version
state_hash
previous_contributor
pending request
```

変化していればPOSTしない。

---

## 46. Final Candidate Revalidation

selected wordを再度:

```text
token
dictionary
DID
syllable
qualification
resulting-state simulation
continuation feasibility
```

へ通す。

---

## 47. Pending Request

pending出現時:

```text
new request_idを作らない
```

既存same-request-id recoveryへ戻る。

---

# Part O — Execution Boundary

## 48. WritingPlannerResult

```yaml
WritingPlannerResult:
  status:
    SELECTED
    QUALITY_GENERATION_EXHAUSTED
    NO_FEASIBLE_SARUKU_WORD
    STALE_STATE
    ERROR

  selected_word:

  expected_version:
  expected_state_hash:

  generation_attempts:
  repair_attempts:

  candidate_count:
  valid_candidate_count:

  emergency_fallback_used:

  source_request_ids:
```

---

## 49. Failure Is Data, Not Decision

STEP4は:

```text
WAIT
COORDINATE
```

を選ばない。

failure factをSTEP3へ返すだけ。

---

# Part P — Existing Runtime Integration

## 50. Preserve Existing Word Request Path

再利用:

```text
reserve_request
same request ID retry
receipt handling
```

legacy candidate-generation部分だけSTEP4へ置換。

---

## 51. One Intentional Write Per Cycle

既存制約維持。

```text
capability announcement
coordination
pending retry
new word
```

のintentional writeはcycle最大1件。

---

# Part Q — Implementation Structure

## 52. New Module

推奨:

```text
src/sonnet_chain/writing.py
```

主責務:

```text
WritingContext
RollingPlan
WritingFailureState
WritingCandidate
CandidateValidation
WritingPlannerResult

build_writing_context()

generate_candidates()

validate_candidate()

simulate_resulting_state()

continuation_feasibility()

publisher_branch_metrics()

rank_candidates()

emergency_candidates()

select_candidate()
```

---

## 53. Deterministic Dictionary Search Helper

例:

```text
find_legal_words_for_member()
```

pure helperとして分離。

---

## 54. Performance

必要ならindex/cache:

```text
dictionary by syllable count

normalized dictionary token cache

DID compatibility cache

(member DID, remaining syllables) feasible cache
```

correctnessは変えない。

---

# Part R — Required Tests

最低限:

1. STEP3 WRITE以外ではWritingPlannerを呼ばない。
2. WritingContext current version/state_hash。
3. frozen dictionary外reject。
4. invalid token reject。
5. DID letters違反reject。
6. max pronunciation syllable count使用。
7. overflow reject。
8. exact 10 completion valid。
9. rhymeだけ弱くてもvalid。
10. meterだけ弱くてもvalid。
11. requested word考慮。
12. invalid requested word reject。
13. bounded lexical constraint。
14. bounded semantic constraint。
15. instruction-like semantic value reject。
16. raw peer textをWriter LLMへ渡さない。
17. accepted poemはDATA扱い。
18. stale writing request除外。
19. multiple candidates。
20. bounded normal attempts。
21. bounded repair。
22. quality_generation_exhausted記録。
23. quality exhaustion != Saruku infeasible。
24. STAGE_0 quality exhaustion後はemergencyを即実行しない。
25. 同stateでSTAGE_2到達後emergencyへ再進入できる。
26. emergencyはfailed LLM generationを繰り返さない。
27. emergency candidatesもHard validation。
28. emergency soft-rule weaknessだけでrejectしない。
29. emergency candidate 0 → `NO_FEASIBLE_SARUKU_WORD`。
30. `saruku_no_feasible_word`時STEP3がblind WRITEしない。
31. 他DIDまでinfeasibleと誤認しない。
32. version進行でfailure reset。
33. state_hash変更でfailure reset。
34. locally valid + zero continuation → reject。
35. locally valid + continuation exists → valid。
36. line 3=9 + 1 syllable candidate → line 4 / 0 / remaining10としてcontinuation check。
37. non-final line completionをremaining0として誤判定しない。
38. line 13 completion → line14 remaining10 simulation。
39. final line completion → continuation不要。
40. next_step_branch_count。
41. Sarukuをnext contributorから除外。
42. qualification destructive candidate reject。
43. CRITICAL candidate-specific gate。
44. terminal_publish_risksをcontextへ保持。
45. publisher riskはHard rejectしない。
46. safe_publisher_branch_count算出。
47. terminal_risk_branch_count算出。
48. line14終盤でsafe publisher branchをsoft優先可能。
49. pre-action version race discard。
50. state_hash race discard。
51. previous contributor changes to Saruku → no POST。
52. pending出現 →新requestなし。
53. same-request-id retry維持。
54. max one intentional write/cycle。
55. rolling plan version scoped。
56. accepted wordでplan invalidate。
57. rhyme context構築。
58. STEP4はSTEP3 Decisionを変更しない。
59. STEP4はCOORDINATEを選ばない。
60. global Saruku State & Growthを書き換えない。
61. STEP1/2/3/3.1 tests全維持。

---

# 55. PASS Criteria

STEP4 v0.3 PASS:

```text
STEP3 alone decides whether Saruku writes.

STEP4 alone selects the word.

Mechanical legality is deterministic.

Rhyme / meter remain soft literary goals.

Non-final line completion correctly transitions
to the next empty line before continuation analysis.

A locally legal candidate that provably removes
every legal next move is rejected.

Quality-generation exhaustion does not permanently
disable later emergency progress.

Emergency fallback can activate later at STAGE_2
on the same poem state.

Only deterministic enumeration may establish
saruku_no_feasible_word.

Saruku infeasibility is never interpreted as
team-wide infeasibility.

Known final-publisher risk influences soft handoff
ranking but never becomes a claim-derived Hard Gate.

Raw peer instructions never enter Writer LLM.

Stale state never posts.

Each accepted word triggers fresh observation
and replanning.
```

## Final Principle

STEP4 v0.3:

```text
Be creative when creativity works.

Be deterministic when correctness matters.

Fall back deterministically when creativity fails.

Never consume the last legal path forward.
```