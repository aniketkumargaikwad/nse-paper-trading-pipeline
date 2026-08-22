"""Config additions for Phase 0: 5m/25m timeframes and the Dhan provider."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402


def test_five_and_twentyfive_minute_timeframes_supported() -> None:
    assert "5m" in config.SUPPORTED_TIMEFRAMES
    assert "25m" in config.SUPPORTED_TIMEFRAMES
    assert config.TIMEFRAME_MINUTES["5m"] == 5
    assert config.TIMEFRAME_MINUTES["25m"] == 25


def test_base_timeframe_is_five_minutes() -> None:
    assert config.BASE_TIMEFRAME == "5m"


def test_only_underivable_timeframes_are_stored() -> None:
    """15m/25m/30m/60m come from the 5m base, so storing them would be
    duplication that can drift. 1m and day cannot be derived at all —
    nothing resamples downward, and the daily feed is adjusted."""
    assert set(config.STORED_TIMEFRAMES) == {"1m", "5m", "day"}


def test_one_minute_is_served_from_its_own_store() -> None:
    """NOT from the 5m base: a finer timeframe cannot be resampled out of
    a coarser one, and quietly serving 5m bars under a 1m label would be
    wrong in a way nothing downstream could detect."""
    assert config.source_timeframe_for("1m") == "1m"


def test_derived_timeframes_map_to_the_base() -> None:
    for tf in ("15m", "25m", "30m", "60m"):
        assert config.source_timeframe_for(tf) == "5m"
    assert config.source_timeframe_for("5m") == "5m"
    assert config.source_timeframe_for("day") == "day"


def test_unknown_timeframe_rejected() -> None:
    with pytest.raises(config.ConfigError, match="Unsupported timeframe"):
        config.source_timeframe_for("3m")


def test_dhan_is_a_supported_provider_needing_no_daily_login() -> None:
    # Dhan renews its token unattended via TOTP, so no morning ritual.
    assert "dhan" in config.SUPPORTED_DATA_PROVIDERS
    assert "dhan" not in config.PROVIDERS_REQUIRING_DAILY_LOGIN
