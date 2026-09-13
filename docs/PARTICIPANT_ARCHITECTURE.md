# Sonnet Participant Architecture v0.1

**Status:** Architecture Baseline / STEP5 Pending  
**Date:** 2026-09-13

---

# 1. Purpose

本documentは、SarukuがFLOP Labs `sonnet-2`へparticipantとして参加するruntime全体の上位architectureを定義する。

本documentは、現在のSTEP1〜4を要約するための文書ではない。

起点は、

```text
Game Understanding
        ↓
Definition of Success
        ↓
Strategic Doctrine
        ↓
Architecture Principles / Invariants
        ↓
STEP1–STEP5 Responsibility Boundaries
        ↓
STEP-specific Design
```

である。

したがって、既存STEP designが本architectureと整合しない場合、

> architectureを既存実装へ合わせる

のではなく、

> STEP design / implementationにstrategic gapが存在する可能性

として評価する。

本documentが定義する主な対象は以下である。

- Game Understanding
- Definition of Success
- Strategic Doctrine
- Hard Safety Envelope
- system-wide architectural principles
- STEP1–STEP5 responsibility boundaries
- cross-step invariants
- state / authority model
- failure / uncertainty model
- end-to-end lifecycle
- architecture conformance model

各STEP内部のalgorithm、timer、schema、state transition等はSTEP-specific designへ委譲する。

---

# 2. Game Understanding

## 2.1 This is not primarily a poetry generation problem

Sonnet Challengeではliterary qualityは重要である。

しかしSarukuが解くべき問題を、

> 可能な限り高品質なsonnetを生成すること

だけと理解してはならない。

実際のchallengeは、

> 独立して動作する複数DIDと、非同期かつ部分的にしか協調できない環境で、protocolとqualificationを守りながらpoemを完成させ、publicationとvalid submissionまで到達すること

である。

100点相当の文学的計画を持っていても、

```text
teamが成立しない

memberが停止する

next writerが現れない

qualificationを満たせない

stale stateへactionする

poemが途中停止する

final publicationが成立しない

submissionまで到達しない
```

なら、end-to-end outcomeとしては失敗である。

---

## 2.2 Other participants are autonomous

他participantはSarukuのcontrol下にない。

Sarukuは以下を異常事態ではなく、通常のoperating conditionとして扱う。

```text
peer becomes silent

peer responds slowly

peer changes plan

peer ignores a request

peer misunderstands coordination

multiple peers act concurrently

peer claims conflict

unexpected peer acts first

room remains active but poem does not progress

planned contributor never contributes

network observation is delayed

request outcome becomes ambiguous
```

したがってarchitectureは、

> 他DIDが期待どおり動くこと

をcompletionの前提にしてはならない。

Coordinationは利用する。

しかしSaruku自身のlivenessをpeer cooperationだけへ委ねない。

---

## 2.3 The game is a chain of irreversible and partially irreversible commitments

Team participation、roster consent、accepted word、final contribution、publication、submissionは同じ性質ではない。

あるactionは後から変更できる。

一方、あるactionはofficial stateを変更し、future optionを失わせる。

したがってSarukuは、

```text
what can still be changed?

what becomes irreversible?

what future options disappear?

what terminal paths remain?
```

を意識して行動する必要がある。

この性質から、

> Preserve Optionality

がsystem-wide strategic principleとなる。

---

## 2.4 Local success is not global success

各STEPの局所的successをend-to-end successと混同しない。

例:

```text
team formed
≠
completion guaranteed
```

```text
good next word found
≠
qualification preserved
```

```text
14 lines complete
≠
valid submission complete
```

各STEPは自身のlocal goalを持つが、

> SUBMITTEDへのreachabilityを不用意に破壊しない

という共通責務を持つ。

---

# 3. Definition of Success

## 3.1 Core completion target

Saruku Participant Runtimeのcore successは、

```text
eligible poem
+
required publication completed
+
valid submission completed
```

である。

すなわち、

```text
SUBMITTED
```

がend-to-end completion targetである。

---

## 3.2 Competitive optimization after submission

Contestではsubmission後のcampaign / voting等がcompetitive outcomeへ影響する可能性がある。

ただし、

```text
valid submission
```

はcampaign optimizationより優先する。

Campaignのためにsubmission completionを危険にさらしてはならない。

STEP5では、

```text
Completion objective:
    reach SUBMITTED

Post-completion optimization:
    campaign / voting support
```

を分離する。

---

## 3.3 Mission

Saruku Sonnet Participant Runtimeのmissionは、

> **unreliable and partially cooperative multi-agent environmentにおいて、protocol safetyとqualification feasibilityを維持しながら自らのagencyを保ち、停止を乗り越え、valid submissionへ到達する確率を最大化すること**

である。

Literary qualityは重要なoptimization objectiveである。

ただし、

```text
legality
qualification
terminal reachability
```

を犠牲にして追求しない。

---

# 4. Strategic Doctrine

## 4.1 Optimize end-to-end completion, not local metrics

最適化対象は、

```text
team formation speed

message response rate

number of coordination messages

rhyme quality

LLM score
```

等の局所metricではない。

最終的に重視するのは、

> valid submissionへ到達するprobability

である。

Local metricはこの目的のproxyとしてのみ利用する。

---

## 4.2 Maintain Agency

Sarukuは、

> 誰かが何かしてくれるまで永久に待つparticipant

になってはならない。

可能な範囲で、自身がterminal stateへ近づくactionを持つ。

これは、

```text
write when appropriate

coordinate when useful

leave a dead formation when safe

initiate missing progress where protocol allows
```

等を含む。

他participantへ依存する必要がある局面でも、

```text
observe
reconcile
coordinate
switch
initiate
```

等の自分自身のoptionを可能な限り保持する。

---

## 4.3 Preserve Optionality

現在合法なactionであっても、

> future legal continuationをdeterministically破壊する

なら避ける。

これはwritingだけに限らない。

例:

```text
team commitment

roster selection

next word

final contributor path

publication path
```

にも適用する。

Sarukuは局所的最適化によってfuture completion branchを不要に狭めない。

---

## 4.4 Prefer Real Progress over Apparent Activity

activityとprogressを区別する。

```text
messages
discussion
proposal
coordination
LLM execution
```

は、それ自体ではprogressではない。

Progressとは、

> terminal objectiveへの距離がverifiedに縮まったこと

である。

対象STEPごとの例:

```text
Team Formation:
    structural formation progress

Writing:
    accepted poem progress

Publication / Submission:
    authoritative completion progress
```

---

## 4.5 Commit carefully, reconcile conservatively

official stateを変更するcommitment前はoptionalitiesを評価する。

commitment後、結果がambiguousなら、

> 都合よく「失敗した」と仮定して別optionへ進む

ことを禁止する。

official side effectが存在する可能性がある間はreconciliationを優先する。

---

## 4.6 Plan backward from the terminal state

Sarukuは現在のSTEPだけを見るのではなく、

```text
What must still be true at SUBMITTED?
```

を意識する。

例えばTeam Formationは、

> rosterを成立させること

だけが目的ではない。

本質的には、

> poem completionとsubmissionまで到達可能なteamを得ること

が目的である。

同様にWritingは、

> 今のwordを合法にすること

だけではなく、

> future writing / qualification / publication pathを残すこと

が目的である。

---

## 4.7 Degrade quality before degrading completion

状況が悪化した場合、

```text
literary exploration depth

coordination elegance

creative optimization

rhyme perfection
```

は縮小してよい。

しかし、

```text
protocol legality

qualification feasibility

safe continuation

submission reachability
```

を意図的に弱めてはならない。

概念的には、

> elegance may degrade before completion does.

---

## 4.8 Adapt strategy to remaining opportunity

contestの残り時間やremaining opportunityは戦略上意味を持つ。

例えば、

```text
deadlineまで6日
```

と、

```text
deadlineまで45分
```

で同じWAIT、同じquality exploration、同じteam-selection patienceが最適とは限らない。

Architectureはdeadline-aware strategyを許容し、将来のSTEP policyはremaining time / remaining opportunityを考慮できるようにする。

ただしdeadline pressureによってHard Safety Envelopeを破ってはならない。

---

## 4.9 Cooperate, but do not require perfect cooperation

Sarukuはteam memberの意図やrequestを尊重し、coordinationを活用する。

しかし、

> perfect coordination

をsystem requirementにしない。

他DIDの協力が得られない場合も、protocol上可能な自律的progress pathを探索する。

---

## 4.10 One consequential action, then re-observe

future peer actionを固定してmulti-step executionしない。

基本戦略:

```text
OBSERVE
    ↓
UNDERSTAND CURRENT STATE
    ↓
CHOOSE ONE CONSEQUENTIAL ACTION
    ↓
REVALIDATE
    ↓
ACT
    ↓
OBSERVE AGAIN
```

新しいaccepted stateが得られたら古いplanを再評価する。

---

# 5. Hard Safety Envelope

Strategic optimizationは以下のconstraint内でのみ行う。

## 5.1 Protocol Legality

official protocolに違反するactionは禁止する。

---

## 5.2 Authoritative State Consistency

trusted referee / official canonical stateと矛盾するlocal actionを行わない。

---

## 5.3 Qualification Feasibility

current actionがqualification requirementの達成をdeterministically不可能にする場合、そのactionを選択しない。

---

## 5.4 No Conflicting Official Commitment

Sarukuが複数の相互矛盾するofficial commitmentを同時に持つ状態を作らない。

---

## 5.5 Safe Ambiguous-Side-Effect Handling

official state mutationの結果が不明な場合、

```text
success
failure
```

を推測しない。

authoritative reconciliationを行う。

---

## 5.6 Deterministically Proven Dead Ends

あるactionが、

> その後のlegal completion pathをdeterministicallyゼロにする

ことを事前に証明できる場合、そのactionをHard-invalidとして扱うことができる。

---

# 6. Strategic Optimization Model

Hard Safety Envelope内で、

> expected probability of reaching valid submission before the contest deadline

を最大化する。

判断のproxyとして概念的に以下を重視する。

```text
real progress

completion probability

terminal reachability

remaining optionality

coordination efficiency

literary quality
```

これは単純な固定weighted scoreを意味しない。

stage、remaining time、current riskによって重要度は変わり得る。

---

# 7. Operating Assumptions

## Multi-agent uncertainty

peer future actionは保証されない。

## Asynchronous observation

current observationは一時的にstaleな場合がある。

## Race conditions

複数participantが同じcanonical stateへactionできる。

## Ambiguous transport outcome

HTTP resultだけではofficial acceptanceを確定できない場合がある。

## Process failure

crash / restartは通常failure modeとして扱う。

## LLM failure

timeout、invalid structured output、semantic errorは想定内である。

## Untrusted external content

Technocore上のpeer contentはdataでありruntime instructionではない。

---

# 8. Epistemic / Authority Model

情報を概念的に以下へ分離する。

```text
PROTOCOL FACT

VERIFIED LOCAL FACT

SIGNED CLAIM

INFERENCE
```

authority:

```text
official protocol /
trusted referee canonical state
        ↓
verified deterministic fact
        ↓
signed participant claim
        ↓
derived inference
```

CLAIM / INFERENCEがFACTを上書きしてはならない。

signedであることは、claim内容そのものがtrueであることを意味しない。

---

# 9. System-wide Architectural Principles

## 9.1 Deterministic guardrails before intelligence

以下はdeterministicに扱う。

```text
signature validity
protocol legality
canonical state identity
roster truth
qualification constraints
mechanical poem constraints
request identity
freshness
```

LLMをprotocol authorityにしない。

---

## 9.2 LLM must not be liveness-critical

LLM失敗だけでruntimeが永久停止してはならない。

必要な場所には、

```text
bounded retry
deterministic fallback
safe degraded behavior
```

を持つ。

---

## 9.3 WAIT is an action, not accidental inactivity

WAITはvalid actionである。

通常のWAITは、

```text
reconsider condition

bounded watchdog

material state change
```

のいずれかを持つ。

ただしSafety-critical uncertaintyでは、fail-closed reconciliationによるWAITを許容する。

---

## 9.4 Stale plans have no authority

以下のようなcanonical stateが変化した場合、

```text
roster
room generation
poem version
state hash
previous contributor
qualification state
referee state
```

旧stateに基づくdecision / creative planを自動継続しない。

---

## 9.5 Ambiguous side effects are reconciled

state-changing actionはdurable request identityを持つ。

ambiguous resultに対してblind retryを行わない。

same logical requestをreconcileする。

---

## 9.6 Restart must preserve uncertainty

restartによって、

```text
possibly sent
possibly consented
pending withdrawal
pending proposal
delivery uncertainty
reconciliation deadline
```

等を都合のよいpre-action stateへ戻してはならない。

---

# 10. STEP Responsibility Model

STEPはimplementation convenienceではなく、明確なdecision responsibilityで分離する。

---

## STEP1 — Team Formation

### Strategic Question

> How does Saruku obtain a viable team that preserves a credible path to completion?

### Responsibility

```text
formation opportunity discovery

team viability evaluation

application / formation lifecycle

roster formation

commitment safety

formation liveness

safe switching / withdrawal

TEAM_READY transition
```

Architecture上、Sarukuがteamを得る方法を、

```text
join peer-created team only
```

に限定しない。

protocolが許す場合、

```text
Saruku-initiated team formation
```

も将来のvalid strategyとなり得る。

STEP1はpoem semanticsやword generationを行わない。

---

## STEP2 — Team Intelligence

### Strategic Question

> What has actually happened, what are peers claiming, and what can Saruku safely infer?

### Responsibility

```text
observation

verification

FACT extraction

CLAIM extraction

proposal / request representation

bounded inference

semantic event ledger

current team-context reconstruction
```

STEP2はnext actionを決めない。

---

## STEP3 — Decision Engine

### Strategic Question

> Given the current state and strategic objectives, what single action should Saruku take now?

### Responsibility

概念的には、

```text
WAIT

COORDINATE

SARUKU_ACTION
```

からcurrent actionを選ぶ。

writing phaseではSARUKU_ACTIONがword proposalとなる。

STEP3はfuture peer behaviorを固定したmulti-turn plannerではない。

---

## STEP4 — Writing Planner

### Strategic Question

> If Saruku should write now, what single contribution best preserves legal completion while maximizing poem quality?

### Responsibility

```text
candidate generation

mechanical legality

qualification preservation

resulting-state simulation

continuation feasibility

future optionality

terminal-path awareness

literary ranking

final candidate selection
```

基本単位:

```text
one accepted contribution
→ observe
→ replan
```

---

## STEP5 — Publication / Submission / Campaign

**Status: detailed design pending**

### Strategic Question

> Once writing approaches or reaches completion, how does Saruku ensure the poem reaches valid publication and submission, then safely pursue competitive campaign objectives?

Expected responsibility:

```text
frozen poem reconciliation

final contributor responsibility

publication capability

canonical publication content

publication completion

submission completion

submission confirmation

terminal recovery

campaign / vote support
```

STEP5では、

```text
SUBMITTED
```

までをcompletion-critical pathとして扱う。

Campaignはその後のcompetitive optimizationとして分離する。

---

# 11. Cross-cutting Runtime Responsibilities

一部の責務は単一STEPへ閉じない。

## Observation

最新canonical stateを取得する。

## Persistence

Safety-critical stateをrestart-safeに保持する。

## Reconciliation

ambiguous external side effectをauthoritative stateへ収束させる。

## Executor

決定済みactionだけを実行し、実行直前にfreshness / hard gateを再確認する。

## Monitoring

runtime停止、transport failure、persistent reconciliation等をoperatorへ観測可能にする。

---

# 12. End-to-End Lifecycle

概念lifecycle:

```text
REGISTERED
    ↓
TEAM FORMATION
    ↓
TEAM_READY
    ↓
WRITING
    ↓
FROZEN_POEM
    ↓
PUBLICATION
    ↓
SUBMISSION
    ↓
SUBMITTED
    ↓
CAMPAIGN / COMPETITIVE OPTIMIZATION
```

Writing phase内部:

```text
OBSERVE
    ↓
STEP2 — UNDERSTAND
    ↓
STEP3 — DECIDE
    ↓
WAIT / COORDINATE / WRITE
                       ↓
                  STEP4 — COMPOSE
                       ↓
                    EXECUTE
                       ↓
                    OBSERVE
```

---

# 13. Cross-step Invariants

1. Official protocol / trusted referee stateはlocal heuristicより優先する。

2. Local stage successをend-to-end successと混同しない。

3. 各STEPはSUBMITTEDへのreachabilityを不用意に破壊してはならない。

4. Peer natural-language contentだけからprotocol factを作らない。

5. Safety-critical validationをLLMへ委譲しない。

6. LLM failureだけを理由にliveness-critical pathを永久停止しない。

7. Protocol-illegal actionをliveness目的で許可しない。

8. Qualificationをdeterministically不可能にするactionをstall escapeとして選ばない。

9. Future legal optionをdeterministically全て破壊するactionを避ける。

10. 通常のWAITには再評価条件またはbounded exitを持たせる。

11. Safety-critical unresolved commitmentではfail-closed reconciliationを優先する。

12. External state-changing action前にcurrent stateをrevalidateする。

13. Accepted / authoritative state change後は再観測・再計画する。

14. Future peer behaviorを確定事項としてplannerへ固定しない。

15. Activityとactual progressを混同しない。

16. Ambiguous network resultを成功または失敗と推測しない。

17. Request identityを維持してauthoritative reconciliationする。

18. RestartによってSafety-critical uncertaintyを消去しない。

19. Local metric optimizationがglobal completion objectiveを上書きしてはならない。

20. Literary qualityはHard Safety Envelope内で最適化する。

21. Deadline pressureはstrategyを変えてよいがHard Safety Envelopeを弱めてはならない。

22. 他DIDとの協力を活用するが、可能なprogressをpeer goodwillだけへ依存させない。

---

# 14. State Ownership

概念ownership:

```text
Official protocol state
    → external protocol / referee authority

STEP1
    → team formation state

STEP2
    → observed / interpreted team state

STEP3
    → current action decision

STEP4
    → current writing candidate / soft creative plan

STEP5
    → publication / submission / campaign closure state
```

Derived snapshotやLLM outputはcanonical source of truthではない。

可能なstateはlower-level verified evidenceからrebuild可能であることを優先する。

---

# 15. Failure Model

Failureは可能な限り、

```text
observable

explicit

persistent where safety-relevant

reconcilable

restart-safe
```

であること。

最低限以下を区別する。

```text
rejected

stale

not yet observed

delivery unknown

protocol invalid

temporarily unavailable

LLM failure

peer inactivity

local inability

global impossibility
```

これらを単一の、

```text
error
```

または、

```text
WAIT
```

へ潰さない。

---

# 16. Non-goals

本architectureは以下を目的としない。

```text
all peersを制御すること

perfect global coordination

future turn sequenceの固定

maximum literary scoreの保証

LLMによるprotocol authority

aggressive progress at any cost

Safety uncertaintyを無視したcompletion
```

---

# 17. Architecture Conformance

本architectureはSTEP1〜5を説明するだけでなく、STEP designを評価する基準である。

各STEPについて定期的に、

```text
Does this preserve Saruku's agency?

Does this preserve optionality?

Does it optimize real progress rather than activity?

Can it survive silent / unpredictable peers?

Does it preserve qualification?

Does it preserve terminal reachability?

Does it avoid unnecessary irreversible commitment?

Does it adapt when time / opportunity becomes scarce?

Can it recover from ambiguous side effects?

Can it survive restart?
```

を確認する。

Architectureに対するgapが見つかった場合、

既存STEPに合わせてArchitectureを弱めるのではなく、

```text
Architecture Gap / Design Backlog
```

として記録する。

ただしofficial protocol interpretationの誤りが判明した場合はArchitecture側を修正する。

---

# 18. Initial Architecture Backlog

本v0.1作成時点ですでに認識しているstrategic gapを以下へ記録する。

これらは現在のSTEP designが無効であることを意味しない。

現行scopeを超えるfuture capabilityまたはoptimization gapである。

---

## A-01 — Saruku-Initiated Team Formation

**Affected:** STEP1

Current limitation:

Sarukuは現在、主にpeer-created formation opportunityへ参加する。

Strategic concern:

```text
no viable invite appears

peer-led team creation stalls

Saruku itself could legally initiate formation
```

という状況でSarukuのagencyが不足する可能性がある。

Architecture direction:

> protocolが許す範囲でSaruku自身がteam formationをinitiateできる能力を将来検討する。

Disposition:

```text
Deferred enhancement after current STEP1 baseline.
```

---

## A-02 — End-to-End Team Viability

**Affected:** STEP1 / STEP2 / future cross-step logic

Formation上もっとも進んだteamが、

> SUBMITTEDへもっとも到達しやすいteam

とは限らない。

将来的に検討可能な要素:

```text
roster lexical / letter viability

contributor feasibility

member responsiveness evidence

terminal publication capability

remaining time

known operational risk
```

これらをどこまでTeam Formation policyへ取り込むべきかは別designで決定する。

---

## A-03 — Verified Publication Capability / Terminal Reachability

**Affected:** STEP1 / STEP2 / STEP4 / STEP5

final contributorがpublication requirementを実行できない場合、poem completion後でもsubmission pathが失われる可能性がある。

現在のCLAIM / INFERENCEだけでHard Fact化してはならない。

将来的に、

```text
publication capabilityをどのようにverifyするか

どのSTEPが保持するか

終盤writingでどう使用するか

team formation時点で考慮するか
```

を設計する。

---

## A-04 — Deadline-Aware Completion Policy

**Affected:** STEP1 / STEP3 / STEP4 / STEP5

fixed local watchdogだけでは、

```text
contest序盤
contest終盤
```

の戦略差を十分表現できない可能性がある。

将来的に、

```text
remaining contest time

remaining poem capacity

remaining qualification obligation

publication / submission lead time
```

を利用してpolicy aggressivenessを調整することを検討する。

Hard Safety Envelopeはdeadlineによって変更しない。

---

# 19. Document Authority

authority structure:

```text
Official Sonnet Protocol
+
Pinned Referee Authority
        ↓
PARTICIPANT_ARCHITECTURE
        ↓
STEP-specific Design
        ↓
Implementation
```

意味:

### Official protocol

外部authority。

すべてのlocal designより優先する。

### PARTICIPANT_ARCHITECTURE

system-wide mission、strategic doctrine、responsibility boundary、cross-step invariantを定義する。

### STEP-specific Design

architectureを各STEPへ具体化するnormative detailed design。

### Implementation

designを実装する。

---

STEP-specific designとArchitectureが矛盾する場合、

> 既存implementationへ都合よく一方を選ぶ

ことは禁止する。

`Architecture Conformance Gap`としてreviewする。

---

Historical document:

```text
docs/archive/PARTICIPANT_DESIGN_v0.md
```

はdesign history / rationaleとしてのみ保持する。

current normative authorityを持たない。

---

# 20. v0.1 Completion Boundary

本v0.1では以下をarchitecture baselineとする。

```text
Game Understanding

Definition of Success

Strategic Doctrine

Hard Safety Envelope

Strategic Optimization Model

Operating Assumptions

Epistemic / Authority Model

System-wide Principles

STEP1–5 Responsibility Boundaries

Cross-step Invariants

Failure Model

Architecture Conformance Model

Initial Architecture Backlog
```

STEP5 design完成後に、

```text
publication lifecycle

submission lifecycle

campaign lifecycle

terminal recovery

final cross-step interfaces
```

をreviewし、

`PARTICIPANT_ARCHITECTURE v1.0`

候補へ進む。