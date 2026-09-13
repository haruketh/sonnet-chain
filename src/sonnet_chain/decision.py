from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from .config import SARUKU_DID
from .discovery import TeamCandidate
from .llm import LLMClient
from .state import Phase
from .teams import score_team

OBSERVE_SECONDS = 120
COORDINATE_SECONDS = 300
HIGH_COVERAGE_SLACK = 3
WAIT_SECONDS = 60
TOTAL_LINES = 14
SYLLABLES_PER_LINE = 10
SUPPORTED_COORDINATION_INTENTS = frozenset({
    "ask_uncovered_member_to_contribute", "warn_terminal_publisher_risk",
    "answer_direct_question",
})
TARGET_REQUIRED_INTENTS = frozenset({"ask_uncovered_member_to_contribute"})
TARGETLESS_INTENTS = frozenset({"answer_direct_question"})

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": [action for action in
                    ("WAIT", "COORDINATE", "SARUKU_PROPOSE_WORD")]},
        "reason_code": {"type": "string"},
        "target_did": {"type": ["string", "null"]},
        "coordination_intent": {"type": ["string", "null"]},
        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
    },
    "required": ["action", "reason_code", "target_did", "coordination_intent", "confidence"],
    "additionalProperties": False,
}


class Action(StrEnum):
    WAIT = "WAIT"
    COORDINATE = "COORDINATE"
    SARUKU_PROPOSE_WORD = "SARUKU_PROPOSE_WORD"


class EscalationStage(StrEnum):
    STAGE_0_OBSERVE = "STAGE_0_OBSERVE"
    STAGE_1_COORDINATE = "STAGE_1_COORDINATE"
    STAGE_2_PROGRESS = "STAGE_2_PROGRESS"


class CoveragePressure(StrEnum):
    IMPOSSIBLE = "IMPOSSIBLE"
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    ELEVATED = "ELEVATED"
    LOW = "LOW"


@dataclass(frozen=True)
class DecisionState:
    game_id: str
    poem_room: str
    room_generation: int
    current_version: int
    current_state_hash: str
    current_line: int
    current_line_syllables: int
    last_progress_at: str
    stall_age_seconds: int
    escalation_stage: EscalationStage
    previous_contributor: str | None
    pending_word_request: bool
    quality_generation_exhausted_for_current_state: bool
    emergency_fallback_attempted_for_current_state: bool
    saruku_no_feasible_word_for_current_state: bool
    roster: tuple[str, ...]
    uncovered_members: tuple[str, ...]
    uncovered_count: int
    saruku_is_uncovered: bool
    eligible_uncovered_coordination_targets: tuple[str, ...]
    remaining_syllables_total: int
    coverage_slack: int
    coverage_pressure: CoveragePressure
    active_self_commitments: tuple[dict[str, Any], ...]
    active_nominations: tuple[dict[str, Any], ...]
    active_requests: tuple[dict[str, Any], ...]
    active_preferences: tuple[dict[str, Any], ...]
    active_requests_to_saruku: tuple[dict[str, Any], ...]
    active_nominations_of_saruku: tuple[dict[str, Any], ...]
    active_writing_requests: tuple[dict[str, Any], ...]
    terminal_publish_risks: tuple[dict[str, Any], ...]
    relevant_open_questions: tuple[dict[str, Any], ...]
    legal_actions: frozenset[Action]
    coordination_sendable: bool
    coordination_already_sent: bool
    coordination_sent_keys: frozenset[str]
    commitment_wait_records: dict[str, dict[str, Any]]
    ledger_high_watermark: str


@dataclass(frozen=True)
class Decision:
    action: Action
    reason_code: str
    target_did: str | None
    coordination_intent: str | None
    expected_game_id: str
    expected_room_generation: int
    expected_version: int
    expected_state_hash: str
    wait_started_at: str | None
    reconsider_at: str | None
    decision_source: str
    confidence: float | None = None


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def escalation_stage(stall: int) -> EscalationStage:
    if stall < OBSERVE_SECONDS:
        return EscalationStage.STAGE_0_OBSERVE
    if stall < COORDINATE_SECONDS:
        return EscalationStage.STAGE_1_COORDINATE
    return EscalationStage.STAGE_2_PROGRESS


def coverage_pressure(slack: int) -> CoveragePressure:
    if slack < 0:
        return CoveragePressure.IMPOSSIBLE
    if slack == 0:
        return CoveragePressure.CRITICAL
    if slack <= HIGH_COVERAGE_SLACK:
        return CoveragePressure.HIGH
    if slack <= 10:
        return CoveragePressure.ELEVATED
    return CoveragePressure.LOW


def remaining_syllables(current_line: int, current_line_syllables: int) -> int:
    return max(0, (TOTAL_LINES - max(1, current_line)) * SYLLABLES_PER_LINE
               + SYLLABLES_PER_LINE - max(0, current_line_syllables))


def coordination_key(state: DecisionState, target: str | None, intent: str) -> str:
    raw = [state.game_id, state.room_generation, state.current_version, target, intent,
           state.escalation_stage.value, state.coverage_pressure.value]
    return hashlib.sha256(json.dumps(raw, separators=(",", ":")).encode()).hexdigest()


def build_decision_state(*, runtime: dict[str, Any], snapshot: dict[str, Any], now: datetime,
                         phase: Phase, deadline_ok: bool, pending_word: bool,
                         coordination_sent: set[str] | None = None,
                         commitment_waits: dict[str, dict[str, Any]] | None = None) -> DecisionState:
    roster = tuple(x for x in runtime.get("roster", []) if isinstance(x, str))
    members = snapshot.get("members", {})
    uncovered = tuple(x for x in roster if not members.get(x, {}).get("facts", {}).get(
        "has_contributed", False))
    eligible_uncovered = tuple(x for x in uncovered if x != SARUKU_DID)
    remain = remaining_syllables(int(runtime.get("current_line", 1)),
                                 int(runtime.get("current_line_syllables", 0)))
    slack = remain - len(uncovered)
    last = str(runtime["last_progress_at"])
    stall = max(0, int((now - utc(last)).total_seconds()))
    stage = escalation_stage(stall)
    proposals = snapshot.get("active_proposals", [])
    grouped = {mode: tuple(p for p in proposals if p.get("proposal_mode") == mode) for mode in
               ("SELF_COMMITMENT", "NOMINATION", "REQUEST", "PREFERENCE")}
    active_commitments, wait_records = bounded_self_commitments(
        grouped["SELF_COMMITMENT"], int(runtime["current_version"]), now,
        commitment_waits or {},
    )
    grouped["SELF_COMMITMENT"] = active_commitments
    current_requests = tuple(
        proposal for proposal in grouped["REQUEST"]
        if proposal.get("scope") == "next_word"
        and proposal.get("observed_at_version") == int(runtime["current_version"])
    )
    requests_to_saruku = tuple(
        proposal for proposal in current_requests
        if proposal.get("resolved_target_did") == SARUKU_DID
    )
    nominations_of_saruku = tuple(
        proposal for proposal in grouped["NOMINATION"]
        if proposal.get("scope") == "next_word"
        and proposal.get("observed_at_version") == int(runtime["current_version"])
        and proposal.get("resolved_target_did") == SARUKU_DID
    )
    legal = {Action.WAIT, Action.COORDINATE, Action.SARUKU_PROPOSE_WORD}
    if (phase != Phase.WRITING or not deadline_ok or runtime.get("poem_complete")
            or not runtime.get("current_state_hash")
            or runtime.get("previous_contributor") == SARUKU_DID or pending_word):
        legal.discard(Action.SARUKU_PROPOSE_WORD)
    if runtime.get("saruku_no_feasible_word_for_current_state") is True:
        legal.discard(Action.SARUKU_PROPOSE_WORD)
    saruku_covered = SARUKU_DID not in uncovered
    if slack <= 0 and saruku_covered:
        legal.discard(Action.SARUKU_PROPOSE_WORD)
    provisional = DecisionState(
        str(runtime["game_id"]), str(runtime["poem_room"]), int(runtime["room_generation"]),
        int(runtime["current_version"]), str(runtime.get("current_state_hash") or ""),
        int(runtime.get("current_line", 1)), int(runtime.get("current_line_syllables", 0)),
        last, stall, stage, runtime.get("previous_contributor"), pending_word,
        bool(runtime.get("quality_generation_exhausted_for_current_state")),
        bool(runtime.get("emergency_fallback_attempted_for_current_state")),
        bool(runtime.get("saruku_no_feasible_word_for_current_state")), roster,
        uncovered, len(uncovered), SARUKU_DID in uncovered, eligible_uncovered,
        remain, slack, coverage_pressure(slack),
        grouped["SELF_COMMITMENT"], grouped["NOMINATION"], grouped["REQUEST"],
        grouped["PREFERENCE"], requests_to_saruku, nominations_of_saruku,
        tuple(snapshot.get("active_writing_requests", [])),
        tuple(snapshot.get("terminal_risks", [])),
        tuple(snapshot.get("questions", [])), frozenset(legal), True, False,
        frozenset(coordination_sent or set()), wait_records,
        str(snapshot.get("ledger_high_watermark", "")),
    )
    target = uncovered[0] if uncovered else None
    target = eligible_uncovered[0] if eligible_uncovered else None
    intent = "ask_uncovered_member_to_contribute" if target else "observe_progress"
    sent = coordination_key(provisional, target, intent) in (coordination_sent or set())
    return DecisionState(**{**asdict(provisional), "roster": roster, "uncovered_members": uncovered,
                            "active_self_commitments": grouped["SELF_COMMITMENT"],
                            "active_nominations": grouped["NOMINATION"],
                            "active_requests": grouped["REQUEST"],
                            "active_preferences": grouped["PREFERENCE"],
                            "active_requests_to_saruku": requests_to_saruku,
                            "active_nominations_of_saruku": nominations_of_saruku,
                            "active_writing_requests": tuple(snapshot.get(
                                "active_writing_requests", []
                            )),
                            "terminal_publish_risks": tuple(snapshot.get("terminal_risks", [])),
                            "relevant_open_questions": tuple(snapshot.get("questions", [])),
                            "legal_actions": frozenset(legal),
                            "coordination_sendable": not sent,
                            "coordination_already_sent": sent})


def bounded_self_commitments(
    commitments: tuple[dict[str, Any], ...], current_version: int, now: datetime,
    records: dict[str, dict[str, Any]],
) -> tuple[tuple[dict[str, Any], ...], dict[str, dict[str, Any]]]:
    updated = {key: dict(value) for key, value in records.items()}
    annotated = []
    for commitment in commitments:
        item = dict(commitment)
        event_id = commitment.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            item["wait_active"] = False
            annotated.append(item)
            continue
        record = updated.get(event_id)
        if record is None or record.get("poem_version") != current_version:
            record = {
                "poem_version": current_version,
                "wait_started_at": now.isoformat(),
                "wait_until": (now + timedelta(seconds=WAIT_SECONDS)).isoformat(),
            }
            updated[event_id] = record
        try:
            still_waiting = now < utc(str(record["wait_until"]))
        except (KeyError, TypeError, ValueError):
            still_waiting = False
        item["wait_active"] = still_waiting
        annotated.append(item)
    return tuple(annotated), updated


def coordination_sendable(state: DecisionState, target: str | None, intent: str) -> bool:
    return coordination_key(state, target, intent) not in state.coordination_sent_keys


def _decision(state: DecisionState, action: Action, reason: str, now: datetime,
              target: str | None = None, intent: str | None = None,
              source: str = "deterministic") -> Decision:
    waiting = action == Action.WAIT
    return Decision(action, reason, target, intent, state.game_id, state.room_generation,
                    state.current_version, state.current_state_hash,
                    now.isoformat() if waiting else None,
                    (now + timedelta(seconds=WAIT_SECONDS)).isoformat() if waiting else None,
                    source)


def _deterministic_policy(state: DecisionState, now: datetime) -> Decision | None:
    if state.pending_word_request:
        return _decision(state, Action.WAIT, "pending_word_reconciliation", now)
    if (
        state.active_requests_to_saruku
        and Action.SARUKU_PROPOSE_WORD in state.legal_actions
        and qualification_preserved(state, 1)
        and (
            not state.quality_generation_exhausted_for_current_state
            or (
                state.escalation_stage == EscalationStage.STAGE_2_PROGRESS
                and not state.emergency_fallback_attempted_for_current_state
            )
        )
    ):
        return _decision(state, Action.SARUKU_PROPOSE_WORD, "direct_next_word_request", now)
    if state.relevant_open_questions and coordination_sendable(
        state, None, "answer_direct_question"
    ):
        return _decision(state, Action.COORDINATE, "direct_question", now,
                         intent="answer_direct_question")
    if state.terminal_publish_risks:
        target = state.terminal_publish_risks[0].get("subject_did")
        if coordination_sendable(state, target, "warn_terminal_publisher_risk"):
            return _decision(state, Action.COORDINATE, "terminal_publisher_risk", now, target,
                             "warn_terminal_publisher_risk")
    if state.eligible_uncovered_coordination_targets:
        committed = {p.get("actor_did") for p in state.active_self_commitments
                     if p.get("wait_active") is True}
        expired_commitment = any(
            p.get("wait_active") is False for p in state.active_self_commitments
        )
        if committed.intersection(state.eligible_uncovered_coordination_targets):
            return _decision(state, Action.WAIT, "uncovered_member_self_committed", now)
        if not (
            expired_commitment
            and state.escalation_stage == EscalationStage.STAGE_2_PROGRESS
        ) and coordination_sendable(
            state, state.eligible_uncovered_coordination_targets[0],
            "ask_uncovered_member_to_contribute"
        ):
            return _decision(state, Action.COORDINATE, "uncovered_member", now,
                             state.eligible_uncovered_coordination_targets[0],
                             "ask_uncovered_member_to_contribute")
    if (any(p.get("wait_active") is True for p in state.active_self_commitments)
            and state.escalation_stage != EscalationStage.STAGE_2_PROGRESS):
        return _decision(state, Action.WAIT, "teammate_self_committed", now)
    if (state.escalation_stage == EscalationStage.STAGE_2_PROGRESS
            and Action.SARUKU_PROPOSE_WORD in state.legal_actions):
        return _decision(state, Action.SARUKU_PROPOSE_WORD, "fallback_progress", now)
    if state.escalation_stage == EscalationStage.STAGE_0_OBSERVE:
        return _decision(state, Action.WAIT, "bounded_observation", now)
    return None


def deterministic_decision(state: DecisionState, now: datetime) -> Decision:
    return _deterministic_policy(state, now) or _decision(
        state, Action.WAIT, "ambiguous_fallback", now, source="deterministic_fallback"
    )


def validate_decision(decision: Decision, state: DecisionState) -> bool:
    coordination_valid = True
    if decision.action == Action.COORDINATE:
        intent = decision.coordination_intent
        coordination_valid = (
            intent in SUPPORTED_COORDINATION_INTENTS
            and (intent not in TARGET_REQUIRED_INTENTS or (
                decision.target_did is not None and decision.target_did in state.roster
                and decision.target_did != SARUKU_DID
            ))
            and (intent not in TARGETLESS_INTENTS or decision.target_did is None)
            and (decision.target_did is None or (
                decision.target_did in state.roster and decision.target_did != SARUKU_DID
            ))
            and coordination_sendable(state, decision.target_did, intent or "")
        )
    return (decision.action in state.legal_actions
            and decision.expected_game_id == state.game_id
            and decision.expected_room_generation == state.room_generation
            and decision.expected_version == state.current_version
            and decision.expected_state_hash == state.current_state_hash
            and (decision.action != Action.WAIT or decision.reconsider_at is not None)
            and coordination_valid)


def qualification_preserved(state: DecisionState, candidate_syllables: int) -> bool:
    if SARUKU_DID in state.uncovered_members:
        return state.remaining_syllables_total - candidate_syllables >= state.uncovered_count - 1
    return state.remaining_syllables_total - candidate_syllables >= state.uncovered_count


def decision_llm_input(state: DecisionState) -> dict[str, Any]:
    """Bounded semantic input only; raw room text is intentionally unreachable here."""
    def proposals(items: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
        allowed = ("event_id", "proposal_mode", "proposal_type", "resolved_target_did",
                   "scope", "observed_at_version", "extraction_confidence")
        return [{key: item.get(key) for key in allowed if key in item} for item in items]

    return {
        "escalation_stage": state.escalation_stage.value,
        "stall_age_seconds": state.stall_age_seconds,
        "coverage": {"uncovered_members": list(state.uncovered_members),
                     "remaining_syllables_total": state.remaining_syllables_total,
                     "coverage_slack": state.coverage_slack,
                     "coverage_pressure": state.coverage_pressure.value},
        "proposals": proposals(state.active_self_commitments + state.active_nominations
                               + state.active_requests + state.active_preferences),
        "terminal_risks": [{key: risk.get(key) for key in
                            ("event_id", "subject_did", "condition", "severity", "basis_event_ids")}
                           for risk in state.terminal_publish_risks],
        "question_source_ids": [question.get("event_id")
                                for question in state.relevant_open_questions],
        "legal_actions": sorted(action.value for action in state.legal_actions),
        "ledger_high_watermark": state.ledger_high_watermark,
    }


def decide(state: DecisionState, now: datetime, llm: LLMClient | None = None) -> Decision:
    deterministic = _deterministic_policy(state, now)
    if deterministic is not None:
        return deterministic
    fallback = _decision(state, Action.WAIT, "ambiguous_fallback", now,
                         source="deterministic_fallback")
    if llm is None:
        return fallback
    try:
        value = llm.structured(
            "Choose exactly one currently legal action. Do not infer protocol facts or invent targets.",
            decision_llm_input(state), "team_decision", DECISION_SCHEMA,
        )
        action = Action(value["action"])
        candidate = _decision(state, action, str(value["reason_code"]), now,
                              value.get("target_did"), value.get("coordination_intent"), "llm")
        candidate = Decision(**{**asdict(candidate), "action": action,
                                "confidence": value.get("confidence")})
        if validate_decision(candidate, state):
            return candidate
    except (KeyError, TypeError, ValueError, RuntimeError):
        pass
    return fallback


def coordination_text(intent: str, target: str | None) -> str:
    if intent == "ask_uncovered_member_to_contribute" and target:
        return f"{target}, can you take the next word so every roster member contributes?"
    if intent == "warn_terminal_publisher_risk":
        return "We should keep final publication capability in mind before the last contribution."
    if intent == "answer_direct_question":
        return "I can help with the next step; please use the verified contest state for coordination."
    raise ValueError("unsupported coordination intent")


@dataclass(frozen=True)
class TeamDecision:
    action: str
    reason: str
    game_id: str | None = None


def deterministic_team_decision(candidates: list[TeamCandidate], minimum: float = 45) -> TeamDecision:
    """Preserved STEP1 discovery policy."""
    viable = [c for c in candidates if isinstance(c.open_seats, int) and c.open_seats > 0]
    if not viable:
        return TeamDecision("wait", "no viable observed team")
    ranked = sorted(((score_team(c), c) for c in viable), key=lambda item: item[0].score,
                    reverse=True)
    best_score, best = ranked[0]
    if best_score.score < minimum:
        return TeamDecision("wait", f"best deterministic score is {best_score.score}")
    return TeamDecision("join", "; ".join(best_score.reasons), best.game_id)
