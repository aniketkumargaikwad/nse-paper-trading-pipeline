"""Unattended Dhan access-token lifecycle.

Dhan tokens are valid 24 hours (mandatory since Oct 2025, driven by SEBI's
retail-algo framework). Rather than a daily manual login, this module renews
them automatically: TOTP -> access token, then /v2/RenewToken before expiry.

Security position
------------------
Automating this requires storing a TOTP secret, which is a second factor. The
mitigation is structural, not procedural: Dhan requires a WHITELISTED STATIC
IP for order placement, and this project never whitelists one. So even a
leaked token cannot place an order on the account.

Handling rules enforced here: secrets live only in environment variables, are
never logged or printed, never appear in a repr, and never appear in an
exception message. Errors name the ENV VAR to check, never its value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Protocol

import requests

from config import UTC

DHAN_API_BASE = "https://api.dhan.co/v2"

# Renew when less than this remains, so a long backfill cannot have its token
# expire underneath it mid-run.
RENEW_MARGIN = timedelta(minutes=30)

TOKEN_LIFETIME = timedelta(hours=24)
REQUEST_TIMEOUT_SECONDS = 20

REQUIRED_ENV_VARS = (
    "DHAN_CLIENT_ID", "DHAN_API_KEY", "DHAN_API_SECRET", "DHAN_TOTP_SECRET",
)


class DhanAuthError(RuntimeError):
    """Authentication failed. Message names what to check, never a secret."""


@dataclass(frozen=True)
class StoredToken:
    """An access token and when it stops being valid."""

    access_token: str
    expires_at: datetime


@dataclass(frozen=True)
class DhanCredentials:
    """Dhan API credentials.

    __repr__ and __str__ are overridden so the secrets cannot leak through an
    accidental log line, f-string, or exception message.
    """

    client_id: str
    api_key: str
    api_secret: str
    totp_secret: str

    def __repr__(self) -> str:
        return f"DhanCredentials(client_id={self.client_id!r}, secrets=<redacted>)"

    __str__ = __repr__

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DhanCredentials":
        """Build from environment variables, naming everything missing at once."""
        source = env if env is not None else os.environ
        values = {name: (source.get(name) or "").strip() for name in REQUIRED_ENV_VARS}
        missing = sorted(n for n, v in values.items() if not v)
        if missing:
            raise DhanAuthError(
                "Missing Dhan credential(s): " + ", ".join(missing)
                + ". Locally: add them to .env. In GitHub Actions: add them as "
                "repository Secrets. Get them from web.dhan.co -> Profile -> "
                "DhanHQ Trading APIs."
            )
        return cls(
            client_id=values["DHAN_CLIENT_ID"],
            api_key=values["DHAN_API_KEY"],
            api_secret=values["DHAN_API_SECRET"],
            totp_secret=values["DHAN_TOTP_SECRET"],
        )


class TokenStore(Protocol):
    """Where tokens are cached so concurrent runs share one.

    Implemented against Supabase in a later task; tested here with a fake.
    """

    def get_token(self, provider: str) -> StoredToken | None: ...
    def save_token(self, provider: str, token: StoredToken) -> None: ...


class DhanTokenManager:
    """Provides a valid access token, renewing it when necessary."""

    provider = "dhan"

    def __init__(
        self,
        credentials: DhanCredentials,
        store: TokenStore,
        now_fn: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        # now_fn is injectable so expiry logic is testable without waiting.
        self._creds = credentials
        self._store = store
        self._now = now_fn

    def get_access_token(self) -> str:
        """Return a token valid for at least RENEW_MARGIN.

        Renewal is lazy and the result is shared through the store, so
        concurrent runs do not each mint a token.
        """
        current = self._store.get_token(self.provider)
        if current and current.expires_at - self._now() > RENEW_MARGIN:
            return current.access_token

        if current:
            try:
                token = self._renew_token()
                self._store.save_token(self.provider, token)
                return token.access_token
            except DhanAuthError:
                pass  # fall through to a full regeneration

        try:
            token = self._generate_token()
        except DhanAuthError as exc:
            raise DhanAuthError(
                f"Could not obtain a Dhan access token: {exc}. Check "
                + ", ".join(REQUIRED_ENV_VARS)
                + ", and that API access is enabled at web.dhan.co -> Profile "
                "-> DhanHQ Trading APIs."
            ) from exc
        self._store.save_token(self.provider, token)
        return token.access_token

    # -- network calls (patched wholesale in tests) --------------------------

    def _renew_token(self) -> StoredToken:
        """Exchange the current token for a fresh 24h one."""
        current = self._store.get_token(self.provider)
        if current is None:
            raise DhanAuthError("no token to renew")
        response = requests.post(
            f"{DHAN_API_BASE}/RenewToken",
            headers={
                "access-token": current.access_token,
                "client-id": self._creds.client_id,
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        return self._token_from_response(response, "renewing the token")

    def _generate_token(self) -> StoredToken:
        """Mint a brand-new token using the TOTP second factor."""
        import pyotp

        code = pyotp.TOTP(self._creds.totp_secret).now()
        response = requests.post(
            f"{DHAN_API_BASE}/GenerateToken",
            json={
                "clientId": self._creds.client_id,
                "apiKey": self._creds.api_key,
                "apiSecret": self._creds.api_secret,
                "totp": code,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        return self._token_from_response(response, "generating a token")

    def _token_from_response(self, response: Any, doing: str) -> StoredToken:
        """Parse a token response without ever echoing the token itself."""
        if response.status_code >= 400:
            raise DhanAuthError(
                f"Dhan rejected the request while {doing} "
                f"(HTTP {response.status_code})."
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise DhanAuthError(
                f"Dhan returned a non-JSON response while {doing}"
            ) from exc

        token = payload.get("accessToken") or payload.get("access_token")
        if not token:
            raise DhanAuthError(
                f"Dhan response contained no access token while {doing}. "
                "The API shape may have changed; check the DhanHQ v2 auth docs."
            )
        return StoredToken(access_token=token, expires_at=self._now() + TOKEN_LIFETIME)
