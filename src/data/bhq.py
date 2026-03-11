"""
Baseball HQ data loader.

Parses BHQ seasonal CSV exports (hitters, pitcher-advanced, pitcher-bb)
and returns clean DataFrames keyed by MLBAMID for joining to Statcast.
"""

import pandas as pd
from pathlib import Path

from config import RAW_DIR

BHQ_DIR = RAW_DIR / "bhq"


def _parse_pct(val):
    """Convert '76%' -> 0.76, handle NaN and already-numeric values."""
    if pd.isna(val):
        return float("nan")
    if isinstance(val, (int, float)):
        return val
    s = str(val).strip().replace("%", "")
    try:
        return float(s) / 100.0
    except ValueError:
        return float("nan")


def load_bhq_hitters(year: int) -> pd.DataFrame:
    """Load BHQ hitter stats for a given year, keyed by MLBAMID."""
    path = BHQ_DIR / f"mlb_seasonal_hitter_stats_and_splits-{year}.csv"
    if not path.exists():
        return pd.DataFrame()

    df = pd.read_csv(path)
    df = df.rename(columns=lambda c: c.strip())

    # Parse percentage columns
    pct_cols = ["BB%", "Ct%", "H%", "GB%", "LD%", "FB%", "Brl%"]
    for col in pct_cols:
        if col in df.columns:
            df[col] = df[col].apply(_parse_pct)

    # Ensure numeric types
    for col in ["PA", "Eye", "HctX", "xBA", "PX", "xPX", "SPD", "RSPD", "MLBAMID"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["MLBAMID"])
    df["MLBAMID"] = df["MLBAMID"].astype(int)
    df = df.set_index("MLBAMID")
    return df


def load_bhq_pitchers(year: int) -> pd.DataFrame:
    """
    Load and merge BHQ pitcher-advanced and pitcher-bb for a given year.
    Returns a single DataFrame keyed by MLBAMID.
    """
    # Handle trailing space in 2025 filename
    adv_path = BHQ_DIR / f"mlb_seasonal_pitcher_stats-advanced-{year}.csv"
    if not adv_path.exists():
        adv_path = BHQ_DIR / f"mlb_seasonal_pitcher_stats-advanced-{year} .csv"
    bb_path = BHQ_DIR / f"mlb_seasonal_pitcher_stats-bb-{year}.csv"

    if not adv_path.exists() or not bb_path.exists():
        return pd.DataFrame()

    adv = pd.read_csv(adv_path)
    bb = pd.read_csv(bb_path)

    # Clean column names
    adv = adv.rename(columns=lambda c: c.strip())
    bb = bb.rename(columns=lambda c: c.strip())

    # Parse percentage columns in advanced
    for col in ["BB%", "K%", "SwK%", "K-BB%"]:
        if col in adv.columns:
            adv[col] = adv[col].apply(_parse_pct)

    # Parse percentage columns in batted ball
    for col in ["H%", "S%", "GB%", "LD %", "LD%", "FB %", "FB%"]:
        if col in bb.columns:
            bb[col] = bb[col].apply(_parse_pct)

    # Normalize column names for batted ball
    bb = bb.rename(columns={"LD %": "LD%", "FB %": "FB%"})

    # Numeric columns
    for col in ["IP", "ERA", "xERA", "WHIP", "K/9", "MLBAMID"]:
        if col in adv.columns:
            adv[col] = pd.to_numeric(adv[col], errors="coerce")
    for col in ["IP", "HR/9", "HR/FB", "xHR/FB", "OBA", "MLBAMID"]:
        if col in bb.columns:
            bb[col] = pd.to_numeric(bb[col], errors="coerce")

    # Merge on MLBAMID
    adv = adv.dropna(subset=["MLBAMID"])
    bb = bb.dropna(subset=["MLBAMID"])
    adv["MLBAMID"] = adv["MLBAMID"].astype(int)
    bb["MLBAMID"] = bb["MLBAMID"].astype(int)

    # Drop duplicate columns before merge
    bb_cols = [c for c in bb.columns if c not in adv.columns or c == "MLBAMID"]
    merged = adv.merge(bb[bb_cols], on="MLBAMID", how="outer")
    merged = merged.set_index("MLBAMID")
    return merged


def load_bhq_all(years: list) -> dict:
    """
    Load BHQ data for multiple years.

    Returns {year: {"hitters": DataFrame, "pitchers": DataFrame}}
    """
    result = {}
    for year in years:
        h = load_bhq_hitters(year)
        p = load_bhq_pitchers(year)
        if not h.empty or not p.empty:
            result[year] = {"hitters": h, "pitchers": p}
    return result
