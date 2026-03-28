"""
Generate TikTok-style vertical videos (1080x1920) for daily picks.

Uses Pillow for frame rendering and MoviePy for video assembly.
Dark background with pastel accents matching the site's glass UI.

Usage:
    from src.tiktok.video import generate_picks_video
    generate_picks_video("data/daily/2026-03-27.json", "output/picks.mp4")
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ── Video dimensions (9:16 vertical) ──
WIDTH = 1080
HEIGHT = 1920
FPS = 30

# ── Color palette (matches site CSS vars, inverted for dark mode) ──
BG_COLOR = (20, 22, 28)  # dark navy
BG_GRADIENT_END = (30, 33, 42)
CARD_BG = (38, 42, 56)
CARD_BORDER = (58, 63, 82)
TEXT_PRIMARY = (245, 247, 250)  # --bg-start inverted
TEXT_SECONDARY = (160, 165, 175)
TEXT_MUTED = (120, 125, 135)
ACCENT = (24, 119, 242)  # --accent #1877F2
GREEN = (49, 162, 76)  # --green
RED = (250, 56, 62)  # --red

# ── Timing (seconds) ──
INTRO_DURATION = 2.5
RECAP_DURATION = 3.5
SEASON_DURATION = 3.0
CARD_REVEAL_DURATION = 0.8
CARD_HOLD_DURATION = 3.0
CARD_TOTAL = CARD_REVEAL_DURATION + CARD_HOLD_DURATION
OUTRO_DURATION = 3.0

# ── Layout ──
CARD_MARGIN_X = 50
CARD_RADIUS = 24
CARD_PADDING = 40

# ── Results data path ──
RESULTS_PATH = Path(__file__).parent.parent.parent / "data" / "daily" / "results.json"

# ── Fonts ──
_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (f"bold={bold}", size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    font_paths = [
        # macOS
        "/System/Library/Fonts/SFCompact.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        # Linux (GitHub Actions)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]

    for path in font_paths:
        try:
            font = ImageFont.truetype(path, size)
            _FONT_CACHE[key] = font
            return font
        except (OSError, IOError):
            continue

    font = ImageFont.load_default(size=size)
    _FONT_CACHE[key] = font
    return font


def _draw_rounded_rect(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    radius: int,
    fill: tuple,
    outline: tuple | None = None,
):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=2)


def _draw_gradient_bg(img: Image.Image):
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / HEIGHT
        r = int(BG_COLOR[0] + (BG_GRADIENT_END[0] - BG_COLOR[0]) * t)
        g = int(BG_COLOR[1] + (BG_GRADIENT_END[1] - BG_COLOR[1]) * t)
        b = int(BG_COLOR[2] + (BG_GRADIENT_END[2] - BG_COLOR[2]) * t)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))


def _centered_text(draw, y, text, font, fill):
    """Draw text centered horizontally."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text(((WIDTH - tw) // 2, y), text, font=font, fill=fill)
    return tw


def _load_results() -> tuple[dict | None, dict | None]:
    """Load yesterday's results and season stats. Returns (yesterday, season_stats)."""
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
    bankroll = round(10000.0 + total_profit, 2)

    season = {
        "wins": total_wins,
        "losses": total_losses,
        "total_profit": total_profit,
        "roi": roi,
        "bankroll": bankroll,
        "days": len(results),
    }

    return yesterday, season


# ── Frame renderers ──

def _render_intro_frame(date: str, num_picks: int, progress: float) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT))
    _draw_gradient_bg(img)
    draw = ImageDraw.Draw(img)

    ease = _ease_out_cubic(min(progress * 1.5, 1.0))
    offset_y = int((1 - ease) * 60)

    # Brand name
    font_brand = _get_font(64, bold=True)
    text = "OZZY ANALYTICS"
    bbox = draw.textbbox((0, 0), text, font=font_brand)
    tw = bbox[2] - bbox[0]
    x = (WIDTH - tw) // 2
    y = 640 - offset_y
    draw.rounded_rectangle(
        [x - 20, y + 82, x + tw + 20, y + 88], radius=3, fill=ACCENT
    )
    draw.text((x, y), text, font=font_brand, fill=TEXT_PRIMARY)

    # Tagline
    font_tag = _get_font(40)
    tag = "10,000 Simulations. Zero Gut Feelings."
    alpha = min(progress * 2 - 0.3, 1.0)
    if alpha > 0:
        color = _fade_color(TEXT_SECONDARY, alpha)
        _centered_text(draw, y + 120 - offset_y, tag, font_tag, color)

    # Date + prediction count
    font_date = _get_font(52, bold=True)
    date_text = f"Predictions for {date}"
    alpha2 = min(progress * 2 - 0.6, 1.0)
    if alpha2 > 0:
        color = _fade_color(TEXT_PRIMARY, alpha2)
        _centered_text(draw, y + 220 - offset_y, date_text, font_date, color)

    # Prediction count badge
    if alpha2 > 0:
        font_count = _get_font(36)
        count_text = f"{num_picks} prediction{'s' if num_picks != 1 else ''} today"
        bbox = draw.textbbox((0, 0), count_text, font=font_count)
        tw = bbox[2] - bbox[0]
        cx = (WIDTH - tw) // 2
        cy = y + 300 - offset_y
        _draw_rounded_rect(
            draw, (cx - 24, cy - 10, cx + tw + 24, cy + 46),
            radius=20, fill=(30, 55, 100), outline=ACCENT,
        )
        draw.text((cx, cy), count_text, font=font_count, fill=TEXT_PRIMARY)

    return img


def _render_recap_frame(yesterday: dict, progress: float) -> Image.Image:
    """Render yesterday's results recap."""
    img = Image.new("RGB", (WIDTH, HEIGHT))
    _draw_gradient_bg(img)
    draw = ImageDraw.Draw(img)

    ease = _ease_out_cubic(min(progress * 1.5, 1.0))

    # Title
    font_title = _get_font(48, bold=True)
    _centered_text(draw, 160, "YESTERDAY'S PREDICTIONS", font_title, TEXT_PRIMARY)

    # Date
    font_date = _get_font(32)
    recap_date = yesterday.get("date", "")
    _centered_text(draw, 225, recap_date, font_date, TEXT_MUTED)

    # Record summary
    wins = yesterday.get("wins", 0)
    losses = yesterday.get("losses", 0)
    total = wins + losses
    pct = round(wins / total * 100) if total > 0 else 0

    font_record = _get_font(72, bold=True)
    _centered_text(draw, 310, f"{wins}W - {losses}L", font_record, TEXT_PRIMARY)

    font_pct = _get_font(48, bold=True)
    pct_color = GREEN if wins >= losses else RED
    _centered_text(draw, 405, f"{pct}% correct", font_pct, pct_color)

    # Individual predictions
    picks = yesterday.get("picks", [])
    card_x1 = CARD_MARGIN_X
    card_x2 = WIDTH - CARD_MARGIN_X
    card_y = 510

    for i, p in enumerate(picks):
        pick_alpha = min((progress - 0.15 - i * 0.1) / 0.3, 1.0)
        if pick_alpha <= 0:
            continue

        row_h = 90
        ry = card_y + i * (row_h + 12)

        won = p.get("won", False)
        row_bg = (30, 55, 35) if won else (55, 30, 30)
        _draw_rounded_rect(draw, (card_x1, ry, card_x2, ry + row_h), radius=16, fill=row_bg)

        # Correct/Wrong badge
        badge_text = "\u2713" if won else "\u2717"
        badge_color = GREEN if won else RED
        font_badge = _get_font(36, bold=True)
        bx = card_x1 + 24
        by = ry + 12
        _draw_rounded_rect(draw, (bx, by, bx + 50, by + 50), radius=12,
                           fill=(20, 22, 28), outline=badge_color)
        bbox = draw.textbbox((0, 0), badge_text, font=font_badge)
        btw = bbox[2] - bbox[0]
        draw.text((bx + (50 - btw) // 2, by + 4), badge_text, font=font_badge, fill=badge_color)

        # Team name (strip "ML" / "+1.5" etc)
        font_pick = _get_font(36, bold=True)
        pick_name = p.get("pick", "").replace(" ML", "").replace(" +1.5", "")
        color = _fade_color(TEXT_PRIMARY, pick_alpha)
        draw.text((bx + 70, ry + 10), pick_name, font=font_pick, fill=color)

        # Score
        font_score = _get_font(28)
        score = p.get("actual_score", "")
        color = _fade_color(TEXT_SECONDARY, pick_alpha)
        draw.text((bx + 70, ry + 52), score, font=font_score, fill=color)

        # Result text on right
        result_text = "CORRECT" if won else "WRONG"
        result_color = _fade_color(GREEN if won else RED, pick_alpha)
        font_result = _get_font(30, bold=True)
        bbox = draw.textbbox((0, 0), result_text, font=font_result)
        rtw = bbox[2] - bbox[0]
        draw.text((card_x2 - 24 - rtw, ry + 28), result_text, font=font_result, fill=result_color)

    return img


def _render_season_frame(season: dict, progress: float) -> Image.Image:
    """Render season stats overview."""
    img = Image.new("RGB", (WIDTH, HEIGHT))
    _draw_gradient_bg(img)
    draw = ImageDraw.Draw(img)

    ease = _ease_out_cubic(min(progress * 1.5, 1.0))

    # Title
    font_title = _get_font(48, bold=True)
    _centered_text(draw, 300, "MODEL PERFORMANCE", font_title, TEXT_PRIMARY)

    # Accent underline
    bbox = draw.textbbox((0, 0), "MODEL PERFORMANCE", font=font_title)
    tw = bbox[2] - bbox[0]
    ux = (WIDTH - tw) // 2
    draw.rounded_rectangle([ux - 10, 365, ux + tw + 10, 371], radius=3, fill=ACCENT)

    # Stats — prediction-focused, no gambling language
    total = season["wins"] + season["losses"]
    accuracy = round(season["wins"] / total * 100, 1) if total > 0 else 0
    stats = [
        ("RECORD", f"{season['wins']}W - {season['losses']}L", TEXT_PRIMARY),
        ("ACCURACY", f"{accuracy}%",
         GREEN if accuracy >= 50 else RED),
        ("GAMES ANALYZED", f"{season.get('days', 0)} day{'s' if season.get('days', 0) != 1 else ''}", TEXT_PRIMARY),
        ("SIMULATIONS", "10,000/game", ACCENT),
    ]

    font_label = _get_font(28)
    font_value = _get_font(64, bold=True)
    grid_y = 440
    col_w = WIDTH // 2
    row_h = 220

    for i, (label, value, color) in enumerate(stats):
        stat_alpha = min((progress - 0.2 - i * 0.1) / 0.3, 1.0)
        if stat_alpha <= 0:
            continue

        col = i % 2
        row = i // 2
        cx = col * col_w + col_w // 2
        cy = grid_y + row * row_h

        # Label
        label_color = _fade_color(TEXT_MUTED, stat_alpha)
        bbox = draw.textbbox((0, 0), label, font=font_label)
        ltw = bbox[2] - bbox[0]
        draw.text((cx - ltw // 2, cy), label, font=font_label, fill=label_color)

        # Value
        val_color = _fade_color(color, stat_alpha)
        bbox = draw.textbbox((0, 0), value, font=font_value)
        vtw = bbox[2] - bbox[0]
        draw.text((cx - vtw // 2, cy + 40), value, font=font_value, fill=val_color)

    return img


def _render_pick_card(
    pick: dict, card_index: int, total_cards: int, reveal_progress: float
) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT))
    _draw_gradient_bg(img)
    draw = ImageDraw.Draw(img)

    ease = _ease_out_cubic(reveal_progress)

    # Card counter at top
    font_counter = _get_font(40, bold=True)
    counter_text = f"PICK {card_index + 1} OF {total_cards}"
    _centered_text(draw, 90, counter_text, font_counter, TEXT_MUTED)

    # Main card - slides up and fades in
    card_x1 = CARD_MARGIN_X
    card_x2 = WIDTH - CARD_MARGIN_X
    card_y1 = 180 + int((1 - ease) * 80)
    card_h = 1150
    card_y2 = card_y1 + card_h

    _draw_rounded_rect(
        draw, (card_x1, card_y1, card_x2, card_y2),
        radius=CARD_RADIUS, fill=CARD_BG, outline=CARD_BORDER,
    )

    if reveal_progress < 0.1:
        return img

    content_alpha = min((reveal_progress - 0.1) / 0.3, 1.0)
    cx = card_x1 + CARD_PADDING
    cy = card_y1 + CARD_PADDING

    # Prediction badge
    font_badge = _get_font(32, bold=True)
    badge_text = "PREDICTION"
    bbox = draw.textbbox((0, 0), badge_text, font=font_badge)
    bw = bbox[2] - bbox[0]
    _draw_rounded_rect(
        draw, (cx, cy, cx + bw + 36, cy + 48),
        radius=22, fill=(20, 45, 85), outline=ACCENT,
    )
    draw.text((cx + 18, cy + 8), badge_text, font=font_badge, fill=TEXT_PRIMARY)

    # Team pick name (big) — strip betting terms
    font_pick = _get_font(96, bold=True)
    pick_name = pick.get("team", pick.get("pick", "???"))
    color = _fade_color(TEXT_PRIMARY, content_alpha)
    draw.text((cx, cy + 72), pick_name, font=font_pick, fill=color)

    # "predicted to win" label
    font_wins = _get_font(40)
    color = _fade_color(GREEN, content_alpha)
    draw.text((cx, cy + 185), "predicted to win", font=font_wins, fill=color)

    # Matchup line
    font_matchup = _get_font(44, bold=True)
    team = pick.get("team", "")
    opponent = pick.get("opponent", "")
    side = pick.get("side", "")
    if side == "away":
        matchup = f"{team} @ {opponent}"
    else:
        matchup = f"{opponent} @ {team}"
    color = _fade_color(TEXT_SECONDARY, content_alpha)
    draw.text((cx, cy + 245), matchup, font=font_matchup, fill=color)

    # Divider line
    div_y = cy + 320
    draw.line([(cx, div_y), (card_x2 - CARD_PADDING, div_y)], fill=CARD_BORDER, width=2)

    # Model win probability — big and centered
    stats_y = div_y + 40
    font_prob_label = _get_font(32)
    font_prob_value = _get_font(100, bold=True)

    prob = pick.get("model_prob", 0)
    prob_text = f"{prob:.0%}"
    prob_color = GREEN if prob >= 0.55 else TEXT_PRIMARY

    _centered_text(draw, stats_y, "MODEL WIN PROBABILITY", font_prob_label, TEXT_MUTED)

    bbox = draw.textbbox((0, 0), prob_text, font=font_prob_value)
    ptw = bbox[2] - bbox[0]
    prob_x = (WIDTH - ptw) // 2
    stat_alpha = min((reveal_progress - 0.25) / 0.3, 1.0)
    if stat_alpha > 0:
        draw.text((prob_x, stats_y + 45), prob_text, font=font_prob_value,
                  fill=_fade_color(prob_color, stat_alpha))

    # Confidence bar
    bar_y = stats_y + 175
    bar_x1 = cx
    bar_x2 = card_x2 - CARD_PADDING
    bar_h = 24
    # Background
    _draw_rounded_rect(draw, (bar_x1, bar_y, bar_x2, bar_y + bar_h),
                       radius=12, fill=(28, 30, 38))
    # Fill
    fill_w = int((bar_x2 - bar_x1) * prob)
    if fill_w > 0:
        _draw_rounded_rect(draw, (bar_x1, bar_y, bar_x1 + fill_w, bar_y + bar_h),
                           radius=12, fill=prob_color)

    # Explanation text at bottom — reframed without betting language
    explanation = pick.get("explanation", "")
    if explanation:
        import re
        clean = explanation
        # Remove full betting-specific sentences
        # Remove betting-specific content, keep pitcher/matchup context
        clean = re.sub(r'Run line bets[^.]*\.?', '', clean)
        clean = re.sub(r'The sim gives them[^.]*\.?', '', clean)
        clean = re.sub(r'The market has \S+ as a [+-]\d+ underdog, but our simulation disagrees\.',
                       'Our simulation sees this differently than the market.', clean)
        clean = re.sub(r'\S+ is getting \+1\.5 at [+-]\d+\.', '', clean)
        clean = re.sub(r'[—\-]\s*\d+\.?\d*%\s*edge\.?', '', clean)
        clean = re.sub(r'\d+% chance of covering', '', clean)
        clean = re.sub(r' [+-]\d+ ', ' ', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()
        # Fallback if everything got stripped
        if not clean or len(clean) < 10:
            team = pick.get("team", "")
            clean = f"Our model likes {team} in this matchup."
        elif not clean.endswith('.'):
            clean += '.'

        if clean:
            font_explain = _get_font(28)
            max_w = card_x2 - card_x1 - 2 * CARD_PADDING
            words = clean.split()
            lines = []
            current_line = ""
            for word in words:
                test = f"{current_line} {word}".strip()
                bbox = draw.textbbox((0, 0), test, font=font_explain)
                if bbox[2] - bbox[0] > max_w:
                    if current_line:
                        lines.append(current_line)
                    current_line = word
                else:
                    current_line = test
            if current_line:
                lines.append(current_line)
            lines = lines[:3]
            line_h = 38
            sy = card_y2 - CARD_PADDING - len(lines) * line_h
            draw.line([(cx, sy - 14), (card_x2 - CARD_PADDING, sy - 14)],
                      fill=CARD_BORDER, width=1)
            for line in lines:
                draw.text((cx, sy), line, font=font_explain, fill=TEXT_SECONDARY)
            sy += line_h

    return img


def _render_outro_frame(date: str, num_picks: int, progress: float) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT))
    _draw_gradient_bg(img)
    draw = ImageDraw.Draw(img)

    ease = _ease_out_cubic(min(progress * 1.5, 1.0))

    # Brand
    font_brand = _get_font(60, bold=True)
    text = "OZZY ANALYTICS"
    bbox = draw.textbbox((0, 0), text, font=font_brand)
    tw = bbox[2] - bbox[0]
    x = (WIDTH - tw) // 2
    y = 680
    draw.text((x, y), text, font=font_brand, fill=TEXT_PRIMARY)
    draw.rounded_rectangle(
        [x - 20, y + 78, x + tw + 20, y + 84], radius=3, fill=ACCENT
    )

    # CTA
    font_cta = _get_font(40)
    cta = "Full analysis + all picks at"
    if ease > 0.3:
        _centered_text(draw, y + 130, cta, font_cta, TEXT_SECONDARY)

    # URL
    font_url = _get_font(52, bold=True)
    url = "ozzyanalytics.com"
    bbox = draw.textbbox((0, 0), url, font=font_url)
    tw = bbox[2] - bbox[0]
    ux = (WIDTH - tw) // 2
    uy = y + 200
    if ease > 0.5:
        _draw_rounded_rect(
            draw, (ux - 30, uy - 14, ux + tw + 30, uy + 60),
            radius=16, fill=(20, 45, 85), outline=ACCENT,
        )
        draw.text((ux, uy), url, font=font_url, fill=TEXT_PRIMARY)

    # Follow CTA
    if ease > 0.7:
        font_follow = _get_font(34)
        follow = "Follow for daily MLB picks"
        _centered_text(draw, uy + 110, follow, font_follow, TEXT_MUTED)

    return img


def _ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def _fade_color(color: tuple, alpha: float) -> tuple:
    alpha = max(0.0, min(1.0, alpha))
    return tuple(int(c * alpha + BG_COLOR[i] * (1 - alpha)) for i, c in enumerate(color[:3]))


def _load_picks(json_path: str | Path) -> tuple[str, list[dict]]:
    with open(json_path) as f:
        data = json.load(f)
    date = data.get("date", "Unknown")
    picks = data.get("picks", [])
    seen = set()
    unique = []
    for p in picks:
        key = p.get("pick", "")
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return date, unique


def _pil_to_array(img: Image.Image):
    import numpy as np
    return np.array(img)


def generate_picks_video(json_path: str | Path, output_path: str | Path = None) -> Path:
    """
    Generate a TikTok vertical video from a daily picks JSON.

    Returns:
        Path to the generated video file.
    """
    from moviepy import ImageClip, concatenate_videoclips

    date, picks = _load_picks(json_path)

    if not picks:
        print(f"  No picks for {date}, skipping video generation.")
        return None

    output_path = Path(output_path) if output_path else Path(f"data/tiktok/{date}.mp4")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    yesterday, season = _load_results()

    print(f"  Generating TikTok video for {date} ({len(picks)} picks)...")

    clips = []

    # Intro: animate then hold
    intro_anim_frames = int(INTRO_DURATION * FPS)
    for i in range(intro_anim_frames):
        progress = i / intro_anim_frames
        frame = _pil_to_array(_render_intro_frame(date, len(picks), progress))
        clips.append(ImageClip(frame, duration=1 / FPS))
    clips.append(ImageClip(
        _pil_to_array(_render_intro_frame(date, len(picks), 1.0)), duration=0.5
    ))

    # Yesterday's recap (if results exist)
    if yesterday and yesterday.get("picks"):
        recap_anim_frames = int(RECAP_DURATION * FPS)
        for i in range(recap_anim_frames):
            progress = i / recap_anim_frames
            frame = _pil_to_array(_render_recap_frame(yesterday, progress))
            clips.append(ImageClip(frame, duration=1 / FPS))
        clips.append(ImageClip(
            _pil_to_array(_render_recap_frame(yesterday, 1.0)), duration=1.0
        ))

    # Season stats (if available)
    if season:
        season_anim_frames = int(SEASON_DURATION * FPS)
        for i in range(season_anim_frames):
            progress = i / season_anim_frames
            frame = _pil_to_array(_render_season_frame(season, progress))
            clips.append(ImageClip(frame, duration=1 / FPS))
        clips.append(ImageClip(
            _pil_to_array(_render_season_frame(season, 1.0)), duration=1.0
        ))

    # Pick cards: slower reveal + longer hold
    for idx, pick in enumerate(picks):
        reveal_frames = int(CARD_REVEAL_DURATION * FPS)
        for i in range(reveal_frames):
            progress = i / reveal_frames
            frame = _pil_to_array(_render_pick_card(pick, idx, len(picks), progress))
            clips.append(ImageClip(frame, duration=1 / FPS))
        clips.append(ImageClip(
            _pil_to_array(_render_pick_card(pick, idx, len(picks), 1.0)),
            duration=CARD_HOLD_DURATION,
        ))

    # Outro: animate then hold
    outro_anim_frames = int(OUTRO_DURATION * FPS)
    for i in range(outro_anim_frames):
        progress = i / outro_anim_frames
        frame = _pil_to_array(_render_outro_frame(date, len(picks), progress))
        clips.append(ImageClip(frame, duration=1 / FPS))
    clips.append(ImageClip(
        _pil_to_array(_render_outro_frame(date, len(picks), 1.0)), duration=1.5
    ))

    # Assemble
    final = concatenate_videoclips(clips, method="chain")
    final.write_videofile(
        str(output_path),
        fps=FPS,
        codec="libx264",
        audio=False,
        preset="medium",
        logger=None,
    )

    total_dur = final.duration
    print(f"  Video saved: {output_path} ({total_dur:.1f}s, {len(picks)} picks)")
    return output_path


def generate_from_latest() -> Path | None:
    daily_dir = Path("data/daily")
    jsons = sorted(daily_dir.glob("2*.json"), reverse=True)
    if not jsons:
        print("  No daily picks JSON found.")
        return None
    return generate_picks_video(jsons[0])


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = None

    if path:
        generate_picks_video(path)
    else:
        generate_from_latest()
