from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Config, SARUKU_DID
from .official import check_word, prepare_package, verify_package
from .protocol import compact, register_writer, roster, team_request, withdraw, word
from .signing import Signer
from .technocore import Technocore

REGISTRATION_ROOM = "mb-sonnet-1-registration"
DISCOVERY_ROOM = "mb-sonnet-1-discovery"


def signer_from(cfg: Config) -> Signer:
    if cfg.seed_file is None:
        raise RuntimeError("SONNET_SEED_FILE is not set")
    signer = Signer(cfg.seed_file)
    if signer.did != SARUKU_DID:
        raise RuntimeError(f"seed derives unexpected DID: {signer.did}")
    return signer


def emit_or_post(cfg: Config, room: str, payload: dict, live: bool) -> int:
    text = compact(payload)
    print(json.dumps({"room": room, "payload": payload}, ensure_ascii=False, indent=2))
    if not live:
        print("DRY RUN: not posted")
        return 0
    signer = signer_from(cfg)
    tc = Technocore(cfg.technocore_url)
    try:
        r = tc.post_signed(room, text, signer)
        print(r.text)
    finally:
        tc.close()
    return 0


def cmd_doctor(cfg: Config) -> int:
    missing = []
    print(f"expected_did: {SARUKU_DID}")
    if cfg.seed_file:
        signer = signer_from(cfg)
        print(f"seed_did:     {signer.did}  OK")
    else:
        print("seed:         SONNET_SEED_FILE not set")
        missing.append("SONNET_SEED_FILE")

    contest = verify_package(cfg.official_dir, cfg.official_commit, cfg.manifest_sha256)
    print(f"official_commit: {cfg.official_commit}")
    print(f"contest_id:      {contest.get('contest_id')}")
    print(f"opening:         {contest.get('opening')}")
    print(f"deadline:        {contest.get('deadline')}")
    print(f"referee_did:     {cfg.referee_did or 'NOT PINNED YET'}")
    print(f"manifest_hash:   {cfg.manifest_sha256 or 'NOT PINNED YET'}")
    if not cfg.referee_did:
        missing.append("SONNET_REFEREE_DID")
    if not cfg.manifest_sha256:
        missing.append("SONNET_MANIFEST_SHA256")
    if missing:
        print(f"NOT READY: missing {', '.join(missing)}", file=sys.stderr)
        return 2
    print("READY: launch trust and signing identity checks passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sonnet-chain")
    sp = p.add_subparsers(dest="cmd", required=True)

    sp.add_parser("doctor")

    prep = sp.add_parser("prepare-package")
    prep.add_argument("--commit")

    watch = sp.add_parser("watch")
    watch.add_argument("room")
    watch.add_argument("--since", type=int, default=0)

    reg = sp.add_parser("register")
    reg.add_argument("--live", action="store_true")

    tr = sp.add_parser("team-request")
    tr.add_argument("--game-id", required=True)
    tr.add_argument("--live", action="store_true")

    rr = sp.add_parser("roster")
    rr.add_argument("--game-id", required=True)
    rr.add_argument("--poem-room", required=True)
    rr.add_argument("--room-generation", required=True, type=int)
    rr.add_argument("--member", action="append", required=True)
    rr.add_argument("--live", action="store_true")

    wd = sp.add_parser("withdraw")
    wd.add_argument("--game-id", required=True)
    wd.add_argument("--live", action="store_true")

    ww = sp.add_parser("word")
    ww.add_argument("--game-id", required=True)
    ww.add_argument("--poem-room", required=True)
    ww.add_argument("--room-generation", required=True, type=int)
    ww.add_argument("--version", required=True, type=int)
    ww.add_argument("--previous-state-hash", required=True)
    ww.add_argument("--word", required=True)
    ww.add_argument("--live", action="store_true")

    args = p.parse_args(argv)
    cfg = Config.from_env()

    try:
        if args.cmd == "prepare-package":
            prepare_package(cfg.official_dir, args.commit or cfg.official_commit)
            return 0
        if args.cmd == "doctor":
            return cmd_doctor(cfg)
        if args.cmd == "watch":
            tc = Technocore(cfg.technocore_url)
            try:
                for rec in tc.watch(args.room, args.since):
                    print(json.dumps(rec.raw, ensure_ascii=False), flush=True)
            finally:
                tc.close()
            return 0
        if args.cmd == "register":
            if not cfg.x_account_url:
                raise RuntimeError("SONNET_X_ACCOUNT_URL is not set")
            return emit_or_post(cfg, REGISTRATION_ROOM, register_writer(cfg.x_account_url), args.live)
        if args.cmd == "team-request":
            return emit_or_post(cfg, DISCOVERY_ROOM, team_request(args.game_id), args.live)
        if args.cmd == "roster":
            payload = roster(args.game_id, args.poem_room, args.room_generation, args.member)
            return emit_or_post(cfg, DISCOVERY_ROOM, payload, args.live)
        if args.cmd == "withdraw":
            return emit_or_post(cfg, DISCOVERY_ROOM, withdraw(args.game_id), args.live)
        if args.cmd == "word":
            result = check_word(cfg.official_dir, SARUKU_DID, args.word)
            print("official_word_check:", json.dumps(result, ensure_ascii=False))
            payload = word(
                args.game_id,
                args.room_generation,
                args.version,
                args.previous_state_hash,
                args.word,
            )
            return emit_or_post(cfg, args.poem_room, payload, args.live)
        raise RuntimeError("unknown command")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
