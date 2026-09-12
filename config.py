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
import sys
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
# Simulated fill-realism defaults (used by BOTH backtester and paper engine)
# ---------------------------------------------------------------------------

DEFAULT_SLIPPAGE_PCT = 0.05        # 0.05% adverse move applied to every fill
# Where candle bars are stored. 'supabase' keeps them as rows (the original
# behaviour); 'parquet' writes columnar files and is 5.6x smaller and ~34x
# faster to read, measured on real data. Metadata stays in Supabase either way.
# 'flat' charges a fixed rupee amount per round trip; 'itemised' models real
# Indian intraday charges, which scale with turnover; 'holding' is itemised
# plus delivery charges for any trade held overnight. Flat is the default so
# existing results stay reproducible - see costs.py for why flat is wrong at
# every trade size but one.
DEFAULT_COST_MODEL = "flat"

DEFAULT_CANDLE_STORE = "supabase"
# Local path or an fsspec URL - "s3://bucket/prefix" for Cloudflare R2 - so
# moving to object storage is configuration rather than code. A local path is
# fine on a laptop but NOT on Railway, whose filesystem is wiped on redeploy.
DEFAULT_CANDLE_ROOT = "data/candles"

DEFAULT_COST_PER_TRADE_INR = 30.0  # flat round-trip brokerage+taxes estimate

# Path (relative to repo root) of the strategy definitions file.
STRATEGIES_FILE = "strategies.yaml"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


# ---------------------------------------------------------------------------
# Timeframes
# ---------------------------------------------------------------------------
# These maps are the single source of truth; the strategy validator, the
# data providers, and the engines all import from here.

# Our timeframe label -> Kite Connect historical API `interval` string.
# ASSUMPTION: Kite Connect v3 interval names. Only used when DATA_PROVIDER=kite.
TIMEFRAME_TO_KITE_INTERVAL: dict[str, str] = {
    "5m": "5minute",
    "15m": "15minute",
    "30m": "30minute",
    "60m": "60minute",
    "day": "day",
}

# Our timeframe label -> candle length in minutes ("day" uses the full NSE
# session length of 375 minutes: 09:15-15:30 IST).
TIMEFRAME_MINUTES: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "25m": 25,
    "30m": 30,
    "60m": 60,
    "day": 375,
}

SUPPORTED_TIMEFRAMES: tuple[str, ...] = tuple(TIMEFRAME_MINUTES)

# ---------------------------------------------------------------------------
# Candle storage model
# ---------------------------------------------------------------------------
# Three timeframes are PERSISTED:
#   * BASE_TIMEFRAME ('5m') - 15m/25m/30m/60m are resampled from it, which
#     guarantees they are mutually consistent and lets us add new
#     timeframes without refetching anything.
#   * 'day' - stored separately because Dhan's daily feed is corporate-action
#     ADJUSTED and reaches back to inception, so it is both cleaner and far
#     longer than anything we could resample from intraday data.
#   * '1m' - stored separately because it CANNOT be derived: nothing can be
#     resampled down. It is deliberately NOT the base for the others.
#     Re-deriving 5m from 1m would be cleaner in the abstract and would
#     silently change every stored 5m candle and every result built on
#     them, so the two series are kept independent and each is fetched
#     from the provider on its own terms.
#
#     1m is roughly five times the rows of 5m for the same window, so it
#     is backfilled only for the symbols that actually need it rather than
#     for a whole universe by default.
BASE_TIMEFRAME = "5m"
MINUTE_TIMEFRAME = "1m"
STORED_TIMEFRAMES: tuple[str, ...] = (MINUTE_TIMEFRAME, BASE_TIMEFRAME, "day")



def use_utf8_stdout() -> None:
    """Let the CLIs print rupee signs on a console that cannot encode them.

    Windows consoles frequently default to cp1252, where a single Rs symbol in
    a progress line raises UnicodeEncodeError. That aborted a run that had
    already done all of its work - minutes of fetching and simulation thrown
    away at a print statement, with an error message that says nothing about
    the actual problem.

    Called explicitly by entry points rather than on import, so importing this
    module never reaches out and mutates global state.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                # A redirected or already-closed stream. Printing is not worth
                # failing over, which is the whole point of this function.
                pass


def source_timeframe_for(timeframe: str) -> str:
    """Which STORED timeframe must be read to serve `timeframe`.

    Intraday requests are served from the 5-minute base; daily is served
    directly. Anything else is rejected rather than silently approximated —
    a quietly-wrong timeframe would corrupt every result built on it.
    """
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ConfigError(
            f"Unsupported timeframe {timeframe!r}. "
            f"Allowed: {', '.join(SUPPORTED_TIMEFRAMES)}"
        )
    # 1m and day are served from their own stores; everything between
    # them is resampled from the 5m base.
    if timeframe in ("day", MINUTE_TIMEFRAME):
        return timeframe
    return BASE_TIMEFRAME


# ---------------------------------------------------------------------------
# Market-data provider
# ---------------------------------------------------------------------------
# Where candles come from. All providers return the identical canonical
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
#   dhan               - Dhan HQ Trading APIs. Account and AMC are free, but
#                        the Data APIs subscription costs ~Rs.499+GST/month
#                        (subscribe on the "Data APIs" tab at web.dhan.co ->
#                        Profile -> DhanHQ Trading APIs). Gives 5 years of
#                        5-minute intraday history plus a
#                        corporate-action-adjusted daily feed back to
#                        inception. No daily login: its 24h access token is
#                        renewed unattended via TOTP (see dhan_auth.py, a
#                        later task). Candles are cached in Supabase so
#                        Dhan's API is only ever hit for the incremental gap.
#
# Switch with DATA_PROVIDER=kite|dhan in .env / GitHub Secrets. No code changes.
DEFAULT_DATA_PROVIDER = "yfinance"
SUPPORTED_DATA_PROVIDERS: tuple[str, ...] = ("yfinance", "kite", "dhan")

# Providers needing an interactive morning login. Dhan is deliberately NOT
# here: it renews its 24h token unattended via TOTP (see dhan_auth.py).
PROVIDERS_REQUIRING_DAILY_LOGIN: frozenset[str] = frozenset({"kite"})


@dataclass(frozen=True)
class Settings:
    """Immutable bag of runtime settings.

    Frozen so no code path can mutate configuration mid-run — every run is
    stateless and should behave identically given the same environment.
    """

    supabase_url: str
    supabase_service_role_key: str
    data_provider: str = DEFAULT_DATA_PROVIDER
    cost_model: str = DEFAULT_COST_MODEL
    candle_store: str = DEFAULT_CANDLE_STORE
    candle_root: str = DEFAULT_CANDLE_ROOT
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
        cost_model=os.environ.get("COST_MODEL", DEFAULT_COST_MODEL).strip().lower(),
        candle_store=os.environ.get("CANDLE_STORE", DEFAULT_CANDLE_STORE).strip().lower(),
        candle_root=os.environ.get("CANDLE_ROOT", DEFAULT_CANDLE_ROOT).strip(),
        slippage_pct=_read_float_env("SLIPPAGE_PCT", DEFAULT_SLIPPAGE_PCT),
        cost_per_trade_inr=_read_float_env(
            "COST_PER_TRADE_INR", DEFAULT_COST_PER_TRADE_INR
        ),
    )
