import json
from pathlib import Path

import httpx
import pytest

from sonnet_chain.config import _load_runtime_env
from sonnet_chain.publisher import CommandPublisher, PublisherUnavailable
from sonnet_chain.secure_files import SecureFileError, read_private_file
from sonnet_chain.x_publisher_adapter import (
    XPublisherAdapter, XPublisherDefinitelyNotSent, XPublisherError,
)
from sonnet_chain.x_oauth import XIdentity


class FakeTokenManager:
    def __init__(self, token="test-token", refreshed="refreshed-token"):
        self.token = token
        self.refreshed = refreshed
        self.refreshes = 0

    def get_valid_access_token(self):
        return self.token

    def verify_identity(self, token):
        return XIdentity("sarukubt", "42")

    def is_current(self, token):
        return token == self.token

    def refresh(self, token):
        self.refreshes += 1
        self.token = self.refreshed
        return self.token


def private_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_secure_reader_rejects_world_readable_file(tmp_path: Path):
    path = tmp_path / "secret"
    path.write_text("not-a-secret")
    path.chmod(0o644)
    with pytest.raises(SecureFileError):
        read_private_file(path, 100)


def test_x_publisher_dry_run_never_reads_credentials(tmp_path: Path):
    adapter = XPublisherAdapter(FakeTokenManager(), tmp_path / "publisher.db")
    try:
        result = adapter.publish(["line one\nline two", "line three"], dry_run=True)
    finally:
        adapter.close()
    assert result["dry_run"] is True
    assert result["post_ids"] == ["dry-run-1", "dry-run-2"]
    assert not (tmp_path / "publisher.db").exists()


def test_x_thread_post_ids_are_persisted_and_reused(tmp_path: Path):
    requests = []
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        body = requests[-1]
        return httpx.Response(200, json={"data": {"id": str(len(requests)), "text": body["text"]}})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = XPublisherAdapter(FakeTokenManager(), tmp_path / "publisher.db", client)
    try:
        first = adapter.publish(["first", "second"], dry_run=False)
        second = adapter.publish(["first", "second"], dry_run=False)
    finally:
        adapter.close()
    assert first["post_ids"] == ["1", "2"] == second["post_ids"]
    assert len(requests) == 2
    assert requests[1]["reply"] == {"in_reply_to_tweet_id": "1"}


def test_ambiguous_x_response_blocks_duplicate_retry(tmp_path: Path):
    calls = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("ambiguous", request=request)
    adapter = XPublisherAdapter(FakeTokenManager(), tmp_path / "publisher.db", httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        with pytest.raises(XPublisherError, match="ambiguous"):
            adapter.publish(["first"], dry_run=False)
        with pytest.raises(XPublisherError, match="refusing duplicate"):
            adapter.publish(["first"], dry_run=False)
    finally:
        adapter.close()
    assert calls == 1


def test_same_text_with_different_root_parent_never_reuses_post_id(tmp_path: Path):
    requests = []
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={"data": {
            "id": f"post-{len(requests)}", "text": body["text"],
        }})
    adapter = XPublisherAdapter(
        FakeTokenManager(), tmp_path / "publisher.db",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        first = adapter.publish(["same"], False, "parent-a")
        second = adapter.publish(["same"], False, "parent-b")
    finally:
        adapter.close()
    assert first["post_ids"] == ["post-1"]
    assert second["post_ids"] == ["post-2"]
    assert [item["reply"]["in_reply_to_tweet_id"] for item in requests] == [
        "parent-a", "parent-b",
    ]


def test_definite_non_auth_x_rejection_can_retry(tmp_path: Path):
    calls = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(403, json={})
        return httpx.Response(200, json={"data": {"id": "ok", "text": "first"}})
    adapter = XPublisherAdapter(FakeTokenManager(), tmp_path / "publisher.db", httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        with pytest.raises(XPublisherDefinitelyNotSent, match="HTTP 403"):
            adapter.publish(["first"], dry_run=False)
        assert adapter.publish(["first"], dry_run=False)["post_ids"] == ["ok"]
    finally:
        adapter.close()


def test_daemon_publisher_rejects_dry_run_ids(monkeypatch):
    result = type("Result", (), {"returncode": 0, "stdout": '{"dry_run":true,"post_ids":["dry-run-1"]}'})()
    monkeypatch.setattr("sonnet_chain.publisher.subprocess.run", lambda *args, **kwargs: result)
    with pytest.raises(PublisherUnavailable, match="dry-run"):
        CommandPublisher("/safe/adapter").publish("poem")


def test_command_publisher_returns_actual_create_response_fields(monkeypatch):
    result = type("Result", (), {
        "returncode": 0,
        "stdout": json.dumps({
            "dry_run": False, "post_ids": ["post-1"],
            "posts": [{"id": "post-1", "text": "returned", "author_id": "account"}],
        }),
    })()
    monkeypatch.setattr("sonnet_chain.publisher.subprocess.run", lambda *args, **kwargs: result)
    assert CommandPublisher("/safe/adapter").publish_part("intended") == {
        "id": "post-1", "text": "returned", "author_id": "account",
    }


def test_runtime_env_is_literal_and_allowlisted(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime.env"
    runtime.write_text("SONNET_MODEL=test-model\nSONNET_SEED_FILE=/safe/path\n")
    runtime.chmod(0o600)
    monkeypatch.delenv("SONNET_MODEL", raising=False)
    monkeypatch.delenv("SONNET_SEED_FILE", raising=False)
    _load_runtime_env(runtime)
    assert __import__("os").environ["SONNET_MODEL"] == "test-model"
    runtime.write_text("UNSAFE_KEY=value\n")
    with pytest.raises(RuntimeError):
        _load_runtime_env(runtime)
