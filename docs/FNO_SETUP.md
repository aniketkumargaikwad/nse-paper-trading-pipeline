# F&O step 3 — your setup steps

Everything here needs your own accounts, cards or logins, so only you can do
it. About 45 minutes in all. When you have finished, tell me **"setup done"** —
just those words. **Never paste a key, token or password into the chat**, an
email or a file in this repository: the repository is public.

| Part | What it gives the tool | Cost |
|---|---|---|
| 1. Upstox | 1-minute prices of expired NIFTY and BANKNIFTY options | Upstox Plus (free to activate as of Oct 2026) |
| 2. Dhan | 1-minute index prices (and stock minutes) | ₹499 + GST a month |
| 3. Cloudflare R2 | A private online folder for the ~2.5 GB of minute data | Free under 10 GB; a card must be on file |
| 4. Saving the keys | The tool can read them on your PC and on GitHub | — |

Keep a note open (a password manager is best) to collect five values as you
go. Each is marked **► save this**.

**Status, 19 Oct 2026:**

| Part | Status |
|---|---|
| 1. Upstox | **Done** (14 Oct) — the one-year key works for expired options; Upstox serves two years of them |
| 2. Dhan | **Done** — data plan renewed; index minutes downloaded (2017 / 2021 onward) |
| 3. Cloudflare R2 | **Done** — bucket works; the F&O store (about 160 MB) is in it |
| 4a. `.env` | **Done** |
| 4b. GitHub secrets | **Done** — all nine F&O secrets are on GitHub |
| 6. Daily GitHub job | **Done** — fno-daily runs every weekday evening |
| 7. Options AI research | **Built 19 Oct 2026** — runs on GitHub once its branch is merged |

---

## 1. Upstox

### 1a. Account and Upstox Plus

1. Log in at [upstox.com](https://upstox.com) (or open an account if you have
   none — account opening needs your PAN and Aadhaar KYC).
2. Activate **Upstox Plus**. In the Upstox app or website, open your
   profile/account menu and look for **Upstox Plus** or **Plans**, then
   activate it. The expired-options data is only available on Plus.
   - You do not have to trade on Upstox. The tool only reads prices.

**If it says "account opening in progress" for days** (it did for you, 5+
days in Oct 2026): Upstox says verification takes up to 4 working days. When
it takes longer, the usual causes are an unfinished form step (e-sign, bank
proof, nominee) or a rejected document — most often a name or signature that
differs between PAN, Aadhaar and bank. Check your email and spam for a
request from Upstox; then raise a ticket at
[help.upstox.com/support/tickets/new](https://help.upstox.com/support/tickets/new)
and keep the ticket ID.

### 1b. The analytics token (a one-year read-only key)

1. Open the **Developer Apps** page:
   [account.upstox.com/developer/apps](https://account.upstox.com/developer/apps)
   (log in if asked).
2. Click the **Analytics** tab.
3. Click **Generate Token**, then **Confirm**.
4. Click the copy icon next to the token. **► save this** as
   `UPSTOX_ANALYTICS_TOKEN`.

About this token:
- It **can only read data**. It cannot place, change or cancel orders.
- It lasts **one year**. Put a reminder in your calendar for 11 months from
  today to make a new one (section 5).
- You can only have one at a time; making a new one replaces the old.

**The open question, answered 14 Oct 2026:** Upstox's documents did not say
whether this token covers *expired* contracts. It does — no daily login is
needed. And Upstox serves two years of expired options (back to 3 Oct 2024),
not the six months its page says.

---

## 2. Dhan — renew the data subscription

1. Log in at [web.dhan.co](https://web.dhan.co).
2. Open **Profile → DhanHQ Trading APIs**, then the **Data APIs** tab.
3. Subscribe (₹499 + GST a month; Dhan waives it in months with 25 or more
   trades, which this tool will not make).
4. Nothing new to save. The tool already uses your existing Dhan details from
   your `.env` file (`DHAN_CLIENT_ID`, `DHAN_PIN`, `DHAN_TOTP_SECRET`).
   Checked on 9 Oct 2026: your Dhan login still works; Dhan refused the data
   only because the plan is "FREE" — so subscribing is the only step here.

---

## 3. Cloudflare R2 — a private online folder

### 3a. Account

1. Sign up at [dash.cloudflare.com/sign-up](https://dash.cloudflare.com/sign-up)
   with your email, and confirm the email Cloudflare sends.
2. In the left menu choose **R2 Object Storage**.
3. Cloudflare asks you to **add a payment method** before R2 can be used —
   even for the free tier. Add a card. Nothing is charged while storage stays
   under 10 GB a month; this project needs about 3 GB.

### 3b. The bucket (the folder)

1. On the R2 page click **Create bucket**.
2. Name: `trading-tool-fno` (lowercase, exactly like this).
3. Location: leave **Automatic**. Storage class: leave **Standard**.
4. Click **Create bucket**.
5. **Leave public access off.** In the bucket's **Settings**, "Public
   Development URL" and "Custom Domains" must stay disabled. The data is
   licensed to you personally and must not be public.
6. **► save this**: `R2_BUCKET` = `trading-tool-fno`.

### 3c. The access key (lets the tool read and write that one bucket)

1. Go back to the main **R2 Object Storage** page.
2. Under **Account Details**, click **Manage** next to **API Tokens**.
3. Click **Create Account API token**.
4. Fill in:
   - Token name: `trading-tool-fno`
   - Permissions: **Object Read & Write**
   - Specify bucket(s): **Apply to specific buckets only** → `trading-tool-fno`
   - TTL: **Forever**
5. Click **Create API Token**. The next page shows several values **once
   only** — copy them before leaving the page:
   - **Access Key ID** → **► save this** as `R2_ACCESS_KEY_ID`
   - **Secret Access Key** → **► save this** as `R2_SECRET_ACCESS_KEY`
6. Your **Account ID** is on the main R2 page under Account Details (also
   inside the "S3 endpoint" address, `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`).
   **► save this** as `R2_ACCOUNT_ID`.

If you lose the secret, delete that token and make a new one; there is no
way to see it again.

---

## 4. Saving the keys in two places

You now have five values: `UPSTOX_ANALYTICS_TOKEN`, `R2_ACCOUNT_ID`,
`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`.

### 4a. On your PC: the `.env` file

1. Open the project folder `C:\Users\Aniket\Personal projects\Trading tool`.
2. Open the file named `.env` with Notepad (right-click → Open with →
   Notepad). It already holds your Supabase and Dhan lines.
3. Add these five lines at the end, with your values after the `=` (no spaces,
   no quote marks):

   ```
   UPSTOX_ANALYTICS_TOKEN=
   R2_ACCOUNT_ID=
   R2_ACCESS_KEY_ID=
   R2_SECRET_ACCESS_KEY=
   R2_BUCKET=trading-tool-fno
   ```

4. Save and close. (`.env` is never uploaded to GitHub — it is on the ignore
   list.)

### 4b. On GitHub: repository secrets

These let the daily GitHub job fetch data while your PC is off.

1. Open [your repository's secrets page](https://github.com/aniketkumargaikwad/nse-paper-trading-pipeline/settings/secrets/actions)
   (Settings → Secrets and variables → Actions).
2. For each of the five values: click **New repository secret**, type the
   name exactly as above (for example `R2_ACCESS_KEY_ID`) in **Name**, paste
   the value in **Secret**, click **Add secret**.
3. Add the three Dhan values the same way if they are not already in the
   list: `DHAN_CLIENT_ID`, `DHAN_PIN`, `DHAN_TOTP_SECRET` — copy each from
   your `.env` file.

GitHub never shows a secret again after it is saved, not even to you, and no
one viewing the public repository can see them.

---

## 5. Then tell me "setup done"

Everything that needs no key is already built. When you say "setup done" I
will, in this order:

1. Run `python scripts/fno_check_setup.py`. It tries each connection and
   prints only "works" or "fails, and why" — never a value. The Upstox line
   also answers the open question in 1b.
2. Copy the F&O data already on your PC (about 300 MB, 2016 to now) into the
   R2 folder: `python scripts/fno_r2.py push`.
3. Run the first minute download from your PC: `python scripts/fno_minute.py`.
   It prints how far back Upstox serves expired options (its own page says
   six months; we will know for certain) and fills the NIFTY, BANKNIFTY and
   India VIX minutes from Dhan back to April 2017. Then push again.
4. Tell you what it found, and plan step 3b — testing intraday strategies —
   around how much option history there really is.

## 6. Switching on the daily GitHub job

GitHub runs scheduled jobs only from the `main` branch, and all the F&O work
is still on its own branch (`feat/fno-options`). When you are ready, tell me
**"merge the F&O branch"** and I will open the pull request and merge it.
From then on:

- **fno-daily** runs every weekday at about 7 pm: the day's NSE file and any
  option expiry that has just ended (it must be saved within Upstox's
  window or it is gone), published to R2. Nothing to do; GitHub emails you
  if a run fails.
- **Index minutes come from Upstox in the daily job** (since 19 Oct 2026).
  Dhan refused this job's login from GitHub's servers on 15 and 16 Oct 2026;
  Upstox serves the same NIFTY, BANKNIFTY and India VIX minutes on the key
  you already made. Dhan filled the history before that.
- **fno-research** runs Monday, Wednesday and Friday at about 9 am: the AI
  designs an options strategy, tests it, and messages you the verdict
  (Telegram and email). Stock research runs Tuesday, Thursday and Saturday.
- **fno-backfill** runs only when you start it: GitHub → **Actions** →
  **fno-backfill** → **Run workflow**. You will not normally need it — the
  first fill runs from your PC in step 5.

## 7. Once a year, or when something changes

- **Upstox token** expires a year after you made it. Make a new one
  (section 1b), then replace `UPSTOX_ANALYTICS_TOKEN` in `.env` and on GitHub
  (on GitHub: click the secret's name, then **Update secret**).
- **Dhan** renews monthly on its own; if it lapses, the daily job will say so.
- **Cloudflare** needs nothing. If you ever want to be emailed about charges,
  Cloudflare's **Notifications** page can send a usage-based billing alert.

## 8. If something goes wrong

- *Upstox Plus not visible*: Plus may be offered only on some account types;
  tell me what the app shows and we will look at it together.
- *Cloudflare won't create the bucket*: the payment method is usually what
  is missing (3a, step 3).
- *Lost the R2 secret*: delete the token on the API Tokens page and create a
  new one (3c).
- *Pasted a secret somewhere public by mistake*: revoke it straight away
  (Upstox: Revoke on the Analytics tab; Cloudflare: delete the token), make a
  new one, and update both places.
