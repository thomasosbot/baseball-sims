"""
Generate the daily "Market Disagreement" tweet — the sharpest
divergence between model and sportsbook for a given day.

Picks the largest-edge pick of the day, builds a side-by-side
Market vs Model comparison image (1200x675), and produces tweet
text. On 0-pick days, falls back to a discipline-themed "no edges"
post.

Usage:
    python3 -m src.twitter.market_take                       # today
    python3 -m src.twitter.market_take --date 2026-06-02     # specific date
    python3 -m src.twitter.market_take --post                # actually tweet
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DAILY_DIR = Path(__file__).parent.parent.parent / "data" / "daily"
OUT_DIR = Path(__file__).parent.parent.parent / "data" / "twitter"

WIDTH = 1200
HEIGHT = 1200  # square — more vertical room for data zones

RECEIPTS_URL = "ozzyanalytics.com/60-days.html"

# League baselines (rough 2025 MLB averages) for stat-vs-league color cues
LG_AVG = {
    "k_pct": 0.224,
    "bb_pct": 0.084,
    "xwoba": 0.318,
    "barrel_pct": 0.075,
}

_ROLLUP_CACHE = None

def _load_rollup() -> dict:
    """Load (or return cached) statcast rollup keyed by MLBAM ID."""
    global _ROLLUP_CACHE
    if _ROLLUP_CACHE is not None:
        return _ROLLUP_CACHE
    import pickle
    p = Path(__file__).parent.parent.parent / "data" / "processed" / "statcast_rollup_2025.pkl"
    if not p.exists():
        _ROLLUP_CACHE = {"hitters": {}, "pitchers": {}}
        return _ROLLUP_CACHE
    with open(p, "rb") as f:
        _ROLLUP_CACHE = pickle.load(f)
    return _ROLLUP_CACHE


def _pitcher_stats(name: str) -> dict | None:
    """Resolve a pitcher name → stats dict, or None if unavailable. Includes 'all', 'vsL', 'vsR'."""
    if not name or name == "TBD":
        return None
    try:
        from src.features.name_resolver import resolve_id
        pid = resolve_id(name)
    except Exception:
        return None
    if pid is None:
        return None
    rollup = _load_rollup()
    return rollup.get("pitchers", {}).get(pid)


def _top_hitters(names: list[str], n: int = 3) -> list[tuple[str, float]]:
    """Return [(name, xwoba), ...] of the top n hitters in a lineup by xwOBA. Skips unresolved."""
    if not names:
        return []
    try:
        from src.features.name_resolver import resolve_id
    except Exception:
        return []
    rollup = _load_rollup()
    hitters = rollup.get("hitters", {})
    scored = []
    for nm in names:
        pid = resolve_id(nm)
        if pid is None:
            continue
        stats = hitters.get(pid, {}).get("all", {})
        xw = stats.get("xwoba")
        if xw is None:
            continue
        # Require min sample to avoid noisy small-sample callouts
        if stats.get("pa", 0) < 50:
            continue
        scored.append((nm, xw))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:n]

# ── v8 palette: dark navy matching the website ──
PAPER = (15, 20, 36)          # site bg-end #0F1424
PAPER_TOP = (10, 14, 26)      # site bg-start #0A0E1A (subtle gradient top)
INK = (244, 246, 250)         # site text #F4F6FA
INK_SOFT = (184, 191, 204)    # site text-secondary #B8BFCC
INK_MUT = (107, 114, 128)     # site text-muted
RULE = (55, 60, 80)            # subtle divider on dark
CARD_BG = (24, 29, 45)         # rgba(255,255,255,0.04) over navy
CARD_BORDER = (38, 42, 56)     # rgba(255,255,255,0.08)
BAR_BG = (32, 38, 56)
BAR_MARKET = (138, 144, 158)   # cool gray
BAR_MODEL = (74, 222, 128)     # site green #4ADE80 — single bright accent
BAR_MODEL_TINT = (24, 60, 40)  # green-tinted card bg for pills
GREEN_DEEP = (22, 163, 74)     # site green-deep #16A34A

_FONT_CACHE: dict = {}

# Font preference order: Avenir Next (light/regular/medium variants) → fallback
_AVENIR_PATH = "/System/Library/Fonts/Avenir Next.ttc"
_GEORGIA_PATH = "/System/Library/Fonts/Supplemental/Georgia Italic.ttf"
_FALLBACK = "/System/Library/Fonts/Helvetica.ttc"


def _font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    """weight: 'light', 'regular', 'medium', 'demi', 'italic-serif'."""
    key = (weight, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    # Avenir Next TTC indices: 0 Regular, 1 Italic, 2 Bold, 3 Bold Italic,
    # 4 Demi Bold, 5 Demi Bold Italic, 6 Medium, 7 Medium Italic, 8 Ultra Light, 9 Ultra Light Italic
    weight_to_idx = {
        "light": 8, "regular": 0, "medium": 6, "demi": 4, "bold": 2,
    }
    if weight == "italic-serif":
        try:
            f = ImageFont.truetype(_GEORGIA_PATH, size)
            _FONT_CACHE[key] = f
            return f
        except (OSError, IOError):
            pass
    idx = weight_to_idx.get(weight, 0)
    try:
        f = ImageFont.truetype(_AVENIR_PATH, size, index=idx)
        _FONT_CACHE[key] = f
        return f
    except (OSError, IOError):
        pass
    try:
        f = ImageFont.truetype(_FALLBACK, size)
        _FONT_CACHE[key] = f
        return f
    except (OSError, IOError):
        pass
    f = ImageFont.load_default(size=size)
    _FONT_CACHE[key] = f
    return f


def _paper_bg(img):
    """Dark navy background with subtle top-to-bottom gradient matching site."""
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / HEIGHT
        r = int(PAPER_TOP[0] + (PAPER[0] - PAPER_TOP[0]) * t)
        g = int(PAPER_TOP[1] + (PAPER[1] - PAPER_TOP[1]) * t)
        b = int(PAPER_TOP[2] + (PAPER[2] - PAPER_TOP[2]) * t)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))


def implied_prob(american_odds) -> float:
    """American odds → implied probability (0-1). Accepts '+205', '-160', or int."""
    if isinstance(american_odds, str):
        s = american_odds.strip().replace("+", "")
        try:
            o = int(s)
        except ValueError:
            return 0.0
    else:
        o = int(american_odds)
    if o > 0:
        return 100.0 / (o + 100.0)
    return abs(o) / (abs(o) + 100.0)


def load_daily(date: str) -> dict | None:
    path = DAILY_DIR / f"{date}.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def pick_top_disagreement(picks: list) -> dict | None:
    """Choose the largest-edge pick (deduplicating doubleheaders by team+type)."""
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


def best_book(pick: dict) -> tuple[str, int]:
    """Return (book_name, odds) for the best book on this pick."""
    books = pick.get("sportsbook_odds", {})
    if not books:
        return ("", 0)
    # Best for the bettor: highest positive, or closest-to-zero negative
    def value(o):
        return o if o > 0 else -10000 - o  # large neg odds rank lowest
    best = max(books.items(), key=lambda kv: value(kv[1]))
    return best


def format_book_name(book_key: str) -> str:
    """Human-friendly sportsbook name."""
    mapping = {
        "fanduel": "FanDuel",
        "draftkings": "DraftKings",
        "betmgm": "BetMGM",
        "williamhill_us": "Caesars",
        "bovada": "Bovada",
    }
    return mapping.get(book_key, book_key.title())


def format_odds(o: int) -> str:
    return f"+{o}" if o > 0 else f"{o}"


def llm_take(pick: dict, matchup: str) -> tuple[str | None, list[str] | None]:
    """Ask Claude for a sharp tweet body + 3 bullets. Returns (tweet, bullets) or (None, None) on failure."""
    explanation = pick.get("explanation", "").strip()
    if not explanation:
        return None, None
    try:
        from src.betting.narrative import _call_llm
    except Exception:
        return None, None
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
    user = f"""Given this pick, write:
1. TWEET: a single sharp 1-2 sentence tweet body (max 200 chars, NO URL — the URL gets appended separately). Frame it as the market and the model disagreeing on this game. Lead with the disagreement, not the recommendation.
2. BULLETS: exactly 3 short bullet points (max 80 chars each), each starting with "-", distilling WHY the model disagrees with the market. Reference specific player names, splits, or matchup details from the explanation. No generic content.

PICK: {pick_str} at {odds}
MATCHUP: {matchup}
MARKET IMPLIED: {implied:.1f}%
MODEL: {model_pct:.1f}%
EDGE: {edge:.1f} points

LLM EXPLANATION OF PICK:
{explanation}

Output format exactly:
TWEET: <the tweet text>
BULLETS:
- <bullet 1>
- <bullet 2>
- <bullet 3>"""
    raw = _call_llm(system, user, max_tokens=500)
    if not raw:
        return None, None
    tweet_line = None
    bullets = []
    for line in raw.splitlines():
        line = line.strip()
        if line.upper().startswith("TWEET:"):
            tweet_line = line.split(":", 1)[1].strip()
        elif line.startswith("- "):
            bullets.append(line[2:].strip())
    if not tweet_line or len(bullets) < 2:
        return None, None
    return tweet_line, bullets[:3]


def fallback_tweet(pick: dict, matchup: str) -> tuple[str, list[str]]:
    """Rule-based tweet if LLM is unavailable. Always works."""
    odds = pick.get("odds", "")
    implied = implied_prob(odds) * 100
    model_pct = pick.get("model_prob", 0) * 100
    team = pick.get("team", "")
    pick_str = pick.get("pick", "")
    book, book_odds = best_book(pick)
    book_name = format_book_name(book) if book else "Vegas"
    if book_odds:
        market_line = f"{book_name} has {team} at {format_odds(book_odds)} ({implied:.0f}% implied)."
    else:
        market_line = f"Market has {team} at {odds} ({implied:.0f}% implied)."
    tweet = f"{market_line} The model has {model_pct:.0f}%. {pick.get('edge_pct', 0):.1f}-point gap on {pick_str}."
    bullets = [
        f"Model says {model_pct:.0f}%, market says {implied:.0f}%",
        f"Best price: {format_odds(book_odds)} at {book_name}" if book_odds else f"Best price: {odds}",
        f"Edge: +{pick.get('edge_pct', 0):.1f} points after vig",
    ]
    return tweet, bullets


# ─── Image rendering (v2: editorial, minimal, cream paper) ───

def _centered(draw, text, font, y, fill, x_start=0, x_end=WIDTH) -> int:
    """Draw `text` centered horizontally between x_start and x_end. Return text width."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text((x_start + (x_end - x_start - tw) // 2, y), text, font=font, fill=fill)
    return tw


def _hairline(draw, x1, y, x2, fill=RULE, width=1):
    """Thin horizontal divider."""
    draw.line([(x1, y), (x2, y)], fill=fill, width=width)


def _hbar(draw, x, y, w, h, pct, fill, track_fill=BAR_BG, max_pct=100.0):
    """Horizontal bar with track. Returns the end-x of the fill (for annotation positioning)."""
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=track_fill)
    fill_w = int(w * (min(pct, max_pct) / max_pct))
    if fill_w >= h:
        draw.rounded_rectangle([x, y, x + fill_w, y + h], radius=h // 2, fill=fill)
    elif fill_w > 0:
        draw.rectangle([x, y, x + fill_w, y + h], fill=fill)
    return x + fill_w


def _find_game(daily: dict, team: str, opponent: str) -> dict | None:
    """Locate the matching game from the daily JSON's games list."""
    for g in daily.get("games", []):
        if {g.get("away"), g.get("home")} == {team, opponent}:
            return g
    return None


def _text_height(font) -> int:
    """Approximate cap height from font metrics."""
    a, d = font.getmetrics()
    return a


def _stat_block(draw, x, y, w, label, value, baseline=None, lower_is_better=False):
    """Render a labeled stat block with value and an optional mini-bar showing position vs league."""
    draw.text((x, y), label, font=_font(11, "medium"), fill=INK_MUT)
    draw.text((x, y + 18), value, font=_font(26, "demi"), fill=INK)
    # Mini bar: position of value relative to league avg
    if baseline is not None and "value_pct" in baseline:
        # baseline expects {"value_pct": float 0-1 normalized position, "is_good": bool}
        bar_y = y + 56
        bar_h = 5
        draw.rounded_rectangle([x, bar_y, x + w - 16, bar_y + bar_h], radius=2, fill=BAR_BG)
        fill_w = int((w - 16) * baseline["value_pct"])
        col = BAR_MODEL if baseline.get("is_good") else BAR_MARKET
        if fill_w > 0:
            draw.rounded_rectangle([x, bar_y, x + fill_w, bar_y + bar_h], radius=2, fill=col)
        draw.text((x, bar_y + 12), baseline.get("note", ""), font=_font(10, "regular"), fill=INK_MUT)


def _pct_str(v) -> str:
    return f"{v*100:.1f}%" if v is not None else "—"


def _woba_str(v) -> str:
    return f".{int(round(v*1000)):03d}" if v is not None else "—"


def _baseline_for(stat: str, value: float | None, lower_is_better: bool) -> dict | None:
    """Build mini-bar config: where does value sit on a 0-1 scale vs league avg?"""
    if value is None:
        return None
    lg = LG_AVG.get(stat)
    if lg is None:
        return None
    # Normalize: anchor league avg at 0.5, scale so 2x is 1.0
    pos = min(value / (lg * 2.0), 1.0)
    delta = value - lg
    if lower_is_better:
        is_good = delta < 0  # below league avg = good (for pitcher allowing K%, BB% etc.)
    else:
        is_good = delta > 0
    if stat == "k_pct" and not lower_is_better:
        # K% for pitchers: higher is good
        is_good = delta > 0
    pct_diff = abs(delta) / lg * 100 if lg else 0
    direction = "above lg" if delta > 0 else "below lg"
    note = f"{pct_diff:.0f}% {direction}"
    return {"value_pct": pos, "is_good": is_good, "note": note}


def _pick_feature_pitcher(pick: dict, game: dict | None) -> tuple[str, str, str, dict | None]:
    """Choose which pitcher to feature based on data availability and narrative.

    Returns (label, name, team, stats_dict). Prefers the OPPOSING pitcher (the one our
    team is hitting against) since that's typically the narrative target. Falls back to
    OUR pitcher if opposing data is unavailable. Returns (label, "", "", None) if neither.
    """
    if not game:
        return ("STARTING PITCHER", "", "", None)
    our_is_away = pick.get("side") == "away"
    our_team = pick.get("team", "")
    opp_team = pick.get("opponent", "")
    away_pitcher = (game.get("away_pitcher") or "").strip()
    home_pitcher = (game.get("home_pitcher") or "").strip()

    # OUR pitcher is on our team; OPPOSING is on the opponent
    our_pitcher = away_pitcher if our_is_away else home_pitcher
    opp_pitcher = home_pitcher if our_is_away else away_pitcher

    opp_stats = _pitcher_stats(opp_pitcher)
    if opp_stats and opp_stats.get("all"):
        return ("OPPOSING STARTER", opp_pitcher, opp_team, opp_stats)
    our_stats = _pitcher_stats(our_pitcher)
    if our_stats and our_stats.get("all"):
        return ("OUR STARTER", our_pitcher, our_team, our_stats)
    return ("STARTING PITCHER", "", "", None)


def render_disagreement_card(pick: dict, matchup: str, bullets: list[str], date: str, output_path: Path,
                              daily: dict | None = None) -> Path:
    """Render mobile-first Market vs Model card. 3 zones, monochrome, large type."""
    img = Image.new("RGB", (WIDTH, HEIGHT))
    _paper_bg(img)
    draw = ImageDraw.Draw(img)

    margin = 64
    inner_w = WIDTH - margin * 2  # 1072

    # v8 grid — 4 zones, mobile-readable, no overlaps
    Y_EYEBROW = 56
    Y_HAIRLINE_1 = 114
    Y_MATCHUP = 158
    Y_EDGE = 304
    Y_HAIRLINE_2 = 376
    # Zone 2: big side-by-side numbers (no bars)
    Y_NUMS_LABEL = 412         # MARKET / MODEL small-caps labels
    Y_NUMS_VALUE = 458         # the big percentages
    Y_HAIRLINE_3 = 644
    # Zone 3: sportsbook table
    Y_BOOKS_LABEL = 678
    Y_BOOKS_HEADER = 720
    Y_BOOKS_ROW0 = 762
    Y_HAIRLINE_4 = 932
    # Zone 4: pitcher OR conditions (compact)
    Y_ZONE4_LABEL = 962
    Y_ZONE4_STATS = 1004
    Y_HAIRLINE_5 = HEIGHT - 76
    Y_FOOTER = HEIGHT - 44

    # ── (1) Eyebrow: brand left, date right ── (mobile-readable 26pt)
    f_brand = _font(26, "demi")
    f_eyebrow = _font(26, "regular")
    draw.text((margin, Y_EYEBROW), "OZZY ANALYTICS", font=f_brand, fill=INK)
    dt_str = datetime.strptime(date, "%Y-%m-%d").strftime("%b %-d, %Y").upper()
    dw = draw.textbbox((0, 0), dt_str, font=f_eyebrow)[2]
    draw.text((WIDTH - margin - dw, Y_EYEBROW + 2), dt_str, font=f_eyebrow, fill=INK_MUT)
    _hairline(draw, margin, Y_HAIRLINE_1, WIDTH - margin, width=2)

    # ── (2) Matchup headline + edge sub-line ──
    game = _find_game(daily, pick.get("team", ""), pick.get("opponent", "")) if daily else None

    # Matchup: very large, centered, auto-shrink to fit; cap at 110pt so it never crowds the edge line
    matchup_font_size = 110
    while matchup_font_size > 56:
        f_match = _font(matchup_font_size, "demi")
        mw = draw.textbbox((0, 0), matchup, font=f_match)[2]
        if mw <= inner_w:
            break
        matchup_font_size -= 4
    _centered(draw, matchup, f_match, Y_MATCHUP, INK)

    # Edge sub-line, centered, large — explicit Y position (no longer based on matchup font size)
    edge = pick.get("edge_pct", 0)
    edge_text = f"+{edge:.1f} POINT EDGE"
    _centered(draw, edge_text, _font(34, "demi"), Y_EDGE, BAR_MODEL)

    _hairline(draw, margin, Y_HAIRLINE_2, WIDTH - margin, width=2)

    # ── (3) Side-by-side big numbers — Market vs Model (no bars) ──
    market_pct = implied_prob(pick.get("odds", "")) * 100
    model_pct = pick.get("model_prob", 0) * 100

    col_w = inner_w // 2

    f_nums_label = _font(28, "demi")
    f_nums_val = _font(150, "demi")

    # MARKET column
    mk_x_start = margin
    mk_x_end = margin + col_w
    _centered(draw, "MARKET WIN %", f_nums_label, Y_NUMS_LABEL, INK_MUT, mk_x_start, mk_x_end)
    _centered(draw, f"{market_pct:.1f}%", f_nums_val, Y_NUMS_VALUE, INK_SOFT, mk_x_start, mk_x_end)

    # MODEL column
    md_x_start = margin + col_w
    md_x_end = WIDTH - margin
    _centered(draw, "MODEL WIN %", f_nums_label, Y_NUMS_LABEL, INK_MUT, md_x_start, md_x_end)
    _centered(draw, f"{model_pct:.1f}%", f_nums_val, Y_NUMS_VALUE, BAR_MODEL, md_x_start, md_x_end)

    # Vertical divider between the two big numbers
    div_x = margin + col_w
    draw.line([(div_x, Y_NUMS_LABEL - 8), (div_x, Y_NUMS_VALUE + 160)], fill=RULE, width=1)

    # ── (4) Sportsbook lines table ──
    _hairline(draw, margin, Y_HAIRLINE_3, WIDTH - margin, width=2)
    _centered(draw, "SPORTSBOOK LINES · ALL 5 BOOKS", _font(24, "demi"), Y_BOOKS_LABEL, INK_MUT)

    books = pick.get("sportsbook_odds", {}) or {}
    def book_rank(kv):
        o = kv[1]
        return o if o > 0 else -10000 - o
    book_rows = sorted(books.items(), key=book_rank, reverse=True)
    best_key = book_rows[0][0] if book_rows else None

    # 4-column table: BOOK | ODDS | IMPLIED | (best dot)
    # Column widths — distribute across inner_w
    col_book_x = margin + 40
    col_odds_x = margin + 480
    col_impl_x = margin + 720
    col_best_x = margin + inner_w - 60

    # Column headers
    f_col_hdr = _font(20, "demi")
    draw.text((col_book_x, Y_BOOKS_HEADER), "BOOK", font=f_col_hdr, fill=INK_MUT)
    draw.text((col_odds_x, Y_BOOKS_HEADER), "ODDS", font=f_col_hdr, fill=INK_MUT)
    draw.text((col_impl_x, Y_BOOKS_HEADER), "IMPLIED", font=f_col_hdr, fill=INK_MUT)
    # Light divider under header
    draw.line([(margin + 20, Y_BOOKS_HEADER + 32), (WIDTH - margin - 20, Y_BOOKS_HEADER + 32)],
              fill=RULE, width=1)

    row_h = 34
    f_row = _font(24, "regular")
    f_row_best = _font(24, "demi")
    for i, (book_key, book_odds) in enumerate(book_rows[:5]):
        ry = Y_BOOKS_ROW0 + i * row_h
        is_best = book_key == best_key
        name = format_book_name(book_key)
        odds_str = format_odds(book_odds)
        impl = implied_prob(book_odds) * 100
        ink = INK if is_best else INK_SOFT
        f = f_row_best if is_best else f_row
        draw.text((col_book_x, ry), name, font=f, fill=ink)
        draw.text((col_odds_x, ry), odds_str, font=f, fill=ink)
        draw.text((col_impl_x, ry), f"{impl:.1f}%", font=f, fill=ink)
        if is_best:
            # Green "BEST" pill
            best_pill_text = "BEST"
            f_best = _font(16, "demi")
            bw = draw.textbbox((0, 0), best_pill_text, font=f_best)[2]
            pad_x = 10
            px = col_best_x - bw - pad_x * 2
            draw.rounded_rectangle([px, ry + 2, px + bw + pad_x * 2, ry + 28],
                                   radius=13, fill=BAR_MODEL_TINT, outline=BAR_MODEL, width=2)
            draw.text((px + pad_x, ry + 5), best_pill_text, font=f_best, fill=BAR_MODEL)

    # ── (5) Fourth zone — smart pitcher OR conditions (compact, 4 stat blocks) ──
    _hairline(draw, margin, Y_HAIRLINE_4, WIDTH - margin, width=2)
    label, p_name, p_team, p_stats = _pick_feature_pitcher(pick, game)

    if p_stats and p_stats.get("all"):
        zone_label = f"{label} · {p_name.upper()} ({p_team})"
        _centered(draw, zone_label, _font(22, "demi"), Y_ZONE4_LABEL, INK_MUT)
        a = p_stats["all"]
        blocks = [
            ("K%", _pct_str(a.get("k_pct"))),
            ("BB%", _pct_str(a.get("bb_pct"))),
            ("xwOBA", _woba_str(a.get("xwoba"))),
            ("BRL%", _pct_str(a.get("barrel_pct"))),
        ]
    else:
        _centered(draw, "GAME CONDITIONS", _font(22, "demi"), Y_ZONE4_LABEL, INK_MUT)
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
    f_block_lab = _font(18, "demi")
    f_block_val = _font(52, "demi")
    for i, (lab, val) in enumerate(blocks):
        bx_start = margin + i * block_w
        lw = draw.textbbox((0, 0), lab, font=f_block_lab)[2]
        draw.text((bx_start + (block_w - lw) // 2, Y_ZONE4_STATS),
                  lab, font=f_block_lab, fill=INK_MUT)
        vw = draw.textbbox((0, 0), val, font=f_block_val)[2]
        draw.text((bx_start + (block_w - vw) // 2, Y_ZONE4_STATS + 28),
                  val, font=f_block_val, fill=INK)

    # ── (6) Footer ──
    _hairline(draw, margin, Y_HAIRLINE_5, WIDTH - margin, width=2)
    f_foot = _font(22, "demi")
    draw.text((margin, Y_FOOTER), RECEIPTS_URL, font=f_foot, fill=BAR_MODEL)
    tag = "10K SIMS · FREE DAILY"
    tw = draw.textbbox((0, 0), tag, font=f_foot)[2]
    draw.text((WIDTH - margin - tw, Y_FOOTER), tag, font=f_foot, fill=INK_MUT)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), quality=95)
    return output_path


def render_no_edges_card(date: str, games_count: int, output_path: Path) -> Path:
    """Card for 0-pick days: lean into the discipline angle."""
    img = Image.new("RGB", (WIDTH, HEIGHT))
    _paper_bg(img)
    draw = ImageDraw.Draw(img)

    margin = 80
    eb_y = 56
    draw.text((margin, eb_y), "OZZY ANALYTICS", font=_font(18, "medium"), fill=INK)
    dt_str = datetime.strptime(date, "%Y-%m-%d").strftime("%B %-d, %Y")
    bbox = draw.textbbox((0, 0), dt_str, font=_font(18, "regular"))
    dw = bbox[2] - bbox[0]
    draw.text((WIDTH - margin - dw, eb_y), dt_str, font=_font(18, "regular"), fill=INK_SOFT)
    _hairline(draw, margin, eb_y + 36, WIDTH - margin)

    _centered(draw, "Market vs Model", _font(32, "regular"), 134, INK)

    _centered(draw, str(games_count), _font(260, "light"), 200, INK)
    _centered(draw, "games tonight", _font(28, "regular"), 472, INK_SOFT)
    _centered(draw, "0 edges. The model passes.", _font(26, "demi"), 524, INK)

    bl_y = 612
    _hairline(draw, margin, bl_y - 18, WIDTH - margin)
    draw.text((margin, bl_y), "Discipline over action", font=_font(18, "medium"), fill=INK)
    bbox = draw.textbbox((0, 0), RECEIPTS_URL, font=_font(18, "medium"))
    fw = bbox[2] - bbox[0]
    draw.text((WIDTH - margin - fw, bl_y), RECEIPTS_URL, font=_font(18, "medium"), fill=INK)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), quality=95)
    return output_path


def build_market_take(date: str) -> dict:
    """Build the full Market Disagreement payload for a date. Returns dict with 'image', 'tweet', 'fallback' fields."""
    daily = load_daily(date)
    if daily is None:
        return {"error": f"no daily JSON found for {date}"}

    picks = daily.get("picks", [])
    games = daily.get("games", [])

    if not picks:
        # 0-pick day fallback
        out = OUT_DIR / f"market_take_{date}_nope.png"
        path = render_no_edges_card(date, len(games), out)
        tweet = (
            f"{len(games)} games on the board tonight. 0 edges cleared the threshold. "
            f"The model passes. Receipts: {RECEIPTS_URL}"
        )
        return {"image": str(path), "tweet": tweet, "mode": "no_edges"}

    pick = pick_top_disagreement(picks)
    team = pick.get("team", "")
    opponent = pick.get("opponent", "")
    side = pick.get("side", "")
    matchup = f"{team} @ {opponent}" if side == "away" else f"{opponent} @ {team}"

    # Try LLM first, fall back to rule-based
    tweet, bullets = llm_take(pick, matchup)
    used_llm = tweet is not None
    if not tweet or not bullets:
        tweet, bullets = fallback_tweet(pick, matchup)

    # Append URL to tweet
    tweet_full = f"{tweet}\n\n{RECEIPTS_URL}"

    out = OUT_DIR / f"market_take_{date}.png"
    path = render_disagreement_card(pick, matchup, bullets, date, out, daily=daily)

    return {
        "image": str(path),
        "tweet": tweet_full,
        "bullets": bullets,
        "mode": "disagreement",
        "llm_used": used_llm,
        "edge_pct": pick.get("edge_pct", 0),
        "pick": pick.get("pick", ""),
        "matchup": matchup,
    }


def post_to_twitter(image_path: str, tweet_text: str) -> bool:
    """Fire the tweet with the image attached."""
    try:
        import tweepy
    except ImportError:
        print("  ERROR: tweepy not installed")
        return False
    keys = ["TWITTER_API_KEY", "TWITTER_API_SECRET", "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_TOKEN_SECRET"]
    if not all(os.getenv(k) for k in keys):
        print("  ERROR: Twitter creds missing")
        return False
    client = tweepy.Client(
        consumer_key=os.getenv("TWITTER_API_KEY"),
        consumer_secret=os.getenv("TWITTER_API_SECRET"),
        access_token=os.getenv("TWITTER_ACCESS_TOKEN"),
        access_token_secret=os.getenv("TWITTER_ACCESS_TOKEN_SECRET"),
    )
    auth = tweepy.OAuth1UserHandler(
        os.getenv("TWITTER_API_KEY"),
        os.getenv("TWITTER_API_SECRET"),
        os.getenv("TWITTER_ACCESS_TOKEN"),
        os.getenv("TWITTER_ACCESS_TOKEN_SECRET"),
    )
    api = tweepy.API(auth)
    try:
        media = api.media_upload(image_path)
        resp = client.create_tweet(text=tweet_text, media_ids=[media.media_id])
        tid = resp.data["id"]
        print(f"  POSTED: https://x.com/Ozzy_Analytics/status/{tid}")
        return True
    except Exception as e:
        print(f"  Twitter error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Build today's Market Disagreement tweet")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--post", action="store_true", help="Actually post to Twitter (default: preview only)")
    args = parser.parse_args()

    # Load .env if present
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

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
        post_to_twitter(result["image"], result["tweet"])
    else:
        print("\n  (Preview only. Re-run with --post to tweet.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
