# TradingView comparison — what our tool says

Run `sma_cross_reliance_daily.pine` in TradingView and compare its trade list
with the table below. Nothing here is estimated; these are the exact trades
our backtester produced on 2026-08-22.

## Set up the chart exactly like this

| Setting | Value |
|---|---|
| Symbol | **NSE:RELIANCE** |
| Timeframe | **1 day** |
| Chart type | Candles (regular — not Heikin Ashi, not Renko) |
| Adjustments | Splits and bonuses **on** (TradingView's default) |

Then open the strategy's **Properties** tab and confirm:

| Property | Value |
|---|---|
| Initial capital | 100000 |
| Order size | 1 Contract |
| Pyramiding | 0 |
| Commission | 0 |
| Slippage | 0 |
| Recalculate — After order is filled | **off** |
| Recalculate — On every tick | **off** |
| Fill orders — Using bar magnifier | **off** |

Those settings are also written into the script, but TradingView lets a saved
chart override them, so it is worth a look.

## Check the data first

If the candles differ, the trades will differ and it says nothing about
either tool. Hover these three dates on the chart and confirm they match:

| Date | Open | High | Low | Close |
|---|---|---|---|---|
| 2024-06-03 | 1483.00 | 1514.50 | 1459.00 | 1510.33 |
| 2025-01-15 | 1244.95 | 1257.00 | 1241.85 | 1252.20 |
| 2026-08-20 | 1315.50 | 1316.50 | 1307.00 | 1313.20 |

Small differences in the last decimal are fine. Anything bigger — especially
a price that is roughly **double** ours — means TradingView is showing
unadjusted prices across Reliance's bonus issue, and the adjustment setting
needs fixing before going further.

## What our tool produced

**NSE:RELIANCE, daily, 2024-04-01 to 2026-08-21. One share per trade. No fees,
no slippage.**

| # | Buy date | Buy price | Sell date | Sell price | P&L |
|---|---|---|---|---|---|
| 1 | 2024-04-08 | 1462.98 | 2024-05-06 | 1435.50 | −27.48 |
| 2 | 2024-05-28 | 1468.00 | 2024-07-31 | 1504.00 | +36.00 |
| 3 | 2024-08-30 | 1534.00 | 2024-09-16 | 1477.55 | −56.45 |
| 4 | 2024-12-09 | 1303.00 | 2024-12-19 | 1239.00 | −64.00 |
| 5 | 2025-01-20 | 1316.00 | 2025-02-14 | 1219.00 | −97.00 |
| 6 | 2025-03-20 | 1251.85 | 2025-04-11 | 1195.15 | −56.70 |
| 7 | 2025-04-25 | 1303.50 | 2025-07-23 | 1426.00 | +122.50 |
| 8 | 2025-09-19 | 1414.90 | 2025-10-03 | 1363.20 | −51.70 |
| 9 | 2025-10-21 | 1468.00 | 2026-01-09 | 1465.00 | −3.00 |
| 10 | 2026-02-17 | 1431.10 | 2026-03-04 | 1330.00 | −101.10 |
| 11 | 2026-05-04 | 1433.40 | 2026-05-21 | 1367.20 | −66.20 |
| 12 | 2026-06-30 | 1306.90 | 2026-07-02 | 1310.00 | +3.10 |

**12 closed trades. Net −362.03 per share.**

There is also a 13th trade still open at the end: bought 2026-08-11 at
1326.60. TradingView will show this as an open position too, and it should
not be counted in the comparison.

## How to read the result

**Compare the dates first, then the prices, then the money.**

* **Dates match** — the two engines agree on when to trade. That is the whole
  question, and everything else follows from it.
* **Prices match too** — the fill rule agrees as well: decide on a bar's
  close, buy at the next bar's open.
* **A date is off by one bar** — usually a crossover so marginal that a tiny
  data difference tips it. Check that day's candle on both sides.
* **An extra or missing trade** — check it is not the very first or last one,
  where the window edges differ.
* **Everything matches but the money** — arithmetic, not logic. Confirm the
  order size really is 1.

## What this does and does not prove

**Does:** that our indicators, our crossover detection, and our
decide-on-close-fill-at-next-open rule agree with an independent, widely used
platform on real data.

**Does not:** anything about fees, slippage, position sizing, stop-loss
handling or intraday behaviour — all deliberately switched off here so they
could not muddy the signal comparison. Those need their own tests, and the
intraday one is harder because hourly bar alignment differs between data
providers.
