# F&O Options Research and Paper Trading — Design

**Status:** Approved in conversation, awaiting written review · **Date:** 2026-10-02

Make the platform able to research, backtest and paper-trade NIFTY and
BANKNIFTY option strategies, using the option chain, India VIX and the other
market facts a real options trader decides with — always as they stood at the
moment of the decision, never later.

---

## 0. In plain words

- **Two kinds of option strategy.** *Intraday*: decided minute by minute,
  closed by 3:15 pm. *Positional*: decided once a day at the close, held for
  days or to expiry.
- **Free data does most of the work.** NSE publishes every F&O contract's
  end-of-day figures free, back to at least 2016. Positional strategies are
  tested on those.
- **Minute data is paid for, by the owner's choice.** Upstox Plus (currently
  free to activate) supplies 1-minute prices for expired NIFTY and BANKNIFTY
  options; a renewed Dhan (₹499 + GST a month) supplies index and stock
  minute data. Intraday strategies are tested on those.
- **The backtest replays the option chain as it was** at each decision, with
  NSE's real charges for that date, realistic spreads, margin, lot sizes and
  expiry settlement.
- **Every result is split by market conditions** — VIX level, trend, days to
  expiry, how expensive options were, event days — and lists its worst days.
- **The pass/fail rule is the strict one approved on 26 Sep 2026**, plus one
  options rule: no single day may lose more than 10% of capital.
- **The AI researches options on Monday, Wednesday and Friday**, stocks on
  Tuesday, Thursday and Saturday. It starts from an *atlas* of classic option
  strategies measured first.
- **Strategies that pass can be paper-traded** on live data once the owner
  switches them on. **No real orders**: there is no code path to any order
  endpoint, as today.
- **The owner's manual steps** (accounts, keys) are listed in §11 and handed
  over as a step-by-step guide.

Decisions taken in the conversation that shape everything below:

| Decision | Choice |
|---|---|
| Data budget | Pay for minute data now (changes the zero-budget rule for this) |
| Minute data source | Upstox Plus for options; renewed Dhan for indexes and stocks |
| Underlyings | NIFTY and BANKNIFTY (minute + daily); other NSE indexes daily only |
| Minute-data storage | Cloudflare R2, free tier, private bucket |
| "Environment" | The market facts a strategy decides with, replayed point-in-time |
| How far | Research + paper trading; no real orders |
| AI schedule | Options Mon/Wed/Fri, stocks Tue/Thu/Sat, nothing Sunday |
| Account | ₹5,00,000 starting capital; worst single day ≤ 10% |

---

## 1. Architecture

A separate options engine beside the stock engine, sharing the plumbing.
Options differ in shape — several legs, strikes chosen relative to the price,
expiries, lots, margin, settlement — and bending the single-instrument stock
engine to fit would give misleading results. Shared: candle storage backends,
the cost-model pattern, the expression evaluator, the research loop, the
picker's statistics, notifications and the dashboard shell.

New package `fno/`, each module with one job:

| Module | Job |
|---|---|
| `fno/nse_files.py` | Download and parse NSE's daily F&O files, both formats |
| `fno/upstox.py` | Upstox client: expired contracts, minute candles, live quotes |
| `fno/store.py` | Read/write option data in R2 (and a local mirror) |
| `fno/contracts.py` | Contract identity, expiry calendar, lot sizes in force on a date |
| `fno/pricing.py` | Black-76 implied volatility and Greeks |
| `fno/chain.py` | `chain_at(underlying, ts)`: the option chain as it stood at `ts` |
| `fno/features.py` | VIX, ATM IV, IV rank, PCR, max pain, skew, expected move, DTE, events |
| `fno/strategy.py` | The options strategy format: parse and validate |
| `fno/costs.py` | NSE F&O charges with effective dates |
| `fno/margin.py` | Margin estimate, calibrated against the broker's calculator |
| `fno/engine.py` | The backtester: legs, fills, exits, expiry settlement |
| `fno/account.py` | Capital, lot sizing by margin, monthly returns, worst day |
| `fno/report.py` | Splits by market condition, worst-days table |
| `fno/paper.py` | Paper-trading runs on live data |

---

## 2. Data

### 2.1 Sources

| What | From | Reaches back |
|---|---|---|
| Every contract, end-of-day: OHLC, settlement, OI, volume; since 8 Jul 2024 also lot size and underlying price | NSE daily F&O files, free | 2016-01-01 (verified) |
| 1-minute OHLC + volume + OI for expired NIFTY/BANKNIFTY options | Upstox Plus, Expired Instruments API | as far as Upstox serves; measured at the start of the build and stated on every report |
| 1-minute OHLC for live contracts (daily top-up, paper trading) | Upstox historical/intraday candles + market quotes | — |
| India VIX daily | Yahoo `^INDIAVIX` | 2008-03-03 (verified) |
| India VIX 1-minute | Upstox | as served |
| NIFTY / BANKNIFTY index 1-minute; stock 1/5-minute | Dhan, renewed | 2017-04-03 |
| FII / DII / pro / client F&O open interest | NSE daily participant-OI file, free | as published |
| Event days | `fno/events.yaml` in the repo: Union Budget, RBI policy, general-election results, plus owner additions | — |
| Lot sizes | NSE file from 8 Jul 2024; before that derived from futures traded value (§2.3) | 2016 |
| Charges | `fno/charges.yaml`: each rate with its effective date, from NSE/SEBI circulars | 2016 |
| Risk-free rate | `fno/rates.yaml`: yearly 91-day T-bill yield from RBI | 2016 |

The two NSE file formats, both verified on 2 Oct 2026:

- **Old** (`fo<DD><MON><YYYY>bhav.csv.zip`, up to 5 Jul 2024): `INSTRUMENT,
  SYMBOL, EXPIRY_DT, STRIKE_PR, OPTION_TYP, OPEN, HIGH, LOW, CLOSE,
  SETTLE_PR, CONTRACTS, VAL_INLAKH, OPEN_INT, CHG_IN_OI, TIMESTAMP`.
- **UDiFF** (`BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip`, from 8 Jul
  2024): adds `UndrlygPric`, `SttlmPric`, `NewBrdLotQty`; instrument types
  `IDO` (index option), `IDF` (index future), `STO`, `STF`.

Both are normalised to one schema: `trade_date, underlying, instrument
(OPT|FUT), expiry, strike, option_type (CE|PE|null), open, high, low, close,
settle, volume_contracts, oi, oi_change, lot_size, underlying_price`.

### 2.2 What is stored

- **Daily:** every NIFTY and BANKNIFTY option and future, all strikes and
  expiries; FINNIFTY, MIDCPNIFTY and NIFTYNXT50 the same, for positional
  research only. About 13 million rows since 2016, ~200 MB of Parquet.
- **Minute:** for each NIFTY and BANKNIFTY expiry, every strike that was
  within ±5% of the index's daily close on any day of the expiry's final 10
  trading days (weekly cycles) or final 20 (monthly cycles), for those days.
  ~2–2.5 GB of Parquet over five years.
- **Layout in R2** (private bucket): `fno/daily/<UNDERLYING>/<YEAR>.parquet`,
  `fno/minute/<UNDERLYING>/<EXPIRY>.parquet`, `fno/vix/{daily,minute}/...`,
  `fno/participants/<YEAR>.parquet`. A local mirror with the same layout is
  what every reader actually opens; the runner fills it from R2.

### 2.3 Lot sizes before July 2024

The old file has no lot size. For each underlying and day, take the
near-month future and compute `VAL_INLAKH × 100000 / (CONTRACTS × CLOSE)`.
Average traded price differs from the close by under a few percent, so the
result rounds to the true lot. The derived history is written once to
`fno/lot_sizes.yaml`, each change checked by hand against the NSE circular
that announced it, and from then on read, never re-derived. From 8 Jul 2024
`NewBrdLotQty` is used directly, and the two are checked to agree where they
overlap.

### 2.4 Calculated facts (`fno/features.py`)

All computed at a decision moment from data at or before it:

- **Per contract:** implied volatility and delta, gamma, theta, vega, by
  Black-76 with the forward from the same-expiry future, or, for weekly
  expiries with no future, from put–call parity at the ATM strike. Rate from
  `rates.yaml`.
- **Per chain:** ATM strike, ATM IV, IV rank and IV percentile (vs the past
  252 sessions), PCR by OI and by volume, max pain, ATM straddle price and the
  expected move it implies, 25-delta skew, highest-OI call and put strikes,
  futures basis, days to expiry.
- **Market:** India VIX level and change, underlying trend inputs (the
  existing indicators on the index's own candles), weekday, event-today and
  event-this-expiry flags, FII index-futures net position (end-of-day only).

### 2.5 The chain as it was (`fno/chain.py`)

`chain_at(underlying, ts, expiry=None) -> ChainSnapshot` returns every
contract of the requested expiry with: last traded price and its timestamp
(so staleness is visible), volume so far, OI, IV, Greeks, plus spot, future
price and days to expiry. In daily mode `ts` is the session close and the
snapshot comes from the daily file. Nothing after `ts` can enter a snapshot;
a test feeds data with a deliberately planted future value and asserts it
never appears.

### 2.6 Updates and checks

- **Daily, after the close** (GitHub workflow `fno-daily`, ~19:00 IST): that
  day's NSE file, participant OI, VIX, and Upstox minute candles for the
  contracts in range; written to R2.
- **Backfill** (workflow `fno-backfill`, started by hand, resumable): the
  NSE files since 2016 and the Upstox minute history, in chunks that survive
  a cancelled run.
- **Checks on every write:** minutes missing inside a session; contracts
  with no trade; a contract's last minute close against NSE's daily close for
  that contract (beyond a tolerance, flagged); lot size in the file against
  `lot_sizes.yaml`. Issues go to a report beside the data,
  `data/fno/quality/<YEAR>.csv`: `data_quality_flags` requires a stock or
  index `instrument_id` and allows only three flag types, so it cannot hold
  a contract. Nothing is filled in or invented.

### 2.7 Stocks, as a side effect

With Dhan renewed, the August–September 2026 stock sessions that Yahoo
served without their closing candles are re-fetched from Dhan. Re-opening
intraday stock research is a separate decision for the owner; this design
only makes the data whole.

---

## 3. The options strategy format (`fno/strategy.py`)

A YAML document, written by the AI or by hand:

```yaml
version: options/1
name: weekly-20-delta-strangle
underlying: NIFTY            # NIFTY | BANKNIFTY; others positional only
mode: intraday               # intraday | positional
expiry: {cycle: weekly, which: 0}         # 0 = nearest, 1 = the one after
entry:
  window: {from: "09:30", to: "10:00"}    # intraday only
  when: "vix > 11 and vix < 16 and not event_today and dte <= 4"
legs:
  - {side: sell, type: CE, strike: {delta: 0.20}, lots: 1}
  - {side: sell, type: PE, strike: {delta: -0.20}, lots: 1}
exit:
  target_pct: 50             # of net premium collected (credit) or paid (debit)
  stop_pct: 100              # net premium moved against by this much
  time: "15:15"              # intraday
  days_before_expiry: 0      # positional
  when: "vix_chg_pct > 15"   # optional extra condition
reentry: {max_per_day: 1}
```

**Strike rules:** `{atm: n}` (n strikes from ATM), `{otm_pct: x}`,
`{delta: d}`, `{premium: rupees}` (nearest), `{max_oi: true}` (highest-OI
strike on that side), `{from_leg: i, offset: points}` (wings relative to
another leg).

**Expressions** in `when` reuse the existing evaluator, with the §2.4 facts as
named inputs (`vix`, `vix_chg_pct`, `atm_iv`, `iv_rank`, `pcr_oi`, `pcr_vol`,
`max_pain`, `straddle`, `expected_move_pct`, `skew_25d`, `dte`, `weekday`,
`event_today`, `basis_pct`, `fii_index_fut_net`) and the underlying's own
candles (`daily.sma(200)`, `rsi(14)`, …).

**Out of version 1:** adjustments (rolling a threatened leg), more than one
underlying per strategy, stock options. Each can be added once the basics are
proven.

---

## 4. The backtest engine (`fno/engine.py`)

- **Decisions and fills.** Intraday: decide on a finished minute, fill each
  leg at the next minute's open. Positional with minute data: decide at
  15:15, fill at 15:16. Positional in the daily-only years: decide at the
  close, fill at the next session's open price for that contract.
- **No phantom fills.** A leg whose contract has not traded within the last
  five minutes (intraday) or did not trade that session (daily) is not
  entered; the whole entry is skipped and recorded with the reason.
- **Spreads.** Every fill pays an estimated half-spread: the larger of one
  tick (₹0.05) and 0.5% of the premium, doubled when the contract traded
  fewer than 100 contracts that session. These starting values live in
  config and are replaced by what paper trading measures (§8).
- **Exits.** Target and stop are checked against each minute's high and low,
  stop first, gaps filling at the open — the stock engine's worst-case rule.
  In daily-only years they are checked on closing prices, and reports for
  those years say so.
- **Charges** (`fno/costs.py`): STT, exchange transaction charge, SEBI fee,
  stamp duty, GST and ₹20 brokerage per order, each at the rate in force on
  the trade date from `charges.yaml` (STT on options changed on 1 Oct 2024).
- **Expiry.** Positions held to expiry settle at NSE's settlement price;
  in-the-money options pay intrinsic value and STT on exercise; out-of-the-
  money options expire worthless.
- **Margin** (`fno/margin.py`): short options use a SPAN-style estimate — a
  scan range on the underlying that widens with India VIX, plus NSE's
  exposure margin; defined-risk spreads use their maximum loss as a floor;
  long options use the premium paid. The estimate is calibrated against the
  broker's margin calculator on the live chain, the calibration error is
  stored, and every report states it.
- **Account** (`fno/account.py`): ₹5,00,000 by default, sized in whole lots
  at the lot size in force that day, never committing more margin than the
  balance. Monthly returns compound on the balance (the 22 Sep 2026 fix).

---

## 5. What each result shows (`fno/report.py`)

- Average and median month, months up, worst fall, **worst single day**,
  trades per month, average days held, total charges.
- **Split by market condition:** India VIX calm (< 13) / normal (13–18) /
  fearful (> 18); trend rising / falling / sideways; days to expiry; IV rank
  in thirds; event days vs other days.
- **Worst days:** the ten worst, plus named shock days where in range —
  12 Mar 2020, 23 Mar 2020, 24 Feb 2022, 4 Jun 2024.
- The minute-data start date and the margin-calibration error.

---

## 6. Pass/fail (`research/picker.py`, options branch)

A strategy qualifies only if **all** hold:

- at least 24 months of history;
- worst fall ≤ 30% of capital;
- **worst single day ≥ −10% of capital**;
- average month > 0;
- at least 10 trades a month, or an average month at the owner's target;
- beat **holding the underlying index on the same capital** in at least half
  its years;
- passes the luck test (monthly lead over that holding, t ≥ 2).

The **exam year** is the last 365 days, sealed exactly as for stocks: nothing
the AI sees is built from it.

---

## 7. AI research for options

- **Options atlas first.** Measured across all history before the AI runs,
  split by market condition: short straddle, short strangle at 15/20/30
  delta, iron condor, bull put and bear call spreads, long straddle before
  events, and the same on BANKNIFTY. Stored like the stock atlas and shown to
  the AI.
- **The AI's instructions** add the options format, the facts it may use,
  charges, margin, the pass/fail rule and the atlas — and nothing from the
  exam year (the existing leak test covers the new prompts).
- **Checker:** parses the document and refuses, with a reason the AI can act
  on, anything the data cannot test — a strike rule that lands outside the
  minute data's ±5% band in intraday mode, an unknown fact name, a weekly
  expiry on an underlying that no longer has weeklies on that date.
- **Schedule:** the existing daily research job runs the options track on
  Monday, Wednesday and Friday and the stock track on Tuesday, Thursday and
  Saturday, selected by weekday.
- **Report:** the daily email/Telegram message gains an options section.

---

## 8. Paper trading for options (`fno/paper.py`)

- **Only strategies the owner has switched on**, in the dashboard, after they
  pass. Everything starts paused.
- **Intraday:** a GitHub workflow runs every 15 minutes, 09:15–15:45 IST,
  Monday to Friday. Each run fetches the 1-minute candles since the last
  processed minute and replays them one by one, so entries, stops and exits
  happen on the same minute the backtest would choose, however late the run
  wakes. It is stateless and idempotent, like the stock paper engine:
  unique keys make a re-run change nothing.
- **Positional:** one run after the close.
- **Real spreads:** every simulated fill also records the live bid and ask at
  that moment from Upstox market quotes. These samples refine the backtest's
  spread estimate.
- Tables: `options_positions`, `options_trades`, `options_paper_runs`,
  `options_spread_samples` (new migration).

---

## 9. Dashboard

Three new pages:

- **Option chain:** live, or replayed at any past date and minute — prices,
  IV, OI and OI change, PCR, max pain.
- **Options research:** each run's results, the split by market condition,
  worst days, and a payoff chart of the strategy at entry.
- **Options paper trading:** open positions, P&L, Greeks, margin used, and a
  switch per strategy.

---

## 10. Errors and data gaps

- A missing NSE file (holiday, late publication) is retried on the next run;
  a holiday is recognised from the existing market calendar.
- Upstox or Dhan failures mark the run failed with the reason; nothing is
  half-written (each file is written whole or not at all).
- Gaps in minute data are recorded as data-quality flags; a strategy whose
  decision window falls in a gap skips that day and the skip is reported.
- Expired or missing credentials stop the run with a message saying which
  secret to renew, as `research.brain` does for the Claude token.

---

## 11. The owner's manual steps

Handed over as a step-by-step guide at the end of the build:

1. **Upstox:** open or log in to an account, activate **Upstox Plus**, create
   an API app, and generate a long-lived **analytics token** for market data.
   If that token turns out not to cover the expired-instruments endpoints,
   the guide covers the daily-login alternative.
2. **Dhan:** renew the Data API subscription; the existing secrets are
   reused.
3. **Cloudflare:** create an account, an R2 bucket, and an API token with
   read/write on that bucket.
4. **GitHub secrets:** `UPSTOX_ANALYTICS_TOKEN`, `R2_ACCOUNT_ID`,
   `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`; the Dhan secrets
   if they need refreshing.
5. **Start the backfill** once (one button in GitHub Actions).
6. **Switch on** strategies for paper trading when wanted.
7. **Review `fno/events.yaml`** once a year and add events.

---

## 12. Build order

Each step is finished, tested and checked against real data before the next,
and each gets its own implementation plan in `docs/superpowers/plans/`.

1. **Daily data:** NSE files (both formats) for all five indexes since 2016,
   lot-size history, VIX, participant OI. Needs
   nothing from the owner; built locally first, published to R2 when the
   bucket exists.
2. **Engine + atlas on daily data:** charges and rates tables (with the
   code that reads them), pricing, chain, features, strategy
   format, costs, margin, engine, account, reports; the options atlas for
   positional strategies since 2016.
3. **Minute data:** Upstox and Dhan connections, backfill, daily top-up;
   intraday strategies and the intraday atlas.
4. **AI research for options:** prompts, checker, picker branch, schedule,
   report.
5. **Paper trading and dashboard pages.**

---

## 13. Testing

- Parsers: real NSE files from both formats, checked in as small fixtures.
- Lot sizes: the derived history matches every overlap day with
  `NewBrdLotQty`.
- Pricing: implied volatility and Greeks against published reference values;
  round-tripping price → IV → price.
- Point-in-time: a planted future value never reaches `chain_at` or any fact.
- Engine: hand-worked cases for fills, stops (stop before target), skipped
  illiquid legs, charges on each side of 1 Oct 2024, expiry settlement in and
  out of the money, margin limits on lot sizing.
- One golden backtest: a NIFTY short straddle over a few real expiries,
  worked by hand and frozen.
- Reconciliation: minute closes against NSE daily closes across the backfill.
- Paper engine: replaying the same minutes twice changes nothing.

---

## 14. Not in this design

Real orders of any kind; option adjustments; stock options; BSE (SENSEX)
options; multi-underlying strategies; re-opening intraday stock research
(the data is made whole, the decision is the owner's).
