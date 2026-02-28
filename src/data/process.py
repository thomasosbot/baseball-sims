"""
Data processing: aggregates Statcast pitch-level data into player-level PA outcome rates.
These rates feed directly into the simulation's PA probability model.
"""

import numpy as np
import pandas as pd

from src.simulation.constants import LEAGUE_RATES

# Map Statcast event strings -> our 8 PA outcome categories
_STRIKEOUT_EVENTS = {"strikeout", "strikeout_double_play"}
_WALK_EVENTS      = {"walk", "intent_walk"}
_HBP_EVENTS       = {"hit_by_pitch"}
_FIELD_OUT_EVENTS = {
    "field_out", "grounded_into_double_play", "force_out",
    "sac_fly", "sac_bunt", "double_play", "fielders_choice_out",
    "bunt_groundout", "sac_fly_double_play", "triple_play",
}


def _classify_outcome(event: str):
    if pd.isna(event):
        return None
    if event in _STRIKEOUT_EVENTS:  return "K"
    if event in _WALK_EVENTS:        return "BB"
    if event in _HBP_EVENTS:         return "HBP"
    if event == "home_run":           return "HR"
    if event == "triple":             return "3B"
    if event == "double":             return "2B"
    if event in {"single", "fielders_choice"}: return "1B"
    if event in _FIELD_OUT_EVENTS:    return "OUT"
    return None


OUTCOMES = ["K", "BB", "HBP", "HR", "3B", "2B", "1B", "OUT"]


def aggregate_batter_rates(statcast_df: pd.DataFrame, min_pa: int = 50) -> pd.DataFrame:
    """
    Aggregate Statcast pitch-level data to batter-level PA outcome rates.
    Returns one row per batter with overall rates and platoon splits (vsL / vsR).
    Also includes xwOBA, hard_hit_rate, and barrel_rate from Statcast.
    """
    pa = statcast_df[statcast_df["events"].notna()].copy()
    pa["outcome"] = pa["events"].apply(_classify_outcome)
    pa = pa[pa["outcome"].notna()]

    records = []
    for batter_id, grp in pa.groupby("batter"):
        total_pa = len(grp)
        if total_pa < min_pa:
            continue

        rec = {
            "batter_id": batter_id,
            "name": grp["player_name"].iloc[0] if "player_name" in grp.columns else str(batter_id),
            "total_pa": total_pa,
        }

        # Overall rates
        for o in OUTCOMES:
            rec[f"rate_{o}"] = (grp["outcome"] == o).sum() / total_pa

        # Platoon splits
        for ph in ["L", "R"]:
            sub = grp[grp["p_throws"] == ph]
            n = len(sub)
            rec[f"pa_vs{ph}"] = n
            for o in OUTCOMES:
                rec[f"rate_{o}_vs{ph}"] = (sub["outcome"] == o).sum() / n if n >= 20 else rec[f"rate_{o}"]

        # Statcast quality metrics
        xwoba = grp["estimated_woba_using_speedangle"].dropna()
        rec["xwOBA"] = xwoba.mean() if len(xwoba) else np.nan

        ev = grp["launch_speed"].dropna()
        rec["hard_hit_rate"] = (ev >= 95).mean() if len(ev) else np.nan
        rec["barrel_rate"]   = grp["barrel"].mean() if "barrel" in grp.columns else np.nan

        records.append(rec)

    return pd.DataFrame(records)


def aggregate_pitcher_rates(statcast_df: pd.DataFrame, min_bf: int = 50) -> pd.DataFrame:
    """
    Aggregate Statcast pitch-level data to pitcher-level PA outcome rates allowed.
    Returns one row per pitcher with overall rates and platoon splits.
    Also includes xwOBA_against and batted-ball rates (GB%, FB%, LD%).
    """
    pa = statcast_df[statcast_df["events"].notna()].copy()
    pa["outcome"] = pa["events"].apply(_classify_outcome)
    pa = pa[pa["outcome"].notna()]

    records = []
    for pitcher_id, grp in pa.groupby("pitcher"):
        total_bf = len(grp)
        if total_bf < min_bf:
            continue

        rec = {
            "pitcher_id": pitcher_id,
            "name": grp["player_name"].iloc[0] if "player_name" in grp.columns else str(pitcher_id),
            "total_bf": total_bf,
            "throws": grp["p_throws"].iloc[0],
        }

        for o in OUTCOMES:
            rec[f"rate_{o}"] = (grp["outcome"] == o).sum() / total_bf

        # Platoon splits: rate_{outcome}_vs{batter_hand}
        for bh in ["L", "R"]:
            sub = grp[grp["stand"] == bh]
            n = len(sub)
            rec[f"bf_vs{bh}"] = n
            for o in OUTCOMES:
                rec[f"rate_{o}_vs{bh}"] = (sub["outcome"] == o).sum() / n if n >= 20 else rec[f"rate_{o}"]

        xwoba = grp["estimated_woba_using_speedangle"].dropna()
        rec["xwOBA_against"] = xwoba.mean() if len(xwoba) else np.nan

        if "bb_type" in grp.columns:
            in_play = grp[grp["bb_type"].notna()]
            if len(in_play):
                rec["gb_rate"] = (in_play["bb_type"] == "ground_ball").mean()
                rec["fb_rate"] = (in_play["bb_type"] == "fly_ball").mean()
                rec["ld_rate"] = (in_play["bb_type"] == "line_drive").mean()
            else:
                rec["gb_rate"] = rec["fb_rate"] = rec["ld_rate"] = np.nan
        else:
            rec["gb_rate"] = rec["fb_rate"] = rec["ld_rate"] = np.nan

        records.append(rec)

    return pd.DataFrame(records)


def extract_game_lineups(statcast_df: pd.DataFrame) -> pd.DataFrame:
    """
    Reconstruct starting lineups and starting pitchers for every game
    directly from Statcast pitch-level data.

    Logic:
      - Top of inning: away team bats, home team pitches
      - Bot of inning: home team bats, away team pitches
      - First 9 unique batters (by at_bat_number) = starting lineup
      - First pitcher each side faces = opposing starter

    Also determines each batter's handedness:
      - Always L or always R → that hand
      - Both → "S" (switch hitter)

    Returns DataFrame with one row per game:
      game_pk, game_date, home_team, away_team,
      home_lineup (list of MLBAM IDs), away_lineup,
      home_starter_id, away_starter_id
    """
    records = []

    for game_pk, gdf in statcast_df.groupby("game_pk"):
        gdf = gdf.sort_values("at_bat_number")
        home_team = gdf["home_team"].iloc[0]
        away_team = gdf["away_team"].iloc[0]
        game_date = gdf["game_date"].iloc[0]

        top = gdf[gdf["inning_topbot"] == "Top"]
        bot = gdf[gdf["inning_topbot"] == "Bot"]

        # Starting lineups: first 9 unique batters in PA order
        away_lineup = _first_n_unique(top["batter"], 9)
        home_lineup = _first_n_unique(bot["batter"], 9)

        # Starting pitchers
        # Top = away bats vs HOME pitcher, Bot = home bats vs AWAY pitcher
        home_starter_id = int(top["pitcher"].iloc[0]) if len(top) else None
        away_starter_id = int(bot["pitcher"].iloc[0]) if len(bot) else None

        records.append({
            "game_pk":          game_pk,
            "game_date":        game_date,
            "home_team":        home_team,
            "away_team":        away_team,
            "home_lineup":      home_lineup,
            "away_lineup":      away_lineup,
            "home_starter_id":  home_starter_id,
            "away_starter_id":  away_starter_id,
        })

    return pd.DataFrame(records)


def extract_batter_handedness(statcast_df: pd.DataFrame) -> dict:
    """
    Determine each batter's hitting side from Statcast `stand` column.
    Returns {batter_id (int): "L" | "R" | "S"}.
    """
    hand = {}
    for batter_id, grp in statcast_df.groupby("batter"):
        sides = grp["stand"].dropna().unique()
        if len(sides) == 1:
            hand[int(batter_id)] = sides[0]
        elif len(sides) > 1:
            hand[int(batter_id)] = "S"  # switch hitter
        else:
            hand[int(batter_id)] = "R"  # default
    return hand


def _first_n_unique(series, n):
    """Return first n unique values from a pandas Series, preserving order."""
    seen = set()
    result = []
    for v in series:
        v = int(v)
        if v not in seen:
            seen.add(v)
            result.append(v)
            if len(result) == n:
                break
    return result


def prepare_for_rolling(statcast_df: pd.DataFrame) -> pd.DataFrame:
    """
    Prepare Statcast data for day-by-day rolling ingestion.

    Filters to PA-ending events, classifies outcomes, sorts by game_date.
    Returns DataFrame with columns: batter, pitcher, outcome, stand, p_throws,
    player_name, game_date, game_pk, home_team, away_team, inning_topbot.
    """
    pa = statcast_df[statcast_df["events"].notna()].copy()
    pa["outcome"] = pa["events"].apply(_classify_outcome)
    pa = pa[pa["outcome"].notna()]
    pa = pa.sort_values("game_date")
    return pa[["batter", "pitcher", "outcome", "stand", "p_throws",
               "player_name", "game_date", "game_pk",
               "home_team", "away_team", "inning_topbot"]]


def extract_team_relievers(statcast_df: pd.DataFrame) -> pd.DataFrame:
    """
    Identify relievers from Statcast data.

    For each game, the first pitcher per side is the starter; all others are relievers.
    Top of inning → away bats vs home pitchers; Bot → home bats vs away pitchers.

    Returns DataFrame: pitcher_id, team, game_pk, game_date, is_reliever
    """
    pa = statcast_df[statcast_df["events"].notna()].copy()
    records = []

    for game_pk, gdf in pa.groupby("game_pk"):
        gdf = gdf.sort_values("at_bat_number")
        game_date = gdf["game_date"].iloc[0]
        home_team = gdf["home_team"].iloc[0]
        away_team = gdf["away_team"].iloc[0]

        # Top of inning: home team pitches
        top = gdf[gdf["inning_topbot"] == "Top"]
        home_pitchers = list(dict.fromkeys(top["pitcher"]))  # order-preserving unique
        for i, pid in enumerate(home_pitchers):
            records.append({
                "pitcher_id": int(pid),
                "team": home_team,
                "game_pk": game_pk,
                "game_date": game_date,
                "is_reliever": i > 0,
            })

        # Bot of inning: away team pitches
        bot = gdf[gdf["inning_topbot"] == "Bot"]
        away_pitchers = list(dict.fromkeys(bot["pitcher"]))
        for i, pid in enumerate(away_pitchers):
            records.append({
                "pitcher_id": int(pid),
                "team": away_team,
                "game_pk": game_pk,
                "game_date": game_date,
                "is_reliever": i > 0,
            })

    return pd.DataFrame(records)


def aggregate_team_bullpen_rates(
    statcast_df: pd.DataFrame,
    reliever_info: pd.DataFrame,
) -> dict:
    """
    Aggregate Statcast PA outcomes for relievers only, grouped by team.

    Merges reliever_info (which flags relievers per game) with Statcast,
    then calls aggregate_pitcher_rates on the reliever subset.

    Returns {team_abbrev: DataFrame} matching pitcher rates format.
    """
    relievers_only = reliever_info[reliever_info["is_reliever"]]
    if relievers_only.empty:
        return {}

    # Get unique reliever pitcher_id + game_pk pairs
    reliever_keys = relievers_only[["pitcher_id", "game_pk"]].drop_duplicates()

    # Filter Statcast to reliever PAs by merging on pitcher + game
    pa = statcast_df[statcast_df["events"].notna()].copy()
    pa["pitcher_int"] = pa["pitcher"].astype(int)
    reliever_pa = pa.merge(
        reliever_keys,
        left_on=["pitcher_int", "game_pk"],
        right_on=["pitcher_id", "game_pk"],
        how="inner",
    )

    if reliever_pa.empty:
        return {}

    # Aggregate per-pitcher rates with lower threshold for relievers
    pitcher_rates = aggregate_pitcher_rates(reliever_pa, min_bf=10)

    if pitcher_rates.empty:
        return {}

    # Map pitchers to teams (use most recent team assignment)
    pitcher_team = (
        relievers_only.sort_values("game_date")
        .drop_duplicates("pitcher_id", keep="last")[["pitcher_id", "team"]]
    )
    pitcher_rates = pitcher_rates.merge(
        pitcher_team, on="pitcher_id", how="left"
    )

    team_bullpen = {}
    for team, grp in pitcher_rates.groupby("team"):
        team_bullpen[team] = grp
    return team_bullpen


def fangraphs_batting_to_rates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Derive PA outcome rates from FanGraphs batting stats.
    Fallback when Statcast data is unavailable for a player.
    Expects standard FanGraphs columns: PA, K%, BB%, HBP, HR, 3B, 2B, H.
    """
    out = df.copy()
    out["rate_K"]   = out["K%"]
    out["rate_BB"]  = out["BB%"]
    out["rate_HBP"] = out.get("HBP", 0) / out["PA"]
    out["rate_HR"]  = out["HR"] / out["PA"]
    out["rate_3B"]  = out["3B"] / out["PA"]
    out["rate_2B"]  = out["2B"] / out["PA"]
    out["rate_1B"]  = (out["H"] - out["HR"] - out["3B"] - out["2B"]) / out["PA"]
    out["rate_OUT"] = (
        1.0 - out["rate_K"] - out["rate_BB"] - out["rate_HBP"]
            - out["rate_HR"] - out["rate_3B"] - out["rate_2B"] - out["rate_1B"]
    ).clip(lower=0)
    return out
