"""Generate docs/STRATEGY_FORMAT.md from the strategy vocabulary.

    .venv\\Scripts\\python.exe scripts/gen_strategy_format_doc.py

The generated page is meant to be pasted into an external AI tool (ChatGPT or
similar) BEFORE asking it for a strategy, so what comes back is something this
system actually accepts.

Generating it rather than hand-writing it is what stops the reference drifting
from the validator, and a test asserts the committed copy matches. A stale
reference would send you in circles debugging strategies that were never going
to validate.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import SUPPORTED_TIMEFRAMES  # noqa: E402
from strategy.migrate import CURRENT_VERSION  # noqa: E402
from strategy.vocabulary import (  # noqa: E402
    COMPARISON_OPERATORS,
    CROSS_OPERATORS,
    INDICATOR_OUTPUTS,
    INDICATOR_PARAMS,
    MAX_ATR_MULTIPLIER,
    MAX_OFFSET,
    MAX_STOP_PERCENT,
    POSITION_TYPES,
    PRICE_SOURCES,
    SESSION_CLOSE_HHMM,
    SESSION_OPEN_HHMM,
    SIZING_TYPES,
    SOURCE_ALLOWED_FOR,
    STOP_TYPES,
)
from strategy.vocabulary import (  # noqa: E402
    CANDLE_FIELDS,
    EXPR_MULTI_OUTPUT,
    EXPR_PRECEDENCE,
    EXPR_SIMPLE_INDICATORS,
    EXPR_STRUCTURE,
    HIGHER_TIMEFRAME_PREFIXES,
    POSITION_FIELDS,
)
from strategy.v3 import CURRENT_V3_VERSION  # noqa: E402

V3_DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "STRATEGY_FORMAT_V3.md"

DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "STRATEGY_FORMAT.md"

EXAMPLE = """version: 2

strategies:
  - name: TF-EMA-RSI-15m-v2
    enabled: true
    position_type: long
    timeframe: 15m

    universe: NIFTY100

    entry:
      all:
        - indicator: ema
          params: { period: 9 }
          operator: crosses_above
          compare_to: { indicator: ema, params: { period: 21 } }
        - indicator: rsi
          params: { period: 14 }
          operator: ">"
          value: 50

    exit:
      any:
        - indicator: ema
          params: { period: 9 }
          operator: crosses_below
          compare_to: { indicator: ema, params: { period: 21 } }

    sizing:
      type: notional
      notional_per_trade: 100000

    risk:
      stop_loss: { type: percent, value: 0.7 }
      target:    { type: percent, value: 1.5 }
      trailing_stop: { type: percent, value: 0.5 }

    session:
      no_entry_before: "09:30"
      square_off: "15:15"

    max_cycles_per_day: 2
"""


def _indicator_rows() -> str:
    rows = []
    for name in sorted(INDICATOR_PARAMS):
        params = INDICATOR_PARAMS[name]
        params_text = ", ".join(f"`{p}`" for p in sorted(params)) or "—"
        outputs = INDICATOR_OUTPUTS.get(name)
        outputs_text = ", ".join(f"`{o}`" for o in outputs) if outputs else "—"
        rows.append(f"| `{name}` | {params_text} | {outputs_text} |")
    return "\n".join(rows)


def render_document() -> str:
    """Build the whole page. Pure — returns text, writes nothing."""
    return f"""# Strategy format v{CURRENT_VERSION}

<!-- GENERATED FILE — do not edit by hand.
     Regenerate: .venv\\\\Scripts\\\\python.exe scripts/gen_strategy_format_doc.py -->

Paste this whole page into ChatGPT (or any similar tool) before asking it to
write a strategy. It describes exactly what this system accepts, so what comes
back will usually validate first time.

If a strategy does not validate, the error names the exact location — for
example `strategies[0].entry.all[1].operator` — and lists what is allowed
there. Paste that error back into the chat and the tool will usually correct
itself.

## Worked example

```yaml
{EXAMPLE}```

## Rules

* `version` must be `{CURRENT_VERSION}`.
* Every strategy needs **exactly one** of `universe:` (a named group like
  `NIFTY100`) or `instruments:` (an explicit list like `[NSE:RELIANCE]`).
* `position_type`: {", ".join(f"`{p}`" for p in sorted(POSITION_TYPES))}.
* `timeframe`: {", ".join(f"`{t}`" for t in SUPPORTED_TIMEFRAMES)}.
* Conditions are grouped with `all:` (AND) or `any:` (OR); groups may nest.
* Every condition compares a left operand against **either** a fixed `value`
  **or** another operand under `compare_to` — exactly one of the two.
* Unknown keys are rejected rather than ignored, so a typo like `perod: 14` is
  reported instead of silently changing what the strategy does.

## Indicators

| indicator | required params | `output` options |
|---|---|---|
{_indicator_rows()}

Raw price and volume series ({", ".join(f"`{s}`" for s in sorted(PRICE_SOURCES))})
take no params.

`source:` selects which series a moving average is computed over and is allowed
only on {", ".join(f"`{s}`" for s in sorted(SOURCE_ALLOWED_FOR))}. A volume
average is written `{{indicator: sma, source: volume, params: {{period: 20}}}}`.

## Reading earlier bars — `offset`

`offset:` reads a value N **closed bars back**, on any operand. `0` (the
default) is the current bar, `1` the previous one. Maximum {MAX_OFFSET}.

```yaml
# this bar's close breaks the previous bar's high
- indicator: close
  operator: ">"
  compare_to: {{ indicator: high, offset: 1 }}
```

```yaml
# RSI is higher than it was three bars ago
- indicator: rsi
  params: {{ period: 14 }}
  operator: ">"
  compare_to: {{ indicator: rsi, params: {{ period: 14 }}, offset: 3 }}
```

On a `timeframe: day` strategy, `offset: 1` on `high` IS the previous day's
high. There is no negative offset: it would read a bar that has not closed,
which is look-ahead bias, and the parser rejects it.

Each bar of offset delays the strategy's first possible signal by one bar,
because the value does not exist until that much history has accumulated.

## Reading a higher timeframe — `timeframe`

`timeframe:` on an operand reads it on a **higher** timeframe than the
strategy's own — a daily trend filter under a 15m entry, say. One strategy,
two timeframes.

```yaml
timeframe: 15m          # the strategy trades 15m bars
entry:
  all:
    # daily trend filter: price above the 200-day EMA
    - indicator: close
      timeframe: day
      operator: ">"
      compare_to: {{ indicator: ema, params: {{ period: 200 }}, timeframe: day }}
    # 15m entry trigger
    - indicator: rsi
      params: {{ period: 14 }}
      operator: crosses_above
      value: 60
```

```yaml
# today's price breaks yesterday's high, on an intraday strategy
- indicator: close
  operator: ">"
  compare_to: {{ indicator: high, timeframe: day }}
```

**A higher-timeframe bar is only visible once it has fully closed.** During
today's session the newest closed daily bar is *yesterday's*, so
`{{indicator: high, timeframe: day}}` is yesterday's high on every bar of
today — including the last one. This is what makes the reference free of
look-ahead, and it is why the breakout example above needs no `offset`.

`offset` then counts bars of the OPERAND's timeframe:
`{{indicator: high, timeframe: day, offset: 1}}` is the high of the day
*before* yesterday.

Rules:

* The operand's timeframe must be the strategy's own or higher. A lower one is
  rejected — the strategy's candles do not contain that detail.
* Higher-timeframe bars are built by aggregating the strategy's own candles,
  so the filter and the entry can never disagree about the price.
* History scales with the timeframe: a daily EMA(200) under a 15m strategy
  needs 200 trading days of 15m candles before it produces a single value.

## Operators

Comparison: {", ".join(f"`{o}`" for o in sorted(COMPARISON_OPERATORS))} —
**quote these in YAML**, e.g. `operator: ">"`.

Crossing: {", ".join(f"`{o}`" for o in sorted(CROSS_OPERATORS))} — true when the
value was on the other side on the PREVIOUS closed candle and is on this side
on the current one. Never evaluated on a forming candle.

## Sizing

`sizing` is required, with one of these types:
{", ".join(f"`{s}`" for s in sorted(SIZING_TYPES))}.

```yaml
sizing: {{ type: notional, notional_per_trade: 100000 }}   # recommended
sizing: {{ type: fixed_quantity, quantity: 5 }}            # legacy, discouraged
```

Prefer `notional`. Quantity is derived per symbol as
`floor(notional_per_trade / entry price)`, so trading costs weigh the same on a
cheap stock as an expensive one. With a fixed share count they do not, and a
comparison across many symbols partly becomes a comparison of share prices.

If `notional_per_trade` is below a symbol's share price, that trade is skipped
and recorded as skipped — never taken at zero quantity.

## Risk

`stop_loss` and `target` are required; `trailing_stop` is optional. Each is one
of these types: {", ".join(f"`{s}`" for s in sorted(STOP_TYPES))}.

```yaml
risk:
  stop_loss: {{ type: percent, value: 0.7 }}            # 0.7% from entry
  target:    {{ type: atr, period: 14, multiplier: 3 }} # 3 x ATR(14)
  trailing_stop: {{ type: percent, value: 0.5 }}
```

Percent values must be between 0 and {MAX_STOP_PERCENT:g}; an ATR `multiplier`
at most {MAX_ATR_MULTIPLIER:g}. Do not mix the two forms — `{{type: atr, value:
1.5}}` is rejected rather than quietly defaulted.

The trailing stop follows the candle high (long) or low (short), updates at the
candle close, applies from the next candle, and never loosens. The tighter of
the fixed and trailing stop always applies.

## Session

Every key is optional; all times are IST, **quoted**, between
`{SESSION_OPEN_HHMM}` and `{SESSION_CLOSE_HHMM}`.

```yaml
session:
  no_entry_before: "09:30"
  no_entry_after:  "14:30"
  square_off:      "15:15"
```

Quote the times. Unquoted, YAML reads `15:15` as a number, not a time.

`no_entry_before` / `no_entry_after` bound the candle the entry **fills** on.
`square_off` closes any open position at the open of the first candle at or
after that time, and blocks entries from then — so a strategy with `square_off`
never holds a position overnight.

## Order of exits within one candle

Signal exit → stop loss (including trailing) → target → square-off. A price gap
through a level fills at the candle open, not at the level.
"""


def main() -> int:
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text(render_document(), encoding="utf-8")
    print(f"wrote {DOC_PATH}")
    V3_DOC_PATH.write_text(render_v3_document(), encoding="utf-8")
    print(f"wrote {V3_DOC_PATH}")
    return 0



# ---------------------------------------------------------------------------
# Version 3: state machines
# ---------------------------------------------------------------------------

V3_EXAMPLE = """version: 3
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
"""


def _v3_function_rows() -> str:
    rows = []
    for name, arities in sorted(EXPR_SIMPLE_INDICATORS.items()):
        if name in ("sma", "ema"):
            call = f"`{name}(period)` or `{name}(series, period)`"
        elif 0 in arities:
            call = f"`{name}()`"
        else:
            call = f"`{name}(period)`"
        rows.append(f"| {call} | one series |")
    # Argument names per family. A family missing here still renders — it just
    # shows a generic placeholder — so adding an indicator can never crash the
    # document generator, which is how this list went stale the first time.
    ARGS = {
        "macd": "fast, slow, signal",
        "bbands": "period, std",
        "supertrend": "period, multiplier",
        "stoch": "k_period, d_period[, smooth]",
        "stochrsi": "rsi_period, stoch_period[, k, d]",
        "adx": "period",
        "aroon": "period",
        "donchian": "period",
        "keltner": "period, multiplier[, atr_period]",
        "psar": "[step, max_step]",
    }
    for family, outputs in sorted(EXPR_MULTI_OUTPUT.items()):
        params = ARGS.get(family, "...")
        spelled = ", ".join(f"`{family}.{o}(...)`" for o in outputs)
        rows.append(f"| `{family}.<output>({params})` | {spelled} |")
    return "\n".join(rows)


def _v3_precedence_rows() -> str:
    return "\n".join(
        f"| {i + 1} | `{ops}` | {meaning} |"
        for i, (ops, meaning) in enumerate(EXPR_PRECEDENCE)
    )


def render_v3_document() -> str:
    """The v3 reference. Pure — returns text, writes nothing."""
    prefixes = ", ".join(
        f"`{p}.`" for p in sorted(HIGHER_TIMEFRAME_PREFIXES)
    )
    return f"""# Strategy format v{CURRENT_V3_VERSION} — state machines

<!-- GENERATED FILE — do not edit by hand.
     Regenerate: .venv\\Scripts\\python.exe scripts/gen_strategy_format_doc.py -->

Paste this whole page into ChatGPT (or any similar tool) before asking it to
write a v3 strategy.

**Use v{CURRENT_V3_VERSION} when the strategy waits for things in order** — "sweep the low,
then wait for a confirming candle, then enter on a break of *that* candle" —
or when it needs arithmetic, or a stop placed at a level it noticed earlier.
For a plain indicator-threshold rule, v{CURRENT_VERSION} (see STRATEGY_FORMAT.md) is
shorter and does the same job.

## Worked example

```yaml
{V3_EXAMPLE}```

## How it runs

A machine sits in one state and checks that state's `on:` list against each
closed candle, in written order. **The first `when:` that is true wins, and at
most one transition fires per candle.** Two events a strategy waits for happen
on different candles anyway, so this costs nothing and keeps a cycle in the
state graph from spinning forever inside one bar.

`set:` captures values at the transition's own candle and they persist until
reassigned — that is how `confirm_low` is still there several candles later.

`timeout: {{bars: N, goto: S}}` leaves a state after N candles with no
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
| `{'` `'.join(sorted(CANDLE_FIELDS))}` | prefixed `candle.` — e.g. `candle.is_bullish` |
| `{'` `'.join(sorted(POSITION_FIELDS))}` | prefixed `position.` — e.g. `position.bars_held` |
| anything you `set:` | your own variable |

### Functions

| call | outputs |
|---|---|
{_v3_function_rows()}

### Swing points

`swing.high(n)` and `swing.low(n)` give the most recent **confirmed** swing,
where a swing high is a bar that `n` bars on each side failed to exceed.

The confirmation delay is real and deliberate: a swing high at a bar is not
known to be one until `n` further bars have closed, so it only becomes
readable `n` bars later. Anything that reported it sooner would be reading the
future — and would backtest beautifully while being worthless.

The value then persists as a standing level until a newer swing replaces it.

### Higher timeframes

Prefix any name or call with {prefixes} — `prev_day.high`, `daily.ema(50)`,
`hourly.rsi(14)`.

A higher-timeframe bar becomes visible only once it has **fully closed**, so
during today's session `prev_day.high` is yesterday's high, and it stays
yesterday's on every candle of the day rather than turning into today's on the
last one.

### Reading earlier candles

`close[1]` is the previous candle's close; `rsi(14)[2]` is the RSI two candles
back. Under a timeframe prefix the step is in THAT timeframe's bars, so
`prev_day.high[1]` is the day before yesterday.

The offset must be a whole number from 0 to {MAX_OFFSET}. **A negative offset is
rejected**: it would read a candle that has not closed yet.

### Operator precedence

Loosest first. Use brackets when in doubt.

| | operators | |
|---|---|---|
{_v3_precedence_rows()}

## Actions on a transition

* `goto:` — **required**. Which state to move to. Name an existing state; a
  typo is rejected rather than stranding the machine.
* `set:` — capture values, e.g. `{{confirm_low: "low"}}`.
* `enter:` — `side: long|short`, plus optional `stop:` and `target:`
  **expressions**. A level you name here beats the `risk:` block, which stays
  as the fallback so every machine has a stop either way.
* `exit:` — close the position; optional `reason:` shows in the trade log.
  Add `fraction:` below 1 to close only part of it and **leave the rest
  running** — `exit: {{fraction: 0.5, reason: first_target}}` takes half off.
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

## Shared with v{CURRENT_VERSION}

`risk:`, `sizing:`, `session:`, `max_cycles_per_day:`, and
`universe:`/`instruments:` mean exactly what they mean in v{CURRENT_VERSION} —
see STRATEGY_FORMAT.md. Fills, slippage, costs and the order of exits within a
candle are identical too, so a v{CURRENT_V3_VERSION} result is directly comparable with a
v{CURRENT_VERSION} one.
"""

if __name__ == "__main__":
    raise SystemExit(main())
