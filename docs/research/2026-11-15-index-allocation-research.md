# The AI research, rebuilt on honest data — 15 Nov 2026

## In plain words

The stock AI was not finding a winning strategy for three reasons, and none of
them was the AI being weak.

1. **It could only turn eight dials on one rule.** Its whole toolbox was: how
   many stocks, how many months to look back, one trend filter, one volatility
   filter. After 19 versions it was down to trying 30, 25 and 15 stocks. A
   plain list would have covered that in a minute.
2. **Its test was bent.** Every version was tested on *today's* NIFTY 200 - a
   list that already knows which companies grew. That adds about 12% a year
   of hindsight, so no number could be believed on its own, and a version
   could only be judged against another version on the same bent list.
3. **A pass went nowhere.** Two versions did pass, on 10 and 12 November, and
   both beat the paper-traded rule in the exam year. Nothing started
   paper-trading them. The message said "passed" and the system moved on.

All three are fixed.

## What the AI does now (Tuesday, Thursday, Saturday mornings)

It designs an **index allocation plan**: which of NSE's indexes to hold through
index funds or ETFs, how much of the account each gets, and when to sit in
cash. It can combine:

- 33 NSE indexes: broad (NIFTY 50, Midcap 150, Smallcap 250...), factor
  (momentum, quality, low volatility, value, alpha) and sector
- ranking them by recent strength, over one to three look-back periods
- holding the strongest one, two or more
- three kinds of protection: the market's trend, each index's own trend, and
  a volatility target that invests less when prices swing violently
- deciding monthly or weekly

## Why this test can be believed

- **Real history, no hindsight.** NSE publishes each index's history using the
  companies that were actually in it on each date, dividends included.
- **2008 is in the test.** The history runs from April 2006, so every plan has
  to live through the crash that halved the market (and the sharp fall of
  May 2006, which turns out to be the hardest month for these plans).
- **No lucky decision day.** Every plan is run as equal parts that decide on
  different days: a monthly plan as four parts a week apart, a weekly plan as
  five parts, one per weekday. See "The pass that was luck" below for why.
- **Costs are charged.** 0.2% of every rupee switched, and 0.75% a year on
  invested money for the fund's fee and its shortfall against the index. Cash
  earns 6% a year. *Tax is not counted.*
- **The last year is kept hidden** from the AI and opened only for a plan that
  passes.

## The pass rule

The first two lines are the owner's own numbers.

| Check | A plan passes only if |
|---|---|
| Return | the average month is at least **1.5%** |
| Fall | the worst fall from a high is no more than **35%** (2008 counts) |
| Years | it beat NIFTY 50 in at least half the calendar years |
| Luck | its edge over NIFTY 50, after allowing for time spent in cash, would come from luck less than 1 time in 200 (t at least 2.6) |
| Both halves | that edge is there in both halves of the history |
| Better | it is not worse on both return and fall than a plan already paper-traded |
| Not fitted | with each of its settings nudged by about a fifth, at least 80% of the nudged plans still hold up |
| Exam year | in the hidden last year it did not fall more than 35% and was not more than 5 points behind NIFTY 50 |

**A plan that passes is paper-traded automatically from the next session.** It
appears on the Paper books page by itself, is reviewed 6 and 12 months from
its own first day by the rule in `docs/TRACKING.md`, and the day's message
says so. At most six such plans run at once.

## The pass that was luck (15 Nov 2026) - and what it changed

On the first real day the AI's first plan PASSED: Midcap150 Momentum 50 under
a 20% volatility target plus the monthly NIFTY cash rule - 1.63% a month, a
34.6% worst fall, luck t 3.4. It was checked before being believed:

| The same plan, decided... | Average month | Worst fall |
|---|---:|---:|
| on the month's last session (as tested) | 1.64% | 34.5% |
| 3 sessions later | 1.57% | 35.5% |
| 5 sessions later | 1.49% | 36.8% |
| 8 sessions later | 1.54% | 39.4% |
| 10 sessions later | 1.45% | 38.9% |
| 13 sessions later | 1.37% | 45.4% |
| 15 sessions later | 1.46% | 49.3% |
| 18 sessions later | 1.47% | 42.0% |

The month's last session was the luckiest of the eight days on both counts. A
rule that switches the whole account at once depends heavily on which day it
looks, and nobody can pick that day in advance. **The pass was withdrawn before
it traded a day, and the test was changed so this cannot happen again:** every
plan is now run as four parts deciding a week apart. Judged that way the same
plan makes **1.51% a month with a 35.4% worst fall - a near miss, not a pass.**

The first test day also moved by a week (3 to 10 April 2006): on the earlier
date a 12-month look-back was one day short, so plans sat in cash for their
first month by accident.

## Where things stand

Twelve standard plans, measured as above (four parts, training years only).
They are re-measured every day and given to the AI as its starting map.

| Plan | Average month | Worst fall | Luck t |
|---|---:|---:|---:|
| Hold NIFTY 50 | 1.07% | 60% | - |
| Hold Midcap150 Momentum 50 | 1.72% | 73% | 2.5 |
| Midcap150 Momentum 50 + monthly NIFTY cash rule *(paper book)* | 1.56% | 45% | 2.5 |
| Momentum 30 + monthly NIFTY cash rule *(paper book)* | 1.23% | 40% | 1.6 |
| Midcap150 Momentum 50 + NIFTY cash rule, weekly | 1.66% | 41% | 2.8 |
| Midcap150 Momentum 50 above its own 200-day average, weekly | 1.49% | 37% | 2.4 |
| Midcap150 Momentum 50 above its own 200-day average, monthly | 1.33% | 45% | 1.7 |
| Midcap150 Momentum 50, volatility target 18% | 1.64% | 41% | 3.1 |
| Midcap150 Momentum 50, volatility target 15% | 1.51% | 39% | 2.8 |
| Midcap150 Momentum 50, vol. target 15% + own average, weekly | 1.35% | 26% | 2.6 |
| Top 2 of 8 factor indexes, above own average | 1.26% | 44% | 1.2 |
| Top 3 of 8 factor indexes, each beating cash, weekly | 1.14% | 54% | 1.0 |

**None of the twelve passes.** What they show:

- The honest numbers are lower than anything quoted before. With 2008 in, fees
  charged and no lucky decision day, the two momentum paper books made about
  1.2-1.6% a month with a worst fall of 40-45% - not the 17-24% a year with a
  23-32% fall measured from 2009 on the month's last session.
- The return target and the fall limit pull against each other. Plans reach
  1.5-1.7% a month with a 39-41% fall, or a 26% fall with 1.35% a month. **The
  gap to a pass is a few points of fall, or about 0.15% a month.**
- That gap is the AI's job now. Every message ends with "Closest so far": the
  best return inside the fall limit, and the smallest fall at the monthly
  target - or, once one plan has both, what that plan still fails.

## The first real day under the corrected test (15 Nov 2026)

Nothing passed. Four versions, all Midcap150 Momentum 50 under a volatility
target plus its own 200-day average, decided weekly:

| Version | Average month | Worst fall | Luck t | Fails |
|---|---:|---:|---:|---|
| volatility target 20% | 1.46% | 29.5% | 2.69 | return (needs 1.5%) |
| volatility target 25% | 1.48% | 33.3% | 2.58 | return; luck (needs 2.6) |
| volatility target 30% | **1.50%** | **33.3%** | 2.56 | luck only |
| (the same plan sent again) | | | | wasted version |

**The closest plan has both of the owner's numbers - 1.50% a month and a 33%
worst fall - and misses only the luck test, 2.56 against 2.6.** The bar was set
before the run and was not moved. Loosening the volatility target from 20% to
30% bought return but thinned the edge, which is what a rule with no more to
give looks like; the next gain has to come from a different idea.

Two things the run exposed were fixed the same day: a plan identical to one
already tested is now refused (the AI is told why and proposes another), and
the "Closest so far" line now says what a plan with both numbers still fails.
The AI asked for one tool the format does not have - a band around a trend
filter, so an index is not sold and re-bought on every small crossing. That is
the natural next addition.

If weeks pass and the two rows do not meet, that is itself the answer: on real
history, 1.5% a month with a 35% worst fall may not exist, and the choice
becomes which of the two numbers to give ground on. That choice is the owner's.

## Also changed

- **The stock-rotation pass of 12 November is now paper-traded** ("Momentum
  rotation, 20 stocks, no skip month"), from 16 November.
- **A crashed job now sends a Telegram message** the same hour. From 24 October
  to 7 November every research day crashed in silence.
- The old stock-rotation research can still be run by hand (the `rotation`
  track of the research job). The options research is unchanged.

## Read with care

- Most factor indexes were launched in the last few years; their earlier years
  are NSE's own back-calculation. The companies in them are the real ones for
  each date, but the index rules were written knowing the past.
- Before real money, check that a fund or ETF tracks the index a plan uses and
  what it charges.
- Paper trading stays the judge. Nothing here places an order. Not investment
  advice.

## Where the code is

`research/alloc.py` (the replay), `research/alloc_day.py` (the day, the pass
rule, the prompt), `research/paper_books.py` (`alloc` books, `promoted.json`),
`scripts/alert_failure.py`, `.github/workflows/research.yml` (track
`allocation`). Records: `research/journal/alloc/`.
