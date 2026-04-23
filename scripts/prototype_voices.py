"""Side-by-side voice comparison: Opus + snark vs Opus + Berman-hint.

Runs 3 picks from yesterday so we can eyeball which voice lands.
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

from src.betting.narrative import generate_narrative

PICK_KEYS = ["SFG ML", "MIN ML", "Athletics ML"]  # variety: HR-heavy fade, AL matchup, underdog
MODEL = "claude-opus-4-7"


def load_picks_from_git(date: str) -> dict:
    out = subprocess.check_output(
        ["git", "show", f"origin/main:data/daily/{date}.json"],
        cwd=Path(__file__).parent.parent,
    )
    return json.loads(out)


def main():
    date = "2026-04-22"
    data = load_picks_from_git(date)
    picks = [p for p in data.get("picks", []) if p["pick"] in PICK_KEYS]
    games_by_teams = {(g["away"], g["home"]): g for g in data.get("games", [])}

    def game_for_pick(p):
        for (a, h), g in games_by_teams.items():
            if p["team"] in (a, h) and p["opponent"] in (a, h):
                return g
        return None

    def run_voice(args):
        p, voice = args
        g = game_for_pick(p)
        return p, voice, generate_narrative(p, g, model=MODEL, voice=voice)

    jobs = [(p, v) for p in picks for v in ("snark", "berman")]
    print(f"Running {len(jobs)} calls (Opus, 2 voices × {len(picks)} picks)...\n")

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(run_voice, jobs))

    by_pick = {}
    for p, voice, text in results:
        by_pick.setdefault(p["pick"], {})[voice] = text

    for key in PICK_KEYS:
        if key not in by_pick:
            continue
        print("=" * 80)
        print(f"PICK: {key}")
        print("=" * 80)
        print("\n--- SNARK (dry wit) ---\n")
        print(by_pick[key].get("snark") or "(failed)")
        print("\n--- BERMAN-HINT (light hype/wordplay) ---\n")
        print(by_pick[key].get("berman") or "(failed)")
        print()


if __name__ == "__main__":
    main()
