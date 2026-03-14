"""
Daily newsletter sender via Resend API.

Usage:
    from src.newsletter.sender import send_daily_picks
    send_daily_picks(picks_data)

Requires RESEND_API_KEY in .env.
Free tier: 100 emails/day, 3K/month.
"""

import json
import os
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = Path(__file__).parent / "templates"
SUBSCRIBERS_PATH = Path(__file__).parent.parent.parent / "data" / "subscribers.json"


def load_subscribers() -> list:
    """Load subscriber email list."""
    if not SUBSCRIBERS_PATH.exists():
        return []
    with open(SUBSCRIBERS_PATH) as f:
        data = json.load(f)
    return [s["email"] for s in data if s.get("active", True)]


def add_subscriber(email: str, name: str = ""):
    """Add a subscriber to the list."""
    subs = []
    if SUBSCRIBERS_PATH.exists():
        with open(SUBSCRIBERS_PATH) as f:
            subs = json.load(f)

    # Check for duplicates
    if any(s["email"] == email for s in subs):
        print(f"  {email} already subscribed")
        return

    subs.append({
        "email": email,
        "name": name,
        "active": True,
        "added": datetime.now().isoformat(),
    })

    SUBSCRIBERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SUBSCRIBERS_PATH, "w") as f:
        json.dump(subs, f, indent=2)
    print(f"  Added {email}")


def remove_subscriber(email: str):
    """Deactivate a subscriber."""
    if not SUBSCRIBERS_PATH.exists():
        return
    with open(SUBSCRIBERS_PATH) as f:
        subs = json.load(f)
    for s in subs:
        if s["email"] == email:
            s["active"] = False
    with open(SUBSCRIBERS_PATH, "w") as f:
        json.dump(subs, f, indent=2)
    print(f"  Deactivated {email}")


def render_email(picks_data: dict, season_stats: dict = None) -> str:
    """Render the daily picks email as HTML."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    template = env.get_template("daily_email.html")
    return template.render(
        today=picks_data,
        stats=season_stats or {},
        date=picks_data.get("date", datetime.now().strftime("%Y-%m-%d")),
    )


def send_daily_picks(picks_data: dict, season_stats: dict = None):
    """
    Send daily picks email to all active subscribers.

    picks_data: the daily JSON output from run_daily.py
    season_stats: optional season summary stats
    """
    try:
        import resend
    except ImportError:
        print("  ERROR: resend package not installed. Run: pip install resend")
        return

    api_key = os.getenv("RESEND_API_KEY", "")
    if not api_key:
        print("  ERROR: RESEND_API_KEY not set in .env")
        return

    resend.api_key = api_key
    subscribers = load_subscribers()
    if not subscribers:
        print("  No subscribers found.")
        return

    html = render_email(picks_data, season_stats)
    date = picks_data.get("date", datetime.now().strftime("%Y-%m-%d"))
    picks_count = len(picks_data.get("picks", []))
    subject = f"MLB Model Picks — {date} ({picks_count} pick{'s' if picks_count != 1 else ''})"

    if picks_count == 0:
        subject = f"MLB Model — {date} — No edges today"

    from_email = os.getenv("RESEND_FROM_EMAIL", "picks@yourdomain.com")

    print(f"  Sending to {len(subscribers)} subscribers...")
    for email in subscribers:
        try:
            resend.Emails.send({
                "from": from_email,
                "to": email,
                "subject": subject,
                "html": html,
            })
            print(f"    Sent to {email}")
        except Exception as e:
            print(f"    Failed to send to {email}: {e}")

    print(f"  Done. {len(subscribers)} emails sent.")
