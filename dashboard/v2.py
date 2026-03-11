"""
v2 Dashboard — Backtest Results
Run: streamlit run dashboard/v2.py
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from pathlib import Path

BANKROLL = 20_000
DATA_PATH = Path(__file__).parent.parent / "data" / "processed" / "backtest_rolling.csv"

st.set_page_config(page_title="MLB Model — Backtest", layout="wide")

# --- Minimal CSS ---
st.markdown("""
<style>
    [data-testid="stHeader"] { background: rgba(0,0,0,0); }
    .block-container { padding-top: 2rem; max-width: 1200px; }
    h1 { font-size: 1.8rem; font-weight: 700; margin-bottom: 0; }
    h2 { font-size: 1.3rem; font-weight: 600; color: #555; margin-top: 2rem; }
</style>
""", unsafe_allow_html=True)


@st.cache_data
def load_data():
    df = pd.read_csv(DATA_PATH, parse_dates=["date"])
    return df


def compute_cumulative_roi(bets: pd.DataFrame, bankroll: float) -> pd.DataFrame:
    """Compute running ROI from a chronological bet DataFrame."""
    if bets.empty:
        return pd.DataFrame(columns=["date", "cum_profit", "cum_staked", "roi", "balance"])
    bets = bets.sort_values("date").copy()
    bets["cum_profit"] = bets["profit"].cumsum()
    bets["cum_staked"] = bets["stake"].cumsum()
    bets["roi"] = (bets["cum_profit"] / bets["cum_staked"] * 100).round(2)
    bets["balance"] = bankroll + bets["cum_profit"]
    return bets


def extract_sides_bets(df: pd.DataFrame) -> pd.DataFrame:
    """Extract moneyline bets into a clean table."""
    mask = df["bet_side"].notna()
    bets = df[mask].copy()
    if bets.empty:
        return pd.DataFrame()

    rows = []
    for _, r in bets.iterrows():
        is_home = r["bet_side"] == "home"
        team = r["home_team"] if is_home else r["away_team"]
        opponent = r["away_team"] if is_home else r["home_team"]
        model_prob = r["model_home_win_prob"] if is_home else r["model_away_win_prob"]
        market_prob = r["market_home_nv_prob"] if is_home else r["market_away_nv_prob"]

        rows.append({
            "date": r["date"],
            "team": team,
            "opponent": opponent,
            "side": r["bet_side"],
            "odds": int(r["bet_odds"]),
            "model_prob": model_prob,
            "market_prob": market_prob,
            "edge": r["bet_edge"],
            "stake": r["bet_stake"],
            "profit": r["bet_profit"],
            "won": r["bet_won"],
            "score": f"{int(r['actual_home_score'])}–{int(r['actual_away_score'])}" if is_home else f"{int(r['actual_away_score'])}–{int(r['actual_home_score'])}",
        })
    return pd.DataFrame(rows)


def extract_totals_bets(df: pd.DataFrame) -> pd.DataFrame:
    """Extract totals bets into a clean table."""
    mask = df["totals_bet_side"].notna()
    bets = df[mask].copy()
    if bets.empty:
        return pd.DataFrame()

    rows = []
    for _, r in bets.iterrows():
        is_over = r["totals_bet_side"] == "over"
        model_prob = r["model_over_prob"] if is_over else r["model_under_prob"]
        market_prob = r["market_over_nv_prob"] if is_over else r["market_under_nv_prob"]
        actual_total = int(r["actual_home_score"]) + int(r["actual_away_score"])

        rows.append({
            "date": r["date"],
            "matchup": f"{r['away_team']} @ {r['home_team']}",
            "side": r["totals_bet_side"],
            "line": r["total_line"],
            "odds": int(r["totals_bet_odds"]),
            "model_prob": model_prob,
            "market_prob": market_prob,
            "edge": r["totals_bet_edge"],
            "stake": r["totals_bet_stake"],
            "profit": r["totals_bet_profit"],
            "won": r["totals_bet_won"],
            "actual_total": actual_total,
            "score": f"{int(r['actual_home_score'])}–{int(r['actual_away_score'])}",
        })
    return pd.DataFrame(rows)


def format_odds(v):
    return f"+{int(v)}" if v > 0 else str(int(v))


def format_pct(v):
    return f"{v*100:.1f}%"


def format_money(v):
    if v >= 0:
        return f"${v:,.2f}"
    return f"–${abs(v):,.2f}"


def result_icon(won):
    if pd.isna(won):
        return "—"
    return "W" if won else "L"


def color_pnl(val, won):
    """Return P&L string wrapped in colored span."""
    text = format_money(val)
    if pd.isna(won):
        return text
    color = "#16a34a" if won else "#dc2626"
    return f'<span style="color:{color};font-weight:600">{text}</span>'


def render_table(df: pd.DataFrame, columns: list, headers: list, height: int = 400):
    """Render a DataFrame as a styled HTML table."""
    html = '<div style="overflow-y:auto;max-height:' + str(height) + 'px">'
    html += '<table style="width:100%;border-collapse:collapse;font-size:0.85rem">'
    html += '<thead><tr style="border-bottom:2px solid #ddd;position:sticky;top:0;background:#fff">'
    for h in headers:
        html += f'<th style="padding:6px 8px;text-align:left;white-space:nowrap">{h}</th>'
    html += '</tr></thead><tbody>'
    for _, row in df.iterrows():
        html += '<tr style="border-bottom:1px solid #f0f0f0">'
        for col in columns:
            val = row[col]
            html += f'<td style="padding:5px 8px;white-space:nowrap">{val}</td>'
        html += '</tr>'
    html += '</tbody></table></div>'
    st.markdown(html, unsafe_allow_html=True)


# =========================================================================
# LOAD DATA
# =========================================================================

if not DATA_PATH.exists():
    st.error(f"No backtest data found at `{DATA_PATH}`. Run the backtest first.")
    st.stop()

df = load_data()

st.title("MLB Model — 2025 Out-of-Sample Backtest")

sides = extract_sides_bets(df)
totals = extract_totals_bets(df)

# =========================================================================
# KPI ROW
# =========================================================================

n_sides = len(sides)
n_totals = len(totals)
sides_profit = sides["profit"].sum() if n_sides else 0
totals_profit = totals["profit"].sum() if n_totals else 0
sides_staked = sides["stake"].sum() if n_sides else 0
totals_staked = totals["stake"].sum() if n_totals else 0
total_profit = sides_profit + totals_profit

col1, col2, col3, col4 = st.columns(4)
col1.metric("Starting Bankroll", f"${BANKROLL:,.0f}")
col2.metric("Sides P&L", format_money(sides_profit),
            delta=f"{sides_profit/sides_staked*100:+.1f}% ROI" if sides_staked else None)
col3.metric("Totals P&L", format_money(totals_profit),
            delta=f"{totals_profit/totals_staked*100:+.1f}% ROI" if totals_staked else None)
col4.metric("Total P&L", format_money(total_profit),
            delta=f"${BANKROLL + total_profit:,.0f} balance")

# =========================================================================
# CUMULATIVE ROI CHART
# =========================================================================

st.markdown("## Cumulative P&L")

sides_cum = compute_cumulative_roi(sides, BANKROLL)
totals_cum = compute_cumulative_roi(totals, BANKROLL)

# Build combined (all bets chronologically)
all_bets = []
if not sides.empty:
    s = sides[["date", "stake", "profit"]].copy()
    s["type"] = "sides"
    all_bets.append(s)
if not totals.empty:
    t = totals[["date", "stake", "profit"]].copy()
    t["type"] = "totals"
    all_bets.append(t)

fig = go.Figure()

if all_bets:
    combined = pd.concat(all_bets).sort_values("date")
    combined["cum_profit"] = combined["profit"].cumsum()
    combined["balance"] = BANKROLL + combined["cum_profit"]

    fig.add_trace(go.Scatter(
        x=combined["date"], y=combined["balance"],
        name="Total", line=dict(color="#1a1a2e", width=3),
        hovertemplate="%{x|%b %d}<br>Balance: $%{y:,.0f}<extra></extra>",
    ))

if not sides_cum.empty:
    fig.add_trace(go.Scatter(
        x=sides_cum["date"], y=sides_cum["balance"],
        name="Sides", line=dict(color="#4361ee", width=2),
        hovertemplate="%{x|%b %d}<br>Balance: $%{y:,.0f}<extra></extra>",
    ))

if not totals_cum.empty:
    fig.add_trace(go.Scatter(
        x=totals_cum["date"], y=totals_cum["balance"],
        name="Totals", line=dict(color="#f72585", width=2),
        hovertemplate="%{x|%b %d}<br>Balance: $%{y:,.0f}<extra></extra>",
    ))

fig.add_hline(y=BANKROLL, line=dict(color="#ccc", dash="dash", width=1))

fig.update_layout(
    height=450,
    margin=dict(l=0, r=0, t=30, b=0),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    xaxis=dict(title="", showgrid=False),
    yaxis=dict(title="Balance ($)", gridcolor="#f0f0f0", tickprefix="$", tickformat=","),
    plot_bgcolor="white",
    hovermode="x unified",
)

st.plotly_chart(fig, use_container_width=True)

# =========================================================================
# SIDES BET TABLE
# =========================================================================

st.markdown("## Moneyline Bets")

if sides.empty:
    st.info("No moneyline bets in the backtest.")
else:
    display_sides = sides.copy()
    display_sides = display_sides.sort_values("date", ascending=False).reset_index(drop=True)
    display_sides["date"] = display_sides["date"].dt.strftime("%Y-%m-%d")
    display_sides["result"] = display_sides["won"].apply(result_icon)
    display_sides["odds_fmt"] = display_sides["odds"].apply(format_odds)
    display_sides["model"] = display_sides["model_prob"].apply(format_pct)
    display_sides["market"] = display_sides["market_prob"].apply(format_pct)
    display_sides["edge_fmt"] = display_sides["edge"].apply(lambda x: f"{x*100:.1f}%")
    display_sides["stake_fmt"] = display_sides["stake"].apply(lambda x: f"${x:,.0f}")
    display_sides["profit_fmt"] = display_sides.apply(lambda r: color_pnl(r["profit"], r["won"]), axis=1)

    render_table(
        display_sides,
        columns=["date", "team", "opponent", "side", "score", "odds_fmt",
                 "model", "market", "edge_fmt", "stake_fmt", "result", "profit_fmt"],
        headers=["Date", "Team", "Opp", "Side", "Score", "Odds",
                 "Model", "Market", "Edge", "Stake", "", "P&L"],
    )

    sc1, sc2, sc3, sc4 = st.columns(4)
    sc1.metric("Bets", n_sides)
    sc2.metric("Win Rate", f"{sides['won'].mean()*100:.1f}%")
    sc3.metric("Avg Edge", f"{sides['edge'].mean()*100:.1f}%")
    sc4.metric("Avg Odds", format_odds(sides["odds"].mean()))

# =========================================================================
# TOTALS BET TABLE
# =========================================================================

st.markdown("## Totals Bets")

if totals.empty:
    st.info("No totals bets in the backtest.")
else:
    display_totals = totals.copy()
    display_totals = display_totals.sort_values("date", ascending=False).reset_index(drop=True)
    display_totals["date"] = display_totals["date"].dt.strftime("%Y-%m-%d")
    display_totals["result"] = display_totals["won"].apply(result_icon)
    display_totals["odds_fmt"] = display_totals["odds"].apply(format_odds)
    display_totals["model"] = display_totals["model_prob"].apply(format_pct)
    display_totals["market"] = display_totals["market_prob"].apply(format_pct)
    display_totals["edge_fmt"] = display_totals["edge"].apply(lambda x: f"{x*100:.1f}%")
    display_totals["stake_fmt"] = display_totals["stake"].apply(lambda x: f"${x:,.0f}")
    display_totals["profit_fmt"] = display_totals.apply(lambda r: color_pnl(r["profit"], r["won"]), axis=1)
    display_totals["side_line"] = display_totals.apply(
        lambda r: f"{'O' if r['side']=='over' else 'U'} {r['line']}", axis=1
    )

    render_table(
        display_totals,
        columns=["date", "matchup", "side_line", "score", "actual_total", "odds_fmt",
                 "model", "market", "edge_fmt", "stake_fmt", "result", "profit_fmt"],
        headers=["Date", "Matchup", "Pick", "Score", "Total", "Odds",
                 "Model", "Market", "Edge", "Stake", "", "P&L"],
    )

    tc1, tc2, tc3, tc4 = st.columns(4)
    tc1.metric("Bets", n_totals)
    tc2.metric("Win Rate", f"{totals['won'].mean()*100:.1f}%")
    tc3.metric("Avg Edge", f"{totals['edge'].mean()*100:.1f}%")
    tc4.metric("Avg Odds", format_odds(totals["odds"].mean()))
