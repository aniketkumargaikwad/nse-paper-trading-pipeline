# NSE factor indexes — what else beats NIFTY, measured honestly

Measured 21 Oct 2026 by `python scripts/factor_indexes.py`, the same way as
`2026-10-20-momentum-rotation.md`: NSE's own total-return indexes (dividends
in, each date's real constituents - no stock-list hindsight), held, and held
under the NIFTY 50 200-day cash rule (cash at 6%, 0.2% a switch). Nothing was
tuned; every index NSE publishes in this family that has history since 2005
is shown.

## In plain words

- **Midcap momentum is the strongest evidence found anywhere in this project.**
  NSE's Midcap150 Momentum 50 made **27.3% a year 2009–Oct 2025** just held, with
  a luck test of **t = 3.9** (the rule asks for 2) - about 2% a month, the
  owner's target band. Its worst fall was 39%; with the cash rule it made 23.7%
  a year with a worst fall of 32%, and **29.9% a year with a 16% worst fall in
  2021–Oct 2025**. It is now paper-traded with the cash rule (Paper books).
- Momentum among the wider 500 (22.6%, t = 2.3) and the Alpha 30 index (21.9%,
  t = 2.2) also clear the luck test held; Nifty200 Momentum 30 just does (t = 2.0).
- Quality, low-volatility and value beat NIFTY by less and do not clear it.
- **The cash rule trades return for smaller falls** on every index; it never
  passes the luck test itself, but it held up in the exam year (Oct 2025–Oct
  2026), a falling market for almost everything.

## The table

| index | rule | 2009-Oct 2025 a year (worst fall) | 2021-Oct 2025 a year (worst fall) | exam year a year (worst fall) | luck t (2009-2025) |
|---|---|---:|---:|---:|---:|
| NIFTY MIDCAP150 MOMENTUM 50 | hold | +27.3% (39%) | +29.1% (26%) | -3.6% (14%) | 3.88 |
| NIFTY MIDCAP150 MOMENTUM 50 | cash rule | +23.7% (32%) | +29.9% (16%) | +1.2% (8%) | 1.82 |
| NIFTY500 MOMENTUM 50 | hold | +22.6% (38%) | +24.4% (32%) | -2.7% (16%) | 2.31 |
| NIFTY500 MOMENTUM 50 | cash rule | +18.9% (30%) | +25.6% (22%) | -0.9% (10%) | 0.76 |
| NIFTY200 MOMENTUM 30 | hold | +21.1% (34%) | +20.5% (32%) | -7.9% (16%) | 2.02 |
| NIFTY200 MOMENTUM 30 | cash rule | +17.5% (23%) | +22.4% (19%) | +1.2% (9%) | 0.39 |
| NIFTY200 ALPHA 30 | hold | +21.9% (37%) | +24.5% (31%) | -1.1% (15%) | 2.22 |
| NIFTY200 ALPHA 30 | cash rule | +18.7% (25%) | +27.4% (18%) | +2.2% (9%) | 0.7 |
| NIFTY200 QUALITY 30 | hold | +20.3% (29%) | +13.8% (22%) | -7.4% (16%) | 1.96 |
| NIFTY200 QUALITY 30 | cash rule | +15.8% (17%) | +13.4% (13%) | -1.6% (8%) | -0.03 |
| NIFTY100 LOW VOLATILITY 30 | hold | +18.6% (31%) | +16.0% (18%) | -8.3% (14%) | 1.46 |
| NIFTY100 LOW VOLATILITY 30 | cash rule | +13.5% (19%) | +14.1% (13%) | +1.9% (6%) | -0.68 |
| NIFTY200 VALUE 30 | hold | +18.9% (58%) | +33.3% (23%) | -1.2% (15%) | 1.38 |
| NIFTY200 VALUE 30 | cash rule | +13.9% (51%) | +30.5% (16%) | +15.5% (5%) | 0.02 |
| NIFTY ALPHA LOW-VOLATILITY 30 | hold | +20.1% (31%) | +16.8% (25%) | -7.2% (13%) | 1.66 |
| NIFTY ALPHA LOW-VOLATILITY 30 | cash rule | +16.6% (18%) | +16.2% (15%) | +3.7% (6%) | 0.12 |
| NIFTY 200 | hold | +16.3% (38%) | +16.0% (18%) | -7.6% (15%) | 1.7 |
| NIFTY 200 | cash rule | +11.6% (24%) | +14.0% (13%) | +0.3% (6%) | -1.23 |
| NIFTY 50 | hold | +15.6% (38%) | +14.4% (16%) | -10.3% (15%) | 1.4 |
| NIFTY 50 | cash rule | +10.4% (24%) | +11.7% (14%) | +0.1% (6%) | -1.62 |

## Read with care

- Most of these indexes were launched in recent years (check each one's NSE
  factsheet); their earlier years are NSE's back-calculation. Constituents are point-in-time, but the
  rules were designed knowing the past - the 2021–2025 column is the closest
  to a live record.
- **Midcaps fall harder in a crash.** Over 2006-2026, which includes 2008:
  Midcap150 Momentum 50 held made 19.8% a year but **lost 65% in 2008** (72%
  from its peak); with the cash rule 20.6% a year, 44% lost in 2008 (49% from
  its peak). Nifty200 Momentum 30: -60% held, -42% with the rule. NIFTY 50:
  -51% held, -28% with the rule. The edge is real; so is this risk.
- Midcaps are less liquid. An index has no costs; a fund tracking it charges a fee.
- One exam year is one year. Not investment advice.
