from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import SARUKU_DID
from .official import check_word

LETTERS = set("abcdefghijklmnopqrstuvwxyz")
WORD = re.compile(r"^([A-Za-z]+(?:'[A-Za-z]+)?)[,.;:!?]?$")


def did_letters(did: str) -> set[str]:
    return {c for c in did.casefold() if c in LETTERS}


def missing_letters(did: str) -> set[str]:
    return LETTERS - did_letters(did)


def locally_compatible(did: str, token: str) -> bool:
    match = WORD.fullmatch(token)
    return bool(match and set(match.group(1).casefold().replace("'", "")) <= did_letters(did))


@dataclass
class PoemState:
    version: int
    state_hash: str
    words: list[str] = field(default_factory=list)
    line_syllables: int = 0
    line_number: int = 1
    previous_contributor: str | None = None


def validate_candidate(
    candidate: str,
    observed_version: int,
    current: PoemState,
    official_dir: Path,
    did: str = SARUKU_DID,
) -> dict:
    if observed_version != current.version:
        raise ValueError("stale poem state")
    if current.previous_contributor == did:
        raise ValueError("previous contributor may not propose")
    if not locally_compatible(did, candidate):
        raise ValueError("word is incompatible with contributor DID")
    result = check_word(official_dir, did, candidate)
    syllables = result.get("syllables")
    if not isinstance(syllables, int) or current.line_syllables + syllables > 10:
        raise ValueError("word exceeds remaining syllable budget")
    return result


def build_word_index(dictionary: Path, cache: Path, did: str = SARUKU_DID) -> int:
    """Build a small derived index; the official checker remains authoritative."""
    entries: dict[str, list[list[str]]] = {}
    for raw in dictionary.read_text(encoding="utf-8").splitlines():
        if not raw or raw.startswith(";;; "):
            continue
        parts = raw.split()
        word = re.sub(r"\(\d+\)$", "", parts[0]).lower()
        if not locally_compatible(did, word):
            continue
        phones = parts[1:]
        syllables = sum(any(ch.isdigit() for ch in phone) for phone in phones)
        stress = "".join(ch for phone in phones for ch in phone if ch.isdigit())
        rhyme = " ".join(phones[next((i for i in range(len(phones) - 1, -1, -1) if any(ch in "12" for ch in phones[i])), 0):])
        entries.setdefault(word, []).append([str(syllables), stress, rhyme])
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(entries, separators=(",", ":")), encoding="utf-8")
    return len(entries)
