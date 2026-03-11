"""
Convert Baseball HQ skills metrics into 8-category PA outcome rates.

Uses calibrated relationships between BHQ leading indicators and actual
Statcast outcomes. The key insight: BHQ metrics like Ct%, BB%, Brl%
predict future outcomes better than raw counting stats because they
measure underlying skill rather than noisy results.

Calibration (BHQ year-N → actual year-N+1, 2023→2024, 394+ players):
  Hitters:  (1-Ct%) → K  r=0.747 | BB% → BB  r=0.608 | Brl% → HR  r=0.526
  Pitchers: K% → K  r=0.475 | BB% → BB  r=0.320 | xHR/FB → HR  r=0.310
"""

import numpy as np
import pandas as pd
from typing import Optional

from src.simulation.constants import LEAGUE_RATES

OUTCOMES = ["K", "BB", "HBP", "HR", "3B", "2B", "1B", "OUT"]

# League average BIP fractions (2021-2024 Statcast)
_LG_HR_OF_BIP = 0.0461
_LG_3B_OF_BIP = 0.0056
_LG_2B_OF_BIP = 0.0654
_LG_1B_OF_BIP = 0.2133
_LG_OUT_OF_BIP = 0.6696

# Barrel-to-HR calibration: from BHQ Brl% quartile analysis
# Q1: Brl%=2.8% -> HR/BIP=2.7%, Q4: Brl%=14.0% -> HR/BIP=5.8%
# Approximately: HR_of_BIP = 0.015 + 0.30 * Brl%
_BRL_HR_INTERCEPT = 0.015
_BRL_HR_SLOPE = 0.30


def bhq_hitter_to_rates(row: pd.Series, league: Optional[dict] = None) -> dict:
    """
    Convert one BHQ hitter row to PA outcome rate dict.

    Uses BHQ skill metrics as primary drivers:
      K  = 1 - Ct% (contact rate is the most stable K predictor, r=0.747)
      BB = BB% (direct, r=0.608)
      HBP = league average (BHQ doesn't track this)
      HR = derived from Brl% via calibrated barrel-to-HR conversion (r=0.526)
      3B = SPD-scaled league average
      2B = residual from xBA-implied hit rate minus HR, 3B, 1B
      1B = derived from contact quality (Ct%, H%) and hit distribution
      OUT = residual (1 - sum of others)
    """
    if league is None:
        league = LEAGUE_RATES

    # --- K rate from contact rate ---
    # BHQ Ct% includes fouls as "contact", so (1-Ct%) overcounts K by ~13%.
    # Calibrated: K = 0.620 * (1-Ct%) + 0.065  (r=0.747, 2023->2024)
    ct = row.get("Ct%")
    if pd.notna(ct) and ct > 0:
        k_rate = 0.620 * (1.0 - ct) + 0.065
    else:
        k_rate = league["K"]

    # --- BB rate ---
    bb = row.get("BB%")
    if pd.notna(bb) and bb > 0:
        bb_rate = bb
    else:
        bb_rate = league["BB"]

    # --- HBP (not in BHQ) ---
    hbp_rate = league["HBP"]

    # Balls in play fraction
    bip_rate = max(1.0 - k_rate - bb_rate - hbp_rate, 0.20)

    # --- HR from barrel rate ---
    brl = row.get("Brl%")
    fb_pct = row.get("FB%")
    px = row.get("PX")

    if pd.notna(brl) and brl > 0:
        # Calibrated: HR/BIP scales with barrel rate
        hr_of_bip = _BRL_HR_INTERCEPT + _BRL_HR_SLOPE * brl
        hr_rate = bip_rate * hr_of_bip
    elif pd.notna(px) and px > 0:
        # Fallback: PX (power index) scaled to league HR rate
        hr_rate = league["HR"] * (px / 100.0)
    else:
        hr_rate = league["HR"]

    # --- 3B from speed ---
    spd = row.get("SPD")
    if pd.notna(spd) and spd > 0:
        triple_rate = bip_rate * _LG_3B_OF_BIP * (spd / 100.0)
    else:
        triple_rate = league["3B"]

    # --- Hit rate and distribution ---
    # Total hits/BIP from H% (BABIP + HR)
    h_pct = row.get("H%")
    xba = row.get("xBA")

    if pd.notna(h_pct) and h_pct > 0:
        # H% is BABIP (hits on balls in play, excluding HR in BHQ's definition)
        # Total hit rate = HR + BABIP * (BIP - HR_component)
        babip_hits = h_pct * (bip_rate - hr_rate)
        total_hit_rate = hr_rate + babip_hits
    elif pd.notna(xba) and xba > 0:
        # xBA approximation: xBA ≈ total_hits / PA (including HR)
        total_hit_rate = xba
    else:
        total_hit_rate = league["HR"] + league["3B"] + league["2B"] + league["1B"]

    # --- 2B from GB/LD/FB distribution ---
    gb_pct = row.get("GB%")
    ld_pct = row.get("LD%")

    if pd.notna(fb_pct) and pd.notna(gb_pct):
        # More fly balls = more HR but fewer doubles
        # More ground balls = fewer extra base hits
        # Doubles come mostly from line drives and gap hits
        double_of_bip = _LG_2B_OF_BIP
        if pd.notna(ld_pct):
            # Scale by LD% relative to league avg (~19.6%)
            double_of_bip *= (ld_pct / 0.196) if ld_pct > 0 else 1.0
        double_rate = bip_rate * double_of_bip
    else:
        double_rate = league["2B"]

    # --- 1B = total hits - HR - 3B - 2B ---
    single_rate = total_hit_rate - hr_rate - triple_rate - double_rate
    single_rate = max(single_rate, 0.01)

    # --- OUT = residual ---
    out_rate = 1.0 - k_rate - bb_rate - hbp_rate - hr_rate - triple_rate - double_rate - single_rate
    out_rate = max(out_rate, 0.10)

    # Normalize
    rates = {
        "K": k_rate, "BB": bb_rate, "HBP": hbp_rate,
        "HR": hr_rate, "3B": triple_rate, "2B": double_rate,
        "1B": single_rate, "OUT": out_rate,
    }
    total = sum(rates.values())
    rates = {o: v / total for o, v in rates.items()}
    return rates


def bhq_pitcher_to_rates(row: pd.Series, league: Optional[dict] = None) -> dict:
    """
    Convert one BHQ pitcher row (merged advanced + bb) to PA outcome rate dict.

    Uses:
      K  = K% (direct, r=0.475) with SwK% as quality cross-check
      BB = BB% (direct, r=0.320)
      HR = derived from xHR/FB and FB% (r=0.310) and GB% (r=-0.310)
      Others = league-average BIP distribution, adjusted by GB/LD/FB splits
    """
    if league is None:
        league = LEAGUE_RATES

    # --- K rate ---
    k_pct = row.get("K%")
    swk = row.get("SwK%")
    if pd.notna(k_pct) and k_pct > 0:
        k_rate = k_pct
    elif pd.notna(swk) and swk > 0:
        # SwK% to K% approximation: K% ≈ SwK% * 2.0 (rough calibration)
        k_rate = min(swk * 2.0, 0.40)
    else:
        k_rate = league["K"]

    # --- BB rate ---
    bb = row.get("BB%")
    if pd.notna(bb) and bb > 0:
        bb_rate = bb
    else:
        bb_rate = league["BB"]

    # --- HBP ---
    hbp_rate = league["HBP"]

    bip_rate = max(1.0 - k_rate - bb_rate - hbp_rate, 0.20)

    # --- HR from xHR/FB and FB% ---
    xhr_fb = row.get("xHR/FB")
    fb_pct = row.get("FB%")
    gb_pct = row.get("GB%")
    hr_per_9 = row.get("HR/9")

    if pd.notna(xhr_fb) and pd.notna(fb_pct) and fb_pct > 0:
        # BHQ xHR/FB scale: 1.0 = 10% HR/FB (league avg). Divide by 10 to get ratio.
        hr_fb_rate = xhr_fb / 10.0
        hr_rate = bip_rate * fb_pct * hr_fb_rate
    elif pd.notna(gb_pct):
        # High GB% = fewer HR
        gb_factor = (1.0 - gb_pct) / (1.0 - 0.43)  # 0.43 is league avg GB%
        hr_rate = league["HR"] * gb_factor
    else:
        hr_rate = league["HR"]

    # --- BIP distribution from GB/LD/FB ---
    if pd.notna(gb_pct) and pd.notna(fb_pct):
        ld_pct = row.get("LD%")
        if pd.isna(ld_pct):
            ld_pct = 1.0 - gb_pct - fb_pct

        # 3B: ground balls suppress triples
        triple_rate = bip_rate * _LG_3B_OF_BIP * ((1.0 - gb_pct) / 0.57)

        # 2B: line drives drive doubles
        double_of_bip = _LG_2B_OF_BIP * (ld_pct / 0.196) if ld_pct > 0 else _LG_2B_OF_BIP
        double_rate = bip_rate * double_of_bip

        # 1B: H% (BABIP) drives singles
        h_pct = row.get("H%")
        if pd.notna(h_pct) and h_pct > 0:
            total_hit_rate_bip = h_pct
            single_rate = bip_rate * total_hit_rate_bip - hr_rate - triple_rate - double_rate
            single_rate = max(single_rate, 0.01)
        else:
            single_rate = bip_rate * _LG_1B_OF_BIP
    else:
        triple_rate = league["3B"]
        double_rate = league["2B"]
        single_rate = league["1B"]

    # --- OUT = residual ---
    out_rate = 1.0 - k_rate - bb_rate - hbp_rate - hr_rate - triple_rate - double_rate - single_rate
    out_rate = max(out_rate, 0.10)

    rates = {
        "K": k_rate, "BB": bb_rate, "HBP": hbp_rate,
        "HR": hr_rate, "3B": triple_rate, "2B": double_rate,
        "1B": single_rate, "OUT": out_rate,
    }
    total = sum(rates.values())
    rates = {o: v / total for o, v in rates.items()}
    return rates


def convert_bhq_hitters(bhq_df: pd.DataFrame, league: Optional[dict] = None) -> dict:
    """
    Convert all BHQ hitters to rate dicts.
    Returns {mlbamid: {"rates": {outcome: prob}, ...}}
    """
    result = {}
    for mlbamid, row in bhq_df.iterrows():
        rates = bhq_hitter_to_rates(row, league)
        result[int(mlbamid)] = {"rates": rates}
    return result


def convert_bhq_pitchers(bhq_df: pd.DataFrame, league: Optional[dict] = None) -> dict:
    """
    Convert all BHQ pitchers to rate dicts.
    Returns {mlbamid: {"rates": {outcome: prob}, ...}}
    """
    result = {}
    for mlbamid, row in bhq_df.iterrows():
        rates = bhq_pitcher_to_rates(row, league)
        result[int(mlbamid)] = {"rates": rates}
    return result
