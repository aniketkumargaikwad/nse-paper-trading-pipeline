"""Sending the morning message, and surviving a channel that is down."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.notify import (  # noqa: E402
    NotifyResult,
    send_all,
    send_telegram,
    token_warning,
)


class FakePost:
    def __init__(self, status=200, boom=None):
        self.status, self.boom, self.calls = status, boom, []

    def __call__(self, url, json=None, timeout=None):
        self.calls.append((url, json, timeout))
        if self.boom:
            raise self.boom
        return type("Response", (), {"status_code": self.status, "text": "ok"})()


def test_the_bot_token_stays_out_of_the_message_body():
    post = FakePost()
    send_telegram("SECRET-TOKEN", "42", "hello", post=post)
    url, body, _ = post.calls[0]
    assert "SECRET-TOKEN" in url            # the API puts it in the path
    assert "SECRET-TOKEN" not in str(body)
    assert body["chat_id"] == "42" and body["text"] == "hello"


def test_a_telegram_failure_is_reported_not_raised():
    result = send_telegram("t", "42", "hello", post=FakePost(status=401))
    assert result.sent is False and "401" in result.detail


def test_a_network_error_is_reported_not_raised():
    post = FakePost(boom=OSError("no route to host"))
    result = send_telegram("t", "42", "hello", post=post)
    assert result.sent is False and "no route to host" in result.detail


def test_email_failing_does_not_stop_telegram():
    post = FakePost()

    def broken_email(*_args, **_kwargs):
        raise OSError("smtp refused")

    results = send_all(
        "hello", "subject", "body",
        telegram=("t", "42"), email=("user@example.test", "pw", "to@example.test"),
        post=post, send_mail=broken_email,
    )
    assert results["telegram"].sent is True
    assert results["email"].sent is False and "smtp refused" in results["email"].detail


def test_a_channel_with_no_credentials_is_skipped_not_failed():
    results = send_all("hello", "s", "b", telegram=None, email=None, post=FakePost())
    assert results["telegram"] == NotifyResult(sent=False, detail="not configured")
    assert results["email"] == NotifyResult(sent=False, detail="not configured")


@pytest.mark.parametrize("status", [200, 201])
def test_any_success_status_counts(status):
    assert send_telegram("t", "42", "hi", post=FakePost(status=status)).sent is True


# --- the Claude token running out of time ------------------------------------


def test_a_fresh_token_says_nothing():
    """A token issued this month has eleven more of them to run."""
    assert token_warning("2026-09-01", today=date(2026, 9, 13)) == ""


def test_a_token_inside_thirty_days_of_expiry_warns_with_the_date():
    warning = token_warning("2025-09-18", today=date(2026, 9, 13))
    assert "2026-09-18" in warning and "5 day" in warning


def test_an_expired_token_says_so_plainly():
    assert "expired" in token_warning("2025-01-01", today=date(2026, 9, 13)).lower()


def test_an_unset_or_unreadable_date_says_nothing():
    assert token_warning("", today=date(2026, 9, 13)) == ""
    assert token_warning("not a date", today=date(2026, 9, 13)) == ""
