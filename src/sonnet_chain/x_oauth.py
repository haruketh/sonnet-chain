from __future__ import annotations

import base64
import fcntl
import getpass
import hashlib
import hmac
import json
import os
import secrets
import stat
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx

from .secure_files import atomic_write_private, read_private_file, require_private_directory

AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
USERS_ME_URL = "https://api.x.com/2/users/me"
SCOPES = ("tweet.read", "tweet.write", "users.read", "offline.access")
DEFAULT_REDIRECT = "http://127.0.0.1:8765/callback"


class XOAuthError(RuntimeError):
    pass


class XAuthRequired(XOAuthError):
    pass


@dataclass(frozen=True)
class XIdentity:
    username: str
    user_id: str


def generate_state() -> str:
    return secrets.token_urlsafe(32)


def generate_code_verifier() -> str:
    verifier = secrets.token_urlsafe(64).rstrip("=")
    if not 43 <= len(verifier) <= 128:
        raise RuntimeError("generated PKCE verifier has invalid length")
    return verifier


def s256_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")


def authorization_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    if redirect_uri != DEFAULT_REDIRECT:
        raise XOAuthError("redirect URI must exactly match the configured Sonnet callback")
    query = urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES), "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256",
    }, quote_via=quote)
    return f"{AUTHORIZE_URL}?{query}"


def validate_callback(target: str, expected_state: str) -> str:
    parsed = urlparse(target)
    if parsed.path != "/callback":
        raise XOAuthError("unexpected OAuth callback path")
    values = parse_qs(parsed.query, keep_blank_values=True)
    state = values.get("state", [""])[0]
    code = values.get("code", [""])[0]
    if not state or not hmac.compare_digest(state, expected_state):
        raise XOAuthError("OAuth state mismatch")
    if not code:
        raise XOAuthError("OAuth callback did not contain a code")
    return code


def wait_for_callback(expected_state: str, timeout: int = 180, browser_url: str | None = None) -> str:
    result: dict[str, str | Exception] = {}
    done = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            try:
                result["code"] = validate_callback(self.path, expected_state)
                body = b"Saruku Sonnet X authorization received. You can close this window."
                status = 200
            except XOAuthError as exc:
                result["error"] = exc
                body = b"Saruku Sonnet X authorization was rejected."
                status = 400
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            done.set()

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", 8765), Handler)
    server.timeout = timeout
    try:
        if browser_url is not None and not webbrowser.open(browser_url):
            raise XOAuthError("browser could not be opened")
        server.handle_request()
    finally:
        server.server_close()
    if not done.is_set():
        raise XOAuthError("OAuth callback timed out")
    if "error" in result:
        raise result["error"]  # type: ignore[misc]
    return str(result["code"])


def _read_text_secret(path: Path | None, label: str) -> str:
    if path is None:
        raise XOAuthError(f"{label} file path is not configured")
    try:
        value = read_private_file(path, 4096).decode("ascii").strip()
    except (OSError, UnicodeDecodeError, RuntimeError) as exc:
        raise XOAuthError(f"{label} file is unavailable or unsafe") from exc
    if not value:
        raise XOAuthError(f"{label} file is empty")
    return value


def parse_token_response(data: Any, now: float | None = None, prior_refresh: str | None = None) -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
        raise XOAuthError("X token response is invalid")
    expires_in = data.get("expires_in")
    if not isinstance(expires_in, (int, float)) or expires_in <= 0:
        raise XOAuthError("X token response has invalid expiry")
    refresh = data.get("refresh_token", prior_refresh)
    if not isinstance(refresh, str) or not refresh:
        raise XOAuthError("X token response did not provide a refresh token")
    scope = data.get("scope", "")
    if not isinstance(scope, str) or not set(SCOPES) <= set(scope.split()):
        raise XOAuthError("X token response is missing required scopes")
    return {
        "access_token": data["access_token"], "refresh_token": refresh,
        "token_type": str(data.get("token_type", "bearer")).lower(),
        "scope": scope, "expires_at": int((time.time() if now is None else now) + expires_in),
    }


class XTokenManager:
    def __init__(self, client_id_file: Path | None, client_secret_file: Path | None,
                 token_file: Path | None, expected_username: str, client: httpx.Client | None = None,
                 refresh_threshold: int = 300):
        self.client_id_file = client_id_file
        self.client_secret_file = client_secret_file
        self.token_file = token_file
        self.expected_username = expected_username.casefold()
        self.identity_file = token_file.with_name("identity.json") if token_file else None
        self.lock_file = token_file.with_name("refresh.lock") if token_file else None
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)
        self.refresh_threshold = refresh_threshold

    def close(self) -> None:
        self.client.close()

    def _credentials(self) -> tuple[str, str]:
        return (_read_text_secret(self.client_id_file, "X client ID"),
                _read_text_secret(self.client_secret_file, "X client secret"))

    def _load(self) -> dict[str, Any]:
        if self.token_file is None:
            raise XAuthRequired("SONNET_X_TOKEN_FILE is not configured")
        try:
            value = json.loads(read_private_file(self.token_file, 32_768))
        except (OSError, RuntimeError, json.JSONDecodeError) as exc:
            raise XAuthRequired("Sonnet X authorization is required") from exc
        if not isinstance(value, dict):
            raise XAuthRequired("Sonnet X token file is invalid")
        return value

    def _save(self, value: dict[str, Any]) -> None:
        if self.token_file is None:
            raise XOAuthError("X token file path is not configured")
        atomic_write_private(self.token_file, json.dumps(value, separators=(",", ":")).encode())

    def exchange_code(self, code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
        client_id, client_secret = self._credentials()
        response = self.client.post(TOKEN_URL, auth=httpx.BasicAuth(client_id, client_secret), data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": redirect_uri, "code_verifier": verifier,
        })
        if response.status_code != 200:
            raise XOAuthError(f"X token exchange failed with HTTP {response.status_code}")
        try:
            return parse_token_response(response.json())
        except (ValueError, json.JSONDecodeError) as exc:
            raise XOAuthError("X token exchange returned invalid JSON") from exc

    def verify_identity(self, access_token: str) -> XIdentity:
        response = self.client.get(USERS_ME_URL, headers={"Authorization": f"Bearer {access_token}"})
        if response.status_code != 200:
            raise XAuthRequired(f"X identity check failed with HTTP {response.status_code}")
        try:
            data = response.json()["data"]
            identity = XIdentity(str(data["username"]), str(data["id"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise XOAuthError("X identity response is invalid") from exc
        if identity.username.casefold() != self.expected_username:
            raise XOAuthError("authorized X account does not match expected username")
        if self.identity_file and self.identity_file.exists():
            saved = json.loads(read_private_file(self.identity_file, 4096))
            if saved.get("user_id") != identity.user_id:
                raise XOAuthError("authorized X user ID changed")
        return identity

    def save_identity(self, identity: XIdentity) -> None:
        if self.identity_file is None:
            raise XOAuthError("X identity file path is unavailable")
        atomic_write_private(self.identity_file, json.dumps({
            "username": identity.username, "user_id": identity.user_id,
        }, separators=(",", ":")).encode())

    def _refresh_locked(self, expected_access: str | None = None) -> str:
        current = self._load()
        if expected_access is not None and current.get("access_token") != expected_access:
            return str(current["access_token"])
        client_id, client_secret = self._credentials()
        response = self.client.post(TOKEN_URL, auth=httpx.BasicAuth(client_id, client_secret), data={
            "grant_type": "refresh_token", "refresh_token": current.get("refresh_token"),
        })
        if response.status_code != 200:
            raise XAuthRequired(f"X token refresh failed with HTTP {response.status_code}")
        updated = parse_token_response(response.json(), prior_refresh=current.get("refresh_token"))
        self._save(updated)
        identity = self.verify_identity(updated["access_token"])
        self.save_identity(identity)
        return str(updated["access_token"])

    def refresh(self, expected_access: str | None = None) -> str:
        if self.lock_file is None:
            raise XAuthRequired("X refresh lock path is unavailable")
        require_private_directory(self.lock_file.parent)
        fd = os.open(self.lock_file, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            return self._refresh_locked(expected_access)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def get_valid_access_token(self, now: float | None = None) -> str:
        current = self._load()
        expiry = current.get("expires_at")
        current_time = time.time() if now is None else now
        if isinstance(expiry, (int, float)) and expiry > current_time + self.refresh_threshold:
            return str(current["access_token"])
        return self.refresh(expected_access=str(current.get("access_token", "")))

    def is_current(self, access_token: str) -> bool:
        return hmac.compare_digest(str(self._load().get("access_token", "")), access_token)

    def status(self) -> dict[str, Any]:
        def safe_file(path: Path | None) -> bool:
            if path is None:
                return False
            try:
                info = os.lstat(path)
            except OSError:
                return False
            return (stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                    and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600)

        configured = safe_file(self.client_id_file) and safe_file(self.client_secret_file) and self.token_file is not None
        try:
            token = self._load()
            identity = json.loads(read_private_file(self.identity_file, 4096)) if self.identity_file else {}
            authenticated = (identity.get("username", "").casefold() == self.expected_username
                             and isinstance(identity.get("user_id"), str) and bool(identity["user_id"]))
            return {
                "configured": bool(configured), "authenticated": authenticated,
                "username": identity.get("username"), "user_id": identity.get("user_id"),
                "refresh_available": bool(token.get("refresh_token")),
                "access_token_valid": isinstance(token.get("expires_at"), (int, float)) and token["expires_at"] > time.time(),
            }
        except (OSError, RuntimeError, json.JSONDecodeError):
            return {"configured": bool(configured), "authenticated": False, "username": None,
                    "user_id": None, "refresh_available": False, "access_token_valid": False}


def configure_credentials(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=False, exist_ok=True)
    os.chmod(directory, 0o700)
    require_private_directory(directory)
    client_id = getpass.getpass("Sonnet X Client ID (hidden): ").strip()
    client_secret = getpass.getpass("Sonnet X Client Secret (hidden): ").strip()
    if not client_id or not client_secret:
        raise XOAuthError("client credentials may not be empty")
    atomic_write_private(directory / "client_id", client_id.encode("ascii"))
    atomic_write_private(directory / "client_secret", client_secret.encode("ascii"))


def authorize(manager: XTokenManager, redirect_uri: str) -> XIdentity:
    client_id, _ = manager._credentials()
    state = generate_state()
    verifier = generate_code_verifier()
    url = authorization_url(client_id, redirect_uri, state, s256_challenge(verifier))
    print("Opening X authorization in the browser for @{}...".format(manager.expected_username))
    code = wait_for_callback(state, browser_url=url)
    tokens = manager.exchange_code(code, verifier, redirect_uri)
    identity = manager.verify_identity(tokens["access_token"])
    manager._save(tokens)
    manager.save_identity(identity)
    return identity
