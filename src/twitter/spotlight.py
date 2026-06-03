"""
Slot 3 of the daily Twitter cadence — Spotlight Matchup (~3:30 PM ET).

Analyst voice (not bettor). Picks the most extreme single-player matchup
from today's picks — typically the top-xwOBA hitter on a picked team
facing the opposing starter — and shows the underlying granularity of
the model (xwOBA, K%, splits).

Card layout (1200x1200, dark navy):
  Eyebrow + date
  "TONIGHT'S SPOTLIGHT MATCHUP" + game label
  HITTER zone (name, team, 4 stat blocks: xwOBA, BRL%, K%, BB%)
  vs
  PITCHER zone (name, team, 4 stat blocks: K%, BB%, xwOBA-against, BRL%-against)
  Conditions strip
  Footer

Usage:
    python3 -m src.twitter.spotlight                       # today
    python3 -m src.twitter.spotlight --date 2026-06-02     # specific date
    python3 -m src.twitter.spotlight --post                # actually tweet
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from src.twitter._card_common import (
    WIDTH, HEIGHT, OUT_DIR, RECEIPTS_URL,
    INK, INK_SOFT, INK_MUT, RULE, BAR_MODEL, BAR_MODEL_TINT,
    _font, paper_bg, centered, hairline,
    load_daily, find_game, hitter_stats, pitcher_stats, stats_complete,
    pct_str, woba_str, load_env, post_image_tweet, call_llm_shared,
)


def _opposing_split(opp_throws_default: str = "R") -> str:
    """Return split key ('vsL' or 'vsR') a hitter sees against this pitcher's hand."""
    return "vsL" if opp_throws_default == "L" else "vsR"


def pick_spotlight(daily: dict) -> dict | None:
    """Find the most extreme hitter-vs-pitcher matchup from today's picks.

    Scans each picked team's lineup; for each hitter with a rollup match, computes
    the hitter's xwOBA against the opposing pitcher's expected handedness side.
    Returns the top such matchup.
    """
    picks = daily.get("picks", [])
    if not picks:
        return None

    candidates = []
    for pick in picks:
        # The picked team is "ours"; the opposing pitcher is what our hitters face
        side = pick.get("side", "")
        team = pick.get("team", "")
        opp = pick.get("opponent", "")
        game = find_game(daily, team, opp)
        if not game:
            continue
        our_is_away = side == "away"
        # The opposing pitcher is on the OTHER side
        opp_pitcher_name = game.get("home_pitcher") if our_is_away else game.get("away_pitcher")
        if not opp_pitcher_name or opp_pitcher_name == "TBD":
            continue
        # Picked team's lineup
        lineup_names = (game.get("away_lineup_names") if our_is_away
                        else game.get("home_lineup_names")) or []
        if not lineup_names:
            continue
        opp_p_stats = pitcher_stats(opp_pitcher_name)
        # Hard requirement: pitcher must have complete stats. We never publish a card
        # with missing stat blocks — if the opposing pitcher's data is incomplete,
        # every hitter in this game is disqualified from the spotlight.
        if not stats_complete(opp_p_stats):
            continue
        for hname in lineup_names:
            h_stats = hitter_stats(hname)
            if not stats_complete(h_stats):
                continue
            h_all = h_stats["all"]
            if h_all.get("pa", 0) < 80:
                continue
            candidates.append({
                "pick": pick,
                "game": game,
                "hitter_name": hname,
                "hitter_team": team,
                "hitter_stats": h_stats,
                "pitcher_name": opp_pitcher_name,
                "pitcher_team": opp,
                "pitcher_stats": opp_p_stats,
                "score": h_all["xwoba"],
            })
    if not candidates:
        return None
    return max(candidates, key=lambda c: c["score"])


def llm_spotlight(c: dict) -> str | None:
    """LLM tweet for the spotlight matchup."""
    h_stats = c["hitter_stats"].get("all", {})
    p_stats = c["pitcher_stats"].get("all", {}) if c["pitcher_stats"] else {}
    h_xw = h_stats.get("xwoba")
    p_xw = p_stats.get("xwoba") if p_stats else None
    system = (
        "You write tweets for an anonymous MLB betting model account. "
        "Voice: cold, matter-of-fact, math-pilled. No vibes, no emojis, no exclamation points, no em dashes. "
        "Speak as 'the model'. Analyst tone, not bettor — surface the matchup, do not pitch a bet."
    )
    user = f"""Write a sharp 1-2 sentence tweet (max 220 chars, NO URL) spotlighting tonight's most lopsided hitter-pitcher matchup. Lead with the numbers. Frame as a matchup the model is tracking, not a pick.

HITTER: {c['hitter_name']} ({c['hitter_team']})
HITTER xwOBA (2025): {woba_str(h_xw)}
HITTER BARREL %: {pct_str(h_stats.get('barrel_pct'))}
HITTER K %: {pct_str(h_stats.get('k_pct'))}

OPPOSING PITCHER: {c['pitcher_name']} ({c['pitcher_team']})
PITCHER xwOBA ALLOWED: {woba_str(p_xw) if p_xw else 'unknown'}
PITCHER K %: {pct_str(p_stats.get('k_pct')) if p_stats else 'unknown'}
PITCHER BARREL %: {pct_str(p_stats.get('barrel_pct')) if p_stats else 'unknown'}

Output only the tweet text."""
    return call_llm_shared(system, user, max_tokens=220)


def fallback_spotlight_tweet(c: dict) -> str:
    h_stats = c["hitter_stats"].get("all", {})
    h_xw = h_stats.get("xwoba")
    return (f"Tonight's matchup the model is tracking: {c['hitter_name']} ({c['hitter_team']}) "
            f"vs {c['pitcher_name']} ({c['pitcher_team']}). "
            f"Hitter sits at {woba_str(h_xw)} xwOBA on the season.")


def _render_stat_blocks(draw, y, blocks: list[tuple[str, str]], inner_w: int, margin: int):
    """4-up labeled stat blocks centered within inner_w."""
    block_w = inner_w // len(blocks)
    f_lab = _font(20, "demi")
    f_val = _font(54, "demi")
    for i, (lab, val) in enumerate(blocks):
        bx_start = margin + i * block_w
        lw = draw.textbbox((0, 0), lab, font=f_lab)[2]
        draw.text((bx_start + (block_w - lw) // 2, y), lab, font=f_lab, fill=INK_MUT)
        vw = draw.textbbox((0, 0), val, font=f_val)[2]
        draw.text((bx_start + (block_w - vw) // 2, y + 30), val, font=f_val, fill=INK)


def render_spotlight_card(c: dict, date: str, output_path: Path) -> Path:
    img = Image.new("RGB", (WIDTH, HEIGHT))
    paper_bg(img)
    draw = ImageDraw.Draw(img)

    margin = 64
    inner_w = WIDTH - margin * 2

    Y_EYEBROW = 56
    Y_HAIRLINE_1 = 114
    Y_LABEL = 158
    Y_GAME = 218
    Y_HAIRLINE_2 = 288
    Y_HITTER_HDR = 324
    Y_HITTER_NAME = 374
    Y_HITTER_STATS = 470
    Y_VS = 614
    Y_HAIRLINE_3 = 660
    Y_PITCHER_HDR = 696
    Y_PITCHER_NAME = 746
    Y_PITCHER_STATS = 842
    Y_HAIRLINE_4 = 986
    Y_COND_LABEL = 1014
    Y_COND_LINE = 1054
    Y_HAIRLINE_5 = HEIGHT - 76
    Y_FOOTER = HEIGHT - 44

    # (1) Eyebrow
    draw.text((margin, Y_EYEBROW), "OZZY ANALYTICS", font=_font(26, "demi"), fill=INK)
    dt_str = datetime.strptime(date, "%Y-%m-%d").strftime("%b %-d, %Y").upper()
    dw = draw.textbbox((0, 0), dt_str, font=_font(26, "regular"))[2]
    draw.text((WIDTH - margin - dw, Y_EYEBROW + 2), dt_str, font=_font(26, "regular"), fill=INK_MUT)
    hairline(draw, margin, Y_HAIRLINE_1, WIDTH - margin, width=2)

    # (2) Section label + game
    centered(draw, "TONIGHT'S SPOTLIGHT MATCHUP", _font(34, "demi"), Y_LABEL, BAR_MODEL)
    game = c["game"]
    away = game.get("away", "")
    home = game.get("home", "")
    centered(draw, f"{away} @ {home}", _font(38, "demi"), Y_GAME, INK)
    hairline(draw, margin, Y_HAIRLINE_2, WIDTH - margin, width=2)

    # (3) HITTER zone
    centered(draw, f"HITTER · {c['hitter_team']}", _font(20, "demi"), Y_HITTER_HDR, INK_MUT)
    # Auto-shrink name if too wide
    name = c["hitter_name"]
    size = 72
    while size > 36:
        f = _font(size, "demi")
        if draw.textbbox((0, 0), name, font=f)[2] <= inner_w:
            break
        size -= 4
    centered(draw, name, _font(size, "demi"), Y_HITTER_NAME, INK)
    h_all = c["hitter_stats"].get("all", {})
    _render_stat_blocks(draw, Y_HITTER_STATS, [
        ("xwOBA", woba_str(h_all.get("xwoba"))),
        ("BRL%", pct_str(h_all.get("barrel_pct"))),
        ("HARD%", pct_str(h_all.get("hard_hit_pct"))),
        ("K%", pct_str(h_all.get("k_pct"))),
    ], inner_w, margin)

    # (4) "vs" divider
    centered(draw, "vs", _font(36, "italic-serif"), Y_VS, INK_MUT)
    hairline(draw, margin, Y_HAIRLINE_3, WIDTH - margin, width=2)

    # (5) PITCHER zone
    centered(draw, f"OPPOSING STARTER · {c['pitcher_team']}", _font(20, "demi"),
             Y_PITCHER_HDR, INK_MUT)
    p_name = c["pitcher_name"]
    size = 72
    while size > 36:
        f = _font(size, "demi")
        if draw.textbbox((0, 0), p_name, font=f)[2] <= inner_w:
            break
        size -= 4
    centered(draw, p_name, _font(size, "demi"), Y_PITCHER_NAME, INK)
    p_stats = c["pitcher_stats"] or {}
    p_all = p_stats.get("all", {}) if p_stats else {}
    _render_stat_blocks(draw, Y_PITCHER_STATS, [
        ("K%", pct_str(p_all.get("k_pct"))),
        ("BB%", pct_str(p_all.get("bb_pct"))),
        ("xwOBA", woba_str(p_all.get("xwoba"))),
        ("BRL%", pct_str(p_all.get("barrel_pct"))),
    ], inner_w, margin)

    # (6) Conditions strip
    hairline(draw, margin, Y_HAIRLINE_4, WIDTH - margin, width=2)
    centered(draw, "CONDITIONS", _font(20, "demi"), Y_COND_LABEL, INK_MUT)
    weather = game.get("weather", {})
    park = game.get("park_factors", {})
    parts = []
    if weather.get("temperature"):
        parts.append(f"{weather['temperature']:.0f}°F")
    wind_spd = weather.get("wind_speed")
    if wind_spd:
        wd = (weather.get("wind_direction", "") or "").upper()
        parts.append(f"{wind_spd:.0f}mph {wd}".strip())
    elif weather.get("condition") == "Dome":
        parts.append("DOME")
    hr_factor = park.get("HR") or park.get("HR_factor")
    if hr_factor:
        parts.append(f"{hr_factor:.2f}x HR")
    runs_factor = park.get("runs")
    if runs_factor and abs(runs_factor - 1.0) > 0.03:
        parts.append(f"{runs_factor:.2f}x runs")
    line = "   ·   ".join(parts) if parts else "Conditions data unavailable."
    centered(draw, line, _font(26, "regular"), Y_COND_LINE, INK)

    # (7) Footer
    hairline(draw, margin, Y_HAIRLINE_5, WIDTH - margin, width=2)
    draw.text((margin, Y_FOOTER), RECEIPTS_URL, font=_font(22, "demi"), fill=BAR_MODEL)
    tag = "10K SIMS · FREE DAILY"
    tw = draw.textbbox((0, 0), tag, font=_font(22, "demi"))[2]
    draw.text((WIDTH - margin - tw, Y_FOOTER), tag, font=_font(22, "demi"), fill=INK_MUT)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), quality=95)
    return output_path


def render_no_spotlight_card(date: str, output_path: Path, games_count: int = 0) -> Path:
    """Fallback if we have no qualifying matchup."""
    img = Image.new("RGB", (WIDTH, HEIGHT))
    paper_bg(img)
    draw = ImageDraw.Draw(img)
    margin = 64
    draw.text((margin, 56), "OZZY ANALYTICS", font=_font(26, "demi"), fill=INK)
    dt_str = datetime.strptime(date, "%Y-%m-%d").strftime("%b %-d, %Y").upper()
    dw = draw.textbbox((0, 0), dt_str, font=_font(26, "regular"))[2]
    draw.text((WIDTH - margin - dw, 58), dt_str, font=_font(26, "regular"), fill=INK_MUT)
    hairline(draw, margin, 114, WIDTH - margin, width=2)
    centered(draw, "TONIGHT'S SPOTLIGHT", _font(34, "demi"), 240, BAR_MODEL)
    centered(draw, "No matchup cleared the threshold.", _font(28, "regular"), 360, INK_SOFT)
    centered(draw, "The model is watching, not pitching.", _font(28, "regular"), 410, INK_SOFT)
    hairline(draw, margin, HEIGHT - 76, WIDTH - margin, width=2)
    draw.text((margin, HEIGHT - 44), RECEIPTS_URL, font=_font(22, "demi"), fill=BAR_MODEL)
    tag = "10K SIMS · FREE DAILY"
    tw = draw.textbbox((0, 0), tag, font=_font(22, "demi"))[2]
    draw.text((WIDTH - margin - tw, HEIGHT - 44), tag, font=_font(22, "demi"), fill=INK_MUT)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), quality=95)
    return output_path


def build_spotlight(date: str) -> dict:
    daily = load_daily(date)
    if daily is None:
        return {"error": f"no daily JSON found for {date}"}
    c = pick_spotlight(daily)
    if c is None:
        out = OUT_DIR / f"spotlight_{date}_nope.png"
        path = render_no_spotlight_card(date, out, len(daily.get("games", [])))
        return {"image": str(path), "tweet": (
            f"Tonight's slate: the model didn't find a single-player matchup it wants to surface. "
            f"Quiet night for the spotlight. {RECEIPTS_URL}"),
                "mode": "no_matchup"}
    tweet = llm_spotlight(c)
    used_llm = tweet is not None
    if not tweet:
        tweet = fallback_spotlight_tweet(c)
    tweet_full = f"{tweet}\n\n{RECEIPTS_URL}"
    out = OUT_DIR / f"spotlight_{date}.png"
    path = render_spotlight_card(c, date, out)
    return {"image": str(path), "tweet": tweet_full, "mode": "spotlight",
            "llm_used": used_llm, "hitter": c["hitter_name"], "pitcher": c["pitcher_name"]}


def main():
    parser = argparse.ArgumentParser(description="Build today's Spotlight matchup tweet")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--post", action="store_true")
    args = parser.parse_args()
    load_env()
    result = build_spotlight(args.date)
    if "error" in result:
        print(f"  ERROR: {result['error']}")
        return 1
    print(f"\n=== SPOTLIGHT for {args.date} ===")
    print(f"  Mode: {result['mode']}")
    if result.get("hitter"):
        print(f"  {result['hitter']} vs {result['pitcher']}")
        print(f"  LLM used: {result['llm_used']}")
    print(f"  Image: {result['image']}")
    print(f"\n  Tweet ({len(result['tweet'])} chars):")
    print(f"  ---")
    for line in result["tweet"].split("\n"):
        print(f"  | {line}")
    print(f"  ---")
    if args.post:
        post_image_tweet(result["image"], result["tweet"])
    else:
        print("\n  (Preview only. Re-run with --post to tweet.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
