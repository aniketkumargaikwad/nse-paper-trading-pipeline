# The data, explained

Measured on 2026-08-22 by asking Dhan directly, not from documentation.

---

## 1. What we know about vs what we have

These are two very different numbers, and confusing them is the easiest
mistake to make here.

**We KNOW about 5,255 instruments.** That is the symbol master — a directory
of names and IDs, no prices:

| Kind | Where | How many |
|---|---|---|
| Shares | NSE | 2,735 |
| Shares | BSE | 2,333 |
| Indexes (NIFTY, BANKNIFTY, …) | NSE | 187 |

**We HAVE price history for 50 of them.** All NSE shares, all in the NIFTY 50.

So: **5,205 instruments have no data at all.** Not because we cannot get it —
we have simply never asked for it. Downloading is a decision, not a
limitation.

---

## 2. What we hold today

| Candle size | Symbols | Oldest | Newest | Candles |
|---|---|---|---|---|
| 1 minute | **2** | 2026-05-22 | 2026-08-21 | ~47,000 |
| 5 minutes | **200** | 2017-04-03 | 2026-08-25 | ~30.6 million |
| 1 day | **200** | **2002-01-01** | 2026-08-21 | 785,325 |

Expanded from NIFTY50 to NIFTY200 on 2026-08-28. The whole store is 660 MB of
Parquet against Supabase Storage's 1 GB free tier, so this cost nothing.

### Careful: `candle_coverage` is not "where the data starts"

That table records the range we have ASKED Dhan for, so it will not re-ask.
It is not a claim that candles exist across all of it. After backfilling
twenty years, every row says 2006 — but JIOFIN only listed in 2023 and has
746 candles, not twenty years of them.

Both facts are correct and they answer different questions. To ask "how much
history does this symbol really have", read the candles, not the coverage.

The 5-minute history is uneven, and more so at 200 symbols than it was at 50:

| 5-minute history starts | Symbols |
|---|---|
| 2017 (the full 9.4 years) | **155** |
| 2018-2020 | 17 |
| 2021 | 12 |
| 2023-2025 | 16 |

**Forty-five of the 200 listed after the intraday archive begins**, and 13 of
those have under three years - GROWW, ICICIAMC, SWIGGY, HYUNDAI and other
recent IPOs. A backtest across the whole universe is only as long as its
shortest symbol unless told otherwise, and those 13 contribute noise rather
than evidence to a nine-year test.

Daily history was extended to twenty years on 2026-08-22. It no longer starts
where the 5-minute data does; it goes far deeper, and costs almost nothing —
the whole 226,892 daily candles added about 10 MB.

**Companies do not all start at the same time**, and the young ones bound any
long test across the universe:

| Symbol | Listed | Candles |
|---|---|---|
| NSE:RELIANCE | 2002-01-01 | 6,126 |
| NSE:INDIGO | 2015-11-10 | 2,671 |
| NSE:SBILIFE | 2017-10-03 | 2,203 |
| NSE:HDFCLIFE | 2017-11-17 | 2,171 |
| NSE:MAXHEALTH | 2020-08-21 | 1,490 |
| NSE:ETERNAL | 2021-07-23 | 1,261 |
| NSE:JIOFIN | 2023-08-21 | 746 |

One hundred and twelve of the 200 reach back to 2006. A "twenty year" backtest across
the universe is therefore twenty years for most of them and three for JIOFIN.
The run record stores exactly which symbols were used, so this is visible —
but nothing corrects for it automatically.

---

## 3. What Dhan will actually give us

Probed against NSE:RELIANCE. The often-repeated "Dhan gives 5 years" is
**wrong** — it gives considerably more.

| Candle size | Reaches back to | Roughly | Candles per symbol |
|---|---|---|---|
| 1 day | **2006** | 20 years | ~5,000 |
| 5 minutes | **April 2017** | 9.4 years | ~173,000 |
| 1 minute | **April 2017** | 9.4 years | ~871,000 |

Asking for more than 20 years of daily is rejected outright. Both intraday
sizes stop at the same wall — **3 April 2017** — no matter how much more is
requested, so that is where Dhan's intraday archive begins.

Indexes work the same way: `NSE:NIFTY` and `NSE:BANKNIFTY` both return
candles.

**We are using a small fraction of what is available.** On the deepest
timeframe we hold 4 years of 5-minute data for 50 symbols; Dhan would give
9.4 years for any of 5,255.

---

## 4. Timeframes a strategy can use

| Timeframe | Available? | How it works |
|---|---|---|
| 1 minute | Yes — but only 2 symbols hold it | Stored directly |
| 5 minutes | Yes, all 50 | Stored directly |
| 15 / 25 / 30 / 60 minutes | Yes, all 50 | **Built from the 5-minute data** |
| 1 day | Yes, all 50 | Stored directly |
| Anything else (2h, 10m, 45m…) | Not yet | A one-line change, see below |

Only three sizes are ever **stored**: 1 minute, 5 minutes, and 1 day.
Everything between 15 and 60 minutes is **calculated on the fly** by grouping
5-minute candles.

That design has a real benefit: derived sizes can never disagree with each
other, because they all come from the same source. Adding a new one — 2-hour,
45-minute, whatever — needs no new download at all, only a new entry in the
timeframe list. **The data already on disk supports it.**

Two rules follow from this and are worth knowing:

* **Nothing below 5 minutes can be derived.** You cannot build 1-minute
  candles out of 5-minute ones — the detail is gone. That is why 1 minute is
  stored separately, and why it must be downloaded per symbol.
* **1-minute and 5-minute are kept independent.** 5-minute candles could in
  principle be rebuilt from 1-minute ones, but that would silently change
  every 5-minute candle already stored and every result computed from them.
  Each is fetched from Dhan on its own terms instead.

Candles are grouped from the **9:15 session open**, not from the clock hour.
A 30-minute candle therefore covers 09:15–09:45, not 09:00–09:30. This was
deliberate: a free data feed once produced "30-minute" bars starting at 09:00,
where the first bar of each day held only fifteen minutes of trading.

---

## 5. What more data would cost

Parquet stores a candle in roughly **21 bytes**. The database uses about
**155 bytes** for the same candle — seven times more. Today: 3.1M candles take
483 MB in Supabase, while the Parquet copy is 69 MB.

That ratio is what makes deeper history practical.

| If we downloaded | Candles | Parquet size | Download time (rough) |
|---|---|---|---|
| 50 stocks, daily, 20 years | 250,000 | ~5 MB | minutes |
| 50 stocks, 5-minute, 9.4 years | 8.7 million | ~180 MB | 1–2 hours |
| 50 stocks, 1-minute, 9.4 years | 43 million | ~900 MB | 6–10 hours |
| 500 stocks, 5-minute, 9.4 years | 87 million | ~1.8 GB | 10–20 hours |
| 500 stocks, 1-minute, 9.4 years | 435 million | ~9 GB | days |

Dhan serves 90 days per request, so time scales with how far back you go
multiplied by how many symbols. The 1-minute rows are the expensive ones:
five times as many candles as 5-minute, for the same period.

**Recommended order, cheapest and most useful first:**

1. **Daily, 20 years, all 50** — costs almost nothing and quadruples the
   testable history. Longer history is the single best defence against the
   regime problem found in the research: signals that reversed between 2024
   and 2026 can finally be checked across several market cycles.
2. **5-minute back to 2017 for the 50** — makes every intraday timeframe
   deeper at once, since 15/30/60-minute all derive from it.
3. **Widen to more symbols** before going to 1-minute. The research found the
   universe of 50 too narrow for cross-sectional work; breadth helps more than
   resolution.
4. **1-minute only for symbols that need it.** Five times the storage, and
   nothing yet suggests an edge lives there.

---

## 6. Things that will bite, and why

**Survivorship.** Our universe lists are today's membership applied to the
past. A stock that fell out of the NIFTY 50 is missing from the whole history,
so every universe result is flattered. This matters most for "buy the dip"
ideas, because the dips that never recovered are exactly the ones deleted. No
point-in-time membership is recorded yet, and every day without it is a day
that can never be tested honestly later.

**Corporate actions — found, and now corrected.** Dhan's daily feed is
adjusted for splits and bonuses; its intraday feed is raw. So EICHERMOT's
5-minute candles say 21,780 on 2020-08-21 and 2,178 the next morning: a 90%
overnight collapse that never happened. It was a 1:10 split, and nobody lost
a rupee.

This is the worst kind of data problem, because nothing errors and nothing
looks odd. A breakout rule simply finds the strongest signal in its entire
sample and reports the result.

**How bad it was:** 49 of the 200 symbols, 66 separate periods. EICHERMOT and
APLAPOLLO ×10 (a 90% overnight "crash" in the raw data), six 1:5 splits
(LAURUSLABS, UNITDSPR, SRF, CHOLAFIN, YESBANK, DIXON), GAIL three times, TMPV's
demerger, and dozens more.

**Mid-caps are worse than large-caps.** The original NIFTY50 contributed 21
corrections; the 150 stocks added for NIFTY200 contributed 45 - more than
twice as many from three times the symbols. Smaller companies split more
often, so widening the universe raises this risk faster than it raises the
symbol count.

**How it is fixed:** not by looking up split announcements. The daily feed is
adjusted to *today's* basis, so the gap between the two feeds on any past day
*is* the adjustment still owed to that day. `scripts/detect_adjustments.py`
measures that gap and stores it in the `price_adjustments` table; every read
through `CandleStore` applies it. Backtests never see the fake moves. Running
detection a second time now finds nothing, which is the check that the
corrections are self-consistent.

**Two things worth knowing:**

- *The stored candles are untouched.* Nothing was multiplied into the raw
  feed, because a corrected price is indistinguishable from a real one. The
  correction is 66 rows you can read, question, and delete. That is also why
  `audit_data_quality.py` still reports 8 splits — it reads the raw feed on
  purpose — but now marks each one **CORRECTED ON READ**.
- *Volume is deliberately NOT corrected.* A split really does change volume,
  so this looks like an oversight. It isn't. The measured volume gap tracks
  changes in how the two feeds count volume, not corporate actions:
  RELIANCE's is 0.50 across nine years and 1.01 over the last one, with no
  price change at all. Applying it would have doubled nine years of RELIANCE
  volume for nothing. **So volume-based rules are still wrong across a split
  date** — a known limit, not a hidden one.

**Holidays.** The NSE holiday list is a file we maintain, and it was wrong in
five places until the candles themselves were used to check it — including a
day it claimed was a holiday when the market actually traded. It is verified
against our data through 2026-08; later dates are unverified.

**Uneven starts.** Because 15 symbols only reach 2024, a "four year" backtest
across the universe is really four years for 35 symbols and two for the rest.
The run record stores exactly which symbols were used, so this is visible —
but it is not automatically corrected for.

---

## 7. How to get more

```bash
# One symbol, deeper daily history
python backfill.py --symbols NSE:RELIANCE --timeframe day --years 20

# Several symbols at 5-minute
python backfill.py --symbols NSE:RELIANCE,NSE:TCS --timeframe 5m --years 9

# 1-minute, which is the expensive one
python backfill.py --symbols NSE:RELIANCE --timeframe 1m --years 3
```

Backfilling is safe to repeat. It only fetches what is missing, so an
interrupted run can simply be started again.

After any backfill, check what arrived:

```bash
python scripts/audit_data_quality.py --dry-run
```
