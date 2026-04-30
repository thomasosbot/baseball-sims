"""
Apply the latest BHQ in-season YTD+projection snapshot to the active state.

Run this AFTER dropping a fresh `hitter_ytd_proj_mlb_YYYY_MM_DD.csv` and
`pitcher_ytd_proj_mlb_YYYY_MM_DD.csv` pair into `data/raw/bhq/`. The script
adds BHQ's projection-derived rates as additional pseudo-PAs into the
existing `CumulativeStats`, nudging it toward the latest BHQ view without
wiping the real 2026 PA data we've already accumulated.

Usage:
    python scripts/refresh_bhq.py                        # latest state, latest BHQ
    python scripts/refresh_bhq.py --weight 200           # default 150 effective PAs
    python scripts/refresh_bhq.py --dry-run              # show diff, don't save

Default weight (150 effective PAs) is calibrated so that one refresh per
month adds a meaningful BHQ signal without dwarfing real in-season PA
accumulation. Adjust if you want stronger or weaker BHQ pull.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.bhq_inseason import (
    find_latest_snapshot,
    load_hitter_proj_rates,
    load_pitcher_proj_rates,
    load_speed_scores,
)
from src.data.cumulative import OUTCOMES
from src.data.state import load_state, save_state


def _add_pseudo_pas_batter(b_dict: dict, rates: dict, weight: float):
    """Add `weight` pseudo-PAs at given outcome rates to a batter cumulative dict."""
    b_dict["total"] = b_dict.get("total", 0.0) + weight
    for o in OUTCOMES:
        b_dict[o] = b_dict.get(o, 0.0) + rates[o] * weight

    # Platoon splits — same fractions as init_from_marcel (62% vs RHP, 38% vs LHP)
    for hand, frac in (("L", 0.38), ("R", 0.62)):
        split_w = weight * frac
        b_dict[f"pa_vs{hand}"] = b_dict.get(f"pa_vs{hand}", 0.0) + split_w
        for o in OUTCOMES:
            key = f"{o}_vs{hand}"
            b_dict[key] = b_dict.get(key, 0.0) + rates[o] * split_w


def _add_pseudo_pas_pitcher(p_dict: dict, rates: dict, weight: float):
    """Add `weight` pseudo-BFs at given outcome rates to a pitcher cumulative dict."""
    p_dict["total"] = p_dict.get("total", 0.0) + weight
    for o in OUTCOMES:
        p_dict[o] = p_dict.get(o, 0.0) + rates[o] * weight

    # 56% vs RHB, 44% vs LHB — same as init_from_marcel
    for hand, frac in (("L", 0.44), ("R", 0.56)):
        split_w = weight * frac
        p_dict[f"bf_vs{hand}"] = p_dict.get(f"bf_vs{hand}", 0.0) + split_w
        for o in OUTCOMES:
            key = f"{o}_vs{hand}"
            p_dict[key] = p_dict.get(key, 0.0) + rates[o] * split_w


def main(weight: float = 150.0, dry_run: bool = False):
    print(f"\n{'=' * 60}")
    print(f"  BHQ in-season refresh")
    print(f"  Effective PAs per player: {weight}")
    print(f"  Dry run: {dry_run}")
    print(f"{'=' * 60}\n")

    # 1. Load latest state
    state = load_state()
    if state is None:
        print("  ERROR: no state file found in data/state/. Run init_season.py first.")
        sys.exit(1)
    cumulative = state["cumulative"]
    elo = state["elo"]
    batter_speeds = state["batter_speeds"]
    bankroll = state["bankroll"]
    date = state["date"]
    print(f"    {cumulative.num_batters} batters, {cumulative.num_pitchers} pitchers")
    print(f"    state date: {date}, bankroll: ${bankroll:,.2f}")

    # 2. Load latest BHQ YTD+Proj snapshot
    hitter_file = find_latest_snapshot("hitter")
    pitcher_file = find_latest_snapshot("pitcher")
    if hitter_file is None or pitcher_file is None:
        print("\n  ERROR: no in-season BHQ files found.")
        print(f"  Expected: data/raw/bhq/hitter_ytd_proj_mlb_*.csv (and pitcher_*)")
        sys.exit(1)
    print(f"\n  Loading BHQ snapshot:")
    print(f"    hitters:  {hitter_file.name}")
    print(f"    pitchers: {pitcher_file.name}")

    hitter_rates = load_hitter_proj_rates(hitter_file)
    pitcher_rates = load_pitcher_proj_rates(pitcher_file)
    proj_speeds = load_speed_scores(hitter_file)
    print(f"    {len(hitter_rates)} hitter rates, {len(pitcher_rates)} pitcher rates, "
          f"{len(proj_speeds)} speed scores")

    # 3. Apply: add fresh BHQ-Proj rates as additional pseudo-PAs
    h_updated = h_seeded_new = 0
    for pid, rates_data in hitter_rates.items():
        is_new = pid not in cumulative._batters  # noqa: SLF001
        b_dict = cumulative._batters[pid]  # noqa: SLF001
        _add_pseudo_pas_batter(b_dict, rates_data["rates"], weight)
        if is_new:
            h_seeded_new += 1
            bats = rates_data.get("bats", "R")
            if bats == "S":
                cumulative._batter_stands[pid] = {"L", "R"}  # noqa: SLF001
            else:
                cumulative._batter_stands[pid] = {bats}  # noqa: SLF001
        else:
            h_updated += 1

    p_updated = p_seeded_new = 0
    for pid, rates_data in pitcher_rates.items():
        is_new = pid not in cumulative._pitchers  # noqa: SLF001
        p_dict = cumulative._pitchers[pid]  # noqa: SLF001
        _add_pseudo_pas_pitcher(p_dict, rates_data["rates"], weight)
        if is_new:
            p_seeded_new += 1
            throws = rates_data.get("throws", "R")
            cumulative._pitcher_throws[pid] = throws  # noqa: SLF001
        else:
            p_updated += 1

    # 4. Update speed scores (overwrites, since BHQ projections are more current)
    speed_updated = 0
    for pid, spd in proj_speeds.items():
        prev = batter_speeds.get(pid)
        batter_speeds[pid] = spd
        if prev != spd:
            speed_updated += 1

    print(f"\n  Refresh applied:")
    print(f"    hitters:  {h_updated} updated, {h_seeded_new} newly seeded")
    print(f"    pitchers: {p_updated} updated, {p_seeded_new} newly seeded")
    print(f"    speed scores: {speed_updated} changed (total {len(batter_speeds)})")

    if dry_run:
        print(f"\n  [DRY RUN] state not saved.")
        return

    # 5. Save state with same date (this is an in-place skill update, not a date advance)
    save_state(cumulative, elo, batter_speeds, bankroll, date=date)
    print(f"\n  State saved (date={date}). Daily pipeline will use refreshed priors.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apply latest BHQ in-season snapshot to state")
    parser.add_argument("--weight", type=float, default=150.0,
                        help="Effective PAs of BHQ projection signal to add (default 150)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change but don't save")
    args = parser.parse_args()
    main(weight=args.weight, dry_run=args.dry_run)
