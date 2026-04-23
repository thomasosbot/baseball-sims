"""Prototype: generate LLM narratives for yesterday's picks and print side-by-side.

Pulls 2026-04-22 picks from origin/main (local is behind), builds a brief per
pick with Statcast rollups, calls Claude Haiku, prints.
"""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.betting.narrative import generate_narrative, build_brief


def load_picks_from_git(date: str) -> dict:
    out = subprocess.check_output(
        ["git", "show", f"origin/main:data/daily/{date}.json"],
        cwd=Path(__file__).parent.parent,
    )
    return json.loads(out)


def main():
    date = "2026-04-22"
    data = load_picks_from_git(date)
    picks = data.get("picks", [])
    games_by_teams = {(g["away"], g["home"]): g for g in data.get("games", [])}

    def game_for_pick(p):
        for (a, h), g in games_by_teams.items():
            if p["team"] in (a, h) and p["opponent"] in (a, h):
                return g
        return None

    print(f"\n=== Generating narratives for {date} ({len(picks)} picks) ===\n")

    def run_one(p):
        g = game_for_pick(p)
        if not g:
            return p, None, None
        brief = build_brief(p, g)
        narrative = generate_narrative(p, g)
        return p, brief, narrative

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(run_one, picks))

    for p, brief, narrative in results:
        print("=" * 80)
        print(f"PICK: {p['pick']} ({p['type']}) @ {p['odds']} | edge {p['edge_pct']}%")
        print(f"Current (rule-based): {p.get('explanation','')}")
        print()
        print("--- BRIEF FED TO LLM ---")
        print(brief if brief else "(no brief)")
        print()
        print("--- LLM NARRATIVE ---")
        print(narrative if narrative else "(failed)")
        print()


if __name__ == "__main__":
    main()
