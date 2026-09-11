from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from .config import OFFICIAL_REPO


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def prepare_package(dest: Path, commit: str) -> None:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", OFFICIAL_REPO, str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "fetch", "--all", "--prune"], check=True)
    subprocess.run(["git", "-C", str(dest), "checkout", "--detach", commit], check=True)


def current_commit(dest: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], text=True
    ).strip()


def verify_package(dest: Path, expected_commit: str, manifest_sha256: str | None) -> dict:
    if not dest.exists():
        raise RuntimeError(f"official package missing: {dest}; run prepare-package")
    actual_commit = current_commit(dest)
    if actual_commit != expected_commit:
        raise RuntimeError(f"official commit mismatch: {actual_commit} != {expected_commit}")

    manifest = dest / "manifest.json"
    if manifest_sha256:
        actual = sha256(manifest)
        if actual.lower() != manifest_sha256.lower():
            raise RuntimeError(f"manifest SHA-256 mismatch: {actual} != {manifest_sha256}")

    subprocess.run([sys.executable, "scripts/verify.py"], cwd=dest, check=True)
    return json.loads((dest / "contest.json").read_text(encoding="utf-8"))


def check_word(dest: Path, did: str, token: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "scripts/check_word.py", did, token],
        cwd=dest,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise ValueError(proc.stderr.strip() or proc.stdout.strip() or "official word check failed")
    return json.loads(proc.stdout)
