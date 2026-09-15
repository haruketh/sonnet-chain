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
from .x_oauth import XAuthRequired, XOAuthError, XTokenManager

X_POST_URL = "https://api.x.com/2/tweets"


class XPublisherError(RuntimeError):
    pass


class XPublisherDefinitelyNotSent(XPublisherError):
    pass


class XPublisherAdapter:
    def __init__(self, token_manager: XTokenManager, state_db: Path, client: httpx.Client | None = None):
        self.token_manager = token_manager
        self.state_db = state_db
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _validate(posts: Any) -> list[str]:
        if not isinstance(posts, list) or not posts or not all(isinstance(x, str) and x for x in posts):
            raise XPublisherError("publisher input must contain nonempty post strings")
        if len(posts) > 14 or any(weighted_length(x) > 280 for x in posts):
            raise XPublisherError("publisher input exceeds thread limits")
        return posts

    def publish(self, posts: Any, dry_run: bool, reply_to: str | None = None) -> dict[str, Any]:
        posts = self._validate(posts)
        if dry_run:
            return {"dry_run": True, "post_ids": [f"dry-run-{i + 1}" for i in range(len(posts))], "posts": posts}
        try:
            token = self.token_manager.get_valid_access_token()
            identity = self.token_manager.verify_identity(token)
        except XOAuthError as exc:
            raise XAuthRequired("X_AUTH_REQUIRED: token or identity validation failed") from exc
        # The root parent is part of the publication identity.  Reusing a post
        # created as a reply to another parent would corrupt thread lineage.
        identity_input = {"posts": posts, "reply_to": reply_to}
        digest = hashlib.sha256(
            json.dumps(identity_input, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
        self.state_db.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.state_db)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS publication_posts ("
                "poem_hash TEXT NOT NULL, part INTEGER NOT NULL, text_hash TEXT NOT NULL, "
                "status TEXT NOT NULL, post_id TEXT, returned_text TEXT, author_id TEXT, "
                "parent_post_id TEXT, PRIMARY KEY(poem_hash,part))"
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(publication_posts)")}
            for name in ("returned_text", "author_id", "parent_post_id"):
                if name not in columns:
                    db.execute(f"ALTER TABLE publication_posts ADD COLUMN {name} TEXT")
            ids: list[str] = []
            returned_posts: list[dict[str, Any]] = []
            parent = reply_to
            for index, text in enumerate(posts):
                text_hash = hashlib.sha256(text.encode()).hexdigest()
                row = db.execute(
                    "SELECT text_hash,status,post_id,returned_text,author_id,parent_post_id "
                    "FROM publication_posts WHERE poem_hash=? AND part=?",
                    (digest, index),
                ).fetchone()
                if row:
                    if row[0] != text_hash:
                        raise XPublisherError("stored publication text mismatch")
                    if row[1] == "pending":
                        raise XPublisherError("ambiguous prior X request; refusing duplicate post")
                    if row[1] == "posted" and row[2]:
                        if not isinstance(row[3], str) or not isinstance(row[4], str):
                            raise XPublisherError("stored publication lacks strong response evidence")
                        parent = row[2]
                        ids.append(parent)
                        returned_posts.append({"id": row[2], "text": row[3],
                                               "author_id": row[4], "parent_post_id": row[5]})
                        continue
                with db:
                    db.execute(
                        "INSERT INTO publication_posts(poem_hash,part,text_hash,status,post_id) "
                        "VALUES(?,?,?,'pending',NULL) "
                        "ON CONFLICT(poem_hash,part) DO UPDATE SET status='pending'",
                        (digest, index, text_hash),
                    )
                body: dict[str, Any] = {"text": text}
                if parent:
                    body["reply"] = {"in_reply_to_tweet_id": parent}
                try:
                    response = self.client.post(X_POST_URL, headers={"Authorization": f"Bearer {token}"}, json=body)
                    if response.status_code == 401:
                        try:
                            token = self.token_manager.refresh(token) if self.token_manager.is_current(token) else self.token_manager.get_valid_access_token()
                        except XOAuthError as exc:
                            with db:
                                db.execute(
                                    "UPDATE publication_posts SET status='rejected' WHERE poem_hash=? AND part=?",
                                    (digest, index),
                                )
                            raise XAuthRequired("X_AUTH_REQUIRED: token refresh or identity validation failed") from exc
                        try:
                            identity = self.token_manager.verify_identity(token)
                        except XOAuthError as exc:
                            with db:
                                db.execute(
                                    "UPDATE publication_posts SET status='rejected' WHERE poem_hash=? AND part=?",
                                    (digest, index),
                                )
                            raise XAuthRequired("X_AUTH_REQUIRED: refreshed identity validation failed") from exc
                        response = self.client.post(X_POST_URL, headers={"Authorization": f"Bearer {token}"}, json=body)
                        if response.status_code == 401:
                            with db:
                                db.execute(
                                    "UPDATE publication_posts SET status='rejected' WHERE poem_hash=? AND part=?",
                                    (digest, index),
                                )
                            raise XAuthRequired("X_AUTH_REQUIRED: repeated HTTP 401")
                    if response.status_code >= 400:
                        with db:
                            db.execute(
                                "UPDATE publication_posts SET status='rejected' WHERE poem_hash=? AND part=?",
                                (digest, index),
                            )
                        if response.status_code < 500:
                            raise XPublisherDefinitelyNotSent(
                                f"X rejected the post with HTTP {response.status_code}"
                            )
                        raise XPublisherError(
                            f"X server failed with ambiguous HTTP {response.status_code}"
                        )
                    data = response.json()["data"]
                    post_id = data["id"]
                    returned_text = data["text"]
                except (XPublisherError, XAuthRequired):
                    raise
                except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                    raise XPublisherError("X post failed or response was ambiguous") from exc
                if not isinstance(post_id, str) or not post_id or not isinstance(returned_text, str):
                    raise XPublisherError("X response did not contain strong post evidence")
                author_id = identity.user_id
                with db:
                    db.execute(
                        "UPDATE publication_posts SET status='posted',post_id=?,returned_text=?,"
                        "author_id=?,parent_post_id=? WHERE poem_hash=? AND part=?",
                        (post_id, returned_text, author_id, parent, digest, index),
                    )
                returned_posts.append({"id": post_id, "text": returned_text,
                                       "author_id": author_id, "parent_post_id": parent})
                parent = post_id
                ids.append(post_id)
            return {"dry_run": False, "post_ids": ids, "posts": returned_posts}
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
        if not isinstance(payload, dict) or not set(payload) <= {"posts", "reply_to"} or "posts" not in payload:
            raise XPublisherError("publisher accepts posts and optional reply_to only")
        reply_to = payload.get("reply_to")
        if reply_to is not None and (not isinstance(reply_to, str) or not reply_to):
            raise XPublisherError("reply_to must be a nonempty post ID")
        manager = XTokenManager(
            cfg.x_client_id_file, cfg.x_client_secret_file, cfg.x_token_file,
            cfg.x_expected_username,
        )
        adapter = XPublisherAdapter(manager, cfg.x_publish_state_db)
        try:
            result = adapter.publish(payload["posts"], args.dry_run, reply_to)
        finally:
            adapter.close()
            manager.close()
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except XAuthRequired as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    except XPublisherDefinitelyNotSent as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 4
    except (OSError, json.JSONDecodeError, XPublisherError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
