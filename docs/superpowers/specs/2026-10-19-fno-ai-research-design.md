# F&O Step 4 — AI Research for Options — Design

**Status:** Owner chose this direction on 19 Oct 2026 ("go with 3"). Extends
`2026-10-02-fno-options-design.md` §7 and §12 step 4.

## 0. In plain words

- On **Monday, Wednesday and Friday** mornings the AI (the same Opus that
  runs the stock research) designs its **own options strategy**, instead of
  the textbook ones the atlases tested. On **Tuesday, Thursday and Saturday**
  it researches stocks as now. Sunday is off.
- It may try up to **five versions** a day. Each version is tested on
  **NIFTY and BANKNIFTY**, on the training years only, by the same engine and
  judged by the **same pass rule** (`fno/rules.py`): history, worst day ≥
  −10%, worst fall ≤ 30%, average month > 0, 10+ trades a month or the
  target month, beating holding the index in half its years, luck test t ≥ 2.
- The AI sees what every atlas strategy earned (training numbers only), what
  earlier options days learned, and every idea already tried — never anything
  from the exam year.
- Only if the day's final version **passes on an index** is the exam year
  opened, once, for that index. Otherwise the message says plainly that
  nothing passed.
- The message (Telegram and email) **starts with the verdict** in one line.

## 1. The day (`fno/ai/day.py`)

`python -m fno.ai.day` — the options twin of `research.run_day`, reusing its
loop (`research.loop.run_versions`), its Claude caller (`research.brain`, no
tools, the same model) and its choice of the final version
(`research.run_day.choose_final`).

1. Load NIFTY and BANKNIFTY (daily for positional; daily + minutes for
   intraday), cut at the training window's end.
2. Loop: propose → check → test on both indexes → review → next version,
   new idea or stop. Up to 5 versions, 4-hour budget.
3. Final version → the pick: of the indexes where it passes, the one with the
   best average month. None passes → no exam.
4. Exam: the final strategy on the pick's exam year; reported, stored.
5. Journal note, ledger lines, commit; the message.

`--dry-run` replaces the AI with a fixed atlas strategy, so the wiring is
proven without spending the Claude allowance.

## 2. What the AI is told (`fno/ai/prompts.py`)

- **Aim:** the owner's target month (`research.segment`), stated as for
  stocks, and the pass rule word for word. A rare-trade strategy is called
  out as failing ("10+ trades a month, or the target month").
- **What is tested and how:** ₹5,00,000; fills at the next open (daily) or
  next minute's open (intraday) with half the spread; NSE charges by date;
  SPAN-style margin estimate; whole sets sized by `sizing.max_margin_pct`;
  intraday out by `exit.time`; positional from Jan 2016, intraday from Oct
  2024 (12 months of training — the owner's rule); BANKNIFTY has only a
  monthly expiry since Nov 2024; minute data covers strikes within ±5% of the
  index.
- **The format:** `docs/OPTIONS_STRATEGY_FORMAT.md` (new, the AI's reference)
  and the fact names from `fno.strategy.FACT_NAMES`.
- **The atlas:** one line per atlas strategy and index from both atlases and
  the IV-rank test — training numbers and why it failed.
- **History:** the last 30 options journal lessons and every options idea
  tried (one line each, with its numbers).

## 3. The checker (`fno/ai/checker.py`)

Parses with `fno.strategy.parse` and sends any refusal back to the AI to
repair (3 attempts, as for stocks). Forces: `name`
(`O-YYYYMMDD-<idea>-vN`); `underlying` is replaced per index tested; a
missing `sizing.max_margin_pct` becomes the atlas convention (25 positional,
100 intraday). Refuses what the data cannot test:

- intraday `otm_pct` beyond 5 (no minutes there);
- intraday `expiry.which` above 1 (minutes cover an expiry's last 10–20
  sessions only).

## 4. What a tested version reports (`fno/ai/summary.py`)

Per index: the window, passes / why not, the headline (average and median
month, months up, worst day, worst fall, trades and trades a month, win rate,
days held, charges, holding's average month, years beating holding, t), the
year-by-year rows, the market-condition splits, the five worst days, and the
skip counts (a strategy skipped on most days is flagged). Every date in it is
before the exam year — a test asserts it.

## 5. Records

In the repository, under `research/journal/options/` (public; training
numbers and exam results only, never broker data):

- `<day>.md` — the journal note (training lessons only).
- `ledger.jsonl` — one line per version: idea, versions, strategy, training
  summary per index, review. **This is what later prompts read.**
- `exams.jsonl` — one line per opened exam. **Never read by the prompt
  builder** (a test asserts the prompt cannot contain it).

No database tables: nothing new to migrate, and the stock tables stay as
they are.

## 6. Fresh index minutes on GitHub (`fno/index_minutes.py`)

Dhan's login fails from GitHub (Oct 2026), so the exam year's index and VIX
minutes would stop at the last PC fill. Upstox serves them with the same
analytics token (verified 19 Oct 2026: NIFTY, Nifty Bank, India VIX 1-minute,
2022 and 2026). An Upstox provider joins the Dhan one; `fno-daily` tops the
indexes up from Upstox every weekday. A stored day from Dhan is compared
with Upstox before the switch; a mismatch beyond 0.05% stops it.

## 7. Schedule (`.github/workflows/`)

- `fno-research.yml` (new): Mon/Wed/Fri 03:33 UTC (09:03 IST). Pulls the F&O
  store from R2, runs the day, pushes the journal, sends the message. Shares
  the `research` concurrency group, so a stock day and an options day never
  run at once.
- `research.yml`: Tue/Thu/Sat only (was every day).

## 8. Not in this step

Paper trading of a passing options strategy (step 5); dashboard pages; the
AI choosing among more than NIFTY and BANKNIFTY; adjustments and per-leg
stops.
