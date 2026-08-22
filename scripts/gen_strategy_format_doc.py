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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
