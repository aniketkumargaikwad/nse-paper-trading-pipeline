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

---

## 1. Upstox

### 1a. Account and Upstox Plus

1. Log in at [upstox.com](https://upstox.com) (or open an account if you have
   none — account opening needs your PAN and Aadhaar KYC).
2. Activate **Upstox Plus**. In the Upstox app or website, open your
   profile/account menu and look for **Upstox Plus** or **Plans**, then
   activate it. The expired-options data is only available on Plus.
   - You do not have to trade on Upstox. The tool only reads prices.

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

**One open question.** Upstox's documents say this token covers "market data",
but do not say clearly whether that includes *expired* contracts. I will test
it the first time I use it. If Upstox refuses it, I will tell you, and you
will make an ordinary Upstox app instead. That needs a quick login once a
day only while the history is being downloaded (about a week), and I will
write those steps then. Nothing to do about it now.

---

## 2. Dhan — renew the data subscription

1. Log in at [web.dhan.co](https://web.dhan.co).
2. Open **Profile → DhanHQ Trading APIs**, then the **Data APIs** tab.
3. Subscribe (₹499 + GST a month; Dhan waives it in months with 25 or more
   trades, which this tool will not make).
4. Nothing new to save. The tool already uses your existing Dhan details from
   your `.env` file (`DHAN_CLIENT_ID`, `DHAN_PIN`, `DHAN_TOTP_SECRET`). If you
   have changed your Dhan PIN or reset its authenticator since August, tell
   me and we will update them.

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

I will run a check that tries each connection and prints only "works" or
"fails, and why" — never the values. Then I start building step 3: the
Upstox and Dhan connections, the minute-data download, and the daily top-up.

### Once a year, or when something changes

- **Upstox token** expires a year after you made it. Make a new one
  (section 1b), then replace `UPSTOX_ANALYTICS_TOKEN` in `.env` and on GitHub
  (on GitHub: click the secret's name, then **Update secret**).
- **Dhan** renews monthly on its own; if it lapses, the daily job will say so.
- **Cloudflare** needs nothing. If you ever want to be emailed about charges,
  Cloudflare's **Notifications** page can send a usage-based billing alert.

### If something goes wrong

- *Upstox Plus not visible*: Plus may be offered only on some account types;
  tell me what the app shows and we will look at it together.
- *Cloudflare won't create the bucket*: the payment method is usually what
  is missing (3a, step 3).
- *Lost the R2 secret*: delete the token on the API Tokens page and create a
  new one (3c).
- *Pasted a secret somewhere public by mistake*: revoke it straight away
  (Upstox: Revoke on the Analytics tab; Cloudflare: delete the token), make a
  new one, and update both places.
