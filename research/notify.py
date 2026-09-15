"""Send the morning message. A channel that is down loses its message, not the day.

    python -m research.notify --run-id-file run-id.txt
    python -m research.notify                       # the newest run

Read from the database rather than handed the numbers, so it can be re-run
by hand after a delivery failure without re-running the day.
"""

from __future__ import annotations

import argparse
import os
import smtplib
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TIMEOUT_SECONDS = 30
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"


@dataclass(frozen=True)
class NotifyResult:
    sent: bool
    detail: str


def send_telegram(
    token: str, chat_id: str, text: str, *,
    parse_mode: str | None = "HTML",
    post: Callable[..., Any] = requests.post,
) -> NotifyResult:
    """One POST. The token travels in the URL path, never in the body.

    HTML mode is what puts the numbers in an aligned monospace block. Every
    value in that text is escaped by research.message; an unescaped '>' from
    a strategy title would make Telegram reject the whole message with a 400.
    """
    body = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if parse_mode:
        body["parse_mode"] = parse_mode
    try:
        response = post(
            TELEGRAM_URL.format(token=token),
            json=body,
            timeout=TIMEOUT_SECONDS,
        )
    except Exception as exc:        # noqa: BLE001 - a channel, not the day
        return NotifyResult(False, str(exc))
    status = getattr(response, "status_code", 0)
    if 200 <= status < 300:
        return NotifyResult(True, "sent")
    return NotifyResult(
        False, f"Telegram returned {status}: {getattr(response, 'text', '')[:200]}")


def send_email(
    user: str, password: str, to: str, subject: str, body: str,
    *, host: str = "smtp.gmail.com", port: int = 465,
) -> None:
    """Gmail SMTP with an app password. Raises; the caller decides what that means."""
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = user, to, subject
    message.set_content(body)
    with smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_SECONDS) as server:
        server.login(user, password)
        server.send_message(message)


def send_all(
    text: str,
    subject: str,
    body: str,
    *,
    telegram: tuple[str, ...] | None,
    email: tuple[str, ...] | None,
    post: Callable[..., Any] = requests.post,
    send_mail: Callable[..., Any] = send_email,
) -> dict[str, NotifyResult]:
    """Every configured channel, each one's failure kept to itself."""
    results = {"telegram": NotifyResult(False, "not configured"),
               "email": NotifyResult(False, "not configured")}
    if telegram:
        results["telegram"] = send_telegram(telegram[0], telegram[1], text, post=post)
    if email:
        try:
            send_mail(email[0], email[1], email[2], subject, body)
            results["email"] = NotifyResult(True, "sent")
        except Exception as exc:    # noqa: BLE001 - a channel, not the day
            results["email"] = NotifyResult(False, str(exc))
    return results


def _pair(*names: str) -> tuple[str, ...] | None:
    """Every one of these environment variables, or None if any is missing."""
    values = tuple((os.environ.get(name) or "").strip() for name in names)
    return values if all(values) else None


# `claude setup-token` issues a credential good for a year - measured from
# the real one on 2026-09-13, not assumed. The repository variable
# CLAUDE_TOKEN_CREATED holds the day it was made, because nothing else knows:
# from inside a run a dead token looks exactly like a usage limit, so without
# this every morning would quietly become stopped_limit.
#
# Set CLAUDE_TOKEN_LIFETIME_DAYS if a future token is issued for longer or
# shorter; guessing high would let one die silently.
TOKEN_LIFETIME_DAYS = int(os.environ.get("CLAUDE_TOKEN_LIFETIME_DAYS") or 365)
TOKEN_WARN_WITHIN_DAYS = 30


def token_warning(created: str, *, today: date | None = None) -> str:
    """A line for every message once the Claude token is nearly out of time."""
    try:
        made = date.fromisoformat((created or "").strip())
    except ValueError:
        return ""
    expires = made + timedelta(days=TOKEN_LIFETIME_DAYS)
    left = (expires - (today or date.today())).days
    if left < 0:
        return (f"⚠️ The Claude token expired on {expires}. "
                "Run `claude setup-token` and update the secret.")
    if left <= TOKEN_WARN_WITHIN_DAYS:
        return (f"⚠️ The Claude token expires on {expires} ({left} day(s)). "
                "Run `claude setup-token` and update the secret.")
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send the morning research message.")
    parser.add_argument("--run-id-file", default="",
                        help="a file holding the run id, written by run_day")
    parser.add_argument("--run-id", default="")
    args = parser.parse_args(argv)

    from config import get_settings, use_utf8_stdout
    from db import SupabaseStore
    from research.message import plain_text, telegram_html

    use_utf8_stdout()
    run_id = args.run_id
    if not run_id and args.run_id_file and Path(args.run_id_file).exists():
        run_id = Path(args.run_id_file).read_text(encoding="utf-8").strip()

    client = SupabaseStore.connect(get_settings(require_supabase=True))._client
    query = client.table("research_runs").select("*")
    query = query.eq("id", run_id) if run_id else query.order("started_at", desc=True)
    rows = query.limit(1).execute().data or []
    if not rows:
        # The day crashed before it stored anything. That is exactly when a
        # message matters most, so send one saying so.
        text = ("<b>\U0001f4ca Research run</b>\n⚠️ The run finished without storing "
                "a result. Check the workflow log.")
        body = ("\U0001f4ca Research run\n⚠️ The run finished without storing "
                "a result. Check the workflow log.")
        run = None
    else:
        run = rows[0]
        title = None
        if run.get("final_strategy_name"):
            found = (client.table("strategies").select("title")
                     .eq("name", run["final_strategy_name"]).execute().data or [])
            title = (found[0] if found else {}).get("title")
        # Every version the day tried, so a three-idea morning says what the
        # other two were rather than only naming the survivor.
        versions = (client.table("research_versions")
                    .select("idea_no,version_no,valid,decision,strategy_name,training_summary")
                    .eq("run_id", run["id"]).order("id").execute().data or [])
        link = (os.environ.get("DASHBOARD_URL") or "").strip() or None
        text = telegram_html(run, title=title, dashboard_url=link, versions=versions)
        body = plain_text(run, title=title, dashboard_url=link, versions=versions)

    warning = token_warning(os.environ.get("CLAUDE_TOKEN_CREATED", ""))
    if warning:
        text = f"{text}\n{warning}"

    results = send_all(
        text,
        subject=f"Research run — {(run or {}).get('started_at', '')}"[:120],
        body=body,
        telegram=_pair("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
        email=_pair("GMAIL_USER", "GMAIL_APP_PASSWORD", "NOTIFY_EMAIL"),
    )
    for channel, result in results.items():
        print(f"{channel:9s} {'sent' if result.sent else result.detail}")
    # A day that ran but could not be delivered is still a day. Only say
    # nothing worked when nothing worked.
    return 0 if any(r.sent for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
