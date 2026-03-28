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
ACCENT_GLOW = (24, 119, 242, 40)

# ── Timing (seconds) ──
INTRO_DURATION = 2.0
CARD_REVEAL_DURATION = 0.4
CARD_HOLD_DURATION = 2.0
CARD_TOTAL = CARD_REVEAL_DURATION + CARD_HOLD_DURATION
OUTRO_DURATION = 2.5

# ── Layout ──
CARD_MARGIN_X = 60
CARD_RADIUS = 24
CARD_PADDING = 36

# ── Fonts ──
# Try system fonts, fall back gracefully
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
    """Draw a rounded rectangle with optional outline."""
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=2)


def _draw_gradient_bg(img: Image.Image):
    """Draw a subtle vertical gradient background."""
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / HEIGHT
        r = int(BG_COLOR[0] + (BG_GRADIENT_END[0] - BG_COLOR[0]) * t)
        g = int(BG_COLOR[1] + (BG_GRADIENT_END[1] - BG_COLOR[1]) * t)
        b = int(BG_COLOR[2] + (BG_GRADIENT_END[2] - BG_COLOR[2]) * t)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))


def _render_intro_frame(date: str, num_picks: int, progress: float) -> Image.Image:
    """Render the intro/title frame. progress: 0.0 to 1.0 over INTRO_DURATION."""
    img = Image.new("RGB", (WIDTH, HEIGHT))
    _draw_gradient_bg(img)
    draw = ImageDraw.Draw(img)

    # Fade in via alpha simulation (just use progress for position)
    ease = _ease_out_cubic(min(progress * 1.5, 1.0))
    offset_y = int((1 - ease) * 60)

    # Brand name
    font_brand = _get_font(52, bold=True)
    text = "OZZY ANALYTICS"
    bbox = draw.textbbox((0, 0), text, font=font_brand)
    tw = bbox[2] - bbox[0]
    x = (WIDTH - tw) // 2
    y = 680 - offset_y
    # Accent underline
    draw.rounded_rectangle(
        [x - 20, y + 70, x + tw + 20, y + 76], radius=3, fill=ACCENT
    )
    draw.text((x, y), text, font=font_brand, fill=TEXT_PRIMARY)

    # Tagline
    font_tag = _get_font(36)
    tag = "10,000 Simulations. Zero Gut Feelings."
    bbox = draw.textbbox((0, 0), tag, font=font_tag)
    tw = bbox[2] - bbox[0]
    alpha = min(progress * 2 - 0.3, 1.0)
    if alpha > 0:
        color = _fade_color(TEXT_SECONDARY, alpha)
        draw.text(((WIDTH - tw) // 2, y + 100 - offset_y), tag, font=font_tag, fill=color)

    # Date + pick count
    font_date = _get_font(44, bold=True)
    date_text = f"Picks for {date}"
    bbox = draw.textbbox((0, 0), date_text, font=font_date)
    tw = bbox[2] - bbox[0]
    alpha2 = min(progress * 2 - 0.6, 1.0)
    if alpha2 > 0:
        color = _fade_color(TEXT_PRIMARY, alpha2)
        draw.text(((WIDTH - tw) // 2, y + 200 - offset_y), date_text, font=font_date, fill=color)

    # Pick count badge
    if alpha2 > 0:
        font_count = _get_font(30)
        count_text = f"{num_picks} pick{'s' if num_picks != 1 else ''} today"
        bbox = draw.textbbox((0, 0), count_text, font=font_count)
        tw = bbox[2] - bbox[0]
        cx = (WIDTH - tw) // 2
        cy = y + 270 - offset_y
        _draw_rounded_rect(
            draw,
            (cx - 24, cy - 8, cx + tw + 24, cy + 40),
            radius=20,
            fill=(30, 55, 100),
            outline=ACCENT,
        )
        draw.text((cx, cy), count_text, font=font_count, fill=TEXT_PRIMARY)

    return img


def _render_pick_card(
    pick: dict, card_index: int, total_cards: int, reveal_progress: float
) -> Image.Image:
    """Render a single pick card frame. reveal_progress: 0.0 (hidden) to 1.0 (full)."""
    img = Image.new("RGB", (WIDTH, HEIGHT))
    _draw_gradient_bg(img)
    draw = ImageDraw.Draw(img)

    ease = _ease_out_cubic(reveal_progress)

    # Card counter at top
    font_counter = _get_font(28)
    counter_text = f"PICK {card_index + 1} OF {total_cards}"
    bbox = draw.textbbox((0, 0), counter_text, font=font_counter)
    tw = bbox[2] - bbox[0]
    draw.text(((WIDTH - tw) // 2, 120), counter_text, font=font_counter, fill=TEXT_MUTED)

    # Main card - slides up and fades in
    card_x1 = CARD_MARGIN_X
    card_x2 = WIDTH - CARD_MARGIN_X
    card_y1 = 240 + int((1 - ease) * 80)
    card_h = 900
    card_y2 = card_y1 + card_h

    _draw_rounded_rect(
        draw, (card_x1, card_y1, card_x2, card_y2),
        radius=CARD_RADIUS, fill=CARD_BG, outline=CARD_BORDER,
    )

    if reveal_progress < 0.15:
        return img

    content_alpha = min((reveal_progress - 0.15) / 0.3, 1.0)
    cx = card_x1 + CARD_PADDING
    cy = card_y1 + CARD_PADDING

    # Pick type badge (ML or RL)
    pick_type = pick.get("type", "moneyline")
    badge_text = "MONEYLINE" if pick_type == "moneyline" else "RUN LINE"
    badge_color = ACCENT if pick_type == "moneyline" else GREEN
    font_badge = _get_font(22, bold=True)
    bbox = draw.textbbox((0, 0), badge_text, font=font_badge)
    bw = bbox[2] - bbox[0]
    # Darker fill so text pops
    badge_fill = (20, 45, 85) if pick_type == "moneyline" else (20, 60, 35)
    _draw_rounded_rect(
        draw, (cx, cy, cx + bw + 28, cy + 36),
        radius=18, fill=badge_fill, outline=badge_color,
    )
    draw.text((cx + 14, cy + 6), badge_text, font=font_badge, fill=TEXT_PRIMARY)

    # Team pick name (big)
    font_pick = _get_font(64, bold=True)
    pick_name = pick.get("pick", "???")
    color = _fade_color(TEXT_PRIMARY, content_alpha)
    draw.text((cx, cy + 70), pick_name, font=font_pick, fill=color)

    # Matchup line
    font_matchup = _get_font(32)
    team = pick.get("team", "")
    opponent = pick.get("opponent", "")
    side = pick.get("side", "")
    if side == "away":
        matchup = f"{team} @ {opponent}"
    else:
        matchup = f"{opponent} @ {team}"
    color = _fade_color(TEXT_SECONDARY, content_alpha)
    draw.text((cx, cy + 155), matchup, font=font_matchup, fill=color)

    # Divider line
    div_y = cy + 220
    draw.line([(cx, div_y), (card_x2 - CARD_PADDING, div_y)], fill=CARD_BORDER, width=2)

    # Stats grid
    stats_y = div_y + 30
    stats = [
        ("ODDS", pick.get("odds", "—")),
        ("MODEL WIN %", f"{pick.get('model_prob', 0):.0%}"),
        ("EDGE", f"+{pick.get('edge_pct', 0):.1f}%"),
    ]

    col_width = (card_x2 - card_x1 - 2 * CARD_PADDING) // len(stats)
    font_stat_label = _get_font(22)
    font_stat_value = _get_font(48, bold=True)

    for i, (label, value) in enumerate(stats):
        sx = cx + i * col_width
        # Stagger reveal
        stat_alpha = min((reveal_progress - 0.3 - i * 0.08) / 0.3, 1.0)
        if stat_alpha <= 0:
            continue

        label_color = _fade_color(TEXT_MUTED, stat_alpha)
        value_color = _fade_color(TEXT_PRIMARY, stat_alpha)
        if label == "EDGE":
            value_color = _fade_color(GREEN, stat_alpha)

        draw.text((sx, stats_y), label, font=font_stat_label, fill=label_color)
        draw.text((sx, stats_y + 35), str(value), font=font_stat_value, fill=value_color)

    # Sportsbook odds breakdown
    books = pick.get("sportsbook_odds", {})
    if books:
        book_y = stats_y + 140
        font_book_label = _get_font(20)
        font_book_val = _get_font(20, bold=True)
        draw.text((cx, book_y), "BEST AVAILABLE ODDS", font=font_book_label, fill=TEXT_MUTED)
        book_y += 35
        book_names = {
            "fanduel": "FanDuel",
            "bovada": "Bovada",
            "betmgm": "BetMGM",
            "draftkings": "DraftKings",
            "williamhill_us": "Caesars",
        }
        # Find best odds
        best_val = max(books.values()) if books else None
        for bk, val in books.items():
            name = book_names.get(bk, bk)
            is_best = val == best_val
            val_str = f"+{val}" if val > 0 else str(val)
            draw.text((cx, book_y), name, font=font_book_label, fill=TEXT_SECONDARY)
            v_color = GREEN if is_best else TEXT_SECONDARY
            draw.text((cx + 200, book_y), val_str, font=font_book_val, fill=v_color)
            if is_best:
                # Best tag
                draw.text((cx + 280, book_y), "BEST", font=_get_font(16, bold=True), fill=GREEN)
            book_y += 32

    # Lineup status pill at bottom of card
    status = pick.get("lineup_status", "projected")
    status_color = GREEN if status == "confirmed" else (200, 160, 50)
    font_status = _get_font(20)
    status_text = f"Lineups {status.upper()}"
    bbox = draw.textbbox((0, 0), status_text, font=font_status)
    sw = bbox[2] - bbox[0]
    sx = (WIDTH - sw) // 2
    sy = card_y2 - 55
    draw.text((sx, sy), status_text, font=font_status, fill=status_color)

    return img


def _render_outro_frame(date: str, num_picks: int, progress: float) -> Image.Image:
    """Render the outro/CTA frame."""
    img = Image.new("RGB", (WIDTH, HEIGHT))
    _draw_gradient_bg(img)
    draw = ImageDraw.Draw(img)

    ease = _ease_out_cubic(min(progress * 1.5, 1.0))

    # Brand
    font_brand = _get_font(48, bold=True)
    text = "OZZY ANALYTICS"
    bbox = draw.textbbox((0, 0), text, font=font_brand)
    tw = bbox[2] - bbox[0]
    x = (WIDTH - tw) // 2
    y = 700
    draw.text((x, y), text, font=font_brand, fill=TEXT_PRIMARY)
    draw.rounded_rectangle(
        [x - 20, y + 65, x + tw + 20, y + 71], radius=3, fill=ACCENT
    )

    # CTA
    font_cta = _get_font(36)
    cta = "Full analysis + all picks at"
    bbox = draw.textbbox((0, 0), cta, font=font_cta)
    tw = bbox[2] - bbox[0]
    if ease > 0.3:
        draw.text(((WIDTH - tw) // 2, y + 110), cta, font=font_cta, fill=TEXT_SECONDARY)

    # URL
    font_url = _get_font(44, bold=True)
    url = "ozzyanalytics.com"
    bbox = draw.textbbox((0, 0), url, font=font_url)
    tw = bbox[2] - bbox[0]
    ux = (WIDTH - tw) // 2
    uy = y + 170
    if ease > 0.5:
        # Accent box around URL — solid dark fill so text is readable
        _draw_rounded_rect(
            draw, (ux - 30, uy - 12, ux + tw + 30, uy + 52),
            radius=16, fill=(20, 45, 85), outline=ACCENT,
        )
        draw.text((ux, uy), url, font=font_url, fill=TEXT_PRIMARY)

    # Follow CTA
    if ease > 0.7:
        font_follow = _get_font(28)
        follow = "Follow for daily MLB picks"
        bbox = draw.textbbox((0, 0), follow, font=font_follow)
        tw = bbox[2] - bbox[0]
        draw.text(((WIDTH - tw) // 2, uy + 90), follow, font=font_follow, fill=TEXT_MUTED)

    return img


def _ease_out_cubic(t: float) -> float:
    """Ease-out cubic for smooth animations."""
    return 1 - (1 - t) ** 3


def _fade_color(color: tuple, alpha: float) -> tuple:
    """Simulate fade by interpolating color toward background."""
    alpha = max(0.0, min(1.0, alpha))
    return tuple(int(c * alpha + BG_COLOR[i] * (1 - alpha)) for i, c in enumerate(color[:3]))


def _load_picks(json_path: str | Path) -> tuple[str, list[dict]]:
    """Load picks from a daily JSON file. Returns (date, picks)."""
    with open(json_path) as f:
        data = json.load(f)
    date = data.get("date", "Unknown")
    picks = data.get("picks", [])
    # Deduplicate picks (JSON sometimes has dupes from early+late)
    seen = set()
    unique = []
    for p in picks:
        key = p.get("pick", "")
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return date, unique


def _pil_to_array(img: Image.Image):
    """Convert PIL Image to numpy array for MoviePy."""
    import numpy as np
    return np.array(img)


def generate_picks_video(json_path: str | Path, output_path: str | Path = None) -> Path:
    """
    Generate a TikTok vertical video from a daily picks JSON.

    Args:
        json_path: Path to daily picks JSON (e.g., data/daily/2026-03-27.json)
        output_path: Output MP4 path. Defaults to data/tiktok/<date>.mp4

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

    print(f"  Generating TikTok video for {date} ({len(picks)} picks)...")

    clips = []

    # Intro: render animated frames, then hold final frame
    intro_anim_frames = int(INTRO_DURATION * FPS)
    for i in range(intro_anim_frames):
        progress = i / intro_anim_frames
        frame = _pil_to_array(_render_intro_frame(date, len(picks), progress))
        clips.append(ImageClip(frame, duration=1 / FPS))
    # Hold final intro frame
    final_intro = _pil_to_array(_render_intro_frame(date, len(picks), 1.0))
    clips.append(ImageClip(final_intro, duration=0.5))

    # Pick cards: animate reveal, then hold
    for idx, pick in enumerate(picks):
        reveal_frames = int(CARD_REVEAL_DURATION * FPS)
        for i in range(reveal_frames):
            progress = i / reveal_frames
            frame = _pil_to_array(_render_pick_card(pick, idx, len(picks), progress))
            clips.append(ImageClip(frame, duration=1 / FPS))
        # Hold fully revealed card
        final_card = _pil_to_array(_render_pick_card(pick, idx, len(picks), 1.0))
        clips.append(ImageClip(final_card, duration=CARD_HOLD_DURATION))

    # Outro: animate in, then hold
    outro_anim_frames = int(OUTRO_DURATION * FPS)
    for i in range(outro_anim_frames):
        progress = i / outro_anim_frames
        frame = _pil_to_array(_render_outro_frame(date, len(picks), progress))
        clips.append(ImageClip(frame, duration=1 / FPS))
    final_outro = _pil_to_array(_render_outro_frame(date, len(picks), 1.0))
    clips.append(ImageClip(final_outro, duration=1.0))

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

    duration = INTRO_DURATION + len(picks) * CARD_TOTAL + OUTRO_DURATION
    print(f"  Video saved: {output_path} ({duration:.1f}s, {len(picks)} picks)")
    return output_path


def generate_from_latest() -> Path | None:
    """Generate video from the most recent daily picks JSON."""
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
