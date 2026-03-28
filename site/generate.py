"""
Static site generator: reads daily picks JSON files, renders HTML via Jinja2.

Usage:
    python site/generate.py                  # generate full site
    python site/generate.py --date 2026-04-01  # regenerate specific day
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader

OPENING_DAY = "2026-03-26"

SITE_DIR = Path(__file__).parent
TEMPLATE_DIR = SITE_DIR / "templates"
OUTPUT_DIR = SITE_DIR / "public"
DAILY_DIR = Path(__file__).parent.parent / "data" / "daily"
DATA_DIR = Path(__file__).parent.parent / "data"
RESULTS_PATH = DAILY_DIR / "results.json"

# Backtest CSV naming convention
BACKTEST_CSV_PATTERN = "backtest_rolling_{year}_weather.csv"


def generate_site():
    """Generate the full static site."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

    # Load all daily picks
    daily_files = sorted(DAILY_DIR.glob("*.json"))
    all_days = []
    for f in daily_files:
        if f.name in ("results.json", "changelog.json") or f.name.startswith(("odds_cache", "spread_cache")):
            continue
        with open(f) as fh:
            all_days.append(json.load(fh))

    # Load season results
    season_results = []
    if RESULTS_PATH.exists():
        with open(RESULTS_PATH) as f:
            season_results = json.load(f)

    # Compute season stats
    stats = _compute_season_stats(season_results)

    # Latest day's picks (for homepage) — filter out totals picks
    for day in all_days:
        if "picks" in day:
            day["picks"] = [p for p in day["picks"] if p.get("type", "moneyline") != "totals"]

    # Enrich games with run line odds from spread cache
    for day in all_days:
        _enrich_with_spread_odds(day)

    # Show the most relevant day: prefer today's non-preview over tomorrow's preview
    latest = all_days[-1] if all_days else None
    today_str = datetime.now().strftime("%Y-%m-%d")
    if latest and latest.get("run_mode") == "preview" and len(all_days) >= 2:
        # If latest is a preview for tomorrow and we have today's data, show today
        prev = all_days[-2]
        if prev.get("date") == today_str and prev.get("run_mode") != "preview":
            latest = prev

    # Format the display date (e.g. "March 17, 2026")
    display_date = ""
    if latest and latest.get("date"):
        try:
            display_date = datetime.strptime(latest["date"], "%Y-%m-%d").strftime("%B %-d, %Y")
        except (ValueError, TypeError):
            display_date = latest["date"]

    # Check if it's opening day
    is_opening_day = latest and latest.get("date") == OPENING_DAY
    opening_day_schedule = []
    if is_opening_day:
        opening_day_schedule = _build_opening_day_schedule(latest)

    # --- Render pages ---
    # Index (today's picks)
    template = env.get_template("index.html")
    picks_date = latest.get("date", "") if latest else ""
    is_past = picks_date < today_str if picks_date else False

    html = template.render(
        today=latest,
        display_date=display_date,
        stats=stats,
        is_opening_day=is_opening_day,
        opening_day_schedule=opening_day_schedule,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M ET"),
        is_past=is_past,
    )
    (OUTPUT_DIR / "index.html").write_text(html)

    # Results — build pick-by-pick log from results
    all_picks = []
    for day in season_results:
        for pick in day.get("picks", []):
            all_picks.append({**pick, "date": day["date"]})

    template = env.get_template("history.html")
    html = template.render(
        results=season_results,
        stats=stats,
        all_days=all_days,
        all_picks=all_picks,
    )
    (OUTPUT_DIR / "results.html").write_text(html)

    # Backtest
    backtest_data, chart_data, combined_data, combined_chart = _load_backtest_data()
    if backtest_data:
        template = env.get_template("backtest.html")
        html = template.render(
            years=sorted(backtest_data.keys()),
            backtest_data=backtest_data,
            chart_data_json=json.dumps(chart_data),
            combined_data=combined_data,
            combined_chart_json=json.dumps(combined_chart) if combined_chart else "null",
        )
        (OUTPUT_DIR / "backtest.html").write_text(html)
        print(f"  backtest.html ({', '.join(str(y) for y in sorted(backtest_data.keys()))})")

    # Simulate — fetch today's actual schedule from MLB API
    sim_data = _build_sim_data()
    today_matchups = []
    try:
        import statsapi
        today_str_sched = datetime.now().strftime("%Y-%m-%d")
        sched = statsapi.schedule(date=today_str_sched)
        from src.data.fetch import TEAM_NAME_TO_ABBREV
        for g in sched:
            away_name = g.get("away_name", "")
            home_name = g.get("home_name", "")
            away_abbr = TEAM_NAME_TO_ABBREV.get(away_name, away_name)
            home_abbr = TEAM_NAME_TO_ABBREV.get(home_name, home_name)
            today_matchups.append({"away": away_abbr, "home": home_abbr})
    except Exception as e:
        print(f"  Warning: could not fetch today's schedule for simulator: {e}")
        # Fallback to latest daily JSON
        if latest and latest.get("games"):
            for g in latest["games"]:
                today_matchups.append({"away": g.get("away", ""), "home": g.get("home", "")})
    template = env.get_template("simulate.html")
    html = template.render(
        sim_data_json=json.dumps(sim_data, separators=(',', ':')) if sim_data else "null",
        today_matchups_json=json.dumps(today_matchups, separators=(',', ':')),
    )
    (OUTPUT_DIR / "simulate.html").write_text(html)
    if sim_data:
        print(f"  simulate.html ({len(sim_data.get('teams', {}))} teams)")

    # About
    template = env.get_template("about.html")
    html = template.render(stats=stats)
    (OUTPUT_DIR / "about.html").write_text(html)

    # Game preview pages — one per game per day
    game_count = _generate_game_pages(env, all_days)
    if game_count:
        print(f"  {game_count} game preview pages")

    # Copy static assets
    _copy_static()

    print(f"Site generated: {OUTPUT_DIR}")
    print(f"  index.html, results.html, about.html")
    print(f"  {len(all_days)} daily pick files processed")


# ---------------------------------------------------------------------------
# Run line odds enrichment
# ---------------------------------------------------------------------------

def _enrich_with_spread_odds(day):
    """Add run line odds from spread cache to each game object."""
    date = day.get("date", "")
    spread_path = DAILY_DIR / f"spread_cache_{date}.json"
    if not spread_path.exists():
        return

    try:
        with open(spread_path) as f:
            spread_data = json.load(f)
        df_spread = pd.DataFrame(spread_data)
    except Exception:
        return

    from src.data.fetch import TEAM_NAME_TO_ABBREV

    for game in day.get("games", []):
        away = game.get("away", "")
        home = game.get("home", "")

        for _, row in df_spread.iterrows():
            row_home = TEAM_NAME_TO_ABBREV.get(row.get("home_team", ""), "")
            row_away = TEAM_NAME_TO_ABBREV.get(row.get("away_team", ""), "")
            if row_home == home and row_away == away:
                game["rl_home_odds"] = row.get("best_home_odds")
                game["rl_away_odds"] = row.get("best_away_odds")
                game["rl_home_spread"] = row.get("home_spread")
                game["rl_away_spread"] = row.get("away_spread")
                break


# ---------------------------------------------------------------------------
# Game preview page generation
# ---------------------------------------------------------------------------

def _generate_game_pages(env, all_days):
    """Generate individual game preview pages for SEO."""
    game_template = env.get_template("game.html")
    index_template = env.get_template("games_index.html")
    total = 0

    for day in all_days:
        date = day.get("date", "")
        games = day.get("games", [])
        picks = day.get("picks", [])
        if not date or not games:
            continue

        # Format display date
        try:
            display_date = datetime.strptime(date, "%Y-%m-%d").strftime("%B %-d, %Y")
        except (ValueError, TypeError):
            display_date = date

        # Build picks lookup by team
        picks_by_team = {}
        for p in picks:
            team = p.get("team", "")
            if team:
                picks_by_team[team] = p

        # Create directory for this date
        day_dir = OUTPUT_DIR / "games" / date
        day_dir.mkdir(parents=True, exist_ok=True)

        # Generate individual game pages
        for game in games:
            away = game.get("away", "")
            home = game.get("home", "")
            if not away or not home:
                continue

            # Find the pick for this game (if any)
            pick = picks_by_team.get(away) or picks_by_team.get(home)

            # Determine favorite
            fav_prob = max(game.get("model_home_wp", 0.5), game.get("model_away_wp", 0.5))
            favorite = home if game.get("model_home_wp", 0.5) >= 0.5 else away

            html = game_template.render(
                game=game,
                away=away,
                home=home,
                date=date,
                display_date=display_date,
                pick=pick,
                favorite=favorite,
                fav_prob=fav_prob,
                generated_at=datetime.now().strftime("%Y-%m-%d %H:%M ET"),
                base_url="../../",
            )
            filename = f"{away}-vs-{home}.html"
            (day_dir / filename).write_text(html)
            total += 1

        # Generate daily games index page
        # Enrich games with pick info for the index
        index_games = []
        for game in games:
            away = game.get("away", "")
            home = game.get("home", "")
            pick = picks_by_team.get(away) or picks_by_team.get(home)
            index_games.append({
                **game,
                "pick": pick.get("pick", "") if pick else None,
                "pick_type": pick.get("type", "") if pick else None,
            })

        html = index_template.render(
            games=index_games,
            date=date,
            display_date=display_date,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M ET"),
            base_url="../../",
        )
        (day_dir / "index.html").write_text(html)

    return total


# ---------------------------------------------------------------------------
# Backtest data loading and metrics
# ---------------------------------------------------------------------------

def _load_backtest_data():
    """Load backtest CSVs and compute metrics for the backtest page.

    Only loads the 2025 season (with spreads if available).
    """
    backtest_data = {}
    chart_data = {}

    # Prefer the spreads CSV if it exists, otherwise fall back to weather CSV
    for year in [2025]:
        csv_path = DATA_DIR / f"backtest_rolling_{year}_spreads.csv"
        if not csv_path.exists():
            csv_path = DATA_DIR / BACKTEST_CSV_PATTERN.format(year=year)
        if not csv_path.exists():
            continue

        df = pd.read_csv(csv_path)
        if df.empty:
            continue

        metrics = _compute_backtest_metrics(df, year)
        charts = _compute_chart_data(df)

        backtest_data[year] = metrics
        chart_data[str(year)] = charts

    # No combined view needed for single year
    combined_data = None
    combined_chart = None

    # Add bankroll tracking
    starting = 10_000.0  # fresh $10K bankroll for 2025
    for year in sorted(backtest_data.keys()):
        csv_path = DATA_DIR / f"backtest_rolling_{year}_spreads.csv"
        if not csv_path.exists():
            csv_path = DATA_DIR / BACKTEST_CSV_PATTERN.format(year=year)
        df = pd.read_csv(csv_path)
        brl = _compute_bankroll_series(df, starting)
        chart_data[str(year)]["bankroll_series"] = brl

    return backtest_data, chart_data, combined_data, combined_chart


def _compute_backtest_metrics(df, year):
    """Compute summary metrics from a backtest CSV."""
    # Brier score
    brier = float(np.mean(
        (df["model_home_win_prob"] - df["actual_home_win"].astype(float)) ** 2
    ))

    # Run totals
    actual_total = df["actual_home_score"] + df["actual_away_score"]
    avg_model_total = float(df["avg_total_runs"].mean())
    avg_actual_total = float(actual_total.mean())
    run_gap = avg_model_total - avg_actual_total

    # --- Moneyline betting ---
    ml_bets = df[df["bet_side"].notna()].copy()
    ml_count = len(ml_bets)
    if ml_count > 0:
        ml_wins = int(ml_bets["bet_won"].sum())
        ml_win_rate = ml_wins / ml_count * 100
        ml_profit = float(ml_bets["bet_profit"].sum())
        ml_staked = float(ml_bets["bet_stake"].sum())
        ml_roi = ml_profit / ml_staked * 100 if ml_staked > 0 else 0
        ml_avg_odds = float(ml_bets["bet_odds"].mean())
    else:
        ml_wins = 0
        ml_win_rate = 0
        ml_profit = 0
        ml_roi = 0
        ml_avg_odds = 0

    # --- Totals betting ---
    totals_col = "totals_bet_side"
    if totals_col in df.columns:
        totals_bets = df[df[totals_col].notna()].copy()
    else:
        totals_bets = pd.DataFrame()

    totals_count = len(totals_bets)
    if totals_count > 0:
        totals_won_col = totals_bets["totals_bet_won"].dropna()
        totals_wins = int(totals_won_col.sum())
        totals_decided = len(totals_won_col)
        totals_win_rate = totals_wins / totals_decided * 100 if totals_decided > 0 else 0
        totals_profit = float(totals_bets["totals_bet_profit"].sum())
        totals_staked = float(totals_bets["totals_bet_stake"].sum())
        totals_roi = totals_profit / totals_staked * 100 if totals_staked > 0 else 0
        totals_avg_odds = float(totals_bets["totals_bet_odds"].mean())
    else:
        totals_wins = 0
        totals_win_rate = 0
        totals_profit = 0
        totals_roi = 0
        totals_avg_odds = 0

    # --- Spread (run line) betting ---
    spread_col = "spread_bet_side"
    if spread_col in df.columns:
        spread_bets = df[df[spread_col].notna()].copy()
    else:
        spread_bets = pd.DataFrame()

    spread_count = len(spread_bets)
    if spread_count > 0:
        spread_won_col = spread_bets["spread_bet_won"].dropna()
        spread_wins = int(spread_won_col.sum())
        spread_decided = len(spread_won_col)
        spread_win_rate = spread_wins / spread_decided * 100 if spread_decided > 0 else 0
        spread_profit = float(spread_bets["spread_bet_profit"].sum())
        spread_staked = float(spread_bets["spread_bet_stake"].sum())
        spread_roi = spread_profit / spread_staked * 100 if spread_staked > 0 else 0
        spread_avg_odds = float(spread_bets["spread_bet_odds"].mean())
    else:
        spread_wins = 0
        spread_win_rate = 0
        spread_profit = 0
        spread_roi = 0
        spread_avg_odds = 0

    # --- Monthly breakdown ---
    df["month"] = pd.to_datetime(df["date"]).dt.strftime("%B")
    df["month_num"] = pd.to_datetime(df["date"]).dt.month
    monthly = []
    for month_num in sorted(df["month_num"].unique()):
        mdf = df[df["month_num"] == month_num]
        month_name = mdf["month"].iloc[0]

        m_ml = mdf[mdf["bet_side"].notna()]
        m_ml_staked = float(m_ml["bet_stake"].sum()) if len(m_ml) > 0 else 0
        m_ml_profit = float(m_ml["bet_profit"].sum()) if len(m_ml) > 0 else 0
        m_ml_roi = m_ml_profit / m_ml_staked * 100 if m_ml_staked > 0 else 0

        if totals_col in mdf.columns:
            m_tot = mdf[mdf[totals_col].notna()]
        else:
            m_tot = pd.DataFrame()
        m_tot_staked = float(m_tot["totals_bet_stake"].sum()) if len(m_tot) > 0 else 0
        m_tot_profit = float(m_tot["totals_bet_profit"].sum()) if len(m_tot) > 0 else 0
        m_tot_roi = m_tot_profit / m_tot_staked * 100 if m_tot_staked > 0 else 0

        if spread_col in mdf.columns:
            m_sp = mdf[mdf[spread_col].notna()]
        else:
            m_sp = pd.DataFrame()
        m_sp_staked = float(m_sp["spread_bet_stake"].sum()) if len(m_sp) > 0 else 0
        m_sp_profit = float(m_sp["spread_bet_profit"].sum()) if len(m_sp) > 0 else 0
        m_sp_roi = m_sp_profit / m_sp_staked * 100 if m_sp_staked > 0 else 0

        monthly.append({
            "month": month_name,
            "games": len(mdf),
            "ml_bets": len(m_ml),
            "ml_roi": m_ml_roi,
            "totals_bets": len(m_tot),
            "totals_roi": m_tot_roi,
            "spread_bets": len(m_sp),
            "spread_roi": m_sp_roi,
        })

    # Combined (ML + spread only — totals excluded, market too efficient)
    total_profit = ml_profit + spread_profit
    total_staked = (float(ml_bets["bet_stake"].sum()) if ml_count > 0 else 0) + \
                   (float(spread_bets["spread_bet_stake"].sum()) if spread_count > 0 else 0)
    total_bets_count = ml_count + spread_count
    total_roi = total_profit / total_staked * 100 if total_staked > 0 else 0

    # --- Bet log (every individual ML and spread bet) ---
    bet_log = _compute_bet_log(df)

    return {
        "games": len(df),
        "brier": brier,
        "avg_total": avg_model_total,
        "actual_avg_total": avg_actual_total,
        "run_gap": run_gap,
        "ml_bets": ml_count,
        "ml_wins": ml_wins if ml_count > 0 else 0,
        "ml_win_rate": ml_win_rate,
        "ml_profit": ml_profit,
        "ml_staked": float(ml_bets["bet_stake"].sum()) if ml_count > 0 else 0,
        "ml_roi": ml_roi,
        "ml_avg_odds": ml_avg_odds,
        "totals_bets": totals_count,
        "totals_wins": totals_wins if totals_count > 0 else 0,
        "totals_win_rate": totals_win_rate,
        "totals_profit": totals_profit,
        "totals_staked": float(totals_bets["totals_bet_stake"].sum()) if totals_count > 0 else 0,
        "totals_roi": totals_roi,
        "totals_avg_odds": totals_avg_odds,
        "spread_bets": spread_count,
        "spread_wins": spread_wins if spread_count > 0 else 0,
        "spread_win_rate": spread_win_rate,
        "spread_profit": spread_profit,
        "spread_staked": float(spread_bets["spread_bet_stake"].sum()) if spread_count > 0 else 0,
        "spread_roi": spread_roi,
        "spread_avg_odds": spread_avg_odds,
        "total_bets": total_bets_count,
        "total_profit": total_profit,
        "total_staked": total_staked,
        "total_roi": total_roi,
        "monthly": monthly,
        "bet_log": bet_log,
    }


def _compute_bet_log(df):
    """Build a list of every individual ML and spread bet for display."""
    log = []
    df = df.sort_values("date")

    for _, row in df.iterrows():
        date = str(row["date"])[:10]
        away = row.get("away_team", "")
        home = row.get("home_team", "")
        home_score = row.get("actual_home_score", "")
        away_score = row.get("actual_away_score", "")

        # ML bet
        if pd.notna(row.get("bet_side")):
            side = row["bet_side"]
            team = home if side == "home" else away
            odds = row["bet_odds"]
            edge = row["bet_edge"]
            stake = row["bet_stake"]
            profit = row["bet_profit"]
            won = row["bet_won"]
            log.append({
                "date": date,
                "matchup": f"{away} @ {home}",
                "score": f"{int(away_score)}-{int(home_score)}",
                "type": "ML",
                "pick": team,
                "odds": odds,
                "edge": round(edge * 100, 1),
                "stake": round(stake, 2),
                "profit": round(profit, 2),
                "won": bool(won) if pd.notna(won) else None,
            })

        # Spread bet
        if pd.notna(row.get("spread_bet_side")):
            side_str = row["spread_bet_side"]  # e.g. "home 1.5" or "away -1.5"
            parts = side_str.split()
            side = parts[0]
            spread_val = parts[1] if len(parts) > 1 else ""
            team = home if side == "home" else away
            odds = row["spread_bet_odds"]
            edge = row["spread_bet_edge"]
            stake = row["spread_bet_stake"]
            profit = row["spread_bet_profit"]
            won = row["spread_bet_won"]
            log.append({
                "date": date,
                "matchup": f"{away} @ {home}",
                "score": f"{int(away_score)}-{int(home_score)}",
                "type": f"RL {spread_val}",
                "pick": team,
                "odds": odds,
                "edge": round(edge * 100, 1),
                "stake": round(stake, 2),
                "profit": round(profit, 2),
                "won": bool(won) if pd.notna(won) else None,
            })

    return log


def _compute_chart_data(df):
    """Compute chart data (P&L over time, calibration) for a backtest."""
    # Sort by date
    df = df.sort_values("date")

    # --- Cumulative P&L by date ---
    dates = pd.to_datetime(df["date"]).dt.strftime("%b %d").tolist()

    # ML cumulative
    ml_cum = []
    running = 0.0
    for _, row in df.iterrows():
        if pd.notna(row.get("bet_side")):
            running += row["bet_profit"]
        ml_cum.append(round(running, 2))

    # Totals cumulative
    totals_cum = []
    running = 0.0
    for _, row in df.iterrows():
        if pd.notna(row.get("totals_bet_side")):
            running += row["totals_bet_profit"]
        totals_cum.append(round(running, 2))

    # Spread cumulative
    spread_cum = []
    running = 0.0
    for _, row in df.iterrows():
        if pd.notna(row.get("spread_bet_side")):
            running += row["spread_bet_profit"]
        spread_cum.append(round(running, 2))

    # Downsample for chart performance (max 200 points)
    if len(dates) > 200:
        step = len(dates) // 200
        dates = dates[::step]
        ml_cum = ml_cum[::step]
        totals_cum = totals_cum[::step]
        spread_cum = spread_cum[::step]

    # --- Calibration ---
    bins = [(0.2, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8)]
    cal_predicted = []
    cal_actual = []
    cal_counts = []
    for lo, hi in bins:
        mask = (df["model_home_win_prob"] >= lo) & (df["model_home_win_prob"] < hi)
        group = df[mask]
        if len(group) >= 10:
            cal_predicted.append(round(float(group["model_home_win_prob"].mean()), 4))
            cal_actual.append(round(float(group["actual_home_win"].astype(float).mean()), 4))
            cal_counts.append(int(len(group)))

    return {
        "pnl_dates": dates,
        "ml_cumulative": ml_cum,
        "totals_cumulative": totals_cum,
        "spread_cumulative": spread_cum,
        "cal_predicted": cal_predicted,
        "cal_actual": cal_actual,
        "cal_counts": cal_counts,
    }


def _compute_bankroll_series(df, starting_bankroll):
    """Compute daily bankroll values from bet profits."""
    df = df.sort_values("date").copy()
    df["_date"] = pd.to_datetime(df["date"]).dt.strftime("%b %d")

    bankroll = starting_bankroll
    dates = []
    values = []

    for date_str, day_df in df.groupby("date", sort=True):
        day_pnl = day_df["bet_profit"].fillna(0).sum()
        # Totals excluded from P&L (market too efficient)
        if "spread_bet_profit" in day_df.columns:
            day_pnl += day_df["spread_bet_profit"].fillna(0).sum()
        bankroll += day_pnl
        display_date = pd.to_datetime(date_str).strftime("%b %d")
        dates.append(display_date)
        values.append(round(bankroll, 2))

    # Downsample if too many points
    if len(dates) > 200:
        step = len(dates) // 200
        dates = dates[::step]
        values = values[::step]

    return {
        "dates": dates,
        "values": values,
        "starting": starting_bankroll,
        "ending": values[-1] if values else starting_bankroll,
    }


def _compute_season_stats(results):
    """Compute aggregate season statistics."""
    if not results:
        return {
            "total_picks": 0, "wins": 0, "losses": 0, "pushes": 0,
            "win_rate": 0, "total_profit": 0, "roi": 0,
            "current_bankroll": 10000, "starting_bankroll": 10000,
            "best_day": 0, "worst_day": 0, "current_streak": 0,
            "daily_pnl": [],
        }

    total_picks = sum(d["picks_count"] for d in results)
    wins = sum(d["wins"] for d in results)
    losses = sum(d["losses"] for d in results)
    pushes = sum(d.get("pushes", 0) for d in results)
    total_profit = sum(d["day_profit"] for d in results)
    total_wagered = sum(
        sum(p.get("wager", 0) for p in d.get("picks", []))
        for d in results
    )

    current_bankroll = round(10000.0 + total_profit, 2)
    starting_bankroll = 10000

    daily_pnl = []
    cumulative = 0
    for d in results:
        cumulative += d["day_profit"]
        daily_pnl.append({
            "date": d["date"],
            "day_profit": d["day_profit"],
            "cumulative": round(cumulative, 2),
            "bankroll": round(10000.0 + cumulative, 2),
        })

    # Streak
    streak = 0
    if results:
        for d in reversed(results):
            if d["day_profit"] > 0:
                streak += 1
            elif d["day_profit"] < 0:
                streak -= 1
                break
            else:
                break

    return {
        "total_picks": total_picks,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_rate": round(wins / max(1, wins + losses) * 100, 1),
        "total_profit": round(total_profit, 2),
        "roi": round(total_profit / max(1, total_wagered) * 100, 1) if total_wagered else 0,
        "current_bankroll": round(current_bankroll, 2),
        "starting_bankroll": round(starting_bankroll, 2),
        "best_day": round(max((d["day_profit"] for d in results), default=0), 2),
        "worst_day": round(min((d["day_profit"] for d in results), default=0), 2),
        "current_streak": streak,
        "daily_pnl": daily_pnl,
    }


def _build_opening_day_schedule(latest):
    """Build opening day schedule with odds from the daily JSON data (not live API)."""
    schedule = []
    games = latest.get("games", [])

    for game in games:
        home = game.get("home", "")
        away = game.get("away", "")
        entry = {
            "away": away,
            "home": home,
            "away_pitcher": game.get("away_pitcher", "TBD"),
            "home_pitcher": game.get("home_pitcher", "TBD"),
        }

        # Use odds already in the daily JSON (from the pipeline run)
        books_home = game.get("books_home", {})
        books_away = game.get("books_away", {})

        if books_home and books_away:
            best_home = max(books_home.values()) if books_home else None
            best_away = max(books_away.values()) if books_away else None

            if best_home and best_away:
                entry["home_odds"] = int(best_home)
                entry["away_odds"] = int(best_away)

                # Implied probabilities from market WP (already computed in pipeline)
                market_home = game.get("market_home_wp")
                market_away = game.get("market_away_wp")
                if market_home and market_away:
                    entry["home_implied"] = round(market_home * 100, 1)
                    entry["away_implied"] = round(market_away * 100, 1)
                    if market_home > market_away:
                        entry["favorite"] = home
                        entry["fav_pct"] = entry["home_implied"]
                    else:
                        entry["favorite"] = away
                        entry["fav_pct"] = entry["away_implied"]

        schedule.append(entry)

    return schedule


def _build_sim_data():
    """Build team data for client-side game simulation."""
    try:
        from src.data.state import load_state
        from src.features.batting import build_batter_profile
        from src.features.pitching import build_pitcher_profile, build_tiered_bullpen_profiles
        from src.features.park_factors import get_park_factors
        from src.simulation.constants import LEAGUE_RATES

        state = load_state()
        if state is None:
            return None

        cumulative = state["cumulative"]
        batter_speeds = state.get("batter_speeds", {})

        # Build all profiles
        batter_rates = cumulative.to_batter_rates_df()
        pitcher_rates = cumulative.to_pitcher_rates_df()

        batter_profiles = {}
        for _, row in batter_rates.iterrows():
            pid = int(row["batter_id"])
            try:
                batter_profiles[pid] = build_batter_profile(row)
            except Exception:
                continue

        pitcher_profiles = {}
        pitcher_meta = {}
        for _, row in pitcher_rates.iterrows():
            pid = int(row["pitcher_id"])
            try:
                pitcher_profiles[pid] = build_pitcher_profile(row)
                pitcher_meta[pid] = {
                    "name": cumulative._pitcher_names.get(pid, str(pid)),
                    "throws": row.get("throws", "R"),
                }
            except Exception:
                continue

        batter_hands = cumulative.get_batter_handedness()

        # Build bullpen profiles per team
        team_reliever_rates = cumulative.get_team_reliever_rates()
        team_bullpen_profiles = {}
        for team, df in team_reliever_rates.items():
            try:
                bp = build_tiered_bullpen_profiles(df)
                if isinstance(bp, tuple):
                    team_bullpen_profiles[team] = {"hi": bp[0], "lo": bp[1]}
                else:
                    team_bullpen_profiles[team] = {"hi": bp, "lo": bp}
            except Exception:
                continue

        # Team roster mapping — need to know which batters/pitchers belong to which team
        # Use cumulative state's team tracking if available, otherwise use a simpler approach
        # For now, build team data from the daily JSON if available, or use all 30 teams with defaults
        TEAMS = [
            "ARI", "ATL", "BAL", "BOS", "CHC", "CWS", "CIN", "CLE", "COL", "DET",
            "HOU", "KCR", "LAA", "LAD", "MIA", "MIL", "MIN", "NYM", "NYY", "OAK",
            "PHI", "PIT", "SDP", "SFG", "SEA", "STL", "TBR", "TEX", "TOR", "WSN",
        ]

        TEAM_NAMES = {
            "ARI": "Arizona Diamondbacks", "ATL": "Atlanta Braves",
            "BAL": "Baltimore Orioles", "BOS": "Boston Red Sox",
            "CHC": "Chicago Cubs", "CWS": "Chicago White Sox",
            "CIN": "Cincinnati Reds", "CLE": "Cleveland Guardians",
            "COL": "Colorado Rockies", "DET": "Detroit Tigers",
            "HOU": "Houston Astros", "KCR": "Kansas City Royals",
            "LAA": "Los Angeles Angels", "LAD": "Los Angeles Dodgers",
            "MIA": "Miami Marlins", "MIL": "Milwaukee Brewers",
            "MIN": "Minnesota Twins", "NYM": "New York Mets",
            "NYY": "New York Yankees", "OAK": "Oakland Athletics",
            "PHI": "Philadelphia Phillies", "PIT": "Pittsburgh Pirates",
            "SDP": "San Diego Padres", "SFG": "San Francisco Giants",
            "SEA": "Seattle Mariners", "STL": "St. Louis Cardinals",
            "TBR": "Tampa Bay Rays", "TEX": "Texas Rangers",
            "TOR": "Toronto Blue Jays", "WSN": "Washington Nationals",
        }

        # Try to get recent lineups from today's JSON or use top batters
        latest_json = None
        daily_files = sorted(DAILY_DIR.glob("*.json"))
        for f in reversed(daily_files):
            if f.name not in ("results.json", "changelog.json"):
                with open(f) as fh:
                    latest_json = json.load(fh)
                break

        # Build per-team lineups from latest daily run
        team_lineups = {}
        if latest_json:
            for g in latest_json.get("games", []):
                for side in ("home", "away"):
                    abbr = g.get(side, "")
                    if abbr and abbr not in team_lineups:
                        names = g.get(f"{side}_lineup_names", [])
                        if len(names) >= 9:
                            team_lineups[abbr] = names[:9]

        def _round_rates(d):
            return {k: round(v, 4) for k, v in d.items()}

        def _make_pitcher_json(profile):
            if not profile:
                return {"throws": "R", "L": _round_rates(LEAGUE_RATES), "R": _round_rates(LEAGUE_RATES)}
            return {
                "throws": profile.get("throws", "R"),
                "L": _round_rates(profile.get("L", LEAGUE_RATES)),
                "R": _round_rates(profile.get("R", LEAGUE_RATES)),
            }

        # Default batter (league average)
        default_batter = {
            "name": "League Average",
            "bats": "R",
            "speed": 100,
            "profile": {"R": _round_rates(LEAGUE_RATES), "L": _round_rates(LEAGUE_RATES)},
        }

        teams_data = {}
        for abbr in TEAMS:
            # Lineup: try to resolve from daily data or use defaults
            lineup = []
            lineup_names = team_lineups.get(abbr, [])

            if lineup_names:
                from src.data.fetch import resolve_rg_player_to_id
                for name in lineup_names[:9]:
                    pid = resolve_rg_player_to_id(name, cumulative)
                    if pid and pid in batter_profiles:
                        prof = batter_profiles[pid]
                        lineup.append({
                            "name": name,
                            "bats": batter_hands.get(pid, "R"),
                            "speed": batter_speeds.get(pid, 100),
                            "profile": {
                                "R": _round_rates(prof.get("R", LEAGUE_RATES)),
                                "L": _round_rates(prof.get("L", LEAGUE_RATES)),
                            },
                        })
                    else:
                        lineup.append({**default_batter, "name": name})

            # Pad to 9 with defaults
            while len(lineup) < 9:
                lineup.append(default_batter)

            # Starter: use the pitcher from today's data if available
            starter_name = "Unknown"
            starter_profile = None
            if latest_json:
                from src.data.fetch import resolve_rg_player_to_id
                for g in latest_json.get("games", []):
                    for side in ("home", "away"):
                        if g.get(side) == abbr:
                            sp_name = g.get(f"{side}_pitcher", "")
                            if sp_name and sp_name not in ("TBD", "Unknown"):
                                starter_name = sp_name
                                pid = resolve_rg_player_to_id(sp_name, cumulative)
                                if pid and pid in pitcher_profiles:
                                    starter_profile = pitcher_profiles[pid]

            # Bullpen
            bp = team_bullpen_profiles.get(abbr)
            bullpen_hi = _make_pitcher_json(bp["hi"] if bp else None)
            bullpen_lo = _make_pitcher_json(bp["lo"] if bp else None)

            starter_json = _make_pitcher_json(starter_profile)
            starter_json["name"] = starter_name

            teams_data[abbr] = {
                "name": TEAM_NAMES.get(abbr, abbr),
                "lineup": lineup,
                "starter": starter_json,
                "bullpenHi": bullpen_hi,
                "bullpenLo": bullpen_lo,
            }

        # Park factors
        park_factors = {}
        for abbr in TEAMS:
            try:
                pf = get_park_factors(abbr)
                park_factors[abbr] = {k: round(v, 3) for k, v in pf.items() if isinstance(v, (int, float))}
            except Exception:
                park_factors[abbr] = {}

        return {
            "teams": teams_data,
            "parkFactors": park_factors,
            "leagueRates": _round_rates(LEAGUE_RATES),
        }

    except Exception as e:
        print(f"  WARNING: Could not build sim data: {e}")
        import traceback
        traceback.print_exc()
        return None


def _load_changelog():
    """Load changelog.json and compute summary stats."""
    changelog_path = DAILY_DIR / "changelog.json"
    if not changelog_path.exists():
        return [], {"total_days": 0, "avg_wp_shift": "0.0", "picks_unchanged_pct": "0",
                     "picks_added": 0, "picks_dropped": 0}

    with open(changelog_path) as f:
        entries = json.load(f)

    if not entries:
        return [], {"total_days": 0, "avg_wp_shift": "0.0", "picks_unchanged_pct": "0",
                     "picks_added": 0, "picks_dropped": 0}

    # Build per-day view (use the latest transition per date)
    by_date = {}
    for entry in entries:
        d = entry["date"]
        if d not in by_date or entry.get("new_mode", "") == "late":
            by_date[d] = entry

    days = []
    all_wp_shifts = []
    total_picks_checked = 0
    picks_unchanged = 0
    total_added = 0
    total_dropped = 0

    for d in sorted(by_date.keys()):
        entry = by_date[d]
        try:
            display_date = datetime.strptime(d, "%Y-%m-%d").strftime("%B %-d, %Y")
        except (ValueError, TypeError):
            display_date = d

        # Filter out games with null final data (game wasn't in final run)
        game_diffs = [
            g for g in entry.get("game_diffs", [])
            if g.get("final_fav") is not None
        ]
        pick_changes = entry.get("pick_changes", [])

        for g in game_diffs:
            if g.get("wp_shift_abs"):
                all_wp_shifts.append(g["wp_shift_abs"])

        for pc in pick_changes:
            if pc["type"] == "added":
                total_added += 1
            elif pc["type"] == "dropped":
                total_dropped += 1

        # Count unchanged picks (picks in both runs with < 0.5% edge shift)
        shifted_picks = sum(1 for pc in pick_changes if pc["type"] == "shifted")
        added_picks = sum(1 for pc in pick_changes if pc["type"] == "added")
        dropped_picks = sum(1 for pc in pick_changes if pc["type"] == "dropped")
        # Rough estimate: picks that didn't appear in changes are unchanged
        total_picks_checked += shifted_picks + added_picks + dropped_picks
        if not pick_changes:
            picks_unchanged += 1  # whole day unchanged

        days.append({
            "date": d,
            "display_date": display_date,
            "prev_mode": entry.get("prev_mode", ""),
            "new_mode": entry.get("new_mode", ""),
            "game_diffs": game_diffs,
            "pick_changes": pick_changes,
        })

    total_days = len(days)
    avg_wp = round(sum(all_wp_shifts) / len(all_wp_shifts), 1) if all_wp_shifts else 0.0
    unchanged_pct = round(picks_unchanged / max(1, total_days) * 100) if total_days else 0

    summary = {
        "total_days": total_days,
        "avg_wp_shift": str(avg_wp),
        "picks_unchanged_pct": str(unchanged_pct),
        "picks_added": total_added,
        "picks_dropped": total_dropped,
    }

    return days, summary


def _copy_static():
    """Copy static files to output directory."""
    static_dir = SITE_DIR / "static"
    out_static = OUTPUT_DIR / "static"
    out_static.mkdir(exist_ok=True)

    for f in static_dir.glob("*"):
        (out_static / f.name).write_bytes(f.read_bytes())


if __name__ == "__main__":
    generate_site()
