"""LLM-generated pick narratives.

Takes a structured brief (matchup + Statcast rollups + edge math) and asks
Claude Haiku to write a 5-6 sentence snarky explanation. Falls back to the
rule-based explanation on any failure so the pipeline never breaks.
"""

from __future__ import annotations

import os
from typing import Any

from src.features.statcast_summary import get_rollups
from src.features.name_resolver import resolve_id

SYSTEM_PROMPT_SNARK = """You write pick explanations for @Ozzy_Analytics, a baseball betting site.

PRIME DIRECTIVE: narrative first, stats second. You are telling the story of why this pick is live, not reciting a box score. A reader should finish the paragraph understanding the *angle*, not drowning in numbers.

VOICE: sharp, dry wit, a little smug about the math. Think a great columnist who happens to love numbers. Never hype, never "locks," no emojis, no exclamation points, no em-dashes. Use commas, hyphens, or parentheses instead.

STRUCTURE (4-5 sentences, one flowing paragraph):
  1. Open with the thesis in plain English. NO numbers in sentence 1. Tell me what the market is getting wrong.
  2-3. Middle sentences support the thesis. This is where stats appear. Hard cap: 3 numbers TOTAL across the entire paragraph.
  4 (or 5). Close with a line that lands the pitch. Can be stat-free.

STAT RULES:
  - Hard cap: 3 numbers across the whole paragraph. No exceptions.
  - Pick stats that advance the story, not ones that pad it. "Devers has been crushing righties" + "a .391 xwOBA" beats dumping four stats about Devers.
  - Never stack 3+ numbers in a row. Never cite 2 stats on the same player.
  - Favor plain-English framing over percentages when possible ("the better team by a wide margin" instead of "141 Elo points").

VOICE EXEMPLARS (match this density):
  "Ohtani's ERA looks unhittable until you read the next column over, where the xERA is nearly a run higher and the regression is loaded. The Giants stack three right-handed bats who've been punishing this exact arm profile all year, and Mahle finally gets to pitch the kind of low-scoring game the park was built for. At +184, you're paying for a one-run fight that the board thinks is a blowout."

  "Clay Holmes walks too many people to be a chalk price against a better lineup, and Minnesota happens to be the better lineup tonight by a comfortable margin. Buxton and Bell are exactly the kind of right-handed bats that make command-dependent righties uncomfortable, and a plus-money line on a coin flip is the whole pitch. +135 does the rest of the talking."

DO NOT:
  - Use "our model" or "the model" more than once.
  - String more than two numbers in the same sentence.
  - Open with a stat. Sentence 1 is prose.
  - Use generic phrases: "value play", "straightforward spot", "fundamentally mispriced", "matchup-level edge"."""


SYSTEM_PROMPT_BERMAN = """You write pick explanations for @Ozzy_Analytics, a baseball betting site that runs 10,000-sim Monte Carlo models on every matchup. But you grew up on Chris Berman ESPN highlights and you can't quite shake it.

VOICE: sports-analytics-quant-meets-Chris-Berman. You know the numbers cold, but you let the prose swagger a little. One Berman-style riff per paragraph is the budget: a nickname wordplay on a player name ("Rafael 'Hand Me the' Devers", "Byron 'Paul' Buxton"), OR a "back, back, back" moment, OR an "all the way" callback. Pick one. Do not overuse. The rest of the paragraph is still grounded analysis.

Still no emojis, no exclamation points, no "locks," no em-dashes. Use commas, hyphens, or parentheses instead.

Tonal exemplar (what we're going for):
  - "Rafael 'You Could Look It Up' Devers is sitting on a .391 xwOBA vs right-handers, which is the kind of number that makes a plus-money price look like a typo."
  - "Tyler Mahle's been living a charmed life with a .241 BABIP-against, and BABIP regression doesn't care that the Giants are 'supposed' to lose this one. Back, back, back, to the mean."
  - "Matt 'I Can't Drive 55' Chapman is barreling balls at 13.8%. The sim noticed. The market didn't."

STRUCTURE: 5-6 sentences, flowing paragraph. Pick the 2-3 most interesting numbers from the brief. Do not cram.

MUST INCLUDE:
  - One regression or mispricing callout (xERA vs ERA, xwOBA vs wOBA, xBA vs BA, model % vs market %).
  - Exactly one Berman moment (nickname, "back back back", or "all the way").
  - A closing sentence that names why THIS pick at THIS price, today.

DO NOT:
  - Use "our model" or "the model" more than once per paragraph.
  - Invent numbers not in the brief. Skip missing stats silently.
  - Use generic phrases: "value play", "fundamentally mispriced", "straightforward spot"."""


SYSTEM_PROMPT = SYSTEM_PROMPT_SNARK  # back-compat default


def _pct(x) -> str | None:
    return f"{x*100:.1f}%" if isinstance(x, (int, float)) else None


def _fmt_hitter(h: dict, split: str = "all") -> str:
    s = h.get(split, {}) or {}
    parts = []
    if s.get("pa", 0) < 50:
        return ""
    if s.get("xwoba") is not None:
        parts.append(f"xwOBA {s['xwoba']:.3f}")
    if s.get("woba") is not None:
        parts.append(f"wOBA {s['woba']:.3f}")
    if s.get("barrel_pct") is not None:
        parts.append(f"barrel% {s['barrel_pct']*100:.1f}")
    if s.get("hard_hit_pct") is not None:
        parts.append(f"hard-hit% {s['hard_hit_pct']*100:.1f}")
    if s.get("k_pct") is not None:
        parts.append(f"K% {s['k_pct']*100:.1f}")
    return ", ".join(parts) + f" ({s['pa']} PA {split})"


def _fmt_pitcher(p: dict, split: str = "all") -> str:
    s = p.get(split, {}) or {}
    parts = []
    if s.get("bf", 0) < 50:
        return ""
    if s.get("xwoba_against") is not None:
        parts.append(f"xwOBA-against {s['xwoba_against']:.3f}")
    if s.get("woba_against") is not None:
        parts.append(f"wOBA-against {s['woba_against']:.3f}")
    if s.get("k_pct") is not None:
        parts.append(f"K% {s['k_pct']*100:.1f}")
    if s.get("bb_pct") is not None:
        parts.append(f"BB% {s['bb_pct']*100:.1f}")
    if s.get("barrel_against") is not None:
        parts.append(f"barrel%-against {s['barrel_against']*100:.1f}")
    if s.get("hard_hit_against") is not None:
        parts.append(f"hard-hit%-against {s['hard_hit_against']*100:.1f}")
    if s.get("whiff_pct") is not None:
        parts.append(f"whiff% {s['whiff_pct']*100:.1f}")
    if s.get("chase_pct") is not None:
        parts.append(f"chase% {s['chase_pct']*100:.1f}")
    return ", ".join(parts) + f" ({s['bf']} BF {split})"


def build_brief(pick: dict, game: dict, rollup_year: int = 2025) -> str:
    """Assemble a structured text brief for one pick."""
    hitters, pitchers = get_rollups(rollup_year)

    team = pick["team"]
    opp = pick["opponent"]
    side = pick["side"]
    is_home = side == "home"
    team_lineup = game["home_lineup_names"] if is_home else game["away_lineup_names"]
    opp_lineup = game["away_lineup_names"] if is_home else game["home_lineup_names"]
    team_sp = game["home_pitcher"] if is_home else game["away_pitcher"]
    opp_sp = game["away_pitcher"] if is_home else game["home_pitcher"]

    # Model vs market — use pick-level probabilities so run lines cite cover%,
    # not ML win%. pick["model_prob"] is win prob for ML and cover prob for RL.
    pick_type = pick.get("type", "moneyline")
    model_team = pick.get("model_prob")
    edge_pct = pick.get("edge_pct", 0)
    if model_team is None:
        model_team = game["model_home_wp"] if is_home else game["model_away_wp"]
        market_team = game["market_home_wp"] if is_home else game["market_away_wp"]
    else:
        market_team = model_team - edge_pct / 100

    prob_label = "cover" if pick_type == "run_line" else "win"

    lines = []
    lines.append(f"PICK: {pick['pick']} at odds {pick['odds']}")
    lines.append(f"TYPE: {pick_type}")
    lines.append(
        f"EDGE: model {model_team*100:.1f}% {prob_label} vs market {market_team*100:.1f}% "
        f"= {(model_team-market_team)*100:+.1f} pts"
    )
    lines.append(f"ELO: {team} {game.get('elo_home_rating' if is_home else 'elo_away_rating')} vs {opp} {game.get('elo_away_rating' if is_home else 'elo_home_rating')}")
    sim = game.get("sim_detail", {}) or {}
    team_r = sim.get("avg_home_runs" if is_home else "avg_away_runs")
    opp_r = sim.get("avg_away_runs" if is_home else "avg_home_runs")
    if team_r is not None and opp_r is not None:
        lines.append(f"SIM PROJECTED SCORE: {team} {team_r:.1f} - {opp} {opp_r:.1f}")

    # Park + weather
    pf = game.get("park_factors", {})
    if pf:
        lines.append(f"PARK FACTORS: runs {pf.get('runs',1):.2f}, HR {pf.get('HR',1):.2f}, BB {pf.get('bb',1):.2f}")
    weather = game.get("weather", {})
    if weather:
        w_parts = []
        if weather.get("temperature"):
            w_parts.append(f"{weather['temperature']}°F")
        if weather.get("wind_speed"):
            w_parts.append(f"wind {weather['wind_speed']} mph {weather.get('wind_direction','')}")
        if weather.get("condition"):
            w_parts.append(weather["condition"])
        if w_parts:
            lines.append(f"WEATHER: {', '.join(w_parts)}")

    # Starting pitchers
    opp_sp_id = resolve_id(opp_sp) if opp_sp else None
    team_sp_id = resolve_id(team_sp) if team_sp else None
    if opp_sp_id and opp_sp_id in pitchers:
        lines.append(f"OPPOSING STARTER ({opp_sp}): {_fmt_pitcher(pitchers[opp_sp_id], 'all')}")
        vsr = _fmt_pitcher(pitchers[opp_sp_id], "vsR")
        vsl = _fmt_pitcher(pitchers[opp_sp_id], "vsL")
        if vsr: lines.append(f"  vs RHB: {vsr}")
        if vsl: lines.append(f"  vs LHB: {vsl}")
    if team_sp_id and team_sp_id in pitchers:
        lines.append(f"OUR STARTER ({team_sp}): {_fmt_pitcher(pitchers[team_sp_id], 'all')}")

    # Top hitters for our side — determine handedness split by opposing starter
    opp_sp_throws = pitchers.get(opp_sp_id, {}).get("throws")  # may not exist
    # Fallback: infer from split PAs (if more data vs one hand, they're that hand)
    split = "vsR"  # default
    if opp_sp_id and opp_sp_id in pitchers:
        pdata = pitchers[opp_sp_id]
        # Use whichever split has more BF — that tells us what hand they face most
        # Not perfect but good enough for prototype
        # Actually, a pitcher's `p_throws` is in the data. We have stand distribution instead.
        # Just ask the LLM to intuit; the data below has both sides.
        pass

    lines.append(f"OUR HITTERS (top of {team} order):")
    for name in team_lineup[:4]:
        hid = resolve_id(name)
        if hid and hid in hitters:
            full = _fmt_hitter(hitters[hid], "all")
            vsr = _fmt_hitter(hitters[hid], "vsR")
            vsl = _fmt_hitter(hitters[hid], "vsL")
            line = f"  {name}: {full}"
            if vsr: line += f" | vsRHP: {vsr}"
            if vsl: line += f" | vsLHP: {vsl}"
            lines.append(line)

    lines.append(f"THEIR HITTERS (top of {opp} order):")
    for name in opp_lineup[:3]:
        hid = resolve_id(name)
        if hid and hid in hitters:
            full = _fmt_hitter(hitters[hid], "all")
            line = f"  {name}: {full}"
            lines.append(line)

    return "\n".join(lines)


SYSTEM_PROMPT_RECAP = """You write 2-3 sentence game recaps for @Ozzy_Analytics, covering how last night's picks cashed or died.

VOICE: sharp, dry, a little smug when we win, matter-of-fact when we lose. Jonah Keri meets Zach Lowe. Never hype, never "locks," no emojis, no exclamation points, no em-dashes. Use commas, hyphens, or parentheses.

Length: 2-3 sentences total, max 280 characters. Tight.
Cite specific moments from the brief (player names, innings, plays). Never invent details.
Do not restate the final score (the UI shows it). Do not use "our model" or "the model."

Exemplar voice for a WIN:
  "Rafael Devers put the thing on the scoreboard early with a 3-run shot in the 2nd, and Ohtani couldn't hold up after 6 shutout. The LAD bullpen did the rest. +184 turned into +31.6u."

Exemplar voice for a LOSS:
  "Bibee never settled in. Altuve took him deep in the 3rd, Houston tacked on two more in the 5th, and a quiet Cleveland offense did the rest. Some nights the chalk eats you."

If nothing interesting happened, lean on one sharp observation instead of forcing a highlight."""


SYSTEM_PROMPT_DAY_STORY = """You write the opening paragraph for @Ozzy_Analytics's daily newsletter, recapping how yesterday went.

VOICE: sharp, analytical, dry wit. A little smug when we win, composed when we lose. Never hype, never "locks," no emojis, no exclamation points, no em-dashes. Use commas, hyphens, or parentheses.

LENGTH: 4-6 sentences, one flowing paragraph. This is the first thing readers see, so voice matters more than comprehensiveness.

STRUCTURE:
  - Sentence 1: Set the frame for the day. W-L, profit in units, what kind of day it was.
  - Middle sentences: 1-2 specific moments (a pick that cashed hard, a pick that died, a swing game).
  - Closing sentence: Lands the story. Something about the math, the process, or the next day.

MUST:
  - Cite at least one specific pick and outcome by name.
  - Stay under 600 characters total.
  - If the day was great, avoid gloating. If the day was rough, avoid whining.

DO NOT:
  - List every pick (this is prose, not a scoreboard).
  - Use "our model" or "the model" more than once.
  - Use generic phrases: "variance giveth", "long season", "short memory".

Exemplar voice (green day):
  "Clean day at 4-1 for +47.3u, driven mostly by SFG +184 cashing when Ohtani got outpitched by Tyler Mahle of all people. The run line add-on on the same game turned a single edge into a double dip. MIN ML was the other that landed easy, Buxton and Bell doing exactly what the xwOBA splits said they would. Only stumble was CLE -138, which you pay -138 to live with. On to today."

Exemplar voice (red day):
  "Tough 1-3 for -24.1u, the kind of day where every close game went the wrong way. Bibee gave up a Yordan homer in the 3rd and Cleveland's offense never found a pulse, and MIN's plus-money edge died on a Juan Soto solo shot in the 8th. The one that cashed was SFG +1.5, which mostly softens the blow rather than changes the story. Edge stays real, variance does what variance does.\""""


VOICE_PROMPTS = {
    "snark": SYSTEM_PROMPT_SNARK,
    "berman": SYSTEM_PROMPT_BERMAN,
    "recap": SYSTEM_PROMPT_RECAP,
    "day_story": SYSTEM_PROMPT_DAY_STORY,
}


def generate_narrative(
    pick: dict,
    game: dict,
    rollup_year: int = 2025,
    model: str = "claude-opus-4-7",
    voice: str = "snark",
) -> str | None:
    """Return LLM-generated paragraph, or None on failure (caller falls back)."""
    try:
        import anthropic
    except ImportError:
        return None

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    brief = build_brief(pick, game, rollup_year=rollup_year)
    system = VOICE_PROMPTS.get(voice, SYSTEM_PROMPT_SNARK)

    client = anthropic.Anthropic(api_key=api_key)
    kwargs = dict(
        model=model,
        max_tokens=500,
        system=system,
        messages=[{
            "role": "user",
            "content": f"Write the pick explanation paragraph.\n\nBRIEF:\n{brief}",
        }],
    )
    # Opus 4.7 deprecated temperature; older models still accept it.
    if not model.startswith("claude-opus-4-7"):
        kwargs["temperature"] = 0.85

    try:
        resp = client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        return text or None
    except Exception as e:
        print(f"  narrative LLM error: {e}")
        return None


def _call_llm(system: str, user: str, model: str = "claude-opus-4-7", max_tokens: int = 400) -> str | None:
    """Shared LLM call with API-key + import + error guards."""
    try:
        import anthropic
    except ImportError:
        return None

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    client = anthropic.Anthropic(api_key=api_key)
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if not model.startswith("claude-opus-4-7"):
        kwargs["temperature"] = 0.85

    try:
        resp = client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        return text or None
    except Exception as e:
        print(f"  LLM error: {e}")
        return None


def generate_pick_recap(brief: str, model: str = "claude-opus-4-7") -> str | None:
    """Return a 2-3 sentence snark recap for a single pick. None on failure."""
    return _call_llm(SYSTEM_PROMPT_RECAP, f"Write the recap.\n\nBRIEF:\n{brief}", model=model, max_tokens=200)


def generate_day_story(brief: str, model: str = "claude-opus-4-7") -> str | None:
    """Return a 4-6 sentence opening story paragraph for yesterday. None on failure."""
    return _call_llm(SYSTEM_PROMPT_DAY_STORY, f"Write the opening paragraph.\n\nBRIEF:\n{brief}", model=model, max_tokens=350)
