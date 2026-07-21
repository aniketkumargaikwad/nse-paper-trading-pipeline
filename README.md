# Paper Trading Research Pipeline (NSE / Kite Connect)

A free-tier, serverless research and **paper-trading** pipeline for Indian
equity/F&O markets:

- **GitHub Actions** runs a simulated trading engine every 15 minutes during
  market hours, and a batch backtester on demand.
- **Supabase** (free Postgres) stores all state: strategies, positions, the
  trade log, and an audit trail of every run.
- **Kite Connect** (Zerodha) supplies market data — **read-only**.
- **Streamlit Community Cloud** hosts an always-on dashboard.
- A **one-minute morning login** on your own machine handles Kite's
  SEBI-mandated daily session, and that's the only manual step.

> ## ⚠️ Research and paper trading ONLY
> This system **simulates** trades and records them in a database.
> **No code path anywhere can place, modify, or cancel a real order** — the
> Kite client exposes market-data endpoints only.
> Backtest and paper results overstate real-world performance (fills, slippage
> and costs are modeled, not real). If you ever want live automated trading,
> that belongs in a **separate, compliant system** built with your broker's
> and the exchange's approval processes, real risk controls, and appropriate
> licensing — not in this repo.

---

## How it fits together

```
   YOUR MACHINE (once each morning)          CLOUD (automatic)
  ┌─────────────────────────────┐   ┌───────────────────────────────────┐
  │ python login.py             │   │ GitHub Actions (cron, 15 min)     │
  │  └─ Kite 2FA in browser     │   │  └─ paper_engine.py               │
  │  └─ day's token ──────────────► │       ├─ reads token, strategies, │
  └─────────────────────────────┘   │       │  open positions           │
                                    │       ├─ fetches closed candles   │
        Supabase (Postgres) ◄───────┤       └─ writes trades + audit    │
        the ONLY state store        │                                   │
              ▲                     │ GitHub Actions (manual)           │
              │ read-only anon key  │  └─ backtest.py → results + CSV   │
  ┌───────────┴─────────────┐       └───────────────────────────────────┘
  │ Streamlit dashboard     │
  │ (always-on, read-only)  │
  └─────────────────────────┘
```

| File | Purpose |
|---|---|
| `strategies.yaml` | **The only file you edit to change trading logic.** |
| `strategy_schema.py` | Validates `strategies.yaml`; run after every edit. |
| `config.py` | Env vars, timezones, timeframes, slippage/cost defaults. |
| `sql/001_init.sql` | One-time database setup (run in Supabase SQL editor). |
| `db.py` | All Supabase reads/writes. `python db.py` = connectivity self-test. |
| `kite_client.py` | Read-only Kite market data with retries and caching. |
| `indicators.py`, `signals.py` | Indicator math and rule evaluation (closed candles only). |
| `backtest.py` | Batch backtester with fill/slippage/cost model and kill rules. |
| `paper_engine.py` | The 15-minute simulated trading run. |
| `login.py` | The morning Kite 2FA login. |
| `market_calendar.py`, `nse_holidays.yaml` | Market hours + holiday gate (**update yearly!**). |
| `dashboard.py` | Streamlit dashboard. |
| `.github/workflows/` | The two schedules. |
| `tests/` | 133 unit tests; run with `pytest`. |

---

## What you need before starting

1. **Python 3.11+** installed locally ([python.org](https://www.python.org/downloads/); on Windows tick *"Add python to PATH"*).
2. A **GitHub** account.
3. A **Zerodha** account and a **Kite Connect developer app**
   ([developers.kite.trade](https://developers.kite.trade)).
   *Pricing note:* Kite Connect has been free for personal use since 2023,
   but the **historical-data add-on may be a paid subscription** — check the
   current pricing page. The backtester and engine both use historical
   candles, so you need it.
4. A **Supabase** account (free tier) — [supabase.com](https://supabase.com).
5. A **Streamlit Community Cloud** account (free) — [streamlit.io/cloud](https://streamlit.io/cloud),
   sign in with GitHub.

---

## One-time setup (about 30 minutes)

### Step 1 — Get the code and Python environment

```powershell
# Windows PowerShell, inside the project folder
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

```bash
# Mac/Linux
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Run the tests once to confirm the environment is healthy:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q      # expect: all passed
```

### Step 2 — Create your Kite Connect app

1. Go to [developers.kite.trade](https://developers.kite.trade) → **Create new app**, type *Connect*.
2. **Redirect URL**: `http://127.0.0.1` is fine (the page it opens may fail to
   load during login — that's expected and okay).
3. Copy the **API key** and **API secret** into `.env`
   (`KITE_API_KEY=`, `KITE_API_SECRET=`).

### Step 3 — Create the Supabase project and tables

1. [supabase.com](https://supabase.com) → **New project** (free tier, any region; note the database password it asks you to set — you won't need it here, but keep it).
2. When the project is ready: **SQL Editor → New query**, paste the entire
   contents of **`sql/001_init.sql`**, press **Run**. You should see *Success*.
   (Safe to re-run if it half-fails.)
3. **Project Settings → API**: copy into `.env`:
   - Project URL → `SUPABASE_URL=`
   - `service_role` key → `SUPABASE_SERVICE_ROLE_KEY=`
   - Also note the `anon` `public` key — the dashboard will need it (Step 5).
4. Verify from your machine:

```powershell
.\.venv\Scripts\python.exe db.py          # every table should print OK
```

### Step 4 — Push to GitHub and add Secrets

1. Create a **private** GitHub repository and push this folder to it
   (everything sensitive is protected by `.gitignore` — `.env` never leaves
   your machine).
2. In the repo: **Settings → Secrets and variables → Actions → New repository
   secret**, create these four, values copied from your `.env`:
   - `KITE_API_KEY`
   - `KITE_API_SECRET`
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`

### Step 5 — Deploy the dashboard

1. [share.streamlit.io](https://share.streamlit.io) → **New app** → pick your
   repo, branch `main`, main file **`dashboard.py`** → Deploy.
2. App menu (⋮) → **Settings → Secrets**, paste (with your values):

```toml
SUPABASE_URL = "https://your-project-ref.supabase.co"
SUPABASE_ANON_KEY = "the anon public key"
```

   **Use the `anon` key here, never `service_role`** — the dashboard is
   public-ish and read-only; it will refuse to start with the wrong key.
3. The app loads with friendly "no data yet" messages. That's success.

### Step 6 — First login and first live test

```powershell
.\.venv\Scripts\python.exe login.py        # browser opens; follow the prompts
.\.venv\Scripts\python.exe login.py --check   # should print OK + your name
```

Then, on GitHub: **Actions → paper-engine → Run workflow**. Whatever the time
of day, this must end green:
- outside market hours it logs `SKIPPED: market closed ...` (normal!),
- during market hours it evaluates strategies and the dashboard's
  "last engine run" strip turns 🟢.

### Step 7 — First backtest

**Actions → backtest → Run workflow** (defaults: 2 years, all strategies).
When it finishes: the run page has a **backtest-results** artifact (CSV), and
the `backtest_results` table in Supabase has one row per strategy×instrument
with metrics and pass/fail **kill-rule flags**:

- fewer than 30 trades → too little evidence, ignore the combination;
- net P&L ≤ 0 after costs → it loses money as modeled;
- max drawdown > 20% → too painful to hold;
- profitable on fewer than 3 symbols → probably curve-fit to one stock.

Treat a strategy as interesting only when **all four pass**.

---

## The daily routine (trading days)

| When | What | How long |
|---|---|---|
| Any time before 09:30 IST | `python login.py` on your machine | ~1 minute |
| During the day (optional) | Glance at the dashboard | — |
| If something looks wrong | Dashboard → *Recent engine runs* expander | — |

Forgot the morning login? Nothing breaks: every engine run fails politely
with an "expired token" audit row until you run it, then the next run picks
up normally. There are no real orders at stake — worst case is missed
simulated signals.

## Editing strategies

1. Edit `strategies.yaml` (the schema reference is in comments at the top).
2. Validate: `python strategy_schema.py` — fix anything it complains about
   (it names the exact line-item, e.g. `strategies[0].entry.all[1].params`).
3. Commit and push. The next engine run picks it up automatically.
4. Backtest new ideas **before** enabling them: set `enabled: false`, push,
   run the backtest workflow with the strategy's name, check the kill rules,
   and only then flip `enabled: true`.

## Yearly maintenance

- Update **`nse_holidays.yaml`** each December from NSE's official
  trading-holiday circular. The 2026 lunar-festival dates shipped here are
  placeholders and **must be verified**.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Engine run red with *"No Kite access token stored for ..."* or *"token has expired"* | Morning login not run, or you logged into Kite elsewhere (invalidates the API session) | `python login.py` (diagnose first with `python login.py --check`) |
| Engine runs green but say `SKIPPED: market closed` during a trading day | Date wrongly listed in `nse_holidays.yaml`, or a genuinely closed market | Check the file; remove the date and push |
| Cron runs arrive late or a slot is missing | GitHub cron drift (minutes of delay is normal) | Nothing — runs are idempotent and the next tick catches up |
| Scheduled runs stopped entirely | GitHub disables cron after ~60 days without a repo push | Push any commit (even a README tweak); check Actions tab for a disable banner |
| Dashboard: *"Could not read from Supabase"* | SQL never applied, or wrong/typo'd anon key | Re-run `sql/001_init.sql`; re-copy the `anon` key into Streamlit secrets |
| Dashboard refuses to start: *"looks like a service-role key"* | You pasted the wrong Supabase key | Replace with the `anon` `public` key |
| Dashboard loads but is empty | It works — there's just no data yet | Run the engine during market hours / run a backtest |
| `relation "..." does not exist` anywhere | `sql/001_init.sql` not applied to this project | Supabase SQL editor → run the file → `python db.py` to confirm |
| Backtest red with *"date range longer than Kite allows"* | Kite tightened per-request span limits | Lower `TIMEFRAME_MAX_DAYS_PER_REQUEST` in `kite_client.py` |
| Local: `ZoneInfo` errors on Windows | `tzdata` missing from the venv | `pip install -r requirements.txt` (it's pinned there) |
| `Instrument 'NSE:XYZ' not found on Kite` | Typo in `strategies.yaml`, or an expired F&O contract symbol | Fix the tradingsymbol; for F&O use the current contract's symbol |

**Where to look when anything is weird:** the `run_audit` table (also shown in
the dashboard's *Recent engine runs* expander). Every run — success, skip, or
error — leaves exactly one row with a reason.

## Running the tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

133 tests cover the YAML validator, indicator math (hand-computed values),
signal semantics (crosses fire exactly once), the backtester's fill model
(next-open fills, slippage directions, stop-before-target, gap handling),
the paper engine's idempotency, and the dashboard's aggregations. None of
them need network access, API keys, or a database.

## Safety properties (by design)

- **No live trading:** the only Kite endpoints used are login, profile,
  instruments, and historical candles. There is no order code to misuse.
- **No secrets in the repo:** `.env` and `secrets.toml` are gitignored;
  CI uses GitHub Secrets; the dashboard uses the read-only anon key and
  cannot read the Kite token (row-level security has no policy for it).
- **Idempotent:** running the engine twice for the same candle cannot
  duplicate a position or a trade (database unique constraints, tested).
- **Honest fills:** signals act on closed candles only; fills happen at the
  next candle's open with adverse slippage and flat costs, in both the
  backtester and the paper engine.
- **Fails safe:** expired tokens, network outages, malformed YAML, bad
  holiday files, and missing tables all stop the run loudly with an
  actionable message — nothing half-runs.
