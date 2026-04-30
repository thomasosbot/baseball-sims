"""
In-season BHQ loader for monthly YTD+projection snapshots.

BHQ exports a single CSV per side (hitter/pitcher) that contains both
YTD actuals and full-season `Proj *` projections. We use the projection
columns directly because BHQ has already regressed them against YTD +
prior years internally (smarter than us re-implementing that math).

File naming convention:
    data/raw/bhq/hitter_ytd_proj_mlb_{YYYY}_{MM}_{DD}.csv
    data/raw/bhq/pitcher_ytd_proj_mlb_{YYYY}_{MM}_{DD}.csv

We pick the latest snapshot by filename date. New snapshots can be dropped
in monthly without removing older ones — git history preserves the trail.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pandas as pd

from config import RAW_DIR
from src.simulation.constants import LEAGUE_RATES

BHQ_DIR = RAW_DIR / "bhq"

_HITTER_PATTERN = re.compile(r"hitter_ytd_proj_mlb_(\d{4})_(\d{2})_(\d{2})\.csv")
_PITCHER_PATTERN = re.compile(r"pitcher_ytd_proj_mlb_(\d{4})_(\d{2})_(\d{2})\.csv")


def find_latest_snapshot(side: str = "hitter") -> Optional[Path]:
    """Return the path to the most recent in-season BHQ snapshot file.

    side: "hitter" or "pitcher"
    """
    pattern = _HITTER_PATTERN if side == "hitter" else _PITCHER_PATTERN
    candidates = []
    for f in BHQ_DIR.glob(f"{side}_ytd_proj_mlb_*.csv"):
        m = pattern.match(f.name)
        if m:
            candidates.append((f"{m.group(1)}-{m.group(2)}-{m.group(3)}", f))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]


def _parse_pct(val) -> float:
    """Convert '76%' -> 0.76, handle NaN and already-numeric values."""
    if pd.isna(val):
        return float("nan")
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace("%", "")
    try:
        return float(s) / 100.0
    except ValueError:
        return float("nan")


def _safe_div(num, den):
    if pd.isna(num) or pd.isna(den) or den <= 0:
        return float("nan")
    return float(num) / float(den)


def _hitter_proj_to_rates(row: pd.Series) -> Optional[dict]:
    """Derive the 8 PA-outcome rates from BHQ's projected counting stats.

    Uses Proj PA / Proj H / Proj 2B / Proj 3B / Proj HR / Proj BB / Proj K.
    Returns None if Proj PA is missing/zero.
    """
    pa = row.get("Proj PA")
    if pd.isna(pa) or pa <= 0:
        return None

    k = row.get("Proj K", 0)
    bb = row.get("Proj BB", 0)
    hr = row.get("Proj HR", 0)
    triples = row.get("Proj 3B", 0)
    doubles = row.get("Proj 2B", 0)
    hits = row.get("Proj H", 0)

    # BHQ doesn't project HBP; use league average. Subtracted from BB-side bucket.
    hbp_rate = LEAGUE_RATES.get("HBP", 0.011)

    k_rate = _safe_div(k, pa) or 0.0
    bb_rate = _safe_div(bb, pa) or 0.0
    hr_rate = _safe_div(hr, pa) or 0.0
    triple_rate = _safe_div(triples, pa) or 0.0
    double_rate = _safe_div(doubles, pa) or 0.0
    # Singles = total hits minus extra bases
    singles = max(hits - hr - triples - doubles, 0)
    single_rate = _safe_div(singles, pa) or 0.0

    out_rate = max(
        1.0 - k_rate - bb_rate - hbp_rate - hr_rate - triple_rate - double_rate - single_rate,
        0.0,
    )

    rates = {
        "K": k_rate,
        "BB": bb_rate,
        "HBP": hbp_rate,
        "HR": hr_rate,
        "3B": triple_rate,
        "2B": double_rate,
        "1B": single_rate,
        "OUT": out_rate,
    }
    total = sum(rates.values())
    if total <= 0:
        return None
    return {o: v / total for o, v in rates.items()}


def _pitcher_proj_to_rates(row: pd.Series) -> Optional[dict]:
    """Derive the 8 PA-outcome rates from BHQ's projected pitcher counting stats.

    Uses Proj BF/G * Proj G for total batters faced, Proj K / Proj BB / Proj HR /
    Proj H, plus Proj GB% / Proj LD% / Proj FB% for hit-type breakdown.
    """
    bf_per_g = row.get("Proj BF/G")
    g = row.get("Proj G")
    if pd.isna(bf_per_g) or pd.isna(g) or bf_per_g <= 0 or g <= 0:
        return None
    bf = bf_per_g * g

    k = row.get("Proj K", 0)
    bb = row.get("Proj BB", 0)
    hr = row.get("Proj HR", 0)
    hits = row.get("Proj H", 0)

    hbp_rate = LEAGUE_RATES.get("HBP", 0.011)

    k_rate = _safe_div(k, bf) or 0.0
    bb_rate = _safe_div(bb, bf) or 0.0
    hr_rate = _safe_div(hr, bf) or 0.0
    h_rate = _safe_div(hits, bf) or 0.0

    # Non-HR hits split by batted-ball mix (LD -> doubles, GB -> singles, FB -> mix)
    non_hr_hits_rate = max(h_rate - hr_rate, 0.0)
    ld_pct = _parse_pct(row.get("Proj LD%"))
    gb_pct = _parse_pct(row.get("Proj GB%"))
    fb_pct = _parse_pct(row.get("Proj FB%"))

    if not pd.isna(ld_pct) and not pd.isna(gb_pct) and not pd.isna(fb_pct):
        # Heuristic: doubles ~ LD-driven, triples ~ small constant, singles = residual
        triple_rate = min(non_hr_hits_rate * 0.04, 0.012)  # ~4% of non-HR hits, capped
        double_rate = non_hr_hits_rate * (ld_pct / max(ld_pct + gb_pct + fb_pct, 0.001)) * 0.65
        double_rate = min(double_rate, non_hr_hits_rate - triple_rate)
        single_rate = max(non_hr_hits_rate - double_rate - triple_rate, 0.0)
    else:
        # Fall back to league-avg shares
        triple_rate = LEAGUE_RATES.get("3B", 0.005)
        double_rate = LEAGUE_RATES.get("2B", 0.045)
        single_rate = max(non_hr_hits_rate - double_rate - triple_rate, 0.0)

    out_rate = max(
        1.0 - k_rate - bb_rate - hbp_rate - hr_rate - triple_rate - double_rate - single_rate,
        0.0,
    )

    rates = {
        "K": k_rate,
        "BB": bb_rate,
        "HBP": hbp_rate,
        "HR": hr_rate,
        "3B": triple_rate,
        "2B": double_rate,
        "1B": single_rate,
        "OUT": out_rate,
    }
    total = sum(rates.values())
    if total <= 0:
        return None
    return {o: v / total for o, v in rates.items()}


def load_hitter_proj_rates(path: Optional[Path] = None) -> dict[int, dict]:
    """Load hitter Proj rates keyed by MLBAMID.

    Returns {mlbamid: {"rates": {outcome: prob}, "bats": "L"/"R"/"S"}}.
    """
    if path is None:
        path = find_latest_snapshot("hitter")
        if path is None:
            return {}

    df = pd.read_csv(path)
    df = df.rename(columns=lambda c: c.strip())
    df = df.dropna(subset=["MLBAMID"])
    df["MLBAMID"] = df["MLBAMID"].astype(int)

    out = {}
    for _, row in df.iterrows():
        rates = _hitter_proj_to_rates(row)
        if rates is None:
            continue
        out[int(row["MLBAMID"])] = {
            "rates": rates,
            "bats": row.get("Bats", "R") if not pd.isna(row.get("Bats")) else "R",
        }
    return out


def load_pitcher_proj_rates(path: Optional[Path] = None) -> dict[int, dict]:
    """Load pitcher Proj rates keyed by MLBAMID."""
    if path is None:
        path = find_latest_snapshot("pitcher")
        if path is None:
            return {}

    df = pd.read_csv(path)
    df = df.rename(columns=lambda c: c.strip())
    df = df.dropna(subset=["MLBAMID"])
    df["MLBAMID"] = df["MLBAMID"].astype(int)

    out = {}
    for _, row in df.iterrows():
        rates = _pitcher_proj_to_rates(row)
        if rates is None:
            continue
        throws = row.get("Throws", "R") if not pd.isna(row.get("Throws")) else "R"
        out[int(row["MLBAMID"])] = {"rates": rates, "throws": throws}
    return out


def load_speed_scores(path: Optional[Path] = None) -> dict[int, float]:
    """Pull projected speed scores (Proj Spd) for baserunning model."""
    if path is None:
        path = find_latest_snapshot("hitter")
        if path is None:
            return {}
    df = pd.read_csv(path)
    df = df.rename(columns=lambda c: c.strip())
    df = df.dropna(subset=["MLBAMID"])
    df["MLBAMID"] = df["MLBAMID"].astype(int)

    out = {}
    for _, row in df.iterrows():
        spd = row.get("Proj Spd")
        if pd.notna(spd) and spd > 0:
            out[int(row["MLBAMID"])] = float(spd)
    return out
