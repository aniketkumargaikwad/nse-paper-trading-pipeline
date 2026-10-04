# NSE F&O charges since 2016 — sources for `fno/charges.yaml`

Collected 3–4 Oct 2026 for the options engine (design 2026-10-02 §4). Step 2
turns this into `fno/charges.yaml` with tests. Rates are per side unless
stated. "Premium" means price × quantity of the option traded.

## STT — the large one

| From | Options, sell, on premium | Futures, sell | Options exercised, on settlement value |
|---|---|---|---|
| (before 2016) | 0.05% | 0.01% | 0.125% |
| 2023-04-01 | 0.0625% | 0.0125% | 0.125% |
| 2024-10-01 | 0.10% | 0.02% | 0.125% |
| **2026-04-01** | **0.15%** | **0.05%** | **0.15%** |

The 2026 rise is Finance Bill 2026, clause 143 (amending s.98 of the Finance
(No. 2) Act 2004), for transactions on or after 1 Apr 2026 — inside the
current locked year (25 Sep 2025 – 24 Sep 2026), so roughly half of that
year's option sales pay the new rate. Sources:
[Outlook Money](https://www.outlookmoney.com/invest/stt-hike-from-april-1-2026-budget-what-it-means-for-futures-and-options-traders),
[Business Today](https://www.businesstoday.in/personal-finance/tax/story/budget-2026-income-tax-act-from-april-1-higher-fpi-limits-stt-hike-headline-fms-reform-express-514075-2026-02-02),
[Prabhudas Lilladher](https://www.plindia.com/news/stt-hike-on-fo-in-april-2026-what-can-traders-expect/).
Earlier steps: [Kotak Neo](https://kotakinsights.substack.com/p/stt-and-market-behaviour),
[ICICI Direct](https://www.icicidirect.com/research/equity/finace/new-stt-rules-in-futures-and-options-trading).

## Exchange transaction charge + NSE IPFT (what the trader pays)

| From | Options, on premium | Futures |
|---|---|---|
| (before 2016) | ~0.050% (top volume slab) | ~0.0019% |
| 2024-10-01 | 0.03553% (₹3,503 + ₹50 IPFT per crore) | 0.00183% (₹173 + ₹10 per crore) |
| 2026-03-01 | 0.03553% (relabelled ₹3,552.99 + ₹0.01; total unchanged) | 0.00183% (unchanged total) |

Before October 2024 NSE charged volume-dependent slabs; a retail account sat
in the top slab, about ₹4,950–5,000 per crore of premium on options and
₹188–190 per crore on futures. Modelled as 0.050% and 0.0019%, within
~0.0025% of premium of the true figure. Sources: NSE circular
[NSE/FA/73061](https://nsearchives.nseindia.com/content/circulars/FA73061.pdf)
(27 Feb 2026, read in full), [Zerodha bulletin](https://zerodha.com/marketintel/bulletin/391488/revision-in-transactions-charges-from-1st-october-2024),
[Upstox news](https://upstox.com/news/market-news/trading/bse-nse-revise-transaction-fees-for-cash-and-f-and-o-trades-revised-rates-to-come-into-effect-from-october-1/article-118923/).

## The small ones

| Charge | Rate | Note |
|---|---|---|
| SEBI turnover fee | 0.0001% (₹10/crore) both sides | modelled flat; ±₹5/crore in early years is immaterial |
| Stamp duty | options 0.003% of premium, futures 0.002%, **buy side only** | uniform since 1 Jul 2020; state rates before were similar and are modelled the same |
| GST | 18% of (brokerage + exchange + SEBI) | from 1 Jul 2017; 15% service tax before |
| Brokerage | ₹20 per executed order | flat discount-broker rate, the engine's assumption |

## What matters

On a ₹100 option premium sold today, STT alone is ₹0.15 per unit — about
four times the exchange charge and larger than everything else combined.
Any backtest that charged pre-2023 STT across all years would flatter an
option-selling strategy most in exactly the locked year.
