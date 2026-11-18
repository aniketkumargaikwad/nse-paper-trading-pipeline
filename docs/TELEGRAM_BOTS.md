# One Telegram bot per purpose

Until 18 Nov 2026 every message came from one bot, so a research result, a
paper-trading summary and a "job failed" alert could arrive together and look
alike. Now each kind of message has its own bot, so the bot's name in Telegram
tells you what a message is about.

| Bot (suggested name) | What it sends | When | GitHub secret for its token |
|---|---|---|---|
| **Index research** | The AI's index-plan research results, 🟢 pass / 🔴 fail | Tue, Thu, Sat mornings | `TELEGRAM_RESEARCH_BOT_TOKEN` |
| **Options research** | The AI's options research results, 🟢 / 🔴 | Mon, Wed, Fri mornings | `TELEGRAM_OPTIONS_BOT_TOKEN` |
| **Paper trading** | The weekly paper-trading update, every strategy in its own box | Friday evenings | `TELEGRAM_PAPER_BOT_TOKEN` |
| **Alerts** | "A job FAILED" warnings only | Only when something breaks | `TELEGRAM_ALERTS_BOT_TOKEN` |

Until you add a bot, its messages keep coming from your current bot
(`TELEGRAM_BOT_TOKEN`), so nothing stops working while you set them up.

## Setting up the bots (about 10 minutes)

**1. Make each bot.** In Telegram, open **@BotFather** and send `/newbot`. Give
it a name (e.g. "Aniket Paper Trading") and a username ending in `bot`
(e.g. `aniket_paper_bot`). BotFather replies with a **token**, a long code
like `123456789:AAH...`. Keep it private: anyone with it can send messages as
your bot. Do this four times, once per row in the table above.

**2. Press Start on each new bot.** Open each bot in Telegram (BotFather gives
its link) and tap **Start**. A bot cannot message you until you do.

**3. Add each token to GitHub.** On GitHub, open the repository, then
**Settings → Secrets and variables → Actions → New repository secret**.
- Name: the secret name from the table, e.g. `TELEGRAM_PAPER_BOT_TOKEN`
- Secret: that bot's token

**You do not need new chat IDs.** In a private chat your chat ID is your own
Telegram account number, the same for every bot, and the jobs reuse your
existing `TELEGRAM_CHAT_ID`. Only if you want a bot to post somewhere else
(for example a group) do you add `TELEGRAM_RESEARCH_CHAT_ID`,
`TELEGRAM_OPTIONS_CHAT_ID`, `TELEGRAM_PAPER_CHAT_ID` or
`TELEGRAM_ALERTS_CHAT_ID`. To find a chat ID, put the token in `.env` as
`TELEGRAM_BOT_TOKEN` and run `python scripts/telegram_chat_id.py`.

**4. Check the paper bot.** On GitHub: **Actions → paper-books → Run workflow**,
tick **"Send the weekly paper-trading message now"**, and run it. The weekly
message should arrive from the paper-trading bot within a few minutes.

## Where this is wired

Each workflow in `.github/workflows/` hands its job the purpose's secret, falling
back to the shared one: `${{ secrets.TELEGRAM_PAPER_BOT_TOKEN || secrets.TELEGRAM_BOT_TOKEN }}`.
Every "Say so on Telegram if the job failed" step uses the ALERTS pair. The
code itself (`research/blocks.py`, `research/notify.py`,
`scripts/alert_failure.py`) only ever reads `TELEGRAM_BOT_TOKEN` /
`TELEGRAM_CHAT_ID`. Which bot a job uses is decided in the workflow file alone.

## Colours

Telegram cannot colour text: its message format allows bold, italic, code and
links, but no colours. So a pass is marked 🟢 and a fail 🔴 at the start of the
line, and ⚪ means no verdict yet (`research.blocks.mark`).
