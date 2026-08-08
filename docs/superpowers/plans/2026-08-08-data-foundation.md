# Precise Data Foundation (Phase 0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 58-day yfinance data limit with a local Supabase candle store fed by Dhan, giving 5 years of precise 5-minute history that backtests can read at any hour, on weekends and holidays.

**Architecture:** A `CandleProvider` protocol isolates data sources. `candle_store.py` is the single repository the rest of the platform imports — it tracks what is cached in `candle_coverage`, fetches only missing ranges, and never claims coverage it does not have. A 5-minute base is stored and higher intraday timeframes are derived by session-anchored resampling; daily is stored separately because Dhan's daily feed is corporate-action adjusted.

**Tech Stack:** Python 3.11, pandas 2.2.3, Supabase (Postgres), `requests` for the Dhan REST API, `pyotp` for unattended TOTP token renewal, pytest.

**Spec:** `docs/superpowers/specs/2026-08-08-data-foundation-design.md`

---

## Conventions (follow these in every file)

- Start every module with `from __future__ import annotations`.
- Module docstring: one-line purpose, then a `Responsibilities` or equivalent block. Explain **why**, not just what.
- Section dividers: `# ---------------------------------------------------------------------------`
- Full type hints on every function.
- Any sleeping/backoff takes an injectable `sleep_fn: Callable[[float], None] = time.sleep` so tests run instantly (see `kite_client.MarketDataClient.__init__`).
- Tests start with:
  ```python
  import sys
  from pathlib import Path
  sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
  ```
- Run tests: `.\.venv\Scripts\python.exe -m pytest tests/ -q`
- Never log, print, or store a secret. Not in errors, not in audit rows, not in the UI.

## File Structure

| File | Responsibility |
|---|---|
| `sql/002_data_foundation.sql` | Migration: 6 new tables + RLS |
| `config.py` *(modify)* | Add `5m`/`25m` timeframes, `dhan` provider, Dhan env vars |
| `resample.py` *(new)* | Pure: session-anchored 5m → 15/25/30/60m |
| `data_quality.py` *(new)* | Pure: split / gap / OHLC-sanity detection |
| `providers/__init__.py` *(new)* | Package marker |
| `providers/base.py` *(new)* | `CandleProvider` protocol + canonical frame helper |
| `dhan_auth.py` *(new)* | TOTP → token → auto-renew, cached in Supabase |
| `instruments.py` *(new)* | Dhan security master ↔ `NSE:RELIANCE` |
| `providers/dhan.py` *(new)* | Dhan REST adapter: 90-day paging, backoff |
| `candle_store.py` *(new)* | `ensure_coverage()` / `get_candles()` — the repository |
| `backfill.py` *(new)* | CLI to warm the cache |
| `data_provider.py` *(modify)* | Register the `dhan` provider |
| `requirements.txt` *(modify)* | `pyotp`, `requests` |
| `.env.example` *(modify)* | Dhan credentials |

Pure modules (`resample`, `data_quality`, coverage maths) come first so the risky I/O work builds on tested foundations.

---

### Task 1: Database migration

**Files:**
- Create: `sql/002_data_foundation.sql`

- [ ] **Step 1: Write the migration file**

Create `sql/002_data_foundation.sql`:

```sql
-- ============================================================================
-- 002_data_foundation.sql — Phase 0: precise candle storage.
--
-- HOW TO RUN: Supabase dashboard -> SQL Editor -> New query -> paste -> Run.
-- Safe to re-run: every statement is idempotent.
--
-- Additive only. Nothing here touches the Phase-1 tables (strategies,
-- positions, trades, run_audit, backtest_results).
-- ============================================================================

begin;

-- Symbol master, mapping our 'NSE:RELIANCE' notation to Dhan identifiers.
create table if not exists instruments (
    id               bigserial primary key,
    symbol           text not null,
    exchange         text not null,
    tradingsymbol    text not null,
    dhan_security_id text not null,
    dhan_segment     text not null,
    name             text,
    instrument_type  text not null default 'EQUITY',
    lot_size         integer,
    is_active        boolean not null default true,
    refreshed_on     date not null,
    constraint instruments_symbol_key unique (symbol)
);

comment on table instruments is
    'Symbol master: our EXCHANGE:SYMBOL notation mapped to Dhan security IDs.';

-- Named universes (Nifty 50/100, custom). Populated in Phase 1.
create table if not exists symbol_groups (
    id          bigserial primary key,
    name        text not null unique,
    description text,
    is_system   boolean not null default false,
    created_at  timestamptz not null default now()
);

create table if not exists symbol_group_members (
    group_id      bigint not null references symbol_groups(id) on delete cascade,
    instrument_id bigint not null references instruments(id)   on delete cascade,
    primary key (group_id, instrument_id)
);

-- The candle store. numeric (not float) so P&L maths cannot drift.
create table if not exists candles (
    instrument_id bigint        not null references instruments(id) on delete cascade,
    timeframe     text          not null check (timeframe in ('5m', 'day')),
    ts            timestamptz   not null,
    open          numeric(14,4) not null,
    high          numeric(14,4) not null,
    low           numeric(14,4) not null,
    close         numeric(14,4) not null,
    volume        bigint        not null,
    primary key (instrument_id, timeframe, ts)
);

comment on table candles is
    'Stored candles. Only the 5m base and adjusted daily are stored; 15/25/30/60m are resampled on read.';

-- What is ACTUALLY cached. The guard against claiming coverage we lack.
create table if not exists candle_coverage (
    instrument_id     bigint      not null references instruments(id) on delete cascade,
    timeframe         text        not null,
    first_ts          timestamptz not null,
    last_ts           timestamptz not null,
    source            text        not null,
    last_refreshed_at timestamptz not null default now(),
    primary key (instrument_id, timeframe),
    constraint candle_coverage_range_ok check (first_ts <= last_ts)
);

comment on table candle_coverage is
    'One contiguous cached range per (instrument, timeframe). Never advanced past genuinely fetched data.';

-- Detected problems, surfaced for review - never silently corrected.
create table if not exists data_quality_flags (
    id            bigserial primary key,
    instrument_id bigint not null references instruments(id) on delete cascade,
    timeframe     text   not null,
    flag_type     text   not null
        check (flag_type in ('suspected_split', 'session_gap', 'ohlc_invalid')),
    ts            timestamptz not null,
    detail        jsonb  not null,
    resolved      boolean not null default false,
    created_at    timestamptz not null default now()
);

-- Provider credentials with expiry. NEVER exposed to the dashboard.
create table if not exists provider_tokens (
    provider     text primary key,
    access_token text not null,
    expires_at   timestamptz not null,
    updated_at   timestamptz not null default now()
);

create index if not exists candles_instrument_tf_ts_idx
    on candles (instrument_id, timeframe, ts desc);
create index if not exists quality_unresolved_idx
    on data_quality_flags (instrument_id) where not resolved;

-- ============================================================================
-- Row Level Security
-- ============================================================================
alter table instruments          enable row level security;
alter table symbol_groups        enable row level security;
alter table symbol_group_members enable row level security;
alter table candles              enable row level security;
alter table candle_coverage      enable row level security;
alter table data_quality_flags   enable row level security;
alter table provider_tokens      enable row level security;

drop policy if exists "anon read instruments"          on instruments;
drop policy if exists "anon read symbol_groups"        on symbol_groups;
drop policy if exists "anon read symbol_group_members" on symbol_group_members;
drop policy if exists "anon read candles"              on candles;
drop policy if exists "anon read candle_coverage"      on candle_coverage;
drop policy if exists "anon read quality_flags"        on data_quality_flags;

create policy "anon read instruments"          on instruments          for select to anon using (true);
create policy "anon read symbol_groups"        on symbol_groups        for select to anon using (true);
create policy "anon read symbol_group_members" on symbol_group_members for select to anon using (true);
create policy "anon read candles"              on candles              for select to anon using (true);
create policy "anon read candle_coverage"      on candle_coverage      for select to anon using (true);
create policy "anon read quality_flags"        on data_quality_flags   for select to anon using (true);

-- provider_tokens gets NO anon policy: it holds a live API credential.
-- With RLS on and no policy, the dashboard's key sees zero rows.

commit;
```

- [ ] **Step 2: Apply the migration**

Apply it using the Supabase MCP `apply_migration` tool with name `002_data_foundation`, or paste the file into the Supabase SQL Editor and Run.

- [ ] **Step 3: Verify every table exists**

Run the Supabase MCP `list_tables` tool for schema `public`.
Expected: the seven pre-existing tables plus `instruments`, `symbol_groups`, `symbol_group_members`, `candles`, `candle_coverage`, `data_quality_flags`, `provider_tokens`.

- [ ] **Step 4: Verify the token table is not anon-readable**

Run the Supabase MCP `execute_sql` tool:

```sql
select tablename, policyname from pg_policies
where schemaname = 'public' and tablename = 'provider_tokens';
```

Expected: **zero rows** (RLS on, no policy → invisible to the anon key).

- [ ] **Step 5: Commit**

```bash
git add sql/002_data_foundation.sql
git commit -m "feat(db): add Phase 0 data-foundation tables"
```

---

### Task 2: Config — new timeframes and the Dhan provider

**Files:**
- Modify: `config.py`
- Modify: `requirements.txt`
- Modify: `.env.example`
- Test: `tests/test_config_dhan.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_dhan.py`:

```python
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


def test_stored_timeframes_are_base_and_day_only() -> None:
    # Everything else is derived by resampling, so only these are persisted.
    assert set(config.STORED_TIMEFRAMES) == {"5m", "day"}


def test_derived_timeframes_map_to_the_base() -> None:
    for tf in ("15m", "25m", "30m", "60m"):
        assert config.source_timeframe_for(tf) == "5m"
    assert config.source_timeframe_for("5m") == "5m"
    assert config.source_timeframe_for("day") == "day"


def test_unknown_timeframe_rejected() -> None:
    with pytest.raises(config.ConfigError, match="Unsupported timeframe"):
        config.source_timeframe_for("1m")


def test_dhan_is_a_supported_provider_needing_no_daily_login() -> None:
    # Dhan renews its token unattended via TOTP, so no morning ritual.
    assert "dhan" in config.SUPPORTED_DATA_PROVIDERS
    assert "dhan" not in config.PROVIDERS_REQUIRING_DAILY_LOGIN
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_config_dhan.py -q`
Expected: FAIL — `AttributeError: module 'config' has no attribute 'BASE_TIMEFRAME'`

- [ ] **Step 3: Update the timeframe constants in `config.py`**

Replace the `TIMEFRAME_TO_KITE_INTERVAL` / `TIMEFRAME_MINUTES` / `SUPPORTED_TIMEFRAMES` block (around lines 48-68) with:

```python
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
# Only two timeframes are ever PERSISTED:
#   * BASE_TIMEFRAME ('5m') - every intraday timeframe is resampled from it,
#     which guarantees they are mutually consistent and lets us add new
#     timeframes without refetching anything.
#   * 'day' - stored separately because Dhan's daily feed is corporate-action
#     ADJUSTED and reaches back to inception, so it is both cleaner and far
#     longer than anything we could resample from intraday data.
BASE_TIMEFRAME = "5m"
STORED_TIMEFRAMES: tuple[str, ...] = (BASE_TIMEFRAME, "day")
```

- [ ] **Step 4: Add the timeframe resolver to `config.py`**

Add immediately after the block from Step 3:

```python
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
    return "day" if timeframe == "day" else BASE_TIMEFRAME
```

Note: `source_timeframe_for` references `ConfigError`, which is defined lower in the file. Move the `class ConfigError(RuntimeError)` definition **above** this function (just after the `STRATEGIES_FILE` constant) so it is defined before use at import time.

- [ ] **Step 5: Register the Dhan provider in `config.py`**

Change the provider constants to:

```python
DEFAULT_DATA_PROVIDER = "yfinance"
SUPPORTED_DATA_PROVIDERS: tuple[str, ...] = ("yfinance", "kite", "dhan")

# Providers needing an interactive morning login. Dhan is deliberately NOT
# here: it renews its 24h token unattended via TOTP (see dhan_auth.py).
PROVIDERS_REQUIRING_DAILY_LOGIN: frozenset[str] = frozenset({"kite"})
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_config_dhan.py -q`
Expected: PASS (6 passed)

- [ ] **Step 7: Run the whole suite for regressions**

Run: `.\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all pass. The `5m`/`25m` additions widen `SUPPORTED_TIMEFRAMES`, so if `tests/test_strategy_schema.py::test_bad_timeframe_rejected` used `"5m"` as its invalid example, change that test to use `"1m"` instead:

```python
def test_bad_timeframe_rejected() -> None:
    doc = valid_doc()
    doc["strategies"][0]["timeframe"] = "1m"  # faster than the 5m base is forbidden
    expect_error(doc, "unsupported timeframe '1m'")
```

- [ ] **Step 8: Add dependencies to `requirements.txt`**

Add under the market-data providers section:

```
# TOTP generation for unattended Dhan token renewal (no daily login).
pyotp==2.9.0

# Direct REST calls to Dhan. Pinned explicitly rather than relying on it
# arriving transitively via other packages.
requests==2.32.3
```

- [ ] **Step 9: Install the new dependencies**

Run: `.\.venv\Scripts\python.exe -m pip install pyotp==2.9.0 requests==2.32.3`
Expected: both install successfully.

- [ ] **Step 10: Document the Dhan variables in `.env.example`**

Add after the existing provider block:

```
# --- Dhan (ONLY needed when DATA_PROVIDER=dhan) ------------------------------
# Free: no API fee, no AMC, no account-opening fee. Gives 5 years of intraday
# history. Get these from web.dhan.co -> Profile -> DhanHQ Trading APIs.
#
# DHAN_TOTP_SECRET is a SECOND FACTOR. Treat it like a password: it lives only
# in .env (git-ignored) or GitHub Secrets, and is never logged or displayed.
# Note: order placement additionally requires a whitelisted static IP, which
# this project never configures - so these credentials cannot trade.
#DHAN_CLIENT_ID=your_dhan_client_id
#DHAN_API_KEY=your_dhan_api_key
#DHAN_API_SECRET=your_dhan_api_secret
#DHAN_TOTP_SECRET=your_base32_totp_secret
```

- [ ] **Step 11: Commit**

```bash
git add config.py requirements.txt .env.example tests/test_config_dhan.py tests/test_strategy_schema.py
git commit -m "feat(config): add 5m/25m timeframes, stored-vs-derived model, dhan provider"
```

---

### Task 3: Session-anchored resampling

**Files:**
- Create: `resample.py`
- Test: `tests/test_resample.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_resample.py`:

```python
"""Tests for session-anchored resampling of the 5-minute base."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from resample import ResampleError, resample_candles  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def five_min_frame(rows: list[tuple], start_ist: datetime | None = None) -> pd.DataFrame:
    """rows = [(open, high, low, close, volume)], 5-minute spacing from 09:15 IST."""
    start = start_ist or datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    index = pd.DatetimeIndex(
        [(start + timedelta(minutes=5 * i)).astimezone(UTC) for i in range(len(rows))],
        name="ts",
    )
    arr = np.asarray(rows, dtype=float)
    return pd.DataFrame(
        {"open": arr[:, 0], "high": arr[:, 1], "low": arr[:, 2],
         "close": arr[:, 3], "volume": arr[:, 4]},
        index=index,
    )


def test_three_five_minute_candles_become_one_fifteen_minute_candle() -> None:
    df = five_min_frame([
        (100, 105, 99, 104, 10),    # 09:15
        (104, 108, 103, 107, 20),   # 09:20
        (107, 109, 101, 102, 30),   # 09:25
    ])
    out = resample_candles(df, "15m")
    assert len(out) == 1
    row = out.iloc[0]
    assert row["open"] == 100      # first
    assert row["high"] == 109      # max
    assert row["low"] == 99        # min
    assert row["close"] == 102     # last
    assert row["volume"] == 60     # sum
    # The bucket is stamped with the SESSION-anchored start, 09:15 IST.
    assert out.index[0] == datetime(2026, 8, 3, 9, 15, tzinfo=IST).astimezone(UTC)


def test_first_bucket_anchors_to_0915_not_to_the_clock() -> None:
    """The Yahoo bug we must not reproduce: a 30m bucket starting 09:00 would
    contain only 09:15-09:30, i.e. 15 minutes of data in a 30-minute bar."""
    df = five_min_frame([(100, 101, 99, 100, 1)] * 12)  # 09:15 -> 10:10
    out = resample_candles(df, "30m")
    first, second = out.index[0], out.index[1]
    assert first == datetime(2026, 8, 3, 9, 15, tzinfo=IST).astimezone(UTC)
    assert second == datetime(2026, 8, 3, 9, 45, tzinfo=IST).astimezone(UTC)


def test_partial_trailing_bucket_is_kept() -> None:
    # 4 candles into a 15m grouping: one full bucket + one partial.
    df = five_min_frame([(100, 101, 99, 100, 1)] * 4)
    out = resample_candles(df, "15m")
    assert len(out) == 2
    assert out.iloc[-1]["volume"] == 1  # the lone trailing candle


def test_buckets_never_span_two_sessions() -> None:
    day1 = five_min_frame([(100, 101, 99, 100, 1)] * 2,
                          start_ist=datetime(2026, 8, 3, 15, 20, tzinfo=IST))
    day2 = five_min_frame([(200, 201, 199, 200, 1)] * 2,
                          start_ist=datetime(2026, 8, 4, 9, 15, tzinfo=IST))
    out = resample_candles(pd.concat([day1, day2]), "60m")
    assert len(out) == 2                    # one bucket per session, not merged
    assert out.iloc[0]["close"] == 100
    assert out.iloc[1]["open"] == 200


def test_empty_bucket_produces_no_row() -> None:
    # A gap (lunch halt / missing data) must not create a synthesised candle.
    early = five_min_frame([(100, 101, 99, 100, 1)] * 2)
    late = five_min_frame([(120, 121, 119, 120, 1)] * 2,
                          start_ist=datetime(2026, 8, 3, 11, 15, tzinfo=IST))
    out = resample_candles(pd.concat([early, late]), "15m")
    assert len(out) == 2
    assert not out.isna().any().any()


def test_resampling_to_the_base_is_a_passthrough() -> None:
    df = five_min_frame([(100, 101, 99, 100, 1)] * 3)
    pd.testing.assert_frame_equal(resample_candles(df, "5m"), df)


def test_empty_input_gives_empty_canonical_frame() -> None:
    out = resample_candles(five_min_frame([]), "15m")
    assert out.empty
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]


def test_non_multiple_target_rejected() -> None:
    df = five_min_frame([(100, 101, 99, 100, 1)] * 3)
    with pytest.raises(ResampleError, match="multiple of"):
        resample_candles(df, "day")


def test_naive_index_rejected() -> None:
    df = five_min_frame([(100, 101, 99, 100, 1)] * 3)
    df.index = df.index.tz_localize(None)
    with pytest.raises(ResampleError, match="tz-aware"):
        resample_candles(df, "15m")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_resample.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'resample'`

- [ ] **Step 3: Write the implementation**

Create `resample.py`:

```python
"""Pure, session-anchored resampling of the 5-minute base timeframe.

Higher intraday timeframes are DERIVED rather than stored, which guarantees
they are mutually consistent and lets new timeframes be added without
refetching anything.

Why session-anchored and not clock-anchored
-------------------------------------------
Buckets are measured from the 09:15 IST session open, not from clock
boundaries. This is a correctness requirement learned from a real defect in a
free data feed: its 30-minute bars start at 09:00, so the first bar of each
day holds only 09:15-09:30 - fifteen minutes of data in a bar labelled thirty.
Anchoring to the session makes that class of bug impossible.

A bucket with no underlying candles produces NO row. A synthesised candle is
indistinguishable from real data and could fire a real signal.
"""

from __future__ import annotations

import pandas as pd

from config import BASE_TIMEFRAME, IST, TIMEFRAME_MINUTES

# NSE regular session opens at 09:15 IST; every bucket is measured from there.
SESSION_OPEN_MINUTES = 9 * 60 + 15

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


class ResampleError(ValueError):
    """Raised when a frame or target timeframe cannot be resampled."""


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=OHLCV_COLUMNS,
        index=pd.DatetimeIndex([], tz="UTC", name="ts"),
    )


def resample_candles(df: pd.DataFrame, target_timeframe: str) -> pd.DataFrame:
    """Aggregate 5-minute candles into `target_timeframe`, session-anchored.

    Args:
        df: canonical 5-minute frame (tz-aware UTC index, OHLCV columns).
        target_timeframe: '5m', '15m', '25m', '30m' or '60m'.

    Returns:
        A canonical frame indexed by each bucket's session-anchored START time.

    Raises:
        ResampleError: for a naive index or a target that is not a whole
            multiple of the 5-minute base.
    """
    if target_timeframe == BASE_TIMEFRAME:
        return df  # passthrough: nothing to aggregate

    target_minutes = TIMEFRAME_MINUTES.get(target_timeframe)
    if target_minutes is None:
        raise ResampleError(f"Unknown timeframe {target_timeframe!r}")

    base_minutes = TIMEFRAME_MINUTES[BASE_TIMEFRAME]
    if target_minutes % base_minutes != 0:
        raise ResampleError(
            f"{target_timeframe} ({target_minutes} min) is not a whole multiple "
            f"of the {BASE_TIMEFRAME} base ({base_minutes} min); it cannot be "
            "derived by resampling. Daily candles are stored separately."
        )

    if df.empty:
        return _empty_frame()
    if df.index.tz is None:
        raise ResampleError(
            "resample_candles requires a tz-aware UTC index (got naive timestamps)"
        )

    frame = df.sort_index()
    ist_index = frame.index.tz_convert(IST)

    # Bucket key = (session date, whole buckets elapsed since 09:15 IST).
    # Using the session date keeps buckets from ever spanning two days.
    minutes_from_open = ist_index.hour * 60 + ist_index.minute - SESSION_OPEN_MINUTES
    bucket_number = minutes_from_open // target_minutes
    session_date = ist_index.date

    grouped = frame.groupby([session_date, bucket_number], sort=True)
    out = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )

    # Stamp each bucket with its own session-anchored start time (the first
    # candle's timestamp floored to the bucket boundary), not the first
    # candle's raw timestamp - so a bucket missing its opening candle is
    # still labelled correctly.
    starts = []
    for (day, bucket), _ in grouped:
        offset = SESSION_OPEN_MINUTES + int(bucket) * target_minutes
        starts.append(
            pd.Timestamp(
                year=day.year, month=day.month, day=day.day,
                hour=offset // 60, minute=offset % 60, tz=IST,
            ).tz_convert("UTC")
        )

    out.index = pd.DatetimeIndex(starts, name="ts")
    return out[OHLCV_COLUMNS].sort_index()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_resample.py -q`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add resample.py tests/test_resample.py
git commit -m "feat(data): add session-anchored candle resampling"
```

---

### Task 4: Data-quality checks

**Files:**
- Create: `data_quality.py`
- Test: `tests/test_data_quality.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_data_quality.py`:

```python
"""Tests for candle quality checks. Findings are FLAGGED, never auto-fixed."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_quality import (  # noqa: E402
    QualityFlag,
    check_ohlc_sanity,
    detect_session_gaps,
    detect_suspected_splits,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def frame(rows: list[tuple], start_ist: datetime | None = None, step_min: int = 5) -> pd.DataFrame:
    start = start_ist or datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    index = pd.DatetimeIndex(
        [(start + timedelta(minutes=step_min * i)).astimezone(UTC) for i in range(len(rows))],
        name="ts",
    )
    arr = np.asarray(rows, dtype=float)
    return pd.DataFrame(
        {"open": arr[:, 0], "high": arr[:, 1], "low": arr[:, 2],
         "close": arr[:, 3], "volume": arr[:, 4]},
        index=index,
    )


# --- OHLC sanity ------------------------------------------------------------


def test_valid_candles_produce_no_flags() -> None:
    df = frame([(100, 105, 99, 104, 10), (104, 106, 103, 105, 20)])
    assert check_ohlc_sanity(df) == []


def test_high_below_close_is_flagged() -> None:
    df = frame([(100, 101, 99, 104, 10)])  # high 101 < close 104
    flags = check_ohlc_sanity(df)
    assert len(flags) == 1
    assert flags[0].flag_type == "ohlc_invalid"
    assert "high" in flags[0].detail["reason"]


def test_low_above_open_is_flagged() -> None:
    df = frame([(100, 105, 101, 104, 10)])  # low 101 > open 100
    assert check_ohlc_sanity(df)[0].flag_type == "ohlc_invalid"


def test_negative_volume_and_zero_price_are_flagged() -> None:
    assert check_ohlc_sanity(frame([(100, 105, 99, 104, -5)]))
    assert check_ohlc_sanity(frame([(0, 105, 0, 104, 10)]))


def test_zero_volume_is_allowed() -> None:
    # Illiquid buckets legitimately trade zero shares.
    assert check_ohlc_sanity(frame([(100, 100, 100, 100, 0)])) == []


# --- Split detection --------------------------------------------------------


def test_split_sized_gap_absent_from_adjusted_daily_is_flagged() -> None:
    """Intraday shows a 1:5 split as an 80% crash; the adjusted daily series
    does not. That disagreement is the signal."""
    intraday = pd.DataFrame(
        {"close": [1000.0, 200.0]},
        index=pd.DatetimeIndex(
            [datetime(2026, 8, 3, 15, 25, tzinfo=IST).astimezone(UTC),
             datetime(2026, 8, 4, 9, 15, tzinfo=IST).astimezone(UTC)], name="ts"),
    )
    adjusted_daily = pd.DataFrame(
        {"close": [200.0, 200.0]},  # adjusted: no crash
        index=pd.DatetimeIndex(
            [datetime(2026, 8, 3, tzinfo=IST).astimezone(UTC),
             datetime(2026, 8, 4, tzinfo=IST).astimezone(UTC)], name="ts"),
    )
    flags = detect_suspected_splits(intraday, adjusted_daily)
    assert len(flags) == 1
    assert flags[0].flag_type == "suspected_split"
    assert flags[0].detail["ratio"] == 5.0


def test_genuine_crash_present_in_both_series_is_not_flagged() -> None:
    # A real 80% fall appears in BOTH series, so it is not a split.
    intraday = pd.DataFrame(
        {"close": [1000.0, 200.0]},
        index=pd.DatetimeIndex(
            [datetime(2026, 8, 3, 15, 25, tzinfo=IST).astimezone(UTC),
             datetime(2026, 8, 4, 9, 15, tzinfo=IST).astimezone(UTC)], name="ts"),
    )
    adjusted_daily = pd.DataFrame(
        {"close": [1000.0, 200.0]},
        index=pd.DatetimeIndex(
            [datetime(2026, 8, 3, tzinfo=IST).astimezone(UTC),
             datetime(2026, 8, 4, tzinfo=IST).astimezone(UTC)], name="ts"),
    )
    assert detect_suspected_splits(intraday, adjusted_daily) == []


def test_ordinary_overnight_move_is_not_flagged() -> None:
    intraday = pd.DataFrame(
        {"close": [1000.0, 1020.0]},
        index=pd.DatetimeIndex(
            [datetime(2026, 8, 3, 15, 25, tzinfo=IST).astimezone(UTC),
             datetime(2026, 8, 4, 9, 15, tzinfo=IST).astimezone(UTC)], name="ts"),
    )
    assert detect_suspected_splits(intraday, pd.DataFrame()) == []


# --- Session gaps -----------------------------------------------------------


def test_missing_expected_trading_day_is_flagged() -> None:
    # Mon 3rd present, Tue 4th absent, Wed 5th present.
    present = frame([(100, 101, 99, 100, 1)] * 3,
                    start_ist=datetime(2026, 8, 3, 9, 15, tzinfo=IST))
    later = frame([(100, 101, 99, 100, 1)] * 3,
                  start_ist=datetime(2026, 8, 5, 9, 15, tzinfo=IST))
    flags = detect_session_gaps(
        pd.concat([present, later]),
        expected_days=[date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)],
    )
    assert len(flags) == 1
    assert flags[0].flag_type == "session_gap"
    assert flags[0].detail["missing_date"] == "2026-08-04"


def test_no_gap_when_all_expected_days_present() -> None:
    df = frame([(100, 101, 99, 100, 1)] * 3)
    assert detect_session_gaps(df, expected_days=[date(2026, 8, 3)]) == []


def test_quality_flag_is_serialisable() -> None:
    flag = QualityFlag(
        flag_type="ohlc_invalid",
        ts=datetime(2026, 8, 3, 9, 15, tzinfo=UTC),
        detail={"reason": "test"},
    )
    row = flag.to_row(instrument_id=1, timeframe="5m")
    assert row["instrument_id"] == 1
    assert row["timeframe"] == "5m"
    assert row["flag_type"] == "ohlc_invalid"
    assert row["ts"].endswith("+00:00")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_data_quality.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'data_quality'`

- [ ] **Step 3: Write the implementation**

Create `data_quality.py`:

```python
"""Candle quality checks. Findings are FLAGGED for review, never auto-fixed.

A silently "corrected" price is indistinguishable from real data and would
quietly invalidate every result built on it. So each check returns flags; the
caller records them and (except for structurally impossible candles) keeps the
data as received.

Three checks
------------
* OHLC sanity      - structurally impossible candles. These ARE rejected.
* Suspected splits - an overnight jump in intraday data that is ABSENT from
                     Dhan's corporate-action-adjusted daily series. The
                     disagreement between the two feeds is what makes this
                     detectable without a separate corporate-actions source.
* Session gaps     - an expected trading day with no candles at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Sequence

import pandas as pd

from config import IST, UTC

# An overnight move beyond this ratio is implausible for a large-cap and is
# more likely a corporate action. Deliberately loose: we would rather review a
# few real crashes than silently trade through an unadjusted split.
SPLIT_RATIO_THRESHOLD = 1.5

# How closely the adjusted daily series must agree with the intraday move for
# it to count as a genuine price move rather than a split artefact.
ADJUSTED_AGREEMENT_TOLERANCE = 0.25


@dataclass(frozen=True)
class QualityFlag:
    """One detected problem, ready to be written to data_quality_flags."""

    flag_type: str            # 'suspected_split' | 'session_gap' | 'ohlc_invalid'
    ts: datetime
    detail: dict[str, Any] = field(default_factory=dict)

    def to_row(self, instrument_id: int, timeframe: str) -> dict[str, Any]:
        """Shape this flag as a data_quality_flags row."""
        return {
            "instrument_id": instrument_id,
            "timeframe": timeframe,
            "flag_type": self.flag_type,
            "ts": self.ts.astimezone(UTC).isoformat(),
            "detail": self.detail,
        }


def check_ohlc_sanity(df: pd.DataFrame) -> list[QualityFlag]:
    """Find structurally impossible candles.

    These are the only findings the caller should DROP rather than merely
    flag: a candle whose high is below its close never existed.
    """
    flags: list[QualityFlag] = []
    if df.empty:
        return flags

    for ts, row in df.iterrows():
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        v = float(row["volume"])
        reason: str | None = None
        if h < max(o, c):
            reason = f"high ({h}) is below max(open, close) ({max(o, c)})"
        elif l > min(o, c):
            reason = f"low ({l}) is above min(open, close) ({min(o, c)})"
        elif min(o, h, l, c) <= 0:
            reason = "a price is zero or negative"
        elif v < 0:
            reason = f"volume is negative ({v})"
        if reason:
            flags.append(QualityFlag(
                flag_type="ohlc_invalid",
                ts=ts.to_pydatetime(),
                detail={"reason": reason, "open": o, "high": h, "low": l, "close": c, "volume": v},
            ))
    return flags


def detect_suspected_splits(
    intraday: pd.DataFrame, adjusted_daily: pd.DataFrame
) -> list[QualityFlag]:
    """Flag overnight jumps that the ADJUSTED daily series does not corroborate.

    Dhan's daily feed is corporate-action adjusted; its intraday feed is not
    documented as adjusted. So a 1:5 split shows up intraday as an 80% crash
    while the daily series shows no such move. That disagreement is the signal.

    An overnight move present in BOTH series is a genuine price move and is
    left alone.
    """
    flags: list[QualityFlag] = []
    if intraday.empty or len(intraday) < 2:
        return flags

    ist_dates = intraday.index.tz_convert(IST).date
    closes = intraday["close"].astype(float)

    # Last close of each session, in order.
    session_last = pd.DataFrame({"date": ist_dates, "close": closes.values},
                                index=intraday.index).groupby("date").last()

    daily_by_date: dict[date, float] = {}
    if not adjusted_daily.empty:
        daily_by_date = {
            ts.astimezone(IST).date(): float(close)
            for ts, close in zip(adjusted_daily.index, adjusted_daily["close"])
        }

    previous_date = None
    previous_close = None
    for day, row in session_last.iterrows():
        close = float(row["close"])
        if previous_close is not None and previous_close > 0:
            ratio = max(close / previous_close, previous_close / close)
            if ratio >= SPLIT_RATIO_THRESHOLD:
                # Does the adjusted daily series show the same move?
                prev_adj = daily_by_date.get(previous_date)
                curr_adj = daily_by_date.get(day)
                corroborated = False
                if prev_adj and curr_adj and prev_adj > 0:
                    adj_ratio = max(curr_adj / prev_adj, prev_adj / curr_adj)
                    corroborated = abs(adj_ratio - ratio) <= ADJUSTED_AGREEMENT_TOLERANCE
                if not corroborated:
                    flags.append(QualityFlag(
                        flag_type="suspected_split",
                        ts=datetime.combine(day, datetime.min.time(), tzinfo=IST).astimezone(UTC),
                        detail={
                            "ratio": round(ratio, 4),
                            "previous_close": previous_close,
                            "close": close,
                            "previous_date": previous_date.isoformat() if previous_date else None,
                            "corroborated_by_adjusted_daily": corroborated,
                        },
                    ))
        previous_date, previous_close = day, close
    return flags


def detect_session_gaps(
    df: pd.DataFrame, expected_days: Sequence[date]
) -> list[QualityFlag]:
    """Flag expected trading days that have no candles at all."""
    flags: list[QualityFlag] = []
    present = set(df.index.tz_convert(IST).date) if not df.empty else set()
    for day in expected_days:
        if day not in present:
            flags.append(QualityFlag(
                flag_type="session_gap",
                ts=datetime.combine(day, datetime.min.time(), tzinfo=IST).astimezone(UTC),
                detail={"missing_date": day.isoformat()},
            ))
    return flags
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_data_quality.py -q`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add data_quality.py tests/test_data_quality.py
git commit -m "feat(data): add candle quality checks (splits, gaps, OHLC sanity)"
```

---

### Task 5: Provider protocol

**Files:**
- Create: `providers/__init__.py`
- Create: `providers/base.py`
- Test: `tests/test_provider_base.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_provider_base.py`:

```python
"""Tests for the CandleProvider protocol and its canonical-frame helper."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers.base import ProviderError, canonical_frame, empty_frame  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def test_empty_frame_shape() -> None:
    df = empty_frame()
    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert str(df.index.tz) == "UTC"
    assert df.index.name == "ts"


def test_canonical_frame_sorts_dedupes_and_converts_to_utc() -> None:
    ts = [
        datetime(2026, 8, 3, 9, 20, tzinfo=IST),
        datetime(2026, 8, 3, 9, 15, tzinfo=IST),
        datetime(2026, 8, 3, 9, 20, tzinfo=IST),  # duplicate
    ]
    raw = pd.DataFrame(
        {"open": [2.0, 1.0, 2.0], "high": [2.0, 1.0, 2.0], "low": [2.0, 1.0, 2.0],
         "close": [2.0, 1.0, 2.0], "volume": [20, 10, 20]},
        index=pd.DatetimeIndex(ts),
    )
    out = canonical_frame(raw)
    assert len(out) == 2                       # duplicate dropped
    assert out.index.is_monotonic_increasing   # sorted
    assert str(out.index.tz) == "UTC"
    assert out.index[0] == datetime(2026, 8, 3, 3, 45, tzinfo=UTC)  # 09:15 IST


def test_canonical_frame_localises_naive_index_to_ist() -> None:
    raw = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1]},
        index=pd.DatetimeIndex([datetime(2026, 8, 3, 9, 15)]),
    )
    assert canonical_frame(raw).index[0] == datetime(2026, 8, 3, 3, 45, tzinfo=UTC)


def test_missing_column_fails_loudly() -> None:
    raw = pd.DataFrame({"open": [1.0]}, index=pd.DatetimeIndex([datetime(2026, 8, 3)]))
    with pytest.raises(ProviderError, match="missing column"):
        canonical_frame(raw)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_provider_base.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'providers'`

- [ ] **Step 3: Create the package marker**

Create `providers/__init__.py`:

```python
"""Market-data providers. All return the identical canonical candle frame."""
```

- [ ] **Step 4: Write the protocol**

Create `providers/base.py`:

```python
"""The CandleProvider contract every data source must satisfy.

Because every provider returns the identical canonical frame, nothing
downstream of candle_store can tell which source produced the data.

Canonical frame
---------------
Columns [open, high, low, close, volume] as floats, indexed by candle START
time as tz-aware UTC, sorted ascending, duplicates removed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

import pandas as pd

from config import IST

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


class ProviderError(RuntimeError):
    """A data source failed or returned something unusable."""


def empty_frame() -> pd.DataFrame:
    """The canonical frame with no rows."""
    return pd.DataFrame(
        columns=OHLCV_COLUMNS,
        index=pd.DatetimeIndex([], tz="UTC", name="ts"),
    )


def canonical_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Coerce a provider's frame into canonical form.

    Naive timestamps are read as IST, since every supported source serves
    Indian market data in local time.
    """
    if raw is None or raw.empty:
        return empty_frame()

    missing = set(OHLCV_COLUMNS) - set(raw.columns)
    if missing:
        raise ProviderError(
            f"Provider frame is missing column(s): {sorted(missing)}. "
            "The upstream API shape may have changed."
        )

    df = raw[OHLCV_COLUMNS].astype(float).copy()
    index = pd.to_datetime(raw.index)
    index = index.tz_localize(IST) if index.tz is None else index
    df.index = index.tz_convert("UTC")
    df.index.name = "ts"
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df


@runtime_checkable
class CandleProvider(Protocol):
    """Read-only market-data source. No provider may expose order endpoints."""

    name: str

    def fetch(
        self, symbol: str, timeframe: str, from_utc: datetime, to_utc: datetime
    ) -> pd.DataFrame:
        """Return canonical candles for [from_utc, to_utc]."""
        ...

    def max_history_days(self, timeframe: str) -> int:
        """How far back this provider can serve the given timeframe."""
        ...
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_provider_base.py -q`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add providers/ tests/test_provider_base.py
git commit -m "feat(data): add CandleProvider protocol and canonical frame helper"
```

---

### Task 6: Dhan token lifecycle

**Files:**
- Create: `dhan_auth.py`
- Test: `tests/test_dhan_auth.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dhan_auth.py`:

```python
"""Tests for unattended Dhan token renewal. No network, fake clock."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dhan_auth import (  # noqa: E402
    RENEW_MARGIN,
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


def test_valid_token_is_reused_without_network() -> None:
    store = FakeTokenStore(StoredToken("good-token", NOW + timedelta(hours=8)))
    calls = []
    m = manager(store,
                renew=lambda: calls.append("renew") or StoredToken("x", NOW),
                generate=lambda: calls.append("gen") or StoredToken("y", NOW))
    assert m.get_access_token() == "good-token"
    assert calls == []          # nothing called; no needless token churn


def test_token_near_expiry_is_renewed() -> None:
    # Inside the renew margin -> renew proactively rather than fail mid-run.
    store = FakeTokenStore(StoredToken("old", NOW + RENEW_MARGIN - timedelta(minutes=1)))
    renewed = StoredToken("renewed", NOW + timedelta(hours=24))
    m = manager(store, renew=lambda: renewed,
                generate=lambda: pytest.fail("should not regenerate"))
    assert m.get_access_token() == "renewed"
    assert store.saved == [renewed]


def test_expired_token_is_renewed() -> None:
    store = FakeTokenStore(StoredToken("stale", NOW - timedelta(hours=1)))
    renewed = StoredToken("fresh", NOW + timedelta(hours=24))
    m = manager(store, renew=lambda: renewed, generate=lambda: pytest.fail("no"))
    assert m.get_access_token() == "fresh"


def test_no_token_generates_via_totp() -> None:
    store = FakeTokenStore(None)
    generated = StoredToken("brand-new", NOW + timedelta(hours=24))
    m = manager(store, renew=lambda: pytest.fail("nothing to renew"),
                generate=lambda: generated)
    assert m.get_access_token() == "brand-new"
    assert store.saved == [generated]


def test_renew_failure_falls_back_to_totp_generation() -> None:
    store = FakeTokenStore(StoredToken("stale", NOW - timedelta(hours=1)))
    generated = StoredToken("regenerated", NOW + timedelta(hours=24))

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


def test_credentials_never_appear_in_repr() -> None:
    # A stray f-string in a log line must not leak the second factor.
    text = repr(CREDS) + str(CREDS)
    assert "SECRET" not in text
    assert "JBSWY3DPEHPK3PXP" not in text


def test_missing_credentials_rejected_with_named_variables() -> None:
    with pytest.raises(DhanAuthError, match="DHAN_CLIENT_ID"):
        DhanCredentials.from_env({"DHAN_API_KEY": "k"})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_dhan_auth.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dhan_auth'`

- [ ] **Step 3: Write the implementation**

Create `dhan_auth.py`:

```python
"""Unattended Dhan access-token lifecycle.

Dhan tokens are valid 24 hours (mandatory since Oct 2025, driven by SEBI's
algo framework). Rather than a daily manual login, this module renews them
automatically: TOTP -> access token, then /v2/RenewToken before expiry.

Security position
-----------------
Automating this requires storing a TOTP secret, which is a second factor. The
mitigation is structural, not procedural: Dhan requires a WHITELISTED STATIC
IP for order placement, and this project never whitelists one. So even a
leaked token cannot place an order on the account. The secret lives only in
.env / GitHub Secrets, and is never logged, printed, or rendered.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Mapping, Protocol

import requests

from config import UTC

DHAN_API_BASE = "https://api.dhan.co/v2"

# Renew when less than this remains, so a long backfill cannot have its token
# expire underneath it mid-run.
RENEW_MARGIN = timedelta(minutes=30)

TOKEN_LIFETIME = timedelta(hours=24)
REQUEST_TIMEOUT_SECONDS = 20


class DhanAuthError(RuntimeError):
    """Authentication failed. Message names what to check; never the secret."""


@dataclass(frozen=True)
class StoredToken:
    """An access token and when it stops being valid."""

    access_token: str
    expires_at: datetime


@dataclass(frozen=True)
class DhanCredentials:
    """Dhan API credentials.

    __repr__ and __str__ are overridden so the secrets cannot leak through an
    accidental log line or exception message.
    """

    client_id: str
    api_key: str
    api_secret: str
    totp_secret: str

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"DhanCredentials(client_id={self.client_id!r}, secrets=<redacted>)"

    __str__ = __repr__

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DhanCredentials":
        """Build from environment variables, naming everything missing at once."""
        source = env if env is not None else os.environ
        names = ("DHAN_CLIENT_ID", "DHAN_API_KEY", "DHAN_API_SECRET", "DHAN_TOTP_SECRET")
        values = {n: (source.get(n) or "").strip() for n in names}
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
    """Where tokens are cached so concurrent runs share one."""

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
                "DHAN_CLIENT_ID, DHAN_API_KEY, DHAN_API_SECRET and "
                "DHAN_TOTP_SECRET, and that API access is enabled at "
                "web.dhan.co -> Profile -> DhanHQ Trading APIs."
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

    def _token_from_response(self, response, doing: str) -> StoredToken:
        """Parse a token response without ever echoing the token itself."""
        if response.status_code >= 400:
            raise DhanAuthError(
                f"Dhan rejected the request while {doing} "
                f"(HTTP {response.status_code})."
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise DhanAuthError(f"Dhan returned a non-JSON response while {doing}") from exc

        token = payload.get("accessToken") or payload.get("access_token")
        if not token:
            raise DhanAuthError(
                f"Dhan response contained no access token while {doing}. "
                "The API shape may have changed; check the DhanHQ v2 auth docs."
            )
        return StoredToken(access_token=token, expires_at=self._now() + TOKEN_LIFETIME)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_dhan_auth.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add dhan_auth.py tests/test_dhan_auth.py
git commit -m "feat(data): add unattended Dhan token renewal via TOTP"
```

---

### Task 7: Instrument master

**Files:**
- Create: `instruments.py`
- Test: `tests/test_instruments.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_instruments.py`:

```python
"""Tests for the Dhan security-master mapping."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from instruments import (  # noqa: E402
    Instrument,
    InstrumentError,
    parse_security_master,
    split_symbol,
)

CSV_SAMPLE = """SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL,SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_INSTRUMENT_NAME,SEM_LOT_UNITS,SM_SYMBOL_NAME
2885,RELIANCE,NSE,E,EQUITY,1,RELIANCE INDUSTRIES
1333,HDFCBANK,NSE,E,EQUITY,1,HDFC BANK
11536,TCS,NSE,E,EQUITY,1,TATA CONSULTANCY
1,SENSEX,BSE,I,INDEX,1,SENSEX
"""


def test_split_symbol() -> None:
    assert split_symbol("NSE:RELIANCE") == ("NSE", "RELIANCE")
    assert split_symbol("BSE:SENSEX") == ("BSE", "SENSEX")


@pytest.mark.parametrize("bad", ["RELIANCE", "NSE:", ":RELIANCE", "nse:reliance"])
def test_malformed_symbol_rejected(bad: str) -> None:
    with pytest.raises(InstrumentError):
        split_symbol(bad)


def test_parse_security_master_builds_instruments() -> None:
    found = parse_security_master(CSV_SAMPLE, refreshed_on=date(2026, 8, 3))
    by_symbol = {i.symbol: i for i in found}
    assert "NSE:RELIANCE" in by_symbol
    reliance = by_symbol["NSE:RELIANCE"]
    assert reliance.dhan_security_id == "2885"
    assert reliance.exchange == "NSE"
    assert reliance.tradingsymbol == "RELIANCE"
    assert reliance.instrument_type == "EQUITY"
    assert reliance.dhan_segment == "NSE_EQ"


def test_index_rows_get_the_index_segment() -> None:
    found = {i.symbol: i for i in parse_security_master(CSV_SAMPLE, refreshed_on=date(2026, 8, 3))}
    sensex = found["BSE:SENSEX"]
    assert sensex.instrument_type == "INDEX"
    assert sensex.dhan_segment == "IDX_I"


def test_instrument_to_row_matches_table_columns() -> None:
    inst = Instrument(
        symbol="NSE:RELIANCE", exchange="NSE", tradingsymbol="RELIANCE",
        dhan_security_id="2885", dhan_segment="NSE_EQ", name="RELIANCE INDUSTRIES",
        instrument_type="EQUITY", lot_size=1, refreshed_on=date(2026, 8, 3),
    )
    row = inst.to_row()
    assert row["symbol"] == "NSE:RELIANCE"
    assert row["dhan_security_id"] == "2885"
    assert row["refreshed_on"] == "2026-08-03"
    assert row["is_active"] is True


def test_unparseable_csv_fails_loudly() -> None:
    with pytest.raises(InstrumentError, match="expected column"):
        parse_security_master("wrong,header\n1,2\n", refreshed_on=date(2026, 8, 3))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_instruments.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'instruments'`

- [ ] **Step 3: Write the implementation**

Create `instruments.py`:

```python
"""Maps our 'NSE:RELIANCE' notation to Dhan security identifiers.

Dhan publishes a security master as CSV. We parse the handful of columns we
need and cache them in the `instruments` table, so a run never downloads the
multi-MB file just to resolve a few symbols.

ASSUMPTION: Dhan's master uses the SEM_* column names below. They are checked
explicitly at parse time, so a schema change fails loudly and immediately
rather than producing silently wrong security IDs.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

# Dhan publishes the detailed security master here.
SECURITY_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"

SYMBOL_RE = re.compile(r"^[A-Z]+:[A-Z0-9&\-]+$")

# The columns we depend on. Verified at parse time.
COLUMN_SECURITY_ID = "SEM_SMST_SECURITY_ID"
COLUMN_TRADING_SYMBOL = "SEM_TRADING_SYMBOL"
COLUMN_EXCHANGE = "SEM_EXM_EXCH_ID"
COLUMN_SEGMENT = "SEM_SEGMENT"
COLUMN_INSTRUMENT = "SEM_INSTRUMENT_NAME"
COLUMN_LOT = "SEM_LOT_UNITS"
COLUMN_NAME = "SM_SYMBOL_NAME"

REQUIRED_COLUMNS = (
    COLUMN_SECURITY_ID, COLUMN_TRADING_SYMBOL, COLUMN_EXCHANGE,
    COLUMN_SEGMENT, COLUMN_INSTRUMENT,
)

# Dhan's exchangeSegment values, keyed by (exchange, our instrument type).
SEGMENT_BY_EXCHANGE: dict[tuple[str, str], str] = {
    ("NSE", "EQUITY"): "NSE_EQ",
    ("BSE", "EQUITY"): "BSE_EQ",
    ("NSE", "INDEX"): "IDX_I",
    ("BSE", "INDEX"): "IDX_I",
}


class InstrumentError(ValueError):
    """A symbol or the security master could not be understood."""


@dataclass(frozen=True)
class Instrument:
    """One tradable instrument, as stored in the `instruments` table."""

    symbol: str
    exchange: str
    tradingsymbol: str
    dhan_security_id: str
    dhan_segment: str
    name: str | None
    instrument_type: str
    lot_size: int | None
    refreshed_on: date

    def to_row(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "tradingsymbol": self.tradingsymbol,
            "dhan_security_id": self.dhan_security_id,
            "dhan_segment": self.dhan_segment,
            "name": self.name,
            "instrument_type": self.instrument_type,
            "lot_size": self.lot_size,
            "is_active": True,
            "refreshed_on": self.refreshed_on.isoformat(),
        }


def split_symbol(symbol: str) -> tuple[str, str]:
    """'NSE:RELIANCE' -> ('NSE', 'RELIANCE'), validating the shape."""
    if not isinstance(symbol, str) or not SYMBOL_RE.match(symbol):
        raise InstrumentError(
            f"Malformed symbol {symbol!r}. Expected 'EXCHANGE:TRADINGSYMBOL' "
            "in capitals, e.g. NSE:RELIANCE."
        )
    exchange, _, tradingsymbol = symbol.partition(":")
    return exchange, tradingsymbol


def parse_security_master(
    csv_text: str, refreshed_on: date, wanted: Iterable[str] | None = None
) -> list[Instrument]:
    """Parse Dhan's security-master CSV into Instrument records.

    Args:
        csv_text: the raw CSV.
        refreshed_on: IST date of this refresh.
        wanted: if given, keep only these 'EXCHANGE:SYMBOL' values.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    headers = set(reader.fieldnames or [])
    missing = [c for c in REQUIRED_COLUMNS if c not in headers]
    if missing:
        raise InstrumentError(
            f"Dhan security master is missing expected column(s): {missing}. "
            "The file format may have changed; check the DhanHQ docs."
        )

    keep = set(wanted) if wanted is not None else None
    out: list[Instrument] = []
    for row in reader:
        exchange = (row.get(COLUMN_EXCHANGE) or "").strip().upper()
        tradingsymbol = (row.get(COLUMN_TRADING_SYMBOL) or "").strip().upper()
        if not exchange or not tradingsymbol:
            continue

        symbol = f"{exchange}:{tradingsymbol}"
        if keep is not None and symbol not in keep:
            continue
        if not SYMBOL_RE.match(symbol):
            continue  # exotic contract names we do not support

        instrument_name = (row.get(COLUMN_INSTRUMENT) or "").strip().upper()
        instrument_type = "INDEX" if "INDEX" in instrument_name else "EQUITY"
        segment = SEGMENT_BY_EXCHANGE.get((exchange, instrument_type))
        if segment is None:
            continue  # exchange/type combination we do not support

        lot_raw = (row.get(COLUMN_LOT) or "").strip()
        try:
            lot_size = int(float(lot_raw)) if lot_raw else None
        except ValueError:
            lot_size = None

        out.append(Instrument(
            symbol=symbol,
            exchange=exchange,
            tradingsymbol=tradingsymbol,
            dhan_security_id=(row.get(COLUMN_SECURITY_ID) or "").strip(),
            dhan_segment=segment,
            name=(row.get(COLUMN_NAME) or "").strip() or None,
            instrument_type=instrument_type,
            lot_size=lot_size,
            refreshed_on=refreshed_on,
        ))
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_instruments.py -q`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add instruments.py tests/test_instruments.py
git commit -m "feat(data): add Dhan security-master parsing"
```

---

### Task 8: Dhan provider adapter

**Files:**
- Create: `providers/dhan.py`
- Test: `tests/test_dhan_provider.py`

- [ ] **Step 1: Capture the real response shape as a fixture**

Before writing the parser, record what Dhan actually returns so the parser is
written against reality rather than assumption.

Create `scripts/capture_dhan_fixture.py`:

```python
"""One-off: capture a real Dhan intraday response as a test fixture.

Run once with credentials configured:
    .venv\\Scripts\\python.exe scripts\\capture_dhan_fixture.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from dotenv import load_dotenv

from dhan_auth import DHAN_API_BASE, DhanCredentials, DhanTokenManager, StoredToken

load_dotenv()


class MemoryStore:
    def __init__(self):
        self.token: StoredToken | None = None

    def get_token(self, provider): return self.token
    def save_token(self, provider, token): self.token = token


def main() -> int:
    creds = DhanCredentials.from_env()
    token = DhanTokenManager(creds, MemoryStore()).get_access_token()

    to_date = datetime.now()
    from_date = to_date - timedelta(days=5)
    response = requests.post(
        f"{DHAN_API_BASE}/charts/intraday",
        headers={"access-token": token, "client-id": creds.client_id,
                 "Content-Type": "application/json"},
        json={
            "securityId": "2885",           # RELIANCE
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "interval": "5",
            "fromDate": from_date.strftime("%Y-%m-%d"),
            "toDate": to_date.strftime("%Y-%m-%d"),
        },
        timeout=30,
    )
    print("HTTP", response.status_code)
    payload = response.json()
    print("keys:", list(payload)[:20])

    out = Path("tests/fixtures/dhan_intraday_5m.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    # Trim to the first 10 candles so the fixture stays small and readable.
    trimmed = {k: (v[:10] if isinstance(v, list) else v) for k, v in payload.items()}
    out.write_text(json.dumps(trimmed, indent=2), encoding="utf-8")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run: `.\.venv\Scripts\python.exe scripts\capture_dhan_fixture.py`
Expected: prints `HTTP 200` and the response keys, and writes the fixture.

**If the key names differ from `open/high/low/close/volume/timestamp`, use the real names in Step 3's `_parse_payload` and update the fixture-based test accordingly.** This is the one place the plan cannot be certain in advance, which is exactly why we capture first.

- [ ] **Step 2: Write the failing test**

Create `tests/test_dhan_provider.py`:

```python
"""Tests for the Dhan adapter. No network: HTTP is faked."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers.base import ProviderError  # noqa: E402
from providers.dhan import (  # noqa: E402
    MAX_DAYS_PER_REQUEST,
    DhanProvider,
    date_windows,
    parse_candle_payload,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


# --- payload parsing --------------------------------------------------------


def sample_payload() -> dict:
    """Dhan returns parallel ARRAYS, not a list of candle objects."""
    base = int(datetime(2026, 8, 3, 9, 15, tzinfo=IST).timestamp())
    return {
        "open":      [100.0, 101.0, 102.0],
        "high":      [105.0, 106.0, 107.0],
        "low":       [99.0, 100.0, 101.0],
        "close":     [104.0, 105.0, 106.0],
        "volume":    [1000, 2000, 3000],
        "timestamp": [base, base + 300, base + 600],
    }


def test_parse_payload_builds_canonical_frame() -> None:
    df = parse_candle_payload(sample_payload())
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert str(df.index.tz) == "UTC"
    assert len(df) == 3
    assert df.index[0] == datetime(2026, 8, 3, 9, 15, tzinfo=IST).astimezone(UTC)
    assert df["open"].iloc[0] == 100.0
    assert df["volume"].iloc[2] == 3000


def test_parse_empty_payload_gives_empty_frame() -> None:
    df = parse_candle_payload({"open": [], "high": [], "low": [],
                               "close": [], "volume": [], "timestamp": []})
    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_parse_payload_missing_key_fails_loudly() -> None:
    payload = sample_payload()
    del payload["volume"]
    with pytest.raises(ProviderError, match="missing"):
        parse_candle_payload(payload)


def test_parse_payload_ragged_arrays_fail_loudly() -> None:
    payload = sample_payload()
    payload["close"] = [1.0]  # shorter than the rest
    with pytest.raises(ProviderError, match="same length"):
        parse_candle_payload(payload)


# --- request windowing ------------------------------------------------------


def test_short_range_is_one_window() -> None:
    windows = date_windows(utc(2026, 1, 1), utc(2026, 2, 1))
    assert windows == [(utc(2026, 1, 1), utc(2026, 2, 1))]


def test_long_range_is_split_and_contiguous() -> None:
    windows = date_windows(utc(2024, 1, 1), utc(2026, 1, 1))
    assert len(windows) > 1
    assert windows[0][0] == utc(2024, 1, 1)
    assert windows[-1][1] == utc(2026, 1, 1)
    for (_, prev_end), (next_start, _) in zip(windows, windows[1:]):
        assert prev_end == next_start
    for start, end in windows:
        assert (end - start).days <= MAX_DAYS_PER_REQUEST


def test_inverted_range_rejected() -> None:
    with pytest.raises(ValueError):
        date_windows(utc(2026, 2, 1), utc(2026, 1, 1))


# --- fetch behaviour --------------------------------------------------------


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeHttp:
    """Records calls and returns queued responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        return self.responses.pop(0)


def make_provider(http, tokens="tok") -> DhanProvider:
    class FakeTokens:
        def get_access_token(self):
            return tokens

    class FakeInstruments:
        def resolve(self, symbol):
            return ("2885", "NSE_EQ", "EQUITY")

    return DhanProvider(
        token_manager=FakeTokens(), instrument_resolver=FakeInstruments(),
        http=http, sleep_fn=lambda s: None, client_id="CID",
    )


def test_fetch_returns_canonical_candles() -> None:
    http = FakeHttp([FakeResponse(sample_payload())])
    provider = make_provider(http)
    df = provider.fetch("NSE:RELIANCE", "5m", utc(2026, 8, 1), utc(2026, 8, 4))
    assert len(df) == 3
    assert http.calls[0]["json"]["securityId"] == "2885"
    assert http.calls[0]["json"]["interval"] == "5"


def test_fetch_pages_long_ranges_and_concatenates() -> None:
    http = FakeHttp([FakeResponse(sample_payload()), FakeResponse(sample_payload())])
    provider = make_provider(http)
    provider.fetch("NSE:RELIANCE", "5m", utc(2026, 1, 1), utc(2026, 7, 1))
    assert len(http.calls) == 2  # 180 days -> two 90-day windows


def test_daily_uses_the_historical_endpoint() -> None:
    http = FakeHttp([FakeResponse(sample_payload())])
    provider = make_provider(http)
    provider.fetch("NSE:RELIANCE", "day", utc(2026, 1, 1), utc(2026, 2, 1))
    assert http.calls[0]["url"].endswith("/charts/historical")


def test_http_error_raises_provider_error() -> None:
    http = FakeHttp([FakeResponse({}, status_code=500)])
    provider = make_provider(http)
    with pytest.raises(ProviderError, match="HTTP 500"):
        provider.fetch("NSE:RELIANCE", "5m", utc(2026, 8, 1), utc(2026, 8, 2))


def test_unsupported_timeframe_rejected() -> None:
    provider = make_provider(FakeHttp([]))
    with pytest.raises(ProviderError, match="stored timeframes"):
        provider.fetch("NSE:RELIANCE", "15m", utc(2026, 8, 1), utc(2026, 8, 2))


def test_max_history_days() -> None:
    provider = make_provider(FakeHttp([]))
    assert provider.max_history_days("5m") == 5 * 365
    assert provider.max_history_days("day") > 5 * 365
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_dhan_provider.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'providers.dhan'`

- [ ] **Step 4: Write the implementation**

Create `providers/dhan.py`:

```python
"""Dhan (DhanHQ v2) market-data adapter. READ-ONLY.

This module reaches exactly two endpoints - intraday charts and historical
(daily) charts. It contains no order code of any kind, and none may be added:
live execution belongs behind a separate, deliberately-added adapter.

Shape notes
-----------
* Dhan returns PARALLEL ARRAYS (open[], high[], ..., timestamp[]), not a list
  of candle objects.
* A single request may span at most 90 days, so long ranges are paged.
* Only the STORED timeframes ('5m' and 'day') are fetchable; every other
  intraday timeframe is derived by resampling the 5-minute base.
"""

from __future__ import annotations

import time as time_module
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol

import pandas as pd
import requests

from config import IST, STORED_TIMEFRAMES
from dhan_auth import DHAN_API_BASE
from providers.base import OHLCV_COLUMNS, ProviderError, canonical_frame, empty_frame

# Dhan serves at most 90 days of intraday data per request.
MAX_DAYS_PER_REQUEST = 90

# Documented history depth: 5 years of intraday, daily to inception.
INTRADAY_HISTORY_DAYS = 5 * 365
DAILY_HISTORY_DAYS = 40 * 365

REQUEST_TIMEOUT_SECONDS = 30
_SECONDS_BETWEEN_PAGES = 0.35   # stay well under Dhan's rate limits
_MAX_ATTEMPTS = 4
_BACKOFF_BASE_SECONDS = 1

INTRADAY_ENDPOINT = f"{DHAN_API_BASE}/charts/intraday"
HISTORICAL_ENDPOINT = f"{DHAN_API_BASE}/charts/historical"

# Our timeframe -> Dhan's `interval` value (intraday only).
TIMEFRAME_TO_DHAN_INTERVAL: dict[str, str] = {"5m": "5"}


class TokenProvider(Protocol):
    def get_access_token(self) -> str: ...


class InstrumentResolver(Protocol):
    def resolve(self, symbol: str) -> tuple[str, str, str]:
        """Return (security_id, exchange_segment, instrument_type)."""
        ...


def date_windows(
    from_utc: datetime, to_utc: datetime, max_days: int = MAX_DAYS_PER_REQUEST
) -> list[tuple[datetime, datetime]]:
    """Split a range into contiguous windows Dhan will accept."""
    if from_utc >= to_utc:
        raise ValueError(f"from ({from_utc}) must be before to ({to_utc})")
    windows: list[tuple[datetime, datetime]] = []
    start = from_utc
    step = timedelta(days=max_days)
    while start < to_utc:
        end = min(start + step, to_utc)
        windows.append((start, end))
        start = end
    return windows


def parse_candle_payload(payload: dict[str, Any]) -> pd.DataFrame:
    """Convert Dhan's parallel arrays into the canonical frame."""
    required = ("open", "high", "low", "close", "volume", "timestamp")
    missing = [k for k in required if k not in payload]
    if missing:
        raise ProviderError(
            f"Dhan candle payload is missing key(s): {missing}. "
            "The API shape may have changed; check the DhanHQ v2 chart docs."
        )

    lengths = {k: len(payload[k]) for k in required}
    if len(set(lengths.values())) > 1:
        raise ProviderError(
            f"Dhan candle arrays are not the same length: {lengths}. "
            "Refusing to guess how they align."
        )
    if lengths["timestamp"] == 0:
        return empty_frame()

    # Dhan sends epoch SECONDS, read here as true UTC instants (no double
    # conversion). VERIFY against the captured fixture: if the recorded
    # candle times land 5h30m away from the real session, Dhan is sending
    # IST-shifted epochs and this line needs `utc=False` plus an explicit
    # tz_localize(IST). The fixture test in this task is what catches that.
    index = pd.to_datetime(payload["timestamp"], unit="s", utc=True)
    frame = pd.DataFrame(
        {name: payload[name] for name in OHLCV_COLUMNS},
        index=index,
    )
    return canonical_frame(frame)


class DhanProvider:
    """Read-only Dhan candle source satisfying the CandleProvider protocol."""

    name = "dhan"

    def __init__(
        self,
        token_manager: TokenProvider,
        instrument_resolver: InstrumentResolver,
        client_id: str,
        http: Any = requests,
        sleep_fn: Callable[[float], None] = time_module.sleep,
    ) -> None:
        # http and sleep_fn are injectable so tests never touch the network
        # and never actually sleep.
        self._tokens = token_manager
        self._instruments = instrument_resolver
        self._client_id = client_id
        self._http = http
        self._sleep = sleep_fn

    def max_history_days(self, timeframe: str) -> int:
        return DAILY_HISTORY_DAYS if timeframe == "day" else INTRADAY_HISTORY_DAYS

    def fetch(
        self, symbol: str, timeframe: str, from_utc: datetime, to_utc: datetime
    ) -> pd.DataFrame:
        """Fetch canonical candles for [from_utc, to_utc], paging as needed."""
        if timeframe not in STORED_TIMEFRAMES:
            raise ProviderError(
                f"Dhan is only asked for stored timeframes "
                f"({', '.join(STORED_TIMEFRAMES)}); {timeframe!r} is derived by "
                "resampling and must not be fetched."
            )

        security_id, segment, instrument_type = self._instruments.resolve(symbol)
        endpoint = HISTORICAL_ENDPOINT if timeframe == "day" else INTRADAY_ENDPOINT

        frames: list[pd.DataFrame] = []
        for i, (window_from, window_to) in enumerate(date_windows(from_utc, to_utc)):
            if i > 0:
                self._sleep(_SECONDS_BETWEEN_PAGES)
            body: dict[str, Any] = {
                "securityId": security_id,
                "exchangeSegment": segment,
                "instrument": instrument_type,
                "fromDate": window_from.astimezone(IST).strftime("%Y-%m-%d"),
                "toDate": window_to.astimezone(IST).strftime("%Y-%m-%d"),
            }
            if timeframe != "day":
                body["interval"] = TIMEFRAME_TO_DHAN_INTERVAL[timeframe]

            payload = self._post(endpoint, body, f"fetching {timeframe} candles for {symbol}")
            frames.append(parse_candle_payload(payload))

        if not frames:
            return empty_frame()
        combined = pd.concat(frames)
        combined = combined[~combined.index.duplicated(keep="first")].sort_index()
        # Trim to the requested range: Dhan returns whole days.
        return combined[(combined.index >= from_utc) & (combined.index <= to_utc)]

    def _post(self, url: str, body: dict[str, Any], doing: str) -> dict[str, Any]:
        """One request with backoff on transient failures."""
        last_error: str | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            response = self._http.post(
                url,
                headers={
                    "access-token": self._tokens.get_access_token(),
                    "client-id": self._client_id,
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            status = response.status_code
            if status < 400:
                try:
                    return response.json()
                except ValueError as exc:
                    raise ProviderError(f"Dhan returned non-JSON while {doing}") from exc

            # 4xx (other than rate limiting) will not improve on retry.
            if status < 500 and status != 429:
                raise ProviderError(
                    f"Dhan rejected the request while {doing} (HTTP {status}). "
                    "This is a bad parameter or an auth problem, not a network issue."
                )
            last_error = f"HTTP {status}"
            if attempt < _MAX_ATTEMPTS:
                self._sleep(_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))

        raise ProviderError(
            f"Dhan still failing after {_MAX_ATTEMPTS} attempts while {doing}: "
            f"{last_error}."
        )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_dhan_provider.py -q`
Expected: PASS (12 passed)

- [ ] **Step 6: Verify the parser against the real fixture**

Add to `tests/test_dhan_provider.py`:

```python
def test_parser_handles_the_recorded_real_response() -> None:
    """Guards against the live API shape drifting away from our parser."""
    import json

    fixture = Path(__file__).parent / "fixtures" / "dhan_intraday_5m.json"
    if not fixture.exists():
        pytest.skip("run scripts/capture_dhan_fixture.py to record a fixture")
    df = parse_candle_payload(json.loads(fixture.read_text(encoding="utf-8")))
    assert not df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert str(df.index.tz) == "UTC"
    assert df.index.is_monotonic_increasing
```

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_dhan_provider.py -q`
Expected: PASS (13 passed, or 12 passed + 1 skipped if no fixture yet)

- [ ] **Step 7: Commit**

```bash
git add providers/dhan.py tests/test_dhan_provider.py scripts/capture_dhan_fixture.py tests/fixtures/
git commit -m "feat(data): add read-only Dhan candle provider with 90-day paging"
```

---

### Task 9: Coverage arithmetic

**Files:**
- Create: `coverage_math.py`
- Test: `tests/test_coverage_math.py`

This is the logic that decides what to fetch. It is separated from I/O because
getting it wrong means either refetching everything forever or — far worse —
believing we have data we do not.

- [ ] **Step 1: Write the failing test**

Create `tests/test_coverage_math.py`:

```python
"""Tests for cache-coverage range arithmetic (pure)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coverage_math import CoverageRange, extend_coverage, missing_ranges  # noqa: E402

UTC = timezone.utc


def d(day: int) -> datetime:
    return datetime(2026, 8, day, tzinfo=UTC)


def test_no_existing_coverage_fetches_everything() -> None:
    assert missing_ranges(None, d(1), d(10)) == [(d(1), d(10))]


def test_fully_covered_request_fetches_nothing() -> None:
    covered = CoverageRange(d(1), d(10))
    assert missing_ranges(covered, d(3), d(7)) == []


def test_request_extending_after_fetches_only_the_tail() -> None:
    covered = CoverageRange(d(1), d(10))
    assert missing_ranges(covered, d(5), d(15)) == [(d(10), d(15))]


def test_request_extending_before_fetches_only_the_head() -> None:
    covered = CoverageRange(d(10), d(20))
    assert missing_ranges(covered, d(5), d(15)) == [(d(5), d(10))]


def test_request_extending_both_ends_fetches_both() -> None:
    covered = CoverageRange(d(10), d(15))
    assert missing_ranges(covered, d(5), d(20)) == [(d(5), d(10)), (d(15), d(20))]


def test_disjoint_later_request_fills_the_gap_to_stay_contiguous() -> None:
    # Coverage is modelled as ONE contiguous range, so a disjoint request
    # fetches from the existing boundary rather than leaving an interior hole.
    covered = CoverageRange(d(1), d(5))
    assert missing_ranges(covered, d(10), d(12)) == [(d(5), d(12))]


def test_disjoint_earlier_request_fills_the_gap() -> None:
    covered = CoverageRange(d(10), d(15))
    assert missing_ranges(covered, d(1), d(3)) == [(d(1), d(10))]


def test_extend_coverage_from_nothing() -> None:
    assert extend_coverage(None, d(1), d(5)) == CoverageRange(d(1), d(5))


def test_extend_coverage_widens_both_ends() -> None:
    covered = CoverageRange(d(5), d(10))
    assert extend_coverage(covered, d(1), d(20)) == CoverageRange(d(1), d(20))


def test_extend_coverage_never_shrinks() -> None:
    # A narrow successful fetch must not discard wider existing coverage.
    covered = CoverageRange(d(1), d(20))
    assert extend_coverage(covered, d(5), d(10)) == CoverageRange(d(1), d(20))


def test_partial_fetch_records_only_what_was_retrieved() -> None:
    """The central safety property: a failure part-way through must not
    advance coverage past the last candle actually stored."""
    covered = CoverageRange(d(1), d(5))
    # Asked for d(5)->d(20) but only got as far as d(12).
    assert extend_coverage(covered, d(5), d(12)) == CoverageRange(d(1), d(12))


def test_inverted_request_rejected() -> None:
    with pytest.raises(ValueError):
        missing_ranges(None, d(10), d(1))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_coverage_math.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'coverage_math'`

- [ ] **Step 3: Write the implementation**

Create `coverage_math.py`:

```python
"""Pure range arithmetic for the candle cache.

Coverage is modelled as ONE contiguous [first_ts, last_ts] range per
(instrument, timeframe). That deliberate simplification means a request
disjoint from existing coverage fetches from the existing boundary rather than
leaving an interior hole - slightly more data than strictly needed, in
exchange for coverage that is trivially verifiable.

The property that matters most: coverage is only ever extended over data
genuinely retrieved. Overstating coverage would make every backtest built on
it silently wrong while looking perfectly healthy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CoverageRange:
    """A contiguous cached span."""

    first_ts: datetime
    last_ts: datetime


def missing_ranges(
    covered: CoverageRange | None, from_utc: datetime, to_utc: datetime
) -> list[tuple[datetime, datetime]]:
    """What must be fetched so [from_utc, to_utc] is fully cached."""
    if from_utc >= to_utc:
        raise ValueError(f"from ({from_utc}) must be before to ({to_utc})")

    if covered is None:
        return [(from_utc, to_utc)]

    gaps: list[tuple[datetime, datetime]] = []
    if from_utc < covered.first_ts:
        gaps.append((from_utc, covered.first_ts))
    if to_utc > covered.last_ts:
        gaps.append((covered.last_ts, to_utc))
    return gaps


def extend_coverage(
    covered: CoverageRange | None, fetched_from: datetime, fetched_to: datetime
) -> CoverageRange:
    """Widen coverage to include a genuinely retrieved span. Never shrinks."""
    if covered is None:
        return CoverageRange(fetched_from, fetched_to)
    return CoverageRange(
        min(covered.first_ts, fetched_from),
        max(covered.last_ts, fetched_to),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_coverage_math.py -q`
Expected: PASS (12 passed)

- [ ] **Step 5: Commit**

```bash
git add coverage_math.py tests/test_coverage_math.py
git commit -m "feat(data): add cache coverage range arithmetic"
```

---

### Task 10: Candle store repository

**Files:**
- Create: `candle_store.py`
- Test: `tests/test_candle_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_candle_store.py`:

```python
"""Tests for the candle repository, using an in-memory fake database."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from candle_store import CandleStore  # noqa: E402
from coverage_math import CoverageRange  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def five_min_frame(count: int, start_ist: datetime | None = None) -> pd.DataFrame:
    start = start_ist or datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    index = pd.DatetimeIndex(
        [(start + timedelta(minutes=5 * i)).astimezone(UTC) for i in range(count)],
        name="ts",
    )
    return pd.DataFrame(
        {"open": np.full(count, 100.0), "high": np.full(count, 101.0),
         "low": np.full(count, 99.0), "close": np.full(count, 100.5),
         "volume": np.full(count, 1000.0)},
        index=index,
    )


class FakeBackend:
    """Stands in for Supabase: candles, coverage and flags in memory."""

    def __init__(self):
        self.candles: dict[tuple[int, str], pd.DataFrame] = {}
        self.coverage: dict[tuple[int, str], CoverageRange] = {}
        self.flags: list[dict] = []
        self.instrument_ids = {"NSE:RELIANCE": 1, "NSE:TCS": 2}

    def instrument_id(self, symbol: str) -> int:
        return self.instrument_ids[symbol]

    def read_candles(self, instrument_id, timeframe, from_utc, to_utc):
        df = self.candles.get((instrument_id, timeframe))
        if df is None:
            return None
        return df[(df.index >= from_utc) & (df.index <= to_utc)]

    def write_candles(self, instrument_id, timeframe, df):
        key = (instrument_id, timeframe)
        existing = self.candles.get(key)
        merged = df if existing is None else pd.concat([existing, df])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        self.candles[key] = merged

    def read_coverage(self, instrument_id, timeframe):
        return self.coverage.get((instrument_id, timeframe))

    def write_coverage(self, instrument_id, timeframe, coverage, source):
        self.coverage[(instrument_id, timeframe)] = coverage

    def write_quality_flags(self, rows):
        self.flags.extend(rows)


class FakeProvider:
    """Returns canned frames and records every fetch it was asked to do."""

    name = "fake"

    def __init__(self, frame=None, fail_after=None):
        self.frame = frame if frame is not None else five_min_frame(6)
        self.calls: list[tuple] = []
        self.fail_after = fail_after

    def max_history_days(self, timeframe): return 5 * 365

    def fetch(self, symbol, timeframe, from_utc, to_utc):
        self.calls.append((symbol, timeframe, from_utc, to_utc))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise RuntimeError("provider exploded")
        return self.frame


def make_store(provider=None, backend=None) -> tuple[CandleStore, FakeBackend, FakeProvider]:
    backend = backend or FakeBackend()
    provider = provider or FakeProvider()
    return CandleStore(backend=backend, provider=provider), backend, provider


FROM = datetime(2026, 8, 3, 0, 0, tzinfo=UTC)
TO = datetime(2026, 8, 4, 0, 0, tzinfo=UTC)


def test_first_call_fetches_and_stores() -> None:
    store, backend, provider = make_store()
    store.ensure_coverage("NSE:RELIANCE", "5m", FROM, TO)
    assert len(provider.calls) == 1
    assert (1, "5m") in backend.candles
    assert backend.coverage[(1, "5m")] is not None


def test_second_identical_call_makes_no_network_call() -> None:
    """The property that makes backtests fast and offline-capable."""
    store, backend, provider = make_store()
    store.ensure_coverage("NSE:RELIANCE", "5m", FROM, TO)
    store.ensure_coverage("NSE:RELIANCE", "5m", FROM, TO)
    assert len(provider.calls) == 1  # not 2


def test_partial_failure_does_not_overstate_coverage() -> None:
    """A provider failure part-way must leave coverage honest."""
    backend = FakeBackend()
    provider = FakeProvider(fail_after=0)
    store = CandleStore(backend=backend, provider=provider)
    with pytest.raises(RuntimeError):
        store.ensure_coverage("NSE:RELIANCE", "5m", FROM, TO)
    assert backend.coverage.get((1, "5m")) is None  # nothing claimed


def test_invalid_candles_are_dropped_and_flagged() -> None:
    bad = five_min_frame(3)
    bad.iloc[1, bad.columns.get_loc("high")] = 1.0  # high below close
    store, backend, provider = make_store(provider=FakeProvider(frame=bad))
    store.ensure_coverage("NSE:RELIANCE", "5m", FROM, TO)
    stored = backend.candles[(1, "5m")]
    assert len(stored) == 2                                   # bad row removed
    assert any(f["flag_type"] == "ohlc_invalid" for f in backend.flags)


def test_get_candles_resamples_to_requested_timeframe() -> None:
    store, backend, provider = make_store(provider=FakeProvider(frame=five_min_frame(6)))
    out = store.get_candles("NSE:RELIANCE", "15m", FROM, TO)
    assert len(out) == 2                                      # 6 x 5m -> 2 x 15m
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]


def test_get_candles_at_base_timeframe_is_not_resampled() -> None:
    store, _, _ = make_store(provider=FakeProvider(frame=five_min_frame(6)))
    assert len(store.get_candles("NSE:RELIANCE", "5m", FROM, TO)) == 6


def test_get_candles_for_day_uses_the_day_timeframe() -> None:
    store, backend, provider = make_store()
    store.get_candles("NSE:RELIANCE", "day", FROM, TO)
    assert provider.calls[0][1] == "day"   # fetched 'day', not the 5m base


def test_reading_works_with_no_network_when_cached() -> None:
    """Market closed / weekend: cached reads must still work."""
    store, backend, provider = make_store()
    store.ensure_coverage("NSE:RELIANCE", "5m", FROM, TO)

    class ExplodingProvider(FakeProvider):
        def fetch(self, *a, **kw):
            raise AssertionError("must not hit the network when cached")

    offline = CandleStore(backend=backend, provider=ExplodingProvider())
    assert len(offline.get_candles("NSE:RELIANCE", "5m", FROM, TO)) == 6


def test_request_beyond_provider_history_is_clamped() -> None:
    store, backend, provider = make_store()
    long_ago = datetime(2000, 1, 1, tzinfo=UTC)
    store.ensure_coverage("NSE:RELIANCE", "5m", long_ago, TO)
    requested_from = provider.calls[0][2]
    assert requested_from > long_ago          # clamped to the 5-year window
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_candle_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'candle_store'`

- [ ] **Step 3: Write the implementation**

Create `candle_store.py`:

```python
"""The candle repository - the ONLY module the platform reads candles through.

Everything downstream (backtester, paper engine, dashboard) calls
`get_candles()` and is unaware of which provider produced the data, or whether
the market is currently open. That is what makes backtests runnable at any
hour, on weekends and on exchange holidays.

This module is also the single swap point if candle storage ever moves from
row-per-candle Postgres to day-blob arrays or Parquet: replace the backend,
change nothing else.

Safety property
---------------
Coverage is advanced ONLY over data genuinely retrieved and stored. If a fetch
fails part-way, everything already stored is kept but coverage stops at the
last good candle. Overstating coverage would silently corrupt every result
built on it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol

import pandas as pd

from config import UTC, source_timeframe_for
from coverage_math import CoverageRange, extend_coverage, missing_ranges
from data_quality import check_ohlc_sanity
from providers.base import empty_frame
from resample import resample_candles


class CandleBackend(Protocol):
    """Persistence for candles, coverage and quality flags."""

    def instrument_id(self, symbol: str) -> int: ...
    def read_candles(
        self, instrument_id: int, timeframe: str, from_utc: datetime, to_utc: datetime
    ) -> pd.DataFrame | None: ...
    def write_candles(self, instrument_id: int, timeframe: str, df: pd.DataFrame) -> None: ...
    def read_coverage(self, instrument_id: int, timeframe: str) -> CoverageRange | None: ...
    def write_coverage(
        self, instrument_id: int, timeframe: str, coverage: CoverageRange, source: str
    ) -> None: ...
    def write_quality_flags(self, rows: list[dict[str, Any]]) -> None: ...


class CandleProviderLike(Protocol):
    name: str
    def fetch(self, symbol: str, timeframe: str, from_utc: datetime, to_utc: datetime) -> pd.DataFrame: ...
    def max_history_days(self, timeframe: str) -> int: ...


class CandleStore:
    """Reads candles, fetching and caching only what is missing."""

    def __init__(self, backend: CandleBackend, provider: CandleProviderLike) -> None:
        self._backend = backend
        self._provider = provider

    # -- public API ----------------------------------------------------------

    def get_candles(
        self, symbol: str, timeframe: str, from_utc: datetime, to_utc: datetime
    ) -> pd.DataFrame:
        """Return candles for `timeframe`, fetching anything not yet cached.

        Intraday timeframes are served by resampling the stored 5-minute base;
        'day' is served directly from stored daily candles.
        """
        stored_timeframe = source_timeframe_for(timeframe)
        self.ensure_coverage(symbol, stored_timeframe, from_utc, to_utc)

        instrument_id = self._backend.instrument_id(symbol)
        base = self._backend.read_candles(instrument_id, stored_timeframe, from_utc, to_utc)
        if base is None or base.empty:
            return empty_frame()

        if timeframe == stored_timeframe:
            return base
        return resample_candles(base, timeframe)

    def ensure_coverage(
        self, symbol: str, timeframe: str, from_utc: datetime, to_utc: datetime
    ) -> None:
        """Fetch and store whatever part of [from, to] is not already cached."""
        instrument_id = self._backend.instrument_id(symbol)
        covered = self._backend.read_coverage(instrument_id, timeframe)

        # Never ask for more history than the provider actually serves; a
        # silently-empty response would look like "no data" instead of
        # "you asked for too much".
        earliest = datetime.now(tz=UTC) - timedelta(
            days=self._provider.max_history_days(timeframe)
        )
        effective_from = max(from_utc, earliest)
        if effective_from >= to_utc:
            return

        for gap_from, gap_to in missing_ranges(covered, effective_from, to_utc):
            fetched = self._provider.fetch(symbol, timeframe, gap_from, gap_to)
            if fetched.empty:
                continue

            clean = self._validate_and_flag(instrument_id, timeframe, fetched)
            if clean.empty:
                continue

            self._backend.write_candles(instrument_id, timeframe, clean)

            # Advance coverage only across what we actually stored.
            covered = extend_coverage(covered, gap_from, clean.index[-1].to_pydatetime())
            self._backend.write_coverage(
                instrument_id, timeframe, covered, self._provider.name
            )

    # -- internals -----------------------------------------------------------

    def _validate_and_flag(
        self, instrument_id: int, timeframe: str, df: pd.DataFrame
    ) -> pd.DataFrame:
        """Drop structurally impossible candles and record why."""
        flags = check_ohlc_sanity(df)
        if not flags:
            return df
        self._backend.write_quality_flags(
            [f.to_row(instrument_id, timeframe) for f in flags]
        )
        bad_timestamps = {f.ts for f in flags}
        return df[~df.index.isin(bad_timestamps)]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_candle_store.py -q`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add candle_store.py tests/test_candle_store.py
git commit -m "feat(data): add candle store with honest coverage tracking"
```

---

### Task 11: Supabase backend for the store

**Files:**
- Create: `supabase_candle_backend.py`
- Test: `tests/test_supabase_candle_backend.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_supabase_candle_backend.py`:

```python
"""Tests for the Supabase backend's row shaping (no network)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coverage_math import CoverageRange  # noqa: E402
from supabase_candle_backend import (  # noqa: E402
    candles_to_rows,
    coverage_to_row,
    rows_to_frame,
)

UTC = timezone.utc


def frame(count: int = 2) -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [datetime(2026, 8, 3, 4, 0, tzinfo=UTC) + timedelta(minutes=5 * i)
         for i in range(count)],
        name="ts",
    )
    return pd.DataFrame(
        {"open": np.full(count, 100.0), "high": np.full(count, 101.0),
         "low": np.full(count, 99.0), "close": np.full(count, 100.5),
         "volume": np.full(count, 1234.0)},
        index=index,
    )


def test_candles_to_rows_shape() -> None:
    rows = candles_to_rows(instrument_id=7, timeframe="5m", df=frame(2))
    assert len(rows) == 2
    row = rows[0]
    assert row["instrument_id"] == 7
    assert row["timeframe"] == "5m"
    assert row["ts"].endswith("+00:00")
    assert row["open"] == 100.0
    assert row["volume"] == 1234       # volume stored as an integer


def test_rows_to_frame_roundtrip() -> None:
    rows = candles_to_rows(instrument_id=7, timeframe="5m", df=frame(3))
    out = rows_to_frame(rows)
    assert len(out) == 3
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert str(out.index.tz) == "UTC"
    assert out.index.is_monotonic_increasing


def test_rows_to_frame_of_nothing_is_canonical_empty() -> None:
    out = rows_to_frame([])
    assert out.empty
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]


def test_coverage_to_row_shape() -> None:
    row = coverage_to_row(
        instrument_id=7, timeframe="5m",
        coverage=CoverageRange(datetime(2026, 8, 1, tzinfo=UTC),
                               datetime(2026, 8, 5, tzinfo=UTC)),
        source="dhan",
    )
    assert row["instrument_id"] == 7
    assert row["source"] == "dhan"
    assert row["first_ts"].endswith("+00:00")
    assert row["last_ts"].endswith("+00:00")


def test_naive_timestamps_rejected() -> None:
    df = frame(1)
    df.index = df.index.tz_localize(None)
    with pytest.raises(ValueError, match="timezone-aware"):
        candles_to_rows(instrument_id=1, timeframe="5m", df=df)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_supabase_candle_backend.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'supabase_candle_backend'`

- [ ] **Step 3: Write the implementation**

Create `supabase_candle_backend.py`:

```python
"""Supabase persistence for candles, coverage, instruments and quality flags.

Kept separate from db.py, which owns the trading-state tables (strategies,
positions, trades, run_audit). Candle storage is a different concern with a
very different access pattern - bulk writes and range reads - so it gets its
own module rather than growing db.py further.

Row shaping is exposed as pure functions so it can be tested without a
database.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

import pandas as pd
from postgrest.exceptions import APIError

from config import UTC
from coverage_math import CoverageRange
from providers.base import OHLCV_COLUMNS, empty_frame

# Supabase rejects very large payloads; candles are written in chunks.
WRITE_CHUNK_SIZE = 1000


class CandleBackendError(RuntimeError):
    """A candle-storage operation failed."""


def _iso(dt: datetime) -> str:
    """Serialize a timestamp, refusing naive values.

    A naive datetime silently read as the wrong zone is the classic
    trading-system bug, so we make storing one impossible.
    """
    if dt.tzinfo is None:
        raise ValueError(
            f"Refusing to store naive datetime {dt!r}; timestamps must be "
            "timezone-aware."
        )
    return dt.astimezone(UTC).isoformat()


def candles_to_rows(
    instrument_id: int, timeframe: str, df: pd.DataFrame
) -> list[dict[str, Any]]:
    """Shape a canonical frame as `candles` rows."""
    rows: list[dict[str, Any]] = []
    for ts, row in df.iterrows():
        rows.append({
            "instrument_id": instrument_id,
            "timeframe": timeframe,
            "ts": _iso(ts.to_pydatetime()),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]),
        })
    return rows


def rows_to_frame(rows: Sequence[dict[str, Any]]) -> pd.DataFrame:
    """Rebuild a canonical frame from `candles` rows."""
    if not rows:
        return empty_frame()
    df = pd.DataFrame(rows)
    index = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    out = df[OHLCV_COLUMNS].astype(float)
    out.index = pd.DatetimeIndex(index, name="ts")
    return out[~out.index.duplicated(keep="first")].sort_index()


def coverage_to_row(
    instrument_id: int, timeframe: str, coverage: CoverageRange, source: str
) -> dict[str, Any]:
    """Shape a coverage range as a `candle_coverage` row."""
    return {
        "instrument_id": instrument_id,
        "timeframe": timeframe,
        "first_ts": _iso(coverage.first_ts),
        "last_ts": _iso(coverage.last_ts),
        "source": source,
        "last_refreshed_at": _iso(datetime.now(tz=UTC)),
    }


class SupabaseCandleBackend:
    """CandleBackend implementation backed by Supabase."""

    def __init__(self, client) -> None:
        self._client = client
        self._instrument_ids: dict[str, int] = {}   # per-process memo

    def _table(self, name: str):
        return self._client.table(name)

    @staticmethod
    def _wrap(exc: Exception, doing: str) -> CandleBackendError:
        return CandleBackendError(
            f"Supabase error while {doing}: {exc}. If this says a relation "
            "does not exist, run sql/002_data_foundation.sql in the Supabase "
            "SQL editor."
        )

    def instrument_id(self, symbol: str) -> int:
        """Resolve 'NSE:RELIANCE' to its instruments.id."""
        if symbol in self._instrument_ids:
            return self._instrument_ids[symbol]
        try:
            resp = (
                self._table("instruments").select("id").eq("symbol", symbol).limit(1).execute()
            )
        except APIError as exc:
            raise self._wrap(exc, f"resolving instrument {symbol}") from exc
        if not resp.data:
            raise CandleBackendError(
                f"Instrument {symbol!r} is not in the instruments table. "
                "Run `python backfill.py --refresh-instruments` first."
            )
        self._instrument_ids[symbol] = int(resp.data[0]["id"])
        return self._instrument_ids[symbol]

    def read_candles(
        self, instrument_id: int, timeframe: str, from_utc: datetime, to_utc: datetime
    ) -> pd.DataFrame | None:
        rows: list[dict[str, Any]] = []
        page, start = 1000, 0
        while True:
            try:
                resp = (
                    self._table("candles").select("*")
                    .eq("instrument_id", instrument_id)
                    .eq("timeframe", timeframe)
                    .gte("ts", _iso(from_utc))
                    .lte("ts", _iso(to_utc))
                    .order("ts")
                    .range(start, start + page - 1)
                    .execute()
                )
            except APIError as exc:
                raise self._wrap(exc, "reading candles") from exc
            rows.extend(resp.data)
            if len(resp.data) < page:
                break
            start += page
        return rows_to_frame(rows) if rows else None

    def write_candles(self, instrument_id: int, timeframe: str, df: pd.DataFrame) -> None:
        rows = candles_to_rows(instrument_id, timeframe, df)
        for i in range(0, len(rows), WRITE_CHUNK_SIZE):
            chunk = rows[i:i + WRITE_CHUNK_SIZE]
            try:
                self._table("candles").upsert(
                    chunk, on_conflict="instrument_id,timeframe,ts"
                ).execute()
            except APIError as exc:
                raise self._wrap(exc, "writing candles") from exc

    def read_coverage(self, instrument_id: int, timeframe: str) -> CoverageRange | None:
        try:
            resp = (
                self._table("candle_coverage").select("*")
                .eq("instrument_id", instrument_id)
                .eq("timeframe", timeframe)
                .limit(1).execute()
            )
        except APIError as exc:
            raise self._wrap(exc, "reading coverage") from exc
        if not resp.data:
            return None
        row = resp.data[0]
        return CoverageRange(
            first_ts=datetime.fromisoformat(row["first_ts"].replace("Z", "+00:00")),
            last_ts=datetime.fromisoformat(row["last_ts"].replace("Z", "+00:00")),
        )

    def write_coverage(
        self, instrument_id: int, timeframe: str, coverage: CoverageRange, source: str
    ) -> None:
        try:
            self._table("candle_coverage").upsert(
                coverage_to_row(instrument_id, timeframe, coverage, source),
                on_conflict="instrument_id,timeframe",
            ).execute()
        except APIError as exc:
            raise self._wrap(exc, "writing coverage") from exc

    def write_quality_flags(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        try:
            self._table("data_quality_flags").insert(rows).execute()
        except APIError as exc:
            raise self._wrap(exc, "writing quality flags") from exc

    def upsert_instruments(self, rows: list[dict[str, Any]]) -> int:
        """Store security-master rows. Returns how many were written."""
        if not rows:
            return 0
        for i in range(0, len(rows), WRITE_CHUNK_SIZE):
            try:
                self._table("instruments").upsert(
                    rows[i:i + WRITE_CHUNK_SIZE], on_conflict="symbol"
                ).execute()
            except APIError as exc:
                raise self._wrap(exc, "writing instruments") from exc
        return len(rows)

    def get_token(self, provider: str):
        """TokenStore protocol: read the cached provider token."""
        from dhan_auth import StoredToken

        try:
            resp = (
                self._table("provider_tokens").select("*")
                .eq("provider", provider).limit(1).execute()
            )
        except APIError as exc:
            raise self._wrap(exc, "reading the provider token") from exc
        if not resp.data:
            return None
        row = resp.data[0]
        return StoredToken(
            access_token=row["access_token"],
            expires_at=datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")),
        )

    def save_token(self, provider: str, token) -> None:
        """TokenStore protocol: cache a provider token. Never logged."""
        try:
            self._table("provider_tokens").upsert(
                {
                    "provider": provider,
                    "access_token": token.access_token,
                    "expires_at": _iso(token.expires_at),
                    "updated_at": _iso(datetime.now(tz=UTC)),
                },
                on_conflict="provider",
            ).execute()
        except APIError as exc:
            raise self._wrap(exc, "saving the provider token") from exc
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_supabase_candle_backend.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add supabase_candle_backend.py tests/test_supabase_candle_backend.py
git commit -m "feat(data): add Supabase backend for candle storage"
```

---

### Task 12: Wire Dhan into the provider factory

**Files:**
- Modify: `data_provider.py`
- Create: `dhan_factory.py`
- Test: `tests/test_dhan_factory.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dhan_factory.py`:

```python
"""Tests for assembling the Dhan stack and the instrument resolver."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dhan_factory import SupabaseInstrumentResolver  # noqa: E402
from instruments import InstrumentError  # noqa: E402


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.queries = 0

    def table(self, name):
        return self

    def select(self, *a, **kw): return self
    def eq(self, *a, **kw): return self
    def limit(self, *a, **kw): return self

    def execute(self):
        self.queries += 1
        return type("Resp", (), {"data": self.rows})()


def test_resolver_returns_dhan_identifiers() -> None:
    client = FakeClient([{
        "dhan_security_id": "2885", "dhan_segment": "NSE_EQ", "instrument_type": "EQUITY",
    }])
    resolver = SupabaseInstrumentResolver(client)
    assert resolver.resolve("NSE:RELIANCE") == ("2885", "NSE_EQ", "EQUITY")


def test_resolver_caches_so_repeated_lookups_do_not_re_query() -> None:
    client = FakeClient([{
        "dhan_security_id": "2885", "dhan_segment": "NSE_EQ", "instrument_type": "EQUITY",
    }])
    resolver = SupabaseInstrumentResolver(client)
    resolver.resolve("NSE:RELIANCE")
    resolver.resolve("NSE:RELIANCE")
    assert client.queries == 1


def test_unknown_symbol_says_how_to_fix_it() -> None:
    resolver = SupabaseInstrumentResolver(FakeClient([]))
    with pytest.raises(InstrumentError, match="refresh-instruments"):
        resolver.resolve("NSE:NOSUCH")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_dhan_factory.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dhan_factory'`

- [ ] **Step 3: Write the implementation**

Create `dhan_factory.py`:

```python
"""Assembles the Dhan stack: credentials -> token manager -> provider.

Kept out of data_provider.py so that module stays a thin selector, and out of
providers/dhan.py so the adapter has no knowledge of Supabase.
"""

from __future__ import annotations

from instruments import InstrumentError, split_symbol


class SupabaseInstrumentResolver:
    """Looks up Dhan identifiers from the `instruments` table, with a memo."""

    def __init__(self, client) -> None:
        self._client = client
        self._cache: dict[str, tuple[str, str, str]] = {}

    def resolve(self, symbol: str) -> tuple[str, str, str]:
        """Return (security_id, exchange_segment, instrument_type)."""
        if symbol in self._cache:
            return self._cache[symbol]

        split_symbol(symbol)  # validate the shape before querying
        resp = (
            self._client.table("instruments")
            .select("dhan_security_id,dhan_segment,instrument_type")
            .eq("symbol", symbol)
            .limit(1)
            .execute()
        )
        if not resp.data:
            raise InstrumentError(
                f"{symbol!r} is not in the instruments table. Run "
                "`python backfill.py --refresh-instruments` to load the Dhan "
                "security master, and check the symbol spelling."
            )
        row = resp.data[0]
        resolved = (
            str(row["dhan_security_id"]),
            str(row["dhan_segment"]),
            str(row["instrument_type"]),
        )
        self._cache[symbol] = resolved
        return resolved


def create_dhan_provider(client):
    """Build a ready-to-use DhanProvider from a Supabase client."""
    from dhan_auth import DhanCredentials, DhanTokenManager
    from providers.dhan import DhanProvider
    from supabase_candle_backend import SupabaseCandleBackend

    credentials = DhanCredentials.from_env()
    backend = SupabaseCandleBackend(client)   # doubles as the TokenStore
    tokens = DhanTokenManager(credentials, backend)
    return DhanProvider(
        token_manager=tokens,
        instrument_resolver=SupabaseInstrumentResolver(client),
        client_id=credentials.client_id,
    )


def create_candle_store(client):
    """Build a CandleStore backed by Supabase and fed by Dhan."""
    from candle_store import CandleStore
    from supabase_candle_backend import SupabaseCandleBackend

    return CandleStore(
        backend=SupabaseCandleBackend(client),
        provider=create_dhan_provider(client),
    )
```

- [ ] **Step 4: Register the provider in `data_provider.py`**

In `data_provider.py`, add this branch inside `create_data_client`, immediately
before the `if provider == "kite":` branch:

```python
    if provider == "dhan":
        # Dhan reads through the candle store, so backtests hit Supabase and
        # work with the market closed. Requires the store's Supabase client.
        from dhan_factory import create_candle_store

        if store is None:
            raise RuntimeError(
                "The dhan provider needs a Supabase connection (it caches "
                "candles there). Remove --no-db, or set DATA_PROVIDER=yfinance."
            )
        return create_candle_store(store._client)
```

Then update `describe_provider` so the `dhan` case is described:

```python
def describe_provider(settings: Settings) -> str:
    """One-line human summary for logs and audit rows (never includes keys)."""
    if settings.data_provider == "yfinance":
        return "yfinance (free; 15m/30m history limited to ~60 days)"
    if settings.data_provider == "dhan":
        return "dhan (free; 5 years of 5-minute history, cached in Supabase)"
    return "kite (Kite Connect; requires the daily login token)"
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_dhan_factory.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Run the whole suite**

Run: `.\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add dhan_factory.py data_provider.py tests/test_dhan_factory.py
git commit -m "feat(data): wire the dhan provider into the factory"
```

---

### Task 13: Backfill CLI

**Files:**
- Create: `backfill.py`
- Test: `tests/test_backfill.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_backfill.py`:

```python
"""Tests for the backfill CLI's argument handling and symbol expansion."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backfill import build_parser, expand_symbols  # noqa: E402


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])
    assert args.years == 2.0
    assert args.timeframe == "5m"
    assert args.symbols is None
    assert args.refresh_instruments is False


def test_parser_accepts_symbols_and_years() -> None:
    args = build_parser().parse_args(
        ["--symbols", "NSE:RELIANCE,NSE:TCS", "--years", "5"]
    )
    assert args.symbols == "NSE:RELIANCE,NSE:TCS"
    assert args.years == 5.0


def test_expand_symbols_splits_and_normalises() -> None:
    assert expand_symbols(" nse:reliance , NSE:TCS ") == ["NSE:RELIANCE", "NSE:TCS"]


def test_expand_symbols_deduplicates_preserving_order() -> None:
    assert expand_symbols("NSE:TCS,NSE:RELIANCE,NSE:TCS") == ["NSE:TCS", "NSE:RELIANCE"]


def test_expand_symbols_rejects_malformed_entries() -> None:
    with pytest.raises(ValueError, match="RELIANCE"):
        expand_symbols("RELIANCE")


def test_expand_symbols_of_empty_string_is_empty() -> None:
    assert expand_symbols("") == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_backfill.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'backfill'`

- [ ] **Step 3: Write the implementation**

Create `backfill.py`:

```python
"""Warm the candle cache so backtests run instantly and offline.

Usage (from the project folder):

    .venv\\Scripts\\python.exe backfill.py --refresh-instruments
    .venv\\Scripts\\python.exe backfill.py --symbols NSE:RELIANCE,NSE:TCS --years 2
    .venv\\Scripts\\python.exe backfill.py --symbols NSE:RELIANCE --timeframe day --years 5

Backfilling is deliberately explicit rather than automatic: Dhan serves 90 days
per request, so a five-year pull of many symbols takes a while and should be a
thing you start knowingly.
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timedelta

from config import IST, STORED_TIMEFRAMES, UTC, get_settings
from db import SupabaseStore
from instruments import SYMBOL_RE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill the candle cache from the configured provider."
    )
    parser.add_argument(
        "--symbols",
        help="comma-separated EXCHANGE:SYMBOL list, e.g. NSE:RELIANCE,NSE:TCS",
    )
    parser.add_argument("--years", type=float, default=2.0,
                        help="years of history to ensure (default 2)")
    parser.add_argument("--timeframe", default="5m", choices=list(STORED_TIMEFRAMES),
                        help="stored timeframe to backfill (default 5m)")
    parser.add_argument("--refresh-instruments", action="store_true",
                        help="download and store the Dhan security master, then exit")
    return parser


def expand_symbols(raw: str) -> list[str]:
    """Parse a comma-separated symbol list into validated, deduped symbols."""
    if not raw or not raw.strip():
        return []
    out: list[str] = []
    for part in raw.split(","):
        symbol = part.strip().upper()
        if not symbol:
            continue
        if not SYMBOL_RE.match(symbol):
            raise ValueError(
                f"Malformed symbol {symbol!r}. Expected EXCHANGE:TRADINGSYMBOL, "
                "e.g. NSE:RELIANCE."
            )
        if symbol not in out:
            out.append(symbol)
    return out


def refresh_instruments(client) -> int:
    """Download the Dhan security master and store it."""
    import requests

    from instruments import SECURITY_MASTER_URL, parse_security_master
    from supabase_candle_backend import SupabaseCandleBackend

    print(f"Downloading the Dhan security master from {SECURITY_MASTER_URL} ...")
    response = requests.get(SECURITY_MASTER_URL, timeout=120)
    if response.status_code >= 400:
        print(f"ERROR: download failed with HTTP {response.status_code}", file=sys.stderr)
        return 1

    # IST explicitly, not the machine's local zone: a laptop in another
    # timezone must still stamp the Indian trading date.
    today_ist = datetime.now(tz=UTC).astimezone(IST).date()
    found = parse_security_master(response.text, refreshed_on=today_ist)
    written = SupabaseCandleBackend(client).upsert_instruments([i.to_row() for i in found])
    print(f"Stored {written} instruments.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        settings = get_settings()
        store = SupabaseStore.connect(settings)
    except RuntimeError as exc:
        print(f"SETUP PROBLEM: {exc}", file=sys.stderr)
        return 1

    if args.refresh_instruments:
        return refresh_instruments(store._client)

    symbols = expand_symbols(args.symbols or "")
    if not symbols:
        print("Nothing to do: pass --symbols or --refresh-instruments.", file=sys.stderr)
        return 1

    from dhan_factory import create_candle_store

    candle_store = create_candle_store(store._client)
    to_utc = datetime.now(tz=UTC)
    from_utc = to_utc - timedelta(days=math.ceil(args.years * 365.25))

    failures = 0
    for symbol in symbols:
        print(f"  {symbol:<18} {args.timeframe} ...", end=" ", flush=True)
        try:
            candle_store.ensure_coverage(symbol, args.timeframe, from_utc, to_utc)
        except Exception as exc:
            failures += 1
            print(f"FAILED: {exc}")
        else:
            print("ok")

    print(
        f"\nDone: {len(symbols) - failures} of {len(symbols)} symbol(s) covered "
        f"for {args.years} year(s) of {args.timeframe} candles."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_backfill.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Verify the CLI parses**

Run: `.\.venv\Scripts\python.exe backfill.py --help`
Expected: usage text listing `--symbols`, `--years`, `--timeframe`, `--refresh-instruments`.

- [ ] **Step 6: Commit**

```bash
git add backfill.py tests/test_backfill.py
git commit -m "feat(data): add backfill CLI for warming the candle cache"
```

---

### Task 14: Live verification against the real API

This is the task that proves the whole phase works. Everything before it was
tested against fakes.

**Files:**
- Create: `scripts/verify_dhan_live.py`

- [ ] **Step 1: Write the verification script**

Create `scripts/verify_dhan_live.py`:

```python
"""Live end-to-end check of the Phase 0 data foundation.

Run once, from the project folder, with Dhan credentials in .env:

    .venv\\Scripts\\python.exe scripts\\verify_dhan_live.py

Proves, against the real API and database:
  1. a token is obtained unattended (no manual login),
  2. candles are fetched and stored,
  3. a second call makes NO network call (cache hit),
  4. resampling produces sane higher timeframes,
  5. reading works with the market closed.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from config import IST, UTC, get_settings
from db import SupabaseStore

load_dotenv()

SYMBOL = "NSE:RELIANCE"


def main() -> int:
    settings = get_settings()
    store = SupabaseStore.connect(settings)
    client = store._client

    from dhan_factory import create_candle_store, create_dhan_provider

    print("1. Obtaining an access token unattended ...")
    provider = create_dhan_provider(client)
    token = provider._tokens.get_access_token()
    print(f"   OK - token obtained (length {len(token)}, value not shown)")

    candle_store = create_candle_store(client)
    to_utc = datetime.now(tz=UTC)
    from_utc = to_utc - timedelta(days=30)

    print(f"2. Fetching 30 days of 5-minute candles for {SYMBOL} ...")
    candle_store.ensure_coverage(SYMBOL, "5m", from_utc, to_utc)
    base = candle_store.get_candles(SYMBOL, "5m", from_utc, to_utc)
    print(f"   OK - {len(base)} candles, {base.index[0]} .. {base.index[-1]}")

    print("3. Re-reading (must make NO network call) ...")

    class ExplodingProvider:
        name = "exploding"
        def max_history_days(self, timeframe): return 5 * 365
        def fetch(self, *a, **kw):
            raise AssertionError("cache miss: it hit the network")

    from candle_store import CandleStore
    from supabase_candle_backend import SupabaseCandleBackend

    offline = CandleStore(SupabaseCandleBackend(client), ExplodingProvider())
    cached = offline.get_candles(SYMBOL, "5m", from_utc, to_utc)
    print(f"   OK - {len(cached)} candles served entirely from cache")

    print("4. Resampling to higher timeframes ...")
    for tf in ("15m", "30m", "60m"):
        frame = offline.get_candles(SYMBOL, tf, from_utc, to_utc)
        first = frame.index[0].astimezone(IST)
        print(f"   {tf:>4}: {len(frame):>5} candles, first bucket {first:%Y-%m-%d %H:%M} IST")

    print("5. Sanity-checking the aggregation ...")
    day = base.index[-1].astimezone(IST).date()
    same_day = base[base.index.tz_convert(IST).date == day]
    fifteen = offline.get_candles(SYMBOL, "15m", from_utc, to_utc)
    same_day_15 = fifteen[fifteen.index.tz_convert(IST).date == day]
    assert abs(same_day["volume"].sum() - same_day_15["volume"].sum()) < 1, \
        "resampled volume must equal the base volume for the same session"
    print("   OK - resampled volume matches the 5-minute base exactly")

    print("\nAll checks passed. The data foundation is live.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Load the security master**

Run: `.\.venv\Scripts\python.exe backfill.py --refresh-instruments`
Expected: `Stored N instruments.` with N in the thousands.

- [ ] **Step 3: Run the live verification**

Run: `.\.venv\Scripts\python.exe scripts\verify_dhan_live.py`
Expected: all five steps print `OK`, ending with `All checks passed.`

If step 3 raises `cache miss: it hit the network`, coverage is not being
recorded — check `candle_coverage` in Supabase for a row matching the
instrument and `5m`.

- [ ] **Step 4: Verify data landed in the database**

Run the Supabase MCP `execute_sql` tool:

```sql
select i.symbol, c.timeframe, count(*) as candles,
       min(c.ts) as first_ts, max(c.ts) as last_ts
from candles c join instruments i on i.id = c.instrument_id
group by i.symbol, c.timeframe
order by i.symbol;
```

Expected: one row for `NSE:RELIANCE` / `5m` with roughly 20 trading days × 75 candles.

- [ ] **Step 5: Confirm market-closed operation**

Re-run outside market hours (evening or a weekend):
Run: `.\.venv\Scripts\python.exe scripts\verify_dhan_live.py`
Expected: still passes — steps 3–5 read purely from cache. This is the
holiday/market-hours independence requirement, demonstrated.

- [ ] **Step 6: Commit**

```bash
git add scripts/verify_dhan_live.py
git commit -m "test(data): add live end-to-end verification of the data foundation"
```

---

### Task 15: Documentation

**Files:**
- Modify: `docs/OPERATING_GUIDE.md`
- Modify: `.github/workflows/paper.yml`
- Modify: `.github/workflows/backtest.yml`

- [ ] **Step 1: Pass Dhan credentials to the workflows**

In **both** `.github/workflows/paper.yml` and `.github/workflows/backtest.yml`,
add these four lines to the `env:` block of the run step, after the existing
`KITE_API_SECRET` line:

```yaml
          # Dhan: only used when DATA_PROVIDER=dhan. Data APIs need no static
          # IP, so GitHub's dynamic runner IPs are fine.
          DHAN_CLIENT_ID: ${{ secrets.DHAN_CLIENT_ID }}
          DHAN_API_KEY: ${{ secrets.DHAN_API_KEY }}
          DHAN_API_SECRET: ${{ secrets.DHAN_API_SECRET }}
          DHAN_TOTP_SECRET: ${{ secrets.DHAN_TOTP_SECRET }}
```

- [ ] **Step 2: Document the Dhan setup in the operating guide**

In `docs/OPERATING_GUIDE.md`, replace the whole of section **2.3** (currently
"Market data — nothing to do (it's free by default)") with:

```markdown
### 2.3 Market data — Dhan (free, precise, 5 years of history)

The platform's default data source is **Dhan**, which gives **5 years of
5-minute history** at **zero cost** — no API fee, no AMC, no account-opening
fee — and renews its access token automatically, so there is **no daily
login**.

**One-time setup:**

1. Open a free Dhan account at [dhan.co](https://dhan.co) (₹0 opening, ₹0 AMC).
   You do not need to fund it to use the data API.
2. Go to **web.dhan.co → Profile → DhanHQ Trading APIs** and enable API access.
3. Enable **TOTP** for your account and save the secret it shows you.
4. Put all four values in `.env`:
   ```
   DATA_PROVIDER=dhan
   DHAN_CLIENT_ID=...
   DHAN_API_KEY=...
   DHAN_API_SECRET=...
   DHAN_TOTP_SECRET=...
   ```
5. Load the symbol list and warm the cache:
   ```powershell
   .\.venv\Scripts\python.exe backfill.py --refresh-instruments
   .\.venv\Scripts\python.exe backfill.py --symbols NSE:RELIANCE,NSE:TCS --years 2
   ```

**Why candles are cached:** backtests read from your Supabase database, never
from a live API — so they run identically at 2 a.m., on weekends, and on
exchange holidays.

> **On the TOTP secret.** It is a second factor: keep it in `.env`
> (git-ignored) or GitHub Secrets, never in the repo. Note that Dhan requires a
> **whitelisted static IP** for order placement and this project never
> whitelists one — so even a leaked token cannot trade your account.

<details>
<summary>Other providers (yfinance, Kite)</summary>

`DATA_PROVIDER=yfinance` remains available and needs no account at all, but
serves only **~58 days** of 15-minute history — too little for meaningful
backtests. It is kept as a fallback and as a cross-check on Dhan's data.

`DATA_PROVIDER=kite` requires Zerodha's **paid** Connect plan (₹500/30 days;
the free Personal tier has no historical-data API) plus a daily login.
</details>
```

- [ ] **Step 3: Add the new commands to the cheat-sheet**

In `docs/OPERATING_GUIDE.md` section 5, add these rows to the command table:

```markdown
| Load the Dhan symbol master | `.\.venv\Scripts\python.exe backfill.py --refresh-instruments` |
| Warm the candle cache | `.\.venv\Scripts\python.exe backfill.py --symbols NSE:RELIANCE --years 2` |
| Verify the data layer end to end | `.\.venv\Scripts\python.exe scripts\verify_dhan_live.py` |
```

- [ ] **Step 4: Add data troubleshooting rows**

In `docs/OPERATING_GUIDE.md` section 6, add these rows to the troubleshooting table:

```markdown
| `is not in the instruments table` | Symbol master not loaded | `backfill.py --refresh-instruments` |
| `Missing Dhan credential(s)` | `.env` incomplete | Add the four `DHAN_*` values (§2.3) |
| `Could not obtain a Dhan access token` | API access not enabled, or wrong TOTP secret | Re-check web.dhan.co → Profile → DhanHQ Trading APIs |
| Backtest is slow the first time | Cache is cold; candles are being fetched | Normal — subsequent runs read from cache |
```

- [ ] **Step 5: Run the whole suite one final time**

Run: `.\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add docs/OPERATING_GUIDE.md .github/workflows/paper.yml .github/workflows/backtest.yml
git commit -m "docs: document the Dhan data foundation and backfill commands"
```

---

## Definition of Done

From the spec's §10, each now mapped to the task that proves it:

- [ ] `NSE:RELIANCE` 5-min candles for 2 years stored and re-readable — *Task 13 + 14*
- [ ] A second `ensure_coverage` makes **zero** network calls — *Task 10 (test), Task 14 step 3 (live)*
- [ ] 15/30/60-min resampling matches hand-computed values, session-anchored — *Task 3*
- [ ] Reading candles works with the market closed — *Task 14 step 5*
- [ ] A forced mid-backfill failure leaves coverage honest — *Task 9 + Task 10*
- [ ] Token auto-renews unattended across an expiry boundary — *Task 6*
- [ ] Quality flags raised on a known split and a known holiday gap — *Task 4*
- [ ] Secrets absent from logs, errors, audit rows and UI — *Task 6 (`__repr__` test)*
- [ ] Full offline suite green — *Task 15 step 5*

---

## Self-Review Notes

**Spec coverage.** Every section of the spec maps to a task: §2.1 provider →
Task 8; §2.2 base timeframe → Tasks 2, 3; §2.3 storage → Tasks 1, 11; §2.4 auth
→ Task 6; §3.1 modules → Tasks 3–11; §3.2 session anchoring → Task 3; §3.3
daily stored separately → Tasks 2, 8, 10; §4 data model → Task 1; §5.1 backfill
→ Tasks 9, 10, 13; §5.2 read paths → Tasks 2, 10; §5.3 token lifecycle → Task 6;
§6 quality → Task 4; §7 error handling → Tasks 6, 8, 10, 11; §8 testing → every
task plus Task 14; §9 risks → mitigations built into Tasks 8, 10.

**Deliberately deferred:** §5.4 (the refresh *button*) is UI and belongs to
Phase 3 — its engine-side support, `ensure_coverage` extending to now, is built
here in Task 10. `symbol_groups` tables are created in Task 1 but not populated;
they are Phase 1's concern.

**Type consistency verified.** `CoverageRange(first_ts, last_ts)` is used
identically in Tasks 9, 10 and 11. `QualityFlag.to_row(instrument_id, timeframe)`
matches its call site in `candle_store._validate_and_flag`. The
`CandleBackend` protocol in Task 10 matches `SupabaseCandleBackend` in Task 11
method for method. `resolve()` returns the same 3-tuple in Tasks 8 and 12.
`source_timeframe_for` (Task 2) is used in Task 10.

**One residual uncertainty, handled explicitly:** the exact Dhan response key
names. Task 8 Step 1 captures a real response as a fixture *before* the parser
is written, and Step 6 adds a regression test against it. That is the honest
way to handle the one thing this plan cannot know in advance.
