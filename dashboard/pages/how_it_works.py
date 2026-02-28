"""How It Works page — model introduction and landing screen."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils import COLORS, PLOTLY_TEMPLATE, section_header
from src.backtest.metrics import brier_score


# ---------------------------------------------------------------------------
# League-average PA outcome rates (for illustrative charts)
# ---------------------------------------------------------------------------
LEAGUE_AVG = {
    "K": 0.223, "BB": 0.083, "HBP": 0.012, "HR": 0.031,
    "3B": 0.005, "2B": 0.044, "1B": 0.146, "OUT": 0.456,
}


def render_how_it_works(df: pd.DataFrame):
    # ------------------------------------------------------------------
    # Hero intro
    # ------------------------------------------------------------------
    st.markdown(
        """
# Can a simulation beat the sportsbook?

We built a model that simulates every MLB game **10,000 times**, pitch by
pitch, using real player data. Then we compare our win probabilities to what
the sportsbooks are offering — and bet when we think they're wrong.

No gut feelings. No narratives. Just data, math, and a lot of simulations.
"""
    )

    # Quick-glance stats
    preds = df["model_home_win_prob"].tolist()
    outcomes = df["actual_home_win"].astype(int).tolist()
    bs = brier_score(preds, outcomes)
    accuracy = df["model_correct"].mean() * 100

    bets = df[df["has_bet"]]
    totals_bets = df[df["has_totals_bet"]]
    total_bets = len(bets) + len(totals_bets)
    ml_profit = bets["bet_profit"].sum() if len(bets) else 0
    t_profit = totals_bets["totals_bet_profit"].sum() if len(totals_bets) else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Games Simulated", f"{len(df):,}")
    c2.metric("Prediction Accuracy", f"{accuracy:.1f}%")
    c3.metric("Bets Placed", f"{total_bets:,}")
    c4.metric("Net P&L", f"${ml_profit + t_profit:+,.0f}")

    # ------------------------------------------------------------------
    # The Pipeline — visual overview
    # ------------------------------------------------------------------
    section_header("How It Works", "From raw pitch data to bet recommendations in four steps")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(
            """
**1. Build Player Profiles**

MLB's Statcast system tracks every pitch. We use it to
build a profile for every batter and pitcher — strikeout
rates, home run rates, walk rates, 8 categories total.
"""
        )
    with col2:
        st.markdown(
            """
**2. Simulate Every At-Bat**

When a batter faces a pitcher, we blend their profiles
together, adjust for the ballpark, and draw a random
outcome. Strikeout, walk, homer, single, out — all
weighted by the matchup.
"""
        )
    with col3:
        st.markdown(
            """
**3. Play 10,000 Games**

We simulate the full game — baserunners, scoring, innings,
bullpen changes — then do it 10,000 times. The win
percentage across all simulations becomes our probability.
"""
        )
    with col4:
        st.markdown(
            """
**4. Find the Edge**

We compare our probability to the sportsbook's implied
probability. When we disagree by more than 3%, we bet —
sized by confidence, capped at 5% of bankroll.
"""
        )

    # ------------------------------------------------------------------
    # What goes into a single at-bat
    # ------------------------------------------------------------------
    section_header("Inside a Plate Appearance")

    left, right = st.columns([3, 2])

    with left:
        st.markdown(
            """
Every plate appearance has 8 possible outcomes. The probability of each
depends on who's batting, who's pitching, and where they're playing.

A power hitter facing a weak pitcher at Coors Field? Home run probability
goes up. A contact hitter facing an ace at Oracle Park? Strikeouts go up,
homers go down.

The model blends the batter's rates, the pitcher's rates, and the league
average using a **multiplicative odds-ratio formula** — then adjusts for
park factors and normalizes to a clean probability distribution.
"""
        )

    with right:
        pa_probs = {
            "Strikeout": 0.24, "Walk": 0.09, "HBP": 0.01,
            "Home Run": 0.04, "Triple": 0.005, "Double": 0.05,
            "Single": 0.14, "Out (BIP)": 0.435,
        }
        fig = go.Figure(
            go.Pie(
                labels=list(pa_probs.keys()),
                values=list(pa_probs.values()),
                marker_colors=[
                    COLORS["negative"],   # K
                    COLORS["positive"],   # BB
                    COLORS["secondary"],  # HBP
                    COLORS["primary"],    # HR
                    "#a78bfa",            # 3B
                    "#818cf8",            # 2B
                    "#34d399",            # 1B
                    COLORS["muted"],      # OUT
                ],
                hole=0.4,
                textinfo="label+percent",
                textfont_size=11,
            )
        )
        fig.update_layout(
            template=PLOTLY_TEMPLATE,
            height=300,
            margin=dict(l=0, r=0, t=10, b=10),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)

    # ------------------------------------------------------------------
    # Player profiles — example chart
    # ------------------------------------------------------------------
    section_header("Player Profiles", "How a power hitter looks different from the league average")

    power_hitter = {
        "K": 0.28, "BB": 0.10, "HBP": 0.01, "HR": 0.06,
        "3B": 0.004, "2B": 0.05, "1B": 0.13, "OUT": 0.416,
    }

    categories = list(LEAGUE_AVG.keys())
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=categories,
            y=[LEAGUE_AVG[c] for c in categories],
            name="League Average",
            marker_color=COLORS["muted"],
            opacity=0.7,
        )
    )
    fig.add_trace(
        go.Bar(
            x=categories,
            y=[power_hitter[c] for c in categories],
            name="Power Hitter",
            marker_color=COLORS["primary"],
            opacity=0.8,
        )
    )
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        barmode="group",
        yaxis_title="Rate",
        yaxis_tickformat=".0%",
        height=280,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "More strikeouts, more home runs, fewer ground outs. "
        "The model captures these differences for every player in MLB using Statcast data."
    )

    # ------------------------------------------------------------------
    # Starting pitcher impact + TTO
    # ------------------------------------------------------------------
    section_header("Why the Starting Pitcher Matters Most")
    st.markdown(
        """
The starter faces roughly **21 batters** before getting pulled — about 6-7
innings of a 9-inning game. That's one player controlling the majority of
the opponent's plate appearances.

But starters get worse the longer they pitch. The **Times Through the Order
(TTO) penalty** is real: hitters adjust after seeing a pitcher's repertoire
once. Our model boosts hit rates by 10% on the second time through the
lineup and 20% on the third. After the starter is pulled, a **team-specific
bullpen profile** takes over for the final innings.
"""
    )

    # ------------------------------------------------------------------
    # Finding bets — model vs market
    # ------------------------------------------------------------------
    section_header("Finding the Edge")

    left, right = st.columns([2, 3])

    with left:
        st.markdown(
            """
The sportsbook publishes odds that imply a win probability.
We have our own probability from the simulation. When we
disagree by enough, there's a bet.

**If the book says 60% and we say 55%** — no bet, they're
roughly right.

**If the book says 60% and we say 68%** — that 8% gap is
an edge worth betting.

We use the **Kelly criterion** to size bets: more
confidence = larger bet, but capped at 5% of bankroll
and scaled to quarter-Kelly for safety.
"""
        )

    with right:
        # Model vs market histogram
        has_odds = df["has_odds"]
        odds_df = df[has_odds]
        if len(odds_df) > 20:
            fig = go.Figure()
            fig.add_trace(
                go.Histogram(
                    x=odds_df["model_home_win_prob"],
                    nbinsx=30,
                    name="Our Model",
                    marker_color=COLORS["primary"],
                    opacity=0.6,
                )
            )
            fig.add_trace(
                go.Histogram(
                    x=odds_df["market_home_nv_prob"],
                    nbinsx=30,
                    name="Sportsbook",
                    marker_color=COLORS["secondary"],
                    opacity=0.6,
                )
            )
            fig.update_layout(
                barmode="overlay",
                template=PLOTLY_TEMPLATE,
                xaxis_title="Home Win Probability",
                yaxis_title="Games",
                height=300,
                xaxis=dict(range=[0.15, 0.85], dtick=0.1),
                margin=dict(l=40, r=10, t=10, b=40),
            )
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Where the distributions diverge, we find bets.")

    # ------------------------------------------------------------------
    # What we get right and wrong
    # ------------------------------------------------------------------
    section_header("Strengths and Limitations")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            """
**What the model does well:**
- Beats coin-flip prediction (real signal in the data)
- Probability spread matches the market's range
- Batter-pitcher matchups modeled at the plate appearance level
- Handles small samples with Bayesian regression
- Team-specific bullpen quality
- Times-through-the-order adjustment
"""
        )

    with col2:
        st.markdown(
            """
**What it doesn't do yet:**
- Early-season predictions are weaker (less data)
- No weather adjustments (wind, temperature)
- No umpire adjustments (strike zone varies)
- Baserunning is simplified (no stolen bases)
- No rest days or travel fatigue
- Lineup order doesn't matter yet
"""
        )

    # ------------------------------------------------------------------
    # Navigate prompt
    # ------------------------------------------------------------------
    st.markdown("---")
    st.markdown(
        "Use the sidebar to explore **Performance** (P&L results), "
        "**Predictions** (accuracy analysis), **Betting** (individual bets), "
        "and **Diagnostics** (data health)."
    )
