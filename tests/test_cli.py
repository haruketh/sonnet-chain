from pathlib import Path

from sonnet_chain.cli import cmd_doctor
from sonnet_chain.config import Config


def test_doctor_fails_closed_without_secrets(monkeypatch, tmp_path: Path, capsys):
    contest = {
        "contest_id": "sonnet-1",
        "opening": "2026-09-11T12:00:00Z",
        "deadline": "2026-09-18T12:00:00Z",
    }
    monkeypatch.setattr("sonnet_chain.cli.verify_package", lambda *_: contest)
    cfg = Config(
        technocore_url="https://technocore.chat",
        seed_file=None,
        x_account_url=None,
        referee_did=None,
        manifest_sha256=None,
        official_dir=tmp_path,
        official_commit="624fe936212e865b128047c5c4c1c21bfa80454b",
    )

    assert cmd_doctor(cfg) == 2
    captured = capsys.readouterr()
    assert "SONNET_SEED_FILE not set" in captured.out
    assert "referee_did:     NOT PINNED YET" in captured.out
    assert "manifest_hash:   NOT PINNED YET" in captured.out
    assert "NOT READY" in captured.err
