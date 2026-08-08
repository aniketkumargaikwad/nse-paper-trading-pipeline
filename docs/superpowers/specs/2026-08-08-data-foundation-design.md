# Phase 0 — Precise Data Foundation

**Status:** Approved · **Date:** 2026-08-08

The first of six phases toward a single platform covering strategy authoring →
multi-symbol/multi-timeframe backtesting → paper trading → (eventually) live
broker execution.

---

## 1. Why this phase exists

The platform's stated non-negotiable is **precise data**. The current system
sources candles from Yahoo Finance (`yfinance`), which serves only **~58 days**
of 15-minute history. Every downstream ambition — multi-year backtests across
Nifty 100 on several timeframes, statistically meaningful metrics, honest
robustness rules — is impossible on a 58-day window. Backtests on that data
cannot reach the 30-trade minimum, so nothing can ever be validated.

Phase 0 replaces the data layer with a **local, precise candle store**. Once it
exists, backtests read from our own database and therefore run **identically at
any hour, on weekends, and on exchange holidays** — a hard requirement.

### Non-goals for this phase

Explicitly out of scope here (each has its own later phase):

- Strategy format/DSL changes, AI-assisted authoring — *Phase 1*
- Symbol groups / universes UI — *Phase 1* (tables are defined here, populated later)
- The batch backtest engine and metric suite — *Phase 2*
- Strategies grid, detail page, results popup — *Phase 3*
- Paper-trading deployment and performance page — *Phase 4*
- Live broker order execution — *Phase 5*

---

## 2. Decisions and the evidence behind them

All figures below were verified against current sources on 2026-08-08, not
recalled from training data.

### 2.1 Data provider: Dhan (DhanHQ v2)

| Source | Intraday history | Cost |
|---|---|---|
| yfinance (current) | ~58 days on 15m | free |
| **Dhan** ✅ | **5 years** of 1/5/15/25/60-min; daily to inception | **₹0** API, ₹0 AMC, ₹0 account opening |
| Upstox v3 | minutes/hours from Jan 2022 | free with account |
| Breeze (ICICI) | 3 years | free |
| TrueData / GDFL | tick / L1, exchange-authorised | paid subscription |
| Zerodha Kite | years | **paid** — ₹500/30 days |

**Chosen: Dhan.** Longest free intraday history, zero cost, and it doubles as
the future execution broker — meaning one instrument-ID space and the same data
the broker itself sees.

Zerodha was excluded on evidence: its free *Personal* tier explicitly has **no
historical-data API**; only the paid *Connect* plan (₹500/30 days) includes it.

**Constraint:** Dhan serves a maximum of **90 days per request**, so all
backfill must page.

### 2.2 Base timeframe: 5-minute

Storage cost for Nifty 100 × 5 years (~1,250 trading days), in Postgres at
roughly 120 bytes/row including index overhead:

| Base stored | Rows | Size |
|---|---|---|
| 15-min | 3.1 M | ~375 MB |
| **5-min** ✅ | 9.4 M | ~1.1 GB |
| 1-min | 46.9 M | ~5.6 GB |

Supabase free tier is 500 MB; Pro is 8 GB at $25/mo.

**Chosen: 5-minute base.** Only one base timeframe is stored; 15/25/30/60-min
are **derived by resampling**. This guarantees every timeframe is mutually
consistent and lets us add timeframes later without refetching. Resampling
cannot go *downward*, so the base is a permanent floor — 5-min covers every
realistic intraday strategy while staying an order of magnitude cheaper than
1-min.

### 2.3 Storage approach: row-per-candle, fetched on demand

Three approaches were evaluated:

| | Nifty 100 × 5 yr × 5-min | Trade-off |
|---|---|---|
| **A. Row per candle** ✅ | ~1.1 GB | Simplest, plain SQL, easy incremental upsert |
| B. Day-blob arrays | ~240 MB | Free-tier forever; complex writes, `unnest` for ad-hoc SQL |
| C. Parquet + DuckDB | ~150–190 MB | Densest/fastest; second storage system, file rewrites, no RLS |

**Chosen: A**, with the store behind a narrow repository interface so B or C
remains a **contained, single-module swap** rather than a redesign.

Rationale: storage grows only with symbols actually tested (on-demand fetch),
so the free tier covers realistic early usage; and complexity budget is better
spent on the engine, metrics, and UI than on premature compression.

*Verified:* the Supabase project has **no TimescaleDB**, so columnar compression
is not an option. `pg_cron`, `pg_partman`, and `pgmq` are available.

### 2.4 Authentication: automated TOTP token renewal

Dhan access tokens are valid **24 hours** (mandatory since Oct 2025, driven by
SEBI's algo framework). Automation is supported: **TOTP-based generation** plus
a **`/v2/RenewToken`** endpoint that issues a fresh 24h token.

**Chosen: fully automated renewal.** No daily manual login. One consistent data
source for both backtesting and live paper trading, so their numbers always
agree.

**Security position.** Automating this requires storing a TOTP secret — a second
factor. The mitigation is structural rather than procedural: Dhan requires a
**whitelisted static IP for order placement**, and we will never whitelist one.
Therefore *even a leaked token cannot place an order on the account*. Combined
with the codebase's existing hard rule that no order endpoint is ever called,
this is defence in depth.

Handling rules: secret lives in GitHub Secrets / Supabase Vault, is never
logged, never rendered in the UI, and never written to an audit row.

### 2.5 Static IP: not needed for this phase

Verified: *"Static IP is only required while using Order Placement APIs...
While fetching order details or trade details, no such IP whitelisting is
required."*

**Data fetching therefore works from GitHub Actions' dynamic IPs.** A static IP
(~₹499/mo services exist) becomes necessary only at Phase 5.

### 2.6 Regulatory context for Phase 5 (recorded now, acted on later)

SEBI's retail algorithmic-trading framework became **fully mandatory on
1 April 2026**. Retail algo strategies must be **registered with the exchange
through the broker** and routed via approved systems; API-based strategies count
as algos, and algo providers must empanel with exchanges (brokers are
Principals, providers are Agents).

**This does not affect Phases 0–4** — backtesting, paper trading and historical
data access are untouched. It means Phase 5's "deploy live" is a *compliance*
workflow (submit the strategy through Dhan's registered algo route), not merely
an API call. The design keeps live execution behind an adapter boundary so this
can be added without reworking anything.

---

## 3. Architecture

```
                    ┌───────────────────────────────────┐
   Dhan API ───────►│  providers/dhan.py                │
   (90-day pages)   │   paging · rate-limit backoff     │
                    │   security-ID mapping             │
                    └────────────────┬──────────────────┘
                                     │  canonical DataFrame
   yfinance ───────►providers/       │  (UTC index, ohlcv)
   (fallback,       yfinance.py ─────┤
    cross-check)                     ▼
                    ┌───────────────────────────────────┐
                    │  candle_store.py   ◄── THE SWAP   │
                    │   ensure_coverage()      POINT    │
                    │   get_candles()                   │
                    └────────────────┬──────────────────┘
                                     │
                    ┌────────────────▼──────────────────┐
                    │  Supabase: candles, coverage,     │
                    │  instruments, quality flags       │
                    └────────────────┬──────────────────┘
                                     │
              resample.py ───────────┤ 5-min ➜ 15/25/30/60-min
                                     ▼
                     backtest engine · paper engine
                     (Phases 2 & 4 — never touch a provider)
```

The critical property: **nothing downstream of `candle_store` knows which
provider produced the data, or whether the market is open.**

### 3.1 Modules

| Module | Responsibility | Depends on |
|---|---|---|
| `providers/base.py` | `CandleProvider` protocol: `fetch()`, `max_history_days()`, `requires_auth` | — |
| `providers/dhan.py` | Dhan adapter: 90-day paging, backoff, security IDs | `dhan_auth`, `instruments` |
| `providers/yfinance.py` | Existing provider, retained as fallback + cross-check | — |
| `dhan_auth.py` | TOTP → access token → auto-renew; cached with expiry | Supabase |
| `instruments.py` | Dhan security master → `NSE:RELIANCE` ↔ `securityId` | Supabase |
| `candle_store.py` | `ensure_coverage()`, `get_candles()` — the repository | providers, Supabase |
| `resample.py` | Session-anchored 5-min → higher timeframes (pure) | — |
| `data_quality.py` | Split detection, gap detection, OHLC sanity (pure) | — |

Each module has one clear purpose and can be understood and tested in
isolation. `candle_store` is the only module the rest of the platform imports.

### 3.2 Resampling: session-anchored, not clock-anchored

Higher timeframes are built by grouping 5-min candles **from the 09:15 IST
session open**, not from clock boundaries.

This is a correctness requirement learned from a real defect: Yahoo's 30-minute
bars start at 09:00, so the first bar of each day contains only 09:15–09:30 —
15 minutes of data in a bar labelled 30. Anchoring to the session makes that
class of bug impossible.

Aggregation rule per bucket: `open` = first, `high` = max, `low` = min,
`close` = last, `volume` = sum. A bucket with no underlying candles produces no
row (never a synthesised one).

### 3.3 Daily candles stored separately

Daily data is **not** resampled from 5-min. It is fetched and stored directly
because Dhan's daily feed is **corporate-action adjusted** and reaches back to
**inception** — both cleaner and far longer than anything derivable from the
intraday base.

---

## 4. Data model

```sql
-- Symbol master, mapping our notation to Dhan's identifiers.
create table instruments (
    id                 bigserial primary key,
    symbol             text not null,          -- 'NSE:RELIANCE'
    exchange           text not null,          -- 'NSE' | 'BSE'
    tradingsymbol      text not null,          -- 'RELIANCE'
    dhan_security_id   text not null,
    dhan_segment       text not null,          -- e.g. 'NSE_EQ'
    name               text,
    instrument_type    text not null default 'EQUITY',  -- EQUITY | INDEX
    lot_size           integer,
    is_active          boolean not null default true,
    refreshed_on       date not null,
    constraint instruments_symbol_key unique (symbol)
);

-- Named universes (Nifty 50/100, custom). Populated in Phase 1.
create table symbol_groups (
    id          bigserial primary key,
    name        text not null unique,          -- 'NIFTY50'
    description text,
    is_system   boolean not null default false,-- system lists vs user lists
    created_at  timestamptz not null default now()
);

create table symbol_group_members (
    group_id      bigint not null references symbol_groups(id) on delete cascade,
    instrument_id bigint not null references instruments(id)   on delete cascade,
    primary key (group_id, instrument_id)
);

-- The candle store. Prices are numeric to avoid float drift in P&L maths.
create table candles (
    instrument_id bigint      not null references instruments(id) on delete cascade,
    timeframe     text        not null,        -- '5m' (base) | 'day'
    ts            timestamptz not null,        -- candle START, UTC
    open          numeric(14,4) not null,
    high          numeric(14,4) not null,
    low           numeric(14,4) not null,
    close         numeric(14,4) not null,
    volume        bigint        not null,
    primary key (instrument_id, timeframe, ts)
);

-- What is ACTUALLY cached. Prevents refetching and, more importantly,
-- prevents ever claiming coverage we do not have.
create table candle_coverage (
    instrument_id     bigint      not null references instruments(id) on delete cascade,
    timeframe         text        not null,
    first_ts          timestamptz not null,
    last_ts           timestamptz not null,
    source            text        not null,    -- 'dhan' | 'yfinance'
    last_refreshed_at timestamptz not null default now(),
    primary key (instrument_id, timeframe)
);

-- Detected problems, surfaced for review — never silently corrected.
create table data_quality_flags (
    id            bigserial primary key,
    instrument_id bigint not null references instruments(id) on delete cascade,
    timeframe     text   not null,
    flag_type     text   not null,             -- 'suspected_split' | 'session_gap' | 'ohlc_invalid'
    ts            timestamptz not null,
    detail        jsonb  not null,
    resolved      boolean not null default false,
    created_at    timestamptz not null default now()
);

-- Provider credentials with expiry. Values encrypted; never logged or displayed.
create table provider_tokens (
    provider     text primary key,             -- 'dhan'
    access_token text not null,
    expires_at   timestamptz not null,
    updated_at   timestamptz not null default now()
);

create index candles_instrument_tf_ts_idx on candles (instrument_id, timeframe, ts desc);
create index quality_unresolved_idx on data_quality_flags (instrument_id) where not resolved;
```

**RLS:** every table gets RLS enabled. The dashboard's anon key receives
read-only SELECT on `instruments`, `symbol_groups`, `symbol_group_members`,
`candles`, `candle_coverage`, and `data_quality_flags`. `provider_tokens`
receives **no anon policy at all** — it holds a live credential, exactly as
`daily_token` does today.

**Contiguity assumption:** `candle_coverage` models a single contiguous
`[first_ts, last_ts]` range per (instrument, timeframe). `ensure_coverage`
therefore only ever extends the range at either end and never leaves interior
holes; a requested range that would create a gap is fetched in full from the
existing boundary. This keeps coverage logic simple and verifiable.

---

## 5. Data flows

### 5.1 Backfill / ensure coverage

```
ensure_coverage(symbol, '5m', from, to)
  ├─ read candle_coverage
  ├─ compute the missing range(s) — nothing to do if already covered
  ├─ clamp request to provider limits (Dhan: 5 years intraday)
  ├─ page the provider in ≤90-day windows, with backoff between pages
  ├─ validate each page (OHLC sanity; flag anomalies)
  ├─ upsert into candles (idempotent on the composite PK)
  └─ extend candle_coverage — ONLY over the range genuinely retrieved
```

If a page fails after retries, everything already fetched is still committed and
coverage advances only to the last good candle. **A partial fetch must never be
recorded as complete**, because silently-incomplete history would corrupt every
backtest built on it while looking perfectly healthy.

### 5.2 Reading for a backtest

Requested timeframes route to one of two paths. `resample.py` owns the mapping
`requested timeframe → stored timeframe`:

**Intraday (`15m`, `25m`, `30m`, `60m`) — derived from the 5-min base:**
```
get_candles(symbol, '15m', from, to)
  ├─ ensure_coverage(symbol, '5m', from, to)   # no-op when cached
  ├─ read the 5-min base rows from Supabase
  ├─ resample 5m → 15m, session-anchored
  └─ return the canonical DataFrame (UTC index, ohlcv floats)
```

**`5m` — served directly from the base, no resampling.**

**`day` — served directly from stored daily rows, never resampled:**
```
get_candles(symbol, 'day', from, to)
  ├─ ensure_coverage(symbol, 'day', from, to)
  ├─ read the daily rows from Supabase
  └─ return the canonical DataFrame
```

Daily deliberately bypasses the intraday base because Dhan's daily feed is
corporate-action adjusted and reaches back to inception (§2.1, §3.3). A
requested timeframe outside `{5m, 15m, 25m, 30m, 60m, day}` is rejected with a
clear error rather than silently approximated.

No network call when data is cached — which is precisely what makes backtests
runnable at 2 a.m. on a Sunday.

### 5.3 Token lifecycle

```
get_valid_token()
  ├─ read provider_tokens
  ├─ if >30 min of validity remains → use it
  ├─ else try /v2/RenewToken
  └─ if renewal fails → generate fresh via TOTP
```

Renewal is lazy (only when needed) and the result is shared through Supabase, so
concurrent GitHub Actions runs do not each mint a token.

### 5.4 Refresh (the UI button, Phase 3)

"Refresh" extends coverage from `last_ts` to now and re-runs. Because only the
delta is fetched, a refresh is cheap even on years of history.

---

## 6. Data quality

Three checks run on every fetched page. All findings are **flagged for review,
not auto-corrected** — a silently "fixed" price is indistinguishable from real
data and would quietly invalidate results.

| Check | Method | Action |
|---|---|---|
| **Suspected split/bonus** | Overnight gap beyond a threshold in intraday data that is absent from Dhan's *adjusted* daily series | flag `suspected_split` |
| **Session gap** | A trading day (per the NSE calendar) with no candles, or a session materially short of its expected 75 five-minute bars | flag `session_gap` |
| **OHLC sanity** | `high ≥ max(open, close)`, `low ≤ min(open, close)`, `volume ≥ 0`, all prices > 0 | flag `ohlc_invalid`, reject the row |

Cross-checking intraday against the adjusted daily series is what makes split
detection reliable without a separate corporate-actions feed.

---

## 7. Error handling

| Failure | Behaviour |
|---|---|
| Token expired / renewal fails | Attempt renew → TOTP regeneration → then a clear error naming the exact env var or secret to check. Never retried blindly. |
| Rate limited | Exponential backoff within a page; on exhaustion, commit what succeeded and record honest coverage |
| Provider returns empty | Distinguish *"no data exists"* from *"request exceeded provider limits"* and say which — an empty frame must never be mistaken for "no signals" |
| Unknown symbol | Fail naming the symbol and where to fix it |
| Partial backfill | Commit fetched candles; advance coverage only to the last good candle |
| Supabase unreachable | Fail loudly; no silent degradation to a partial cache |

Secrets never appear in errors, logs, audit rows, or the UI.

---

## 8. Testing

**Offline (no network, no credentials) — the bulk of the suite**
- Resampling: 5m → 15/25/30/60m against hand-computed buckets; session-anchor
  boundary cases; the 09:15 first-bar case explicitly
- Coverage arithmetic: missing-range computation, extension at both ends,
  partial-fetch honesty, idempotent re-runs
- Quality checks: split detection, gap detection, OHLC rejection
- Token lifecycle: renewal thresholds and fallback ordering, with a fake clock
- Provider adapters: recorded Dhan fixtures, including a 90-day page boundary

**Live (opt-in, one command)**
- One smoke test fetching a small window and asserting the canonical shape
- A cross-check comparing Dhan vs yfinance on an overlapping window, reporting
  divergence rather than asserting equality — a data-truth canary

**Migration safety**
- Applied via `apply_migration`; additive only. Existing tables (`strategies`,
  `positions`, `trades`, `run_audit`, `backtest_results`) are untouched by this
  phase.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Dhan changes API/limits | Provider isolated behind `CandleProvider`; yfinance remains a working fallback |
| Storage outgrows free tier | On-demand fetching defers it; repository interface makes approach B (~240 MB) a single-module swap; Supabase Pro is $25/mo |
| Intraday data not split-adjusted | Detected by cross-checking the adjusted daily series; flagged for review |
| TOTP secret compromise | Order placement blocked at broker level (no whitelisted IP); secret in Secrets/Vault, never logged |
| Backfill is slow (90-day pages) | On-demand fetching means you only ever wait for symbols you actually test; nightly job extends active symbols |

---

## 10. Definition of done

- [ ] `NSE:RELIANCE` 5-min candles for 2 years are stored and re-readable
- [ ] A second `ensure_coverage` call for the same range makes **zero** network calls
- [ ] 15/30/60-min resampling matches hand-computed values, session-anchored
- [ ] Reading candles works with the market closed (weekend/holiday) — verified
- [ ] A forced mid-backfill failure leaves coverage honest, not overstated
- [ ] Token auto-renews unattended across an expiry boundary
- [ ] Quality flags are raised on a known split and a known holiday gap
- [ ] Secrets absent from all logs, errors, audit rows, and UI
- [ ] Full offline test suite green

---

## 11. Sources

- [Dhan — Historical Data API](https://dhanhq.co/docs/v2/historical-data/) · [timeframes available](https://dhan.co/support/platforms/dhanhq-api/what-timeframe-data-is-available-through-dhan-s-historical-data-apis/)
- [Dhan — Authentication](https://dhanhq.co/docs/v2/authentication/) · [token validity](https://dhan.co/support/platforms/dhanhq-api/what-is-the-maximum-validity-of-an-api-access-token-in-dhan-apis/) · [Renew Token](https://docs.dhanhq.co/api/v2/authentication/renew-token)
- [Dhan — corporate-action adjustment](https://dhan.co/support/platforms/dhanhq-api/is-the-historical-data-from-dhan-s-data-api-adjusted-for-corporate-actions-like-bonuses-and-splits/)
- [Dhan — static IP scope](https://dhan.co/support/platforms/dhanhq-api/i-have-added-my-static-ip-s-on-dhan-web-but-my-orders-are-getting-rejected-stating-dh-905-invalid-ip-while-placing-them-through-the-api-what-should-i-do/) · [pricing](https://dhan.co/pricing/)
- [Upstox — Historical Candle V3](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/)
- [Breeze (ICICI Direct) API](https://www.icicidirect.com/futures-and-options/api/breeze)
- [TrueData pricing](https://www.truedata.in/price)
- SEBI retail algo framework: [FinSec Law analysis](https://www.finseclaw.com/article/finsec-tracker-on-sebi-issues-guidelines-on-retail-participation-in-algorithmic-trading) · [practical guide](https://cskruti.com/sebis-2025-algo-trading-framework-a-practical-guide/)
- [QuantStats metrics reference](https://github.com/ranaroussi/quantstats) (informs Phase 2)
