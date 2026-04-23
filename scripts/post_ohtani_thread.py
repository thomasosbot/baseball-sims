"""One-off: post the Ohtani fade thread to @Ozzy_Analytics."""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import tweepy

TWEETS = [
    # 1 — hook
    "The best hitter in baseball was on the mound yesterday.\n\n"
    "6 shutout innings. 7 K. 0 walks.\n\n"
    "We bet against his team.\n\n"
    "We won.\n\n"
    "Here's what the market gets wrong about unicorns \U0001F9F5",

    # 2 — the tax
    "Ohtani-on-the-mound is the biggest name-tax in baseball.\n\n"
    "Dodgers closed −210 vs the Giants. Model said they should've been closer to −130.\n\n"
    "That 80-cent gap is the Shohei premium. Casuals hammer it. Books know. Line moves. You pay.\n\n"
    "We don't pay that tax.",

    # 3 — the math
    "A starting pitcher faces ~22 batters. Then he's gone.\n\n"
    "The other 15 outs? Bullpen.\n"
    "Plus 27 PAs from a lineup that still has to score runs.\n\n"
    "One elite arm can't carry a −210 price tag across 9 innings. The math doesn't care how cool the two-way thing is.",

    # 4 — receipts
    "Yesterday:\n\n"
    "Ohtani: 6 IP, 0 R, 7 K. Dominant.\n"
    "LAD bullpen: 3 ER in 1 inning.\n"
    "LAD lineup: 0 runs, shut out by Tyler Mahle.\n\n"
    "Model: SFG 43% | Market: SFG 35%\n"
    "7.9% edge. Quarter-Kelly fired it.\n\n"
    "SFG ML +184 → +31.6u\n"
    "SFG +1.5 +109 → +21.1u\n\n"
    "+52.7u on one game.",

    # 5 — close
    "The best pitcher on the field was a Dodger.\n"
    "The Dodgers lost 3-0.\n\n"
    "That's baseball. One man doesn't decide 76 plate appearances.\n\n"
    "We price the game. The market prices the jersey.\n\n"
    "Season: 83-62 | +228u | 22.8% ROI\n"
    "Every pick public before first pitch.\n\n"
    "ozzyanalytics.com",
]


def main():
    for i, t in enumerate(TWEETS, 1):
        if len(t) > 280:
            print(f"Tweet {i} is {len(t)} chars (>280). Aborting.")
            sys.exit(1)

    client = tweepy.Client(
        consumer_key=os.getenv("TWITTER_API_KEY"),
        consumer_secret=os.getenv("TWITTER_API_SECRET"),
        access_token=os.getenv("TWITTER_ACCESS_TOKEN"),
        access_token_secret=os.getenv("TWITTER_ACCESS_TOKEN_SECRET"),
    )

    prev_id = None
    for i, t in enumerate(TWEETS, 1):
        kwargs = {"text": t}
        if prev_id:
            kwargs["in_reply_to_tweet_id"] = prev_id
        resp = client.create_tweet(**kwargs)
        tid = resp.data["id"]
        print(f"  [{i}/{len(TWEETS)}] posted: https://x.com/Ozzy_Analytics/status/{tid}")
        prev_id = tid
        if i < len(TWEETS):
            time.sleep(2)

    print("\nDone.")


if __name__ == "__main__":
    main()
