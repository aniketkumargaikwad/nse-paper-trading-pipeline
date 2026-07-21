"""Tests for login.py's pure parsing logic (the 2FA flow itself is manual)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from login import extract_request_token  # noqa: E402


def test_full_redirect_url() -> None:
    url = (
        "https://example.com/callback?action=login&type=login"
        "&status=success&request_token=AbC123xyz"
    )
    assert extract_request_token(url) == "AbC123xyz"


def test_url_with_token_first_and_other_params_after() -> None:
    url = "http://127.0.0.1/?request_token=tok456&action=login"
    assert extract_request_token(url) == "tok456"


def test_bare_token() -> None:
    assert extract_request_token("  AbC123xyz  ") == "AbC123xyz"


def test_quoted_paste_is_cleaned() -> None:
    assert extract_request_token('"http://x.test/?request_token=tok789"') == "tok789"


def test_empty_paste_rejected() -> None:
    with pytest.raises(ValueError, match="Nothing was pasted"):
        extract_request_token("   ")


def test_url_without_token_rejected() -> None:
    with pytest.raises(ValueError, match="ENTIRE address"):
        extract_request_token("https://kite.zerodha.com/connect/login?v=3&api_key=abc")


def test_url_with_empty_token_value_rejected() -> None:
    with pytest.raises(ValueError, match="no value"):
        extract_request_token("http://x.test/?request_token=&action=login")


def test_garbage_with_spaces_rejected() -> None:
    with pytest.raises(ValueError):
        extract_request_token("some random words")
