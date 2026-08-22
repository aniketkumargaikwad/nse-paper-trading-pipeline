# Operating Guide — Master Reference

> Setting up for the first time? Follow **[QUICKSTART.md](QUICKSTART.md)**
> instead — it's the shortest path to a running system. This file is the
> complete reference behind it.


This is the **single source of truth** for setting up, running, and extending the
paper-trading pipeline. It is written for someone who has never touched this
project before. Every step says **where to be** and **exactly what to type**.

> ## ⚠️ Read this first: what this system is (and is not)
> This is a **paper-trading / research** system. It **simulates** trades and
> writes them to a database. **No part of it can place, change, or cancel a
> real order** — the broker connection is read-only market data only.
> Results are optimistic (fills, slippage, and costs are modeled, not real).
> Live automated trading is a different, regulated problem and must never be
> bolted onto this repo.

---

## 0. The 60-second mental model

```
  YOUR LAPTOP (whenever you like)                RAILWAY (runs by itself)
  ┌──────────────────────────────┐    ┌──────────────────────────────────────┐
  │ backfill.py                  │    │ dashboard  - the website              │
  │   downloads price history    │    │   run backtests, edit strategies,     │
  │   from Dhan into parquet     │    │   read results                        │
  │ backtest.py                  │    │                                       │
  │   the same runs, faster      │    │ paper engine - a cron job              │
  └──────────────────────────────┘    │   every 15 min in market hours,       │
              │                        │   writes trades + one audit row       │
              │  candles                └──────────────────────────────────────┘
              ▼                                        │
     Supabase Storage (parquet files)                  │ strategies, trades,
              │                                        │ results, audit rows
              └───────────────►  Supabase (Postgres) ◄─┘
```

Four facts that explain everything else:

1. **Supabase is the only place state lives.** Every cloud run starts blank and
   rebuilds itself from Supabase. If Supabase is empty, the dashboard is empty.
2. **Price history is stored as parquet files, not database rows.** About 5x
   smaller and roughly 8x faster to read. On your laptop they sit in
   `data/candles`; on Railway they come from Supabase Storage, which is a
   separate quota from the database. `CANDLE_STORE=parquet` selects this.
3. **Strategies live in the database**, and the dashboard is how you edit them.
   `strategies.yaml` is only a seed: it fills an empty database on first run.
   Once a strategy is stored, the file is no longer the source of truth.
4. **Nothing places a real order.** There is no broker order API in this
   codebase at all — not disabled, absent.

> **Which data provider?** Your `.env` decides. `DATA_PROVIDER=dhan` (what you
> are on) gives about 5 years of 5-minute history; every longer timeframe is
> resampled from it. The free `yfinance` provider needs no account but serves
> only ~58 days of intraday data — see §9.

---

## 1. Where to run commands (read this once)

- **On your laptop:** open **PowerShell**, then go to the project folder:
  ```powershell
  cd "C:\Users\Aniket\Personal projects\Trading tool"
  ```
  Every laptop command in this guide is run from that folder.
- **The Python to use is the one inside the project's virtual environment**, not
  a system Python. On Windows that is `.\.venv\Scripts\python.exe`.
  (Mac/Linux equivalent: `.venv/bin/python`.)
- **In the cloud:** GitHub (the *Actions* and *Settings* tabs of the repo) and
  Supabase and Streamlit are all done in a **web browser** — no terminal.

If `.\.venv\Scripts\python.exe` does not exist yet, do Step 2.1 first.

---

## 2. One-time setup (do this once, ~30 minutes)

Do the parts in order. After each part there is a **check** that must pass before
moving on.

### 2.1 Python environment (laptop)

```powershell
cd "C:\Users\Aniket\Personal projects\Trading tool"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```
**Check:**
```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
```
Expected: `367 passed, 1 skipped`. This proves the code and all libraries are
healthy — **no internet accounts needed yet.** (The one skip is the Dhan
fixture test; it un-skips once you run `scripts\verify_dhan_live.py` with Dhan
credentials.) If this fails, stop and fix Python before anything else.

### 2.2 Create your local secrets file (laptop)

```powershell
Copy-Item .env.example .env
notepad .env
```
Leave `notepad` open — you will paste values into it during 2.3 (market data)
and 2.4 (Supabase). The `.env` file is **git-ignored**; it never leaves your
laptop.

### 2.3 Market data — Dhan (precise, 5 years of history, paid Data API)

The platform can now source candles from **Dhan**, which gives **5 years of
5-minute history** and renews its access token automatically, so there is
**no daily login**. Account opening and the AMC are free, but reading candles
requires Dhan's **Data APIs subscription, ~₹499 + GST/month** — trading APIs
are free, data APIs are not. Subscribe on the **"Data APIs" tab at
web.dhan.co → Profile → DhanHQ Trading APIs** before running a backfill, or
every request fails with HTTP 401 / `DH-902`.

This is optional. The default remains `yfinance` (no account at all), but it
serves only ~58 days of intraday history, which is too little for meaningful
backtests.

**One-time setup:**

1. Open a free Dhan account at [dhan.co](https://dhan.co) (₹0 opening, ₹0 AMC).
   You do not need to fund it to place trades, but you **do** need to
   subscribe to the paid Data APIs (~₹499+GST/month) to pull candles.
2. Go to **web.dhan.co → Profile → DhanHQ Trading APIs**, subscribe on the
   **"Data APIs" tab**, and enable API access.
3. Enable **TOTP** for your account and save the secret it shows you. You will
   also need your account **PIN**.
4. Put the required values in `.env`. Unattended token generation
   (`https://dhanhq.co/docs/v2/authentication/`) authenticates with the
   client ID, PIN, and TOTP — **not** an API key/secret:
   ```
   DATA_PROVIDER=dhan
   DHAN_CLIENT_ID=...
   DHAN_PIN=...
   DHAN_TOTP_SECRET=...
   ```
   `DHAN_API_KEY` / `DHAN_API_SECRET` are **optional** and unused by this
   automated flow — they belong to Dhan's browser-redirect OAuth flow, which
   cannot run unattended.
5. Load the symbol master, then warm the cache:
   ```powershell
   .\.venv\Scripts\python.exe backfill.py --refresh-instruments
   .\.venv\Scripts\python.exe backfill.py --symbols NSE:RELIANCE,NSE:TCS --years 2
   ```
6. Verify the whole path end to end:
   ```powershell
   .\.venv\Scripts\python.exe scripts\verify_dhan_live.py
   ```

**Why candles are cached:** backtests read from your Supabase database, never
from a live API — so they run identically at 2 a.m., on weekends, and on
exchange holidays.

> **⚠️ Run step 6 before trusting any backtest.** Dhan's epoch timestamps are
> read as UTC instants, and that assumption has not yet been checked against a
> live response. If it is wrong, every candle is shifted by 5h30m and results
> are silently wrong. `verify_dhan_live.py` settles it in seconds.

> **On the PIN and TOTP secret.** Both are second-factor credentials: keep
> them in `.env` (git-ignored) or GitHub Secrets, never in the repo. Note
> that Dhan requires a **whitelisted static IP** for order placement and this
> project never whitelists one — so even a leaked token cannot trade your
> account.

<details>
<summary>Other providers (yfinance, Kite)</summary>

`DATA_PROVIDER=yfinance` (the default) needs no account at all, but serves only
**~58 days** of 15m/30m history — too little for meaningful backtests. It is
kept as a fallback and as a cross-check on Dhan's data.

`DATA_PROVIDER=kite` requires Zerodha's **paid** Connect plan (₹500/30 days;
the free Personal tier has no historical-data API) plus a daily login.
</details>

### 2.4 Supabase database (browser → paste into `.env`)

1. Go to **https://supabase.com** → **New project** (free tier, any region).
2. When it's ready: left sidebar **SQL Editor** → **New query** → open the repo
   file **`sql/001_init.sql`**, copy its *entire* contents, paste, click **Run**.
   You should see **Success**. (It's safe to run again if something half-fails.)
3. Left sidebar **Project Settings → API**. Copy three things:
   - **Project URL** → `.env` as `SUPABASE_URL`
   - **`service_role` key** (the secret one) → `.env` as `SUPABASE_SERVICE_ROLE_KEY`
   - **`anon` `public` key** → keep this somewhere; a read-only deployment
     uses it instead of the service key.
   ```
   SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
   SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOi....(very long)
   ```
4. Save and close `notepad`.

**Check (laptop):**
```powershell
.\.venv\Scripts\python.exe db.py
```
Expected: every table prints `OK`, ending with `All checks passed. Supabase is ready.`
If you see `relation "..." does not exist`, the SQL in step 2 didn't apply — re-run it.

### 2.5 Put it online (browser, ~20 minutes)

Everything above works on your laptop alone. To reach the workbench from a
phone, and to have the paper engine run without your laptop being on, deploy it
to Railway.

**That is its own document, written click by click:
[`docs/DEPLOYING.md`](DEPLOYING.md).** It covers creating the account, pasting
the keys, inventing an `APP_PASSWORD`, adding the scheduled job, and what to do
when a step goes wrong.

Two things worth knowing before you start:

* The dashboard needs a password when hosted. It carries the **service_role**
  key to be useful, and that key is full access to your database — so anyone
  who found the URL could edit strategies and read every trade. The app warns
  loudly if it is deployed without `APP_PASSWORD` set.
* Add `CANDLE_ROOT=supabase://candles` to the hosted environment. Railway wipes
  its filesystem on every deploy, so a server pointed at a local folder would
  find no price history at all.

This project previously used GitHub Actions for scheduling and Streamlit Cloud
for the dashboard. Both are gone: one platform running the site and the cron
job together is fewer moving parts, one set of secrets, and one place to look
when something breaks.

### 2.8 (Optional) Preview the dashboard with sample data

Want to see the dashboard populated *before* finishing the Kite setup and
waiting for real trades? Seed some clearly-marked fake data. This needs only
your two **Supabase** values (Kite keys not required).

```powershell
.\.venv\Scripts\python.exe seed_demo.py --dry-run   # optional: preview, writes nothing
.\.venv\Scripts\python.exe seed_demo.py             # insert sample data
```
Refresh the dashboard — you'll see a leaderboard, equity/drawdown charts, open
positions, today's trades, and a green engine-health strip. Everything it
inserts is named `DEMO-...`, so it never mixes with real data. When you're done:

```powershell
.\.venv\Scripts\python.exe seed_demo.py --clear     # removes all DEMO- data
```
> This is fake data for UI preview only — it is **not** a backtest and says
> nothing about any strategy's performance.

**You are now fully set up.** Nothing more is automatic-blocking; the only daily
task is the morning login (Part 3).

---

## 3. The daily routine (every trading day)

**On Dhan (what you are on): there is none.** No morning login, no daily
task — the token refreshes itself. The Railway cron job runs the engine every
15 minutes during market hours. Look at the dashboard whenever you're curious.

| When | Where | Do this |
|---|---|---|
| Anytime | browser | Glance at the dashboard |
| Weekly-ish | browser | Check the *Recent engine runs* panel is green |
| After adding symbols | laptop | `backfill.py --symbols ... --years 2` to fetch their history |

<details>
<summary>Only if you switched to paid Kite (DATA_PROVIDER=kite)</summary>

Zerodha expires API sessions daily (a SEBI rule), so you must log in each
trading morning **before 09:15 IST**, on your laptop:

```powershell
cd "C:\Users\Aniket\Personal projects\Trading tool"
.\.venv\Scripts\python.exe login.py
.\.venv\Scripts\python.exe login.py --check    # should print OK + your name
```

1. It opens Zerodha's login page in your browser.
2. You log in with your Zerodha ID + password + 2FA. *(You type these into
   Zerodha's own site — never into the script.)*
3. Zerodha redirects; **the page may fail to load — that's normal.** Copy the
   **whole address bar** (it contains `request_token=...`).
4. Paste it back into the terminal.

If you forget, engine runs fail with an "expired/missing token" message until
you log in. Nothing is damaged — there are no real orders.

*(On the free provider, `login.py` just prints "nothing to do" and exits.)*
</details>

---

## 4. Building and testing strategies (the important part)

You never touch Python to change what the system trades. A strategy is a
document you write in the dashboard, paste from ChatGPT, or seed from
**`strategies.yaml`** — once saved it lives in the database, and the dashboard
is where you edit it. The workflow is always:

```
edit strategies.yaml → validate → backtest → read kill rules → enable → push
```

### 4.1 Anatomy of a strategy

Open `strategies.yaml`. Each entry under `strategies:` looks like this
(annotated):

```yaml
- name: TF-EMA-RSI-15m-v1     # unique name; used as the key in the database
  enabled: true               # true = the live engine trades it; false = backtest only
  position_type: long         # long (bet up) or short (bet down)
  timeframe: 15m              # 15m | 30m | 60m | day  — NOTHING faster than 15m
  instruments:                # list, as they appear on Kite: EXCHANGE:SYMBOL
    - NSE:RELIANCE
    - NSE:HDFCBANK

  entry:                      # when to OPEN a position
    all:                      # all: = AND (every rule must be true)
      - indicator: ema
        params: { period: 9 }
        operator: crosses_above
        compare_to: { indicator: ema, params: { period: 21 } }
      - indicator: rsi
        params: { period: 14 }
        operator: ">"
        value: 50

  exit:                       # when to CLOSE (in addition to stop/target below)
    any:                      # any: = OR (one rule true is enough)
      - indicator: rsi
        params: { period: 14 }
        operator: "<"
        value: 40

  risk:
    stop_loss_pct: 0.7        # close if price moves 0.7% against you
    target_pct: 1.5           # close if price moves 1.5% in your favour

  sizing:
    type: fixed_quantity
    quantity: 1               # how many shares per trade

  max_cycles_per_day: 2       # max complete buy+sell round-trips per day
```

### 4.2 The building blocks you can use

**Indicators** (the `indicator:` field):

| Indicator | `params` needed | Notes |
|---|---|---|
| `close`, `open`, `high`, `low`, `volume` | none | raw candle values |
| `ema`, `sma` | `period` | add `source: volume` for e.g. volume average |
| `rsi` | `period` | 0–100 oscillator |
| `macd` | `fast`, `slow`, `signal` | `output:` = `line` / `signal` / `histogram` |
| `bbands` (Bollinger) | `period`, `std` | `output:` = `upper` / `middle` / `lower` |
| `supertrend` | `period`, `multiplier` | `output:` = `line` / `direction` |
| `atr` | `period` | volatility |
| `vwap` | none | resets each trading day |

**Operators** (the `operator:` field):
- Compare to a fixed number → use `value:`  →  `>`  `<`  `>=`  `<=`
- Compare to another indicator → use `compare_to:` → same operators, plus
  `crosses_above` / `crosses_below` (true only on the candle where one line
  crosses the other — fires once, not every candle after).

**Grouping:** `all:` = AND, `any:` = OR. You can nest them for complex logic:
```yaml
entry:
  all:
    - indicator: rsi
      params: { period: 14 }
      operator: ">"
      value: 50
    - any:                     # (RSI > 50) AND (close>200 OR volume>1M)
        - indicator: close
          operator: ">"
          value: 200
        - indicator: volume
          operator: ">"
          value: 1000000
```

**Rules that keep you safe:** timeframe must be 15m or slower; every condition
needs exactly one of `value:` or `compare_to:`; `stop_loss_pct`/`target_pct`
must be between 0 and 50 (a `70` is treated as a typo for `0.7`). Decisions are
always made on the **just-closed** candle — never a forming one.

### 4.3 Validate (laptop) — do this after EVERY edit

```powershell
.\.venv\Scripts\python.exe strategy_schema.py
```
- If good: prints each strategy and its settings.
- If broken: prints the **exact location**, e.g.
  `strategies[0].entry.all[1].params: missing required key(s): period`.
  Fix that line and re-run. This costs nothing and catches typos before they
  waste an API call.

### 4.4 Backtest (test on real history)

**From your laptop** (fastest for experimenting):
```powershell
.\.venv\Scripts\python.exe backtest.py --years 2
.\.venv\Scripts\python.exe backtest.py --strategy <name> --years 3
.\.venv\Scripts\python.exe backtest.py --no-db      # only write a CSV, skip the database
```
> `--no-db` on the free provider needs **no accounts at all** — not even
> Supabase. It's the fastest way to try a strategy idea.

**How much history you actually get (free `yfinance` provider — Dhan serves
about 5 years at every timeframe):**

| Timeframe | History | Good for |
|---|---|---|
| 15m / 30m | ~58 days | live paper trading; **weak** backtest evidence |
| 60m | ~2 years | **serious backtesting** |
| day | 5+ years | **serious backtesting** |

If you ask for more than the provider has, the backtester prints a clear NOTE,
runs on the shorter window anyway, and the kill rules flag the thin sample. To
judge a rule-set properly, test it on **60m or day**.

**From the dashboard** (no terminal needed): **Backtest** → **Run a
backtest** → pick a strategy → **Run backtest**. Output streams into the page
and the results appear under **Results**.

**Pin the dates when you intend to compare two runs.** "Last 2 years" measured
today and measured next week are different periods, so the same strategy would
produce different numbers for reasons that have nothing to do with the
strategy. Tick **Pin exact dates**, or from the terminal:

```powershell
.\.venv\Scripts\python.exe backtest.py --strategy <name> --from 2024-01-01 --to 2026-01-01
```

**Check it on data it has never seen — `--holdout`.**

Every backtest number you have read so far is *in-sample*: you chose the rules,
the stop and the target while looking at the very candles that then scored
them. That is the easiest way there is to believe a result that will not
survive a live market, and it gets worse the more variants a `--sweep` tries.

`--holdout 0.3` reserves the **last 30%** of the window, scores both halves,
and prints them separately:

```powershell
.\.venv\Scripts\python.exe backtest.py --strategy <name> --years 2 --holdout 0.3
```

```
  VERDICT        net=₹-1,770,552  profitable on 0/50 symbols  FAILED kill rules
  IN-SAMPLE      net=₹-1,225,591  trades=6638  FAILED
  OUT-OF-SAMPLE  net=₹-544,961    trades=2880  FAILED   <- the one that was not fitted
```

Read the **out-of-sample** line. It is the only one that was not shaped by the
data it reports on. A strategy that passes in-sample and fails out-of-sample
has told you something important: the in-sample result was the tuning, not an
edge.

Two cautions:

* **Check `oos_trades` first.** A handful of out-of-sample trades makes every
  other out-of-sample number noise rather than evidence. The run prints a NOTE
  when there are too few.
* A holdout cannot prove a strategy is good. It can only fail to disprove it.
  Passing out-of-sample once is *interesting*, not *proven* — you can still
  overfit by trying strategies until one passes the holdout too.

The full-window `passed_kill_rules` keeps its old meaning, so runs made with
and without `--holdout` remain comparable.

### 4.5 Read the results — the "kill rules"

Results go to the `backtest_results` table (visible on the dashboard) and the
CSV. Each strategy×instrument row has four **pass/fail flags**. Treat a strategy
as worth running **only if all four pass**:

| Rule | Passes when | Why it matters |
|---|---|---|
| Enough trades | ≥ 30 trades | Fewer is statistical noise, not evidence |
| Net positive | net P&L > 0 **after costs** | It must make money as modeled |
| Drawdown OK | max drawdown ≤ 20% | Bigger is too painful to sit through |
| Robust | profitable on **≥ 40% of the symbols traded** | Working on one stock only = curve-fitting |

> The robustness rule used to be a fixed "at least 3 symbols". That was a 60%
> bar when a strategy named five symbols by hand — but only a 6% bar on
> NIFTY50, where it would pass a strategy losing money on 47 stocks out of 50.
> A proportion scales honestly to a universe of any size.

If a strategy fails these on 2+ years of history, it will almost certainly lose
in reality (where costs are worse). Don't enable it.

#### ⚠️ The #1 trap: your target must beat the cost of trading

**Which costs apply is set by `COST_MODEL` in your `.env`:**

| `COST_MODEL` | What is charged | Round trip in and out at ₹100,000 |
|---|---|---|
| `flat` (default) | a flat ₹30 per round trip | ₹30 |
| `itemised` (what you are on) | brokerage, STT, stamp duty, exchange and SEBI fees, GST | **₹82.45** |

The itemised model scales with turnover, so a bigger position costs more —
which is the honest behaviour, and about 2.7x the flat charge at a lakh per
trade. (The exact figure moves a little with the exit price, because STT is
charged on the sell value.) The flat model is kept so results produced before it can still be
reproduced exactly.

The trap is the same either way. With `quantity: 1`, a
1.5% target on a ₹1,400 share earns **₹21 — less than the ₹30 cost.** Such a
strategy **cannot make money even when every trade wins.**

Real numbers from the shipped demo strategy at `quantity: 1`:

| Stock | Price | 1.5% target | − cost | Net on a WIN |
|---|---|---|---|---|
| RELIANCE | ₹1,400 | ₹21.00 | ₹30 | **−₹9.00** |
| HDFCBANK | ₹1,000 | ₹15.00 | ₹30 | **−₹15.00** |
| INFY | ₹1,550 | ₹23.25 | ₹30 | **−₹6.75** |
| TCS | ₹3,100 | ₹46.50 | ₹30 | +₹16.50 |

At `quantity: 10`, RELIANCE's target becomes ₹210 − ₹30 = **+₹180**. 

**Rule of thumb:** make sure `quantity × price × target_pct/100` is
comfortably larger than the cost — aim for at least 5x.

**The better fix is not to use `quantity` at all.** Use `sizing: {type:
notional, notional_per_trade: 100000}` instead: a fixed rupee amount per trade
buys 25 shares of a ₹4,000 stock and 500 of a ₹200 one, so the cost drag is the
same on both. With a fixed share count you are partly ranking a universe by
share price, which is not a property of the strategy at all.

### 4.6 The safe promotion workflow

1. Write the strategy in the dashboard (**Strategies** → **New**), or paste
   one from ChatGPT. Save it **paused**. A paused strategy is stored and
   backtestable; it simply never trades.
2. Fix anything the validator complains about. It names the exact location.
3. Backtest it → check the four kill rules. Pin the dates.
4. Re-test the winner on a period you did not use while tuning. This is the
   step everyone skips and the only one that distinguishes an edge from a
   coincidence.
5. Only then switch it to **Live** in the dashboard.
6. The next scheduled engine run picks it up automatically — nothing to push,
   because strategies live in the database, not in the repository.

> Editing `strategies.yaml` still works, but it only seeds an **empty**
> database. Once a strategy is stored, changing the file changes nothing.

### 4.7 Worked example: add a new strategy

Goal: a 60-minute long strategy on two banks — buy when price closes above its
20-period SMA **and** MACD is positive; exit when price falls back below the SMA.

Add this under `strategies:` in `strategies.yaml`:
```yaml
- name: SMA-MACD-60m-v1
  enabled: false
  position_type: long
  timeframe: 60m
  instruments:
    - NSE:HDFCBANK
    - NSE:ICICIBANK
  entry:
    all:
      - indicator: close
        operator: ">"
        compare_to: { indicator: sma, params: { period: 20 } }
      - indicator: macd
        params: { fast: 12, slow: 26, signal: 9 }
        output: line
        operator: ">"
        value: 0
  exit:
    any:
      - indicator: close
        operator: "<"
        compare_to: { indicator: sma, params: { period: 20 } }
  risk:
    stop_loss_pct: 1.0
    target_pct: 2.0
  sizing:
    type: fixed_quantity
    quantity: 1
  max_cycles_per_day: 1
```
Then:
```powershell
.\.venv\Scripts\python.exe strategy_schema.py                        # must say OK
.\.venv\Scripts\python.exe backtest.py --strategy SMA-MACD-60m-v1    # check kill rules
```
If the kill rules pass, change `enabled: false` → `true`, then commit and push.

### 4.8 Sweeping a setting to search for better numbers

Rather than editing a stop loss, re-running, editing it again and trying to
remember which was which, test several values in one run.

**In the dashboard:** **Backtest** → **Run a backtest** → pick a strategy →
open **Sweep a setting across several values** → type e.g. `0.5, 0.7, 1.0` into
**Stop loss values to try**. It tells you how many variants that is before you
start.

**From the terminal:**
```powershell
.\.venv\Scripts\python.exe backtest.py --strategy BANKS-EMA-RSI-60m `
  --from 2026-01-01 --to 2026-06-30 `
  --sweep risk.stop_loss.value=0.5,0.7,1.0 `
  --sweep risk.target.value=1.0,2.0
```
That is 3 x 2 = 6 runs. Each variant is stored under its own name — for example
`BANKS-EMA-RSI-60m [stop_loss.value=0.7, target.value=2.0]` — so the results
table and the **Compare runs** tab can tell them apart. The run ends with every
variant ranked by net P&L.

You can sweep any setting in the strategy document by its path:
`risk.stop_loss.value`, `risk.target.value`, `sizing.notional_per_trade`,
`entry.all.1.params.period`. A path that does not exist is refused rather than
created, so a typo is caught before the run starts instead of silently changing
nothing.

> ### Read this part before you trust a sweep
>
> **A sweep is the fastest way to fool yourself in this entire system.** Trying
> 24 combinations and keeping the best one is not research — it is picking the
> luckiest sample from a distribution you generated on purpose.
>
> If any single combination had a 5% chance of looking good by luck alone, then
> across 24 of them the chance that *at least one* did is about **71%**. The
> tool prints this number for your actual sweep, before and after the run.
>
> So: sweep a handful of values, not a hundred. And when something passes,
> treat it as a **hypothesis**, not a finding — re-test it on a period the
> sweep never saw (a different year, with `--from` / `--to`) before you believe
> it. A result that survives that is worth something. A result that only exists
> inside the window you searched is worth nothing.

The default limit is 24 variants; `--max-variants` raises it, deliberately.

---

## 5. Command cheat-sheet (all laptop commands run from the project folder)

| I want to… | Command |
|---|---|
| Set up / repair the Python env | `python -m venv .venv` then `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` |
| Confirm the code is healthy | `.\.venv\Scripts\python.exe -m pytest tests/ -q` |
| Check Supabase, and which migrations are applied | `.\.venv\Scripts\python.exe db.py` |
| Validate strategies after editing | `.\.venv\Scripts\python.exe strategy_schema.py` |
| Do the morning login (**paid Kite only**) | `.\.venv\Scripts\python.exe login.py` |
| Check today's token (**paid Kite only**) | `.\.venv\Scripts\python.exe login.py --check` |
| Backtest everything (2 yrs) | `.\.venv\Scripts\python.exe backtest.py` |
| Backtest one strategy | `.\.venv\Scripts\python.exe backtest.py --strategy <name>` |
| Run one paper-engine tick manually | `.\.venv\Scripts\python.exe paper_engine.py` |
| Fill the dashboard with sample data | `.\.venv\Scripts\python.exe seed_demo.py` |
| Remove the sample data | `.\.venv\Scripts\python.exe seed_demo.py --clear` |
| See the dashboard locally | `.\.venv\Scripts\streamlit.exe run dashboard.py` |
| Backtest a fixed window (reproducible) | `.\.venv\Scripts\python.exe backtest.py --strategy <name> --from 2024-01-01 --to 2026-01-01` |
| Sweep a setting across values | `.\.venv\Scripts\python.exe backtest.py --strategy <name> --sweep risk.stop_loss.value=0.5,0.7,1.0` |
| Check a strategy out-of-sample | `.\.venv\Scripts\python.exe backtest.py --strategy <name> --holdout 0.3` |
| Audit stored candles for splits and gaps | `.\.venv\Scripts\python.exe scripts\audit_data_quality.py --dry-run` |
| Turn the schedule on/off | Railway → the cron service → **Settings** → pause / resume |
| See recent cloud runs | Railway → the service → **Deployments** → **View Logs**, or the dashboard's *Recent engine runs* panel |
| Load the Dhan symbol master | `.\.venv\Scripts\python.exe backfill.py --refresh-instruments` |
| Warm the candle cache | `.\.venv\Scripts\python.exe backfill.py --symbols NSE:RELIANCE --years 2` |
| Verify the data layer end to end | `.\.venv\Scripts\python.exe scripts\verify_dhan_live.py` |

---

## 6. Troubleshooting

**Where to look first for anything weird:** the dashboard's *Recent engine runs*
panel (or the `run_audit` table). Every run leaves one row saying `ok`,
`skipped`, or `error` with a reason.

| Symptom | Cause | Fix |
|---|---|---|
| Log says `Missing required environment variable(s)` | A variable was not copied into Railway | Railway → the service → **Variables** — see [`DEPLOYING.md`](DEPLOYING.md) step 3 |
| A result column is blank, or there is no equity chart | A migration in `sql/` has not been applied | Run `.\.venv\Scripts\python.exe db.py` — it names the exact file to paste into the Supabase SQL editor |
| Every variant of a sweep looks similar | The swept path may not be doing what you think | Check the variant names in the results table; each states the value it used |
| Log says `No Kite access token stored` / `token has expired` | (paid Kite only) morning login not done | `.\.venv\Scripts\python.exe login.py` |
| Backtest returns 0 trades / "no candles returned" | Asked for more history than the free feed has | Normal — see §4.4; use 60m or day for deep tests |
| Every backtest trade loses money | Flat ₹30 cost exceeds your target | See the sizing trap in §4.5 — raise `quantity` |
| `Yahoo Finance failed ... after 4 attempts` | Free endpoint throttled or offline | Nothing — next run retries; if persistent, `pip install -U yfinance` |
| Free provider can't fetch an F&O symbol | Yahoo has no reliable NSE derivatives feed | Use cash-market symbols, or switch to paid Kite (§9) |
| Engine says `SKIPPED: market closed` on a trading day | Wrong date in `nse_holidays.yaml`, or genuinely closed | Check/fix that file, push |
| Runs arrive late or one is missing | Cron delay (normal) | Nothing — runs are idempotent, the next one catches up |
| Scheduled runs stopped entirely | GitHub pauses cron after 60 idle days | Push any commit; re-enable in Actions if needed |
| Dashboard: "Could not read from Supabase" | SQL not applied, or wrong anon key | Re-run `sql/001_init.sql`; re-copy the anon key into Streamlit secrets |
| Dashboard: "looks like a service-role key" | Wrong Supabase key pasted into the dashboard | Use the `anon` `public` key |
| Dashboard loads but is empty | Works — no data yet | Run the engine during market hours, or run a backtest |
| `relation "..." does not exist` | `sql/001_init.sql` not applied to this project | Run it in Supabase SQL editor → confirm with `db.py` |
| Backtest: "date range longer than Kite allows" | Kite tightened its limits | Lower `TIMEFRAME_MAX_DAYS_PER_REQUEST` in `kite_client.py` |
| `Instrument 'NSE:XYZ' not found` | Typo in `strategies.yaml`, or expired F&O contract | Fix the symbol to match Kite exactly |
| `ZoneInfo` error on Windows | `tzdata` not installed | Re-run the pip install from Step 2.1 |
| `is not in the instruments table` | Symbol master not loaded | `backfill.py --refresh-instruments` |
| `Missing Dhan credential(s)` | `.env` incomplete | Add `DHAN_CLIENT_ID`, `DHAN_PIN`, `DHAN_TOTP_SECRET` (§2.3) |
| HTTP 401 / `DH-902` ("User has not subscribed to Data APIs") | Dhan account has no Data APIs subscription | Subscribe (~₹499+GST/month) on the "Data APIs" tab at web.dhan.co → Profile → DhanHQ Trading APIs |
| `Could not obtain a Dhan access token` | API access not enabled, wrong PIN, or a bad TOTP secret | Re-check web.dhan.co → Profile → DhanHQ Trading APIs; verify `DHAN_PIN`; paste the TOTP secret without spaces |
| `The dhan provider needs a Supabase connection` | Used `--no-db` with `DATA_PROVIDER=dhan` | Dhan caches candles in Supabase; drop `--no-db` or use yfinance |
| Backtest is slow the first time | Cache is cold; candles are being fetched | Normal — later runs read from cache |
| Candles look shifted by 5h30m | Dhan timestamp interpretation is wrong | Run `scripts\verify_dhan_live.py`; it detects and explains this |

---

## 7. Safety guarantees (why this is hard to break)

- **No live trading exists.** The only Kite calls used are login, profile,
  instrument list, and historical candles. There is no order code anywhere.
- **No secrets in the repo.** `.env` and `secrets.toml` are git-ignored; the
  cloud uses GitHub Secrets; the dashboard uses a read-only key that cannot even
  read the Kite token.
- **Idempotent.** Running the engine twice for the same candle cannot create a
  duplicate position or trade (enforced by database constraints, covered by
  tests).
- **Honest simulation.** Signals act on closed candles only; fills happen at the
  next candle's open with adverse slippage and a flat cost — in both the
  backtester and the live engine.
- **Fails loudly, never halfway.** Missing secrets, expired tokens, malformed
  YAML, and missing tables all stop the run with a clear, actionable message.

---

## 8. Quick reference: what lives where

| File / folder | What it is |
|---|---|
| `strategies.yaml` | **The only file you edit to change trading logic.** |
| `nse_holidays.yaml` | Market holidays — **update every December** from NSE's circular. |
| `.env` (laptop only, git-ignored) | Your local secrets. |
| `sql/001_init.sql` | One-time database setup, run in Supabase. |
| `login.py` | Morning Kite login. |
| `backtest.py` | The backtester. |
| `paper_engine.py` | The 15-minute live simulation run. |
| `dashboard.py` | The Streamlit dashboard. |
| `strategy_schema.py` | The strategy validator. |
| `db.py` | Database connectivity self-test + all reads/writes. |
| `tests/` | The offline test suite (`pytest tests/ -q`). |
| `sweep.py` | Expands one strategy into a grid of variants. |
| `sql/` | Migrations, applied by hand. `db.py` says which are outstanding. |
| `providers/dhan.py` | The Dhan market-data provider (what you are on). |
| `yfinance_client.py` | The free provider — no account, ~58 days intraday. |
| `kite_client.py` | The paid Kite provider (`DATA_PROVIDER=kite`). |
| `data_provider.py` | Picks between them. |
| `Procfile` | What Railway runs. |
| `docs/OPERATING_GUIDE.md` | **This file.** |

---

## 9. Upgrading to paid Kite Connect (only when you're ready)

You never have to do this. Do it only if the free feed's limits start costing
you more than ₹500/month is worth — mainly if you need **deep 15-minute
history** or **F&O/derivatives** data.

1. Create a **Connect**-type app at https://developers.kite.trade
   (₹500 / 30 days — the free Personal tier has no historical data).
   Redirect URL `http://127.0.0.1` is fine.
2. Add to your laptop's `.env`:
   ```
   DATA_PROVIDER=kite
   KITE_API_KEY=xxxxxxxx
   KITE_API_SECRET=xxxxxxxx
   ```
3. In Railway: add `KITE_API_KEY`, `KITE_API_SECRET` and `DATA_PROVIDER=kite`
   to **both** services' **Variables** tabs.
4. Start doing the morning `login.py` (see §3) — this becomes mandatory.

**No code changes.** To go back, set `DATA_PROVIDER` to `dhan` or `yfinance`.

What you gain: years of 15m history, F&O symbols, official broker-grade data,
and live quotes. What you take on: ₹500/month and the daily login.
