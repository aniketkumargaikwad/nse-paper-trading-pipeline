"""Update the momentum paper account (research/momentum_paper.py). NO real money.

    python scripts/momentum_paper.py                 # fetch NSE levels, write the book
    python scripts/momentum_paper.py --notify        # ...and message on a new month's first session

Writes research/journal/momentum/paper.json, which the dashboard's Momentum
page reads. Run every weekday evening by .github/workflows/momentum-paper.yml.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import use_utf8_stdout  # noqa: E402
from nse_index_history import fetch  # noqa: E402
from research import momentum_paper as mp  # noqa: E402

OUT = Path("research/journal/momentum/paper.json")


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args(argv)
    # A year before the start is enough for the 200-session average.
    tri = fetch(mp.START.year - 1, {mp.MOMENTUM: "NIFTY200MOMENTM30", mp.NIFTY50: "NIFTY 50"})
    b = mp.book(tri)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes((json.dumps(b, indent=1) + "\n").encode("utf-8"))
    text = mp.message(b)
    print(text)
    if args.notify and mp.month_turned(tri):
        from research.notify import send_all
        pair = lambda *n: (tuple(os.environ.get(x, "").strip() for x in n)            # noqa: E731
                           if all(os.environ.get(x, "").strip() for x in n) else None)
        results = send_all(text, f"Momentum paper account {b['as_of']}", text,
                           telegram=pair("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
                           email=pair("GMAIL_USER", "GMAIL_APP_PASSWORD", "NOTIFY_EMAIL"))
        for channel, r in results.items():
            print(f"{channel:9s} {'sent' if r.sent else r.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
