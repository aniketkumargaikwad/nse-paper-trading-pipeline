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
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Protocol

import requests

from config import UTC

DHAN_API_BASE = "https://api.dhan.co/v2"

# Token generation lives on a different host from the data APIs.
DHAN_AUTH_BASE = "https://auth.dhan.co"

GENERATE_TOKEN_PATH = "/app/generateAccessToken"
RENEW_TOKEN_PATH = "/v2/RenewToken"

# Renew when less than this remains, so a long backfill cannot have its token
# expire underneath it mid-run.
RENEW_MARGIN = timedelta(minutes=30)

TOKEN_LIFETIME = timedelta(hours=24)
# (connect, read). A scalar would apply 20s to EACH phase.
REQUEST_TIMEOUT_SECONDS = (5, 20)

REQUIRED_ENV_VARS = ("DHAN_CLIENT_ID", "DHAN_PIN", "DHAN_TOTP_SECRET")
OPTIONAL_ENV_VARS = ("DHAN_API_KEY", "DHAN_API_SECRET")


class DhanAuthError(RuntimeError):
    """Authentication failed. Message names what to check, never a secret."""


@dataclass(frozen=True)
class StoredToken:
    """An access token and when it stops being valid."""

    access_token: str
    expires_at: datetime

    def __repr__(self) -> str:
        # A live 24h bearer credential must never reach a log line, an audit
        # row, or an f-string. Same treatment as DhanCredentials.
        return f"StoredToken(access_token=<redacted>, expires_at={self.expires_at!r})"

    __str__ = __repr__


@dataclass(frozen=True)
class DhanCredentials:
    """Dhan API credentials.

    __repr__ and __str__ are overridden so the secrets cannot leak through an
    accidental log line, f-string, or exception message.
    """

    client_id: str
    api_key: str
    api_secret: str
    pin: str
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
                + ". DHAN_PIN is the Dhan account PIN used to log in at "
                "web.dhan.co. Locally: add them to .env. In GitHub Actions: "
                "add them as repository Secrets. Get the client id and TOTP "
                "secret from web.dhan.co -> Profile -> DhanHQ Trading APIs."
            )
        optional = {
            name: (source.get(name) or "").strip() for name in OPTIONAL_ENV_VARS
        }
        # Dhan displays the TOTP secret grouped in fours; strip internal
        # spaces so a natural copy-paste works. Base32 contains no spaces,
        # so this can never corrupt a valid secret.
        totp_secret = "".join(values["DHAN_TOTP_SECRET"].split())
        return cls(
            client_id=values["DHAN_CLIENT_ID"],
            api_key=optional["DHAN_API_KEY"],
            api_secret=optional["DHAN_API_SECRET"],
            pin=values["DHAN_PIN"],
            totp_secret=totp_secret,
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

        Renewal is lazy and the result is cached in the shared store, so a
        token is not minted on every run. This is NOT a distributed lock:
        two runs starting simultaneously can both mint, and the last write
        wins. Acceptable for a single-user system; if it ever matters, put
        both workflows in one GitHub `concurrency` group.
        """
        current = self._store.get_token(self.provider)
        if current and current.expires_at - self._now() > RENEW_MARGIN:
            return current.access_token

        if current:
            try:
                token = self._renew_token()
                self._store.save_token(self.provider, token)
                return token.access_token
            except DhanAuthError as exc:
                # Regeneration below will most likely succeed and hide this.
                # Surface it: persistent renewal failure means the endpoint or
                # its contract changed and needs a look.
                warnings.warn(
                    f"Dhan token renewal failed, regenerating via TOTP: {exc}",
                    RuntimeWarning,
                    stacklevel=2,
                )

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

    def invalidate(self) -> None:
        """Mark the cached token unusable so the next read mints a fresh one.

        Needed because expiry is not the only way a token dies. Dhan has been
        observed rejecting a token HOURS before its stated expiryTime, and the
        clock-based check in get_access_token cannot see that: it keeps
        handing back a token the server refuses, so every request 401s
        forever with no path to recovery.

        Implemented by back-dating the stored expiry rather than deleting the
        row, so the TokenStore protocol stays a two-method interface and a
        concurrent reader sees "expired" rather than "absent" — both lead to
        the same renew-or-regenerate path.
        """
        current = self._store.get_token(self.provider)
        if current is None:
            return
        self._store.save_token(
            self.provider,
            StoredToken(
                access_token=current.access_token,
                expires_at=self._now() - RENEW_MARGIN,
            ),
        )

    # -- network calls (patched wholesale in tests) --------------------------

    def _request(self, method: str, url: str, path: str, doing: str, **kwargs: Any) -> Any:
        """Call Dhan, converting transport failures into DhanAuthError.

        Reports only `path`, never `url`: the generation URL carries the PIN
        as a query parameter. A requests exception must not escape either -
        its .request attribute is the PreparedRequest, whose .url would hold
        that same PIN.
        """
        detail = None
        try:
            return requests.request(
                method, url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs
            )
        except requests.exceptions.RequestException as exc:
            detail = type(exc).__name__
        # Raised OUTSIDE the except block deliberately: `raise ... from None`
        # inside it would still leave the original reachable via __context__,
        # and with it the PIN-bearing URL.
        raise DhanAuthError(f"Could not reach Dhan while {doing} ({detail} on {path}).")

    def _renew_token(self) -> StoredToken:
        """Exchange the current token for a fresh 24h one.

        Only ACTIVE tokens can be renewed; an expired one errors, which is
        why get_access_token falls back to full generation.
        """
        current = self._store.get_token(self.provider)
        if current is None:
            raise DhanAuthError("no token to renew")
        response = self._request(
            "GET",
            DHAN_API_BASE + "/RenewToken",
            RENEW_TOKEN_PATH,
            "renewing the token",
            headers={
                "access-token": current.access_token,
                "dhanClientId": self._creds.client_id,
            },
        )
        return self._token_from_response(response, "renewing the token")

    def _generate_token(self) -> StoredToken:
        """Mint a brand-new token using the PIN and TOTP second factor.

        Dhan takes these as QUERY-STRING parameters, so they are passed via
        `params=` rather than interpolated into the URL - and the URL is never
        logged or included in an error, because it would carry the PIN.
        """
        import pyotp

        try:
            code = pyotp.TOTP(self._creds.totp_secret).now()
        except Exception:  # binascii.Error and friends - never echo the value
            raise DhanAuthError(
                "DHAN_TOTP_SECRET is not valid base32. Copy it exactly as shown "
                "at web.dhan.co -> Profile -> DhanHQ Trading APIs."
            ) from None

        response = self._request(
            "POST",
            DHAN_AUTH_BASE + GENERATE_TOKEN_PATH,
            GENERATE_TOKEN_PATH,
            "generating a token",
            params={
                "dhanClientId": self._creds.client_id,
                "pin": self._creds.pin,
                "totp": code,
            },
        )
        return self._token_from_response(response, "generating a token")

    def _token_from_response(self, response: Any, doing: str) -> StoredToken:
        """Parse a token response without ever echoing the token itself."""
        if response.status_code >= 400:
            raise DhanAuthError(
                f"Dhan rejected the request while {doing} "
                f"(HTTP {response.status_code}). Check DHAN_CLIENT_ID and "
                "DHAN_PIN, and the system clock - TOTP tolerates only ~30s "
                "of skew."
            )
        decoded = False
        try:
            payload = response.json()
            decoded = True
        except ValueError:
            pass
        if not decoded:
            raise DhanAuthError(f"Dhan returned a non-JSON response while {doing}")

        token = payload.get("accessToken") or payload.get("access_token")
        if not token:
            raise DhanAuthError(
                f"Dhan accepted the request while {doing} but returned no "
                "access token. The usual cause is a TOTP code Dhan has "
                "already consumed: it answers a reused code with HTTP 200 and "
                "an empty token rather than an error. Wait for the next "
                "30-second code and retry. If it persists across several "
                "fresh codes, check the system clock (TOTP tolerates ~30s of "
                "skew) and that API access is enabled at web.dhan.co."
            )
        return StoredToken(
            access_token=token,
            expires_at=self._parse_expiry(payload.get("expiryTime")),
        )

    def _parse_expiry(self, raw: Any) -> datetime:
        """Read the server's expiry, falling back to now + 24h.

        The exact format is not documented, so try ISO-8601 and epoch
        seconds, and never let a parse failure break authentication - a
        slightly-early renewal is harmless, a crash is not.
        """
        fallback = self._now() + TOKEN_LIFETIME
        if raw is None:
            return fallback
        if isinstance(raw, (int, float)):
            try:
                return datetime.fromtimestamp(float(raw), tz=UTC)
            except (OverflowError, OSError, ValueError):
                return fallback
        if isinstance(raw, str):
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return fallback
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        return fallback
