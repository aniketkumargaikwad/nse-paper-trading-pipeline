# Options strategy format — options/1

The reference the options research AI writes against
(`docs/superpowers/specs/2026-10-19-fno-ai-research-design.md`). Parsed by
`fno/strategy.py`; anything it refuses comes back with the reason.

## Worked example

```yaml
version: options/1
name: weekly-20-delta-strangle      # replaced by the tool
underlying: NIFTY                   # replaced: every strategy is tested on NIFTY AND BANKNIFTY
mode: intraday                      # intraday | positional
expiry: {cycle: weekly, which: 0}   # 0 = nearest listed, 1 = the one after
entry:
  window: {from: "09:30", to: "10:00"}   # intraday only
  when: "vix > 11 and vix < 16 and not event_today and dte <= 4"
legs:
  - {side: sell, type: CE, strike: {delta: 0.20}, lots: 1}
  - {side: sell, type: PE, strike: {delta: -0.20}, lots: 1}
exit:
  target_pct: 50          # take profit at 50% of the net premium
  stop_pct: 100           # stop when the position has lost 100% of the net premium
  time: "15:15"           # intraday: the close-out minute
  when: "vix_chg_pct > 15"
reentry: {max_per_day: 1}
sizing: {max_margin_pct: 100}
```

Times are strings in quotes: YAML reads an unquoted 10:00 as the number 600.

## Keys

| key | meaning |
|---|---|
| `mode` | `intraday`: decided minute by minute, always closed by `exit.time` the same day. `positional`: decided at the daily close, held across days |
| `expiry.cycle` | `weekly` lands on the nearest listed expiry (NIFTY weeklies began Feb 2019; BANKNIFTY has had **only a monthly** since Nov 2024, so its "weekly" is its monthly) · `monthly` the month's last expiry |
| `expiry.which` | 0 nearest, 1 next. Intraday: at most 1 |
| `entry.window` | intraday: every finished index minute whose start is in [from, to) is a decision point |
| `entry.when` | optional condition; no condition = enter at the first decision point |
| `legs` | 1 to 4. `side` buy or sell, `type` CE or PE, `lots` whole sets of that leg |
| `exit.target_pct` / `stop_pct` | of the **net premium**: collected for a credit position, paid for a debit one. Checked on the whole position, stop first |
| `exit.time` | intraday only, required |
| `exit.days_before_expiry` | positional: close this many sessions before expiry (0 = hold to expiry, settled at NSE's price) |
| `exit.when` | optional extra exit condition |
| `reentry.max_per_day` | intraday: entries allowed per day (one position open at a time) |
| `sizing.max_margin_pct` | the share of the balance the position's margin may use; whole sets only. Missing = 25 positional, 100 intraday |

## Strike rules — exactly one per leg

| rule | picks |
|---|---|
| `{atm: n}` | n strikes from the money (0 = at the money; calls count up, puts down) |
| `{otm_pct: x}` | the strike nearest x% out of the money. Intraday: at most 5 |
| `{delta: d}` | the strike whose delta is nearest d (calls positive, puts negative) |
| `{premium: rupees}` | the strike whose price is nearest that many rupees |
| `{max_oi: true}` | the strike with the most open interest on that side |
| `{from_leg: i, offset: points}` | an earlier leg's strike plus `offset` index points (wings) |

Two legs on the same contract from opposite sides cancel, and the entry is
refused that day.

## Names an expression may use

Facts, computed point in time. In **positional** mode, the day's own facts
(known at the close you decide on). In **intraday** mode, the **previous
session's** facts, except where marked live.

| name | meaning |
|---|---|
| `vix` | India VIX (intraday: live, this minute) |
| `vix_chg_pct` | VIX's % change from the previous session's close (intraday: live) |
| `atm_iv` | at-the-money implied volatility, % |
| `iv_rank` | where `atm_iv` sits in its past-252-session range, 0-100 |
| `iv_pctile` | share of the past 252 sessions with lower `atm_iv`, % |
| `pcr_oi` / `pcr_vol` | put/call ratio by open interest / by volume |
| `max_pain` | the strike where option buyers would collect least at expiry |
| `straddle` | the ATM call + put price, rupees |
| `expected_move_pct` | `straddle` as a % of the forward |
| `skew_25d` | 25-delta put IV minus 25-delta call IV |
| `basis_pct` | forward over spot, % |
| `fii_index_fut_net` | FIIs' net index-futures contracts (end of day) |
| `spot` / `forward` / `atm_strike` | index level, parity forward, ATM strike |
| `max_oi_call_strike` / `max_oi_put_strike` | the highest-OI strikes |
| `dte` / `expiry_dte` | calendar days to the strategy's expiry (0 on expiry day) |
| `weekday` | 0 Monday … 4 Friday |
| `event_today` | Budget, RBI policy or election-result day |
| `sessions_to_event` | sessions until the next such event (0 = today) |
| `day_open` | intraday: the index's 09:15 open |
| `or_high` / `or_low` | intraday: the index's 09:15–09:44 high / low, known from 09:45 |
| `open` `high` `low` `close` | the index's own candle: the day (positional) or the minute (intraday) |

Functions on the index's candles: `sma(n)`, `ema(n)`, `rsi(n)`, `atr(n)`,
`roc(n)`, `stddev(n)`, `bbands.upper(n, k)`, `macd.line(12,26,9)`,
`supertrend.direction(n, m)` and the rest of `docs/STRATEGY_FORMAT_V3.md`'s
function table; `close[1]` is the previous candle. Positional: daily candles
from 2016. Intraday: minute candles with the previous five sessions as
warm-up, so `sma(30)` is 30 minutes and long daily lookbacks are not
available — use the previous session's facts instead.

Operators: `and`, `or`, `not`, `< > <= >= == !=`, `+ - * /`, brackets.

## How it is tested

- ₹5,00,000. Fills at the next open (positional: next session's open for
  that contract; intraday: next minute's open), paying half the spread —
  the larger of ₹0.05 and 0.5% of the premium, doubled for a thin contract
  (fewer than 100 contracts traded recently). A leg that has not traded
  recently is not entered, and the whole entry is skipped.
- NSE charges by trade date (STT, exchange fees, stamp duty, GST, ₹20 an
  order). SPAN-style margin: a VIX-scaled scan plus exposure margin; a
  covered position's maximum loss; never less than the net premium paid.
- One position at a time. Stops and targets are on the whole position.
  Intraday: each minute's high and low, worst case first; a gap through the
  stop fills at the open. Positional: checked on each session's closing
  prices, and the exit fills at the next session's open - so an overnight
  gap is paid in full, whatever the stop says.
- Positional: daily data from Jan 2016. Intraday: real option minutes from
  Oct 2024, for strikes within ±5% of the index and each expiry's last 10
  (weekly) or 20 (monthly) sessions. A day without them is skipped and
  counted.
