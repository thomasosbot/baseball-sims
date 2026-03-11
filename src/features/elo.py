"""
Elo rating system for MLB teams.

Provides a team-strength signal to widen the simulation's compressed
probability spread.  Updated game-by-game using actual results.

Standard Elo with:
  - K-factor tuned for baseball (~4 per game, low because individual games are noisy)
  - Home field advantage baked into expected score (~24 Elo points)
  - Prior-season ratings regressed 1/3 toward 1500
"""

from typing import Dict, Optional, Tuple

# All 30 MLB team abbreviations
ALL_TEAMS = [
    "ARI", "ATL", "BAL", "BOS", "CHC", "CWS", "CIN", "CLE",
    "COL", "DET", "HOU", "KCR", "LAA", "LAD", "MIA", "MIL",
    "MIN", "NYM", "NYY", "OAK", "PHI", "PIT", "SDP", "SFG",
    "SEA", "STL", "TBR", "TEX", "TOR", "WSN",
]

DEFAULT_RATING = 1500
K_FACTOR = 20          # aggressive K to build spread quickly (~3x standard baseball Elo)
HFA_ELO = 24           # home field advantage in Elo points (~2.5% win prob at 1500)
REGRESSION_FACTOR = 1/4  # regress prior-season Elo toward 1500 (less = more carryover)


class EloRatings:
    """Track and update Elo ratings for all MLB teams."""

    def __init__(self, initial_ratings: Optional[Dict[str, float]] = None):
        if initial_ratings:
            self._ratings = dict(initial_ratings)
        else:
            self._ratings = {team: DEFAULT_RATING for team in ALL_TEAMS}

    def get(self, team: str) -> float:
        return self._ratings.get(team, DEFAULT_RATING)

    def expected_win_prob(self, home_team: str, away_team: str) -> float:
        """Compute expected home win probability from current Elo ratings."""
        home_elo = self.get(home_team) + HFA_ELO
        away_elo = self.get(away_team)
        return 1.0 / (1.0 + 10 ** ((away_elo - home_elo) / 400))

    def update(self, home_team: str, away_team: str, home_won: bool):
        """Update ratings after a game result."""
        expected_home = self.expected_win_prob(home_team, away_team)
        actual_home = 1.0 if home_won else 0.0

        self._ratings[home_team] = self.get(home_team) + K_FACTOR * (actual_home - expected_home)
        self._ratings[away_team] = self.get(away_team) + K_FACTOR * (expected_home - actual_home)

    def regress_to_mean(self):
        """Regress all ratings toward 1500 (call between seasons)."""
        for team in self._ratings:
            self._ratings[team] = DEFAULT_RATING + (1 - REGRESSION_FACTOR) * (
                self._ratings[team] - DEFAULT_RATING
            )

    @property
    def ratings(self) -> Dict[str, float]:
        return dict(self._ratings)

    @property
    def spread(self) -> float:
        """Standard deviation of current ratings."""
        vals = list(self._ratings.values())
        mean = sum(vals) / len(vals)
        return (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5


def build_preseason_elo(schedule_by_year: dict) -> EloRatings:
    """
    Build Elo ratings by replaying prior seasons' results.

    schedule_by_year: {year: list of game dicts with home_team, away_team, home_win}
    Returns Elo ratings after the most recent season, regressed toward mean.
    """
    elo = EloRatings()

    for year in sorted(schedule_by_year.keys()):
        games = schedule_by_year[year]
        for g in games:
            elo.update(g["home_team"], g["away_team"], g["home_win"])
        # Regress between seasons
        elo.regress_to_mean()

    return elo
