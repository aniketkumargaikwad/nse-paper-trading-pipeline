# Deploying the workbench — step by step

This guide assumes you have never used Railway and have never deployed
anything. Every step says exactly what to click.

**What you are about to do:** put this app on the internet so it runs on a
server instead of your laptop, with its own web address you can open from your
phone. Two pieces go up: the **dashboard** (the website) and the **paper
engine** (a small job that wakes up every 15 minutes during market hours).

**Roughly 20 minutes.**

**It costs money.** Railway no longer has a permanently free tier. You get a
small trial credit, then it is about $5/month for something this size. You
will have to add a card. If you would rather not, skip to
[Not ready to pay?](#not-ready-to-pay) at the bottom — you lose nothing about
testing strategies.

---

## Before you start: open your `.env` file

Everything you need is already in this one file. You will copy from it.

1. Open File Explorer and go to `C:\Users\Aniket\Personal projects\Trading tool`
2. You may not see `.env` — Windows hides files starting with a dot. In File
   Explorer, click the **View** menu → tick **Hidden items**.
3. Right-click `.env` → **Open with** → **Notepad**.

You should see eight lines like `SUPABASE_URL=https://...`. Leave this window
open. Do not paste its contents anywhere public — it holds your database and
broker keys.

> **If `.env` does not exist**, you are in the wrong folder, or setup was never
> completed. Stop here and say so.

### One value you have to invent

You also need a password for the dashboard, which is not in `.env` yet.

Pick something long — you will type it rarely. To have the computer make one,
open PowerShell in the project folder and run:

```bash
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(24))"
```

Copy the line it prints and keep it somewhere safe (a password manager). This
is what you will type to open your dashboard.

---

## Step 1 — Create a Railway account

1. Open **https://railway.app** in your browser.
2. Click **Login** (top right).
3. Choose **Login with GitHub**.
4. GitHub asks you to authorise Railway. Click the green **Authorize Railway**
   button.
5. You land on the Railway dashboard — a mostly empty page saying you have no
   projects.

---

## Step 2 — Create the project from your GitHub repo

1. Click the **New Project** button (a purple button, usually centre or top
   right).
2. A menu appears. Click **Deploy from GitHub repo**.
3. The first time, Railway asks for permission to see your repositories. Click
   **Configure GitHub App**. A GitHub page opens.
   - Choose **Only select repositories**
   - In the dropdown, pick **nse-paper-trading-pipeline**
   - Click **Save** / **Install**
   - You return to Railway
4. Back on Railway, click **nse-paper-trading-pipeline** in the list.
5. Railway may ask "Deploy Now?" — click **Deploy**.

A box appears representing your app and it starts building. **It will fail or
crash-loop at this point. That is expected** — it has no database keys yet.
That is Step 3.

> **What "building" means:** Railway is reading your code, installing Python
> and everything in `requirements.txt`. Takes 2–4 minutes the first time.

---

## Step 3 — Give it the keys (the important step)

1. Click the box representing your service (labelled
   `nse-paper-trading-pipeline`).
2. A panel opens. Click the **Variables** tab along the top.
3. Look for **Raw Editor** — usually a small link or button on the right of
   that tab. Click it. This lets you paste everything at once instead of
   typing eight separate rows.
4. Switch to Notepad, select **all** the text in `.env`
   (Ctrl+A), copy it (Ctrl+C).
5. Back in Railway's Raw Editor box, paste (Ctrl+V).
6. **Now add one more line at the bottom**, using the password you generated:

   ```
   APP_PASSWORD=the-password-you-generated
   ```

7. **Change one line before saving.** If your `.env` contains
   `CANDLE_STORE=parquet`, add this line beneath it:

   ```
   CANDLE_ROOT=supabase://candles
   ```

   On your laptop the price history sits in a folder. On the server it has to
   come from Supabase Storage instead. Why is explained just below.

8. Click **Save** / **Update Variables**.

Railway restarts the app automatically with the new values.

> ### Why the server needs `CANDLE_ROOT`
>
> Price history is stored as compressed files rather than database rows —
> about 5x smaller and dramatically faster to read.
>
> On your laptop those files live in a folder (`data/candles`). That cannot
> work on the server: Railway wipes its filesystem on every deploy, and the
> files are deliberately kept out of the repository because they are
> regenerable data, not code. A server pointed at a folder would find nothing
> and re-download millions of price bars on every deploy. The app refuses to
> start in that state rather than doing it quietly.
>
> `supabase://candles` points it at **Supabase Storage** instead — a different
> quota from your database (1 GB of files alongside 500 MB of rows on the free
> plan), on the account you already have. Your history is about 68 MB, so it
> fits comfortably and stops competing with your database for space.
>
> Measured on the same symbol: 1.3 seconds from Supabase Storage against 10.2
> seconds reading the same candles as database rows.

> **If you cannot find Raw Editor**, add them one at a time instead: click
> **New Variable**, type the name in the first box (e.g. `SUPABASE_URL`), the
> value in the second, then repeat. You need all eight from `.env` plus
> `APP_PASSWORD`.

> **`APP_PASSWORD` is not optional.** The dashboard carries a key with full
> access to your database. Without a password, anyone who stumbles on the web
> address could delete your strategies or set one trading live. The app knows
> this: deployed without a password it refuses to open and tells you so.

---

## Step 4 — Get your web address

1. Still in the service panel, click the **Settings** tab.
2. Scroll to **Networking** (sometimes called **Domains**).
3. Click **Generate Domain**.
4. If it asks which port, **accept whatever it suggests**. The app is written
   to listen on whichever port Railway hands it, so the detected value is the
   right one. Only if it refuses to detect anything, type `8080`.
5. A web address appears, something like
   `nse-paper-trading-pipeline-production.up.railway.app`. Click it.

**You should see a password box, not the dashboard.** Type your
`APP_PASSWORD` and click **Sign in**.

You are now looking at your trading workbench, running on the internet.

> **Seeing an error page instead?** Give it 2–3 minutes — it may still be
> building. Then see [When something is wrong](#when-something-is-wrong).

---

## Step 5 — Add the paper trading job

The dashboard is up, but nothing yet wakes up during market hours to check
your strategies. That is a **second service** in the same project.

1. Click your project name (top left) to go back to the project view.
2. Click **New** (or the **+** button) → **GitHub Repo** → pick
   **nse-paper-trading-pipeline** again.

   Yes, the same repository twice. One box runs the website; the other runs
   the scheduled job. Same code, different jobs.

3. Click the new box, then the **Settings** tab.
4. Find **Start Command** and enter exactly:

   ```
   python paper_engine.py
   ```

5. Find **Cron Schedule** and enter exactly:

   ```
   2,17,32,47 4-9 * * 1-5
   ```

   That means every 15 minutes from 09:32 to 15:17 India time, Monday to
   Friday. It looks like `4-9` rather than `9-15` because Railway works in UTC
   and India is 5½ hours ahead.

6. Click the **Variables** tab for **this** service and paste your `.env`
   contents again, exactly as in Step 3.

   `APP_PASSWORD` is not needed here — this service has no web page.

> **Why the engine cannot just run all the time:** it is designed to wake, look
> at the candle that just closed, act, and exit. Running constantly would
> cost more and do nothing extra.

---

## Step 6 — Check everything actually works

### The dashboard

1. Open your web address, sign in.
2. Top right should say **✏️ Edit mode**.
   - If it says **👁 View only**, `SUPABASE_SERVICE_ROLE_KEY` did not copy
     correctly. Redo Step 3.
3. Click **Strategies** in the left sidebar. You should see your grid with
   `NIFTY50-EMA-RSI-60m`.
4. Click **System** in the sidebar. It reports which data provider is
   configured and whether recent runs worked.

### The scheduled job

1. Go to the project view, click the **second** box (the cron one).
2. Click the **Deployments** tab → **Deploy** / **Run Now** to trigger it once
   by hand.
3. Click **View Logs**.

**Outside market hours you should see it say it skipped, and exit
successfully.** That is correct. The engine refuses to trade outside 09:15–
15:30 IST, at weekends, and on NSE holidays. A skipped run is a healthy run.

---

## When something is wrong

### The page says "This deployment has no APP_PASSWORD set"

Working exactly as intended. Go back to Step 3 and add `APP_PASSWORD`.

### "This site can't be reached" / `DNS_PROBE_FINISHED_NXDOMAIN`

**Your app is almost certainly fine.** This error means your computer could
not look up the address at all — it never reached the server, so nothing about
the deployment is implicated. If Railway shows the service as **Online**, it
is running.

Indian ISP resolvers (Jio, Airtel) are often slow to pick up newly created
subdomains, which is exactly what a fresh Railway domain is.

Confirm it, then fix it:

1. **Flush the local cache.** PowerShell as Administrator:

   ```
   ipconfig /flushdns
   ```

2. **Test on your phone with WiFi OFF**, using mobile data. If it loads there
   but not on your PC, the deployment is proven good and the problem is your
   home network's DNS.

3. **Point your PC at a public DNS.** Settings → Network & Internet → your
   network → Hardware properties → DNS server assignment → **Edit** → switch
   Automatic to **Manual** → turn IPv4 on → Preferred `1.1.1.1`, Alternate
   `8.8.8.8` → Save. Flush again.

To check whether the server is really up regardless of your DNS, from
PowerShell:

```
curl.exe -s -o NUL -w "%{http_code}" https://YOUR-DOMAIN.up.railway.app
```

`200` means the app is serving and only your name lookup was broken.

### Two domains appeared

Clicking **Generate Domain** twice creates two. Keep the one that shows a
**Port** underneath it and delete the other with the bin icon — a domain with
no port mapping cannot route to the app.

### "Application failed to respond" / 502

Usually still building, or it crashed at startup.

1. Click the service → **Deployments** → **View Logs**
2. Read the last 20 lines. The most common causes:
   - `SUPABASE_URL is not set` → variables did not save; redo Step 3
   - `ModuleNotFoundError` → the build did not finish; click **Redeploy**

### The password box appears but my password is refused

The value probably picked up a stray space or a quote mark. In Railway's
Variables tab, click `APP_PASSWORD`, delete the value, retype it carefully
with no quotes and no spaces around it, and save.

### It says "View only" and I cannot create strategies

`SUPABASE_SERVICE_ROLE_KEY` is missing or wrong. It is a very long string
starting with `eyJ`. Make sure the whole thing copied — it is easy to miss the
end.

### The cron job runs but nothing happens

Expected outside market hours. Check the logs say **skipped**. If it is a
weekday between 09:32 and 15:17 IST and it still skips, open **System** in the
dashboard — it will say whether the NSE holiday calendar thinks today is a
holiday.

---

## Not ready to pay?

Deployment is only needed for **paper trading on a schedule**. Everything else
works on your laptop for free:

```bash
.\.venv\Scripts\streamlit.exe run dashboard.py
```

That opens the same dashboard at `http://localhost:8501`. You can build
strategies, run backtests across NIFTY50, and read every result. The only
thing you lose is the engine waking up by itself during market hours.

Given you do not yet have a strategy that survives its own robustness rules,
this is a perfectly reasonable place to stay for now.

---

## Where the price history actually lives

Three copies, and it is worth knowing which is which:

| Copy | What it is | Size |
|---|---|---|
| `data/candles` on your laptop | What every backtest reads | 186 MB |
| Supabase **Storage** bucket `candles` | The backup, and what a host would read | 186 MB |
| Supabase **database** table `candles` | **Empty on purpose** | 0 |

The database table used to hold a 5-minute-only copy covering 2022-2026. It
was 483 MB of a 500 MB free tier, it was four years shorter than the files on
disk, and nothing read it - `CANDLE_STORE=parquet` means backtests go to the
files. It was cleared after checking every instrument's counts, spot-checking
candle values, and testing a read back out of the bucket. The database now
sits at 18 MB.

Storage is a **separate quota** from the database: 1 GB of files beside
500 MB of rows. That is why the backup is free.

Back up again after any backfill:

```bash
.venv/Scripts/python.exe scripts/backup_candles_to_storage.py
.venv/Scripts/python.exe scripts/backup_candles_to_storage.py --verify
```

It only sends what changed, so a routine run is quick.

> **If your laptop dies**, set `CANDLE_ROOT=supabase://candles` and the app
> reads history straight from the bucket. Slower than local disk, but nothing
> is lost and nothing needs re-downloading from Dhan.

---

## What this costs

| | |
|---|---|
| Railway | ~$5/month for both services, billed by usage. Check current pricing — this may be out of date |
| Supabase database | Free up to 500 MB. Currently 18 MB - candles live in Storage, not rows |
| Supabase Storage | Free up to 1 GB. NIFTY200 at 9.4 years uses 660 MB. NIFTY500 would need roughly 1.6 GB and exceed it |
| Dhan Data APIs | ~₹499+GST/month, which you already pay |

---

## Two background facts (not steps)

**Why not Vercel.** Vercel runs short-lived serverless functions. This
dashboard is a Streamlit server that stays running and holds a live connection
to your browser. They are incompatible — moving to Vercel would mean rewriting
the whole interface in a different language for no new capability.

**Why GitHub Actions was removed.** It used to run the paper engine every 15
minutes, and it is why that workflow kept failing. GitHub does not promise
scheduled jobs run on time; under load they arrive late or not at all. An
engine reading 15-minute candles cannot tell "nothing happened" from "nobody
woke me", so a late run silently skips signals. Railway's scheduler keeps time.

---

## What still runs from your laptop

Occasional maintenance, not day-to-day:

```bash
# Fetch price history for more symbols
.\.venv\Scripts\python.exe backfill.py --symbols NSE:TCS,NSE:INFY --years 2

# Refresh index membership after a rebalance (about twice a year)
.\.venv\Scripts\python.exe scripts/refresh_universes.py
```

Both write to the same Supabase database the deployment reads, so anything you
backfill locally shows up in the hosted dashboard straight away.
