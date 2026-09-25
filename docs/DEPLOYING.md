# Deploying — what runs where, and the steps you do by hand

This guide assumes you have never deployed anything. Every step says what to
click.

## What the system is made of

| Piece | Where it runs | What it costs | Set up in |
|---|---|---|---|
| **Daily research run** | GitHub Actions, 09:03 IST | free (public repo) | Part 1 |
| **Dashboard** | Streamlit Community Cloud | free | Part 2 |
| **Database + price backup** | Supabase | free tier | already done |
| **The thinking** | Claude Pro, via `claude -p` | already paid | Part 1 |
| **Paper trading engine** | nothing, at present | — | Part 3, optional |

Nothing here needs your laptop switched on except the occasional maintenance
in the last section.

---

## Before you start: your `.env` file

Everything you need to copy is in this one file.

1. Open File Explorer at `C:\Users\Aniket\Personal projects\Trading tool`.
2. You may not see `.env` — Windows hides files starting with a dot. Click the
   **View** menu → tick **Hidden items**.
3. Right-click `.env` → **Open with** → **Notepad**.

Leave it open. Do not paste its contents anywhere public: it holds your
database key, your broker keys and your Claude token.

### One value you have to invent

The dashboard needs a password, which is not in `.env`. Generate one:

```bash
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(24))"
```

Copy what it prints into a password manager. You will need it in Part 2.

---

# Part 1 — The daily research run

Already set up. This section is what to do when something needs changing.

Opus proposes a strategy, the tool tests it on the training years, Opus reviews
the result and decides what to try next — then the pick is measured against the
locked final year, once, and a Telegram message arrives. About 80 minutes a
day, on GitHub's machines.

### Why the repository is public

Free GitHub runners give a public repository **4 CPUs and 6-hour jobs**. A
private one gets 2 CPUs and 2,000 minutes a month — about 66 a day, which will
not fit even one version. Your secrets stay safe: they are not available to
pull requests from forks, and this workflow triggers only on `schedule` and
`workflow_dispatch`. Your commit author name and email are publicly visible, as
in any public repository.

### Secrets — Settings → Secrets and variables → Actions → Secrets

| Secret | Where it comes from |
|---|---|
| `SUPABASE_URL` | your `.env` |
| `SUPABASE_SERVICE_ROLE_KEY` | your `.env` |
| `CLAUDE_CODE_OAUTH_TOKEN` | `claude setup-token` in a terminal — **not** an interactive login, which cannot refresh on a runner |
| `TELEGRAM_BOT_TOKEN` | @BotFather |
| `TELEGRAM_CHAT_ID` | `python scripts/telegram_chat_id.py` |
| `GMAIL_USER` / `GMAIL_APP_PASSWORD` / `NOTIFY_EMAIL` | optional, only for email as well as Telegram |

### Variables — the same page, Variables tab

| Variable | Value |
|---|---|
| `DATA_END` | No longer read by anything (since 25 Sep 2026). It used to name the candle cache; the cache now rolls weekly on its own — see below. Safe to leave or delete. |
| `CLAUDE_TOKEN_CREATED` | the day you ran `claude setup-token`. A token lasts a year, and this is the only thing that knows when the clock started. |
| `DASHBOARD_URL` | the Streamlit address from Part 2, so the message can link to it |

### Setting up Telegram from scratch

1. In Telegram, message `@BotFather` and send `/newbot`. Follow the prompts and
   copy the token it gives you into `.env` as `TELEGRAM_BOT_TOKEN`.
2. Run this, **then** send your bot any message:
   ```bash
   .\.venv\Scripts\python.exe scripts/telegram_chat_id.py
   ```
   It waits for the message and prints the `TELEGRAM_CHAT_ID` line to add.

   > Do not use the `getUpdates` URL by hand. Telegram delivers each message to
   > it **once** and then forgets it, so running it a moment too early returns
   > an empty list and your messages are gone.

### Changing the schedule

Edit the `cron:` line in `.github/workflows/research.yml`. It is in **UTC**:
IST minus 5 hours 30 minutes. `33 3 * * *` is 09:03 IST.

**Do not put it on the hour or half hour.** Those are the minutes every cron on
GitHub picks, and GitHub's scheduler is best-effort — under load a run is
delayed, and at the worst of it dropped entirely. The first version of this was
`30 0 * * *` and never fired once.

The schedule only runs from the **default branch** (`main`), so changes must be
merged there to take effect.

### Running one by hand

Actions → research → **Run workflow**. Set **max_versions** to `1` for a cheap
check: two Opus calls, about 25 minutes.

### The atlas — run it once, and again only after a price top-up

Actions → atlas → **Run workflow**. It measures fifteen baseline signals
(RSI dips, breakouts, band reverts, gap fades, a VWAP revert and so on) on
the training years, one GitHub job each, and stores them in `research_atlas`.
Every morning's proposal prompt lists them with their numbers, so the AI
starts from what this data has already shown. Prices are frozen, so the
atlas needs re-running only when the store is topped up or a baseline is
added in `research/atlas.py`. No Claude call, no allowance used.

### What each failure looks like

- **The Claude allowance runs out mid-run** — the day stops cleanly, keeps the
  versions it finished, still opens the locked year (that needs no AI) and
  still stores a row, with status `stopped_limit`. The message says so. This is
  the design working, not a failure.
- **Supabase refuses the write** — the whole day is saved to
  `research-fallback.json` and kept as a workflow artifact for 30 days, so
  hours of sweeping are not lost.
- **The candle download fails** — the run stops before any AI call, and the
  message says the run stored nothing.
- **The Claude token is near expiry** — every message carries a warning for the
  last 30 days of its life.

### The daily journal commit

Every run writes `research/journal/YYYY-MM-DD.md` and commits it. That is not
only bookkeeping: GitHub **disables scheduled workflows in a public repository
after 60 days without activity**, and this commit is the activity.

---

# Part 2 — The dashboard on Streamlit Community Cloud

Free, and the only place the research results are readable as more than a
Telegram message. **About 10 minutes.**

### Step 1 — Sign in

1. Go to **https://share.streamlit.io**.
2. Click **Continue with GitHub** and authorise it.

### Step 2 — Create the app

1. Click **Create app** (top right), then **Deploy a public app from GitHub**.
2. Fill in:
   - **Repository**: `aniketkumargaikwad/nse-paper-trading-pipeline`
   - **Branch**: `main`
   - **Main file path**: `dashboard.py`
3. Optionally set a custom subdomain — that becomes your web address.

### Step 3 — Give it the keys, before deploying

Click **Advanced settings** → **Secrets**, and paste this, filling in the two
values:

```toml
SUPABASE_URL = "https://uqsygklxszucrxrobgsw.supabase.co"
SUPABASE_ANON_KEY = "<the anon key, NOT the service role key>"
APP_PASSWORD = "<the password you generated at the top>"
CANDLE_STORE = "supabase"
```

**Use the anon key.** It is read-only, which is the point: this page is on the
public internet. The service-role key would give anyone who reached it write
access to everything. The research tables are readable by the anon role, which
`sql/011_research.sql` set up deliberately.

To find the anon key: **supabase.com** → your project → **Settings** (gear,
bottom left) → **API Keys** → copy the key labelled `anon` `public`.

### Step 4 — Deploy

Click **Deploy**. The first build takes 3–5 minutes while it installs
`requirements.txt`.

When it finishes you should see a password box. Type the `APP_PASSWORD` you
set. Then open **Research** in the left sidebar: you should see one row per run,
newest first. Click a row for the detail — the ₹1 lakh chart, every combination
tested, the versions the day tried and Opus's review.

### Step 5 — Tell the messages where it is

Copy the address from your browser (it looks like
`https://<something>.streamlit.app`), then on GitHub: **Settings** → **Secrets
and variables** → **Actions** → **Variables** → **New repository variable** →
name `DASHBOARD_URL`, value that address.

Tomorrow's message will carry a link.

### Keeping it awake

A Community Cloud app that nobody opens for **7 days** goes to sleep. Waking it
is one click on the page itself, and nothing is lost — but if you would rather
it never slept, opening it once a week is enough.

---

# Part 3 — Paper trading (optional, and not currently running)

The paper engine (`paper_engine.py`) wakes during market hours, reads candles
and records simulated trades. **Nothing is running it right now**, by choice.

It is deliberately *not* on GitHub Actions. GitHub does not promise scheduled
jobs run on time; under load they arrive late or not at all. An engine reading
15-minute candles cannot tell "nothing happened" from "nobody woke me", so a
late run silently skips signals. The daily research run does not care — it
reads a frozen nine-year window, so arriving 40 minutes late changes nothing.

If you want it running on a schedule again, it needs a host with a real
scheduler — Railway is about $5/month, which is why it was dropped. Until a
strategy survives its own robustness rules, there is nothing worth paper
trading, so this can wait.

Running it by hand still works:

```bash
.\.venv\Scripts\python.exe paper_engine.py
```

---

## Where the price history actually lives

| Copy | What it is | Size |
|---|---|---|
| `data/candles` on your laptop | what every backtest reads | 663 MB, 5,359 files |
| Supabase **Storage** bucket `candles` | the backup, and what GitHub restores from | 663 MB |
| Supabase **database** table `candles` | **empty on purpose** | 0 |

Storage is a **separate quota** from the database: 1 GB of files beside 500 MB
of rows, so the backup is free — but 663 MB of 1 GB is two thirds of it.
Extending to NIFTY500 would need roughly 1.6 GB and would not fit.

Of that 663 MB, **631 MB is the 5-minute data**; every intraday timeframe is
resampled from it, so none of it can be trimmed.

Back up after any backfill:

```bash
.\.venv\Scripts\python.exe scripts/backup_candles_to_storage.py
.\.venv\Scripts\python.exe scripts/backup_candles_to_storage.py --verify
```

It only sends what changed, so a routine run is quick.

### How GitHub gets them

Each run restores a cache keyed on the ISO week, then runs
`scripts/restore_candles_from_storage.py` to fill any gaps — which normally
finds nothing to do. The weekly `recover-prices` run (Sunday evening) saves
the store it publishes as a cache entry of its own; Monday's first research
run misses the new week's key and restores that newest entry, so the week
starts from the freshest prices without re-downloading them. Supabase's free tier allows **5 GB of egress a month**,
about **seven full restores**, so the cache is what keeps ordinary days from
spending any of it. Cache entries are evicted after 7 days unused.

If the download step starts taking minutes every day rather than seconds, the
cache is missing and that budget is being spent.

---

## What this costs

| | |
|---|---|
| GitHub Actions | free — unlimited minutes on a public repository |
| Streamlit Community Cloud | free |
| Supabase database | free up to 500 MB. Currently ~20 MB |
| Supabase Storage | free up to 1 GB. Using 663 MB |
| Claude | your existing Pro plan; a full day is six Opus calls |
| Dhan Data APIs | ~₹499+GST/month — **only** needed to fetch new prices, not to run research |

---

## What still runs from your laptop

Occasional maintenance, not day to day:

```bash
# Fetch price history for more symbols
.\.venv\Scripts\python.exe backfill.py

# Refresh index membership after a rebalance (about twice a year)
.\.venv\Scripts\python.exe scripts/refresh_universes.py

# Then back the new candles up, so GitHub can restore them
.\.venv\Scripts\python.exe scripts/backup_candles_to_storage.py
```

Nothing on GitHub needs updating after a backfill. The research run re-fetches
every file whose size changed, and the next week's cache is saved from that.

---

## Why not Vercel

Vercel runs short-lived serverless functions. This dashboard is a Streamlit
server that stays running and holds a live connection to your browser. They are
incompatible; moving would mean rewriting the whole interface for no new
capability.
