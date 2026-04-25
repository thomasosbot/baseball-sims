"""Resolve player names to MLBAM IDs via MLB Stats API."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import requests

CACHE_PATH = Path(__file__).parent.parent.parent / "data" / "processed" / "name_to_mlbam.json"


def _load_disk_cache() -> dict:
    if CACHE_PATH.exists():
        with open(CACHE_PATH) as f:
            return json.load(f)
    return {}


def _save_disk_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f)


_disk_cache = _load_disk_cache()


@lru_cache(maxsize=2000)
def resolve_id(name: str) -> int | None:
    """Return MLBAM ID for a player name, or None if not found."""
    if not name:
        return None
    key = name.strip()
    if key in _disk_cache:
        return _disk_cache[key]

    try:
        search = key.replace(" ", "+")
        resp = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/search?names={search}",
            timeout=5,
        )
        if resp.ok:
            people = resp.json().get("people", [])
            if people:
                pid = int(people[0]["id"])
                _disk_cache[key] = pid
                _save_disk_cache(_disk_cache)
                return pid
    except Exception:
        pass

    _disk_cache[key] = None
    _save_disk_cache(_disk_cache)
    return None


def resolve_batch(names: list[str]) -> dict[str, int | None]:
    return {n: resolve_id(n) for n in names}
