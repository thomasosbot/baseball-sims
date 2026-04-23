"""Per-player Statcast rollups for narrative briefs.

Aggregates pitch-level Statcast into season summaries we can cite in
LLM-generated pick explanations. Keyed by MLBAM ID.

Hitters: PA, K%, BB%, wOBA, xwOBA, xBA, barrel%, hard-hit%, split by p_throws.
Pitchers: BF, K%, BB%, wOBA-against, xwOBA-against, barrel%-against, whiff%, chase%.
"""

from __future__ import annotations

import pickle
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache"


def _load_statcast(year: int) -> pd.DataFrame:
    path = CACHE_DIR / f"statcast_{year}.pkl"
    with open(path, "rb") as f:
        return pickle.load(f)


def _hitter_stats(pa_df: pd.DataFrame) -> dict:
    pa_count = len(pa_df)
    if pa_count == 0:
        return {}

    k = (pa_df["events"] == "strikeout").sum()
    bb = pa_df["events"].isin(["walk", "hit_by_pitch"]).sum()

    woba_num = pd.to_numeric(pa_df["woba_value"], errors="coerce").sum(skipna=True)
    woba_den = pd.to_numeric(pa_df["woba_denom"], errors="coerce").sum(skipna=True)
    woba = float(woba_num / woba_den) if woba_den else None

    xwoba_series = pd.to_numeric(pa_df["estimated_woba_using_speedangle"], errors="coerce")
    xwoba_pa_denom = pd.to_numeric(pa_df["woba_denom"], errors="coerce")
    # xwOBA is estimated on BIP + K/BB events; use mean weighted by denom where available
    xwoba_valid = xwoba_series.notna() & (xwoba_pa_denom > 0)
    xwoba = float(xwoba_series[xwoba_valid].mean()) if xwoba_valid.any() else None

    bip = pa_df[pa_df["launch_speed"].notna()]
    xba_series = pd.to_numeric(bip["estimated_ba_using_speedangle"], errors="coerce")
    xba = float(xba_series.mean()) if xba_series.notna().any() else None

    barrels = (pd.to_numeric(bip["launch_speed_angle"], errors="coerce") == 6).sum()
    hard = (pd.to_numeric(bip["launch_speed"], errors="coerce") >= 95).sum()
    bip_count = len(bip)
    barrel_pct = float(barrels / bip_count) if bip_count else None
    hard_pct = float(hard / bip_count) if bip_count else None

    return {
        "pa": int(pa_count),
        "k_pct": float(k / pa_count),
        "bb_pct": float(bb / pa_count),
        "woba": woba,
        "xwoba": xwoba,
        "xba": xba,
        "barrel_pct": barrel_pct,
        "hard_hit_pct": hard_pct,
    }


def _pitcher_stats(df: pd.DataFrame) -> dict:
    pa = df[df["events"].notna()]
    bf = len(pa)
    if bf == 0:
        return {}

    k = (pa["events"] == "strikeout").sum()
    bb = pa["events"].isin(["walk", "hit_by_pitch"]).sum()

    woba_num = pd.to_numeric(pa["woba_value"], errors="coerce").sum(skipna=True)
    woba_den = pd.to_numeric(pa["woba_denom"], errors="coerce").sum(skipna=True)
    woba_against = float(woba_num / woba_den) if woba_den else None

    xwoba_series = pd.to_numeric(pa["estimated_woba_using_speedangle"], errors="coerce")
    xwoba_against = float(xwoba_series.mean()) if xwoba_series.notna().any() else None

    bip = pa[pa["launch_speed"].notna()]
    bip_count = len(bip)
    barrels = (pd.to_numeric(bip["launch_speed_angle"], errors="coerce") == 6).sum()
    hard = (pd.to_numeric(bip["launch_speed"], errors="coerce") >= 95).sum()
    barrel_against = float(barrels / bip_count) if bip_count else None
    hard_against = float(hard / bip_count) if bip_count else None

    # Whiff% and chase% from all pitches, not just PAs
    swinging = df["description"].isin([
        "swinging_strike", "swinging_strike_blocked", "foul", "foul_tip",
        "hit_into_play", "missed_bunt", "foul_bunt",
    ])
    whiffs = df["description"].isin(["swinging_strike", "swinging_strike_blocked"])
    swing_count = int(swinging.sum())
    whiff_pct = float(whiffs.sum() / swing_count) if swing_count else None

    # Chase% = swings out of zone / pitches out of zone
    zone = pd.to_numeric(df["zone"], errors="coerce")
    out_of_zone = zone.isin([11, 12, 13, 14])  # zones 11-14 are outside
    chase_swings = (out_of_zone & swinging).sum()
    chase_pct = float(chase_swings / out_of_zone.sum()) if out_of_zone.sum() else None

    return {
        "bf": int(bf),
        "k_pct": float(k / bf),
        "bb_pct": float(bb / bf),
        "woba_against": woba_against,
        "xwoba_against": xwoba_against,
        "barrel_against": barrel_against,
        "hard_hit_against": hard_against,
        "whiff_pct": whiff_pct,
        "chase_pct": chase_pct,
    }


def build_hitter_rollup(statcast_df: pd.DataFrame) -> dict:
    """Return {batter_id: {'all': stats, 'vsL': stats, 'vsR': stats}}.

    Note: Statcast's `player_name` column is the pitcher's name, not the batter's —
    resolve batter names externally via MLBAM ID lookup.
    """
    pa = statcast_df[statcast_df["events"].notna()].copy()
    out = {}
    for bid, group in pa.groupby("batter"):
        out[int(bid)] = {
            "all": _hitter_stats(group),
            "vsL": _hitter_stats(group[group["p_throws"] == "L"]),
            "vsR": _hitter_stats(group[group["p_throws"] == "R"]),
        }
    return out


def build_pitcher_rollup(statcast_df: pd.DataFrame) -> dict:
    """Return {pitcher_id: {'all': stats, 'vsL': stats, 'vsR': stats}}."""
    out = {}
    for pid, group in statcast_df.groupby("pitcher"):
        pa_group = group[group["events"].notna()]
        if len(pa_group) < 30:
            continue
        out[int(pid)] = {
            "all": _pitcher_stats(group),
            "vsL": _pitcher_stats(group[group["stand"] == "L"]),
            "vsR": _pitcher_stats(group[group["stand"] == "R"]),
        }
    return out


@lru_cache(maxsize=1)
def get_rollups(year: int = 2025) -> tuple[dict, dict]:
    """Cached rollup build. Returns (hitters, pitchers)."""
    df = _load_statcast(year)
    return build_hitter_rollup(df), build_pitcher_rollup(df)
