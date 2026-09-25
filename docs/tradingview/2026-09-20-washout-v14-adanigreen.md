# v1.4 on TradingView — ADANIGREEN, 30m

Checked 20 September 2026. Strategy under test: `strategy_versions.id = 30`,
`R-20260915-washout-entry-38-of-peak-structural-trai-v4`, the version the
2026-09-15 run reported as its luckiest combination.

Pine port: `washout_v14_adanigreen_30m.pine` (same folder). Ported from the
stored state-machine definition, not from the change note.

## The headline: the 30m test could not be reproduced

TradingView serves **5,493 thirty-minute bars on this account, starting
1 January 2025**. The research run covers 7.1 years ending 31 July 2026.
So roughly **24% of the window exists**, and the missing 76% is exactly the
part that produced the result — ADANIGREEN went from about Rs 45 to about
Rs 2,000 between 2019 and 2022, and none of that is in TradingView's 30m feed.

Daily bars do go back to listing (18 June 2018, 2,045 bars), so the daily
cross-check below covers the whole window.

## What the 30m data does say (1 Jan 2025 - 18 Sep 2026)

|                    | v1.4      | Just holding |
|--------------------|-----------|--------------|
| Rs 1,00,000 becomes| 1,15,913  | 1,26,947     |
| Return             | +15.91%   | +26.95%      |
| Trades             | 3         | -            |
| Win rate           | 33% (1/3) | -            |
| Worst drop         | 16.01%    | -            |
| Fees paid          | Rs 618    | -            |

**Over the only 30m window that exists, v1.4 loses to buy-and-hold.** Two of
the three trades were stopped out by the structural trail; the third carried
the whole gain.

## Daily cross-check, full window (18 Jun 2018 - 18 Sep 2026)

Same logic, daily bars, so the 2019-2022 run is included.

|                    | v1.4       | Just holding |
|--------------------|------------|--------------|
| Rs 1,00,000 becomes| 69,45,573  | 29,31,933    |
| Return a year      | +67.2%     | +50.6%       |
| Return a month     | +4.38%     | -            |
| Trades             | 8          | -            |
| Win rate           | 75% (6/8)  | -            |
| Worst drop         | 23.03%     | -            |
| Profit factor      | 5.50       | -            |
| Fees paid          | Rs 66,492  | -            |

Eight round trips, first entry at Rs 45.50, last exit at Rs 1,400.60. The
trade that made it was Rs 131.10 -> Rs 916.30.

## How this compares to the research run

| Measure              | Research (30m, 7.1y) | TradingView (daily, 8.25y) |
|----------------------|----------------------|----------------------------|
| Rs 1,00,000 becomes  | 83,44,976            | 69,45,573                  |
| Just holding         | 36,57,778            | 29,31,933                  |
| Multiple over holding| 2.28x                | 2.37x                      |
| Worst drop reported  | 7.3%                 | 23.03%                     |

The multiple-over-holding lands within 4% on independent data, an independent
feed and a different timeframe. That is a real point in the strategy's favour:
the shape of the edge — hold the compounder, cut it on a structural break —
reproduces outside this codebase.

## Three things the reproduction exposes

1. **The 7.3% worst drop is not the real one.** `research/lakh.py` already
   says so in its own docstring, naming this exact run: the balance between
   closed trades fell 7.30% while the open position was 45.45% underwater.
   TradingView, which marks the equity curve to market, reports 23.03% on
   daily. Neither 7.3% nor 23.03% is wrong — they measure different things —
   but 7.3% is the one that would get someone hurt.

2. **The risk block is inert.** `risk.target = 49%` and
   `risk.stop_loss = 45%` never fire, because `machine_backtest.py:210` lets
   a transition's own `stop:`/`target:` expression win, and this strategy sets
   `target: close * 100`. Worth knowing if a future version tunes `risk.target`
   and sees nothing change.

3. **Five trades is not a sample.** The research run reports 5 trades over 7.1
   years and TradingView finds 8 over 8.25. On the daily run one trade supplies
   most of the gain. Whatever this measures, it is not a repeatable monthly
   return — and "+5.34% a month" invites reading it as one.

## Caveats

- TradingView data is corporate-action adjusted; this project's daily feed is
  not fully adjusted (see the 2026-09-16 audit). The two are not the same
  series, which is part of why an exact match was never available.
- The daily run is a different timeframe, so it is a robustness check, not a
  replication.
- Fees modelled as 0.11% a side (delivery rates from `costs.py`).
- TradingView served this symbol as `NSE_DLY` — a delayed feed. Fine for
  history, not for anything live.
