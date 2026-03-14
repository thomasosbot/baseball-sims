"""
Fetch weather data from MLB Stats API game feeds for 2024 games,
then analyze the relationship between weather and run scoring.
"""

import sys
import time
import pickle
from pathlib import Path

import pandas as pd
import numpy as np
import statsapi

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.data.fetch import fetch_season_schedule
from config import CACHE_DIR


def fetch_weather_data(year: int = 2024) -> pd.DataFrame:
    """Fetch weather from MLB Stats API for all games in a season."""
    cache_path = CACHE_DIR / f"game_weather_{year}.pkl"
    if cache_path.exists():
        print(f"Loading cached weather data from {cache_path}")
        return pd.read_pickle(cache_path)

    schedule = fetch_season_schedule(year)
    # Only final games
    schedule = schedule[schedule["status"] == "Final"].copy()
    print(f"Fetching weather for {len(schedule)} games...")

    rows = []
    errors = 0
    for i, (_, game) in enumerate(schedule.iterrows()):
        game_id = game["game_id"]
        try:
            data = statsapi.get("game", {"gamePk": game_id})
            gd = data.get("gameData", {})
            weather = gd.get("weather", {})
            venue = gd.get("venue", {})

            # Parse temperature
            temp_str = weather.get("temp", "")
            try:
                temp = int(temp_str)
            except (ValueError, TypeError):
                temp = None

            # Parse wind
            wind_str = weather.get("wind", "")
            wind_speed = None
            wind_direction = None
            if wind_str:
                # Format: "8 mph, Out To LF" or "0 mph, None"
                parts = wind_str.split(" mph")
                if parts:
                    try:
                        wind_speed = int(parts[0].strip())
                    except (ValueError, TypeError):
                        pass
                    if len(parts) > 1:
                        dir_part = parts[1].strip().lstrip(",").strip()
                        wind_direction = dir_part if dir_part else None

            condition = weather.get("condition", "")
            venue_name = venue.get("name", game.get("venue_name", ""))

            total_runs = (game.get("home_score", 0) or 0) + (game.get("away_score", 0) or 0)

            rows.append({
                "game_id": game_id,
                "date": game["game_date"],
                "home_team": game["home_name"],
                "away_team": game["away_name"],
                "temperature": temp,
                "wind_speed": wind_speed,
                "wind_direction": wind_direction,
                "wind_raw": wind_str,
                "condition": condition,
                "venue_name": venue_name,
                "home_score": game.get("home_score", 0),
                "away_score": game.get("away_score", 0),
                "total_runs": total_runs,
            })
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  Error on game {game_id}: {e}")

        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{len(schedule)} games fetched ({errors} errors)")
            time.sleep(0.5)
        elif (i + 1) % 10 == 0:
            time.sleep(0.1)  # gentle rate limit

    df = pd.DataFrame(rows)
    df.to_pickle(cache_path)
    print(f"Saved {len(df)} games to {cache_path} ({errors} errors)")
    return df


def categorize_wind_direction(d):
    """Map wind direction string to broad category."""
    if d is None or d == "" or d == "None":
        return "None/Dome"
    d_lower = d.lower()
    if "out" in d_lower:
        return "Out"
    elif "in" in d_lower:
        return "In"
    elif "l to r" in d_lower or "r to l" in d_lower or "cross" in d_lower:
        return "Crosswind"
    else:
        return "None/Dome"


def temp_bucket(t):
    if t is None or np.isnan(t):
        return None
    if t < 55:
        return "< 55F"
    elif t < 65:
        return "55-65F"
    elif t < 75:
        return "65-75F"
    elif t < 85:
        return "75-85F"
    else:
        return "85F+"


def analyze(df: pd.DataFrame):
    print("\n" + "=" * 70)
    print("WEATHER & RUN SCORING ANALYSIS — 2024 MLB")
    print("=" * 70)

    print(f"\nTotal games: {len(df)}")
    print(f"Games with temperature data: {df['temperature'].notna().sum()}")
    print(f"Games with wind data: {df['wind_speed'].notna().sum()}")
    print(f"Average total runs: {df['total_runs'].mean():.2f}")

    # 1. Temperature buckets
    print("\n" + "-" * 50)
    print("1. AVERAGE TOTAL RUNS BY TEMPERATURE")
    print("-" * 50)
    df["temp_bucket"] = df["temperature"].apply(temp_bucket)
    temp_agg = df[df["temp_bucket"].notna()].groupby("temp_bucket").agg(
        games=("total_runs", "count"),
        avg_runs=("total_runs", "mean"),
        std_runs=("total_runs", "std"),
    )
    # Sort by bucket order
    bucket_order = ["< 55F", "55-65F", "65-75F", "75-85F", "85F+"]
    temp_agg = temp_agg.reindex(bucket_order).dropna(subset=["games"])
    for bucket, row in temp_agg.iterrows():
        print(f"  {bucket:>8s}: {row['avg_runs']:.2f} runs/game  ({int(row['games']):4d} games, std={row['std_runs']:.2f})")

    # 2. Wind direction categories
    print("\n" + "-" * 50)
    print("2. AVERAGE TOTAL RUNS BY WIND DIRECTION")
    print("-" * 50)
    df["wind_cat"] = df["wind_direction"].apply(categorize_wind_direction)
    wind_agg = df.groupby("wind_cat").agg(
        games=("total_runs", "count"),
        avg_runs=("total_runs", "mean"),
        std_runs=("total_runs", "std"),
    )
    wind_order = ["Out", "In", "Crosswind", "None/Dome"]
    wind_agg = wind_agg.reindex(wind_order).dropna(subset=["games"])
    for cat, row in wind_agg.iterrows():
        print(f"  {cat:>12s}: {row['avg_runs']:.2f} runs/game  ({int(row['games']):4d} games, std={row['std_runs']:.2f})")

    # 3. Wind speed buckets for "Out" wind only
    print("\n" + "-" * 50)
    print("3. TOTAL RUNS BY WIND SPEED (OUT WIND ONLY)")
    print("-" * 50)
    out_df = df[df["wind_cat"] == "Out"].copy()
    def wind_speed_bucket(s):
        if s is None or np.isnan(s):
            return None
        if s <= 5:
            return "0-5 mph"
        elif s <= 10:
            return "6-10 mph"
        elif s <= 15:
            return "11-15 mph"
        else:
            return "16+ mph"

    out_df["ws_bucket"] = out_df["wind_speed"].apply(wind_speed_bucket)
    ws_agg = out_df[out_df["ws_bucket"].notna()].groupby("ws_bucket").agg(
        games=("total_runs", "count"),
        avg_runs=("total_runs", "mean"),
        std_runs=("total_runs", "std"),
    )
    ws_order = ["0-5 mph", "6-10 mph", "11-15 mph", "16+ mph"]
    ws_agg = ws_agg.reindex(ws_order).dropna(subset=["games"])
    for bucket, row in ws_agg.iterrows():
        print(f"  {bucket:>10s}: {row['avg_runs']:.2f} runs/game  ({int(row['games']):4d} games, std={row['std_runs']:.2f})")

    # 4. Roof closed games
    print("\n" + "-" * 50)
    print("4. ROOF/DOME GAMES")
    print("-" * 50)
    roof_mask = df["condition"].str.contains("roof closed", case=False, na=False) | \
                df["condition"].str.contains("dome", case=False, na=False)
    roof_games = df[roof_mask]
    print(f"  'Roof Closed' or 'Dome' condition games: {len(roof_games)}")
    if len(roof_games) > 0:
        print(f"  Avg runs in dome/roof games: {roof_games['total_runs'].mean():.2f}")
        print(f"  Avg runs in open-air games:  {df[~roof_mask]['total_runs'].mean():.2f}")

    # Also check for missing/blank weather (likely domes)
    no_temp = df[df["temperature"].isna()]
    print(f"  Games with no temperature data: {len(no_temp)}")
    if len(no_temp) > 0:
        print(f"  Venues with no temp data:")
        for v, cnt in no_temp["venue_name"].value_counts().head(10).items():
            print(f"    {v}: {cnt}")

    # 5. Distributions
    print("\n" + "-" * 50)
    print("5. TEMPERATURE DISTRIBUTION")
    print("-" * 50)
    temps = df["temperature"].dropna()
    print(f"  Min: {temps.min():.0f}F, Max: {temps.max():.0f}F")
    print(f"  Mean: {temps.mean():.1f}F, Median: {temps.median():.0f}F")
    print(f"  25th pctl: {temps.quantile(0.25):.0f}F, 75th pctl: {temps.quantile(0.75):.0f}F")

    print("\n" + "-" * 50)
    print("6. WIND SPEED DISTRIBUTION")
    print("-" * 50)
    winds = df["wind_speed"].dropna()
    print(f"  Min: {winds.min():.0f} mph, Max: {winds.max():.0f} mph")
    print(f"  Mean: {winds.mean():.1f} mph, Median: {winds.median():.0f} mph")
    print(f"  25th pctl: {winds.quantile(0.25):.0f} mph, 75th pctl: {winds.quantile(0.75):.0f} mph")

    # 6b. Unique wind directions
    print("\n  Unique wind directions:")
    for d, cnt in df["wind_direction"].value_counts().head(15).items():
        print(f"    {d}: {cnt}")

    # 7. Unique conditions
    print("\n" + "-" * 50)
    print("7. CONDITION DISTRIBUTION")
    print("-" * 50)
    for c, cnt in df["condition"].value_counts().items():
        print(f"  {c}: {cnt}")

    # 8. Effect size summary
    print("\n" + "-" * 50)
    print("8. EFFECT SIZE SUMMARY")
    print("-" * 50)
    overall_mean = df["total_runs"].mean()
    if "< 55F" in temp_agg.index and "85F+" in temp_agg.index:
        cold = temp_agg.loc["< 55F", "avg_runs"]
        hot = temp_agg.loc["85F+", "avg_runs"]
        print(f"  Cold (< 55F) vs Hot (85F+): {cold:.2f} vs {hot:.2f} = {hot - cold:+.2f} runs")
    if "Out" in wind_agg.index and "In" in wind_agg.index:
        out_r = wind_agg.loc["Out", "avg_runs"]
        in_r = wind_agg.loc["In", "avg_runs"]
        print(f"  Wind Out vs In: {out_r:.2f} vs {in_r:.2f} = {out_r - in_r:+.2f} runs")
    print(f"  Overall average: {overall_mean:.2f} runs/game")


if __name__ == "__main__":
    df = fetch_weather_data(2024)
    analyze(df)
