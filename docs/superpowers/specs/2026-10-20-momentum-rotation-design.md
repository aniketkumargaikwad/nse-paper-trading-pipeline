# Momentum rotation — pre-registered test — Design

**Status:** Written 20 Oct 2026 before any result was seen. The owner gave a
free hand ("do whatever and pull me closer to my motive").

## Why this, and why now

28 option strategies and every single-stock rule tested so far fail the
pass rule. The one stock strategy family with decades of published evidence,
in India too (NSE runs a Nifty200 Momentum 30 index on it), is cross-sectional
momentum: own the stocks that rose most over the past year, rebalance
monthly. The research loop cannot test it, because a rule there reads its own
stock only and cannot rank across stocks.

## The test (`research/rotation.py`, `scripts/rotation_test.py`)

- **Prices:** Yahoo daily closes, split- and dividend-adjusted, 2008 to
  today, for today's NIFTY 200 and the NIFTY 50 (`^NSEI`).
- **Decision:** at each month's last session, rank every stock with a price
  then and 12 months earlier by its return over the lookback (skipping the
  last month where the variant says so). Hold the top N equally.
- **Fill:** the NEXT session's close, so a decision never trades on the price
  it was made from.
- **Costs:** 0.2% of the value traded, on every buy and every sell (delivery
  STT, stamp duty, exchange fees, GST, plus slippage). Rebalancing back to
  equal weight is charged too.
- **Trend filter (where the variant says so):** if NIFTY 50 closes below its
  200-session average on the decision day, sell everything and hold cash
  until a later month-end says otherwise.
- **Training:** Jan 2009 to 17 Oct 2025. **Exam:** the year after, opened
  once, for the one variant the rule below picks.

## The variants, fixed now — all reported

| | lookback | skip last month | holdings | trend filter |
|---|---|---|---:|---|
| R1 | 12 months | yes | 20 | no |
| R2 | 6 months | no | 20 | no |
| R3 | 12 months | yes | 20 | yes |
| R4 | 12 months | yes | 10 | no |

## Judged against

- **NIFTY 50** (a price index, so it misses about 1.2% a year of dividends —
  stated beside it).
- **The same 200 stocks held equally** and rebalanced monthly: the fair
  comparison, because it carries the same survivorship bias.

## The pick rule

Among the variants whose training worst fall is 30% or less and that beat
NIFTY 50 in at least half their years, the one with the best training CAGR.
If none qualifies, the exam is not opened and the report says so.

## The big caveat: survivorship

Today's NIFTY 200 applied to 2009 knows which companies became big. That
flatters every strategy on this list, momentum perhaps most (it buys stocks
on their way up into the index). The equal-weight comparison carries the
same bias, so the momentum lead over it is the cleaner number — but no
result here is believed until it is checked against NSE's own published
Nifty200 Momentum 30 index, which used the constituents of each date, and
then paper-traded.
