# FabInvests vs. this platform — analysis and comparison

Source: `https://fabrichhhhhh.com/free/build-an-ai-trading-bot-with-claude`
"How to Build an AI Trading Bot With Claude (Paper Trading)" by Zaid Koradia
(FabRich), updated 10 Jul 2026. 9,747 words, 22-minute read.

**Method:** the page was fetched and read in full. Everything below about
FabInvests comes from what the article *describes*. **I did not build or run
their bot**, so statements about its behaviour are statements about its design,
not measurements. Everything about our platform is measured.

---

## 1. What the article actually is

A **free lead magnet**. Note the URL path: `/free/`.

Eleven copy-paste prompts that make Claude Code build "FabInvests" — a
paper-trading bot plus a Next.js dashboard. The reader writes no code; they
paste prompts in order. The author states he built it in two days.

The funnel is explicit at the end of the page:

| Offer | Price |
|---|---|
| The guide | Free |
| "AI Mastery" course | Not priced on this page |
| 1:1 with Zaid | **from ₹1,00,000** ("Only 2 of 10 seats left this month") |
| Done-for-you build | **US$2,500 setup + US$500/month** |

That is not a criticism. It is the business model, and it matters for §9.

## 2. Its stated motive and end goal

Unusually honest, and worth quoting because it contradicts what the title
leads you to expect:

> "Every trading bot video promises you profit and hides the losses. It made me
> angry. So I built the opposite. A bot that shows you the real loss math."

> "It has no secret edge, so a fast gain is luck, not skill. Never put real
> money on it."

> **"Can I switch it to real money later? Please do not."**

**The end goal is education, not profit.** The product teaches how leverage,
liquidation, fees and funding actually destroy accounts, by letting you watch
it happen safely. The author states five separate times that it makes no money
and should never be pointed at real money.

## 3. What it builds, technically

| | |
|---|---|
| Runtime | Node ESM, **zero third-party dependencies**, built-in `fetch`/`fs` |
| UI | Next.js 16 + React 19 + Tailwind v4 + Recharts, 8 tabs |
| Storage | **JSON / JSONL files on disk.** No database |
| Data | **Yahoo Finance, keyless**; Coinbase spot fallback for crypto; USDINR from Yahoo |
| History depth | `fetchHistory(symbol, range="6mo", interval="1d")` — **6 months of daily closes** |
| Live loop | `loopSeconds: 60` |
| Markets | Crypto, US equities, Indian equities — 14 symbols |
| Strategies | 6 hardcoded rules: time-series momentum, Donchian breakout, RSI-2 dip (Connors), momentum+trend, opening-range breakout, intraday breakout. Explicitly "deterministic rules, ZERO AI tokens" |
| Leverage | **Up to 40× crypto**, 4× US, 5× India |
| Sizing | Fractional Kelly, clamped 0.02–0.40 |
| Episodes | Start $100 → goal $500 in 24h, or blow up → reset → retry |
| Memory | Lessons banked per episode, tagged to market regime |
| "World" layer | VIX, DXY, oil, gold, ^TNX; crypto Fear & Greed; Hyperliquid funding/OI; StockTwits bull-bear |
| Backtesting | **None.** No prompt builds one |

That last row is the single most important technical fact.
`backtest_stats.json` appears as a *path* and as a Kelly *prior*, but nothing
in the eleven prompts produces it. The bot is **forward-only**: it learns by
losing money in live paper time.

Worth noting what the AI does and does not do here. Despite "AI trading bot"
in the title, the strategies are hardcoded deterministic rules — the prompt
says "ZERO AI tokens". Claude builds the bot; Claude does not trade it.

---

## 4. Head-to-head: data

| | FabInvests | This platform |
|---|---|---|
| Provider | Yahoo Finance (free, keyless) | **Dhan DhanHQ v2 (paid, ₹499/mo)** |
| Granularity | Daily closes | **5-minute base**, resampled to 15/25/30/60m |
| History used | **6 months** | **2 years cached** (5 available) |
| Historical bars | ~14 symbols × ~126 days ≈ **1,800** | **3,119,362 candles** |
| Coverage tracking | None | `candle_coverage`; a partial fetch is never recorded as complete |
| Quality checks | None described | Split / gap / OHLC sanity (`data_quality.py`) |
| Corporate actions | Not mentioned | Dhan daily feed is adjusted |
| Symbol identity | Yahoo tickers | Dhan security IDs, deduplicated, series-filtered |

**We are ahead on precision and depth by roughly three orders of magnitude in
historical data points.** Our audit already rejected Yahoo for exactly the
reason they depend on it: it is free, and free is what their zero-cost promise
requires.

**They are ahead on breadth** — crypto (24/7), US, and India in one system,
where we cover NSE cash equity only.

## 5. Head-to-head: correctness

| | FabInvests | This platform |
|---|---|---|
| Look-ahead awareness | Yes — "next-tick fills (no looking into the future)" | Yes — signals fill next candle; ATR read at signal candle; trailing stop updates *after* exit checks, **mutation-tested** |
| Fill realism | Fees, slippage, funding, **mark-price liquidation** | Adverse slippage, flat cost, worst-case stop-before-target, gap fills at open |
| Leverage / margin | **Modelled properly** (IMR, MMR, tiers, liquidation price) | **Not modelled at all** |
| Survivorship bias | Not addressed | Present, **recorded** (`constituents_as_of`) |
| Test suite | Not mentioned | **586 tests** |
| Null vs zero | Not addressed | Unmeasurable metrics return null, never 0 |

**Split verdict.** We are stronger on backtest correctness and have tests to
prove it. **They model leverage and liquidation, which we do not model at all**
— if margin trading ever matters to you, they are ahead and we start at zero.

Credit where due: their instruction that strategies emit "next-tick fills (no
looking into the future)" shows the author understands look-ahead bias. Many
tutorials do not.

## 6. Head-to-head: architecture

| | FabInvests | This platform |
|---|---|---|
| Language | Node/TypeScript | Python |
| Persistence | Flat files | Postgres (Supabase) |
| Layering | Scripts + shared lib | Providers → candle store → engine → metrics → UI, behind protocols |
| Provider swappability | Yahoo hardcoded in `lib.mjs` | `CandleProvider` + `CandleBackend` protocols |
| Reproducibility | Episodes are append-only logs | **Broken** — strategies keyed by name; saving overwrites |
| Deployability | **Local only** — "some free data sources block data-center networks. Run it on your home internet, not a server" | Deployed on Railway, publicly reachable |

**Ours is the more serious architecture, with one embarrassing exception.**
Their episode log is append-only and therefore *more reproducible than our
mutable strategies table*. That is the P0 in our own audit, and a two-day
tutorial got it structurally right where we got it wrong.

Their hosting constraint is a real limitation they state honestly: the design
cannot run on a server, because the free feeds block data-centre IPs.

## 7. Head-to-head: enrichment and output

| | FabInvests | This platform |
|---|---|---|
| Market regime | **VIX, DXY, oil, gold, yields → risk_on/risk_off** | **Nothing** |
| Sentiment | Crypto F&G, StockTwits, funding rates | Nothing |
| Cross-asset context | Yes | No |
| Per-symbol dispersion | No | **Yes** |
| Statistical honesty | **Wilson lower bound** on win rate, SQN | Null-not-zero, dispersion, kill rules |
| Risk metrics | SQN, expectancy, profit factor | Sharpe, Sortino, CAGR, expectancy, SQN, max DD |

**They win on enrichment, clearly.** Our audit listed regime dependence as
unaddressed; they have built it. Their **Wilson lower bound** is a genuinely
good idea we lack — it is the statistically honest way to say "this 60% win
rate is based on 8 trades, so the true rate is somewhere between 27% and 86%."

On output: theirs is 8 live tabs built for *watching*. Ours is a grid and
verdict built for *judging*. Neither has equity curves per backtest, trade
drill-down, or comparison views.

---

## 8. The core problem each one solves

This is where the comparison stops being close, because they are not competing.

**FabInvests answers:** *"What does leverage actually do to an account?"*
It answers this well, viscerally, and safely.

**This platform answers:** *"Is this edge real, or am I fooling myself?"*
It answered that decisively: 3,309 trades, 1 of 50 symbols profitable,
Sharpe −5.27, kill rules failed.

A bot that cannot backtest cannot answer our question at all. A backtester
that cannot model 40× leverage cannot answer theirs. **Neither is a better
version of the other.**

### On accuracy — the uncomfortable symmetry

Their bot runs **fractional Kelly at up to 40× leverage** on edge estimates
drawn from live paper results and six months of daily bars. Kelly is brutal
when edge is overestimated: it sizes *up* into a mirage. They mitigate with
Wilson bounds, probation and an "earn it" unlock, which is thoughtful — but the
underlying statistics are thin by construction.

**They know this.** The blow-ups are the curriculum.

Our exposure is the mirror image: our numbers are statistically sounder (3,309
trades, not 30) but our record-keeping is not (results reference mutable
strategies). **They have a weak signal honestly logged. We have a strong signal
carelessly filed.** Both are fixable; ours is cheaper to fix.

---

## 9. Money — answered directly

You said earning is the core purpose, so this deserves bluntness.

**Neither platform makes trading money today.** Theirs by design and admission;
ours because the one strategy tested lost decisively.

**But look at where the money in that article actually is.** The bot is free.
The revenue is ₹1,00,000 1:1 coaching, a course, and US$2,500 + US$500/month
done-for-you builds.

**FabRich monetises teaching people to build trading bots, not trading.**

That is the most commercially instructive fact on the page, and it reflects the
industry: in retail algorithmic trading, selling tools and education to traders
is a far more reliable business than trading. That is not cynicism — it is why
brokers give platforms away.

### Three honest paths from where you stand

| Path | Realistic? | What it needs |
|---|---|---|
| **Find a genuine edge and trade it** | Hard and uncertain, but the only one that scales without your time | Parameter sweeps, walk-forward, out-of-sample. Months. Most searches fail — that is the normal outcome, not a failure of the tool |
| **Sell the platform to other traders** | Plausible; Indian retail algo tooling is underserved | Multi-user auth, hosting, support, marketing. A different job from the one you are doing |
| **Content/education, like FabRich** | Proven by his existence | An audience. You would start from zero against someone with 10M views |

**My honest read:** your platform is *better engineered for the first path than
FabInvests could ever be*, because it can actually test whether an edge exists
— precisely the capability their design lacks. But that path is the hardest of
the three, and the correctness work already done buys you one specific thing:
**you will discover you are wrong quickly and cheaply, instead of slowly and
expensively.** Over a search that mostly produces failures, that is the whole
game.

---

## 10. What to take from them

Ranked by value to your stated goal:

1. **The regime / world layer.** VIX, DXY, yields, F&G scored into
   risk_on/risk_off. Our audit flagged regime dependence as unaddressed; they
   have a working design and it costs nothing. **Highest-value borrow.**
2. **Wilson lower bound on win rate.** One function. Exactly the honesty
   discipline we already apply elsewhere, applied to the number most likely to
   fool you.
3. **Episodes as immutable, append-only records with a lesson attached.** Very
   close to the `strategy_versions` + `backtest_configs` entities our audit
   says we need.
4. **Crypto as a research accelerator.** 24/7 markets generate far more
   independent test data per week than NSE's 6.25 hours × 5 days.
5. **Leverage and liquidation math**, if margin ever matters.

## 11. What not to take

1. **Yahoo as a precision source.** Already rejected on evidence: ~58 days of
   intraday history. Fine for a demo, not for research.
2. **Kelly at 40× on thin statistics.** Correct as a teaching device, wrong as
   a method.
3. **Sizing decisions from live results with no backtest.** That is the loop we
   built a backtester specifically to avoid.
4. **Flat files at our scale.** Fine for 14 symbols; we hold 3.1M candles.

---

## 12. Scorecard

| Dimension | Winner | Margin |
|---|---|---|
| Historical data volume | **Ours** — 3.1M vs ~1,800 bars | Decisive |
| Data precision | **Ours** — paid 5-min vs free daily | Decisive |
| Market breadth | **Theirs** — crypto + US + India | Clear |
| Backtesting | **Ours** — they have none | Total |
| Bias controls | **Ours** — mutation-tested | Clear |
| Leverage / liquidation | **Theirs** — we have none | Total |
| Market regime enrichment | **Theirs** — we have none | Total |
| Reproducibility | **Theirs** — append-only vs our mutable rows | Uncomfortable |
| Architecture rigour | **Ours** — 586 tests, layered, swappable | Clear |
| Visual polish | **Theirs** | Clear |
| Decision usefulness | **Ours** | Clear |
| Deployability | **Ours** — theirs cannot be hosted | Clear |
| Time to build | **Theirs** — 2 days vs weeks | Decisive |
| Money from trading | **Neither** | — |
| Money from the project | **Theirs** — course + services funnel | Total |

## 13. One-line summary

**They built an excellent teaching toy in two days and monetised the teaching.
We built a genuine research instrument over weeks and have not monetised
anything.**

Their honesty about having no edge is the same value our kill rules enforce —
we just enforce it with evidence rather than a disclaimer.

The gaps worth closing are theirs to teach us: **market regime, Wilson bounds,
immutable run records, and possibly crypto for faster iteration.** The gap they
cannot close is the one that decides whether you ever earn anything: **knowing
whether a strategy works before you risk money on it.**
