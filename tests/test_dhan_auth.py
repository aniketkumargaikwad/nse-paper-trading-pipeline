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


# --- _token_from_response (pure, no network) --------------------------------


class FakeResponse:
    def __init__(self, status_code, payload=None, raise_json=False):
        self.status_code = status_code
        self._payload, self._raise = payload, raise_json

    def json(self):
        if self._raise:
            raise ValueError("no json")
        return self._payload


@pytest.mark.parametrize("key", ["accessToken", "access_token"])
def test_token_parsed_from_either_casing(key) -> None:
    m = manager(FakeTokenStore(None))
    token = m._token_from_response(FakeResponse(200, {key: "tok"}), "generating a token")
    assert token.access_token == "tok"
    assert token.expires_at == NOW + TOKEN_LIFETIME


def test_http_error_names_the_status_but_not_the_body() -> None:
    m = manager(FakeTokenStore(None))
    with pytest.raises(DhanAuthError, match="HTTP 401") as exc:
        m._token_from_response(FakeResponse(401, {"secret": "LEAKME"}), "generating a token")
    assert "LEAKME" not in str(exc.value)


def test_non_json_response_is_a_clear_auth_error() -> None:
    m = manager(FakeTokenStore(None))
    with pytest.raises(DhanAuthError, match="non-JSON"):
        m._token_from_response(FakeResponse(200, raise_json=True), "generating a token")


def test_missing_token_key_points_at_the_api_shape() -> None:
    m = manager(FakeTokenStore(None))
    with pytest.raises(DhanAuthError, match="no access token"):
        m._token_from_response(FakeResponse(200, {"status": "ok"}), "renewing the token")


def test_stored_token_never_appears_in_repr_or_str() -> None:
    """A live 24h bearer credential must not reach a log line or audit row."""
    token = StoredToken("eyJhbGciOiJIUzI1NiJ9.LIVE-BEARER", NOW)
    text = repr(token) + str(token) + f"{token}"
    assert "LIVE-BEARER" not in text
    assert "<redacted>" in text


def test_network_failure_becomes_a_clean_auth_error() -> None:
    """A requests exception must never escape: its .request.body holds the
    plaintext apiSecret and totp."""
    import requests as _requests

    m = DhanTokenManager(CREDS, FakeTokenStore(None), now_fn=lambda: NOW)

    def boom(*a, **kw):
        raise _requests.exceptions.ConnectionError("network down")

    monkey = _requests.post
    _requests.post = boom
    try:
        with pytest.raises(DhanAuthError) as exc:
            m._post("/GenerateToken", "generating a token", json={"apiSecret": "LEAKME"})
        assert "ConnectionError" in str(exc.value)
        assert "LEAKME" not in str(exc.value)
        assert exc.value.__cause__ is None      # original fully severed
        assert exc.value.__context__ is None
    finally:
        _requests.post = monkey


def test_totp_secret_with_spaces_is_accepted() -> None:
    """Dhan's enrolment screen shows the secret grouped in fours."""
    creds = DhanCredentials.from_env({
        "DHAN_CLIENT_ID": "c", "DHAN_API_KEY": "k",
        "DHAN_API_SECRET": "s", "DHAN_TOTP_SECRET": "JBSW Y3DP EHPK 3PXP",
    })
    assert creds.totp_secret == "JBSWY3DPEHPK3PXP"
