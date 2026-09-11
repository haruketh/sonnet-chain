import base64
import getpass
import json
import os
import stat
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from sonnet_chain.secure_files import atomic_write_private
from sonnet_chain.x_oauth import (
    DEFAULT_REDIRECT,
    SCOPES,
    XAuthRequired,
    XIdentity,
    XOAuthError,
    XTokenManager,
    authorization_url,
    authorize,
    configure_credentials,
    generate_code_verifier,
    generate_state,
    parse_token_response,
    s256_challenge,
    validate_callback,
)
from sonnet_chain.x_publisher_adapter import XPublisherAdapter


def private(path: Path, value: str) -> Path:
    path.write_text(value, encoding="ascii")
    path.chmod(0o600)
    return path


def configured(tmp_path: Path, handler, token: dict | None = None) -> XTokenManager:
    tmp_path.chmod(0o700)
    cid = private(tmp_path / "client_id", "client-id")
    secret = private(tmp_path / "client_secret", "client-secret")
    token_file = tmp_path / "oauth_tokens.json"
    if token is not None:
        private(token_file, json.dumps(token))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return XTokenManager(cid, secret, token_file, "sarukubt", client)


def token(access="access", refresh="refresh", expires_at=None):
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer",
            "scope": " ".join(SCOPES), "expires_at": expires_at or int(time.time()) + 3600}


def test_pkce_verifier_is_rfc7636_shape():
    verifier = generate_code_verifier()
    assert 43 <= len(verifier) <= 128
    assert set(verifier) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def test_s256_challenge_known_vector():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert s256_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_authorization_url_has_exact_scope_and_no_secret():
    url = authorization_url("client", DEFAULT_REDIRECT, "state", "challenge")
    query = parse_qs(urlparse(url).query)
    assert query["scope"] == [" ".join(SCOPES)]
    assert query["code_challenge_method"] == ["S256"]
    assert "secret" not in query


def test_oauth_state_mismatch_rejected():
    with pytest.raises(XOAuthError, match="state mismatch"):
        validate_callback("/callback?state=wrong&code=code", "expected")


def test_oauth_missing_code_rejected():
    with pytest.raises(XOAuthError, match="code"):
        validate_callback("/callback?state=expected", "expected")


def test_token_response_parsing_and_rotation():
    parsed = parse_token_response({"access_token": "new", "refresh_token": "new-refresh", "expires_in": 7200, "scope": " ".join(SCOPES)}, now=100)
    assert parsed["refresh_token"] == "new-refresh"
    assert parsed["expires_at"] == 7300


def test_token_response_keeps_refresh_when_not_rotated():
    parsed = parse_token_response({"access_token": "new", "expires_in": 7200, "scope": " ".join(SCOPES)}, now=100, prior_refresh="old")
    assert parsed["refresh_token"] == "old"


def test_token_response_rejects_missing_required_scope():
    with pytest.raises(XOAuthError, match="required scopes"):
        parse_token_response({"access_token": "new", "refresh_token": "refresh", "expires_in": 7200,
                              "scope": "tweet.read users.read"})


def test_configure_credentials_uses_private_files(monkeypatch, tmp_path: Path):
    values = iter(["client-id-value", "client-secret-value"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt: next(values))
    directory = tmp_path / "sonnet-x"
    configure_credentials(directory)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / "client_id").stat().st_mode) == 0o600
    assert stat.S_IMODE((directory / "client_secret").stat().st_mode) == 0o600


def test_atomic_token_file_is_mode_600(tmp_path: Path):
    tmp_path.chmod(0o700)
    path = tmp_path / "tokens.json"
    atomic_write_private(path, b"{}")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_atomic_token_replacement(tmp_path: Path):
    tmp_path.chmod(0o700)
    path = tmp_path / "tokens.json"
    atomic_write_private(path, b'{"v":1}')
    first_inode = path.stat().st_ino
    atomic_write_private(path, b'{"v":2}')
    assert path.read_bytes() == b'{"v":2}'
    assert path.stat().st_ino != first_inode


def test_access_token_expiry_and_refresh_threshold(tmp_path: Path):
    manager = configured(tmp_path, lambda request: httpx.Response(500), token(expires_at=1000))
    manager.refresh = lambda expected_access=None: "refreshed"  # type: ignore[method-assign]
    try:
        assert manager.get_valid_access_token(now=600) == "access"
        assert manager.get_valid_access_token(now=701) == "refreshed"
    finally:
        manager.close()


def test_access_token_check_accepts_epoch_zero(tmp_path: Path):
    manager = configured(tmp_path, lambda request: httpx.Response(500), token(expires_at=400))
    manager.refresh = lambda expected_access=None: "refreshed"  # type: ignore[method-assign]
    try:
        assert manager.get_valid_access_token(now=0) == "access"
    finally:
        manager.close()


def test_refresh_uses_basic_auth_and_rotates_token(tmp_path: Path):
    requests = []
    def handler(request: httpx.Request):
        requests.append(request)
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 7200, "scope": " ".join(SCOPES)})
        return httpx.Response(200, json={"data": {"username": "sarukubt", "id": "42"}})
    manager = configured(tmp_path, handler, token(expires_at=1))
    try:
        assert manager.get_valid_access_token(now=time.time()) == "new-access"
    finally:
        manager.close()
    saved = json.loads((tmp_path / "oauth_tokens.json").read_text())
    assert saved["refresh_token"] == "new-refresh"
    auth = requests[0].headers["Authorization"]
    assert auth.startswith("Basic ")
    assert b"client-secret" not in requests[0].content


def test_simultaneous_refresh_only_uses_old_token_once(tmp_path: Path):
    lock = threading.Lock()
    calls = 0
    def handler(request: httpx.Request):
        nonlocal calls
        if request.url.path.endswith("/oauth2/token"):
            with lock:
                calls += 1
            time.sleep(0.05)
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "rotated", "expires_in": 7200, "scope": " ".join(SCOPES)})
        return httpx.Response(200, json={"data": {"username": "sarukubt", "id": "42"}})
    first = configured(tmp_path, handler, token(access="old", expires_at=1))
    second = XTokenManager(first.client_id_file, first.client_secret_file, first.token_file, "sarukubt",
                           httpx.Client(transport=httpx.MockTransport(handler)))
    results = []
    threads = [threading.Thread(target=lambda m=m: results.append(m.refresh("old"))) for m in (first, second)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    first.close(); second.close()
    assert results == ["new", "new"]
    assert calls == 1
    assert stat.S_IMODE((tmp_path / "refresh.lock").stat().st_mode) == 0o600


def test_users_me_expected_account_passes_and_identity_is_saved(tmp_path: Path):
    manager = configured(tmp_path, lambda request: httpx.Response(200, json={"data": {"username": "sarukubt", "id": "42"}}), token())
    try:
        identity = manager.verify_identity("access")
        manager.save_identity(identity)
    finally:
        manager.close()
    assert identity == XIdentity("sarukubt", "42")
    assert stat.S_IMODE((tmp_path / "identity.json").stat().st_mode) == 0o600


def test_wrong_username_rejected(tmp_path: Path):
    manager = configured(tmp_path, lambda request: httpx.Response(200, json={"data": {"username": "other", "id": "42"}}), token())
    try:
        with pytest.raises(XOAuthError, match="username"):
            manager.verify_identity("access")
    finally:
        manager.close()


def test_wrong_user_id_rejected(tmp_path: Path):
    manager = configured(tmp_path, lambda request: httpx.Response(200, json={"data": {"username": "sarukubt", "id": "99"}}), token())
    private(tmp_path / "identity.json", json.dumps({"username": "sarukubt", "user_id": "42"}))
    try:
        with pytest.raises(XOAuthError, match="user ID"):
            manager.verify_identity("access")
    finally:
        manager.close()


class RetryManager:
    def __init__(self): self.token = "old"; self.refreshes = 0
    def get_valid_access_token(self): return self.token
    def verify_identity(self, token): return XIdentity("sarukubt", "42")
    def is_current(self, token): return token == self.token
    def refresh(self, expected_access=None): self.refreshes += 1; self.token = "new"; return self.token


def test_publisher_401_refresh_retry_success(tmp_path: Path):
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(401 if calls == 1 else 200, json={} if calls == 1 else {"data": {"id": "tweet"}})
    manager = RetryManager()
    adapter = XPublisherAdapter(manager, tmp_path / "publisher.db", httpx.Client(transport=httpx.MockTransport(handler)))
    try: result = adapter.publish(["poem"], False)
    finally: adapter.close()
    assert result["post_ids"] == ["tweet"] and manager.refreshes == 1 and calls == 2


def test_publisher_repeated_401_stops(tmp_path: Path):
    manager = RetryManager()
    adapter = XPublisherAdapter(manager, tmp_path / "publisher.db", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))))
    try:
        with pytest.raises(XAuthRequired, match="X_AUTH_REQUIRED"):
            adapter.publish(["poem"], False)
    finally: adapter.close()
    assert manager.refreshes == 1


def test_x_auth_flow_never_posts(monkeypatch, tmp_path: Path):
    called = []
    manager = configured(tmp_path, lambda request: (_ for _ in ()).throw(AssertionError("unexpected HTTP")))
    monkeypatch.setattr("sonnet_chain.x_oauth.webbrowser.open", lambda url: called.append(url) or True)
    monkeypatch.setattr("sonnet_chain.x_oauth.wait_for_callback", lambda state, browser_url=None: called.append(browser_url) or "short-lived-code")
    monkeypatch.setattr(manager, "exchange_code", lambda *args: token())
    monkeypatch.setattr(manager, "verify_identity", lambda access: XIdentity("sarukubt", "42"))
    try: identity = authorize(manager, DEFAULT_REDIRECT)
    finally: manager.close()
    assert identity.username == "sarukubt"
    assert len(called) == 1 and called[0].startswith("https://x.com/i/oauth2/authorize")
    assert (tmp_path / "oauth_tokens.json").exists()


def test_generated_state_is_unpredictable_shape():
    first, second = generate_state(), generate_state()
    assert first != second and len(first) >= 32 and len(second) >= 32


def test_failures_do_not_print_token_values(tmp_path: Path, capsys):
    secret_token = "DO-NOT-LOG-THIS-TOKEN"
    manager = configured(tmp_path, lambda request: httpx.Response(401, json={"error": "Unauthorized"}),
                         token(access=secret_token))
    try:
        with pytest.raises(XAuthRequired):
            manager.verify_identity(secret_token)
    finally:
        manager.close()
    captured = capsys.readouterr()
    assert secret_token not in captured.out
    assert secret_token not in captured.err
