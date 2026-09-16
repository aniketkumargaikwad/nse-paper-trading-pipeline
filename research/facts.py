"""What the data has already shown. Read by Opus before it designs anything.

Every line here was MEASURED on this store and written up in docs/research.
Without it, each morning started from folklore: three days running proposed
the same "always long above the 200-day average" family that the first
memo had already shown to be beta. A fact that costs a day to re-learn is a
fact worth stating.

Training-window facts only. Nothing here comes from the locked year.
"""

from __future__ import annotations

FACTS = """\
What has already been measured on this exact data (do not spend a version
re-discovering these):
- The training years were a strong bull market: simply holding the average
  stock returned about 418% over eight years, ~23% a year. Any long-only rule
  shows winners; only excess over holding counts.
- Daily momentum / trend-following on large caps looked good in-sample and
  REVERSED out of sample, measured twice (50 stocks at 60m, 200 stocks daily).
  Trend rules that hold above a 200-day average beat holding on fewer than a
  quarter of stocks in every version tried (13-15 Sep 2026).
- 15-minute breakouts of the previous day's high or low, with VWAP and
  volume filters, lose BEFORE fees in both directions on 200 stocks.
- 60-minute price autocorrelation is about 0.003% - real but six times too
  small to trade. Do not look for edge in 60-minute momentum on large caps.
- Sub-hour rules that trade often churn: a typical intraday round trip pays
  about 0.08% in fees plus 0.10% in slippage. Ten trades a month per stock is
  about 1.8% a month gone before any edge.
- Overnight positions pay delivery charges (STT both legs): about 0.21% a
  round trip, versus 0.08% intraday.
- Mean-reversion (RSI-2 washouts, band touches) on daily bars beat holding
  on 5-25% of stocks; the profitable ones were the ones with a positive
  drift, i.e. beta again.
- Buy-the-dip results are inflated by survivorship: today's index list is
  applied to the past, so stocks that fell and never recovered are absent.
- Identical results across 5m, 15m, 25m, 30m and 60m mean the rule only
  reads daily values; test one timeframe or read intrabar structure.
- Volatility-scaled stops (ATR) behave better across a 200-stock universe
  than one fixed percentage; a 100% worst dip means sizing had no survival
  constraint.
- The atlas (16 Sep 2026, ten-slot accounts): on DAILY bars the plain trend
  rules - 250-day high breakout, MACD zero cross, Supertrend, EMA 20/50,
  Donchian 20/10 - earn +0.6% to +1.25% a month with 50-58% of months up;
  holding the same stocks averaged +1.9% a month. Every sub-hour timeframe of
  every baseline is NEGATIVE, from -0.2% to -38% a month: fees and slippage
  eat the churn. Mean-reversion baselines (RSI-2 dips, band reverts, gap
  fades and bounces, VWAP reverts) lose on every timeframe.
Where edge has NOT been measured yet: short-side rules squared off the same
day; relative strength ACROSS stocks (rank, not level); regime filters built
from index breadth; gap behaviour at the open; time-of-day effects; holding
periods of 2-10 days with volatility-scaled exits.
"""
