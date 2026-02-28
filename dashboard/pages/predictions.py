"""Predictions page — model accuracy deep-dive."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils import COLORS, PLOTLY_TEMPLATE, section_header
from src.backtest.metrics import brier_score, log_loss, calibration_table


def render_predictions(df: pd.DataFrame):
    st.title("Predictions")
    st.caption("How accurate are the model's probability estimates?")

    preds = df["model_home_win_prob"].tolist()
    outcomes = df["actual_home_win"].astype(int).tolist()

    # ------------------------------------------------------------------
    # Accuracy metrics
    # ------------------------------------------------------------------
    bs = brier_score(preds, outcomes)
    ll = log_loss(preds, outcomes)

    cal = calibration_table(preds, outcomes, n_buckets=20)
    cal_error = (cal["avg_predicted"] - cal["actual_win_pct"]).abs().mean()
    model_std = df["model_home_win_prob"].std()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Brier Score", f"{bs:.4f}", delta=f"{bs - 0.25:+.4f} vs coin-flip", delta_color="inverse")
    c2.metric("Log Loss", f"{ll:.4f}")
    c3.metric("Mean Cal Error", f"{cal_error:.4f}")
    c4.metric("Sharpness (Std)", f"{model_std:.4f}", help="Std dev of predictions — higher = more decisive")

    # ------------------------------------------------------------------
    # Reliability diagram with Wilson CIs
    # ------------------------------------------------------------------
    section_header("Reliability Diagram", "Predicted probability vs actual win rate with 95% confidence intervals")

    cal10 = calibration_table(preds, outcomes, n_buckets=10)
    cal10 = cal10.copy()
    z = 1.96
    n = cal10["n"]
    p = cal10["actual_win_pct"]
    cal10["ci_low"] = (p + z**2/(2*n) - z*np.sqrt((p*(1-p) + z**2/(4*n))/n)) / (1 + z**2/n)
    cal10["ci_high"] = (p + z**2/(2*n) + z*np.sqrt((p*(1-p) + z**2/(4*n))/n)) / (1 + z**2/n)

    fig = go.Figure()

    # Confidence band
    fig.add_trace(
        go.Scatter(
            x=list(cal10["avg_predicted"]) + list(cal10["avg_predicted"][::-1]),
            y=list(cal10["ci_high"]) + list(cal10["ci_low"][::-1]),
            fill="toself",
            fillcolor="rgba(99,102,241,0.12)",
            line=dict(color="rgba(0,0,0,0)"),
            showlegend=False,
            hoverinfo="skip",
        )
    )

    # Actual points
    fig.add_trace(
        go.Scatter(
            x=cal10["avg_predicted"],
            y=cal10["actual_win_pct"],
            mode="markers+lines",
            marker=dict(color=COLORS["primary"], size=8),
            line=dict(color=COLORS["primary"], width=2),
            name="Model",
            hovertemplate="Predicted: %{x:.1%}<br>Actual: %{y:.1%}<br>n=%{customdata}<extra></extra>",
            customdata=cal10["n"],
        )
    )

    # Perfect calibration line
    fig.add_trace(
        go.Scatter(
            x=[0, 1], y=[0, 1],
            mode="lines",
            line=dict(dash="dash", color=COLORS["muted"]),
            name="Perfect",
        )
    )

    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        xaxis_title="Predicted Probability",
        yaxis_title="Actual Win Rate",
        height=420,
    )
    st.plotly_chart(fig, use_container_width=True)

    # ------------------------------------------------------------------
    # Calibration residuals
    # ------------------------------------------------------------------
    section_header("Calibration Residuals", "Actual win rate minus predicted probability per bin")

    residuals = cal10["actual_win_pct"] - cal10["avg_predicted"]
    labels = cal10["bucket"] if "bucket" in cal10.columns else cal10["avg_predicted"].apply(lambda x: f"{x:.0%}")

    fig = go.Figure(
        go.Bar(
            x=labels,
            y=residuals,
            marker_color=[COLORS["positive"] if r >= 0 else COLORS["negative"] for r in residuals],
            hovertemplate="Bin: %{x}<br>Residual: %{y:.3f}<extra></extra>",
        )
    )
    fig.add_hline(y=0, line_dash="dash", line_color=COLORS["muted"])
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        xaxis_title="Prediction Bin",
        yaxis_title="Actual \u2212 Predicted",
        height=280,
    )
    st.plotly_chart(fig, use_container_width=True)

    # ------------------------------------------------------------------
    # Model vs Market histogram
    # ------------------------------------------------------------------
    has_odds = df["has_odds"]
    odds_df = df[has_odds]

    if len(odds_df) > 0:
        section_header(
            "Model vs Market Distribution",
            f"Comparing predictions to odds-implied probabilities across {len(odds_df):,} games",
        )

        fig = go.Figure()
        fig.add_trace(
            go.Histogram(
                x=odds_df["model_home_win_prob"],
                nbinsx=30,
                name="Model",
                marker_color=COLORS["primary"],
                opacity=0.6,
                hovertemplate="Prob: %{x:.0%}<br>Games: %{y}<extra>Model</extra>",
            )
        )
        fig.add_trace(
            go.Histogram(
                x=odds_df["market_home_nv_prob"],
                nbinsx=30,
                name="Market (no-vig)",
                marker_color=COLORS["secondary"],
                opacity=0.6,
                hovertemplate="Prob: %{x:.0%}<br>Games: %{y}<extra>Market</extra>",
            )
        )
        fig.update_layout(
            barmode="overlay",
            template=PLOTLY_TEMPLATE,
            xaxis_title="Home Win Probability",
            yaxis_title="Number of Games",
            height=340,
            xaxis=dict(range=[0.15, 0.85], dtick=0.05),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Summary stats
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Model Std", f"{odds_df['model_home_win_prob'].std():.3f}")
        s2.metric("Market Std", f"{odds_df['market_home_nv_prob'].std():.3f}")
        m_range = odds_df["model_home_win_prob"]
        s3.metric("Model Range", f"{m_range.min():.0%} \u2013 {m_range.max():.0%}")
        mk_range = odds_df["market_home_nv_prob"]
        s4.metric("Market Range", f"{mk_range.min():.0%} \u2013 {mk_range.max():.0%}")

    # ------------------------------------------------------------------
    # Brier score by month
    # ------------------------------------------------------------------
    section_header("Brier Score by Month")

    months = []
    for month, group in df.groupby("month"):
        bs_m = brier_score(
            group["model_home_win_prob"].tolist(),
            group["actual_home_win"].astype(int).tolist(),
        )
        months.append({"Month": month, "Brier": bs_m, "n": len(group)})
    month_df = pd.DataFrame(months)

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=month_df["Month"],
            y=month_df["Brier"],
            marker_color=COLORS["primary"],
            hovertemplate="Month: %{x}<br>Brier: %{y:.4f}<extra></extra>",
        )
    )
    fig.add_hline(
        y=0.25, line_dash="dash", line_color=COLORS["negative"],
        annotation_text="Coin flip (0.25)", annotation_position="top right",
    )
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        xaxis_title="Month",
        yaxis_title="Brier Score",
        height=300,
    )
    st.plotly_chart(fig, use_container_width=True)

    # ------------------------------------------------------------------
    # Prediction accuracy histogram (correct vs incorrect)
    # ------------------------------------------------------------------
    section_header("Prediction Distribution", "Model probabilities split by correctness")

    correct = df[df["model_correct"]]
    incorrect = df[~df["model_correct"]]

    fig = go.Figure()
    fig.add_trace(
        go.Histogram(
            x=correct["model_home_win_prob"],
            name="Correct",
            marker_color=COLORS["positive"],
            opacity=0.7,
            nbinsx=30,
        )
    )
    fig.add_trace(
        go.Histogram(
            x=incorrect["model_home_win_prob"],
            name="Incorrect",
            marker_color=COLORS["negative"],
            opacity=0.7,
            nbinsx=30,
        )
    )
    fig.update_layout(
        barmode="overlay",
        template=PLOTLY_TEMPLATE,
        xaxis_title="Model Home Win Probability",
        yaxis_title="Count",
        height=320,
    )
    st.plotly_chart(fig, use_container_width=True)

    # ------------------------------------------------------------------
    # Model probability by score differential (box plot)
    # ------------------------------------------------------------------
    section_header(
        "Model Probability by Score Differential",
        "Pre-game model probability grouped by actual game outcome margin",
    )

    df_box = df.copy()
    df_box["score_diff_binned"] = df_box["score_diff"].clip(-6, 6)

    fig = go.Figure()
    for diff in sorted(df_box["score_diff_binned"].unique()):
        subset = df_box[df_box["score_diff_binned"] == diff]
        label = f"{diff:+d}" if abs(diff) < 6 else (f"{diff:+d}+" if diff > 0 else f"{diff:+d}+")
        fig.add_trace(
            go.Box(
                y=subset["model_home_win_prob"],
                name=label,
                marker_color=COLORS["primary"],
                boxmean=True,
            )
        )
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        xaxis_title="Home Score Differential (actual)",
        yaxis_title="Model Home Win Prob (pre-game)",
        height=380,
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)
