"""MLB Simulation Model Dashboard — main entry point.

Run with:  streamlit run dashboard/app.py
"""

import streamlit as st

from utils import (
    load_backtest_data,
    load_rolling_data,
    apply_filters,
    get_all_teams,
    inject_css,
)
from pages.performance import render_performance
from pages.how_it_works import render_how_it_works
from pages.predictions import render_predictions
from pages.betting import render_betting
from pages.diagnostics import render_diagnostics

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    layout="wide",
    page_title="MLB Sim Model",
    page_icon="\u26be",
)

inject_css()

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("MLB Sim Model")

page = st.sidebar.radio(
    "Navigate",
    ["Performance", "How It Works", "Predictions", "Betting", "Diagnostics"],
    label_visibility="collapsed",
)

st.sidebar.divider()

# Data source toggle
data_source = st.sidebar.radio(
    "Data source",
    ["Full Backtest", "Rolling Window"],
    help="Full Backtest uses the complete season data. Rolling Window uses only data available at each game date (no look-ahead).",
)

# Load data
if data_source == "Full Backtest":
    raw_df = load_backtest_data()
else:
    rolling = load_rolling_data()
    if rolling is not None:
        raw_df = rolling
    else:
        st.sidebar.warning("Rolling data unavailable or too small. Falling back to full backtest.")
        raw_df = load_backtest_data()

# Also load rolling data separately for diagnostics comparison
rolling_df = load_rolling_data()

# Filters
st.sidebar.divider()
st.sidebar.subheader("Filters")

min_date = raw_df["date"].min().date()
max_date = raw_df["date"].max().date()
date_range = st.sidebar.date_input(
    "Date range",
    value=(min_date, max_date),
    min_value=min_date,
    max_value=max_date,
)

teams = st.sidebar.multiselect(
    "Teams",
    options=get_all_teams(raw_df),
    default=[],
    placeholder="All teams",
)

df = apply_filters(raw_df, date_range if len(date_range) == 2 else None, teams)

st.sidebar.caption(f"{len(df):,} games loaded")

st.sidebar.divider()
st.sidebar.markdown('<div class="sidebar-version">v0.7</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Page routing
# ---------------------------------------------------------------------------
if page == "Performance":
    render_performance(df)
elif page == "How It Works":
    render_how_it_works(df)
elif page == "Predictions":
    render_predictions(df)
elif page == "Betting":
    render_betting(df)
elif page == "Diagnostics":
    render_diagnostics(df, rolling_df, load_backtest_data())
