# Tracking and concluding the work — where things stand, and how they will be judged

Written 21 Oct 2026. The review rule below was fixed **before any paper book
had a single day**, so no result can bend it. It lives in code too
(`research/paper_books.py`, `scorecard`), and the dashboard applies it.

## What the research concluded (Aug–Oct 2026)

| Question | Answer | Where |
|---|---|---|
| Do single-stock rules (intraday, swing) beat holding? | No. ~200 AI-designed versions; none passed. Their leads came from the 2020–24 boom and from hindsight in today's stock list. | `docs/research/2026-08-22-why-strategies-lose.md`, research runs |
| Do textbook option strategies make money after costs? | No. 16 overnight and 12 same-day classics; none passed. Selling wins often and loses big; costs eat the rest. | `docs/research/2026-10-06-fno-atlas.md`, `2026-10-19-fno-intraday-atlas.md` |
| Can the AI design better option strategies? | Not yet. Best: a BANKNIFTY iron condor, +6% a month on only 33 trades, failed on its worst day. | `research/journal/options/` |
| Is there any real edge? | **Yes - momentum.** NSE's own momentum indexes beat NIFTY over 2009-2025: Nifty200 Momentum 30 about 21% a year, **Midcap150 Momentum 50 about 27% a year (luck test t = 3.9)**, NIFTY 50 15%. | `docs/research/2026-10-20-momentum-rotation.md`, `2026-10-21-factor-indexes.md` |
| What does that edge cost? | Deep falls: Midcap150 Momentum 50 lost 65% in 2008. A simple cash rule (out when NIFTY is below its 200-day average) cut that to 44% and the 2009-2025 worst fall to 32%. | same |
| Realistic target? | **1.5-2% a month** (20-27% a year), reset 20 Oct 2026 from 5-7%, which nothing reached. | `research/segment.py` |

## What is running, by itself, on GitHub (no real money anywhere)

| When | Job | What you get |
|---|---|---|
| Mon, Wed, Fri morning | `fno-research` | the options AI designs and tests; Telegram message with the verdict |
| Tue, Thu, Sat morning | `research` | the stock AI improves on momentum; Telegram message |
| Weekday evenings | `fno-daily` | option and index data saved |
| Weekday evenings | `momentum-paper`, `paper-books` | every paper book recomputed; the dashboard updates |
| First trading day of each month | `paper-books` | one Telegram message: every book, its return, and its review status |

GitHub starts these late, often by 4-5 hours. Email messages need the
`GMAIL_USER`, `GMAIL_APP_PASSWORD` and `NOTIFY_EMAIL` secrets, which are empty;
Telegram works.

## The paper books (started 20-21 Oct 2026)

| Book | Training said | Benchmark |
|---|---|---|
| Momentum 30 + the cash rule | 17.5% a year, worst fall 23% (NSE index) | NIFTY 50 |
| Midcap150 Momentum 50 + the cash rule | 23.7% a year, worst fall 32% (NSE index) | NIFTY 50 |
| Momentum rotation, 10 stocks (R4) | 3.5% a month (hindsight-inflated) | NIFTY 50 |
| Momentum rotation, 20 stocks (R1) | 3.1% a month (hindsight-inflated) | NIFTY 50 |
| Leader buy-and-hold (stock AI) | 4.4% a month (hindsight-inflated, few trades) | NIFTY 50 |
| BANKNIFTY same-day iron condor (options AI) | 6.0% a month on 33 trades | BANKNIFTY |

From the start date on there is no hindsight: the stock list is fixed and the
future is unknown to it. These are the honest numbers.

## The review rule (fixed 21 Oct 2026)

- **STOP, any time:** the paper account falls more than **30%** from its high.
- **6 months — 21 Apr 2027:** **DROP** if more than **5 points** behind just
  holding its benchmark from the same day. The stock and options books need
  **20 closed trades** first; with fewer they wait for 12 months.
- **12 months — 21 Oct 2027:** **PASSED** if ahead of its benchmark with a
  worst fall within 30%; otherwise **FAILED**.
- Only a PASSED book is worth discussing with real money, small, and that
  decision is the owner's. Nothing in this system can place an order.

## How to check, in two minutes

1. **Monthly:** read the Telegram message on the first trading day of the month.
2. **Any time:** dashboard → **Paper books** (one row per book: return,
   benchmark, worst fall, review status) and **Momentum**.
3. **On the review dates:** open Paper books; the Review column already says
   STOP / DROP / waiting / on track / PASSED / FAILED by the rule above.
