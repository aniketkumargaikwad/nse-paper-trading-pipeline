# F&O Step 3b — Intraday Options Backtests — Design

**Status:** Approved in conversation 19 Oct 2026 · Extends `2026-10-02-fno-options-design.md` §4, §6, §7

## 0. In plain words

- Intraday strategies (decided minute by minute, closed by an exit time such
  as 15:15) are tested on the real option minutes from Upstox (NIFTY from
  3 Oct 2024, BANKNIFTY from 9 Oct 2024) and the index / India VIX minutes
  from Dhan.
- **Owner's decision (19 Oct 2026):** intraday strategies need **12 months**
  of history to pass, not 24. With the exam year sealed, real option minutes
  give about 12 months of training today. Every other pass rule is unchanged:
  worst single day ≥ −10%, worst fall ≤ 30%, average month > 0, the trade
  frequency rule, beating holding the index in half its years, and the luck
  test (t ≥ 2).
- The engine reuses the daily engine's pieces: strike rules, fills with half
  the spread, dated NSE charges, SPAN-style margin, whole-set sizing, the
  no-arbitrage limits, the report.

## 1. A trading day

- A strategy has `entry.window` (from, to) and `exit.time`.
- Every finished index minute whose start lies in `[from, to)` is a decision
  point. If the entry condition holds (and fewer than `reentry.max_per_day`
  entries were made today, and nothing is open), the expiry and strikes are
  chosen from the chain *at that minute*, the position is sized, and each leg
  fills at the **next minute's open**, paying half the spread and the charges
  in force that day.
- **No phantom fills.** A leg whose contract has not traded in the 5 minutes
  up to the decision, or has no candle in the fill minute, skips the whole
  entry, recorded with the reason.
- **Thin market.** A leg is "thin" when fewer than 100 contracts traded in
  the 5 minutes before its fill; thin fills pay double the half-spread.
- **Exit time.** An open position closes at the open of the `exit.time`
  minute (e.g. the 15:15 candle). Never held overnight, expiry day included.

## 2. What a decision may know (point in time)

At a decision on minute `t` of day `D`:

- **The previous session's daily facts** (ATM IV, IV rank, PCR, max pain,
  skew, basis, FII positioning…): D's own are only known at D's close.
- **Live:** India VIX's minute close at `t` (`vix`) and its change from the
  previous session's close (`vix_chg_pct`); the index's own minute bars up to
  `t` (indicators such as `sma(20)` run on index minutes, with the previous
  five sessions' minutes as warm-up).
- **The day so far:** `day_open` (the index's 09:15 open) and the opening
  range `or_high` / `or_low` (the index's high and low over 09:15–09:44),
  known from the 09:45 minute on and missing before it.
- **Calendar facts for D**, known in advance: `weekday`, `event_today`,
  `sessions_to_event`, `dte` / `expiry_dte` (calendar days to expiry, 0 on
  expiry day).
- **The chain at `t`**: every contract of the chosen expiry with a candle at
  or before `t` today, priced at its latest close, with IV and delta from
  Black-76 using time to the expiry's close (15:30 on expiry day) in calendar
  years — so expiry-day (0 DTE) deltas exist. The forward is put–call parity
  at the strike where call and put are closest, else the index minute.
- **Which contracts exist** comes from the previous session's NSE file (the
  expiry list and strikes are known before the open); prices never do.

A test plants a later price in the data and asserts no decision moves.

## 3. Stops and targets, each minute

For every minute a position is open (from the fill minute on):

- The position is valued three ways: at each leg's **open**, at its **worst**
  (sold legs at their high, bought legs at their low) and at its **best**.
- **Stop first:** if the open is already past the stop, exit at the opens
  (the gap is paid). Else, if the worst breaches it, exit at the stop level —
  each leg priced between its open and its worst in the proportion that puts
  the position exactly at the stop.
- **Then target**, the same way with the best.
- A strategy's own `exit.when` is evaluated on finished minutes and fills at
  the next open.
- Leg prices used together are held to the position's no-arbitrage limits.
- A leg with no candle in a minute keeps its last price (high = low = open =
  close = last).
- Stops apply to the **whole position**; per-leg stops are not in this
  version.

## 4. Data coverage

Minutes exist for strikes within ±5% of the index and for each expiry's final
10 (weekly) or 20 (monthly) sessions. A day whose chosen expiry has no minute
file, or no candles in the decision window, is skipped and recorded
("no minute data"). Upstox has nothing for NIFTY 26 Dec 2024 and BANKNIFTY
1 Oct 2024; days needing them are skipped.

## 5. Results and the pass rule

- Same report as the daily engine (months, worst day, splits, shock days),
  with intraday notes: fills at the next minute's open; stops on minute
  high/low, worst case first; margin uncalibrated; history starts Oct 2024.
- `fno/rules.py` holds the options pass rule (design §6) with the minimum
  history set by mode: 24 months positional, **12 months intraday**. The atlas
  table shows whether each strategy passes, and if not, why.

## 6. The intraday atlas

NIFTY and BANKNIFTY, ₹5 lakh, a quarter of the balance in margin, training
years only:

| Name | Idea |
|---|---|
| `straddle-0920` | Sell ATM call and put at 09:20; stop 30%, target 50%; out 15:15 |
| `strangle-0930` | Sell 20-delta call and put at 09:30; stop 50%, target 50% |
| `expiry-day-straddle` | The 09:20 straddle on expiry day only (`expiry_dte == 0`) |
| `iron-fly` | Sell ATM, buy wings 2% out of the money, at 09:20; stop 50%, target 40% |
| `orb-call` / `orb-put` | Buy an ATM call (put) when a minute closes above `or_high` (below `or_low`), 09:45–13:00; stop 30%, target 60% |

## 7. Not in this step

Per-leg stops, adjustments, model-priced history before Oct 2024 (option C,
declined), live paper trading (step 5).
