"""Tests for unattended Dhan token renewal. No network, fake clock."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dhan_auth import (  # noqa: E402
    RENEW_MARGIN,
    TOKEN_LIFETIME,
    DhanAuthError,
    DhanCredentials,
    DhanTokenManager,
    StoredToken,
)

UTC = timezone.utc
NOW = datetime(2026, 8, 3, 6, 0, tzinfo=UTC)

CREDS = DhanCredentials(
    client_id="CID", api_key="KEY", api_secret="SECRET", totp_secret="JBSWY3DPEHPK3PXP"
)


class FakeTokenStore:
    def __init__(self, token: StoredToken | None = None):
        self.token = token
        self.saved: list[StoredToken] = []

    def get_token(self, provider: str) -> StoredToken | None:
        return self.token

    def save_token(self, provider: str, token: StoredToken) -> None:
        self.token = token
        self.saved.append(token)


def manager(store, *, renew=None, generate=None) -> DhanTokenManager:
    m = DhanTokenManager(CREDS, store, now_fn=lambda: NOW)
    if renew is not None:
        m._renew_token = renew          # type: ignore[assignment]
    if generate is not None:
        m._generate_token = generate    # type: ignore[assignment]
    return m


# --- token reuse and renewal ------------------------------------------------


def test_valid_token_is_reused_without_network() -> None:
    store = FakeTokenStore(StoredToken("good-token", NOW + timedelta(hours=8)))
    calls = []
    m = manager(store,
                renew=lambda: calls.append("renew") or StoredToken("x", NOW),
                generate=lambda: calls.append("gen") or StoredToken("y", NOW))
    assert m.get_access_token() == "good-token"
    assert calls == []          # no needless token churn


def test_token_near_expiry_is_renewed() -> None:
    # Inside the renew margin -> renew proactively rather than fail mid-run.
    store = FakeTokenStore(StoredToken("old", NOW + RENEW_MARGIN - timedelta(minutes=1)))
    renewed = StoredToken("renewed", NOW + TOKEN_LIFETIME)
    m = manager(store, renew=lambda: renewed,
                generate=lambda: pytest.fail("should not regenerate"))
    assert m.get_access_token() == "renewed"
    assert store.saved == [renewed]


def test_expired_token_is_renewed() -> None:
    store = FakeTokenStore(StoredToken("stale", NOW - timedelta(hours=1)))
    renewed = StoredToken("fresh", NOW + TOKEN_LIFETIME)
    m = manager(store, renew=lambda: renewed, generate=lambda: pytest.fail("no"))
    assert m.get_access_token() == "fresh"


def test_no_token_generates_via_totp() -> None:
    store = FakeTokenStore(None)
    generated = StoredToken("brand-new", NOW + TOKEN_LIFETIME)
    m = manager(store, renew=lambda: pytest.fail("nothing to renew"),
                generate=lambda: generated)
    assert m.get_access_token() == "brand-new"
    assert store.saved == [generated]


def test_renew_failure_falls_back_to_totp_generation() -> None:
    store = FakeTokenStore(StoredToken("stale", NOW - timedelta(hours=1)))
    generated = StoredToken("regenerated", NOW + TOKEN_LIFETIME)

    def failing_renew():
        raise DhanAuthError("renew rejected")

    m = manager(store, renew=failing_renew, generate=lambda: generated)
    assert m.get_access_token() == "regenerated"


def test_total_failure_message_names_the_variables_to_check() -> None:
    store = FakeTokenStore(None)

    def failing_generate():
        raise DhanAuthError("totp rejected")

    m = manager(store, renew=lambda: pytest.fail("no"), generate=failing_generate)
    with pytest.raises(DhanAuthError) as exc:
        m.get_access_token()
    assert "DHAN_TOTP_SECRET" in str(exc.value)


# --- secret hygiene ---------------------------------------------------------


def test_credentials_never_appear_in_repr_or_str() -> None:
    """A stray f-string in a log line must not leak the second factor."""
    text = repr(CREDS) + str(CREDS)
    assert "SECRET" not in text
    assert "JBSWY3DPEHPK3PXP" not in text
    assert "CID" in text          # the non-secret client id may show


def test_failure_message_never_contains_secret_values() -> None:
    store = FakeTokenStore(None)

    def failing_generate():
        raise DhanAuthError("totp rejected")

    m = manager(store, renew=lambda: pytest.fail("no"), generate=failing_generate)
    with pytest.raises(DhanAuthError) as exc:
        m.get_access_token()
    message = str(exc.value)
    assert "JBSWY3DPEHPK3PXP" not in message
    assert "SECRET" not in message.replace("DHAN_API_SECRET", "").replace(
        "DHAN_TOTP_SECRET", ""
    )


# --- credential loading -----------------------------------------------------


def test_missing_credentials_rejected_with_named_variables() -> None:
    with pytest.raises(DhanAuthError, match="DHAN_CLIENT_ID"):
        DhanCredentials.from_env({"DHAN_API_KEY": "k"})


def test_missing_credentials_names_every_absent_variable_at_once() -> None:
    with pytest.raises(DhanAuthError) as exc:
        DhanCredentials.from_env({})
    message = str(exc.value)
    for name in ("DHAN_CLIENT_ID", "DHAN_API_KEY", "DHAN_API_SECRET", "DHAN_TOTP_SECRET"):
        assert name in message


def test_whitespace_only_credential_treated_as_missing() -> None:
    with pytest.raises(DhanAuthError, match="DHAN_API_KEY"):
        DhanCredentials.from_env({
            "DHAN_CLIENT_ID": "c", "DHAN_API_KEY": "   ",
            "DHAN_API_SECRET": "s", "DHAN_TOTP_SECRET": "t",
        })


def test_credentials_load_from_a_complete_mapping() -> None:
    creds = DhanCredentials.from_env({
        "DHAN_CLIENT_ID": " c ", "DHAN_API_KEY": "k",
        "DHAN_API_SECRET": "s", "DHAN_TOTP_SECRET": "t",
    })
    assert creds.client_id == "c"     # trimmed
    assert creds.api_key == "k"


# --- renewal boundary -------------------------------------------------------


def test_token_expiring_exactly_at_the_margin_is_renewed() -> None:
    """Boundary: at exactly RENEW_MARGIN remaining, renew rather than risk a
    long backfill outliving its token."""
    store = FakeTokenStore(StoredToken("edge", NOW + RENEW_MARGIN))
    renewed = StoredToken("renewed", NOW + TOKEN_LIFETIME)
    m = manager(store, renew=lambda: renewed, generate=lambda: pytest.fail("no"))
    assert m.get_access_token() == "renewed"
