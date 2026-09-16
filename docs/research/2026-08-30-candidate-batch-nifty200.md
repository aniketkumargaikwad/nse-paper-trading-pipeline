# Six candidates on NIFTY200: what survived, and what the costs hide

**Date:** 2026-08-30
**Data:** 200 NIFTY200 symbols · daily and 15m · 2019-01-01 – 2026-08-28 · Dhan
  (parquet candle store)
**Method:** time-ordered 70/30 in-sample / out-of-sample split (`--holdout 0.3`).
  Itemised cost model (`COST_MODEL=itemised`), notional sizing at Rs 100,000.
**Follows:** `2026-08-22-why-strategies-lose.md`, whose findings this batch was
  designed around and, in the main, reproduces.

---

## The batch

Six strategies, written to the v2 format in `strategies.yaml` and saved to the
database paused. Designed against the previous memo's three conclusions: look
at longer horizons, use a wider universe, prefer rarer setups — and pair every
long with a **short mirror**, because both windows in that memo rose, so a
long-only result is partly just market exposure.

| # | name | side | tf | premise |
|---|---|---|---|---|
| 1 | N200-BREAKOUT-DAY | long | day | close outside upper Bollinger band, above 200-SMA, high volume |
| 2 | N200-BREAKDOWN-DAY | short | day | mirror of 1 |
| 3 | N200-PULLBACK-DAY | long | day | shallow RSI(3) flush inside an intact uptrend |
| 4 | N200-MACD-REGIME-DAY | long | day | MACD cross + 200-SMA filter — a deliberate control |
| 5 | N200-PDH-BREAK-15m | long | 15m | break of yesterday's high, above VWAP, squared off 15:10 |
| 6 | N200-PDL-BREAK-15m | short | 15m | mirror of 5 |

---

## Results

| strategy | IS net | OOS net | OOS/trade | OOS % of notional | OOS Sharpe |
|---|---:|---:|---:|---:|---:|
| N200-PULLBACK-DAY | +Rs 3.53M | **+Rs 37k** | +Rs 16 | **+0.016%** | +0.06 |
| N200-MACD-REGIME-DAY | +Rs 4.71M | −Rs 534k | −Rs 207 | −0.207% | −0.73 |
| N200-BREAKOUT-DAY | +Rs 4.75M | −Rs 628k | −Rs 385 | −0.385% | −0.87 |
| N200-PDL-BREAK-15m | −Rs 1.51M | −Rs 915k | −Rs 144 | −0.144% | −3.90 |
| N200-BREAKDOWN-DAY | −Rs 946k | −Rs 1.30M | −Rs 1,351 | −1.351% | −2.50 |
| N200-PDH-BREAK-15m | −Rs 5.13M | −Rs 1.49M | −Rs 169 | −0.169% | −8.10 |

**One of six is positive out of sample, by Rs 16 a trade.**

---

## 1. The reversal finding reproduced, on new ground

The previous memo found 14 of 15 momentum signals inverting between an
in-sample and an out-of-sample window. Both momentum strategies here did the
same thing:

* BREAKOUT-DAY: +Rs 1,204/trade in sample, −Rs 385 out of sample
* MACD-REGIME-DAY: +Rs 857/trade in sample, −Rs 207 out of sample

That was measured before on 50 symbols, at 60m, over two years. It is now also
true on 200 symbols, on the daily bar, over 7.6 years. It is not an artifact of
that particular window — treat it as a property of this market and this
universe until something contradicts it.

## 2. The short mirrors confirm the longs were mostly beta

BREAKDOWN-DAY runs BREAKOUT-DAY's logic downward and loses Rs 2.2M. The
in-sample profit of the long side was not a breakout edge; it was a rising
market, bought.

This is the cheapest diagnostic in the batch and it should be standard: pair
every long candidate with its mirror. A long that works and a short that is
merely mediocre is interesting. A long that works and a short that is
catastrophic is beta wearing a strategy's clothes.

## 3. Intraday breakouts are negative gross — in BOTH directions

The 15m pair is the most decisive result here, and the only one whose costs are
modelled correctly (both square off at 15:10, so the intraday/MIS rates in
`costs.py` genuinely apply).

| | OOS net/trade | cost | **gross** |
|---|---:|---:|---:|
| PDH-BREAK (long) | −0.169% | 0.082% | **−0.087%** |
| PDL-BREAK (short) | −0.144% | 0.082% | **−0.062%** |

Both lose **before** paying any cost, over 31,588 and 19,024 trades
respectively, in both halves of the split. PDH is profitable on 7 of 200
symbols; PDL on 23 of 200.

This is not noise and it is not friction. A 15m break of the previous day's
extreme, with a VWAP and volume filter, has negative expectancy on NIFTY200 in
either direction. Note what that rules out: the failure is not directional
bias, so inverting the rule does not rescue it — the *setup* carries no
information, and fading it would pay the same 0.082% out of a smaller gross.

## 4. The delivery-cost gap invalidates the one survivor

The four daily strategies hold overnight. `costs.py` models **intraday/MIS**
charges and says so explicitly:

> Delivery (CNC) trades are NOT modelled: this platform trades intraday, and
> delivery has a different STT rate

On Rs 100,000 of notional, round trip:

| | STT | total | % of notional |
|---|---|---:|---:|
| intraday (what was charged) | 0.025% sell only | Rs 82 | **0.082%** |
| delivery, Rs 20/order | 0.1% both legs | Rs 257 | **0.257%** |
| delivery, zero brokerage | 0.1% both legs | Rs 210 | **0.210%** |

Every daily figure above is flattered by roughly **0.13–0.17% per trade**.

For PULLBACK-DAY this is decisive. Its out-of-sample edge is +0.016% per trade
net, or +0.099% gross. Against a real delivery cost of 0.21% it is **not
marginal, it is negative** — roughly −0.11% per trade, or about −Rs 250k over
the 2,293 out-of-sample trades instead of +Rs 37k.

**Nothing in this batch is deployable.**

## 5. Six candidates, one apparent survivor

`false_positive_warning()` exists for this. Six strategies were tested; one
came out positive out of sample by an amount indistinguishable from zero. That
is the expected yield of six coin flips, and it should not be read as "the
pullback nearly worked". Section 4 is the reason it did not, but even without
Section 4 the honest reading of +Rs 16 a trade is "no signal detected".

---

## What follows

**Add a delivery cost model to `costs.py`.** This is the concrete tooling gap
the batch exposed. Four of six natural strategy shapes hold overnight, and the
engine currently prices all four wrong in the optimistic direction. A
`DeliveryCostModel` alongside `CostModel` and `FlatCostModel`, selected by the
strategy's own holding behaviour (does it set `session.square_off`?) rather
than by an env var, would stop this class of error at the source. Until it
exists, mentally add ~0.15%/trade to any overnight backtest.

**Pair every long with its short mirror, as a rule.** Section 2. It cost one
extra backtest run and it reframed the interpretation of the entire long side.

**The previous memo's open items remain the open items.** Point-in-time
universe membership is still not recorded, and it still gets more expensive to
fix every day it is not. Nothing in this batch addressed it, and the pullback
strategy — a buy-the-dip variant, however shallow — is exposed to exactly the
survivorship bias it creates.

**Where not to spend the next batch:** daily momentum on a large-cap universe
(Section 1, twice measured now) and intraday breakouts at 15m (Section 3,
negative gross in both directions). Both are now measured dead ends rather than
untested guesses.
