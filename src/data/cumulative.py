"""
Cumulative stat tracker for rolling-window backtests.

Maintains running PA outcome counts per batter and pitcher so we can
snapshot rates at any point in the season without look-ahead bias.
"""

from collections import defaultdict
import numpy as np
import pandas as pd

from src.data.process import OUTCOMES


class CumulativeStats:
    """
    Incrementally accumulates PA outcomes from Statcast data, day by day.

    Internal state per player:
        {player_id: {"total": N, "K": N, "BB": N, ..., "K_vsL": N, ...}}

    Call update_from_day() with each date's PAs, then snapshot rates
    via to_batter_rates_df() / to_pitcher_rates_df() before simulating
    that date's games.
    """

    def __init__(self):
        # {batter_id: {"total": float, "K": float, ..., "pa_vsL": float, "K_vsL": float, ...}}
        # Floats to support fractional counts from discounted prior-year data.
        self._batters = defaultdict(lambda: defaultdict(float))
        # {pitcher_id: {"total": float, "K": float, ..., "bf_vsL": float, "K_vsL": float, ...}}
        self._pitchers = defaultdict(lambda: defaultdict(float))
        # {batter_id: set("L", "R")} — observed stand values
        self._batter_stands = defaultdict(set)
        # {pitcher_id: most recent p_throws value}
        self._pitcher_throws = {}
        # {batter_id: most recent player_name}
        self._batter_names = {}
        # {pitcher_id: most recent player_name}
        self._pitcher_names = {}
        # {team_abbrev: set(pitcher_id)} — known relievers per team
        self._team_relievers = defaultdict(set)

    def update_from_day(self, day_pa: pd.DataFrame):
        """
        Ingest one date's worth of PA events (already classified).

        Expects columns: batter, pitcher, outcome, stand, p_throws, player_name
        """
        for row in day_pa.itertuples(index=False):
            batter_id = int(row.batter)
            pitcher_id = int(row.pitcher)
            outcome = row.outcome
            stand = row.stand       # batter's hitting side
            p_throws = row.p_throws  # pitcher's throwing hand

            # Batter counts
            b = self._batters[batter_id]
            b["total"] += 1
            b[outcome] += 1
            b[f"pa_vs{p_throws}"] += 1
            b[f"{outcome}_vs{p_throws}"] += 1

            # Pitcher counts
            p = self._pitchers[pitcher_id]
            p["total"] += 1
            p[outcome] += 1
            p[f"bf_vs{stand}"] += 1
            p[f"{outcome}_vs{stand}"] += 1

            # Track batter handedness
            if pd.notna(stand):
                self._batter_stands[batter_id].add(stand)

            # Track pitcher throwing hand and names
            if pd.notna(p_throws):
                self._pitcher_throws[pitcher_id] = p_throws

            if hasattr(row, "player_name") and pd.notna(row.player_name):
                self._batter_names[batter_id] = row.player_name

    def init_from_prior_year(self, prior_pa: pd.DataFrame, weight: float = 0.5):
        """
        Seed cumulative stats with prior-year PA data, discounted by weight.

        A weight of 0.5 means 500 prior-year PA count as 250 effective PA,
        producing more regressed profiles than current-year data. As current-
        year PAs accumulate (at weight 1.0), they gradually overtake the prior.

        Expects same columns as update_from_day().
        """
        for row in prior_pa.itertuples(index=False):
            batter_id = int(row.batter)
            pitcher_id = int(row.pitcher)
            outcome = row.outcome
            stand = row.stand
            p_throws = row.p_throws

            b = self._batters[batter_id]
            b["total"] += weight
            b[outcome] += weight
            b[f"pa_vs{p_throws}"] += weight
            b[f"{outcome}_vs{p_throws}"] += weight

            p = self._pitchers[pitcher_id]
            p["total"] += weight
            p[outcome] += weight
            p[f"bf_vs{stand}"] += weight
            p[f"{outcome}_vs{stand}"] += weight

            if pd.notna(stand):
                self._batter_stands[batter_id].add(stand)
            if pd.notna(p_throws):
                self._pitcher_throws[pitcher_id] = p_throws
            if hasattr(row, "player_name") and pd.notna(row.player_name):
                self._batter_names[batter_id] = row.player_name

    def to_batter_rates_df(self) -> pd.DataFrame:
        """
        Convert current cumulative counts to a DataFrame matching the
        format of aggregate_batter_rates() from process.py.

        No min_pa filter — regression handles small samples naturally.
        """
        records = []
        for batter_id, counts in self._batters.items():
            total = counts["total"]
            if total == 0:
                continue

            rec = {
                "batter_id": batter_id,
                "name": self._batter_names.get(batter_id, str(batter_id)),
                "total_pa": total,
            }

            # Overall rates
            for o in OUTCOMES:
                rec[f"rate_{o}"] = counts[o] / total

            # Platoon splits
            for ph in ("L", "R"):
                n = counts[f"pa_vs{ph}"]
                rec[f"pa_vs{ph}"] = n
                for o in OUTCOMES:
                    if n >= 20:
                        rec[f"rate_{o}_vs{ph}"] = counts[f"{o}_vs{ph}"] / n
                    else:
                        rec[f"rate_{o}_vs{ph}"] = rec[f"rate_{o}"]

            # Statcast quality metrics not available in rolling mode
            rec["xwOBA"] = np.nan
            rec["hard_hit_rate"] = np.nan
            rec["barrel_rate"] = np.nan

            records.append(rec)

        return pd.DataFrame(records)

    def to_pitcher_rates_df(self) -> pd.DataFrame:
        """
        Convert current cumulative counts to a DataFrame matching the
        format of aggregate_pitcher_rates() from process.py.

        No min_bf filter — regression handles small samples naturally.
        """
        records = []
        for pitcher_id, counts in self._pitchers.items():
            total = counts["total"]
            if total == 0:
                continue

            rec = {
                "pitcher_id": pitcher_id,
                "name": self._pitcher_names.get(pitcher_id, str(pitcher_id)),
                "total_bf": total,
                "throws": self._pitcher_throws.get(pitcher_id, "R"),
            }

            # Overall rates
            for o in OUTCOMES:
                rec[f"rate_{o}"] = counts[o] / total

            # Platoon splits vs batter hand
            for bh in ("L", "R"):
                n = counts[f"bf_vs{bh}"]
                rec[f"bf_vs{bh}"] = n
                for o in OUTCOMES:
                    if n >= 20:
                        rec[f"rate_{o}_vs{bh}"] = counts[f"{o}_vs{bh}"] / n
                    else:
                        rec[f"rate_{o}_vs{bh}"] = rec[f"rate_{o}"]

            # Batted-ball metrics not available in rolling mode
            rec["xwOBA_against"] = np.nan
            rec["gb_rate"] = np.nan
            rec["fb_rate"] = np.nan
            rec["ld_rate"] = np.nan

            records.append(rec)

        return pd.DataFrame(records)

    def get_batter_handedness(self) -> dict:
        """Return {batter_id: 'L' | 'R' | 'S'} from observed stand values."""
        hand = {}
        for batter_id, stands in self._batter_stands.items():
            if len(stands) > 1:
                hand[batter_id] = "S"
            elif len(stands) == 1:
                hand[batter_id] = next(iter(stands))
            else:
                hand[batter_id] = "R"
        return hand

    def register_reliever(self, pitcher_id: int, team: str):
        """Record a pitcher as a reliever for a given team."""
        self._team_relievers[team].add(pitcher_id)

    def get_team_reliever_rates(self) -> dict:
        """
        Export reliever rates grouped by team.

        Returns {team_abbrev: DataFrame} matching the format of
        aggregate_pitcher_rates() — one row per reliever with their
        cumulative rates from self._pitchers.
        """
        team_dfs = {}
        for team, reliever_ids in self._team_relievers.items():
            records = []
            for pid in reliever_ids:
                counts = self._pitchers.get(pid)
                if counts is None or counts["total"] == 0:
                    continue
                total = counts["total"]
                rec = {
                    "pitcher_id": pid,
                    "name": self._pitcher_names.get(pid, str(pid)),
                    "total_bf": total,
                    "throws": self._pitcher_throws.get(pid, "R"),
                }
                for o in OUTCOMES:
                    rec[f"rate_{o}"] = counts[o] / total
                for bh in ("L", "R"):
                    n = counts[f"bf_vs{bh}"]
                    rec[f"bf_vs{bh}"] = n
                    for o in OUTCOMES:
                        if n >= 20:
                            rec[f"rate_{o}_vs{bh}"] = counts[f"{o}_vs{bh}"] / n
                        else:
                            rec[f"rate_{o}_vs{bh}"] = rec[f"rate_{o}"]
                records.append(rec)
            if records:
                team_dfs[team] = pd.DataFrame(records)
        return team_dfs

    @property
    def num_batters(self) -> int:
        return len(self._batters)

    @property
    def num_pitchers(self) -> int:
        return len(self._pitchers)
