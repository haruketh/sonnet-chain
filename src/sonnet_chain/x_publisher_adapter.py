from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import httpx

from .publisher import weighted_length
from .secure_files import read_private_file

X_POST_URL = "https://api.x.com/2/tweets"


class XPublisherError(RuntimeError):
    pass


class XPublisherAdapter:
    def __init__(self, token_file: Path | None, state_db: Path, client: httpx.Client | None = None):
        self.token_file = token_file
        self.state_db = state_db
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)

    def close(self) -> None:
        self.client.close()

    def _token(self) -> str:
        if self.token_file is None:
            raise XPublisherError("SONNET_X_TOKEN_FILE is not set")
        try:
            data = json.loads(read_private_file(self.token_file, 32_768))
            token = data["access_token"]
        except (OSError, RuntimeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise XPublisherError("X credential file is unavailable or unsafe") from exc
        if not isinstance(token, str) or not token:
            raise XPublisherError("X credential file has no access token")
        return token

    @staticmethod
    def _validate(posts: Any) -> list[str]:
        if not isinstance(posts, list) or not posts or not all(isinstance(x, str) and x for x in posts):
            raise XPublisherError("publisher input must contain nonempty post strings")
        if len(posts) > 14 or any(weighted_length(x) > 280 for x in posts):
            raise XPublisherError("publisher input exceeds thread limits")
        return posts

    def publish(self, posts: Any, dry_run: bool) -> dict[str, Any]:
        posts = self._validate(posts)
        if dry_run:
            return {"dry_run": True, "post_ids": [f"dry-run-{i + 1}" for i in range(len(posts))], "posts": posts}
        token = self._token()
        digest = hashlib.sha256(json.dumps(posts, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        self.state_db.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.state_db)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS publication_posts ("
                "poem_hash TEXT NOT NULL, part INTEGER NOT NULL, text_hash TEXT NOT NULL, "
                "status TEXT NOT NULL, post_id TEXT, PRIMARY KEY(poem_hash,part))"
            )
            ids: list[str] = []
            parent = None
            for index, text in enumerate(posts):
                text_hash = hashlib.sha256(text.encode()).hexdigest()
                row = db.execute(
                    "SELECT text_hash,status,post_id FROM publication_posts WHERE poem_hash=? AND part=?",
                    (digest, index),
                ).fetchone()
                if row:
                    if row[0] != text_hash:
                        raise XPublisherError("stored publication text mismatch")
                    if row[1] == "pending":
                        raise XPublisherError("ambiguous prior X request; refusing duplicate post")
                    if row[1] == "posted" and row[2]:
                        parent = row[2]
                        ids.append(parent)
                        continue
                with db:
                    db.execute(
                        "INSERT INTO publication_posts VALUES(?,?,?,'pending',NULL) "
                        "ON CONFLICT(poem_hash,part) DO UPDATE SET status='pending'",
                        (digest, index, text_hash),
                    )
                body: dict[str, Any] = {"text": text}
                if parent:
                    body["reply"] = {"in_reply_to_tweet_id": parent}
                try:
                    response = self.client.post(
                        X_POST_URL, headers={"Authorization": f"Bearer {token}"}, json=body
                    )
                    if response.status_code >= 400:
                        with db:
                            db.execute(
                                "UPDATE publication_posts SET status='rejected' WHERE poem_hash=? AND part=?",
                                (digest, index),
                            )
                        raise XPublisherError(f"X rejected the post with HTTP {response.status_code}")
                    post_id = response.json()["data"]["id"]
                except XPublisherError:
                    raise
                except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                    raise XPublisherError("X post failed or response was ambiguous") from exc
                if not isinstance(post_id, str) or not post_id:
                    raise XPublisherError("X response did not contain a post ID")
                with db:
                    db.execute(
                        "UPDATE publication_posts SET status='posted',post_id=? WHERE poem_hash=? AND part=?",
                        (post_id, digest, index),
                    )
                parent = post_id
                ids.append(post_id)
            return {"dry_run": False, "post_ids": ids}
        finally:
            db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sonnet-chain-x-publisher")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    from .config import Config

    cfg = Config.from_env()
    try:
        payload = json.loads(sys.stdin.read(65_537))
        if not isinstance(payload, dict) or set(payload) != {"posts"}:
            raise XPublisherError("publisher accepts only a posts payload")
        adapter = XPublisherAdapter(cfg.x_token_file, cfg.x_publish_state_db)
        try:
            result = adapter.publish(payload["posts"], args.dry_run)
        finally:
            adapter.close()
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (OSError, json.JSONDecodeError, XPublisherError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
