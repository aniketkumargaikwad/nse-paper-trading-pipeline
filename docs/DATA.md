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

| Candle size | Symbols | Oldest | Newest |
|---|---|---|---|
| 1 minute | **2** | 2026-05-22 | 2026-08-21 |
| 5 minutes | 50 | 2022-08-15 | 2026-08-11 |
| 1 day | 50 | 2024-01-31 | 2026-08-20 |

The 5-minute history is uneven: **35 symbols reach back to 2022** (about four
years) and **15 only to 2024** (about two). A backtest across all fifty is
therefore only as long as its shortest symbol unless it is told otherwise.

The daily history is **shorter than the 5-minute history**, which looks
backwards and is worth understanding. It is not a Dhan limit — daily was
backfilled with `--years 2.5` and simply never asked for more.

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

**Corporate actions.** Dhan's daily feed is adjusted for splits and bonuses;
its intraday feed is not documented as adjusted. We detect the disagreement
automatically — `scripts/audit_data_quality.py` compares the two and flags
gaps the daily series does not corroborate. It found one real case:
**NSE:TMPV** shows a 40% drop across 2025-10-13/14 intraday, while the
adjusted daily feed moved 1.15%. Any backtest spanning that date on TMPV is
reading a price break that never happened.

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
