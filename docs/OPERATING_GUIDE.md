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
  YOUR LAPTOP (once each morning)                 THE CLOUD (runs by itself)
  ┌──────────────────────────────┐    ┌──────────────────────────────────────┐
  │ Yahoo Finance (FREE)         │    │ GitHub Actions – paper-engine         │
  │   no account, no API key,    │◄───┤   every 15 min during market hours    │
  │   no daily login             │    │     reads strategies + open positions,│
  └──────────────────────────────┘    │     fetches candles, evaluates rules, │
   (swap to paid Kite any time        │     writes trades + an audit row      │
    with DATA_PROVIDER=kite)          │                                       │
                                       │ GitHub Actions – backtest (manual)    │
        Supabase (Postgres)  ◄─────────┤     writes results table + CSV        │
        = the ONLY memory              └──────────────────────────────────────┘
              ▲
              │ read-only anon key
  ┌───────────┴──────────────┐
  │ Streamlit dashboard      │   (always-on web page, read-only)
  └──────────────────────────┘
```

Four facts that explain everything else:
1. **Supabase is the only place state lives.** Every cloud run starts blank and
   rebuilds itself from Supabase. If Supabase is empty, the dashboard is empty.
2. **The engine needs one thing to work:** a Supabase connection. Market data
   comes from a **free** source (Yahoo Finance) that needs no account, no API
   key, and no daily login. *(Only if you later switch to paid Kite Connect
   does a daily login appear — see §9.)*
3. **The dashboard needs one thing:** a read-only Supabase key. It shows nothing
   until an engine has written some rows.
4. **You edit strategies in `strategies.yaml` only.** You never edit Python to
   change trading logic.

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
Expected: `133 passed`. This proves the code and all libraries are healthy —
**no internet accounts needed yet.** If this fails, stop and fix Python before
anything else.

### 2.2 Create your local secrets file (laptop)

```powershell
Copy-Item .env.example .env
notepad .env
```
Leave `notepad` open — you will paste four values into it during 2.3 and 2.4.
The `.env` file is **git-ignored**; it never leaves your laptop.

### 2.3 Market data — Dhan (free, precise, 5 years of history)

The platform can now source candles from **Dhan**, which gives **5 years of
5-minute history** at **zero cost** — no API fee, no AMC, no account-opening
fee — and renews its access token automatically, so there is **no daily
login**.

This is optional. The default remains `yfinance` (no account at all), but it
serves only ~58 days of intraday history, which is too little for meaningful
backtests.

**One-time setup:**

1. Open a free Dhan account at [dhan.co](https://dhan.co) (₹0 opening, ₹0 AMC).
   You do not need to fund it to use the data API.
2. Go to **web.dhan.co → Profile → DhanHQ Trading APIs** and enable API access.
3. Enable **TOTP** for your account and save the secret it shows you.
4. Put all five values in `.env`:
   ```
   DATA_PROVIDER=dhan
   DHAN_CLIENT_ID=...
   DHAN_API_KEY=...
   DHAN_API_SECRET=...
   DHAN_TOTP_SECRET=...
   ```
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

> **On the TOTP secret.** It is a second factor: keep it in `.env`
> (git-ignored) or GitHub Secrets, never in the repo. Note that Dhan requires a
> **whitelisted static IP** for order placement and this project never
> whitelists one — so even a leaked token cannot trade your account.

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
   - **`anon` `public` key** → keep this somewhere; the dashboard needs it in 2.7.
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

### 2.5 GitHub repository Secrets  ← **THIS is the step that was missing**

The cloud engine cannot read your laptop's `.env`. You must copy the same four
values into GitHub, or **every scheduled run fails** (that was the cause of the
failure emails).

1. In a browser open the repo → **Settings** → **Secrets and variables** →
   **Actions**.
2. Click **New repository secret** and add these **two**, one at a time, names
   spelled exactly, values copied from your `.env`:

   | Secret name | Value from `.env` |
   |---|---|
   | `SUPABASE_URL` | your Supabase project URL |
   | `SUPABASE_SERVICE_ROLE_KEY` | your Supabase service_role key |

   That's all — the free data provider needs no API keys. (Only if you later
   switch to paid Kite do you add `KITE_API_KEY` / `KITE_API_SECRET`; see §9.)

**Check:** the Actions → Secrets page lists both names.

### 2.6 Re-enable the scheduled engine (laptop or browser)


The auto-schedule was **disabled** to stop the failure emails during setup.
Turn it back on only *after* 2.5 is done:
```powershell
gh workflow enable paper-engine
```
(Or in the browser: repo → **Actions** → **paper-engine** → **⋯ / Enable workflow**.)

### 2.7 Deploy the dashboard (browser)

1. Go to **https://share.streamlit.io** → sign in with GitHub → **New app**.
2. Pick this repo, branch **main**, main file **`dashboard.py`** → **Deploy**.
3. App menu (top-right **⋮**) → **Settings → Secrets** → paste (with your values):
   ```toml
   SUPABASE_URL = "https://xxxxxxxxxxxx.supabase.co"
   SUPABASE_ANON_KEY = "eyJhbGciOi....(the anon/public key from 2.4)"
   ```
   **Use the `anon` key here, never `service_role`.** The dashboard refuses to
   start with a service key on purpose.
4. The page loads with friendly "no data yet" messages. That is success.

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

**On the free provider (the default): there is none.** No morning login, no
daily task. GitHub Actions runs the engine every 15 minutes during market hours
by itself. Just look at the dashboard whenever you're curious.

| When | Where | Do this |
|---|---|---|
| Anytime | browser | Glance at the Streamlit dashboard |
| Weekly-ish | browser | Check the *Recent engine runs* panel is green |

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

You never touch Python to change what the system trades. All logic lives in one
file: **`strategies.yaml`** (in the project root). The workflow is always:

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
.\.venv\Scripts\python.exe backtest.py --strategy TF-EMA-RSI-15m-v1 --years 3
.\.venv\Scripts\python.exe backtest.py --no-db      # only write a CSV, skip the database
```
> `--no-db` on the free provider needs **no accounts at all** — not even
> Supabase. It's the fastest way to try a strategy idea.

**How much history you actually get (free provider):**

| Timeframe | History | Good for |
|---|---|---|
| 15m / 30m | ~58 days | live paper trading; **weak** backtest evidence |
| 60m | ~2 years | **serious backtesting** |
| day | 5+ years | **serious backtesting** |

If you ask for more than the provider has, the backtester prints a clear NOTE,
runs on the shorter window anyway, and the kill rules flag the thin sample. To
judge a rule-set properly, test it on **60m or day**.

**From the cloud** (no laptop needed): repo → **Actions** → **backtest** →
**Run workflow** → optionally fill in `years` / `strategy` → **Run**. When it
finishes, the run page has a downloadable **backtest-results** CSV.

### 4.5 Read the results — the "kill rules"

Results go to the `backtest_results` table (visible on the dashboard) and the
CSV. Each strategy×instrument row has four **pass/fail flags**. Treat a strategy
as worth running **only if all four pass**:

| Rule | Passes when | Why it matters |
|---|---|---|
| Enough trades | ≥ 30 trades | Fewer is statistical noise, not evidence |
| Net positive | net P&L > 0 **after** ₹30/trade + slippage | It must make money as modeled |
| Drawdown OK | max drawdown ≤ 20% | Bigger is too painful to sit through |
| Robust | profitable on ≥ 3 symbols | Working on one stock only = curve-fitting |

If a strategy fails these on 2+ years of history, it will almost certainly lose
in reality (where costs are worse). Don't enable it.

#### ⚠️ The #1 trap: your target must beat the flat ₹30 cost

Every simulated round-trip is charged a flat **₹30**. With `quantity: 1`, a
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

**Rule of thumb:** make sure
`quantity × price × target_pct/100` is comfortably larger than ₹30 — aim for at
least 5×. Either raise `quantity` or pick a larger `target_pct`. This one line
in `strategies.yaml` decides whether a strategy is mathematically capable of
profit.

### 4.6 The safe promotion workflow

1. Add your new strategy to `strategies.yaml` with **`enabled: false`**.
2. `strategy_schema.py` → fix until valid.
3. Backtest it → check the four kill rules.
4. Only if it passes, set **`enabled: true`**.
5. Commit and push:
   ```powershell
   git add strategies.yaml
   git commit -m "Add/enable strategy <name>"
   git push
   ```
6. The next scheduled engine run picks it up automatically. (Pushing also
   re-activates the cron if GitHub had auto-paused it after 60 idle days.)

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

---

## 5. Command cheat-sheet (all laptop commands run from the project folder)

| I want to… | Command |
|---|---|
| Set up / repair the Python env | `python -m venv .venv` then `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` |
| Confirm the code is healthy | `.\.venv\Scripts\python.exe -m pytest tests/ -q` |
| Check Supabase is reachable | `.\.venv\Scripts\python.exe db.py` |
| Validate strategies after editing | `.\.venv\Scripts\python.exe strategy_schema.py` |
| Do the morning login (**paid Kite only**) | `.\.venv\Scripts\python.exe login.py` |
| Check today's token (**paid Kite only**) | `.\.venv\Scripts\python.exe login.py --check` |
| Backtest everything (2 yrs) | `.\.venv\Scripts\python.exe backtest.py` |
| Backtest one strategy | `.\.venv\Scripts\python.exe backtest.py --strategy <name>` |
| Run one paper-engine tick manually | `.\.venv\Scripts\python.exe paper_engine.py` |
| Fill the dashboard with sample data | `.\.venv\Scripts\python.exe seed_demo.py` |
| Remove the sample data | `.\.venv\Scripts\python.exe seed_demo.py --clear` |
| See the dashboard locally | `.\.venv\Scripts\streamlit.exe run dashboard.py` |
| Turn the auto-schedule on/off | `gh workflow enable paper-engine` / `gh workflow disable paper-engine` |
| See recent cloud runs | `gh run list --limit 10` |
| Read a failed run's log | `gh run view <run-id> --log-failed` |
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
| **Failure emails from GitHub Actions** | The two repository Secrets are missing/blank | Do **Step 2.5**, then `gh workflow enable paper-engine` |
| Log says `Missing required environment variable(s)` | Same as above | Same as above |
| Log says `No Kite access token stored` / `token has expired` | (paid Kite only) morning login not done | `.\.venv\Scripts\python.exe login.py` |
| Backtest returns 0 trades / "no candles returned" | Asked for more history than the free feed has | Normal — see §4.4; use 60m or day for deep tests |
| Every backtest trade loses money | Flat ₹30 cost exceeds your target | See the sizing trap in §4.5 — raise `quantity` |
| `Yahoo Finance failed ... after 4 attempts` | Free endpoint throttled or offline | Nothing — next run retries; if persistent, `pip install -U yfinance` |
| Free provider can't fetch an F&O symbol | Yahoo has no reliable NSE derivatives feed | Use cash-market symbols, or switch to paid Kite (§9) |
| Engine says `SKIPPED: market closed` on a trading day | Wrong date in `nse_holidays.yaml`, or genuinely closed | Check/fix that file, push |
| Runs arrive late or one is missing | GitHub cron delay (normal, up to ~1–2 h) | Nothing — runs are idempotent, the next one catches up |
| Scheduled runs stopped entirely | GitHub pauses cron after 60 idle days | Push any commit; re-enable in Actions if needed |
| Dashboard: "Could not read from Supabase" | SQL not applied, or wrong anon key | Re-run `sql/001_init.sql`; re-copy the anon key into Streamlit secrets |
| Dashboard: "looks like a service-role key" | Wrong Supabase key pasted into the dashboard | Use the `anon` `public` key |
| Dashboard loads but is empty | Works — no data yet | Run the engine during market hours, or run a backtest |
| `relation "..." does not exist` | `sql/001_init.sql` not applied to this project | Run it in Supabase SQL editor → confirm with `db.py` |
| Backtest: "date range longer than Kite allows" | Kite tightened its limits | Lower `TIMEFRAME_MAX_DAYS_PER_REQUEST` in `kite_client.py` |
| `Instrument 'NSE:XYZ' not found` | Typo in `strategies.yaml`, or expired F&O contract | Fix the symbol to match Kite exactly |
| `ZoneInfo` error on Windows | `tzdata` not installed | Re-run the pip install from Step 2.1 |
| `is not in the instruments table` | Symbol master not loaded | `backfill.py --refresh-instruments` |
| `Missing Dhan credential(s)` | `.env` incomplete | Add the four `DHAN_*` values (§2.3) |
| `Could not obtain a Dhan access token` | API access not enabled, or a bad TOTP secret | Re-check web.dhan.co → Profile → DhanHQ Trading APIs; paste the TOTP secret without spaces |
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
| `tests/` | 133 offline tests (`pytest tests/`). |
| `.github/workflows/` | The two cloud schedules. |
| `yfinance_client.py` | The free (default) market-data provider. |
| `kite_client.py` | The paid Kite provider (used only when `DATA_PROVIDER=kite`). |
| `data_provider.py` | Picks between them. |
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
3. In GitHub: add secrets `KITE_API_KEY` and `KITE_API_SECRET`, **and** a
   repository *variable* (Settings → Secrets and variables → Actions →
   **Variables** tab) named `DATA_PROVIDER` with value `kite`.
4. Start doing the morning `login.py` (see §3) — this becomes mandatory.

**No code changes.** To go back to free, remove the `DATA_PROVIDER` variable.

What you gain: years of 15m history, F&O symbols, official broker-grade data,
and live quotes. What you take on: ₹500/month and the daily login.
