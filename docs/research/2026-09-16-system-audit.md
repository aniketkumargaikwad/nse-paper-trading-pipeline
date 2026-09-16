# System audit, 16 September 2026: what stands between the tool and the goal

**Goal, as stated:** a strategy that consistently makes more than 4–6% a month
on average, proven by backtest and then by paper trading, found by an AI loop
that runs without the owner.

**Short answer:** the loop runs every morning and does what it was built to
do, but until today it was measuring the wrong thing, so it could not have
answered the goal even if a good strategy had appeared. Two of the six
requirements have no working machinery at all (paper trading, fresh data).
And the goal itself is far above what the data has shown so far: no idea has
yet beaten simply holding the stocks, let alone earned 4% a month.

---

## 1. What exists today, against the six requirements

| # | Requirement | State on 16 Sep |
|---|---|---|
| 1 | AI designs a strategy | **Works.** Opus 5, no tools, every morning on GitHub. 21 versions across 11 runs since 12 Sep. |
| 2 | Backtest across symbols and timeframes | **Works.** 200 NIFTY200 stocks × 6 timeframes + 9 indexes × 2, ~1,177 combinations a version, itemised fees including delivery. |
| 3 | Analyse, refine up to 5 versions, drop dead ideas | **Works mechanically; was judging the wrong thing** (§2). |
| 4 | Learn across days | **Weak.** Lessons were prose ("check gross first"); the same idea family came back three days running. Fixed today: the index now carries numbers. |
| 5 | Telegram / email | **Works** when secrets are set. The run never recorded whether a message was delivered (`notify_status` was always empty). Fixed today. |
| 6 | Fully autonomous | **Works.** Scheduled 09:03 IST daily, public repo, zero cost. 15 Sep ran out of Claude allowance at version 6; the limit is now 5. |
| — | **Paper trading passed** | **Does not exist.** No workflow runs the paper engine (retired with Railway); prices are frozen at 31 Jul 2026, so nothing could be paper traded anyway. Last paper run 22 Aug, zero trades ever. |

## 2. The measuring fault, and the fix made today

Every one of the eleven locked-year verdicts was "failed". Reading them
shows why that was inevitable:

| run | what was picked | trades in the exam year |
|---|---|---|
| 15 Sep | one stock (BSE), 25-minute bars | **1** |
| 14 Sep | one stock (KEI), daily | 6 |
| 14 Sep | one stock (KPITTECH), 25-minute | **1** |
| 13 Sep | one stock (VMM), 25-minute | 48 |
| 12 Sep | one stock (SWIGGY), 15-minute | 71 |

The rule chose **one stock out of 1,177 tries** - the one furthest ahead of
holding across eight years - and then examined that one stock over one year.
The best of 1,177 is almost always a fluke, and one stock trading once says
nothing about "per month" or "consistently".

Three smaller faults sat on top:

- A day cut short chose its final version by **yearly return**, while the
  pick rule ranked by **lead over holding**. Two rules that disagree; the
  first rewards whatever rose most.
- The exam demanded **10+ trades** to pass, but the pick allowed a stock
  trading **once a month**. That pick could never pass.
- Opus saw the 15 best and 15 worst stocks and a total, never a month-by-
  month or year-by-year view - so it could not tell steady from lucky.

**Fixed today** (design `docs/superpowers/specs/2026-09-16-portfolio-judgement-design.md`):
the strategy is now judged as **₹1 lakh spread equally over all 200 stocks on
one timeframe, month by month**. The pick is a timeframe, not a stock. The
exam runs that whole basket over the locked year and reports the average
month, how many months were up, the worst month, and whether the 4% floor was
met. Opus sees the same per-month figures for the training years, with a
plain luck check. A day cut short now picks by the same rule.

## 3. The other faults found

### Price data (measured on the parquet store, 675 MB)

- **Frozen at 31 Jul 2026.** Dhan's subscription lapsed 11 Sep. Only 16 of
  200 stocks have complete 5-minute sessions past 6 Aug. Nothing since July
  can be tested, and nothing at all can be paper traded.
- **Free top-up deadline is real and close.** Yahoo keeps about 60 trading
  days of 5-minute bars. Days after 31 Jul can be filled free only until
  roughly **23 Oct 2026**; after that August is lost unless Dhan is paid for.
- **The daily feed is not fully corporate-action adjusted**, contrary to the
  assumption in `config.py`. Twenty stocks show one-day close moves over 40%.
  Most are before 2010 (outside the training window), but **ADANIENT shows a
  −83% "crash" on 3 Jun 2015** that is a demerger, inside the training years.
  Smaller unadjusted actions (15–40%) were not scanned. Any daily strategy
  with a stop-loss is being hit by events that never happened.
- 3% of 5-minute sessions (6,342 across 196 stocks) have fewer than 70
  candles; 0.5% of candles carry zero volume. Neither is fatal; both mean a
  volume-based rule sees gaps.
- **Survivorship bias is unfixed**: today's NIFTY200 list is applied to
  2010. Every buy-the-dip idea is flattered, and there is no point-in-time
  membership to correct it.

### Costs and fills

- Fees are itemised and right for both intraday and delivery. Slippage is a
  flat 0.05% a side. For an intraday system trading 10 times a month per
  stock, fees plus slippage alone cost about **1.8% a month** - nearly half
  the 4% target before a single winning trade.
- **The paper engine still charges a flat ₹30 a trade** (`paper_engine.py`,
  `paper_machine.py`), not the itemised model the research uses. If paper
  trading is revived, its P&L will not match the backtest's.

### The loop

- Prompts carry both strategy-format documents (17 KB) every call; with the
  summary that is fine for five versions but was what exhausted the Pro
  allowance at seven.
- The review has no `final_version` field (the design asked for one); the
  final is always the last version Opus reviewed. Harmless, but it means
  Opus cannot say "v3 was better, use that".

## 4. The gap between the data and the goal

Plainly: **nothing tested so far has an edge.** Across 21 versions, the best
share of combinations beating holding was 25% (299 of 1,177). The earlier
hand-built batch found the same: momentum reverses out of sample, intraday
breakouts lose before fees, and the one survivor was fee-negative once
delivery charges were priced.

4% a month is 60% a year, compounded; 6% is 100%. The training years
returned about 418% over eight years for simply holding - roughly 23% a
year. The goal asks for three to four times the market, every month, net of
fees, on liquid large caps, with rules an AI can write in YAML. That is not
impossible, but it is well beyond what any published systematic strategy on
this universe sustains, and every day of results so far says so. The tool
will now measure the distance to the goal honestly; it cannot close it.

**What would move the needle, in order of value:**

1. **Fresh prices** - the Yahoo top-up before 23 Oct for 5-minute bars, or a
   Dhan renewal (₹499+GST a month). Without it every day tests the same
   frozen window, and paper trading is impossible.
2. **Fix the daily feed**: scan for unadjusted actions from 2010 on and
   record corrections the way `sql/010` already does for 5-minute bars.
3. **Revive paper trading on the free feed** for daily and 60-minute
   strategies only (Yahoo serves those), with the itemised cost model, and
   promote a strategy to it when the basket passes the exam.
4. **Point-in-time index membership**, from NSE's published changes, so
   buy-the-dip ideas can be trusted.
5. **A position cap** in the basket (say ten open at once) so the number the
   owner sees matches a real account rather than 200 funded sleeves.

## 5. What was changed today

- `research/portfolio.py`, `research/month_closes.py` (new): the basket.
- `research/picker.py`: picks a timeframe's basket, not a stock.
- `research/summary.py`, `research/prompts.py`: Opus sees the baskets and
  is told to judge on them.
- `research/evaluate.py`, `research/run_day.py`: the exam runs the basket;
  a cut-short day picks by the same rule; the ideas index carries numbers.
- `research/records.py`, `sql/012_research_basket.sql` (applied): the new
  columns.
- `research/message.py`, `app_pages/research_page.py`: monthly figures and
  the target on the phone and the page.
- `research/notify.py`: records whether the message was delivered.
- 46 new tests; 1,481 pass.
