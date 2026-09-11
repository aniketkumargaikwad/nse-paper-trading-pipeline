# Autonomous Research Loop — Design

**Status:** Draft, awaiting review · **Date:** 2026-09-11

A system that, every morning with no laptop and no money spent, invents a
trading strategy, improves it up to seven times, tests it on every stock,
index and timeframe we hold, judges it on a year it never saw, and tells the
owner what happened.

---

## 0. In plain words

- **Every morning at 6:00 am India time**, GitHub starts the run on its own.
- **Claude Opus 5 invents one new strategy idea** — a title, a description and
  what it expects to happen. It reads the notes from earlier days first.
- **The tool tests it** on 200 stocks and 9 indexes, on every timeframe, using
  only the **training years** (everything before the last year).
- **Opus reads the results and decides**: improve it (next version), drop it
  for a different idea, or stop. At most **7 versions a day**.
- **The last year is locked.** Nothing that builds the strategy ever sees it.
  At the end it is opened **once**, to answer: *"₹1 lakh invested a year ago —
  what would it be now?"*
- **Opus is never told the locked-year results.** Only you see them. Otherwise
  it would slowly learn that one year by heart and the numbers would become
  meaningless.
- **You get an email and a Telegram message**, and the run appears as a row in
  a new **Research** page on the dashboard. Click the row for the full story.
- **Nothing is ever switched Live.** Every strategy is saved paused.
- **It costs nothing**: GitHub (public repository) does the computing, your
  Claude Pro plan does the thinking, Supabase stores results, Streamlit
  Community Cloud shows the dashboard.

Three details decided while writing this, which differ slightly from the
conversation:

1. **The tool, not Opus, picks the best stock and timeframe**, by a fixed rule
   (§5.3). Opus picks which *version* is final. A fixed rule cannot be tempted
   to cherry-pick.
2. **Short strategies must close the same day.** In the Indian cash market you
   cannot hold a short position overnight, so a test that does is testing
   something no broker allows (§5.6).
3. **Prices end on 27 August 2026, not 28.** The 5-minute data for 28 August
   stops at 15:10, so that day is incomplete. The last complete day is used.

---

## 1. Why this exists

### 1.1 Research is currently manual and slow

Each strategy so far was written by hand, backtested by hand, and read by hand.
The 2026-08-30 batch — six strategies — took an evening. The owner wants that
loop to run daily without him.

### 1.2 The obvious automation would lie

A naive loop — "try variations, keep the best, report it" — is the fastest way
to manufacture a strategy that looks excellent and is worthless:

- Up to 1,218 stock-timeframe combinations × 7 versions a day is ~8,500
  tries daily. Some will look brilliant by luck alone.
- If the builder can see the final year, repeated daily runs fit that year.
  With prices frozen, the final year is *the same year every day*, so this
  happens within weeks.
- Overnight trades are currently charged intraday fees, flattering every
  multi-day strategy by ~0.15% per trade
  (`docs/research/2026-08-30-candidate-batch-nifty200.md` §4).

The design exists as much to prevent those three lies as to automate.

### Non-goals

- **Fresh prices.** Data is frozen at 27 Aug 2026 by decision. A free Yahoo
  top-up is a later, separate piece; the 5-minute top-up must start before
  **15 Nov 2026** or the days after 28 Aug can never be filled for free.
- **Point-in-time index membership** — still unrecorded; results carry the
  survivorship warning instead.
- **Paper or live trading** of research output.
- **Multiple AI providers.** Claude via Claude Code only.
- **NIFTY500**, 1-minute data, options, futures.

---

## 2. Decisions and the reasoning behind them

### 2.1 GitHub Actions, public repository, for compute

| Option | Why not / why |
|---|---|
| Laptop | Must be on. Owner wants it off. |
| Railway | ~$5+/month. Budget is zero. |
| GoDaddy shared hosting | Built for websites; CPU/memory limits kill hour-long Python jobs; intro price renews higher. |
| Claude Code routines | Compute size undocumented, network allowlist, daily run cap. Good for thinking, wrong for number-crunching. |
| GitHub Actions, private repo | 2 CPUs, 2,000 free minutes/month (~66/day). Fits ~2–3 full versions a day. |
| **GitHub Actions, public repo** | **4 CPUs, 16 GB RAM, free standard runners, 6-hour jobs. Chosen.** |

Checked before choosing: no real secret has ever been committed (the three
`SUPABASE_SERVICE_ROLE_KEY=eyJh...` lines are documentation placeholders), and
no Supabase project ref, Dhan client ID or email appears in tracked files.

### 2.2 Claude Code on the Pro plan, Opus 5 for every step

`claude setup-token` issues a one-year OAuth token that authenticates Claude
Code with the Pro subscription; GitHub Actions accepts it as
`CLAUDE_CODE_OAUTH_TOKEN`. No API billing. Opus 5 is available on Pro.

Accepted cost: this draws on the same allowance as the owner's own Claude use.
A heavy day can exhaust it; the run then stops cleanly (§8).

### 2.3 Claude gets no tools

Each AI step is a single non-interactive call (`claude -p`) whose input is
assembled by Python and whose output is JSON. Claude cannot read files, run
commands, or reach the network. This is what makes §2.4 enforceable rather
than a promise: Opus can only know what the prompt builder chose to give it.

Exact CLI flags for disabling tools and requesting JSON are verified against
the Claude Code headless documentation during implementation, not assumed
here.

### 2.4 The locked year is structurally invisible to the builder

- The **training window** ends where the **locked year** begins.
- Every AI step's input is built only from `TrainingSummary` objects, which
  have no locked-year fields.
- Locked-year evaluation runs **after the last AI call of the day**.
- Research notes that Opus reads on later days are written from the training
  review only. Locked-year numbers live in `research_runs`, the dashboard and
  the messages — none of which the prompt builder reads.
- A test asserts the prompt builder's inputs contain no locked-year field
  (§9).

### 2.5 A fixed rule picks the stock and timeframe

Opus chooses the final *version*. The tool then picks that version's best
stock × timeframe by the rule in §5.3, on training data. The locked year then
judges exactly that one pick.

### 2.6 Every morning is a new idea

Yesterday's strategy is never continued. Lessons carry over through research
notes. Within a day, Opus may drop an idea and start a different one; every
version of every idea counts toward the limit of 7.

### 2.7 Summaries for versions, full detail only for the final one

Saving per-stock results and equity curves for all 7 versions would add
~100,000 rows a day and fill the free 500 MB database in about two months.
Versions store a compact summary; only the final version stores per-combination
rows. Estimated growth: under 15 MB/month (§6.3).

### 2.8 Indexes from Yahoo, daily and 60-minute only

Dhan's subscription has lapsed. Yahoo has free index history:

| Index | Yahoo symbol | Daily from | 60m from |
|---|---|---|---|
| NIFTY 50 | `^NSEI` | 2007-09 | 2023-10 |
| BANK NIFTY | `^NSEBANK` | 2007-09 | 2023-10 |
| NIFTY IT | `^CNXIT` | 2007-09 | 2023-10 |
| NIFTY AUTO | `^CNXAUTO` | 2011-07 | 2023-10 |
| NIFTY PHARMA | `^CNXPHARMA` | 2011-01 | 2023-10 |
| NIFTY FMCG | `^CNXFMCG` | 2011-01 | 2023-10 |
| NIFTY METAL | `^CNXMETAL` | 2011-07 | 2023-10 |
| FIN NIFTY | `NIFTY_FIN_SERVICE.NS` | 2011-09 | 2023-10 |
| NIFTY MIDCAP 100 | `NIFTY_MIDCAP_100.NS` | 2005-09 | 2023-10 |

Intraday index history beyond 60 days is not available free, so indexes are
tested on **day and 60m** only. Yahoo's 60m history is a rolling 730 days, so
the one-time download should happen soon. India VIX is excluded: it cannot be
traded.

Indexes cannot be bought directly; results on them stand in for an index ETF
or future. Yahoo's index "volume" is not real exchange volume, so **a strategy
whose rules use volume is not tested on indexes**, and the detail view says so.

---

## 3. Architecture

```
GitHub Actions (06:00 IST, or "Run workflow")
│
├─ restore price history ── GitHub cache ──(miss)── Supabase Storage backup
│
└─ python -m research.run_day
     │
     ├─ load research notes (training lessons only) ─────── Supabase
     │
     ├─ for up to 7 versions:
     │     brain.propose()   ── claude -p (Opus 5, no tools) ──> JSON
     │     checker.validate() ─ schema + research rules (§5.6)
     │     sweep.run_training() ─ 1,218 combos, 4 processes
     │     brain.review()    ── claude -p ──> decision + lessons
     │
     ├─ picker.best_combo(final version)      (training data only)
     ├─ locked.evaluate(pick)                 (opened once, after last AI call)
     ├─ store.save_run()                      ─────────────── Supabase
     ├─ journal.commit()                      ─────────────── repo (keeps schedule alive)
     └─ notify.send()                         ─── Gmail SMTP + Telegram Bot API

Streamlit Community Cloud ── Research page reads Supabase (anon key + password)
```

### 3.1 Modules

| Module | Purpose | Pure? |
|---|---|---|
| `costs.py` (extended) | `DeliveryCostModel`; per-trade fee choice by holding | yes |
| `research/windows.py` | `DATA_END`, training and locked windows per timeframe | yes |
| `research/sweep.py` | Run one strategy across all combos in parallel; build `TrainingSummary` | no (CPU) |
| `research/picker.py` | The §5.3 rule | yes |
| `research/lakh.py` | ₹1 lakh compounding and just-holding (§5.4) | yes |
| `research/checker.py` | Schema validation + research rules | yes |
| `research/prompts.py` | Builds AI inputs from training-only objects | yes |
| `research/brain.py` | Calls `claude -p`, parses JSON, classifies failures | no |
| `research/loop.py` | Version loop, limits, stop decisions, time budget | yes (brain and sweep injected) |
| `research/store.py` | Supabase writes for §6 tables | no |
| `research/notify.py` | Email and Telegram formatting and sending | formatting pure |
| `research/journal.py` | Writes and commits the daily notes file | no |
| `research/run_day.py` | Wires everything; the workflow entry point | no |
| `scripts/download_index_history.py` | One-time Yahoo index download into the parquet store | no |
| `app_pages/research_page.py` | Grid + detail view | no |
| `.github/workflows/research.yml` | Schedule, cache, secrets, run, commit | — |

`research/loop.py` takes the brain and the sweep as parameters, so the whole
decision logic is testable with fakes and no network.

---

## 4. One day's run, precisely

1. **Start.** Cron `30 0 * * *` (00:30 UTC = 06:00 IST) or manual dispatch.
   GitHub concurrency group `research` runs one at a time.
2. **Prices.** Restore `data/candles` from the GitHub cache keyed on
   `DATA_END`. On a miss (first run, or unused for 7 days), download once from
   the Supabase Storage backup (~660 MB, within the free 5 GB monthly egress).
3. **Windows.** Compute `DATA_END` = last date complete in every stored
   timeframe (today: 2026-08-27). Locked year = 2025-08-28 → 2026-08-27.
   Training = timeframe start → 2025-08-27, where start is 2017-04-03 for stock
   intraday, 2010-01-01 for daily, and 2023-10-04 for index 60m.
4. **Context for Opus.** Strategy format docs (v2 and v3), research rules
   (§5.6), the last 30 days of research notes, and a one-line index of every
   idea ever tried with its training outcome.
5. **Propose.** Opus returns JSON: `title`, `description`, `hypothesis`,
   `strategy` (YAML), `change_note` (empty for v1).
6. **Check.** Invalid → the error goes back to Opus, up to 3 attempts, which do
   not count as versions. Still invalid → the version is recorded as failed and
   counts toward 7.
7. **Test.** 200 stocks × {5m, 15m, 25m, 30m, 60m, day} + 9 indexes ×
   {60m, day} = up to 1,218 combinations on the training window (fewer when a
   volume rule skips the indexes), 4 processes.
8. **Review.** Opus receives the `TrainingSummary` (§5.2) and returns JSON:
   `why_failed`, `why_worked`, `lessons`, `decision`
   (`next_version` | `new_idea` | `stop`), and `final_version` when stopping.
   The review of the 7th version is told it is the last and must return
   `stop` with `final_version`.
9. **Loop** steps 5–8 until `stop`, 7 versions, or the time budget (§8).
10. **Pick.** §5.3 on the final version.
11. **Locked year.** §5.4 and §5.5, once.
12. **Save.** Strategies and versions to the library (paused) with title and
    description; run, versions, combo rows, locked trades and equity to §6
    tables.
13. **Journal.** Write `research/journal/YYYY-MM-DD.md` (training lessons
    only) and push it. This also stops GitHub disabling the schedule, which it
    does in public repositories after 60 days without activity.
14. **Notify.** Email and Telegram (§7.3).

---

## 5. Definitions

### 5.1 Fees

A trade is **delivery** when its entry and exit fall on different IST dates,
otherwise **intraday**. Delivery trades use `DeliveryCostModel`: STT 0.1% on
both legs, stamp duty on the buy leg, exchange, SEBI and GST as today, and
delivery brokerage (₹0 at Dhan; configurable). Deciding per trade, not per
strategy, means an intraday strategy that happens to hold overnight pays the
right fee too.

### 5.2 Training summary (what Opus sees)

Per version, training window only:

- Totals: trades, win rate, net P&L after fees, P&L before fees, fees paid.
- Per timeframe: trades, net P&L, share of symbols profitable.
- Top 15 and bottom 15 combinations by net P&L, with trades, win rate and
  worst dip.
- Long vs. just-holding over the same window, per timeframe (so Opus can tell
  edge from a rising market).
- Combinations skipped and why (too little data, volume rule on an index).
- The number of combinations tested, stated plainly.

### 5.3 The pick rule

Among the final version's combinations with **at least 30 training trades**
and a **training worst dip no deeper than 30%**, choose the highest
**compounded annual return after fees** on the training window. Both the
return and the worst dip are computed exactly as in §5.4, over the training
window instead of the locked year. Ties go to
more trades. If nothing qualifies, the run records "no qualifying pick" and
the locked year is not opened — this is a result, not an error.

### 5.4 ₹1 lakh

Start with ₹1,00,000 on the first day of the locked year. Trade the pick in
order. Each trade uses the whole balance at that moment; its fees are
recomputed on that balance with the §5.1 model. Money earns nothing between
trades. The value on `DATA_END` is **₹1 lakh → became**.

**Just holding:** buy the pick's symbol at the first close of the locked year,
sell at the `DATA_END` close, delivery fees once. An index is charged the
same fees, standing in for an index ETF. For a short strategy the column
still shows holding the symbol long, labelled as the market's move.

### 5.5 Locked-year columns

| Column | Definition |
|---|---|
| Trades / month | Locked-year trades on the pick ÷ 12 |
| Success ratio | Winning trades ÷ trades, locked year, pick |
| Worst dip | Largest fall from a running high of the ₹1 lakh balance |
| Verdict: Passed | Ended above ₹1 lakh **and** at least 10 trades **and** worst dip no deeper than 20% |
| Beat holding | ₹1 lakh result > just-holding result (shown separately from the verdict) |
| Broad or lucky | Training: combinations profitable ÷ combinations tested, for the final version |

### 5.6 Research rules (checked, not requested)

- Sizing is forced to `notional_per_trade: 100000`.
- `universe`, `instruments` and `timeframe` in the proposal are ignored; the
  sweep supplies them.
- `position_type: short` requires `session.square_off`.
- `enabled` is forced to `false`.
- Names are generated: `R-YYYYMMDD-<idea>`, versions through the existing
  `strategy_versions` table.

---

## 6. Data model

All tables additive, RLS enabled, `anon` read-only, matching existing tables.

### 6.1 `strategies` gains

`title text`, `description text`, `hypothesis text`,
`origin text` (`manual` | `research`). The stored `timeframe` is the pick's
timeframe; `sql/001_init.sql` limits it to 15m/30m/60m/day, so the migration
widens that check if a later migration has not already done so.

### 6.2 New tables

**`research_runs`** — one per run (the grid row)
`id, trigger, started_at, finished_at, status (completed | stopped_limit |
stopped_time | failed), failed_step, data_end, locked_from, locked_to,
final_strategy_name, final_version_id, pick_symbol, pick_timeframe,
locked_trades, trades_per_month, win_rate_pct, lakh_end_value, hold_end_value,
worst_dip_pct, verdict_passed, beat_holding, combos_profitable, combos_tested,
versions_tried, ideas_dropped, ai_review, warnings jsonb, notify_status jsonb`

**`research_versions`** — one per version attempt
`id, run_id, idea_no, version_no, strategy_name, strategy_version_id, valid,
error, change_note, why_failed, why_worked, lessons, decision,
training_summary jsonb, created_at`

**`research_combo_results`** — final version only
`run_id, symbol, timeframe, trades, win_rate_pct, net_pnl, cagr_pct,
worst_dip_pct, skipped_reason`

**`research_locked_trades`** — the pick's locked-year trades
`run_id, entry_at, exit_at, side, entry_price, exit_price, fees, net_return_pct,
balance_after`

**`research_locked_equity`** — daily balances
`run_id, day, lakh_balance, hold_balance`

**`research_notes`** — what Opus reads on later days
`run_id, day, idea_title, outcome_training, lessons`

### 6.3 Size estimate

Per day: ~1,218 combo rows (~200 B), ≤ 60 trade rows, ≤ 500 equity rows,
≤ 7 version rows with ~20 KB summaries. About **0.4 MB/day, ~12 MB/month**.
The free 500 MB database lasts years at this rate.

---

## 7. Dashboard and messages

### 7.1 Research page (grid)

`st.dataframe(on_select="rerun", selection_mode="single-row")` — supported by
the pinned Streamlit 1.41.1. Columns: Date · Strategy · Description ·
Trades/month · Best stock/index · Best timeframe · Success ratio ·
₹1 lakh → became · Just holding → became · Worst dip · Verdict · Beat holding ·
Broad or lucky · Versions · Run status. Newest first.

### 7.2 Detail view (selected row)

1. Strategy: title, description, hypothesis, rules in plain words, the exact
   strategy file.
2. Version timeline: each version's change note, decision, and training
   headline.
3. Opus's review: why it failed, why it worked, lessons.
4. Stock × timeframe table for the final version, pick highlighted.
5. Locked year: ₹1 lakh vs. just-holding chart, trade list.
6. Warnings: frozen prices, survivorship, few trades, combinations tested.

### 7.3 Messages

Telegram (truncated to the Bot API's 4,096-character limit):

```text
📊 Research run — 12 Sep 2026
Idea: <title> (final: v4, 5 of 7 tried)
Best pick: <symbol> · <timeframe> (chosen on training years)
Locked year: ₹1,00,000 → ₹1,08,400
Just holding: ₹1,00,000 → ₹1,12,900
Won 46% of 38 trades · worst dip −6.1%
Verdict: ✅ Passed · Did not beat holding
Why: <one sentence from the review>
Details: <dashboard link>
⚠️ Prices frozen at 27 Aug — decide on top-up by 15 Nov
```

Email: the same, plus the version timeline, the full review, and the best and
worst five combinations. Sent through Gmail SMTP with an app password.

### 7.4 Hosting

Streamlit Community Cloud with the **anon** Supabase key (view only) and
`APP_PASSWORD` set — the existing gate refuses to run hosted without one.

---

## 8. Error handling

| Failure | Behaviour | Status |
|---|---|---|
| Price restore and backup download both fail | Stop before any AI call; message "run skipped" | `failed` / `prices` |
| Opus output not valid JSON or strategy | Error returned to Opus, 3 attempts; then a failed version | continues |
| Claude usage limit or auth error | Stop; save finished versions; the valid version with the best §5.3 score becomes final and the locked year is still evaluated (it needs no AI) | `stopped_limit` |
| One combination errors | Recorded as skipped with reason | continues |
| Elapsed + slowest version so far > 5 h | No new version; final version chosen by best §5.3 score; locked year evaluated | `stopped_time` |
| Supabase write fails | Results written to a JSON file uploaded as a workflow artifact; message says so | `completed` + warning |
| One notification channel fails | The other still sends; failure in `notify_status` | continues |
| Claude token within 30 days of expiry | Warning line in every message (repo variable `CLAUDE_TOKEN_CREATED`) | — |
| Workflow crashes outright | GitHub marks the run failed; no message is the signal | — |

**Known GitHub limit:** a concurrency group holds one running and one waiting
run. A third request while one is waiting replaces the waiting one.

**Security for a public repository:** the workflow triggers only on `schedule`
and `workflow_dispatch` — never `pull_request` or `pull_request_target` — so
no outside contribution can run with the secrets.

---

## 9. Testing

**Pure units**

- Delivery vs. intraday classification and the delivery fee arithmetic.
- `windows`: `DATA_END` from partial days; per-timeframe training starts.
- `picker`: qualifying filters, tie-break, "no qualifying pick".
- `lakh`: compounding, per-balance fees, just-holding, worst dip.
- `checker`: forced sizing and `enabled`, short without `square_off` rejected,
  volume rule flags index skipping.
- **Locked-year isolation:** every object reachable from `prompts` inputs is
  scanned; any field or value derived from the locked window fails the test.
- `notify`: formatting, Telegram truncation.

**Loop with fakes** (`FakeBrain`, `FakeSweep`)

- Stops at 7 versions; `new_idea` versions count toward 7.
- Invalid strategy retried 3 times, then counted.
- Usage-limit error → `stopped_limit`, finished versions saved, best §5.3
  version made final, locked year still evaluated.
- Time budget → `stopped_time`, same fallback.
- 7th review without `stop` is rejected and retried as a final-choice call.
- Locked evaluation happens after the last brain call (call-order assertion).

**Integration**

- Local end-to-end on 5 stocks + 1 index × 2 timeframes with `FakeBrain`.
- One real Opus call with a fixed tiny summary, run by hand.
- First GitHub run dispatched manually, watched to completion.

---

## 10. Build order

Each piece gets its own implementation plan and works on its own.

1. **Honest numbers** — delivery fees, parallel sweep, windows, picker, ₹1
   lakh, one-time index download. *Done when:* a hand-run CLI prints the pick
   and locked-year result for an existing strategy across all 1,218
   combinations.
2. **Research page** — §6 migrations, store, grid and detail view. *Done
   when:* a hand-run result appears as a clickable row.
3. **AI loop** — prompts, brain, checker, loop, notes. *Done when:* a laptop
   run completes a full day with real Opus calls and writes a row.
4. **Going hosted** — workflow, cache, secrets, journal commit, messages,
   Streamlit Cloud, repo made public. *Done when:* a scheduled run at 06:00 IST
   arrives by email and Telegram with the laptop off.

Before piece 4 makes the repository public, the owner confirms again.

---

## 11. Risks

- **Pro allowance.** Opus 5 for every step may exhaust the plan on heavy days,
  and competes with the owner's own use. Mitigation: clean stop; a later switch
  to Sonnet for proposals is a one-line change.
- **The owner is also a leak.** Choosing which strategies to pursue after
  reading locked-year results fits that year by hand. The detail view says so.
- **Frozen data ages.** The locked year never moves until prices are topped
  up; after 15 Nov the free 5-minute top-up is no longer possible.
- **Survivorship.** Today's NIFTY200 applied to the past flatters results,
  most of all for buy-the-dip ideas.
- **Many tries.** ~8,500 combinations a day guarantee lucky-looking results in
  training; only the locked year is evidence, and a single passed run is not
  proof.
- **Public strategies.** Anyone can read the code and research notes.
- **Moving targets.** Claude Code CLI flags and GitHub runner specs can
  change; both are verified at implementation and monitored by the daily run
  failing loudly.
- **Cache eviction.** A pause over 7 days forces one ~660 MB re-download,
  within the free egress allowance.
- **Supabase free projects pause** after a week without activity. The daily
  run keeps the project active; a long pause of the workflow would need the
  project resumed by hand in the Supabase dashboard.

---

## 12. Sources

- GitHub Actions billing — https://docs.github.com/en/billing/concepts/product-billing/github-actions
- GitHub-hosted runner hardware — https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- GitHub Actions limits — https://docs.github.com/en/actions/reference/limits
- GitHub dependency caching — https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching
- Claude Code GitHub Actions (subscription token, schedule, 60-day rule) — https://code.claude.com/docs/en/github-actions
- Claude Code authentication (`setup-token`, one-year token) — https://code.claude.com/docs/en/authentication
- Claude Code model configuration (Pro default and Opus access) — https://code.claude.com/docs/en/model-config
- Claude Code routines — https://code.claude.com/docs/en/routines
- Supabase egress quotas — https://supabase.com/docs/guides/platform/manage-your-usage/egress
- Railway cron jobs — https://docs.railway.com/reference/cron-jobs
- Railway volumes — https://docs.railway.com/reference/volumes
- Streamlit Community Cloud resource limits — https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app
- OpenRouter free-model limits — https://openrouter.ai/docs/api-reference/limits
- Twilio WhatsApp business-initiated messages — https://www.twilio.com/docs/whatsapp/api
- Yahoo Finance index availability — measured 2026-09-11 with `yfinance`
- Dhan Data API lapse — measured 2026-09-11 (HTTP 451, "not subscribed to Data APIs")
