from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from sonnet_chain.config import CONTEST_ID, ROOMS, SARUKU_DID
from sonnet_chain.rosters import CanonicalRoster
from sonnet_chain.signing import Signer, did_of
from sonnet_chain.state import StateStore
from sonnet_chain.team_formation import (
    ApplicationDelivery, ConsentDelivery, FormationHistoryState, FormationOpportunity,
    FormationStage, StructuralKey, TeamFormationState, TeamFormationStore,
    formation_decision, formation_watchdog, latest_consent, materially_stronger,
    reduce_candidate_states, start_epoch, targeted_recruitment_note,
)


def signed(key, payload, seq):
    signer = Signer.__new__(Signer)
    signer._key = key
    signer.did = did_of(key)
    signer._last_nonce = 0
    text = json.dumps(payload, separators=(",", ":"))
    stored, sig = signer.sign_room(ROOMS.discovery, str(seq), text)
    return {"seq": seq, "from": signer.did, "nonce": seq, "text": stored, "sig": sig}


def roster_payload(game, members, rid="r"):
    return {"type": "sonnet.roster.v1", "contest_id": CONTEST_ID, "game_id": game,
            "poem_room": ROOMS.team(game), "room_generation": 1,
            "members": members, "request_id": rid}


def test_history_continuity_and_multiple_gaps():
    history = FormationHistoryState("room", 1)
    history.observe([100, 101, 110, 112])
    assert [(g.start, g.end) for g in history.gap_ranges] == [(102, 109), (111, 111)]
    assert history.is_complete(100, 101)
    assert not history.is_complete(100, 110)


def test_retention_floor_is_not_a_reconcilable_gap():
    history = FormationHistoryState("room", 1)
    history.observe(
        [*range(1, 1415), *range(85215, 90001)],
        available_from_seq=85215, available_through_seq=90000,
    )
    assert history.gap_ranges == []
    assert history.reconciliation_required is False
    assert history.retention_truncated is True
    assert history.is_complete(85215, 90000)
    assert not history.is_complete(1000, 90000)


def test_missing_sequence_inside_retained_interval_is_reconcilable():
    history = FormationHistoryState("room", 1)
    history.observe(
        [*range(85215, 86000), *range(86011, 90001)],
        available_from_seq=85215, available_through_seq=90000,
    )
    assert [(gap.start, gap.end) for gap in history.gap_ranges] == [(86000, 86010)]
    assert history.reconciliation_required is True


def test_retention_boundary_survives_restart(tmp_path: Path):
    state = StateStore(tmp_path / "state.db")
    store = TeamFormationStore(state)
    history = FormationHistoryState("room", 7)
    history.observe([1, 85215, 85216], available_from_seq=85215, available_through_seq=85216)
    store.save_history(history)
    state.close()
    state = StateStore(tmp_path / "state.db")
    restored = TeamFormationStore(state).load_history("room", 7)
    assert restored is not None
    assert restored.available_from_seq == 85215
    assert restored.retention_truncated is True
    assert restored.reconciliation_required is False
    assert not restored.is_complete(1, 85216)
    state.close()


def test_positive_terminal_fact_still_outranks_truncated_history():
    assert formation_decision(
        None, history_complete=False, team_ready=True, now=datetime.now(timezone.utc)
    ) == "TEAM_READY"


def test_history_reconciliation_and_generation_are_persistent(tmp_path: Path):
    state = StateStore(tmp_path / "state.db")
    formation = TeamFormationStore(state)
    first = FormationHistoryState("room", 1); first.observe([1, 3])
    second = FormationHistoryState("room", 2); second.observe([1, 2])
    formation.save_history(first); formation.save_history(second)
    first.observe([1, 2, 3]); formation.save_history(first)
    assert formation.load_history("room", 1).is_complete(1, 3)
    assert formation.load_history("room", 2).is_complete(1, 2)
    state.close()


def test_latest_action_consent_sequences():
    keys = [Ed25519PrivateKey.generate() for _ in range(4)]
    members = [did_of(k) for k in keys]
    signer = keys[0]; did = members[0]
    sign_a = signed(signer, roster_payload("g", members, "a"), 1)
    withdraw = signed(signer, {"type": "sonnet.withdraw.v1", "contest_id": CONTEST_ID,
                              "game_id": "g", "request_id": "w"}, 2)
    assert latest_consent([sign_a], "g", did).state == "ROSTER"
    assert latest_consent([sign_a, withdraw], "g", did).state == "NONE"
    resign = signed(signer, roster_payload("g", members, "b"), 3)
    assert latest_consent([sign_a, withdraw, resign], "g", did).state == "ROSTER"


def test_candidate_reducer_marks_progressive_inviter_anchored_roster_ready():
    keys = [Ed25519PrivateKey.generate() for _ in range(3)]
    members = [did_of(key) for key in keys] + [SARUKU_DID]
    now = datetime.now(timezone.utc)
    opportunity = FormationOpportunity("g", members[0], 1, now)
    records = [
        signed(keys[0], roster_payload("g", members, "anchor"), 2),
        signed(keys[1], roster_payload("g", members, "second"), 3),
    ]

    reduced = reduce_candidate_states(records, [opportunity])

    assert reduced[0].structural_key.signer_count == 2
    assert reduced[0].structural_key.missing_signers == 2
    assert reduced[0].structural_key.stage_rank == list(FormationStage).index(
        FormationStage.READY_TO_COUNTERSIGN
    )


def test_gap_after_consent_is_unknown():
    keys = [Ed25519PrivateKey.generate() for _ in range(4)]
    members = [did_of(k) for k in keys]
    record = signed(keys[0], roster_payload("g", members), 1)
    history = FormationHistoryState(ROOMS.discovery, 1); history.observe([1, 3])
    assert latest_consent([record], "g", members[0], history).state == "UNKNOWN"


def test_recruitment_allowlist_and_trusted_role():
    key = Ed25519PrivateKey.generate(); did = did_of(key)
    base = {"contest_id": CONTEST_ID, "game_id": "g", "target_did": SARUKU_DID,
            "role": "writer"}
    note = signed(key, {"type": "sonnet.note.v1", **base}, 1)
    assert targeted_recruitment_note(note, {did}) is not None
    assert targeted_recruitment_note(note, set()) is None
    assert targeted_recruitment_note(signed(key, {"type": "sonnet.invite.v1", **base}, 2), {did}) is None
    assert targeted_recruitment_note(signed(key, {"type": "sonnet.invite.v2", **base}, 3), {did}) is None


def test_recruitment_preserves_verified_lead_signal():
    key = Ed25519PrivateKey.generate(); did = did_of(key)
    note = signed(key, {
        "type": "sonnet.note.v1", "contest_id": CONTEST_ID, "game_id": "g",
        "target_did": SARUKU_DID, "team_lead_did": did,
    }, 1)
    opportunity = targeted_recruitment_note(note, {did})
    assert opportunity is not None and opportunity.lead_verified is True


def test_tampered_recruitment_is_ignored():
    key = Ed25519PrivateKey.generate(); did = did_of(key)
    item = signed(key, {"type": "sonnet.note.v1", "contest_id": CONTEST_ID,
                       "game_id": "g", "target_did": SARUKU_DID}, 1)
    item["text"] = item["text"].replace('"g"', '"x"')
    assert targeted_recruitment_note(item, {did}) is None


def test_structural_comparator_ignores_recency_and_prefers_fewer_missing():
    current = StructuralKey(4, True, True, True, 1, 3)
    fresh_but_equal = StructuralKey(4, True, True, True, 1, 3)
    fewer_missing = StructuralKey(4, True, True, True, 0, 4)
    assert not materially_stronger(fresh_but_equal, current)
    assert materially_stronger(fewer_missing, current)


def test_hwm_only_moves_on_strict_improvement():
    now = datetime.now(timezone.utc)
    epoch = start_epoch(FormationOpportunity("g", "did:key:zLead", 1, now), "app", now)
    three = StructuralKey(4, True, True, True, 1, 3)
    two = StructuralKey(4, True, True, True, 2, 2)
    assert epoch.observe_progress(three, now, 3)
    assert not epoch.observe_progress(two, now + timedelta(minutes=1), 4)
    assert not epoch.observe_progress(three, now + timedelta(minutes=2), 5)
    assert epoch.epoch_high_watermark_at == now.isoformat()


def test_replacement_recovery_is_single_and_fixed():
    now = datetime.now(timezone.utc)
    epoch = start_epoch(FormationOpportunity("g", "did:key:zLead", 1, now), "app", now)
    old = StructuralKey(4, True, True, True, 1, 3)
    replacement = StructuralKey(4, True, True, True, 2, 2)
    epoch.observe_lineage("old", old, now, 1)
    lineage_progress, epoch_progress = epoch.observe_lineage("replacement", replacement, now, 2)
    assert lineage_progress and not epoch_progress
    assert epoch.maybe_start_replacement_recovery(now)
    deadline = epoch.replacement_recovery_deadline
    assert not epoch.maybe_start_replacement_recovery(now + timedelta(minutes=1))
    assert epoch.replacement_recovery_deadline == deadline
    assert epoch.replacement_recovery_active(now + timedelta(minutes=19, seconds=59))
    assert not epoch.replacement_recovery_active(now + timedelta(minutes=20))


def test_application_deadline_survives_restart(tmp_path: Path):
    path = tmp_path / "state.db"; now = datetime.now(timezone.utc)
    state = StateStore(path); store = TeamFormationStore(state)
    epoch = start_epoch(FormationOpportunity("g", "did:key:zLead", 1, now), "app", now)
    store.save_epoch(epoch); deadline = epoch.application_reconcile_deadline; state.close()
    state = StateStore(path)
    assert TeamFormationStore(state).active_epoch().application_reconcile_deadline == deadline
    state.close()


def test_application_reconciliation_is_bounded():
    now = datetime.now(timezone.utc)
    epoch = start_epoch(FormationOpportunity("g", "did:key:zLead", 1, now), "app", now)
    assert formation_watchdog(epoch, now + timedelta(minutes=9, seconds=59)) == "RECONCILE_APPLICATION"
    assert formation_watchdog(epoch, now + timedelta(minutes=10)) == "APPLICATION_DELIVERY_UNKNOWN"


def test_stall_boundaries_and_ready_precedence():
    now = datetime.now(timezone.utc)
    epoch = start_epoch(FormationOpportunity("g", "did:key:zLead", 1, now), "app", now)
    epoch.application_delivery_state = ApplicationDelivery.CONFIRMED
    epoch.application_observed_at = now.isoformat()
    assert formation_watchdog(epoch, now + timedelta(minutes=19, seconds=59)) == "STAY"
    assert formation_watchdog(epoch, now + timedelta(minutes=20)) == "REEVALUATE"
    assert formation_watchdog(epoch, now + timedelta(minutes=59, seconds=59)) == "REEVALUATE"
    assert formation_watchdog(epoch, now + timedelta(minutes=60)) == "HARD_STALL"
    assert formation_decision(epoch, history_complete=True, ready_to_countersign=True,
                              now=now + timedelta(minutes=60)) == "COUNTERSIGN"


def test_possible_consent_forbids_hard_stall_and_switch():
    now = datetime.now(timezone.utc)
    epoch = start_epoch(FormationOpportunity("g", "did:key:zLead", 1, now), "app", now)
    epoch.application_observed_at = (now - timedelta(hours=2)).isoformat()
    epoch.consent_delivery_state = ConsentDelivery.CONSENT_POSTED_UNCONFIRMED
    assert formation_decision(epoch, history_complete=True, challenger_stronger=True, now=now) == "RECONCILE_CONSENT"


def test_outbox_is_exact_idempotent_and_restart_safe(tmp_path: Path):
    path = tmp_path / "state.db"; state = StateStore(path)
    payload = {"type": "sonnet.roster.v1", "request_id": "r", "members": ["x"]}
    assert state.persist_protocol_intent("r", "roster", ROOMS.discovery, payload,
                                         ConsentDelivery.CONSENT_POSTED_UNCONFIRMED,
                                         created_at=datetime.now(timezone.utc).isoformat())
    with pytest.raises(ValueError, match="different protocol intent"):
        state.persist_protocol_intent("r", "roster", ROOMS.discovery, {"changed": True},
                                      ConsentDelivery.CONSENT_POSTED_UNCONFIRMED,
                                      created_at=datetime.now(timezone.utc).isoformat())
    state.close(); state = StateStore(path)
    intent = state.unresolved_protocol_intent("roster")
    assert intent["payload"] == payload
    state.close()


def test_migration_is_idempotent_and_preserves_old_data(tmp_path: Path):
    path = tmp_path / "state.db"
    first = StateStore(path); first.record_event("room", 1, 1, {"seq": 1}); first.close()
    second = StateStore(path); second.close()
    third = StateStore(path)
    assert third.events("room")[0]["seq"] == 1
    tables = {row[0] for row in third.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"formation_epochs", "formation_history_state", "formation_opportunities", "protocol_outbox"} <= tables
    third.close()
