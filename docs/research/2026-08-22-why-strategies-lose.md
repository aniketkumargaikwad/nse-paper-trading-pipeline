# Why every strategy loses, and where an edge might actually be

**Date:** 2026-08-22
**Data:** 50 NIFTY50 symbols · 60m and daily · Aug 2024 – Aug 2026 · Dhan
**Method:** every figure below is measured, with a time-ordered 70/30
in-sample / out-of-sample split. Nothing here is an opinion about markets.

---

## 1. Costs are not the main problem. The signal is.

The story carried since the first backtests was that transaction costs eat the
edge. That was true under the old flat ₹30-per-trade model at `quantity: 1`.
With notional sizing and the itemised cost model it is no longer the binding
constraint.

`NIFTY50-EMA-RSI-60m`, 1,322 trades over 20 symbols and 2 years:

| | |
|---|---|
| gross P&L | −₹147,993 |
| costs | −₹108,440 |
| **net** | **−₹256,433** |
| average notional | ₹98,906 |
| round-trip cost | **0.083%** of notional |
| mean gross move per trade | **−0.113%** |

**The strategy loses money before it pays a single rupee of cost.** Costs make
a bad strategy worse; they did not make a good one bad. Eliminating them
entirely would move it from −0.196% to −0.113% per trade — still losing.

This reframes everything: the work is not cost reduction, it is finding a
signal with positive gross expectancy larger than 0.083%.

**0.083% is the hurdle.** Every number below is measured against it.

---

## 2. There is almost no autocorrelation at 60m

Unconditional next-bar return across 172,848 observations: +0.0030%
(t = 1.98). Sorting by the previous bar's return:

| prior bar | n | next-bar mean | t |
|---|---|---|---|
| worst 20% | 34,570 | +0.0140% | 4.49 |
| middle | 34,570 | −0.0056% | −1.59 |
| best 20% | 34,570 | +0.0119% | 3.43 |

correlation(prev, next) = **−0.0028**

Statistically detectable, economically irrelevant: the largest effect is
0.014%, which is **six times smaller than the cost of trading it**. Single-bar
reversal at 60m is not a strategy.

---

## 3. Fourteen of fifteen signals reversed out of sample

Six signals × three horizons, quintile top-minus-bottom spread on forward
returns. In-sample: Aug 2024 – Dec 2025. Out-of-sample: Dec 2025 – Aug 2026.

| signal | horizon | in-sample | out-of-sample | verdict |
|---|---|---|---|---|
| rangepos | 20b | +0.1170% (t 4.76) | −0.4000% (t −9.51) | **REVERSED** |
| mom5 | 20b | +0.0955% (t 3.54) | −0.3687% (t −8.09) | **REVERSED** |
| dist from EMA20 | 10b | +0.0405% (t 2.08) | −0.2157% (t −6.57) | **REVERSED** |
| volratio | 20b | +0.1025% (t 4.16) | −0.0664% (t −1.58) | REVERSED |
| atrpct | 20b | +0.2256% (t 8.46) | +0.6633% (t 14.92) | held |

Every momentum-flavoured signal that worked in the first period **inverted**
in the second. This is not noise around zero — the t-statistics are large in
both directions. The first period trended; the second mean-reverted.

The practical lesson is not "momentum does not work". It is that a signal
fitted on eighteen months of Indian large-caps tells you about those eighteen
months. Any strategy built on the in-sample half of this data would have
traded confidently into a regime that punished it.

---

## 4. The one survivor was beta, not alpha

`atrpct` (ATR as a percentage of price) held its sign and grew stronger — the
profile that usually means "real". It is not.

Removing the market by demeaning forward returns across symbols at each
timestamp:

| period | raw spread | market-neutral |
|---|---|---|
| in-sample | +0.2259% (t 8.49) | **−0.0125% (t −0.55)** |
| out-of-sample | +0.6586% (t 14.84) | +0.1291% (t 3.59) |

In-sample the effect is **entirely** the market. High-volatility names are
high-beta names, and both halves of this window rose (+0.063% and +0.040% mean
forward 20-bar return). Buying high-ATR stocks was buying leveraged index
exposure, which costs nothing to obtain and is not an edge.

The residual out-of-sample effect does not replicate in-sample, so there is no
consistent cross-sectional alpha here either.

---

## 5. One candidate, and the bias that invalidates it

Quintiles average over 20% of all bars; a real setup fires on far fewer. So
the same test was run on rare conditions, market-neutral, 5-day horizon,
daily bars:

| condition | frequency | in-sample | out-of-sample |
|---|---|---|---|
| gap down < −3% | 0.63% | +0.467% (t 1.45) | −0.830% (t −2.11) |
| gap up > 2% | 1.33% | +0.363% (t 1.72) | +0.167% (t 0.76) |
| RSI(2) < 5 | 7.15% | +0.017% (t 0.20) | −0.237% (t −1.78) |
| **−20% below 60d high** | 5.12% | +0.166% (t 1.42) | +0.526% (t 3.88) |
| **−25% below 60d high** | 1.90% | +0.416% (t 2.04) | **+1.346% (t 5.56)** |

Effect size grows monotonically as the condition gets rarer, holds its sign
across both periods, and at −25% it clears the 0.083% hurdle sixteen times
over.

**And it is the signal most damaged by the bias this dataset has.**

Universe membership is `constituents_as_of = 2026-08-11` — today's NIFTY50,
applied to two years of past data. There is no point-in-time membership. A
stock that fell 25% and kept falling is *removed from the index*, so it is
absent from the sample entirely.

This measures: *"what happened to stocks that fell 25% and were still in the
NIFTY50 in August 2026."* Buying deep dips looks excellent when the ones that
never recovered have been deleted from the record. Survivorship bias inflates
every universe result here, and it inflates a buy-the-dip result most of all.

**This candidate cannot be evaluated with the data we have.** Not "is
probably weaker" — cannot be evaluated.

---

## 6. What follows

**Start recording point-in-time universe membership now.** The audit listed
this in August 2026 as P1 with the note "start now; unrecoverable later", and
it was never done. Every day it is not done is a day that can never be tested
honestly later. It is cheap — a monthly snapshot of the constituent list — and
it is the only thing here that gets *more* expensive to fix by waiting.

**Do not deploy anything built on the in-sample half of this data.** Section 3
is the argument: the same signals that looked strong reversed hard.

**Stop looking for edges at 60m on 50 large caps.** Section 2 measures why:
the autocorrelation is ~0.003%, the cost is 0.083%. The search space is one
where the transaction cost is an order of magnitude larger than the structure.
Better places to look, in rough order of promise:

* **Longer horizons** — daily and multi-day, where moves are large relative to
  a fixed 0.083% cost.
* **A wider universe** — NIFTY500 is already loaded (5,255 instruments in the
  symbol master). Cross-sectional signals need breadth; 50 names is very few.
* **Rarer, more specific setups** — Section 5 shows effect size rising sharply
  with rarity. The v3 state machine exists precisely to express setups that
  quintile sorts cannot.

**What was NOT tested,** and should not be assumed either way: intraday
seasonality, earnings and event windows, sector-relative signals, order-book
or volume microstructure, and anything on the 1m data (only two symbols are
backfilled at that resolution).

---

## 7. Method notes

* Splits are by time, never random — a shuffled split leaks tomorrow into
  today, which is the bias being measured.
* "Market-neutral" means forward returns demeaned across symbols at each
  timestamp, which removes the common factor and leaves cross-sectional
  ranking.
* t-statistics treat observations as independent. Overlapping forward windows
  and cross-sectional correlation both violate that, so the true significance
  is **weaker** than the numbers shown. They are used here to rank candidates,
  not to certify them.
* Roughly 45 signal × horizon combinations were examined. At conventional
  thresholds, two or three would clear by chance alone — which is why the
  out-of-sample column, not the in-sample one, is the column that matters.
