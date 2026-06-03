"""
Shared primitives for daily Twitter card generators.

Provides: palette (dark navy matching site), Avenir-based font helper,
gradient background, drawing primitives (centered, hairline, horizontal
bar), sportsbook helpers, statcast rollup lookups (pitcher + hitter +
top-N hitters), American odds → implied prob, daily-JSON loader, and a
shared Twitter posting helper. Importers add their own selection logic,
LLM prompts, and render layouts.
"""
from __future__ import annotations

import json
import os
import pickle
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ── Paths + dimensions ──
ROOT = Path(__file__).parent.parent.parent
DAILY_DIR = ROOT / "data" / "daily"
OUT_DIR = ROOT / "data" / "twitter"
ROLLUP_PATH = ROOT / "data" / "processed" / "statcast_rollup_2025.pkl"

WIDTH = 1200
HEIGHT = 1200  # square — Twitter renders these at full size in feed

RECEIPTS_URL = "ozzyanalytics.com/60-days.html"

# ── Palette: dark navy matching site theme ──
PAPER = (15, 20, 36)            # site bg-end #0F1424
PAPER_TOP = (10, 14, 26)        # site bg-start #0A0E1A
INK = (244, 246, 250)           # site text #F4F6FA
INK_SOFT = (184, 191, 204)      # site text-secondary #B8BFCC
INK_MUT = (107, 114, 128)
RULE = (55, 60, 80)
CARD_BG = (24, 29, 45)
CARD_BORDER = (38, 42, 56)
BAR_BG = (32, 38, 56)
BAR_MARKET = (138, 144, 158)
BAR_MODEL = (74, 222, 128)      # site green #4ADE80 — single bright accent
BAR_MODEL_TINT = (24, 60, 40)
GREEN_DEEP = (22, 163, 74)

# League baselines (rough 2025 MLB averages)
LG_AVG = {
    "k_pct": 0.224,
    "bb_pct": 0.084,
    "xwoba": 0.318,
    "barrel_pct": 0.075,
}

# ── Fonts (Avenir Next via .ttc indices) ──
_AVENIR_PATH = "/System/Library/Fonts/Avenir Next.ttc"
_GEORGIA_PATH = "/System/Library/Fonts/Supplemental/Georgia Italic.ttf"
_FALLBACK = "/System/Library/Fonts/Helvetica.ttc"

_FONT_CACHE: dict = {}


def _font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    """weight: 'light', 'regular', 'medium', 'demi', 'bold', 'italic-serif'."""
    key = (weight, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    weight_to_idx = {"light": 8, "regular": 0, "medium": 6, "demi": 4, "bold": 2}
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


def paper_bg(img: Image.Image):
    """Dark navy background with subtle top-to-bottom gradient matching the site."""
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / HEIGHT
        r = int(PAPER_TOP[0] + (PAPER[0] - PAPER_TOP[0]) * t)
        g = int(PAPER_TOP[1] + (PAPER[1] - PAPER_TOP[1]) * t)
        b = int(PAPER_TOP[2] + (PAPER[2] - PAPER_TOP[2]) * t)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))


# ── Drawing primitives ──

def centered(draw, text, font, y, fill, x_start=0, x_end=WIDTH) -> int:
    """Draw `text` horizontally centered between x_start and x_end. Returns width."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text((x_start + (x_end - x_start - tw) // 2, y), text, font=font, fill=fill)
    return tw


def hairline(draw, x1, y, x2, fill=RULE, width=1):
    """Thin horizontal divider."""
    draw.line([(x1, y), (x2, y)], fill=fill, width=width)


def hbar(draw, x, y, w, h, pct, fill, track_fill=BAR_BG, max_pct=100.0):
    """Horizontal bar with track. Returns end-x of fill (for annotation positioning)."""
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=track_fill)
    fill_w = int(w * (min(pct, max_pct) / max_pct))
    if fill_w >= h:
        draw.rounded_rectangle([x, y, x + fill_w, y + h], radius=h // 2, fill=fill)
    elif fill_w > 0:
        draw.rectangle([x, y, x + fill_w, y + h], fill=fill)
    return x + fill_w


# ── Odds + sportsbook helpers ──

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


def format_odds(o: int) -> str:
    return f"+{o}" if o > 0 else f"{o}"


def format_book_name(book_key: str) -> str:
    mapping = {
        "fanduel": "FanDuel",
        "draftkings": "DraftKings",
        "betmgm": "BetMGM",
        "williamhill_us": "Caesars",
        "bovada": "Bovada",
    }
    return mapping.get(book_key, book_key.title())


def best_book(pick: dict) -> tuple[str, int]:
    """(book_name, odds) for the best price on this pick. Higher payout wins."""
    books = pick.get("sportsbook_odds", {}) or {}
    if not books:
        return ("", 0)
    def value(o):
        return o if o > 0 else -10000 - o
    return max(books.items(), key=lambda kv: value(kv[1]))


# ── Daily JSON + game lookup ──

def load_daily(date: str) -> dict | None:
    path = DAILY_DIR / f"{date}.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def find_game(daily: dict, team: str, opponent: str) -> dict | None:
    for g in daily.get("games", []):
        if {g.get("away"), g.get("home")} == {team, opponent}:
            return g
    return None


# ── Statcast rollup lookups ──

_ROLLUP_CACHE = None


def load_rollup() -> dict:
    """Load (or return cached) statcast rollup, keyed by MLBAM ID."""
    global _ROLLUP_CACHE
    if _ROLLUP_CACHE is not None:
        return _ROLLUP_CACHE
    if not ROLLUP_PATH.exists():
        _ROLLUP_CACHE = {"hitters": {}, "pitchers": {}}
        return _ROLLUP_CACHE
    with open(ROLLUP_PATH, "rb") as f:
        _ROLLUP_CACHE = pickle.load(f)
    return _ROLLUP_CACHE


# Pitcher rollup stores keys as "<stat>_against" / "bf" (batters faced).
# Hitter rollup uses bare keys (xwoba, barrel_pct, hard_hit_pct, woba, pa).
# Normalize pitcher records to the hitter schema so render code is uniform.
_PITCHER_FIELD_RENAMES = {
    "xwoba_against": "xwoba",
    "woba_against": "woba",
    "barrel_against": "barrel_pct",
    "hard_hit_against": "hard_hit_pct",
    "bf": "pa",  # batters faced is the pitcher equivalent of plate appearances
}


def _normalize_pitcher_dict(d: dict | None) -> dict | None:
    """Translate pitcher rollup field names to match hitter schema. Non-mutating."""
    if not d:
        return d
    out = {}
    for split_key in ("all", "vsL", "vsR"):
        section = d.get(split_key)
        if section is None:
            continue
        normalized = dict(section)
        for src, dst in _PITCHER_FIELD_RENAMES.items():
            if src in normalized and dst not in normalized:
                normalized[dst] = normalized[src]
        out[split_key] = normalized
    # Carry forward any other top-level keys
    for k, v in d.items():
        if k not in ("all", "vsL", "vsR"):
            out[k] = v
    return out


def pitcher_stats(name: str) -> dict | None:
    """Resolve a pitcher name → normalized stats dict ({'all','vsL','vsR'}) or None.

    Normalizes pitcher-specific field names (xwoba_against → xwoba, etc.) so callers
    can use the same key names as hitter records.
    """
    if not name or name == "TBD":
        return None
    try:
        from src.features.name_resolver import resolve_id
        pid = resolve_id(name)
    except Exception:
        return None
    if pid is None:
        return None
    raw = load_rollup().get("pitchers", {}).get(pid)
    return _normalize_pitcher_dict(raw)


def stats_complete(stats: dict | None, fields: tuple = ("k_pct", "bb_pct", "xwoba", "barrel_pct")) -> bool:
    """True iff the 'all' section of `stats` has non-None values for every required field."""
    if not stats or not stats.get("all"):
        return False
    a = stats["all"]
    return all(a.get(f) is not None for f in fields)


def hitter_stats(name: str) -> dict | None:
    """Resolve a hitter name → rollup stats dict ({'all','vsL','vsR'}) or None."""
    if not name or name == "TBD":
        return None
    try:
        from src.features.name_resolver import resolve_id
        pid = resolve_id(name)
    except Exception:
        return None
    if pid is None:
        return None
    return load_rollup().get("hitters", {}).get(pid)


def top_hitters(names: list[str], n: int = 3, min_pa: int = 50) -> list[tuple[str, dict]]:
    """Return [(name, all_stats_dict), ...] of top n hitters by xwOBA."""
    if not names:
        return []
    try:
        from src.features.name_resolver import resolve_id
    except Exception:
        return []
    hitters = load_rollup().get("hitters", {})
    scored = []
    for nm in names:
        pid = resolve_id(nm)
        if pid is None:
            continue
        all_stats = hitters.get(pid, {}).get("all", {})
        xw = all_stats.get("xwoba")
        if xw is None or all_stats.get("pa", 0) < min_pa:
            continue
        scored.append((nm, hitters[pid], xw))
    scored.sort(key=lambda t: t[2], reverse=True)
    return [(nm, stats) for nm, stats, _ in scored[:n]]


# ── Stat formatting + display helpers ──

def pct_str(v) -> str:
    return f"{v*100:.1f}%" if v is not None else "—"


def woba_str(v) -> str:
    return f".{int(round(v*1000)):03d}" if v is not None else "—"


def text_height(font) -> int:
    a, _ = font.getmetrics()
    return a


# ── .env loading (for standalone CLI use) ──

def load_env():
    """Load .env at repo root into os.environ if not already set."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


# ── Twitter posting (image + text) ──

def post_image_tweet(image_path: str, tweet_text: str) -> bool:
    """Upload `image_path` as media and post a tweet with `tweet_text`. Returns success bool."""
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


def call_llm_shared(system: str, user: str, max_tokens: int = 500) -> str | None:
    """Wrapper that imports _call_llm from src.betting.narrative."""
    try:
        from src.betting.narrative import _call_llm
        return _call_llm(system, user, max_tokens=max_tokens)
    except Exception:
        return None
