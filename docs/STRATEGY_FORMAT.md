# Strategy format v2

<!-- GENERATED FILE — do not edit by hand.
     Regenerate: .venv\\Scripts\\python.exe scripts/gen_strategy_format_doc.py -->

Paste this whole page into ChatGPT (or any similar tool) before asking it to
write a strategy. It describes exactly what this system accepts, so what comes
back will usually validate first time.

If a strategy does not validate, the error names the exact location — for
example `strategies[0].entry.all[1].operator` — and lists what is allowed
there. Paste that error back into the chat and the tool will usually correct
itself.

## Worked example

```yaml
version: 2

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
```

## Rules

* `version` must be `2`.
* Every strategy needs **exactly one** of `universe:` (a named group like
  `NIFTY100`) or `instruments:` (an explicit list like `[NSE:RELIANCE]`).
* `position_type`: `long`, `short`.
* `timeframe`: `5m`, `15m`, `25m`, `30m`, `60m`, `day`.
* Conditions are grouped with `all:` (AND) or `any:` (OR); groups may nest.
* Every condition compares a left operand against **either** a fixed `value`
  **or** another operand under `compare_to` — exactly one of the two.
* Unknown keys are rejected rather than ignored, so a typo like `perod: 14` is
  reported instead of silently changing what the strategy does.

## Indicators

| indicator | required params | `output` options |
|---|---|---|
| `atr` | `period` | — |
| `bbands` | `period`, `std` | `upper`, `middle`, `lower` |
| `close` | — | — |
| `ema` | `period` | — |
| `high` | — | — |
| `low` | — | — |
| `macd` | `fast`, `signal`, `slow` | `line`, `signal`, `histogram` |
| `open` | — | — |
| `rsi` | `period` | — |
| `sma` | `period` | — |
| `supertrend` | `multiplier`, `period` | `line`, `direction` |
| `volume` | — | — |
| `vwap` | — | — |

Raw price and volume series (`close`, `high`, `low`, `open`, `volume`)
take no params.

`source:` selects which series a moving average is computed over and is allowed
only on `ema`, `sma`. A volume
average is written `{indicator: sma, source: volume, params: {period: 20}}`.

## Operators

Comparison: `<`, `<=`, `>`, `>=` —
**quote these in YAML**, e.g. `operator: ">"`.

Crossing: `crosses_above`, `crosses_below` — true when the
value was on the other side on the PREVIOUS closed candle and is on this side
on the current one. Never evaluated on a forming candle.

## Sizing

`sizing` is required, with one of these types:
`fixed_quantity`, `notional`.

```yaml
sizing: { type: notional, notional_per_trade: 100000 }   # recommended
sizing: { type: fixed_quantity, quantity: 5 }            # legacy, discouraged
```

Prefer `notional`. Quantity is derived per symbol as
`floor(notional_per_trade / entry price)`, so trading costs weigh the same on a
cheap stock as an expensive one. With a fixed share count they do not, and a
comparison across many symbols partly becomes a comparison of share prices.

If `notional_per_trade` is below a symbol's share price, that trade is skipped
and recorded as skipped — never taken at zero quantity.

## Risk

`stop_loss` and `target` are required; `trailing_stop` is optional. Each is one
of these types: `atr`, `percent`.

```yaml
risk:
  stop_loss: { type: percent, value: 0.7 }            # 0.7% from entry
  target:    { type: atr, period: 14, multiplier: 3 } # 3 x ATR(14)
  trailing_stop: { type: percent, value: 0.5 }
```

Percent values must be between 0 and 50; an ATR `multiplier`
at most 20. Do not mix the two forms — `{type: atr, value:
1.5}` is rejected rather than quietly defaulted.

The trailing stop follows the candle high (long) or low (short), updates at the
candle close, applies from the next candle, and never loosens. The tighter of
the fixed and trailing stop always applies.

## Session

Every key is optional; all times are IST, **quoted**, between
`09:15` and `15:30`.

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
