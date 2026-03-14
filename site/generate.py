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
        if f.name == "results.json":
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

    # Latest day's picks (for homepage)
    latest = all_days[-1] if all_days else None

    # --- Render pages ---
    # Index (today's picks)
    template = env.get_template("index.html")
    html = template.render(
        today=latest,
        stats=stats,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M ET"),
    )
    (OUTPUT_DIR / "index.html").write_text(html)

    # History
    template = env.get_template("history.html")
    html = template.render(
        results=season_results,
        stats=stats,
        all_days=all_days,
    )
    (OUTPUT_DIR / "history.html").write_text(html)

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

    # About
    template = env.get_template("about.html")
    html = template.render(stats=stats)
    (OUTPUT_DIR / "about.html").write_text(html)

    # Copy static assets
    _copy_static()

    print(f"Site generated: {OUTPUT_DIR}")
    print(f"  index.html, history.html, about.html")
    print(f"  {len(all_days)} daily pick files processed")


# ---------------------------------------------------------------------------
# Backtest data loading and metrics
# ---------------------------------------------------------------------------

def _load_backtest_data():
    """Load backtest CSVs and compute metrics for the backtest page."""
    backtest_data = {}
    chart_data = {}
    all_dfs = []

    for year in range(2024, 2030):
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
        all_dfs.append(df)

    # Combined metrics across all years
    combined_data = None
    combined_chart = None
    if len(all_dfs) >= 2:
        combined_df = pd.concat(all_dfs, ignore_index=True)
        combined_data = _compute_backtest_metrics(combined_df, "combined")
        combined_chart = _compute_chart_data(combined_df)
        # Add bankroll tracking to combined chart
        combined_chart["bankroll_series"] = _compute_bankroll_series(combined_df, 10_000.0)

    # Add bankroll tracking to each year's chart data
    starting = 10_000.0
    for year in sorted(backtest_data.keys()):
        csv_path = DATA_DIR / BACKTEST_CSV_PATTERN.format(year=year)
        df = pd.read_csv(csv_path)
        brl = _compute_bankroll_series(df, starting)
        chart_data[str(year)]["bankroll_series"] = brl
        if brl["values"]:
            starting = brl["values"][-1]  # roll into next year

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

        monthly.append({
            "month": month_name,
            "games": len(mdf),
            "ml_bets": len(m_ml),
            "ml_roi": m_ml_roi,
            "totals_bets": len(m_tot),
            "totals_roi": m_tot_roi,
        })

    # Combined (all bets) stats
    total_profit = ml_profit + totals_profit
    total_staked = (float(ml_bets["bet_stake"].sum()) if ml_count > 0 else 0) + \
                   (float(totals_bets["totals_bet_stake"].sum()) if totals_count > 0 else 0)
    total_bets_count = ml_count + totals_count
    total_roi = total_profit / total_staked * 100 if total_staked > 0 else 0

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
        "total_bets": total_bets_count,
        "total_profit": total_profit,
        "total_staked": total_staked,
        "total_roi": total_roi,
        "monthly": monthly,
    }


def _compute_chart_data(df):
    """Compute chart data (P&L over time, calibration) for a backtest."""
    # Sort by date
    df = df.sort_values("date")

    # --- Cumulative P&L by date ---
    dates = pd.to_datetime(df["date"]).dt.strftime("%b %d").tolist()

    # ML cumulative
    ml_mask = df["bet_side"].notna()
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

    # Downsample for chart performance (max 200 points)
    if len(dates) > 200:
        step = len(dates) // 200
        dates = dates[::step]
        ml_cum = ml_cum[::step]
        totals_cum = totals_cum[::step]

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
        if "totals_bet_profit" in day_df.columns:
            day_pnl += day_df["totals_bet_profit"].fillna(0).sum()
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

    current_bankroll = results[-1]["bankroll"] if results else 10000
    starting_bankroll = results[0]["bankroll"] - results[0]["day_profit"] if results else 10000

    daily_pnl = []
    cumulative = 0
    for d in results:
        cumulative += d["day_profit"]
        daily_pnl.append({
            "date": d["date"],
            "day_profit": d["day_profit"],
            "cumulative": round(cumulative, 2),
            "bankroll": d["bankroll"],
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


def _copy_static():
    """Copy static files to output directory."""
    static_dir = SITE_DIR / "static"
    out_static = OUTPUT_DIR / "static"
    out_static.mkdir(exist_ok=True)

    for f in static_dir.glob("*"):
        (out_static / f.name).write_bytes(f.read_bytes())


if __name__ == "__main__":
    generate_site()
