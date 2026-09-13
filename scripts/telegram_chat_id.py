"""Find the chat id to send the morning research message to.

WHY THIS EXISTS
---------------
A bot cannot start a conversation, so its chat id only appears once you have
messaged it. The obvious way to read that is `getUpdates` — but updates are
delivered ONCE and then confirmed, so running it a moment too early returns
an empty list and the messages are gone. That is a confusing five minutes for
something that should take ten seconds.

This waits instead of racing: it long-polls, so you can start it first and
send the message afterwards.

    python scripts/telegram_chat_id.py

Needs TELEGRAM_BOT_TOKEN in .env (from @BotFather).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import use_utf8_stdout  # noqa: E402

API = "https://api.telegram.org/bot{token}/{method}"
POLL_SECONDS = 50


def main() -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait", type=int, default=120,
                        help="how long to wait for a message, in seconds")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN is not set. Put the token @BotFather gave you "
              "in .env first.", file=sys.stderr)
        return 1

    try:
        me = requests.get(API.format(token=token, method="getMe"), timeout=20).json()
    except Exception as exc:        # noqa: BLE001
        print(f"Could not reach Telegram: {exc}", file=sys.stderr)
        return 1
    if not me.get("ok"):
        print(f"Telegram rejected the token: {me.get('description')}", file=sys.stderr)
        return 1
    bot = me["result"]
    print(f"bot: {bot.get('first_name')} (@{bot.get('username')})")
    print(f"\nSend any message to @{bot.get('username')} now. Waiting up to "
          f"{args.wait}s...\n")

    waited = 0
    while waited < args.wait:
        try:
            response = requests.get(
                API.format(token=token, method="getUpdates"),
                params={"timeout": POLL_SECONDS, "limit": 10},
                timeout=POLL_SECONDS + 15,
            ).json()
        except Exception as exc:        # noqa: BLE001 - keep waiting
            print(f"  (retrying: {exc})")
            waited += 5
            continue

        for update in response.get("result", []):
            message = update.get("message") or update.get("edited_message") or {}
            chat = message.get("chat") or {}
            if chat.get("id") is not None:
                name = chat.get("first_name") or chat.get("title") or chat.get("username")
                print(f"Found it: {chat['id']}  ({name})")
                print("\nAdd this line to .env, and add the same value as the "
                      "GitHub secret TELEGRAM_CHAT_ID:\n")
                print(f"TELEGRAM_CHAT_ID={chat['id']}")
                return 0
        waited += POLL_SECONDS

    print("No message arrived. Open the chat with the bot and send anything, "
          "then run this again.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
