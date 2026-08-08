# Quickstart — from here to a live strategy

You have already done: **Supabase project + `sql/001_init.sql`**, and the
**two GitHub secrets**. This guide takes you from there to watching a strategy
trade live (on paper) in your own dashboard.

Every command below is run in **PowerShell**, from this exact folder:

```powershell
cd "C:\Users\Aniket\Personal projects\Trading tool"
```

Keep that window open — every step uses it.

---

## Step 1 — Put your Supabase details on your laptop (2 min)

The cloud has your secrets, but your laptop needs them too so the app can read
and write.

**1a.** Create your local secrets file:

```powershell
Copy-Item .env.example .env
notepad .env
```

**1b.** In the Notepad window that opens, fill in these two lines (leave
everything else commented out):

```
SUPABASE_URL=https://YOUR-REF.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOi...your long service_role key...
```

**Where to get them:** Supabase → your project → ⚙️ **Project Settings** →
**API**. Copy *Project URL* and the **`service_role`** key.

**1c.** Save (Ctrl+S) and close Notepad.

**1d.** Check it worked:

```powershell
.\.venv\Scripts\python.exe db.py
```

✅ **Expected:** every table prints `OK`, ending with
`All checks passed. Supabase is ready.`
❌ If you see `relation ... does not exist`, the SQL didn't apply — re-run
`sql/001_init.sql` in Supabase's SQL Editor.

---

## Step 2 — Load your strategies into the database (30 sec)

The database is the source of truth for what gets traded. Seed it from the
`strategies.yaml` file that ships with the repo:

```powershell
.\.venv\Scripts\python.exe paper_engine.py
```

✅ **Expected:** either
`seeded 1 strategy definition(s) from strategies.yaml` followed by an `OK:` or
`SKIPPED:` line. **Both are success.** `SKIPPED: market closed` simply means
you ran it outside 09:30–15:50 IST on a weekday.

---

## Step 3 — Open your dashboard (1 min)

```powershell
.\.venv\Scripts\streamlit.exe run dashboard.py
```

Your browser opens at **http://localhost:8501**. You'll see six pages in the
left sidebar and an **✏️ Edit mode** badge (because you're running locally with
the service key — that's what unlocks building strategies).

> To stop the app later: click back in the PowerShell window and press
> **Ctrl+C**.

**What each page is for:**

| Page | Use it to |
|---|---|
| 📊 **Overview** | See at a glance: is the engine alive, what's my P&L, what should I do next |
| 🧠 **Strategies** | View, build, edit strategies; **one click to go live or pause** |
| 🔬 **Backtest** | Run a test on history and read the pass/fail robustness rules |
| 📈 **Paper trading** | Open positions, equity curve, drawdown, today's trades |
| 📜 **Trade log** | Every trade, filterable, with CSV export |
| ⚙️ **System** | Engine run history, configuration, troubleshooting |

---

## Step 4 — Fix the shipped strategy so it *can* make money (2 min)

The demo strategy ships with `quantity: 1`, which **cannot profit** — the flat
₹30 round-trip cost is larger than a 1.5% gain on a ₹1,400 share (₹21).

In the dashboard: **🧠 Strategies** → **➕ Build a new one**. Fill in:

- **Name:** `MY-EMA-RSI-15m-v1`
- **Timeframe:** `15m` · **Direction:** `long` · **Max round-trips/day:** `2`
- **Instruments:**
  ```
  NSE:RELIANCE
  NSE:TCS
  NSE:INFY
  ```
- **Entry rules** — "ALL must be true", 3 conditions:
  1. `ema` period 9 · `crosses_above` · another indicator → `ema` period 21
  2. `rsi` period 14 · `>` · fixed number `50`
  3. `volume` · `>` · another indicator → `sma` **On: volume** period 20
- **Exit rules** — "ANY can be true", 2 conditions:
  1. `ema` period 9 · `crosses_below` · another indicator → `ema` period 21
  2. `rsi` period 14 · `<` · fixed number `40`
- **Risk & sizing:** Stop `0.7` · Target `1.5` · **Quantity `20`**

Watch the **cost check** line. It must be green — if it's red, raise Quantity
until a winning trade earns at least ~5× the ₹30 cost.

Click **✅ Validate**, then **💾 Save strategy**. It saves **paused** on
purpose — you test before you deploy.

---

## Step 5 — Backtest it before going live (5 min)

Go to **🔬 Backtest** → **▶️ Run a backtest** tab:

- **Years of history:** `2`
- **Strategy:** pick your new one
- Click **▶️ Run backtest** and watch the log.

Then open the **📈 Results** tab and read the four rules:

| Rule | Passes when |
|---|---|
| Enough trades | ≥ 30 trades |
| Profitable after costs | net P&L > 0 |
| Drawdown ≤ 20% | max drawdown within cap |
| Works on ≥3 symbols | not curve-fit to one stock |

> **Expect 15m tests to fail "Enough trades".** The free data feed only reaches
> back ~58 days on 15m. That's a real limit, not a bug. To judge a rule-set
> properly, make a copy on the **60m** timeframe (2 years of history available)
> and backtest that.

**Only promote a strategy that passes all four.**

---

## Step 6 — Go live (10 seconds)

**🧠 Strategies** → expand your strategy → click **🚀 Go live**.

That's it. It now shows 🟢 **LIVE**, and the scheduled cloud engine will trade
it on its next run. **No code push, no redeploy.**

Make sure the schedule is switched on (it was disabled while your secrets were
missing):

```powershell
gh workflow enable paper-engine
```

Verify it's running:

```powershell
gh run list --limit 5
```

✅ **Expected:** recent `paper-engine` runs with `success`.

---

## Step 7 — Watch it trade

The engine runs **every 15 minutes, 09:32–15:32 IST, Mon–Fri**. After each run:

- **📊 Overview** → the engine-health strip turns 🟢 with a fresh timestamp
- **📈 Paper trading** → open positions appear as entries trigger
- **📜 Trade log** → completed round-trips, with intended vs filled prices

To see a run happen immediately during market hours:

```powershell
.\.venv\Scripts\python.exe paper_engine.py
```

---

## Step 8 (optional) — View it from anywhere

Deploy a **read-only** copy so you can check your phone:

1. Go to **https://share.streamlit.io** → **New app**
2. Repo: your `nse-paper-trading-pipeline`, branch `main`, file `dashboard.py`
3. After deploy: **⋮ → Settings → Secrets**, paste:
   ```toml
   SUPABASE_URL = "https://YOUR-REF.supabase.co"
   SUPABASE_ANON_KEY = "your anon public key"
   ```

⚠️ **Use the `anon` key there — never the `service_role` key.** The cloud copy
becomes view-only (no strategy editing), which is exactly what you want for a
public URL. Building strategies stays on your laptop.

---

## Daily routine

**There isn't one.** The free data provider needs no login. The cloud engine
runs itself. Just open the dashboard when you're curious.

*(Only if you later switch to paid Kite does a morning `login.py` appear.)*

---

## Common problems

| What you see | What to do |
|---|---|
| `Missing required environment variable(s)` locally | Your `.env` is missing/incomplete — redo Step 1 |
| Dashboard says "Could not read from Supabase" | Check `SUPABASE_URL` is exactly `https://<ref>.supabase.co`; re-copy the key |
| Dashboard shows "👁 View only" locally | Your `.env` has the anon key; put the **service_role** key in `SUPABASE_SERVICE_ROLE_KEY` |
| Strategies page is empty | Run Step 2 (`paper_engine.py`) once to seed |
| Backtest: "no candles returned" | You asked for more history than the free feed has — normal; see Step 5 |
| Every backtest trade loses | Quantity too low vs the ₹30 cost — see Step 4 |
| GitHub failure emails | Secrets missing, or the schedule is on while something's broken. `gh run view <id> --log-failed` shows the reason |
| Engine says `SKIPPED: market closed` | Correct behaviour outside 09:30–15:50 IST or on a holiday |

**Where to look first:** the **⚙️ System** page lists every engine run with its
status and reason.

---

## What's next

- **More history / F&O:** upgrade to paid Kite Connect — §9 of
  [OPERATING_GUIDE.md](OPERATING_GUIDE.md). One environment variable, no code
  changes.
- **Deeper reference:** [OPERATING_GUIDE.md](OPERATING_GUIDE.md) covers the
  full indicator/operator vocabulary and the strategy YAML format.

> **Reminder:** everything here is simulated. No screen in this app can place a
> real order. Real automated trading is a separate, regulated problem and
> should not be bolted onto this research tool.
