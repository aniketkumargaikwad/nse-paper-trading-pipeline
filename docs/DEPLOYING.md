# Deploying the workbench

Two things need to run:

| What | How often | Why |
|---|---|---|
| **The dashboard** | always on | The website: build strategies, run backtests, read results |
| **The paper engine** | every 15 min, 09:32–15:17 IST, Mon–Fri | Evaluates the just-closed candle and simulates fills |

Both run from this one repository. Railway hosts both.

---

## Why not Vercel

Vercel runs serverless functions with a short execution limit and no
long-lived process. Streamlit is a persistent Python server that holds a
websocket open to the browser for the life of a session. They are
incompatible — this is not a configuration problem.

Putting the dashboard on Vercel would mean rewriting the UI as a Next.js
front end plus a separate Python API hosted elsewhere. That is weeks of work
for no capability this system does not already have.

## Why not GitHub Actions for the schedule

GitHub Actions ran the paper engine until now, and it is why the workflow was
disabled after intermittent failures.

GitHub does not guarantee scheduled workflows run on time. Under load they are
delayed — sometimes 20 minutes or more — and can be dropped entirely. For a
strategy evaluating 15-minute candles, a delayed run silently misses candles,
and the engine cannot tell the difference between "nothing happened" and "I
was never asked". A scheduler that keeps time is not optional here.

Railway's cron runs on a real schedule, and puts the app and the job in the
same place, with the same environment variables and one log to read.

---

## One-time setup

### 1. Create the project

Connect this GitHub repository to a new Railway project. Railway detects
Python and installs `requirements.txt` automatically.

### 2. Environment variables

Set these on the Railway service (Variables tab):

| Variable | Value | Why |
|---|---|---|
| `SUPABASE_URL` | your project URL | Database |
| `SUPABASE_SERVICE_ROLE_KEY` | the service-role key | **Full database access.** Required for the dashboard to create strategies and for the engine to write trades |
| `APP_PASSWORD` | a password you choose | **Required.** See the warning below |
| `DHAN_CLIENT_ID` | from Dhan | Market data |
| `DHAN_API_KEY` | from Dhan | |
| `DHAN_API_SECRET` | from Dhan | |
| `DHAN_TOTP_SECRET` | from Dhan | Automated token renewal |
| `DHAN_PIN` | your Dhan PIN | |

> ### ⚠️ `APP_PASSWORD` is not optional on a host
>
> The dashboard carries the **service-role** database key, because creating
> strategies and toggling them live requires it. Anyone who reaches the URL
> without a gate could create or delete strategies, flip one live, and read
> every trade you have made.
>
> The app refuses to render on a detected host when `APP_PASSWORD` is unset,
> and says so. Do not work around that.
>
> It is a single shared password, suitable for one operator. It is not user
> accounts, and is not suitable for handing to several people.

### 3. The web service

Railway reads the `Procfile`:

```
web: streamlit run dashboard.py --server.port $PORT ...
```

`$PORT` is injected by Railway; binding to `0.0.0.0` is what makes the
container reachable. Generate a domain under Settings → Networking.

### 4. The paper engine as a cron job

Add a **second service** in the same Railway project, from the same repo:

- **Start command:** `python paper_engine.py`
- **Cron schedule:** `2,17,32,47 4-9 * * 1-5`

That is every 15 minutes from 09:32 to 15:17 IST, Monday to Friday. Railway
cron is UTC, and IST is UTC+5:30 — hence `4-9` rather than `9-15`.

The engine gates itself as well: outside market hours, on a weekend, or on an
NSE holiday it writes a `run_audit` row with `status=skipped` and exits 0.
A skipped run is the normal result outside trading hours, not a failure.

Copy the same environment variables to this service. `APP_PASSWORD` is not
needed here — there is no web surface.

---

## Verifying a deployment

1. Open the domain. You should get a password prompt, not a dashboard.
2. Sign in. The header should read **✏️ Edit mode**; "View only" means the
   service-role key is missing or wrong.
3. Open **System**. It reports which data provider is configured and whether
   recent runs succeeded.
4. Trigger the cron service manually once from Railway. Outside market hours,
   expect a `skipped` run — that is correct behaviour, not an error.

## Costs

Railway bills by usage. A dashboard this size plus a job that runs a few
minutes a day is small, but check current pricing before committing — this
document may be out of date. Supabase's free tier covers roughly 500 MB, which
is about NIFTY100 at the 5-minute base; NIFTY500 needs the paid plan.

## Running a long backtest

A 50-symbol backtest takes several minutes. Two ways to run one:

* **From the dashboard** — the Backtest page's *Run a backtest* button. Fine
  for a handful of symbols; a large universe will hold that browser session
  open while it works.
* **As a Railway one-off command** — better for a big run, because it does not
  tie up the web service:

  ```
  python backtest.py --strategy MY-STRATEGY --years 2
  ```

  Run it from the Railway service shell, or add a temporary service with that
  start command and no schedule.

The old `backtest.yml` GitHub Action did this too, and was removed along with
the cron workflow. It worked, but it meant a second copy of every secret in a
second place — and secrets drifting out of sync was already a source of
failures here. One environment is easier to keep correct than two.

## What still runs locally

Nothing has to. Backfilling and universe refreshes are occasional commands you
can run from your laptop:

```bash
.venv\Scripts\python.exe backfill.py --symbols NSE:TCS,NSE:INFY --years 2
.venv\Scripts\python.exe scripts/refresh_universes.py
```

Both write to the same Supabase project the deployment reads, so a backfill
from your laptop is immediately visible in the hosted dashboard.
