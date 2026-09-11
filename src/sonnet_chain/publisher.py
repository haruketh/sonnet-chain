from __future__ import annotations

import json
import re
import shlex
import subprocess


class PublisherUnavailable(RuntimeError):
    pass


class PublisherAuthRequired(PublisherUnavailable):
    pass


def canonical_poem(lines: list[str]) -> str:
    if len(lines) != 14 or any(not line.strip() for line in lines):
        raise ValueError("a complete poem requires 14 nonempty lines")
    groups = (lines[:4], lines[4:8], lines[8:12], lines[12:])
    return "\n\n".join("\n".join(" ".join(line.split()) for line in group) for group in groups)


def weighted_length(text: str) -> int:
    """Conservative X weighting: URLs are 23, non-ASCII code points are 2."""
    url = re.compile(r"https?://\S+")
    total = 0
    cursor = 0
    for match in url.finditer(text):
        total += sum(1 if ord(c) < 0x1100 else 2 for c in text[cursor:match.start()]) + 23
        cursor = match.end()
    return total + sum(1 if ord(c) < 0x1100 else 2 for c in text[cursor:])


def split_posts(poem: str, limit: int = 280) -> list[str]:
    posts: list[str] = []
    current = ""
    for line in poem.splitlines():
        candidate = line if not current else current + "\n" + line
        if weighted_length(candidate) > limit and current:
            posts.append(current)
            current = line
        else:
            current = candidate
        if weighted_length(current) > limit:
            raise ValueError("one poem line exceeds publication limit")
    if current:
        posts.append(current)
    return posts


class CommandPublisher:
    def __init__(self, command: str | None):
        self.argv = shlex.split(command) if command else []

    def publish(self, poem: str) -> list[str]:
        if not self.argv:
            raise PublisherUnavailable("SONNET_X_PUBLISH_CMD is not set")
        proc = subprocess.run(
            self.argv, input=json.dumps({"posts": split_posts(poem)}), text=True,
            capture_output=True, check=False, timeout=120,
        )
        if proc.returncode == 3:
            raise PublisherAuthRequired("X_AUTH_REQUIRED")
        if proc.returncode:
            raise PublisherUnavailable("publisher command failed")
        try:
            result = json.loads(proc.stdout)
            if result.get("dry_run") is True:
                raise PublisherUnavailable("publisher adapter remained in dry-run mode")
            post_ids = result["post_ids"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise PublisherUnavailable("publisher returned invalid JSON") from exc
        if not isinstance(post_ids, list) or not post_ids or not all(isinstance(x, str) and x for x in post_ids):
            raise PublisherUnavailable("publisher returned invalid post IDs")
        return post_ids
