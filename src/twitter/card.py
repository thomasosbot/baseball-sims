"""
Generate a Twitter pick card image (1200x675, landscape).

Renders all daily picks, yesterday's recap, and season stats
into a single shareable image.

Usage:
    from src.twitter.card import generate_pick_card
    path = generate_pick_card(picks_data)
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from src.betting.units import fmt_u

# ── Dimensions (Twitter recommended 1200x675 for summary_large_image) ──
WIDTH = 1200
HEIGHT = 675

# ── Colors (dark theme matching site) ──
BG = (20, 22, 28)
BG_END = (28, 31, 40)
CARD_BG = (38, 42, 56)
CARD_BORDER = (58, 63, 82)
TEXT = (245, 247, 250)
TEXT_SEC = (160, 165, 175)
TEXT_MUT = (120, 125, 135)
ACCENT = (24, 119, 242)
GREEN = (49, 162, 76)
RED = (250, 56, 62)

RESULTS_PATH = Path(__file__).parent.parent.parent / "data" / "daily" / "results.json"

_FONT_CACHE: dict = {}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (bold, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    paths = [
        "/System/Library/Fonts/SFCompact.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in paths:
        try:
            f = ImageFont.truetype(p, size)
            _FONT_CACHE[key] = f
            return f
        except (OSError, IOError):
            continue
    f = ImageFont.load_default(size=size)
    _FONT_CACHE[key] = f
    return f


def _load_results():
    if not RESULTS_PATH.exists():
        return None, None
    with open(RESULTS_PATH) as f:
        results = json.load(f)
    if not results:
        return None, None

    yesterday = results[-1]
    total_wins = sum(r.get("wins", 0) for r in results)
    total_losses = sum(r.get("losses", 0) for r in results)
    total_wagered = sum(
        sum(abs(p.get("wager", 0)) for p in r.get("picks", []))
        for r in results
    )
    total_profit = sum(r.get("day_profit", 0) for r in results)
    roi = round(total_profit / total_wagered * 100, 1) if total_wagered > 0 else 0

    season = {
        "wins": total_wins,
        "losses": total_losses,
        "total_profit": total_profit,
        "roi": roi,
        "bankroll": round(10000.0 + total_profit, 2),
    }
    return yesterday, season


def _gradient_bg(img):
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / HEIGHT
        r = int(BG[0] + (BG_END[0] - BG[0]) * t)
        g = int(BG[1] + (BG_END[1] - BG[1]) * t)
        b = int(BG[2] + (BG_END[2] - BG[2]) * t)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))


def generate_pick_card(picks_data: dict, output_path: str | Path = None) -> Path:
    """Generate a single Twitter card image with all picks."""
    date = picks_data.get("date", "")
    picks = picks_data.get("picks", [])
    # Deduplicate
    seen = set()
    unique_picks = []
    for p in picks:
        key = p.get("pick", "")
        if key not in seen:
            seen.add(key)
            unique_picks.append(p)
    picks = unique_picks

    yesterday, season = _load_results()

    output_path = Path(output_path) if output_path else Path(f"data/twitter/card_{date}.png")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (WIDTH, HEIGHT))
    _gradient_bg(img)
    draw = ImageDraw.Draw(img)

    # ── Left side: Brand + Picks ──
    left_w = 750
    x = 40
    y = 32

    # Brand header
    brand_font = _font(28, bold=True)
    draw.text((x, y), "OZZY ANALYTICS", font=brand_font, fill=TEXT)
    bbox = draw.textbbox((0, 0), "OZZY ANALYTICS", font=brand_font)
    brand_w = bbox[2] - bbox[0]
    draw.rounded_rectangle([x, y + 38, x + brand_w, y + 42], radius=2, fill=ACCENT)

    # Date
    draw.text((x + brand_w + 20, y + 5), f"Picks for {date}", font=_font(22), fill=TEXT_SEC)

    y = 85

    # Pick rows
    if picks:
        for i, p in enumerate(picks):
            pick_type = p.get("type", "moneyline")
            badge_text = "ML" if pick_type == "moneyline" else "RL"
            badge_color = ACCENT if pick_type == "moneyline" else GREEN

            row_y = y + i * 72

            # Card background
            draw.rounded_rectangle(
                [x, row_y, left_w, row_y + 62],
                radius=12, fill=CARD_BG, outline=CARD_BORDER, width=1
            )

            # Badge
            badge_fill = (20, 45, 85) if pick_type == "moneyline" else (20, 60, 35)
            draw.rounded_rectangle(
                [x + 12, row_y + 14, x + 52, row_y + 46],
                radius=8, fill=badge_fill, outline=badge_color, width=1
            )
            bbox = draw.textbbox((0, 0), badge_text, font=_font(16, bold=True))
            btw = bbox[2] - bbox[0]
            draw.text((x + 32 - btw // 2, row_y + 17), badge_text, font=_font(16, bold=True), fill=TEXT)

            # Pick name
            draw.text((x + 65, row_y + 10), p.get("pick", ""), font=_font(26, bold=True), fill=TEXT)

            # Odds + Wager (units-first)
            odds = p.get("odds", "")
            wager = p.get("wager", 0)
            draw.text(
                (x + 65, row_y + 38),
                f"{odds}  •  {fmt_u(wager)} (${wager:,.0f})",
                font=_font(16), fill=TEXT_SEC,
            )

            # Model Win %
            prob = p.get("model_prob", 0)
            prob_text = f"{prob:.0%} win"
            draw.text((x + 350, row_y + 15), prob_text, font=_font(22, bold=True), fill=TEXT)

            # Edge
            edge = p.get("edge_pct", 0)
            edge_text = f"+{edge:.1f}% edge"
            draw.text((x + 350, row_y + 40), edge_text, font=_font(16, bold=True), fill=GREEN)

            # Matchup
            team = p.get("team", "")
            opponent = p.get("opponent", "")
            side = p.get("side", "")
            matchup = f"{team} @ {opponent}" if side == "away" else f"{opponent} @ {team}"
            bbox = draw.textbbox((0, 0), matchup, font=_font(18))
            mw = bbox[2] - bbox[0]
            draw.text((left_w - 12 - mw, row_y + 20), matchup, font=_font(18), fill=TEXT_MUT)
    else:
        draw.text((x, y + 20), "No edges today. Model is sitting tight.", font=_font(22), fill=TEXT_SEC)

    # ── Right side: Recap + Season ──
    rx = 790
    ry = 32
    right_w = WIDTH - rx - 30

    # Yesterday's recap
    if yesterday and yesterday.get("picks"):
        draw.text((rx, ry), "YESTERDAY", font=_font(14, bold=True), fill=TEXT_MUT)
        ry += 24
        wins = yesterday.get("wins", 0)
        losses = yesterday.get("losses", 0)
        profit = yesterday.get("day_profit", 0)
        sign = "+" if profit >= 0 else "-"
        p_color = GREEN if profit >= 0 else RED
        draw.text((rx, ry), f"{wins}W-{losses}L", font=_font(26, bold=True), fill=TEXT)
        draw.text((rx + 85, ry + 4), f"{fmt_u(profit, signed=True)}", font=_font(22, bold=True), fill=p_color)
        draw.text((rx + 85, ry + 32), f"{sign}${abs(profit):,.0f}", font=_font(13), fill=TEXT_MUT)
        ry += 54

        for p in yesterday["picks"][:4]:
            won = p.get("won", False)
            icon_color = GREEN if won else RED
            draw.rounded_rectangle([rx, ry, rx + 22, ry + 22], radius=4, fill=icon_color)
            label = "W" if won else "L"
            draw.text((rx + 5, ry + 1), label, font=_font(14, bold=True), fill=TEXT)
            pick_name = p.get("pick", "").replace(" ML", "").replace(" +1.5", "")
            draw.text((rx + 30, ry + 2), pick_name, font=_font(16), fill=TEXT_SEC)
            ry += 28
        ry += 10

    # Season stats
    if season:
        draw.text((rx, ry), "SEASON", font=_font(14, bold=True), fill=TEXT_MUT)
        ry += 24

        draw.rounded_rectangle([rx, ry, rx + right_w, ry + 130], radius=12, fill=CARD_BG)

        w, l = season["wins"], season["losses"]
        draw.text((rx + 14, ry + 10), f"{w}W-{l}L", font=_font(24, bold=True), fill=TEXT)

        profit = season["total_profit"]
        sign = "+" if profit >= 0 else "-"
        p_color = GREEN if profit >= 0 else RED
        draw.text((rx + 14, ry + 42), f"{fmt_u(profit, signed=True)}", font=_font(22, bold=True), fill=p_color)
        draw.text((rx + 14, ry + 68), f"{sign}${abs(profit):,.0f}", font=_font(13), fill=TEXT_MUT)

        draw.text((rx + 14, ry + 88), f"ROI: {season['roi']}%", font=_font(16), fill=TEXT_SEC)
        bankroll = season['bankroll']
        draw.text((rx + 14, ry + 108), f"Roll: {fmt_u(bankroll)} (${bankroll:,.0f})", font=_font(13), fill=TEXT_MUT)

    # ── Footer ──
    draw.text((x, HEIGHT - 38), "10,000 simulations per game", font=_font(16), fill=TEXT_MUT)
    bbox = draw.textbbox((0, 0), "ozzyanalytics.com", font=_font(18, bold=True))
    fw = bbox[2] - bbox[0]
    draw.text((WIDTH - 30 - fw, HEIGHT - 38), "ozzyanalytics.com", font=_font(18, bold=True), fill=ACCENT)

    img.save(str(output_path), quality=95)
    print(f"  Twitter card saved: {output_path}")
    return output_path
