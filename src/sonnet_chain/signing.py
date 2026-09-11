from __future__ import annotations

import base64
import json
import time
import unicodedata
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
MULTICODEC_ED25519 = b"\xed\x01"
INVISIBLE = {"Cc", "Cf", "Cs", "Co", "Zl", "Zp"}


def _b58(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    zeros = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * zeros + (out or "")


def did_of(key: Ed25519PrivateKey) -> str:
    pub = key.public_key().public_bytes_raw()
    return "did:key:z" + _b58(MULTICODEC_ED25519 + pub)


def sweep(text: str) -> str:
    return "".join(" " if unicodedata.category(c) in INVISIBLE else c for c in text).strip()


def load_seed(path: Path) -> bytes:
    raw = path.read_bytes()
    if len(raw) == 32:
        return raw

    text = raw.decode("utf-8").strip()
    if len(text) == 64:
        try:
            value = bytes.fromhex(text)
            if len(value) == 32:
                return value
        except ValueError:
            pass

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj = None

    if isinstance(obj, dict):
        for field in ("seed", "seed_hex", "private_key", "private_key_hex"):
            value = obj.get(field)
            if isinstance(value, str):
                v = value.strip()
                if len(v) == 64:
                    try:
                        b = bytes.fromhex(v)
                        if len(b) == 32:
                            return b
                    except ValueError:
                        pass
                try:
                    b = base64.b64decode(v, validate=True)
                    if len(b) == 32:
                        return b
                except Exception:
                    pass

    try:
        b = base64.b64decode(text, validate=True)
        if len(b) == 32:
            return b
    except Exception:
        pass

    raise ValueError(f"Unsupported Ed25519 seed format in {path}")


class Signer:
    def __init__(self, seed_file: Path):
        self._key = Ed25519PrivateKey.from_private_bytes(load_seed(seed_file))
        self.did = did_of(self._key)
        self._last_nonce = 0

    def nonce(self) -> str:
        now = time.time_ns() // 1_000_000
        if now <= self._last_nonce:
            now = self._last_nonce + 1
        self._last_nonce = now
        return str(now)

    def sign_room(self, room: str, nonce: str, text: str) -> tuple[str, str]:
        stored = sweep(text)
        raw = self._key.sign(f"{room}|{nonce}|{stored}".encode("utf-8"))
        sig = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        return stored, sig
