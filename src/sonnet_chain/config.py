from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SARUKU_DID = "did:key:z6MkpbdDcpSyuxmuivLEiYYoLWqn3wD433rPYBa7oG8EAVX8"
CONTEST_ID = "sonnet-1"
OFFICIAL_REPO = "https://github.com/flop-labs/technocore-sonnet-challange.git"
CANDIDATE_COMMIT = "624fe936212e865b128047c5c4c1c21bfa80454b"


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
    rules_room: str = "d-sonnet-1-rules"
    openai_api_key_file: Path | None = None
    model: str = "gpt-5.1"
    x_publish_cmd: str | None = None

    @classmethod
    def from_env(cls) -> "Config":
        seed = os.getenv("SONNET_SEED_FILE")
        api_key = os.getenv("SONNET_OPENAI_API_KEY_FILE")
        return cls(
            technocore_url=os.getenv("TECHNOCORE_URL", "https://technocore.chat").rstrip("/"),
            seed_file=Path(seed).expanduser() if seed else None,
            x_account_url=os.getenv("SONNET_X_ACCOUNT_URL"),
            referee_did=os.getenv("SONNET_REFEREE_DID") or None,
            manifest_sha256=(os.getenv("SONNET_MANIFEST_SHA256") or "").lower() or None,
            official_dir=Path(os.getenv("SONNET_OFFICIAL_DIR", ".official/technocore-sonnet-challange")),
            official_commit=os.getenv("SONNET_OFFICIAL_COMMIT", CANDIDATE_COMMIT),
            state_db=Path(os.getenv("SONNET_STATE_DB", "state/sonnet-chain.sqlite3")),
            rules_room=os.getenv("SONNET_RULES_ROOM", "d-sonnet-1-rules"),
            openai_api_key_file=Path(api_key).expanduser() if api_key else None,
            model=os.getenv("SONNET_MODEL", "gpt-5.1"),
            x_publish_cmd=os.getenv("SONNET_X_PUBLISH_CMD") or None,
        )
