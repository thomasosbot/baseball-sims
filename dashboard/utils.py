"""Shared utilities for the dashboard: data loading, colors, CSS, helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

# Allow imports from repo root
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = ROOT / "data" / "processed"
BACKTEST_CSV = DATA_DIR / "backtest_results.csv"
ROLLING_CSV = DATA_DIR / "backtest_rolling.csv"

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
COLORS = {
    "positive": "#22c55e",   # green — profit/wins
    "negative": "#ef4444",   # red — losses
    "primary": "#6366f1",    # indigo — model data
    "secondary": "#f59e0b",  # amber — market/comparison
    "muted": "#94a3b8",      # slate — reference lines
    "surface": "#f8fafc",    # light background
    "border": "#e2e8f0",     # subtle borders
    "grid": "#f1f5f9",       # very faint gridlines
}

# Legacy aliases (keep existing code working during transition)
GREEN = COLORS["positive"]
RED = COLORS["negative"]
BLUE = COLORS["primary"]
ORANGE = COLORS["secondary"]
GRAY = COLORS["muted"]

# ---------------------------------------------------------------------------
# Custom Plotly template
# ---------------------------------------------------------------------------
_minimal = go.layout.Template()
_minimal.layout = go.Layout(
    font=dict(family="Inter, system-ui, sans-serif", size=12, color="#334155"),
    paper_bgcolor="white",
    plot_bgcolor="white",
    xaxis=dict(
        gridcolor=COLORS["grid"],
        gridwidth=1,
        linecolor=COLORS["border"],
        linewidth=1,
        zeroline=False,
        title_font=dict(size=12, color="#64748b"),
    ),
    yaxis=dict(
        gridcolor=COLORS["grid"],
        gridwidth=1,
        linecolor=COLORS["border"],
        linewidth=1,
        zeroline=False,
        title_font=dict(size=12, color="#64748b"),
    ),
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="left",
        x=0,
        font=dict(size=11),
    ),
    hoverlabel=dict(
        bgcolor="white",
        bordercolor=COLORS["border"],
        font=dict(size=12, color="#1e293b"),
    ),
    margin=dict(l=40, r=20, t=30, b=40),
)
pio.templates["minimal"] = _minimal
PLOTLY_TEMPLATE = "minimal"

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
CSS = """
<style>
/* Hide footer */
footer {visibility: hidden;}

/* Metric cards */
[data-testid="stMetric"] {
    background: rgba(128, 128, 128, 0.06);
    border: 1px solid rgba(128, 128, 128, 0.15);
    border-radius: 8px;
    padding: 12px 16px;
}
[data-testid="stMetric"] label {
    font-size: 0.7rem;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    opacity: 0.65;
}
[data-testid="stMetric"] [data-testid="stMetricValue"] {
    font-size: 1.4rem;
    font-weight: 600;
}

/* Tighter dataframe font */
[data-testid="stDataFrame"] {
    font-size: 0.85rem;
}

/* Section headers */
.section-header {
    border-bottom: 2px solid rgba(128, 128, 128, 0.2);
    padding-bottom: 6px;
    margin-bottom: 16px;
    margin-top: 32px;
}
.section-header h3 {
    margin: 0;
    font-weight: 600;
}
.section-header .caption {
    font-size: 0.8rem;
    opacity: 0.6;
    margin-top: 2px;
}

/* Sidebar version label */
[data-testid="stSidebar"] .sidebar-version {
    font-size: 0.7rem;
    opacity: 0.5;
    text-align: center;
    padding-top: 16px;
}
</style>
"""


def inject_css():
    """Inject custom CSS into the Streamlit page."""
    st.markdown(CSS, unsafe_allow_html=True)


def section_header(title: str, caption: str = ""):
    """Render a styled section header with optional caption."""
    cap_html = f'<div class="caption">{caption}</div>' if caption else ""
    st.markdown(
        f'<div class="section-header"><h3>{title}</h3>{cap_html}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

@st.cache_data
def load_backtest_data() -> pd.DataFrame:
    """Load full-season backtest results and compute derived columns."""
    df = pd.read_csv(BACKTEST_CSV, parse_dates=["date"])
    return _add_derived_columns(df)


@st.cache_data
def load_rolling_data() -> Optional[pd.DataFrame]:
    """Load rolling-window backtest. Returns None if unavailable/tiny."""
    if not ROLLING_CSV.exists():
        return None
    df = pd.read_csv(ROLLING_CSV, parse_dates=["date"])
    if len(df) < 10:
        return None
    return _add_derived_columns(df)


def _add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Compute columns shared across pages."""
    # Model correctness
    df["model_correct"] = (
        (df["model_home_win_prob"] > 0.5) == df["actual_home_win"]
    )

    # Month label
    df["month"] = df["date"].dt.to_period("M").astype(str)

    # Cumulative profit (only for rows with bets)
    if "bet_profit" in df.columns:
        bets = df["bet_profit"].notna()
        df["cum_profit"] = 0.0
        df.loc[bets, "cum_profit"] = df.loc[bets, "bet_profit"].cumsum()
    else:
        df["bet_profit"] = float("nan")
        df["cum_profit"] = 0.0

    # Has odds / has bet flags
    if "market_home_nv_prob" in df.columns:
        df["has_odds"] = df["market_home_nv_prob"].notna()
    else:
        df["market_home_nv_prob"] = float("nan")
        df["has_odds"] = False

    if "bet_side" in df.columns:
        df["has_bet"] = df["bet_side"].notna()
    else:
        df["bet_side"] = None
        df["has_bet"] = False

    # Totals betting flags and cumulative profit
    if "totals_bet_side" in df.columns:
        df["has_totals_bet"] = df["totals_bet_side"].notna()
        totals_bets = df["has_totals_bet"]
        df["cum_totals_profit"] = 0.0
        df.loc[totals_bets, "cum_totals_profit"] = (
            df.loc[totals_bets, "totals_bet_profit"].cumsum()
        )
    else:
        df["has_totals_bet"] = False
        df["cum_totals_profit"] = 0.0

    # Combined ML + totals profit
    ml_profit = df["bet_profit"].fillna(0)
    totals_profit = df["totals_bet_profit"].fillna(0) if "totals_bet_profit" in df.columns else 0
    df["combined_profit"] = ml_profit + totals_profit
    has_any_bet = df["has_bet"] | df["has_totals_bet"]
    df["cum_combined_profit"] = 0.0
    df.loc[has_any_bet, "cum_combined_profit"] = (
        df.loc[has_any_bet, "combined_profit"].cumsum()
    )

    # Score differential
    df["score_diff"] = df["actual_home_score"] - df["actual_away_score"]

    return df


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def format_american_odds(odds: float) -> str:
    """Format a numeric American odds value to string (e.g. +150, -200)."""
    if pd.isna(odds):
        return "\u2014"
    odds = int(round(odds))
    return f"+{odds}" if odds > 0 else str(odds)


def color_profit(val: float) -> str:
    """Return green or red hex based on sign."""
    return COLORS["positive"] if val >= 0 else COLORS["negative"]


def apply_filters(df: pd.DataFrame, date_range, teams) -> pd.DataFrame:
    """Apply sidebar date-range and team filters."""
    filtered = df.copy()
    if date_range:
        start, end = date_range
        filtered = filtered[
            (filtered["date"] >= pd.Timestamp(start))
            & (filtered["date"] <= pd.Timestamp(end))
        ]
    if teams:
        filtered = filtered[
            filtered["home_team"].isin(teams) | filtered["away_team"].isin(teams)
        ]
    return filtered


def get_all_teams(df: pd.DataFrame) -> list:
    """Sorted list of unique team abbreviations."""
    teams = set(df["home_team"].unique()) | set(df["away_team"].unique())
    return sorted(teams)
