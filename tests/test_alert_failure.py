"""The crash alert (scripts/alert_failure.py): says which job failed, never fails the job, never leaks the token."""

from __future__ import annotations

import importlib.util
import json
import urllib.error
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "alert_failure", Path(__file__).resolve().parent.parent / "scripts" / "alert_failure.py")
af = importlib.util.module_from_spec(spec)
spec.loader.exec_module(af)

ENV = {"TELEGRAM_BOT_TOKEN": "SECRET-TOKEN", "TELEGRAM_CHAT_ID": "42", "GITHUB_SERVER_URL": "https://github.com",
       "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "7"}


class Reply:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_the_message_names_the_job_and_links_the_run():
    text = af.text("stock research", ENV)
    assert "The stock research job FAILED" in text and "https://github.com/o/r/actions/runs/7" in text
    assert "actions/runs" not in af.text("x", {})          # run by hand: no link to give


def test_it_posts_to_the_chat():
    sent = {}

    def post(request, timeout):
        sent["url"], sent["body"] = request.full_url, json.loads(request.data)
        return Reply()

    assert af.send("hello", ENV, post=post) is True
    assert sent["url"].endswith("/botSECRET-TOKEN/sendMessage") and sent["body"]["chat_id"] == "42"
    assert sent["body"]["text"] == "hello"


def test_without_the_secrets_it_says_so_and_sends_nothing(capsys):
    assert af.send("hello", {}, post=lambda *a, **k: 1 / 0) is False
    assert "not set" in capsys.readouterr().err


def test_a_telegram_failure_never_fails_the_job_or_prints_the_token(monkeypatch, capsys):
    def post(request, timeout):
        raise urllib.error.URLError("https://api.telegram.org/botSECRET-TOKEN/sendMessage refused")

    assert af.send("hello", ENV, post=post) is False
    assert "SECRET-TOKEN" not in capsys.readouterr().err
    monkeypatch.setattr(af, "send", lambda *a, **k: False)
    assert af.main(["paper books"]) == 0
