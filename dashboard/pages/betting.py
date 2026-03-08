"""Betting page — bet-level analysis, ROI breakdowns, drawdown."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils import COLORS, PLOTLY_TEMPLATE, section_header, format_american_odds


def render_betting(df: pd.DataFrame):
    st.title("Betting")
    st.caption("Bet-level detail, ROI breakdowns, and risk analysis.")

    bets = df[df["has_bet"]].copy()
    totals_bets = df[df["has_totals_bet"]].copy()

    # ------------------------------------------------------------------
    # Summary metrics — ML
    # ------------------------------------------------------------------
    ml_staked = bets["bet_stake"].sum() if len(bets) else 0
    ml_profit = bets["bet_profit"].sum() if len(bets) else 0
    ml_roi = (ml_profit / ml_staked * 100) if ml_staked else 0
    ml_wr = bets["bet_won"].mean() * 100 if len(bets) else 0

    t_staked = totals_bets["totals_bet_stake"].sum() if len(totals_bets) else 0
    t_profit = totals_bets["totals_bet_profit"].sum() if len(totals_bets) else 0
    t_roi = (t_profit / t_staked * 100) if t_staked else 0
    t_wr = totals_bets["totals_bet_won"].mean() * 100 if len(totals_bets) else 0

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("ML Bets", f"{len(bets):,}")
    c2.metric("ML ROI", f"{ml_roi:+.1f}%")
    c3.metric("ML Win Rate", f"{ml_wr:.1f}%")
    c4.metric("Totals Bets", f"{len(totals_bets):,}")
    c5.metric("Totals ROI", f"{t_roi:+.1f}%")
    c6.metric("Totals Win Rate", f"{t_wr:.1f}%")

    # ------------------------------------------------------------------
    # Inline filters
    # ------------------------------------------------------------------
    st.markdown("")
    f1, f2, f3 = st.columns(3)
    edge_min = f1.slider(
        "Min edge (%)", 0.0, 50.0, 0.0, 1.0,
        help="Filter bets to only show those above this edge threshold.",
    )
    outcome = f2.selectbox("Outcome", ["All", "Won", "Lost"])
    bet_type = f3.selectbox("Bet type", ["All", "Moneyline", "Totals"])

    # ------------------------------------------------------------------
    # Moneyline bets table
    # ------------------------------------------------------------------
    if bet_type in ("All", "Moneyline"):
        section_header("Moneyline Bets")

        filtered_ml = bets.copy()
        if edge_min > 0:
            filtered_ml = filtered_ml[filtered_ml["bet_edge"] * 100 >= edge_min]
        if outcome == "Won":
            filtered_ml = filtered_ml[filtered_ml["bet_won"] == True]
        elif outcome == "Lost":
            filtered_ml = filtered_ml[filtered_ml["bet_won"] == False]

        st.caption(f"{len(filtered_ml):,} bets (of {len(bets):,} total)")

        if len(filtered_ml):
            display = filtered_ml[[
                "date", "home_team", "away_team",
                "best_home_odds", "best_away_odds",
                "model_home_win_prob", "model_away_win_prob",
                "bet_side", "bet_odds", "bet_edge",
                "actual_home_score", "actual_away_score",
                "bet_stake", "bet_profit", "bet_won",
            ]].copy()
            display.columns = [
                "Date", "Home", "Away",
                "Home Odds", "Away Odds",
                "Model Home%", "Model Away%",
                "Bet Side", "Bet Odds", "Edge",
                "Home Score", "Away Score",
                "Stake", "Profit", "Won",
            ]
            display["Score"] = display.apply(
                lambda r: f"{int(r['Home Score'])}-{int(r['Away Score'])}"
                if pd.notna(r["Home Score"]) else "—", axis=1,
            )
            display = display.drop(columns=["Home Score", "Away Score"])
            # Reorder so Score comes after Edge
            cols = display.columns.tolist()
            cols.remove("Score")
            edge_idx = cols.index("Edge") + 1
            cols.insert(edge_idx, "Score")
            display = display[cols]
            display["Date"] = display["Date"].dt.strftime("%Y-%m-%d")
            display["Home Odds"] = display["Home Odds"].apply(format_american_odds)
            display["Away Odds"] = display["Away Odds"].apply(format_american_odds)
            display["Model Home%"] = display["Model Home%"].apply(lambda x: f"{x:.1%}")
            display["Model Away%"] = display["Model Away%"].apply(lambda x: f"{x:.1%}")
            display["Bet Odds"] = display["Bet Odds"].apply(format_american_odds)
            display["Edge"] = display["Edge"].apply(lambda x: f"{x*100:.1f}%")
            display["Stake"] = display["Stake"].apply(lambda x: f"${x:,.0f}")
            display["Profit"] = display["Profit"].apply(lambda x: f"${x:+,.0f}")

            st.dataframe(display, use_container_width=True, hide_index=True)
            csv = filtered_ml.to_csv(index=False)
            st.download_button("Download ML Bets CSV", csv, "ml_bets.csv", "text/csv")
        else:
            st.info("No moneyline bets match the current filters.")

    # ------------------------------------------------------------------
    # Totals bets table
    # ------------------------------------------------------------------
    if bet_type in ("All", "Totals"):
        section_header("Totals (Over/Under) Bets")

        filtered_totals = totals_bets.copy()
        if edge_min > 0:
            filtered_totals = filtered_totals[
                filtered_totals["totals_bet_edge"] * 100 >= edge_min
            ]
        if outcome == "Won":
            filtered_totals = filtered_totals[filtered_totals["totals_bet_won"] == True]
        elif outcome == "Lost":
            filtered_totals = filtered_totals[filtered_totals["totals_bet_won"] == False]

        st.caption(f"{len(filtered_totals):,} bets (of {len(totals_bets):,} total)")

        if len(filtered_totals):
            display = filtered_totals[[
                "date", "home_team", "away_team",
                "total_line",
                "best_over_odds", "best_under_odds",
                "model_over_prob", "model_under_prob",
                "totals_bet_side", "totals_bet_odds", "totals_bet_edge",
                "actual_home_score", "actual_away_score",
                "totals_bet_stake", "totals_bet_profit", "totals_bet_won",
            ]].copy()
            display.columns = [
                "Date", "Home", "Away",
                "Line",
                "Over Odds", "Under Odds",
                "Model Over%", "Model Under%",
                "Bet Side", "Bet Odds", "Edge",
                "Home Score", "Away Score",
                "Stake", "Profit", "Won",
            ]
            display["Score"] = display.apply(
                lambda r: f"{int(r['Home Score'])}-{int(r['Away Score'])}"
                if pd.notna(r["Home Score"]) else "—", axis=1,
            )
            display["Actual"] = display.apply(
                lambda r: int(r["Home Score"]) + int(r["Away Score"])
                if pd.notna(r["Home Score"]) else "—", axis=1,
            )
            display = display.drop(columns=["Home Score", "Away Score"])
            # Reorder: put Score and Actual after Edge
            cols = display.columns.tolist()
            cols.remove("Score")
            cols.remove("Actual")
            edge_idx = cols.index("Edge") + 1
            cols.insert(edge_idx, "Score")
            cols.insert(edge_idx + 1, "Actual")
            display = display[cols]
            display["Date"] = display["Date"].dt.strftime("%Y-%m-%d")
            display["Line"] = display["Line"].apply(
                lambda x: f"{x:.1f}" if pd.notna(x) else "\u2014"
            )
            display["Over Odds"] = display["Over Odds"].apply(format_american_odds)
            display["Under Odds"] = display["Under Odds"].apply(format_american_odds)
            display["Model Over%"] = display["Model Over%"].apply(
                lambda x: f"{x:.1%}" if pd.notna(x) else "\u2014"
            )
            display["Model Under%"] = display["Model Under%"].apply(
                lambda x: f"{x:.1%}" if pd.notna(x) else "\u2014"
            )
            display["Bet Odds"] = display["Bet Odds"].apply(format_american_odds)
            display["Edge"] = display["Edge"].apply(lambda x: f"{x*100:.1f}%")
            display["Stake"] = display["Stake"].apply(lambda x: f"${x:,.0f}")
            display["Profit"] = display["Profit"].apply(lambda x: f"${x:+,.0f}")

            st.dataframe(display, use_container_width=True, hide_index=True)
            csv = filtered_totals.to_csv(index=False)
            st.download_button("Download Totals Bets CSV", csv, "totals_bets.csv", "text/csv")
        else:
            st.info("No totals bets match the current filters.")

    # ------------------------------------------------------------------
    # Edge vs Outcome (jitter plot)
    # ------------------------------------------------------------------
    if len(bets):
        section_header("Edge vs Outcome", "Moneyline bet edges, split by result")

        wins = bets[bets["bet_won"] == True]
        losses = bets[bets["bet_won"] == False]

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=wins["bet_edge"] * 100,
                y=np.random.uniform(-0.3, 0.3, len(wins)),
                mode="markers",
                name="Won",
                marker=dict(color=COLORS["positive"], size=5, opacity=0.6),
                hovertemplate="Edge: %{x:.1f}%<extra></extra>",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=losses["bet_edge"] * 100,
                y=np.random.uniform(0.7, 1.3, len(losses)),
                mode="markers",
                name="Lost",
                marker=dict(color=COLORS["negative"], size=5, opacity=0.6),
                hovertemplate="Edge: %{x:.1f}%<extra></extra>",
            )
        )
        fig.update_layout(
            template=PLOTLY_TEMPLATE,
            xaxis_title="Model Edge (%)",
            yaxis=dict(tickvals=[0, 1], ticktext=["Won", "Lost"], title=""),
            height=250,
        )
        st.plotly_chart(fig, use_container_width=True)

    # ------------------------------------------------------------------
    # ROI breakdowns: by month, by side, by odds range
    # ------------------------------------------------------------------
    if len(bets):
        section_header("ROI Breakdowns")

        # ROI by month — ML
        _render_roi_by_month(bets, "bet_stake", "bet_profit", "ML", COLORS["primary"])

        # ROI by month — Totals
        if len(totals_bets):
            _render_roi_by_month(totals_bets, "totals_bet_stake", "totals_bet_profit", "Totals", COLORS["secondary"])

        # Side-by-side: ROI by side + ROI by odds range
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**ROI by Bet Side**")
            side = bets.groupby("bet_side").agg(
                staked=("bet_stake", "sum"),
                profit=("bet_profit", "sum"),
            ).reset_index()
            side["roi"] = side["profit"] / side["staked"] * 100

            fig = go.Figure(
                go.Bar(
                    x=side["bet_side"],
                    y=side["roi"],
                    marker_color=[COLORS["positive"] if r >= 0 else COLORS["negative"] for r in side["roi"]],
                )
            )
            fig.add_hline(y=0, line_dash="dash", line_color=COLORS["muted"])
            fig.update_layout(
                template=PLOTLY_TEMPLATE,
                yaxis_title="ROI (%)",
                height=300,
            )
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            st.markdown("**ROI by Odds Range**")
            bets_c = bets.copy()
            bins = [-10000, -200, -100, 100, 200, 500, 10000]
            labels = ["< -200", "-200 to -100", "-100 to +100", "+100 to +200", "+200 to +500", "+500+"]
            bets_c["odds_range"] = pd.cut(bets_c["bet_odds"], bins=bins, labels=labels)

            odds_roi = bets_c.groupby("odds_range", observed=True).agg(
                staked=("bet_stake", "sum"),
                profit=("bet_profit", "sum"),
            ).reset_index()
            odds_roi["roi"] = odds_roi["profit"] / odds_roi["staked"] * 100

            fig = go.Figure(
                go.Bar(
                    x=odds_roi["odds_range"].astype(str),
                    y=odds_roi["roi"],
                    marker_color=[COLORS["positive"] if r >= 0 else COLORS["negative"] for r in odds_roi["roi"]],
                )
            )
            fig.add_hline(y=0, line_dash="dash", line_color=COLORS["muted"])
            fig.update_layout(
                template=PLOTLY_TEMPLATE,
                yaxis_title="ROI (%)",
                height=300,
            )
            st.plotly_chart(fig, use_container_width=True)

    # ------------------------------------------------------------------
    # Edge vs Actual Win Rate
    # ------------------------------------------------------------------
    if len(bets):
        section_header("Edge vs Actual Win Rate", "Win rate by edge bucket — larger edges should win more")

        bets_c = bets.copy()
        bets_c["edge_pct"] = bets_c["bet_edge"] * 100
        edge_bins = [3, 5, 10, 15, 20, 30, 100]
        edge_labels = ["3-5%", "5-10%", "10-15%", "15-20%", "20-30%", "30%+"]
        bets_c["edge_bucket"] = pd.cut(bets_c["edge_pct"], bins=edge_bins, labels=edge_labels, right=False)

        edge_wr = bets_c.groupby("edge_bucket", observed=True).agg(
            win_rate=("bet_won", "mean"),
            count=("bet_won", "size"),
        ).reset_index()

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=edge_wr["edge_bucket"].astype(str),
                y=edge_wr["win_rate"],
                mode="markers+lines",
                marker=dict(color=COLORS["primary"], size=edge_wr["count"].clip(5, 30)),
                line=dict(color=COLORS["primary"]),
                hovertemplate="Edge: %{x}<br>Win Rate: %{y:.0%}<br>n=%{customdata}<extra></extra>",
                customdata=edge_wr["count"],
            )
        )
        fig.update_layout(
            template=PLOTLY_TEMPLATE,
            xaxis_title="Edge Bucket",
            yaxis_title="Win Rate",
            yaxis_tickformat=".0%",
            height=300,
        )
        st.plotly_chart(fig, use_container_width=True)

    # ------------------------------------------------------------------
    # Drawdown + Kelly side by side
    # ------------------------------------------------------------------
    if len(bets):
        col1, col2 = st.columns(2)

        with col1:
            section_header("Drawdown Analysis")
            sorted_bets = bets.sort_values("date")
            cum = sorted_bets["bet_profit"].cumsum()
            running_max = cum.cummax()
            drawdown = cum - running_max

            fig = go.Figure()
            fig.add_trace(
                go.Scatter(
                    x=sorted_bets["date"],
                    y=drawdown,
                    mode="lines",
                    fill="tozeroy",
                    fillcolor="rgba(239,68,68,0.15)",
                    line=dict(color=COLORS["negative"], width=1),
                    hovertemplate="Date: %{x|%b %d}<br>Drawdown: $%{y:,.0f}<extra></extra>",
                )
            )
            fig.update_layout(
                template=PLOTLY_TEMPLATE,
                xaxis_title="Date",
                yaxis_title="Drawdown ($)",
                height=300,
            )
            st.plotly_chart(fig, use_container_width=True)
            st.caption(f"Maximum drawdown: ${drawdown.min():,.0f}")

        with col2:
            section_header("Kelly Fraction Distribution")
            if "bet_fraction" in bets.columns:
                fig = go.Figure(
                    go.Histogram(
                        x=bets["bet_fraction"] * 100,
                        nbinsx=20,
                        marker_color=COLORS["primary"],
                    )
                )
                fig.add_vline(x=5, line_dash="dash", line_color=COLORS["negative"], annotation_text="5% cap")
                fig.update_layout(
                    template=PLOTLY_TEMPLATE,
                    xaxis_title="Kelly Fraction (%)",
                    yaxis_title="Count",
                    height=300,
                )
                st.plotly_chart(fig, use_container_width=True)

    # ------------------------------------------------------------------
    # Team performance table
    # ------------------------------------------------------------------
    section_header("Performance by Team")

    rows = []
    all_teams = sorted(set(df["home_team"].unique()) | set(df["away_team"].unique()))

    for team in all_teams:
        team_games = df[(df["home_team"] == team) | (df["away_team"] == team)]

        # Model prob for this team
        home_mask = team_games["home_team"] == team
        team_probs = pd.concat([
            team_games.loc[home_mask, "model_home_win_prob"],
            team_games.loc[~home_mask, "model_away_win_prob"],
        ])

        # Actual wins for this team
        team_wins = (
            (team_games["home_team"] == team) & team_games["actual_home_win"]
        ) | (
            (team_games["away_team"] == team) & ~team_games["actual_home_win"]
        )

        # Bets on this team
        team_bets_home = bets[
            (bets["home_team"] == team) & (bets["bet_side"] == "home")
        ]
        team_bets_away = bets[
            (bets["away_team"] == team) & (bets["bet_side"] == "away")
        ]
        team_bets = pd.concat([team_bets_home, team_bets_away])

        rows.append({
            "Team": team,
            "Games": len(team_games),
            "Avg Model Prob": f"{team_probs.mean():.3f}",
            "Actual Win Rate": f"{team_wins.mean():.3f}",
            "ML Bets": len(team_bets),
            "ML Profit": f"${team_bets['bet_profit'].sum():+,.0f}" if len(team_bets) else "$0",
        })

    team_df = pd.DataFrame(rows)
    st.dataframe(team_df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render_roi_by_month(bets_df, stake_col, profit_col, label, color):
    """Render a monthly ROI bar chart."""
    monthly = bets_df.groupby("month").agg(
        staked=(stake_col, "sum"),
        profit=(profit_col, "sum"),
    ).reset_index()
    monthly["roi"] = monthly["profit"] / monthly["staked"] * 100

    fig = go.Figure(
        go.Bar(
            x=monthly["month"],
            y=monthly["roi"],
            marker_color=[color if r >= 0 else COLORS["negative"] for r in monthly["roi"]],
            hovertemplate="Month: %{x}<br>ROI: %{y:.1f}%<extra></extra>",
        )
    )
    fig.add_hline(y=0, line_dash="dash", line_color=COLORS["muted"])
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        xaxis_title="Month",
        yaxis_title=f"{label} ROI (%)",
        height=280,
    )
    st.plotly_chart(fig, use_container_width=True)
