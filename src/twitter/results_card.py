"""
Generate a Twitter results card image (1200x675, landscape).

Shows today's results: W/L record, P&L, individual pick results
with scores, and running season stats.

Usage:
    from src.twitter.results_card import generate_results_card
    path = generate_results_card(day_results, season_stats)
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1200
HEIGHT = 675

# Colors — same as card.py
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
WIN_BG = (30, 55, 35)
LOSS_BG = (55, 30, 30)

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


def _gradient_bg(img):
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / HEIGHT
        r = int(BG[0] + (BG_END[0] - BG[0]) * t)
        g = int(BG[1] + (BG_END[1] - BG[1]) * t)
        b = int(BG[2] + (BG_END[2] - BG[2]) * t)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))


def generate_results_card(
    day_results: dict,
    season_stats: dict,
    output_path: str | Path = None,
) -> Path:
    """Generate a results card image for the nightly tweet."""
    date = day_results.get("date", "")
    picks = day_results.get("picks", [])
    wins = day_results.get("wins", 0)
    losses = day_results.get("losses", 0)
    day_profit = day_results.get("day_profit", 0)

    output_path = Path(output_path) if output_path else Path(f"data/twitter/results_{date}.png")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (WIDTH, HEIGHT))
    _gradient_bg(img)
    draw = ImageDraw.Draw(img)

    # ── Header: Brand + Date ──
    brand_font = _font(24, bold=True)
    draw.text((40, 28), "OZZY ANALYTICS", font=brand_font, fill=TEXT)
    bbox = draw.textbbox((0, 0), "OZZY ANALYTICS", font=brand_font)
    brand_w = bbox[2] - bbox[0]
    draw.rounded_rectangle([40, 58, 40 + brand_w, 62], radius=2, fill=ACCENT)

    draw.text((40 + brand_w + 20, 32), f"Results — {date}", font=_font(20), fill=TEXT_SEC)

    # ── Big W-L + P&L ──
    record_text = f"{wins}W - {losses}L"
    draw.text((40, 85), record_text, font=_font(52, bold=True), fill=TEXT)

    profit_sign = "+" if day_profit >= 0 else "-"
    profit_color = GREEN if day_profit >= 0 else RED
    profit_text = f"{profit_sign}${abs(day_profit):,.0f}"
    bbox = draw.textbbox((0, 0), record_text, font=_font(52, bold=True))
    record_w = bbox[2] - bbox[0]
    draw.text((40 + record_w + 24, 95), profit_text, font=_font(42, bold=True), fill=profit_color)

    # ── Pick results rows ──
    x = 40
    y = 165
    row_h = 52
    max_rows = 8
    row_w = 700

    for i, p in enumerate(picks[:max_rows]):
        won = p.get("won", False)
        ry = y + i * (row_h + 6)
        row_bg = WIN_BG if won else LOSS_BG

        # Row background
        draw.rounded_rectangle([x, ry, x + row_w, ry + row_h], radius=10, fill=row_bg)

        # W/L badge
        badge_color = GREEN if won else RED
        badge_text = "W" if won else "L"
        draw.rounded_rectangle([x + 10, ry + 10, x + 42, ry + 42], radius=6, fill=BG, outline=badge_color, width=2)
        bbox = draw.textbbox((0, 0), badge_text, font=_font(18, bold=True))
        btw = bbox[2] - bbox[0]
        draw.text((x + 26 - btw // 2, ry + 13), badge_text, font=_font(18, bold=True), fill=badge_color)

        # Pick name
        draw.text((x + 54, ry + 8), p.get("pick", ""), font=_font(22, bold=True), fill=TEXT)

        # Score
        score = p.get("actual_score", "")
        draw.text((x + 54, ry + 32), score, font=_font(14), fill=TEXT_MUT)

        # Odds
        odds = p.get("odds", "")
        draw.text((x + 380, ry + 15), odds, font=_font(18), fill=TEXT_SEC)

        # Profit
        pnl = p.get("profit", 0)
        pnl_sign = "+" if pnl >= 0 else ""
        pnl_color = GREEN if pnl >= 0 else RED
        pnl_text = f"{pnl_sign}${pnl:,.0f}"
        bbox = draw.textbbox((0, 0), pnl_text, font=_font(22, bold=True))
        ptw = bbox[2] - bbox[0]
        draw.text((x + row_w - 14 - ptw, ry + 14), pnl_text, font=_font(22, bold=True), fill=pnl_color)

    # ── Right side: Season stats ──
    rx = 790
    ry = 85

    draw.text((rx, ry), "SEASON", font=_font(14, bold=True), fill=TEXT_MUT)
    ry += 24

    draw.rounded_rectangle([rx, ry, WIDTH - 40, ry + 200], radius=12, fill=CARD_BG)

    sw, sl = season_stats.get("wins", 0), season_stats.get("losses", 0)
    draw.text((rx + 16, ry + 14), f"{sw}W-{sl}L", font=_font(30, bold=True), fill=TEXT)

    sp = season_stats.get("total_profit", 0)
    sp_sign = "+" if sp >= 0 else "-"
    sp_color = GREEN if sp >= 0 else RED
    draw.text((rx + 16, ry + 54), f"{sp_sign}${abs(sp):,.0f}", font=_font(26, bold=True), fill=sp_color)

    roi = season_stats.get("roi", 0)
    roi_color = GREEN if roi >= 0 else RED
    draw.text((rx + 16, ry + 92), f"ROI: {roi}%", font=_font(20), fill=roi_color)

    bankroll = season_stats.get("bankroll", 10000)
    draw.text((rx + 16, ry + 120), f"Bankroll: ${bankroll:,.0f}", font=_font(18), fill=TEXT_SEC)

    # Win rate
    total_picks = sw + sl
    win_pct = round(sw / total_picks * 100, 1) if total_picks > 0 else 0
    draw.text((rx + 16, ry + 152), f"Win Rate: {win_pct}%", font=_font(18), fill=TEXT_SEC)

    # Days
    days = season_stats.get("days", 0)
    draw.text((rx + 16, ry + 176), f"{days} days tracked", font=_font(16), fill=TEXT_MUT)

    # ── Callout line ──
    if wins > 0 and losses == 0:
        callout = "PERFECT DAY"
        callout_color = GREEN
    elif wins == 0 and losses > 0:
        callout = "TOUGH NIGHT"
        callout_color = RED
    elif day_profit > 500:
        callout = "BIG DAY"
        callout_color = GREEN
    elif day_profit > 0:
        callout = "PROFITABLE"
        callout_color = GREEN
    else:
        callout = ""
        callout_color = TEXT_MUT

    if callout:
        bbox = draw.textbbox((0, 0), callout, font=_font(16, bold=True))
        cw = bbox[2] - bbox[0]
        cx = rx + (WIDTH - 40 - rx - cw) // 2
        draw.rounded_rectangle([cx - 12, ry + 210, cx + cw + 12, ry + 236], radius=10, fill=BG, outline=callout_color, width=2)
        draw.text((cx, ry + 213), callout, font=_font(16, bold=True), fill=callout_color)

    # ── Footer ──
    draw.text((40, HEIGHT - 38), "10,000 simulations per game", font=_font(14), fill=TEXT_MUT)
    url_font = _font(16, bold=True)
    bbox = draw.textbbox((0, 0), "ozzyanalytics.com", font=url_font)
    fw = bbox[2] - bbox[0]
    draw.text((WIDTH - 40 - fw, HEIGHT - 38), "ozzyanalytics.com", font=url_font, fill=ACCENT)

    img.save(str(output_path), quality=95)
    print(f"  Results card saved: {output_path}")
    return output_path
