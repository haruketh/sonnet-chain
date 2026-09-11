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
        params = {"format": "json", "since": since, "limit": 200}
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

    def read_page(self, room: str, since: int = 0, wait: int = 0) -> tuple[list[RoomRecord], int | None]:
        params = {"format": "json", "since": since, "limit": 200}
        if wait:
            params["wait"] = max(0, min(10, wait))
        r = self.client.get(f"{self.base_url}/r/{room}", params=params)
        r.raise_for_status()
        data = r.json()
        first_seq = data.get("first_seq") if isinstance(data, dict) else None
        if isinstance(first_seq, int) and first_seq > since + 1:
            export = self.client.get(f"{self.base_url}/r/{room}/export")
            export.raise_for_status()
            items = []
            for line in export.text.splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict) and int(item.get("seq", 0) or 0) > since:
                    items.append(item)
            data = {"messages": items, "generation": int(export.headers.get("X-Room-Generation", "0") or 0)}
        records = []
        for item in _records_from_json(data):
            seq = int(item.get("seq", 0) or 0)
            text = item.get("text", "")
            sender = item.get("from")
            if isinstance(text, str):
                records.append(RoomRecord(seq, sender if isinstance(sender, str) else None, text, item))
        generation = data.get("generation") if isinstance(data, dict) else None
        return records, generation if isinstance(generation, int) else None

    def owner_note(self, room: str) -> Any | None:
        r = self.client.get(f"{self.base_url}/kv/room-owners/{room}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        try:
            return r.json()
        except json.JSONDecodeError:
            return r.text

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
