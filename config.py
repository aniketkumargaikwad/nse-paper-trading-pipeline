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


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


@dataclass(frozen=True)
class Settings:
    """Immutable bag of runtime settings.

    Frozen so no code path can mutate configuration mid-run — every run is
    stateless and should behave identically given the same environment.
    """

    kite_api_key: str
    kite_api_secret: str
    supabase_url: str
    supabase_service_role_key: str
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT
    cost_per_trade_inr: float = DEFAULT_COST_PER_TRADE_INR


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


def get_settings() -> Settings:
    """Load and validate all settings from the environment.

    Raises:
        ConfigError: listing EVERY missing required variable at once (so the
            user fixes them in one pass instead of one failure at a time).
    """
    required = {
        "KITE_API_KEY": os.environ.get("KITE_API_KEY", "").strip(),
        "KITE_API_SECRET": os.environ.get("KITE_API_SECRET", "").strip(),
        "SUPABASE_URL": os.environ.get("SUPABASE_URL", "").strip(),
        "SUPABASE_SERVICE_ROLE_KEY": os.environ.get(
            "SUPABASE_SERVICE_ROLE_KEY", ""
        ).strip(),
    }
    missing = sorted(name for name, value in required.items() if not value)
    if missing:
        raise ConfigError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + ". Locally: copy .env.example to .env and fill them in. "
            "In GitHub Actions: add them as repository Secrets."
        )

    return Settings(
        kite_api_key=required["KITE_API_KEY"],
        kite_api_secret=required["KITE_API_SECRET"],
        supabase_url=required["SUPABASE_URL"],
        supabase_service_role_key=required["SUPABASE_SERVICE_ROLE_KEY"],
        slippage_pct=_read_float_env("SLIPPAGE_PCT", DEFAULT_SLIPPAGE_PCT),
        cost_per_trade_inr=_read_float_env(
            "COST_PER_TRADE_INR", DEFAULT_COST_PER_TRADE_INR
        ),
    )
