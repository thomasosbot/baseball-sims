"""How It Works page — plain-English model explainer for sports bettors."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils import COLORS, PLOTLY_TEMPLATE, section_header


# ---------------------------------------------------------------------------
# League-average PA outcome rates (for illustrative charts)
# ---------------------------------------------------------------------------
LEAGUE_AVG = {
    "K": 0.223, "BB": 0.083, "HBP": 0.012, "HR": 0.031,
    "3B": 0.005, "2B": 0.044, "1B": 0.146, "OUT": 0.456,
}


def render_how_it_works(df: pd.DataFrame):
    st.title("How It Works")
    st.caption(
        "A plain-English walkthrough of the model, aimed at someone who bets on "
        "sports but isn't a data scientist."
    )

    # ==================================================================
    # 1. The Big Idea
    # ==================================================================
    section_header("The Big Idea")
    st.markdown(
        """
We simulate every MLB game **10,000 times** to get our own win probabilities.
Then we compare those probabilities to what the sportsbook is offering.
When our number disagrees with the book's number by enough, we bet.

That's it. No gut feelings, no "hot streaks," no narratives. Just math,
run thousands of times, looking for spots where the market has it wrong.
"""
    )

    # ==================================================================
    # 2. Where the Data Comes From
    # ==================================================================
    section_header("Where the Data Comes From")
    st.markdown(
        """
MLB's **Statcast** system tracks every single pitch thrown in a major league
game — velocity, spin rate, launch angle, exit velocity, and the outcome
(strikeout, home run, ground out, etc.).

We pull this data and build a **profile** for every batter and pitcher. A
profile is just a set of rates: how often does this batter strike out? Walk?
Hit a home run? We track 8 outcome categories:

**K** (strikeout), **BB** (walk), **HBP** (hit-by-pitch), **HR** (home run),
**3B** (triple), **2B** (double), **1B** (single), **OUT** (ball in play, out)
"""
    )

    # Sample profile chart — league average vs a hypothetical power hitter
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
            name="Power Hitter (example)",
            marker_color=COLORS["primary"],
            opacity=0.8,
        )
    )
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        barmode="group",
        yaxis_title="Rate",
        yaxis_tickformat=".0%",
        height=300,
        title_text="Batter Profile: Outcome Rates",
        title_font_size=13,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "A power hitter strikes out more and hits more home runs than average. "
        "The model captures these differences for every player."
    )

    # ==================================================================
    # 3. How We Simulate a Game
    # ==================================================================
    section_header("How We Simulate a Game")
    st.markdown(
        """
For each plate appearance in a simulated game, we:

1. **Look up the batter's profile** (his personal rates for K, BB, HR, etc.)
2. **Look up the pitcher's profile** (his rates for giving up Ks, BBs, HRs, etc.)
3. **Blend them together** using a formula that accounts for both players and
   the league average. If a great hitter faces a great pitcher, they partially
   cancel out. If a great hitter faces a bad pitcher, the hitter's advantage
   gets amplified.
4. **Adjust for the ballpark** — Coors Field in Denver boosts home runs,
   Oracle Park in San Francisco suppresses them.
5. **Draw a random outcome** from the resulting probability distribution.
"""
    )

    # PA outcome pie chart example
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
        height=320,
        title_text="Example: Single Plate Appearance Probabilities",
        title_font_size=13,
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown(
        """
After drawing the outcome, we track baserunners, advance them on hits,
score runs, and record outs. When three outs are made, the half-inning
ends. After 9 innings (plus extras if tied), we have a final score.

**Then we do it all over again. Ten thousand times.**

The fraction of simulations the home team wins becomes our win probability.
If the home team wins 6,200 out of 10,000 simulations, our model says
they have a **62% chance** of winning.
"""
    )

    # ==================================================================
    # 4. What Makes the Starting Pitcher So Important
    # ==================================================================
    section_header("What Makes the Starting Pitcher So Important")
    st.markdown(
        """
The starting pitcher faces roughly **21 batters** before getting pulled
(about 6-7 innings). That's a huge chunk of the game controlled by one
player. A dominant starter suppresses runs across all those plate appearances,
while a struggling starter inflates them.

**The Times Through the Order (TTO) penalty:** Starters get worse the more
times they face the same lineup. The first time through, hitters are seeing
the pitcher's stuff fresh. By the third time through, they've adjusted. Our
model accounts for this — a starter's hit rates increase by 10% on the second
time through and 20% on the third.

After the starter is pulled, a **team-specific bullpen profile** takes over.
Bullpen quality varies enormously between teams, and we model that too.
"""
    )

    # ==================================================================
    # 5. How We Find Bets
    # ==================================================================
    section_header("How We Find Bets")
    st.markdown(
        """
Here's the key insight: the sportsbook publishes odds that imply a win
probability. We have our own win probability from the simulation. When
those numbers disagree enough, there's a potential bet.

**Example:**
- The sportsbook says Team A has a **60%** chance (implied by -150 odds)
- Our model says Team A has a **55%** chance
- That's not a bet — the book actually has it about right

But:
- The sportsbook says Team A has a **60%** chance
- Our model says Team A has a **68%** chance
- That's an **8% edge** — worth betting

We require at least a **3% edge** before placing any bet. Below that,
the signal is too noisy to be reliable.
"""
    )

    # Show model vs market histogram if we have odds data
    has_odds = df["has_odds"]
    odds_df = df[has_odds]
    if len(odds_df) > 50:
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
            yaxis_title="Number of Games",
            height=300,
            xaxis=dict(range=[0.15, 0.85], dtick=0.1),
            title_text="Our Probabilities vs the Sportsbook's",
            title_font_size=13,
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "When our distribution (indigo) extends beyond the sportsbook's "
            "(amber) in either direction, we've found a potential edge."
        )

    st.markdown(
        """
**How much do we bet?** We use the **Kelly criterion** — a formula that
sizes bets proportional to your edge. Bigger edge = bigger bet. But we
use **quarter-Kelly** (only 25% of what the formula recommends) because:

- It's more conservative and survives bad streaks better
- We cap every bet at **5% of bankroll** no matter what
- Real-world edges are noisier than theory assumes

In plain terms: we bet more when we're more confident, but we never go crazy.
"""
    )

    # ==================================================================
    # 6. What the Model Gets Right and Wrong
    # ==================================================================
    section_header("What the Model Gets Right and Wrong")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Gets right:**")
        st.markdown(
            """
- Real predictive signal (Brier score beats coin flip)
- Prediction spread now matches market spread
- Accounts for batter-pitcher matchups at the PA level
- Handles small sample sizes with Bayesian regression
- Times-through-the-order adjustment is realistic
- Team-specific bullpen quality
"""
        )

    with col2:
        st.markdown("**Known limitations:**")
        st.markdown(
            """
- Early-season predictions are weaker (less data accumulated)
- No weather adjustments (wind, temperature affect ball flight)
- No umpire adjustments (strike zone varies by ump)
- Baserunning is simplified (no stolen bases, no speed ratings)
- No rest days or travel fatigue modeling
- Lineup order doesn't matter yet (all 9 batters weighted equally)
"""
        )
