"""Tell the owner on Telegram that a scheduled job FAILED.

    python scripts/alert_failure.py "stock research"

Run as the last step of every scheduled workflow, `if: failure()`. From 24 Oct
to 7 Nov 2026 every research day crashed and the only sign was GitHub's own
failure email: no research message means "nothing passed" and "nothing ran"
look the same from a phone. This makes a crash say so, the same hour.

Standard library only, on purpose: the step that failed may have been
`pip install`, and an alert that needs the packages would fail with it. It
never fails the job itself - there is nothing more to report by then.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def text(job: str, env: dict[str, str]) -> str:
    server, repo, run = (env.get(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    link = f"{server}/{repo}/actions/runs/{run}" if server and repo and run else ""
    return (f"⚠️ The {job} job FAILED on GitHub.\n"
            "Nothing it was due to do today can be trusted as done: no result, no saved notes, "
            "no paper-book update from this run."
            + (f"\nWhat went wrong: {link}" if link else ""))


def send(message: str, env: dict[str, str], post=urllib.request.urlopen) -> bool:
    token, chat = (env.get("TELEGRAM_BOT_TOKEN") or "").strip(), (env.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        print("alert not sent: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set", file=sys.stderr)
        return False
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat, "text": message, "disable_web_page_preview": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with post(request, timeout=30) as reply:
            return 200 <= reply.status < 300
    except (urllib.error.URLError, OSError) as exc:
        # Never echo the URL: it carries the bot token.
        print(f"alert not sent: {type(exc).__name__}", file=sys.stderr)
        return False


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    job = args[0] if args else "scheduled"
    print("alert sent" if send(text(job, dict(os.environ)), dict(os.environ)) else "alert not sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
