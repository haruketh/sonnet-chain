from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sonnet_chain import signing


def _case(key: Ed25519PrivateKey, nonce: int = 1) -> tuple[str, str, int, str, str]:
    room, text = "cache-room", "hello"
    did = signing.did_of(key)
    signature = key.sign(f"{room}|{nonce}|{text}".encode())
    encoded = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return room, did, nonce, text, encoded


class _PublicKeyFactory:
    def __init__(self, original):
        self.original = original
        self.constructions = 0
        self.verifications = 0

    def from_public_bytes(self, raw: bytes):
        self.constructions += 1
        key = self.original.from_public_bytes(raw)
        owner = self

        class CountedKey:
            def verify(self, signature: bytes, message: bytes) -> None:
                owner.verifications += 1
                key.verify(signature, message)

        return CountedKey()


def test_same_did_constructs_once_but_verifies_every_signature(monkeypatch):
    case = _case(Ed25519PrivateKey.generate())
    factory = _PublicKeyFactory(signing.Ed25519PublicKey)
    monkeypatch.setattr(signing, "Ed25519PublicKey", factory)
    signing.public_key_of_did.cache_clear()
    assert all(signing.verify_room_signature(*case) for _ in range(100))
    assert factory.constructions == 1
    assert factory.verifications == 100


def test_distinct_dids_have_independent_cached_keys(monkeypatch):
    cases = [_case(Ed25519PrivateKey.generate(), index + 1) for index in range(3)]
    factory = _PublicKeyFactory(signing.Ed25519PublicKey)
    monkeypatch.setattr(signing, "Ed25519PublicKey", factory)
    signing.public_key_of_did.cache_clear()
    for case in cases:
        assert signing.verify_room_signature(*case)
        assert signing.verify_room_signature(*case)
    assert factory.constructions == 3
    assert factory.verifications == 6


def test_invalid_did_and_signature_cannot_bypass_verification():
    signing.public_key_of_did.cache_clear()
    room, did, nonce, text, signature = _case(Ed25519PrivateKey.generate())
    assert not signing.verify_room_signature(room, "did:key:znot-valid!", nonce, text, signature)
    assert not signing.verify_room_signature(room, did, nonce, text, signature[:-2] + "aa")
    assert signing.verify_room_signature(room, did, nonce, text, signature)


def test_public_key_cache_is_bounded():
    info = signing.public_key_of_did.cache_info()
    assert info.maxsize == signing.PUBLIC_KEY_CACHE_SIZE == 256
