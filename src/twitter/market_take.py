"""
Slot 2 of the daily Twitter cadence — Market Disagreement (~11:30 AM ET).

Picks the largest-edge pick of the day, builds a dark-navy scouting card
(1200x1200) with side-by-side Market vs Model percentages, the full
sportsbook lines table, and a smart pitcher/conditions zone. On 0-pick
days, falls back to a "model passes" discipline card.

Usage:
    python3 -m src.twitter.market_take                       # today, preview
    python3 -m src.twitter.market_take --date 2026-06-02     # specific date
    python3 -m src.twitter.market_take --post                # actually tweet
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from src.twitter._card_common import (
    WIDTH, HEIGHT, OUT_DIR, RECEIPTS_URL,
    INK, INK_SOFT, INK_MUT, RULE, BAR_BG, BAR_MARKET, BAR_MODEL, BAR_MODEL_TINT,
    _font, paper_bg, centered, hairline, hbar,
    implied_prob, format_odds, format_book_name, best_book,
    load_daily, find_game, pitcher_stats, stats_complete, pct_str, woba_str,
    load_env, post_image_tweet, call_llm_shared,
)


def pick_top_disagreement(picks: list) -> dict | None:
    """Largest-edge pick (dedupes doubleheaders by team+type)."""
    if not picks:
        return None
    seen = set()
    deduped = []
    for p in picks:
        key = (p.get("team"), p.get("type"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    return max(deduped, key=lambda p: p.get("edge_pct", 0))


def _pick_feature_pitcher(pick: dict, game: dict | None) -> tuple[str, str, str, dict | None]:
    """(label, name, team, stats_dict). Prefers OPPOSING pitcher with COMPLETE stats;
    falls back to OUR pitcher with COMPLETE stats; returns None data tuple otherwise."""
    if not game:
        return ("STARTING PITCHER", "", "", None)
    our_is_away = pick.get("side") == "away"
    our_team = pick.get("team", "")
    opp_team = pick.get("opponent", "")
    away_pitcher = (game.get("away_pitcher") or "").strip()
    home_pitcher = (game.get("home_pitcher") or "").strip()
    our_pitcher = away_pitcher if our_is_away else home_pitcher
    opp_pitcher = home_pitcher if our_is_away else away_pitcher

    opp_stats = pitcher_stats(opp_pitcher)
    if stats_complete(opp_stats):
        return ("OPPOSING STARTER", opp_pitcher, opp_team, opp_stats)
    our_stats = pitcher_stats(our_pitcher)
    if stats_complete(our_stats):
        return ("OUR STARTER", our_pitcher, our_team, our_stats)
    return ("STARTING PITCHER", "", "", None)


def llm_take(pick: dict, matchup: str) -> str | None:
    """Ask Claude for a sharp 1-2 sentence Market Disagreement tweet. None on failure."""
    explanation = pick.get("explanation", "").strip()
    if not explanation:
        return None
    pick_str = pick.get("pick", "")
    odds = pick.get("odds", "")
    model_pct = pick.get("model_prob", 0) * 100
    implied = implied_prob(odds) * 100
    edge = pick.get("edge_pct", 0)

    system = (
        "You write tweets for an anonymous MLB betting model account. "
        "Voice: cold, matter-of-fact, math-pilled. No vibes, no emojis, no exclamation points, no em dashes. "
        "Speak as 'the model', third-person. Confident without being braggy. "
        "Reference market vs model as a disagreement, not a recommendation."
    )
    user = f"""Write a sharp 1-2 sentence tweet (max 200 chars, NO URL) framing the market and model as disagreeing on this game. Lead with the disagreement, not the recommendation. Reference specific player names from the explanation when they sharpen the take.

PICK: {pick_str} at {odds}
MATCHUP: {matchup}
MARKET IMPLIED: {implied:.1f}%
MODEL: {model_pct:.1f}%
EDGE: {edge:.1f} points

LLM EXPLANATION OF PICK:
{explanation}

Output only the tweet text, nothing else."""
    return call_llm_shared(system, user, max_tokens=200)


def fallback_tweet(pick: dict, matchup: str) -> str:
    """Rule-based tweet if LLM is unavailable. Always works."""
    odds = pick.get("odds", "")
    implied = implied_prob(odds) * 100
    model_pct = pick.get("model_prob", 0) * 100
    team = pick.get("team", "")
    book, book_odds = best_book(pick)
    book_name = format_book_name(book) if book else "Vegas"
    market_line = (f"{book_name} has {team} at {format_odds(book_odds)} ({implied:.0f}% implied)."
                   if book_odds else
                   f"Market has {team} at {odds} ({implied:.0f}% implied).")
    return f"{market_line} The model has {model_pct:.0f}%. {pick.get('edge_pct', 0):.1f}-point gap."


def render_disagreement_card(pick: dict, matchup: str, date: str, output_path: Path,
                              daily: dict | None = None) -> Path:
    """Render the dark-navy Market vs Model scouting card."""
    img = Image.new("RGB", (WIDTH, HEIGHT))
    paper_bg(img)
    draw = ImageDraw.Draw(img)

    margin = 64
    inner_w = WIDTH - margin * 2

    # Strict vertical grid for 1200x1200
    Y_EYEBROW = 56
    Y_HAIRLINE_1 = 114
    Y_MATCHUP = 158
    Y_EDGE = 304
    Y_HAIRLINE_2 = 376
    Y_NUMS_LABEL = 412
    Y_NUMS_VALUE = 458
    Y_HAIRLINE_3 = 644
    Y_BOOKS_LABEL = 678
    Y_BOOKS_HEADER = 720
    Y_BOOKS_ROW0 = 762
    Y_HAIRLINE_4 = 932
    Y_ZONE4_LABEL = 962
    Y_ZONE4_STATS = 1004
    Y_HAIRLINE_5 = HEIGHT - 76
    Y_FOOTER = HEIGHT - 44

    # (1) Eyebrow row
    draw.text((margin, Y_EYEBROW), "OZZY ANALYTICS", font=_font(26, "demi"), fill=INK)
    dt_str = datetime.strptime(date, "%Y-%m-%d").strftime("%b %-d, %Y").upper()
    dw = draw.textbbox((0, 0), dt_str, font=_font(26, "regular"))[2]
    draw.text((WIDTH - margin - dw, Y_EYEBROW + 2), dt_str, font=_font(26, "regular"), fill=INK_MUT)
    hairline(draw, margin, Y_HAIRLINE_1, WIDTH - margin, width=2)

    # (2) Matchup + edge sub-line
    game = find_game(daily, pick.get("team", ""), pick.get("opponent", "")) if daily else None
    matchup_font_size = 110
    while matchup_font_size > 56:
        f_match = _font(matchup_font_size, "demi")
        if draw.textbbox((0, 0), matchup, font=f_match)[2] <= inner_w:
            break
        matchup_font_size -= 4
    centered(draw, matchup, f_match, Y_MATCHUP, INK)
    edge = pick.get("edge_pct", 0)
    centered(draw, f"+{edge:.1f} POINT EDGE", _font(34, "demi"), Y_EDGE, BAR_MODEL)
    hairline(draw, margin, Y_HAIRLINE_2, WIDTH - margin, width=2)

    # (3) Side-by-side big numbers
    market_pct = implied_prob(pick.get("odds", "")) * 100
    model_pct = pick.get("model_prob", 0) * 100
    col_w = inner_w // 2
    f_nums_label = _font(28, "demi")
    f_nums_val = _font(150, "demi")
    mk_x_end = margin + col_w
    centered(draw, "MARKET WIN %", f_nums_label, Y_NUMS_LABEL, INK_MUT, margin, mk_x_end)
    centered(draw, f"{market_pct:.1f}%", f_nums_val, Y_NUMS_VALUE, INK_SOFT, margin, mk_x_end)
    md_x_start = margin + col_w
    centered(draw, "MODEL WIN %", f_nums_label, Y_NUMS_LABEL, INK_MUT, md_x_start, WIDTH - margin)
    centered(draw, f"{model_pct:.1f}%", f_nums_val, Y_NUMS_VALUE, BAR_MODEL, md_x_start, WIDTH - margin)
    div_x = margin + col_w
    draw.line([(div_x, Y_NUMS_LABEL - 8), (div_x, Y_NUMS_VALUE + 160)], fill=RULE, width=1)

    # (4) Sportsbook table
    hairline(draw, margin, Y_HAIRLINE_3, WIDTH - margin, width=2)
    centered(draw, "SPORTSBOOK LINES · ALL 5 BOOKS", _font(24, "demi"), Y_BOOKS_LABEL, INK_MUT)
    books = pick.get("sportsbook_odds", {}) or {}
    def book_rank(kv):
        o = kv[1]
        return o if o > 0 else -10000 - o
    book_rows = sorted(books.items(), key=book_rank, reverse=True)
    best_key = book_rows[0][0] if book_rows else None
    col_book_x = margin + 40
    col_odds_x = margin + 480
    col_impl_x = margin + 720
    col_best_x = margin + inner_w - 60
    draw.text((col_book_x, Y_BOOKS_HEADER), "BOOK", font=_font(20, "demi"), fill=INK_MUT)
    draw.text((col_odds_x, Y_BOOKS_HEADER), "ODDS", font=_font(20, "demi"), fill=INK_MUT)
    draw.text((col_impl_x, Y_BOOKS_HEADER), "IMPLIED", font=_font(20, "demi"), fill=INK_MUT)
    draw.line([(margin + 20, Y_BOOKS_HEADER + 32), (WIDTH - margin - 20, Y_BOOKS_HEADER + 32)],
              fill=RULE, width=1)
    row_h = 34
    for i, (book_key, book_odds) in enumerate(book_rows[:5]):
        ry = Y_BOOKS_ROW0 + i * row_h
        is_best = book_key == best_key
        name = format_book_name(book_key)
        impl = implied_prob(book_odds) * 100
        ink = INK if is_best else INK_SOFT
        f = _font(24, "demi" if is_best else "regular")
        draw.text((col_book_x, ry), name, font=f, fill=ink)
        draw.text((col_odds_x, ry), format_odds(book_odds), font=f, fill=ink)
        draw.text((col_impl_x, ry), f"{impl:.1f}%", font=f, fill=ink)
        if is_best:
            best_text = "BEST"
            bw = draw.textbbox((0, 0), best_text, font=_font(16, "demi"))[2]
            pad_x = 10
            px = col_best_x - bw - pad_x * 2
            draw.rounded_rectangle([px, ry + 2, px + bw + pad_x * 2, ry + 28],
                                   radius=13, fill=BAR_MODEL_TINT, outline=BAR_MODEL, width=2)
            draw.text((px + pad_x, ry + 5), best_text, font=_font(16, "demi"), fill=BAR_MODEL)

    # (5) Smart pitcher OR conditions zone
    hairline(draw, margin, Y_HAIRLINE_4, WIDTH - margin, width=2)
    label, p_name, p_team, p_stats = _pick_feature_pitcher(pick, game)
    if p_stats and p_stats.get("all"):
        centered(draw, f"{label} · {p_name.upper()} ({p_team})",
                 _font(22, "demi"), Y_ZONE4_LABEL, INK_MUT)
        a = p_stats["all"]
        blocks = [
            ("K%", pct_str(a.get("k_pct"))),
            ("BB%", pct_str(a.get("bb_pct"))),
            ("xwOBA", woba_str(a.get("xwoba"))),
            ("BRL%", pct_str(a.get("barrel_pct"))),
        ]
    else:
        centered(draw, "GAME CONDITIONS", _font(22, "demi"), Y_ZONE4_LABEL, INK_MUT)
        weather = game.get("weather", {}) if game else {}
        park = game.get("park_factors", {}) if game else {}
        temp = weather.get("temperature")
        wind_spd = weather.get("wind_speed")
        is_dome = weather.get("condition") == "Dome"
        hr_factor = park.get("HR") or park.get("HR_factor") or 1.0
        runs_factor = park.get("runs") or 1.0
        blocks = [
            ("TEMP", f"{temp:.0f}°F" if temp else ("DOME" if is_dome else "—")),
            ("WIND", f"{wind_spd:.0f}MPH" if wind_spd and not is_dome else ("CALM" if not is_dome else "DOME")),
            ("PARK HR", f"{hr_factor:.2f}X"),
            ("PARK RUNS", f"{runs_factor:.2f}X"),
        ]
    block_w = inner_w // 4
    for i, (lab, val) in enumerate(blocks):
        bx_start = margin + i * block_w
        lw = draw.textbbox((0, 0), lab, font=_font(18, "demi"))[2]
        draw.text((bx_start + (block_w - lw) // 2, Y_ZONE4_STATS), lab, font=_font(18, "demi"), fill=INK_MUT)
        vw = draw.textbbox((0, 0), val, font=_font(52, "demi"))[2]
        draw.text((bx_start + (block_w - vw) // 2, Y_ZONE4_STATS + 28), val, font=_font(52, "demi"), fill=INK)

    # (6) Footer
    hairline(draw, margin, Y_HAIRLINE_5, WIDTH - margin, width=2)
    draw.text((margin, Y_FOOTER), RECEIPTS_URL, font=_font(22, "demi"), fill=BAR_MODEL)
    tag = "10K SIMS · FREE DAILY"
    tw = draw.textbbox((0, 0), tag, font=_font(22, "demi"))[2]
    draw.text((WIDTH - margin - tw, Y_FOOTER), tag, font=_font(22, "demi"), fill=INK_MUT)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), quality=95)
    return output_path


def render_no_edges_card(date: str, games_count: int, output_path: Path) -> Path:
    """0-pick day: discipline-themed card."""
    img = Image.new("RGB", (WIDTH, HEIGHT))
    paper_bg(img)
    draw = ImageDraw.Draw(img)
    margin = 64
    draw.text((margin, 56), "OZZY ANALYTICS", font=_font(26, "demi"), fill=INK)
    dt_str = datetime.strptime(date, "%Y-%m-%d").strftime("%b %-d, %Y").upper()
    dw = draw.textbbox((0, 0), dt_str, font=_font(26, "regular"))[2]
    draw.text((WIDTH - margin - dw, 58), dt_str, font=_font(26, "regular"), fill=INK_MUT)
    hairline(draw, margin, 114, WIDTH - margin, width=2)
    centered(draw, str(games_count), _font(320, "demi"), 240, INK)
    centered(draw, "GAMES TONIGHT", _font(34, "demi"), 600, INK_MUT)
    centered(draw, "0 edges cleared the threshold", _font(28, "regular"), 720, INK_SOFT)
    centered(draw, "The model passes.", _font(56, "demi"), 800, BAR_MODEL)
    hairline(draw, margin, HEIGHT - 76, WIDTH - margin, width=2)
    draw.text((margin, HEIGHT - 44), RECEIPTS_URL, font=_font(22, "demi"), fill=BAR_MODEL)
    tag = "10K SIMS · FREE DAILY"
    tw = draw.textbbox((0, 0), tag, font=_font(22, "demi"))[2]
    draw.text((WIDTH - margin - tw, HEIGHT - 44), tag, font=_font(22, "demi"), fill=INK_MUT)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), quality=95)
    return output_path


def build_market_take(date: str) -> dict:
    """Full payload: image path + tweet text + metadata."""
    daily = load_daily(date)
    if daily is None:
        return {"error": f"no daily JSON found for {date}"}
    picks = daily.get("picks", [])
    games = daily.get("games", [])
    if not picks:
        out = OUT_DIR / f"market_take_{date}_nope.png"
        path = render_no_edges_card(date, len(games), out)
        tweet = (f"{len(games)} games on the board tonight. 0 edges cleared the threshold. "
                 f"The model passes. {RECEIPTS_URL}")
        return {"image": str(path), "tweet": tweet, "mode": "no_edges"}
    pick = pick_top_disagreement(picks)
    side = pick.get("side", "")
    matchup = (f"{pick.get('team','')} @ {pick.get('opponent','')}"
               if side == "away" else
               f"{pick.get('opponent','')} @ {pick.get('team','')}")
    tweet = llm_take(pick, matchup)
    used_llm = tweet is not None
    if not tweet:
        tweet = fallback_tweet(pick, matchup)
    tweet_full = f"{tweet}\n\n{RECEIPTS_URL}"
    out = OUT_DIR / f"market_take_{date}.png"
    path = render_disagreement_card(pick, matchup, date, out, daily=daily)
    return {"image": str(path), "tweet": tweet_full, "mode": "disagreement",
            "llm_used": used_llm, "edge_pct": pick.get("edge_pct", 0),
            "pick": pick.get("pick", ""), "matchup": matchup}


def main():
    parser = argparse.ArgumentParser(description="Build today's Market Disagreement tweet")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--post", action="store_true")
    args = parser.parse_args()
    load_env()
    result = build_market_take(args.date)
    if "error" in result:
        print(f"  ERROR: {result['error']}")
        return 1
    print(f"\n=== MARKET TAKE for {args.date} ===")
    print(f"  Mode: {result['mode']}")
    if result.get("pick"):
        print(f"  Pick: {result['pick']} ({result['matchup']}, edge {result['edge_pct']:.1f}%)")
        print(f"  LLM used: {result['llm_used']}")
    print(f"  Image: {result['image']}")
    print(f"\n  Tweet ({len(result['tweet'])} chars):")
    print(f"  ---")
    for line in result["tweet"].split("\n"):
        print(f"  | {line}")
    print(f"  ---")
    if args.post:
        print("\n  Posting to Twitter...")
        post_image_tweet(result["image"], result["tweet"])
    else:
        print("\n  (Preview only. Re-run with --post to tweet.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
