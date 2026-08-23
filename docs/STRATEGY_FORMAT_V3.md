# Strategy format v3 — state machines

<!-- GENERATED FILE — do not edit by hand.
     Regenerate: .venv\Scripts\python.exe scripts/gen_strategy_format_doc.py -->

Paste this whole page into ChatGPT (or any similar tool) before asking it to
write a v3 strategy.

**Use v3 when the strategy waits for things in order** — "sweep the low,
then wait for a confirming candle, then enter on a break of *that* candle" —
or when it needs arithmetic, or a stop placed at a level it noticed earlier.
For a plain indicator-threshold rule, v2 (see STRATEGY_FORMAT.md) is
shorter and does the same job.

## Worked example

```yaml
version: 3
name: LIQUIDITY-SWEEP-15m
enabled: false
timeframe: 15m
universe: NIFTY50

initial: waiting_for_sweep
on_position_closed: waiting_for_sweep

states:
  - name: waiting_for_sweep
    transitions:
      - when: "low < prev_day.low"
        set: {swept_low: "low"}
        goto: waiting_for_confirmation

  - name: waiting_for_confirmation
    timeout: {bars: 20, goto: waiting_for_sweep}
    transitions:
      - when: "candle.is_bullish and close > prev_day.low"
        set: {confirm_low: "low", confirm_high: "high"}
        goto: armed

  - name: armed
    timeout: {bars: 10, goto: waiting_for_sweep}
    transitions:
      - when: "high > confirm_high"
        enter:
          side: long
          stop: "confirm_low"
          target: "close + atr(14) * 3"
        goto: in_position

  - name: in_position
    transitions:
      - when: "position.bars_held >= 20"
        exit: {reason: time_stop}
        goto: waiting_for_sweep

risk:
  stop_loss: {type: percent, value: 1.5}
  target:    {type: percent, value: 3.0}
sizing:
  type: notional
  notional_per_trade: 100000
session:
  square_off: "15:15"
max_cycles_per_day: 2
```

## How it runs

A machine sits in one state and checks that state's `on:` list against each
closed candle, in written order. **The first `when:` that is true wins, and at
most one transition fires per candle.** Two events a strategy waits for happen
on different candles anyway, so this costs nothing and keeps a cycle in the
state graph from spinning forever inside one bar.

`set:` captures values at the transition's own candle and they persist until
reassigned — that is how `confirm_low` is still there several candles later.

`timeout: {bars: N, goto: S}` leaves a state after N candles with no
transition. Without one, "wait for confirmation" waits forever.

`on_position_closed:` is where the machine goes when the position closes for
**any** reason — its own `exit:`, or a stop, target or square-off. It defaults
to `initial`. This matters: without it a machine that got stopped out would
sit waiting for an exit rule to fire on a position that no longer exists, and
report a strategy that quietly stopped trading.

## Expressions

Every `when:`, `set:`, `stop:` and `target:` is an expression **in quotes**.

### Values you can name

| name | meaning |
|---|---|
| `open` `high` `low` `close` `volume` | the current candle |
| `body` `is_bearish` `is_bullish` `lower_wick` `range` `upper_wick` | prefixed `candle.` — e.g. `candle.is_bullish` |
| `bars_held` `entry_price` `is_long` `is_open` `is_short` `pnl_pct` | prefixed `position.` — e.g. `position.bars_held` |
| anything you `set:` | your own variable |

### Functions

| call | outputs |
|---|---|
| `atr(period)` | one series |
| `awesome()` | one series |
| `cci(period)` | one series |
| `cmf(period)` | one series |
| `dema(period)` | one series |
| `ema(period)` or `ema(series, period)` | one series |
| `hma(period)` | one series |
| `mfi(period)` | one series |
| `momentum(period)` | one series |
| `obv()` | one series |
| `roc(period)` | one series |
| `rsi(period)` | one series |
| `sma(period)` or `sma(series, period)` | one series |
| `stddev(period)` | one series |
| `tema(period)` | one series |
| `trix(period)` | one series |
| `ultimate()` | one series |
| `vwap()` | one series |
| `vwma(period)` | one series |
| `williams_r(period)` | one series |
| `wma(period)` | one series |
| `adx.<output>(period)` | `adx.adx(...)`, `adx.plus_di(...)`, `adx.minus_di(...)` |
| `aroon.<output>(period)` | `aroon.up(...)`, `aroon.down(...)`, `aroon.oscillator(...)` |
| `bbands.<output>(period, std)` | `bbands.upper(...)`, `bbands.middle(...)`, `bbands.lower(...)` |
| `donchian.<output>(period)` | `donchian.upper(...)`, `donchian.middle(...)`, `donchian.lower(...)` |
| `keltner.<output>(period, multiplier[, atr_period])` | `keltner.upper(...)`, `keltner.middle(...)`, `keltner.lower(...)` |
| `macd.<output>(fast, slow, signal)` | `macd.line(...)`, `macd.signal(...)`, `macd.histogram(...)` |
| `psar.<output>([step, max_step])` | `psar.sar(...)`, `psar.direction(...)` |
| `stoch.<output>(k_period, d_period[, smooth])` | `stoch.k(...)`, `stoch.d(...)` |
| `stochrsi.<output>(rsi_period, stoch_period[, k, d])` | `stochrsi.k(...)`, `stochrsi.d(...)` |
| `supertrend.<output>(period, multiplier)` | `supertrend.line(...)`, `supertrend.direction(...)` |

### Swing points

`swing.high(n)` and `swing.low(n)` give the most recent **confirmed** swing,
where a swing high is a bar that `n` bars on each side failed to exceed.

The confirmation delay is real and deliberate: a swing high at a bar is not
known to be one until `n` further bars have closed, so it only becomes
readable `n` bars later. Anything that reported it sooner would be reading the
future — and would backtest beautifully while being worthless.

The value then persists as a standing level until a newer swing replaces it.

### Higher timeframes

Prefix any name or call with `daily.`, `hourly.`, `prev_day.` — `prev_day.high`, `daily.ema(50)`,
`hourly.rsi(14)`.

A higher-timeframe bar becomes visible only once it has **fully closed**, so
during today's session `prev_day.high` is yesterday's high, and it stays
yesterday's on every candle of the day rather than turning into today's on the
last one.

### Reading earlier candles

`close[1]` is the previous candle's close; `rsi(14)[2]` is the RSI two candles
back. Under a timeframe prefix the step is in THAT timeframe's bars, so
`prev_day.high[1]` is the day before yesterday.

The offset must be a whole number from 0 to 500. **A negative offset is
rejected**: it would read a candle that has not closed yet.

### Operator precedence

Loosest first. Use brackets when in doubt.

| | operators | |
|---|---|---|
| 1 | `or` | either side true |
| 2 | `and` | both sides true |
| 3 | `not` | negates what follows |
| 4 | `< > <= >= == !=` | comparison |
| 5 | `+ -` | add, subtract |
| 6 | `* /` | multiply, divide |

## Actions on a transition

* `goto:` — **required**. Which state to move to. Name an existing state; a
  typo is rejected rather than stranding the machine.
* `set:` — capture values, e.g. `{confirm_low: "low"}`.
* `enter:` — `side: long|short`, plus optional `stop:` and `target:`
  **expressions**. A level you name here beats the `risk:` block, which stays
  as the fallback so every machine has a stop either way.
* `exit:` — close the position; optional `reason:` shows in the trade log.
  Add `fraction:` below 1 to close only part of it and **leave the rest
  running** — `exit: {fraction: 0.5, reason: first_target}` takes half off.
  Each slice is booked as its own trade against the same entry price, and
  pays its own costs, because each sale really is a separate order. The stop
  and target then protect what is left. A fraction too small to sell a whole
  share is recorded as a skip rather than silently doing nothing.

A transition cannot both `enter:` and `exit:` on the same candle.

## What is rejected, and why

Each of these would otherwise produce a clean, plausible, wrong backtest:

* a `goto:` naming no declared state — the machine strands
* a state nothing can reach — a branch you think you are testing never runs
* two states with the same name — the second shadows the first
* a variable read that no transition ever `set:` — it resolves to nothing, so
  the condition never fires and the result looks like a strategy with no setups
* a negative offset — look-ahead bias written as configuration

## Shared with v2

`risk:`, `sizing:`, `session:`, `max_cycles_per_day:`, and
`universe:`/`instruments:` mean exactly what they mean in v2 —
see STRATEGY_FORMAT.md. Fills, slippage, costs and the order of exits within a
candle are identical too, so a v3 result is directly comparable with a
v2 one.
