# Momentum rotation — what is real once hindsight is removed

Measured 20 Oct 2026. Pre-registered in
`docs/superpowers/specs/2026-10-20-momentum-rotation-design.md` (variants and
pick rule written before any result). Code: `research/rotation.py`,
`scripts/rotation_test.py`, `scripts/nse_index_history.py`.

## In plain words

- **Our own backtest on today's 200 stocks is not believable.** Simply holding
  today's NIFTY 200 equally "made" 28% a year from 2009 against NIFTY 50's
  13.6% — about 15% a year of pure hindsight (today's list knows which
  companies grew). Every momentum number on that list (31–46% a year) carries
  the same bias.
- **NSE's own Nifty200 Momentum 30 index has no hindsight** — it used each
  date's real members — and it is the honest answer: **about 21% a year from
  2009 to Oct 2025 with dividends, against 15% for NIFTY 50 and 16% for
  NIFTY 200**. A real edge of roughly 5% a year, beating NIFTY 200 in about
  three years in four, but its luck test is t = 1.7 — close to, not over, the
  2.0 our rule asks for. Its worst fall was 34% (68% from 2006, through 2008).
- **The trend filter our test picked in advance halves the falls.** Holding
  the real Momentum index only while NIFTY 50 is above its 200-day average
  (cash at 6% otherwise, 0.2% per switch): **17.5% a year, worst fall 22.9%**
  in training; in the sealed exam year (Oct 2025–Oct 2026, a falling market)
  **+1.2%, against −7.9% for holding momentum and −10.5% for NIFTY 50**.
- **What this means for the goal:** the best-documented stock strategy we
  have found makes about 1.3–1.6% a month on average over many years — with
  losing years — not 4–6%. That is the honest ceiling of what this evidence
  supports.

## Our backtest (today's NIFTY 200, Yahoo adjusted, 0.2% a side) — training 2009–Oct 2025

| | per year | worst fall | beat NIFTY 50 |
|---|---:|---:|---:|
| R1 12-1 momentum, 20 stocks | 40.5% | 42.7% | 94% of years |
| R2 6-month momentum, 20 stocks | 38.3% | 38.7% | 88% |
| R3 R1 + NIFTY 200-day filter (**picked by the rule**) | 31.6% | 23.2% | 82% |
| R4 12-1 momentum, 10 stocks | 46.2% | 42.7% | 94% |
| all 200 held equally | 28.2% | 37.1% | 88% |
| NIFTY 50 (price) | 13.6% | 38.4% | — |

A first run had R3 in cash 71% of months: missing NIFTY days blanked the
200-day average for 200 sessions each. Fixed and tested before these numbers.

## The real indexes (NSE total return, dividends in)

| | 2009–Oct 2025 | worst fall | exam year Oct 2025–Oct 2026 | 2006–today | worst fall |
|---|---:|---:|---:|---:|---:|
| Nifty200 Momentum 30 | 21.1% | 34.0% | −7.9% | 16.5% | 67.7% |
| … with the NIFTY 200-day filter | 17.5% | 22.9% | **+1.2%** | 16.4% | 46.4% |
| NIFTY 200 | 15.7% | 38.0% | −7.3% | 12.2% | 63.7% |
| NIFTY 50 | 15.0% | 38.3% | −10.5% | 11.9% | 59.5% |

## Read with care

- An index has no costs; a fund tracking it charges a fee (and the filter's
  switches would pay tax on gains). The filter was tested at 0.2% a switch
  and 6% on cash.
- The filter's lead over NIFTY 50 also fails the luck test (t = 0.4): it
  mostly trades return for smaller falls.
- One exam year is one year. It went the filter's way; the next may not.
- Not advice: a measurement of what the evidence supports.
