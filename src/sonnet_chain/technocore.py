from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator

import httpx

from .signing import Signer


@dataclass
class RoomRecord:
    seq: int
    sender: str | None
    text: str
    raw: dict[str, Any]


def _records_from_json(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("messages", "records", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


class Technocore:
    def __init__(self, base_url: str, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def read(self, room: str, since: int = 0, wait: int = 0) -> list[RoomRecord]:
        params = {"format": "json", "since": since}
        if wait:
            params["wait"] = max(0, min(10, wait))
        r = self.client.get(f"{self.base_url}/r/{room}", params=params)
        r.raise_for_status()
        data = r.json()
        out = []
        for item in _records_from_json(data):
            seq = int(item.get("seq", 0) or 0)
            sender = item.get("from")
            text = item.get("text", "")
            if isinstance(text, str):
                out.append(RoomRecord(seq=seq, sender=sender if isinstance(sender, str) else None, text=text, raw=item))
        return out

    def watch(self, room: str, since: int = 0) -> Iterator[RoomRecord]:
        cursor = since
        while True:
            records = self.read(room, since=cursor, wait=10)
            for record in records:
                if record.seq > cursor:
                    cursor = record.seq
                yield record

    def post_signed(self, room: str, text: str, signer: Signer) -> httpx.Response:
        nonce = signer.nonce()
        stored, sig = signer.sign_room(room, nonce, text)
        body = {
            "did": signer.did,
            "sig": sig,
            "nonce": nonce,
            "text": stored,
        }
        r = self.client.post(f"{self.base_url}/r/{room}", json=body)
        r.raise_for_status()
        return r
