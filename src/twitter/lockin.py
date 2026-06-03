"""
Slot 4 of the daily Twitter cadence — Lock-in (~30min before first pitch).

Highest-conviction pick of the day, simpler layout than Market Disagreement.
Voice: urgent, punchy, short. The "first pitch is now" energy.

Card layout (1200x1200, dark navy):
  Eyebrow + date + countdown badge
  HUGE: "LOCK OF THE DAY"
  Big matchup
  PICK + odds (very large)
  Tiny side-by-side: model % vs market %
  Season W-L footer (track record context)
  Footer URL

Usage:
    python3 -m src.twitter.lockin                       # today
    python3 -m src.twitter.lockin --date 2026-06-02     # specific date
    python3 -m src.twitter.lockin --post                # actually tweet
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from src.twitter._card_common import (
    WIDTH, HEIGHT, ROOT, OUT_DIR, RECEIPTS_URL,
    INK, INK_SOFT, INK_MUT, RULE, BAR_MODEL, BAR_MODEL_TINT,
    _font, paper_bg, centered, hairline,
    implied_prob, format_odds, best_book, format_book_name,
    load_daily, load_env, post_image_tweet, call_llm_shared,
)

RESULTS_PATH = ROOT / "data" / "daily" / "results.json"


def pick_lockin(daily: dict) -> dict | None:
    """Highest-conviction pick. Uses model_prob as the tiebreaker after edge.

    Definition: among all picks, take the one with the highest score where
    score = edge_pct * model_prob (rewards both edge size AND raw win probability).
    """
    picks = daily.get("picks", [])
    if not picks:
        return None
    # Dedupe doubleheaders
    seen = set()
    deduped = []
    for p in picks:
        key = (p.get("team"), p.get("type"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    return max(deduped, key=lambda p: p.get("edge_pct", 0) * p.get("model_prob", 0))


def _season_record() -> dict:
    """Aggregate season stats from results.json. Returns {} if missing."""
    if not RESULTS_PATH.exists():
        return {}
    with open(RESULTS_PATH) as f:
        rows = json.load(f)
    if not rows:
        return {}
    w = sum(r.get("wins", 0) for r in rows)
    l = sum(r.get("losses", 0) for r in rows)
    profit = sum(r.get("day_profit", 0) for r in rows)
    wagered = sum(sum(abs(p.get("wager", 0)) for p in r.get("picks", [])) for r in rows)
    roi = (profit / wagered * 100) if wagered else 0
    return {
        "wins": w, "losses": l,
        "profit": profit,
        "wagered": wagered,
        "roi": roi,
        "bankroll": 10000.0 + profit,
        "days": len(rows),
    }


def llm_lockin(pick: dict, matchup: str) -> str | None:
    """LLM tweet for the lock-in. Urgent, punchy."""
    pick_str = pick.get("pick", "")
    odds = pick.get("odds", "")
    model_pct = pick.get("model_prob", 0) * 100
    implied = implied_prob(odds) * 100
    edge = pick.get("edge_pct", 0)
    explanation = pick.get("explanation", "").strip()

    system = (
        "You write tweets for an anonymous MLB betting model account. "
        "Voice: cold, urgent, math-pilled. No vibes, no emojis, no exclamation points, no em dashes. "
        "Speak as 'the model'. This is the highest-conviction pick of the day, posted right before first pitch. "
        "Short, punchy, decisive."
    )
    user = f"""Write a sharp 1-2 sentence tweet (max 200 chars, NO URL) calling out tonight's highest-conviction pick. Reference the disagreement (model vs market %). Convey urgency — first pitch is close. Do NOT use the phrase 'lock of the day' (the image already says it).

PICK: {pick_str} at {odds}
MATCHUP: {matchup}
MARKET IMPLIED: {implied:.1f}%
MODEL: {model_pct:.1f}%
EDGE: {edge:.1f} points

EXPLANATION:
{explanation if explanation else '(no explanation available)'}

Output only the tweet text."""
    return call_llm_shared(system, user, max_tokens=200)


def fallback_lockin_tweet(pick: dict, matchup: str) -> str:
    pick_str = pick.get("pick", "")
    odds = pick.get("odds", "")
    model_pct = pick.get("model_prob", 0) * 100
    implied = implied_prob(odds) * 100
    return (f"First pitch soon. The model's highest-conviction play: {pick_str} at {odds}. "
            f"Market: {implied:.0f}%. Model: {model_pct:.0f}%.")


def render_lockin_card(pick: dict, matchup: str, date: str, output_path: Path,
                       season: dict | None = None) -> Path:
    img = Image.new("RGB", (WIDTH, HEIGHT))
    paper_bg(img)
    draw = ImageDraw.Draw(img)

    margin = 64
    inner_w = WIDTH - margin * 2

    Y_EYEBROW = 56
    Y_HAIRLINE_1 = 114
    Y_LOCK_LABEL = 168       # "LOCK OF THE DAY"
    Y_MATCHUP = 270
    Y_PICK = 460             # The PICK string (huge)
    Y_ODDS = 588             # The odds (smaller, beneath)
    Y_HAIRLINE_2 = 700
    Y_MINI_LABEL = 730       # mini comparison labels
    Y_MINI_VALUE = 772       # mini comparison values
    Y_HAIRLINE_3 = 940
    Y_SEASON_LABEL = 970
    Y_SEASON_LINE = 1014
    Y_HAIRLINE_4 = HEIGHT - 76
    Y_FOOTER = HEIGHT - 44

    # (1) Eyebrow
    draw.text((margin, Y_EYEBROW), "OZZY ANALYTICS", font=_font(26, "demi"), fill=INK)
    dt_str = datetime.strptime(date, "%Y-%m-%d").strftime("%b %-d, %Y").upper()
    dw = draw.textbbox((0, 0), dt_str, font=_font(26, "regular"))[2]
    draw.text((WIDTH - margin - dw, Y_EYEBROW + 2), dt_str, font=_font(26, "regular"), fill=INK_MUT)
    hairline(draw, margin, Y_HAIRLINE_1, WIDTH - margin, width=2)

    # (2) "LOCK OF THE DAY" — the headline
    centered(draw, "LOCK OF THE DAY", _font(64, "demi"), Y_LOCK_LABEL, BAR_MODEL)

    # (3) Matchup
    m_size = 56
    while m_size > 32:
        f = _font(m_size, "demi")
        if draw.textbbox((0, 0), matchup, font=f)[2] <= inner_w:
            break
        m_size -= 4
    centered(draw, matchup, _font(m_size, "demi"), Y_MATCHUP, INK)
    centered(draw, "FIRST PITCH SOON", _font(22, "demi"), Y_MATCHUP + 76, INK_MUT)

    # (4) PICK + odds (the visual hero)
    pick_str = pick.get("pick", "")
    odds = pick.get("odds", "")
    # Auto-shrink pick string
    p_size = 140
    while p_size > 56:
        f = _font(p_size, "demi")
        if draw.textbbox((0, 0), pick_str, font=f)[2] <= inner_w - 40:
            break
        p_size -= 4
    centered(draw, pick_str, _font(p_size, "demi"), Y_PICK, INK)
    # Odds with subtle pill background
    odds_text = odds
    o_font = _font(64, "demi")
    bbox = draw.textbbox((0, 0), odds_text, font=o_font)
    ow = bbox[2] - bbox[0]
    pad_x = 28
    pad_y = 14
    pill_w = ow + pad_x * 2
    pill_h = 80
    pill_x = (WIDTH - pill_w) // 2
    pill_y = Y_ODDS
    draw.rounded_rectangle([pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
                           radius=pill_h // 2, fill=BAR_MODEL_TINT, outline=BAR_MODEL, width=2)
    draw.text((pill_x + pad_x, pill_y + pad_y), odds_text, font=o_font, fill=BAR_MODEL)

    # (5) Mini market-vs-model
    hairline(draw, margin, Y_HAIRLINE_2, WIDTH - margin, width=2)
    market_pct = implied_prob(odds) * 100
    model_pct = pick.get("model_prob", 0) * 100
    edge = pick.get("edge_pct", 0)
    col_w = inner_w // 3
    centered(draw, "MARKET", _font(20, "demi"), Y_MINI_LABEL, INK_MUT, margin, margin + col_w)
    centered(draw, f"{market_pct:.1f}%", _font(72, "demi"), Y_MINI_VALUE, INK_SOFT, margin, margin + col_w)
    centered(draw, "EDGE", _font(20, "demi"), Y_MINI_LABEL, INK_MUT, margin + col_w, margin + 2 * col_w)
    centered(draw, f"+{edge:.1f}", _font(72, "demi"), Y_MINI_VALUE, BAR_MODEL, margin + col_w, margin + 2 * col_w)
    centered(draw, "MODEL", _font(20, "demi"), Y_MINI_LABEL, INK_MUT, margin + 2 * col_w, WIDTH - margin)
    centered(draw, f"{model_pct:.1f}%", _font(72, "demi"), Y_MINI_VALUE, INK, margin + 2 * col_w, WIDTH - margin)
    # Two thin dividers between the three columns
    div_x1 = margin + col_w
    div_x2 = margin + 2 * col_w
    draw.line([(div_x1, Y_MINI_LABEL - 8), (div_x1, Y_MINI_VALUE + 80)], fill=RULE, width=1)
    draw.line([(div_x2, Y_MINI_LABEL - 8), (div_x2, Y_MINI_VALUE + 80)], fill=RULE, width=1)

    # (6) Season track record
    hairline(draw, margin, Y_HAIRLINE_3, WIDTH - margin, width=2)
    if season:
        centered(draw, "SEASON TRACK RECORD", _font(20, "demi"), Y_SEASON_LABEL, INK_MUT)
        w, l = season.get("wins", 0), season.get("losses", 0)
        winpct = 100 * w / max(w + l, 1)
        roi = season.get("roi", 0)
        bankroll = season.get("bankroll", 10000)
        line = f"{w}-{l} ({winpct:.1f}%)  ·  {roi:+.1f}% ROI  ·  $10K → ${bankroll:,.0f}"
        centered(draw, line, _font(30, "demi"), Y_SEASON_LINE, INK)
    else:
        centered(draw, "Every pick, every result, in the open.", _font(24, "regular"),
                 Y_SEASON_LABEL + 10, INK_SOFT)

    # (7) Footer
    hairline(draw, margin, Y_HAIRLINE_4, WIDTH - margin, width=2)
    draw.text((margin, Y_FOOTER), RECEIPTS_URL, font=_font(22, "demi"), fill=BAR_MODEL)
    tag = "10K SIMS · FREE DAILY"
    tw = draw.textbbox((0, 0), tag, font=_font(22, "demi"))[2]
    draw.text((WIDTH - margin - tw, Y_FOOTER), tag, font=_font(22, "demi"), fill=INK_MUT)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), quality=95)
    return output_path


def build_lockin(date: str) -> dict:
    daily = load_daily(date)
    if daily is None:
        return {"error": f"no daily JSON found for {date}"}
    pick = pick_lockin(daily)
    if pick is None:
        return {"error": "no picks today — nothing to lock in"}
    side = pick.get("side", "")
    matchup = (f"{pick.get('team','')} @ {pick.get('opponent','')}"
               if side == "away" else
               f"{pick.get('opponent','')} @ {pick.get('team','')}")
    tweet = llm_lockin(pick, matchup)
    used_llm = tweet is not None
    if not tweet:
        tweet = fallback_lockin_tweet(pick, matchup)
    tweet_full = f"{tweet}\n\n{RECEIPTS_URL}"
    out = OUT_DIR / f"lockin_{date}.png"
    season = _season_record()
    path = render_lockin_card(pick, matchup, date, out, season=season)
    return {"image": str(path), "tweet": tweet_full, "mode": "lockin",
            "llm_used": used_llm, "pick": pick.get("pick", ""), "matchup": matchup,
            "edge_pct": pick.get("edge_pct", 0)}


def main():
    parser = argparse.ArgumentParser(description="Build today's Lock-in tweet")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--post", action="store_true")
    args = parser.parse_args()
    load_env()
    result = build_lockin(args.date)
    if "error" in result:
        print(f"  ERROR: {result['error']}")
        return 1
    print(f"\n=== LOCK-IN for {args.date} ===")
    print(f"  Pick: {result['pick']} ({result['matchup']}, edge {result['edge_pct']:.1f}%)")
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
