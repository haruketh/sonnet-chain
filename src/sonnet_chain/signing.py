from __future__ import annotations

import base64
import json
import time
import unicodedata
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

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


def _b58decode(value: str) -> bytes:
    n = 0
    for char in value:
        try:
            digit = B58.index(char)
        except ValueError as exc:
            raise ValueError("invalid base58 character") from exc
        n = n * 58 + digit
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * (len(value) - len(value.lstrip("1"))) + raw


def did_of(key: Ed25519PrivateKey) -> str:
    pub = key.public_key().public_bytes_raw()
    return "did:key:z" + _b58(MULTICODEC_ED25519 + pub)


def public_key_of_did(did: str) -> Ed25519PublicKey:
    prefix = "did:key:z"
    if not did.startswith(prefix):
        raise ValueError("expected did:key with base58btc key")
    decoded = _b58decode(did[len(prefix) :])
    if len(decoded) != 34 or decoded[:2] != MULTICODEC_ED25519:
        raise ValueError("DID is not an Ed25519 did:key")
    return Ed25519PublicKey.from_public_bytes(decoded[2:])


def verify_room_signature(room: str, did: str, nonce: str | int, text: str, sig: str) -> bool:
    if not isinstance(text, str) or not isinstance(sig, str):
        return False
    nonce_text = str(nonce)
    if not nonce_text.isdecimal() or int(nonce_text) <= 0 or nonce_text.startswith("0"):
        return False
    try:
        signature = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        public_key_of_did(did).verify(
            signature, f"{room}|{nonce_text}|{sweep(text)}".encode("utf-8")
        )
    except (ValueError, InvalidSignature):
        return False
    return True


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
