import base64
from pathlib import Path

from sonnet_chain.signing import Signer, sweep


def test_sweep():
    assert sweep(" a\nb ") == "a b"


def test_signer_seed(tmp_path: Path):
    seed = bytes(range(32))
    p = tmp_path / "seed"
    p.write_bytes(seed)
    signer = Signer(p)
    nonce = "1"
    stored, sig = signer.sign_room("room", nonce, "hello")
    assert stored == "hello"
    assert signer.did.startswith("did:key:z6Mk")
    assert len(base64.urlsafe_b64decode(sig + "==")) == 64
