from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .secure_files import read_private_file

SARUKU_DID = "did:key:z6MkpbdDcpSyuxmuivLEiYYoLWqn3wD433rPYBa7oG8EAVX8"
CONTEST_ID = "sonnet-2"
OFFICIAL_REPO = "https://github.com/flop-labs/technocore-sonnet-challange.git"
CANDIDATE_COMMIT = "e1999094c359ef7390bdf07fe2a151393a5c2f51"
CANDIDATE_MANIFEST_SHA256 = "0c87c41b8b33bdd8641f77c9e481a12f2758a0e27d47b90452b1c0a2020a9547"


@dataclass(frozen=True)
class ContestRooms:
    contest_id: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", self.contest_id):
            raise ValueError("contest_id cannot be used in a room namespace")

    @property
    def rules(self) -> str:
        return f"d-{self.contest_id}-rules"

    @property
    def registration(self) -> str:
        return f"mb-{self.contest_id}-registration"

    @property
    def discovery(self) -> str:
        return f"mb-{self.contest_id}-discovery"

    @property
    def campaign(self) -> str:
        return f"mb-{self.contest_id}-campaign"

    @property
    def votes(self) -> str:
        return f"mb-{self.contest_id}-votes"

    @property
    def submissions(self) -> str:
        return f"mb-{self.contest_id}-submissions"

    @property
    def results(self) -> str:
        return f"d-{self.contest_id}-results"

    def team(self, game_id: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,15}", game_id):
            raise ValueError("invalid game_id for team room")
        return f"d-{self.contest_id}-team-{game_id}"


ROOMS = ContestRooms(CONTEST_ID)


@dataclass(frozen=True)
class Config:
    technocore_url: str
    seed_file: Path | None
    x_account_url: str | None
    referee_did: str | None
    manifest_sha256: str | None
    official_dir: Path
    official_commit: str
    state_db: Path = Path("state/sonnet-chain.sqlite3")
    rules_room: str = ROOMS.rules
    openai_api_key_file: Path | None = None
    model: str = "gpt-5.1"
    reflex_model: str | None = None
    x_publish_cmd: str | None = None
    x_token_file: Path | None = None
    x_publish_state_db: Path = Path("state/publisher.sqlite3")
    x_client_id_file: Path | None = None
    x_client_secret_file: Path | None = None
    x_redirect_uri: str = "http://127.0.0.1:8765/callback"
    x_expected_username: str = "sarukubt"

    @classmethod
    def from_env(cls) -> "Config":
        runtime_env = Path(os.getenv("SONNET_RUNTIME_ENV", "runtime.env"))
        if runtime_env.exists():
            _load_runtime_env(runtime_env)
        seed = os.getenv("SONNET_SEED_FILE")
        api_key = os.getenv("SONNET_OPENAI_API_KEY_FILE")
        x_token = os.getenv("SONNET_X_TOKEN_FILE")
        x_client_id = os.getenv("SONNET_X_CLIENT_ID_FILE")
        x_client_secret = os.getenv("SONNET_X_CLIENT_SECRET_FILE")
        return cls(
            technocore_url=os.getenv("TECHNOCORE_URL", "https://technocore.chat").rstrip("/"),
            seed_file=Path(seed).expanduser() if seed else None,
            x_account_url=os.getenv("SONNET_X_ACCOUNT_URL"),
            referee_did=os.getenv("SONNET_REFEREE_DID") or None,
            manifest_sha256=(os.getenv("SONNET_MANIFEST_SHA256", CANDIDATE_MANIFEST_SHA256) or "").lower() or None,
            official_dir=Path(os.getenv("SONNET_OFFICIAL_DIR", ".official/technocore-sonnet-challange")),
            official_commit=os.getenv("SONNET_OFFICIAL_COMMIT", CANDIDATE_COMMIT),
            state_db=Path(os.getenv("SONNET_STATE_DB", "state/sonnet-chain.sqlite3")),
            rules_room=ROOMS.rules,
            openai_api_key_file=Path(api_key).expanduser() if api_key else None,
            model=os.getenv("SONNET_MODEL", "gpt-5.1"),
            reflex_model=os.getenv("SONNET_REFLEX_MODEL") or os.getenv("SONNET_MODEL", "gpt-5.1"),
            x_publish_cmd=os.getenv("SONNET_X_PUBLISH_CMD") or None,
            x_token_file=Path(x_token).expanduser() if x_token else None,
            x_publish_state_db=Path(os.getenv("SONNET_X_PUBLISH_STATE_DB", "state/publisher.sqlite3")),
            x_client_id_file=Path(x_client_id).expanduser() if x_client_id else None,
            x_client_secret_file=Path(x_client_secret).expanduser() if x_client_secret else None,
            x_redirect_uri=os.getenv("SONNET_X_REDIRECT_URI", "http://127.0.0.1:8765/callback"),
            x_expected_username=os.getenv("SONNET_X_EXPECTED_USERNAME", "sarukubt").lstrip("@"),
        )


RUNTIME_KEYS = {
    "SONNET_SEED_FILE", "SONNET_X_ACCOUNT_URL", "SONNET_OPENAI_API_KEY_FILE",
    "SONNET_MODEL", "SONNET_REFLEX_MODEL", "SONNET_X_PUBLISH_CMD", "SONNET_X_TOKEN_FILE",
    "SONNET_X_CLIENT_ID_FILE", "SONNET_X_CLIENT_SECRET_FILE", "SONNET_X_REDIRECT_URI",
    "SONNET_X_EXPECTED_USERNAME",
    "SONNET_X_PUBLISH_STATE_DB", "SONNET_REFEREE_DID", "SONNET_MANIFEST_SHA256",
    "SONNET_OFFICIAL_DIR", "SONNET_OFFICIAL_COMMIT",
    "SONNET_STATE_DB", "TECHNOCORE_URL",
}


def _load_runtime_env(path: Path) -> None:
    raw = read_private_file(path.absolute(), 16_384).decode("utf-8")
    for number, line in enumerate(raw.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise RuntimeError(f"runtime.env line {number} is invalid")
        key, value = stripped.split("=", 1)
        if key not in RUNTIME_KEYS or not value or any(c in value for c in "\r\n\x00"):
            raise RuntimeError(f"runtime.env line {number} is invalid")
        os.environ.setdefault(key, value)
