"""Performance page — executive summary of model results."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils import COLORS, PLOTLY_TEMPLATE, section_header
from src.backtest.metrics import brier_score


def render_performance(df: pd.DataFrame):
    st.title("Performance")
    st.caption("Executive summary of model accuracy and betting results.")

    # ------------------------------------------------------------------
    # Hero metrics
    # ------------------------------------------------------------------
    preds = df["model_home_win_prob"].tolist()
    outcomes = df["actual_home_win"].astype(int).tolist()
    bs = brier_score(preds, outcomes)
    accuracy = df["model_correct"].mean() * 100

    bets = df[df["has_bet"]]
    total_profit = bets["bet_profit"].sum() if len(bets) else 0
    total_staked = bets["bet_stake"].sum() if len(bets) else 0
    ml_roi = (total_profit / total_staked * 100) if total_staked else 0

    totals_bets = df[df["has_totals_bet"]]
    totals_profit = totals_bets["totals_bet_profit"].sum() if len(totals_bets) else 0
    totals_staked = totals_bets["totals_bet_stake"].sum() if len(totals_bets) else 0
    totals_roi = (totals_profit / totals_staked * 100) if totals_staked else 0

    combined_profit = total_profit + totals_profit
    combined_staked = total_staked + totals_staked
    combined_roi = (combined_profit / combined_staked * 100) if combined_staked else 0

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric(
        "Brier Score",
        f"{bs:.4f}",
        delta=f"{bs - 0.25:+.4f} vs coin-flip",
        delta_color="inverse",
    )
    c2.metric("Model Accuracy", f"{accuracy:.1f}%")
    c3.metric(
        "ML ROI",
        f"{ml_roi:+.1f}%",
        delta=f"${total_profit:+,.0f}",
        delta_color="normal",
    )
    c4.metric(
        "Totals ROI",
        f"{totals_roi:+.1f}%",
        delta=f"${totals_profit:+,.0f}",
        delta_color="normal",
    )
    c5.metric(
        "Combined ROI",
        f"{combined_roi:+.1f}%",
        delta=f"${combined_profit:+,.0f}",
        delta_color="normal",
    )
    c6.metric("Games", f"{len(df):,}")

    # ------------------------------------------------------------------
    # Cumulative P&L equity curve
    # ------------------------------------------------------------------
    section_header("Cumulative P&L", "Equity curve across all bet types")

    bet_rows = df[df["has_bet"]].copy()
    totals_rows = df[df["has_totals_bet"]].copy()
    any_bet_rows = df[df["has_bet"] | df["has_totals_bet"]].copy()

    if len(bet_rows) or len(totals_rows):
        fig = go.Figure()

        if len(bet_rows):
            bet_rows = bet_rows.sort_values("date")
            cum_ml = bet_rows["bet_profit"].cumsum()
            fig.add_trace(
                go.Scatter(
                    x=bet_rows["date"],
                    y=cum_ml,
                    mode="lines",
                    name="Moneyline",
                    line=dict(color=COLORS["primary"], width=2),
                    hovertemplate="Date: %{x|%b %d}<br>ML P&L: $%{y:,.0f}<extra></extra>",
                )
            )

        if len(totals_rows):
            totals_rows = totals_rows.sort_values("date")
            cum_totals = totals_rows["totals_bet_profit"].cumsum()
            fig.add_trace(
                go.Scatter(
                    x=totals_rows["date"],
                    y=cum_totals,
                    mode="lines",
                    name="Totals",
                    line=dict(color=COLORS["secondary"], width=2),
                    hovertemplate="Date: %{x|%b %d}<br>Totals P&L: $%{y:,.0f}<extra></extra>",
                )
            )

        if len(any_bet_rows):
            any_bet_rows = any_bet_rows.sort_values("date")
            cum_combined = any_bet_rows["combined_profit"].cumsum()
            fig.add_trace(
                go.Scatter(
                    x=any_bet_rows["date"],
                    y=cum_combined,
                    mode="lines",
                    name="Combined",
                    line=dict(color=COLORS["positive"], width=2, dash="dot"),
                    hovertemplate="Date: %{x|%b %d}<br>Combined P&L: $%{y:,.0f}<extra></extra>",
                )
            )

        fig.add_hline(y=0, line_dash="dash", line_color=COLORS["muted"], line_width=1)
        fig.update_layout(
            template=PLOTLY_TEMPLATE,
            xaxis_title="Date",
            yaxis_title="Cumulative Profit ($)",
            height=380,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No bets in the filtered data.")

    # ------------------------------------------------------------------
    # Monthly P&L bar chart
    # ------------------------------------------------------------------
    section_header("Monthly P&L", "Grouped by bet type")

    if len(bet_rows) or len(totals_rows):
        fig = go.Figure()

        if len(bet_rows):
            ml_monthly = bet_rows.groupby("month")["bet_profit"].sum().reset_index()
            ml_monthly.columns = ["Month", "Profit"]
            colors = [COLORS["positive"] if p >= 0 else COLORS["negative"] for p in ml_monthly["Profit"]]
            fig.add_trace(
                go.Bar(
                    x=ml_monthly["Month"],
                    y=ml_monthly["Profit"],
                    name="Moneyline",
                    marker_color=COLORS["primary"],
                    hovertemplate="Month: %{x}<br>ML P&L: $%{y:,.0f}<extra></extra>",
                )
            )

        if len(totals_rows):
            totals_monthly = totals_rows.groupby("month")["totals_bet_profit"].sum().reset_index()
            totals_monthly.columns = ["Month", "Profit"]
            fig.add_trace(
                go.Bar(
                    x=totals_monthly["Month"],
                    y=totals_monthly["Profit"],
                    name="Totals",
                    marker_color=COLORS["secondary"],
                    hovertemplate="Month: %{x}<br>Totals P&L: $%{y:,.0f}<extra></extra>",
                )
            )

        fig.add_hline(y=0, line_dash="dash", line_color=COLORS["muted"], line_width=1)
        fig.update_layout(
            barmode="group",
            template=PLOTLY_TEMPLATE,
            xaxis_title="Month",
            yaxis_title="Profit ($)",
            height=320,
        )
        st.plotly_chart(fig, use_container_width=True)

    # ------------------------------------------------------------------
    # Compact insights
    # ------------------------------------------------------------------
    section_header("Insights")

    insights = []

    if bs < 0.25:
        insights.append(
            f"The model beats coin-flip prediction (Brier **{bs:.4f}** vs 0.2500) "
            "and has genuine predictive signal."
        )
    else:
        insights.append(
            f"The model's Brier score (**{bs:.4f}**) is at or above coin-flip (0.2500). "
            "Predictions need improvement."
        )

    model_std = df["model_home_win_prob"].std()
    if model_std < 0.06:
        insights.append(
            f"Prediction spread is narrow (std={model_std:.3f}). "
            "The model clusters near 50/50 and may overbet phantom edges."
        )
    else:
        insights.append(
            f"Prediction spread looks healthy (std={model_std:.3f}), "
            "matching market-level differentiation between matchups."
        )

    if len(bets) and ml_roi < 0:
        insights.append(f"ML ROI is negative ({ml_roi:+.1f}%).")
    elif len(bets) and ml_roi > 0:
        insights.append(f"ML ROI is positive ({ml_roi:+.1f}%).")

    if len(totals_bets):
        totals_wr = totals_bets["totals_bet_won"].mean() * 100
        insights.append(
            f"Totals betting: {len(totals_bets):,} bets placed, "
            f"{totals_wr:.1f}% win rate, {totals_roi:+.1f}% ROI."
        )

    for line in insights:
        st.markdown(f"- {line}")
