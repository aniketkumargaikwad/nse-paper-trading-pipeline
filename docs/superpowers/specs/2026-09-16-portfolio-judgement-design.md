# Judge the whole basket, every month — Design

**Status:** Built 2026-09-16 under stated assumptions (the owner was not
available to approve; see §0.3) · **Revises:**
`2026-09-11-autonomous-research-loop-design.md` §5.3–5.5

---

## 0. In plain words

### 0.1 What the owner asked for

> A strategy that consistently makes more than 4–6% a month, on average,
> historically — backtested, then paper traded.

### 0.2 What the loop measured instead, and why that could never answer it

After eleven runs (12–15 September 2026) every locked-year verdict was
"failed". Looking at *why* shows a measuring problem, not only a strategy
problem:

| run | pick | trades in the locked year |
|---|---|---|
| 15 Sep | NSE:BSE, 25m | **1** |
| 14 Sep | NSE:KEI, day | 6 |
| 14 Sep | NSE:KPITTECH, 25m | **1** |
| 13 Sep | NSE:VMM, 25m | 48 |
| 12 Sep | NSE:SWIGGY, 15m | 71 |

The rule picked **one stock** out of ~1,177 tries — the one with the largest
lead over holding across eight years of training. The best of 1,177 is
nearly always a fluke, and a single stock cannot say anything about
"consistently" or "per month". Then the exam judged that one stock over one
year, often on one trade. A one-trade exam is a coin toss, whichever way it
lands.

Three smaller faults compounded it:

- When a day was cut short, the final version was chosen by **yearly
  return**, while the pick rule ranks by **lead over holding** — two rules
  that disagree, and the first one favours whatever rose most.
- The exam demands **10+ trades** to pass, but the pick rule accepts a
  combination that trades **once a month**. Such a pick could never pass.
- Opus was shown 15 best and 15 worst combinations, a total, and a per-
  timeframe count — never a month-by-month or year-by-year view. So it
  could not see whether an idea worked *steadily* or in one lucky stretch,
  and its lessons stayed generic ("check gross first").

### 0.3 What changes

The strategy is judged the way the owner would actually run it: **₹1 lakh
spread equally across all 200 stocks**, on one timeframe, **month by
month**. That gives hundreds of trades a year instead of one, and it gives
the number the goal is stated in — return per month — together with how
often months were positive and how bad the worst month was.

- **Opus sees**, per timeframe: average month, share of positive months,
  worst month, deepest fall, year-by-year returns beside holding, and a
  plain "luck check". Still training years only.
- **The pick** is a *timeframe*, not a stock: the timeframe whose basket
  earned the most per month in training, provided it beat holding the same
  basket, traded at least ten times a month, and never fell more than 30%.
- **The exam** runs that basket through the locked year. "₹1 lakh → became"
  now means the basket, rebalanced monthly.
- The morning message and the Research page show the monthly figures and
  whether the 4–7%-a-month target was met.
- The index of ideas Opus reads carries numbers (best monthly return,
  share beating holding), so repeating a family that already failed costs
  it nothing to avoid.

**Assumptions made without the owner** (each is reversible in one
constant):

1. Equal weight across every stock that has data that month; a stock joins
   the basket when its history begins.
2. Each stock's sleeve is ₹1 lakh notional per trade, as the sweep already
   sizes it; sleeve return for a month is that month's net P&L ÷ ₹1 lakh.
3. The basket compounds monthly (rebalanced at month end). The training
   window does not compound, so a decade of history reads as an average
   month, not a multiplied total.
4. The exam's "worst dip" is measured on closed trades, day by day. Money
   still inside an open position is not marked; with 200 sleeves this
   understates less than it did for one stock, and the message says so.
5. Indexes stay out of the basket (they are not stocks) and keep their
   per-timeframe lines in the summary.

Nothing about design 2.4 changes: the exam is still opened once, after
the last AI call, and nothing Opus reads carries a locked-year number.

---

## 1. Alternatives considered

| Option | Why not / why |
|---|---|
| Keep the single-stock pick, raise its trade floor | Still the best of 1,177; still one year of one stock. Measures luck more precisely. |
| Pick the best **N** stocks and judge those | Choosing N on training data is the same selection problem with N winners instead of one. |
| **Judge the equal-weight basket on one timeframe** | No stock selection at all. Hundreds of exam trades. Answers "per month" directly. **Chosen.** |
| Portfolio with a position cap (say 10 open at once) | More realistic for a small account, but adds an allocation rule that would itself need designing and fitting. Deferred; the basket's utilisation is reported so the owner can see how much capital actually worked. |

---

## 2. Definitions

### 2.1 Month series for one combination

`run_combo` already holds the candles. It now also records, per calendar
month (IST), the **first and last close** inside the window. From that:

- holding return for month *m* = last(m) ÷ last(m−1) − 1, or ÷ first(m)
  for the first month the symbol has data.
- strategy return for month *m* = Σ net P&L of trades **exiting** in *m*
  ÷ ₹1,00,000. Costs are the trade's own (already itemised, delivery when
  held overnight).

### 2.2 Basket (`research/portfolio.py`, pure)

For one timeframe and the stock combinations on it:

- month *m* return = mean over stocks **with data in m** of the sleeve
  return; holding likewise.
- `months`, `avg_month_pct`, `median_month_pct`, `months_positive_pct`,
  `best_month_pct`, `worst_month_pct`, `avg_excess_pct` (strategy − holding,
  per month), `worst_dip_pct` (deepest fall of the monthly-compounded
  curve), `trades_per_month`, `avg_stocks_in_month`, `sleeve_use_pct`
  (share of stock-months with at least one trade),
  `years`: one row per calendar year with strategy and holding return
  (compounded within the year) and trade count,
  `edge_t`: mean excess ÷ its standard error — reported to the owner as a
  luck check in words: ≥ 2 "unlikely to be luck", 1–2 "could be luck",
  < 1 "cannot be told from luck".

### 2.3 The pick rule (replaces 5.3)

Among stock timeframes whose training basket has **at least 24 months**,
**at least 10 trades a month** on average, **worst dip within 30%**,
**average excess over holding > 0** and **average month > 0**: choose the
highest **average month**. Ties go to higher excess. Nothing qualifying →
"no qualifying timeframe", exam not opened.

Ranking by return rather than by excess is deliberate: the goal is stated
as a return, and the excess gate already refuses beta. A strategy in the
market a third of the time cannot beat full-time holding by beta alone.

### 2.4 The exam (replaces 5.4)

The final version is simulated over the **full** window on every stock at
the picked timeframe; trades are split at `locked_from` by entry (the
existing `walk_forward.split_trades`, so indicators are warm). The locked
months form the basket series of §2.2.

- ₹1 lakh → became = 1,00,000 × Π(1 + month return).
- Just holding = the same with the holding months.
- Trades, wins, worst dip (daily, closed trades), and the monthly table
  `{month, strategy_pct, holding_pct, trades}`.
- Verdict (5.5) unchanged in form: ended above ₹1 lakh **and** ≥ 10 trades
  **and** worst dip ≤ 20%. `beat_holding` separate. New: `target_met` =
  average locked month ≥ 4%.

### 2.5 What Opus is shown (extends 5.2)

`TrainingSummary` gains `baskets`: one entry per stock timeframe with the
§2.2 figures. The review prompt says the basket figures are the ones to
judge by and that the aim is the average month. The top/bottom tables stay,
labelled as the luckiest single combinations.

### 2.6 Final version when the day is cut short

`choose_final` scores a version by its best qualifying basket's average
month (the §2.3 rule), never by yearly return.

### 2.7 Ideas index

`tried_ideas` lines become
`<name> — best basket <tf>: <avg>%/month, <positive>% months up, <beat> of <tested> beat holding — <lesson>`
read from the stored `training_summary`.

---

## 3. Storage

`sql/012_research_basket.sql`, additive:

```
research_runs
  basket_stocks           integer        -- how many stocks the basket held
  locked_avg_month_pct    numeric(8,3)
  locked_months_positive_pct numeric(6,2)
  locked_worst_month_pct  numeric(8,3)
  locked_target_met       boolean
  training_avg_month_pct  numeric(8,3)
  training_months         integer
  training_edge_t         numeric(8,3)
  locked_months           jsonb          -- [{month, strategy_pct, holding_pct, trades}]
```

`pick_symbol` holds the text `NIFTY200 basket (200 stocks)`; the grid
column is relabelled "Traded on". `research_locked_trades` receives the
basket's trades when there are 2,000 or fewer; above that only the daily
equity is stored and a warning says so. `research_locked_equity` is the
basket's daily balance beside equal-weight holding.

---

## 4. Message and page

Locked block: traded on, ₹1 lakh → became, just holding, **average month
vs the 4–7% target**, months positive, worst month, trades per month, worst
dip, verdict. Each version block gains "Best basket: <tf> +x.xx%/month,
y% months up, luck check: …".

Research page: grid gains "Return/month"; the detail shows the locked
monthly table.

---

## 5. Testing

Pure units for `portfolio.py` (month keys, first-month holding, missing
symbols excluded from a month's mean, compounding, dip, years, edge_t),
the new picker (each gate, tie-break, none qualifying), records (new
columns), message (target line, luck line), prompts guard (no locked word
in the new fields), `choose_final` (scores by basket, not yearly return).
Integration: `research.run_day --dry-run --max-stocks 5 --stock-timeframes
day,60m --no-save` end to end on the real store.
