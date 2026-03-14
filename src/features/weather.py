"""
Weather adjustments for PA outcome probabilities.

Temperature and wind affect HR/XBH rates. These factors are merged into
the park_factors dict before PA probability computation, so they multiply
onto the same pipeline that park factors already use.

Signal from 2024 MLB data (2,391 games):
  - Temperature: <55F → 7.89 avg runs, 85F+ → 9.54 avg runs (+1.64 swing)
  - Wind out: 8.82, wind in: 8.48, dome: 9.00
"""

from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Dome / roof detection
# ---------------------------------------------------------------------------

def is_dome_game(condition: str, venue: str = "") -> bool:
    """Return True if the game is played indoors (dome or closed roof)."""
    if not condition:
        return False
    cond = str(condition).lower()
    return "dome" in cond or "roof closed" in cond


# ---------------------------------------------------------------------------
# Wind direction classification
# ---------------------------------------------------------------------------

def _classify_wind(wind_direction: str) -> str:
    """
    Classify MLB Stats API wind direction into 'out', 'in', 'cross', or 'none'.

    MLB API reports field-relative directions like "Out To CF", "In From LF",
    "L To R", "R To L", "Varies", or None/empty.
    """
    if not wind_direction or wind_direction == "None":
        return "none"
    d = str(wind_direction).lower()
    if "out to" in d:
        return "out"
    if "in from" in d:
        return "in"
    if "l to r" in d or "r to l" in d or "varies" in d:
        return "cross"
    return "none"


# ---------------------------------------------------------------------------
# Weather factor computation
# ---------------------------------------------------------------------------

# Temperature coefficient: each degree above 72°F adds ~0.2% to HR/XBH rates.
# Derived from 2024 data: ~1.64 run swing over ~30°F range ≈ 0.055 runs/°F,
# distributed across HR and XBH outcomes.
TEMP_COEFF = 0.002

# Wind coefficients (applied to HR only — XBH wind effect is negligible)
WIND_OUT_COEFF = 0.008    # per mph, HR boost when blowing out
WIND_IN_COEFF = 0.006     # per mph, HR suppression when blowing in

# Reference temperature (neutral point)
TEMP_NEUTRAL = 72


def compute_weather_factors(
    temperature: float,
    wind_speed: float = 0,
    wind_direction: str = "",
    condition: str = "",
    venue: str = "",
) -> Optional[Dict[str, float]]:
    """
    Compute multiplicative weather adjustment factors for PA outcomes.

    Returns a dict with keys matching park_factors (HR, 2B, 3B) or None
    if the game is indoors (dome/roof closed → no weather effect).

    These factors get merged into the park_factors dict before
    compute_pa_probabilities() is called.
    """
    # Indoor games: no weather adjustment
    if is_dome_game(condition, venue):
        return None

    # Sanity: if temp is missing or clearly wrong, skip
    if temperature is None or temperature <= 0 or temperature > 130:
        return None

    factors = {}

    # --- Temperature adjustment ---
    # Affects HR, 2B, 3B (all extra-base hits are suppressed in cold / boosted in heat)
    temp_factor = 1.0 + TEMP_COEFF * (temperature - TEMP_NEUTRAL)
    # Clamp to reasonable range (e.g. 30°F → 0.916, 100°F → 1.056)
    temp_factor = max(0.85, min(1.15, temp_factor))

    factors["HR"] = temp_factor
    factors["2B"] = temp_factor
    factors["3B"] = temp_factor

    # --- Wind adjustment (HR only) ---
    wind_class = _classify_wind(wind_direction)
    if wind_speed and wind_speed > 0:
        if wind_class == "out":
            wind_hr = 1.0 + WIND_OUT_COEFF * wind_speed
        elif wind_class == "in":
            wind_hr = 1.0 - WIND_IN_COEFF * wind_speed
        elif wind_class == "cross":
            # Cross-wind has minimal net effect
            wind_hr = 1.0
        else:
            wind_hr = 1.0

        # Clamp wind factor
        wind_hr = max(0.85, min(1.20, wind_hr))
        factors["HR"] *= wind_hr

    return factors


def merge_weather_into_park_factors(
    park_factors: Dict[str, float],
    weather_factors: Optional[Dict[str, float]],
) -> Dict[str, float]:
    """
    Merge weather adjustment factors into existing park factors.

    Weather factors multiply on top of park factors (both are multiplicative
    adjustments applied pre-normalization in compute_pa_probabilities).
    """
    if weather_factors is None:
        return park_factors

    merged = dict(park_factors)
    for key, wf in weather_factors.items():
        if key in merged:
            merged[key] *= wf
        else:
            merged[key] = wf
    return merged


# ---------------------------------------------------------------------------
# Park orientation table (for daily pipeline — compass → field-relative)
# ---------------------------------------------------------------------------

# Approximate home plate compass bearing for each park (degrees from north).
# This is the direction from home plate toward center field.
# Used to convert Open-Meteo compass wind direction to field-relative.
PARK_CF_BEARING = {
    "ARI": 0,    # Chase Field — dome, but included for completeness
    "ATL": 170,
    "BAL": 10,
    "BOS": 120,
    "CHC": 208,
    "CWS": 200,
    "CIN": 240,
    "CLE": 164,
    "COL": 225,
    "DET": 40,
    "HOU": 0,    # Daikin Park — retractable roof
    "KC":  80,
    "LAA": 230,
    "LAD": 335,
    "MIA": 0,    # loanDepot park — retractable roof
    "MIL": 0,    # American Family Field — retractable roof
    "MIN": 345,
    "NYM": 135,
    "NYY": 87,
    "OAK": 285,
    "PHI": 90,
    "PIT": 45,
    "SD":  285,
    "SF":  240,
    "SEA": 0,    # T-Mobile Park — retractable roof
    "STL": 180,
    "TB":  0,    # Tropicana Field — dome
    "TEX": 0,    # Globe Life — retractable roof
    "TOR": 0,    # Rogers Centre — retractable roof
    "WSH": 48,
}

# Retractable roof parks — weather effect only applies when roof is open
RETRACTABLE_ROOF_PARKS = {"ARI", "TEX", "MIL", "HOU", "MIA", "TOR", "SEA"}
FIXED_DOME_PARKS = {"TB"}


def compass_to_field_relative(
    wind_bearing: float,
    park_team: str,
) -> str:
    """
    Convert compass wind direction (degrees, where wind is coming FROM)
    to field-relative direction: 'out', 'in', or 'cross'.

    wind_bearing: compass degrees (0=N, 90=E, 180=S, 270=W) — direction
                  the wind is blowing FROM (meteorological convention).
    park_team: team abbreviation to look up center field bearing.
    """
    cf_bearing = PARK_CF_BEARING.get(park_team, 0)
    if cf_bearing == 0 and park_team in RETRACTABLE_ROOF_PARKS | FIXED_DOME_PARKS:
        return "none"

    # Wind blowing toward CF = "out" (helping batted balls)
    # Wind bearing is where wind comes FROM; wind goes toward bearing+180
    wind_toward = (wind_bearing + 180) % 360

    # Angle between wind direction and CF direction
    diff = abs(wind_toward - cf_bearing)
    if diff > 180:
        diff = 360 - diff

    if diff <= 45:
        return "out"
    elif diff >= 135:
        return "in"
    else:
        return "cross"
