"""Central configuration for the paper-trading pipeline.

Every other module imports its settings from here so there is exactly ONE
place where environment variables are read and defaults live.

Design notes
------------
* Secrets come from environment variables only (GitHub Secrets in CI, a local
  `.env` file loaded by python-dotenv on your machine). Nothing is hardcoded.
* `get_settings()` fails LOUDLY with the names of any missing variables so a
  misconfigured run dies immediately instead of half-working.
* All timestamps in this system are stored timezone-aware in UTC; IST is used
  only for market-hours logic and dashboard display. The two ZoneInfo
  constants below are the single source of truth for that.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Load `.env` if present. This is a no-op in GitHub Actions, where the
# variables are injected directly into the environment from GitHub Secrets.
load_dotenv()

# ---------------------------------------------------------------------------
# Timezone and market-session constants
# ---------------------------------------------------------------------------

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

# NSE equity market hours (regular session). Stored as naive `time` objects
# that are always interpreted in IST by the market-calendar logic.
MARKET_OPEN_IST = time(9, 15)
MARKET_CLOSE_IST = time(15, 30)

# ---------------------------------------------------------------------------
# Timeframes
# ---------------------------------------------------------------------------
# The system supports 15-minute and higher ONLY (hard constraint: no tick/1m/5m
# logic anywhere). These two maps are the single source of truth; the strategy
# validator, the Kite client, and the engines all import from here.

# Our timeframe label -> Kite Connect historical API `interval` string.
# ASSUMPTION: Kite Connect v3 interval names ("15minute", "30minute",
# "60minute", "day"). Verify against current Kite docs if a fetch fails.
TIMEFRAME_TO_KITE_INTERVAL: dict[str, str] = {
    "15m": "15minute",
    "30m": "30minute",
    "60m": "60minute",
    "day": "day",
}

# Our timeframe label -> candle length in minutes ("day" uses the full NSE
# session length of 375 minutes: 09:15-15:30 IST).
TIMEFRAME_MINUTES: dict[str, int] = {
    "15m": 15,
    "30m": 30,
    "60m": 60,
    "day": 375,
}

SUPPORTED_TIMEFRAMES: tuple[str, ...] = tuple(TIMEFRAME_TO_KITE_INTERVAL)

# ---------------------------------------------------------------------------
# Simulated fill-realism defaults (used by BOTH backtester and paper engine)
# ---------------------------------------------------------------------------

DEFAULT_SLIPPAGE_PCT = 0.05        # 0.05% adverse move applied to every fill
DEFAULT_COST_PER_TRADE_INR = 30.0  # flat round-trip brokerage+taxes estimate

# Path (relative to repo root) of the strategy definitions file.
STRATEGIES_FILE = "strategies.yaml"

# ---------------------------------------------------------------------------
# Market-data provider
# ---------------------------------------------------------------------------
# Where candles come from. Both providers return the identical canonical
# DataFrame, so every other module is unaware of which one is in use.
#
#   yfinance (default) - FREE, no account, no API key, NO daily login.
#                        Limits: 15m/30m history only reaches back ~60 days,
#                        60m ~730 days, daily 5y+. Unofficial API: it can
#                        break without notice and data quality is below a
#                        broker feed. Good enough for research; that is the
#                        honest trade-off for zero cost.
#   kite               - Zerodha Kite Connect. Requires the PAID "Connect"
#                        plan (the free Personal tier excludes historical
#                        data) plus a daily 2FA login via login.py.
#
# Switch with DATA_PROVIDER=kite in .env / GitHub Secrets. No code changes.
DEFAULT_DATA_PROVIDER = "yfinance"
SUPPORTED_DATA_PROVIDERS: tuple[str, ...] = ("yfinance", "kite")

# Providers that need the daily Kite access token written by login.py.
PROVIDERS_REQUIRING_DAILY_LOGIN: frozenset[str] = frozenset({"kite"})


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


@dataclass(frozen=True)
class Settings:
    """Immutable bag of runtime settings.

    Frozen so no code path can mutate configuration mid-run — every run is
    stateless and should behave identically given the same environment.
    """

    supabase_url: str
    supabase_service_role_key: str
    data_provider: str = DEFAULT_DATA_PROVIDER
    # Kite credentials are only required when data_provider == 'kite'; they
    # stay empty strings on the free path so no Kite account is needed.
    kite_api_key: str = ""
    kite_api_secret: str = ""
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT
    cost_per_trade_inr: float = DEFAULT_COST_PER_TRADE_INR

    @property
    def requires_daily_login(self) -> bool:
        """True when a morning login.py run is needed for this provider."""
        return self.data_provider in PROVIDERS_REQUIRING_DAILY_LOGIN


def _read_float_env(name: str, default: float) -> float:
    """Read an optional float env var, failing with a clear message if junk."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(
            f"Environment variable {name}={raw!r} is not a valid number. "
            f"Fix it in your .env file (or GitHub Secrets) or remove it to "
            f"use the default of {default}."
        ) from exc
    if value < 0:
        raise ConfigError(f"Environment variable {name} must be >= 0, got {value}.")
    return value


def get_settings(require_supabase: bool = True) -> Settings:
    """Load and validate all settings from the environment.

    Kite credentials are required ONLY when DATA_PROVIDER=kite, so the free
    yfinance path needs no broker account at all.

    Args:
        require_supabase: set False for offline-capable commands that write
            no state (e.g. `backtest.py --no-db`, which only writes a CSV).
            That lets someone try the system with ZERO accounts.

    Raises:
        ConfigError: listing EVERY missing required variable at once (so the
            user fixes them in one pass instead of one failure at a time).
    """
    provider = os.environ.get("DATA_PROVIDER", "").strip().lower() or DEFAULT_DATA_PROVIDER
    if provider not in SUPPORTED_DATA_PROVIDERS:
        raise ConfigError(
            f"DATA_PROVIDER={provider!r} is not supported. "
            f"Use one of: {', '.join(SUPPORTED_DATA_PROVIDERS)}. "
            "Leave it unset for the free default ('yfinance')."
        )

    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    required: dict[str, str] = {}
    if require_supabase:
        required["SUPABASE_URL"] = supabase_url
        required["SUPABASE_SERVICE_ROLE_KEY"] = supabase_key

    kite_key = os.environ.get("KITE_API_KEY", "").strip()
    kite_secret = os.environ.get("KITE_API_SECRET", "").strip()
    if provider == "kite":
        required["KITE_API_KEY"] = kite_key
        required["KITE_API_SECRET"] = kite_secret

    missing = sorted(name for name, value in required.items() if not value)
    if missing:
        hint = (
            " (these are only needed because DATA_PROVIDER=kite; unset it to "
            "use the free 'yfinance' provider instead)"
            if provider == "kite" and any(n.startswith("KITE_") for n in missing)
            else ""
        )
        raise ConfigError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + hint
            + ". Locally: copy .env.example to .env and fill them in. "
            "In GitHub Actions: add them as repository Secrets."
        )

    return Settings(
        supabase_url=supabase_url,
        supabase_service_role_key=supabase_key,
        data_provider=provider,
        kite_api_key=kite_key,
        kite_api_secret=kite_secret,
        slippage_pct=_read_float_env("SLIPPAGE_PCT", DEFAULT_SLIPPAGE_PCT),
        cost_per_trade_inr=_read_float_env(
            "COST_PER_TRADE_INR", DEFAULT_COST_PER_TRADE_INR
        ),
    )
