"""Diagnostics page — data pipeline health and rolling vs full comparison."""

from __future__ import annotations

from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils import COLORS, PLOTLY_TEMPLATE, section_header
from src.backtest.metrics import brier_score, log_loss


def render_diagnostics(
    df: pd.DataFrame,
    rolling_df: Optional[pd.DataFrame],
    full_df: pd.DataFrame,
):
    st.title("Diagnostics")
    st.caption("Data pipeline health, profile coverage, and rolling vs full-season comparison.")

    with_odds = df[df["has_odds"]]

    # ------------------------------------------------------------------
    # Data quality metrics
    # ------------------------------------------------------------------
    odds_match = len(with_odds) / len(df) * 100 if len(df) else 0

    home_profiles = df["home_real_profiles"].mean() if "home_real_profiles" in df.columns else 0
    away_profiles = df["away_real_profiles"].mean() if "away_real_profiles" in df.columns else 0
    avg_profiles = (home_profiles + away_profiles) / 2

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Odds Match Rate", f"{odds_match:.1f}%", help="% of games with closing line data")
    c2.metric("Avg Profiles/Lineup", f"{avg_profiles:.1f}", help="Average real (non-fallback) player profiles per lineup")
    c3.metric("Games Analyzed", f"{len(df):,}")
    c4.metric("Games with Bets", f"{df['has_bet'].sum() + df['has_totals_bet'].sum():,}")

    # ------------------------------------------------------------------
    # Rolling vs Full-Season comparison
    # ------------------------------------------------------------------
    section_header("Rolling vs Full-Season Metrics", "Full-season has look-ahead bias; rolling is realistic")

    if rolling_df is not None and len(rolling_df) >= 50:
        full_preds = full_df["model_home_win_prob"].tolist()
        full_out = full_df["actual_home_win"].astype(int).tolist()
        roll_preds = rolling_df["model_home_win_prob"].tolist()
        roll_out = rolling_df["actual_home_win"].astype(int).tolist()

        full_bets = full_df[full_df["has_bet"]]
        roll_bets = rolling_df[rolling_df["has_bet"]]
        full_totals = full_df[full_df["has_totals_bet"]]
        roll_totals = rolling_df[rolling_df["has_totals_bet"]]

        full_staked = full_bets["bet_stake"].sum() if len(full_bets) else 0
        roll_staked = roll_bets["bet_stake"].sum() if len(roll_bets) else 0
        full_t_staked = full_totals["totals_bet_stake"].sum() if len(full_totals) else 0
        roll_t_staked = roll_totals["totals_bet_stake"].sum() if len(roll_totals) else 0

        metrics = [
            "Games", "Brier Score", "Log Loss", "Model Sharpness",
            "ML Bets Placed", "ML ROI", "ML Win Rate",
        ]
        full_vals = [
            f"{len(full_df):,}",
            f"{brier_score(full_preds, full_out):.4f}",
            f"{log_loss(full_preds, full_out):.4f}",
            f"{full_df['model_home_win_prob'].std():.4f}",
            f"{len(full_bets):,}",
            f"{(full_bets['bet_profit'].sum() / full_staked * 100):+.1f}%" if full_staked else "\u2014",
            f"{full_bets['bet_won'].mean():.1%}" if len(full_bets) else "\u2014",
        ]
        roll_vals = [
            f"{len(rolling_df):,}",
            f"{brier_score(roll_preds, roll_out):.4f}",
            f"{log_loss(roll_preds, roll_out):.4f}",
            f"{rolling_df['model_home_win_prob'].std():.4f}",
            f"{len(roll_bets):,}",
            f"{(roll_bets['bet_profit'].sum() / roll_staked * 100):+.1f}%" if roll_staked else "\u2014",
            f"{roll_bets['bet_won'].mean():.1%}" if len(roll_bets) else "\u2014",
        ]

        if len(full_totals) or len(roll_totals):
            metrics += ["Totals Bets", "Totals ROI"]
            full_vals += [
                f"{len(full_totals):,}",
                f"{(full_totals['totals_bet_profit'].sum() / full_t_staked * 100):+.1f}%" if full_t_staked else "\u2014",
            ]
            roll_vals += [
                f"{len(roll_totals):,}",
                f"{(roll_totals['totals_bet_profit'].sum() / roll_t_staked * 100):+.1f}%" if roll_t_staked else "\u2014",
            ]

        comparison = pd.DataFrame({
            "Metric": metrics,
            "Full Season": full_vals,
            "Rolling Window": roll_vals,
        })
        st.dataframe(comparison, use_container_width=True, hide_index=True)
        st.caption(
            "Full-season uses all data to build profiles (look-ahead bias). "
            "Rolling uses only data available before each game date (realistic)."
        )
    else:
        st.info(
            "Rolling-window backtest data is not yet available or has fewer than "
            "50 games. Run `python scripts/backtest.py --rolling` to generate it."
        )

    # ------------------------------------------------------------------
    # Knowledge growth over time
    # ------------------------------------------------------------------
    source_df = rolling_df if (rolling_df is not None and len(rolling_df) >= 50) else df

    if "cumulative_batters" in source_df.columns:
        section_header("Knowledge Growth Over Time", "Players with real Statcast profiles in the database")

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=source_df["date"],
                y=source_df["cumulative_batters"],
                mode="lines",
                name="Batters Tracked",
                line=dict(color=COLORS["primary"]),
            )
        )
        fig.add_trace(
            go.Scatter(
                x=source_df["date"],
                y=source_df["cumulative_pitchers"],
                mode="lines",
                name="Pitchers Tracked",
                line=dict(color=COLORS["secondary"]),
            )
        )
        fig.update_layout(
            template=PLOTLY_TEMPLATE,
            xaxis_title="Date",
            yaxis_title="Players with Data",
            height=340,
        )
        st.plotly_chart(fig, use_container_width=True)

    # ------------------------------------------------------------------
    # Profile coverage scatter
    # ------------------------------------------------------------------
    if "home_real_profiles" in df.columns:
        section_header("Profile Coverage per Lineup", "Real (non-fallback) player profiles used in each game")

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df["home_real_profiles"],
                mode="markers",
                marker=dict(color=COLORS["primary"], size=3, opacity=0.3),
                name="Home",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df["away_real_profiles"],
                mode="markers",
                marker=dict(color=COLORS["secondary"], size=3, opacity=0.3),
                name="Away",
            )
        )
        fig.add_hline(y=9, line_dash="dash", line_color=COLORS["positive"], annotation_text="Full lineup (9)")
        fig.update_layout(
            template=PLOTLY_TEMPLATE,
            xaxis_title="Date",
            yaxis_title="Real Profiles in Lineup",
            height=300,
        )
        st.plotly_chart(fig, use_container_width=True)

    # ------------------------------------------------------------------
    # Predicted vs Actual total runs
    # ------------------------------------------------------------------
    if "avg_total_runs" in df.columns:
        section_header("Predicted vs Actual Total Runs")

        df_runs = df.copy()
        df_runs["actual_total"] = df_runs["actual_home_score"] + df_runs["actual_away_score"]

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=df_runs["avg_total_runs"],
                y=df_runs["actual_total"],
                mode="markers",
                marker=dict(color=COLORS["primary"], size=4, opacity=0.3),
                hovertemplate="Predicted: %{x:.1f}<br>Actual: %{y}<extra></extra>",
            )
        )
        max_val = max(df_runs["avg_total_runs"].max(), df_runs["actual_total"].max())
        fig.add_trace(
            go.Scatter(
                x=[0, max_val], y=[0, max_val],
                mode="lines",
                line=dict(dash="dash", color=COLORS["muted"]),
                showlegend=False,
            )
        )
        fig.update_layout(
            template=PLOTLY_TEMPLATE,
            xaxis_title="Predicted Total Runs",
            yaxis_title="Actual Total Runs",
            height=380,
        )
        st.plotly_chart(fig, use_container_width=True)
