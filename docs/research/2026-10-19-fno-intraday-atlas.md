# The intraday options atlas — same-day option trades, Sep 2024 to Oct 2025

Measured 19 Oct 2026 by `python scripts/fno_atlas.py --intraday` (step 3b,
`docs/superpowers/specs/2026-10-19-fno-intraday-design.md`). Full reports in
`data/fno/atlas/intraday.json`.

## In plain words

- **None of the six classic same-day option trades passes your rule**, on
  either index. Five of six lost money outright on BANKNIFTY; NIFTY did better.
- **Selling only when options are expensive** (the lead from the overnight
  atlas) was tested too, fixed in advance: it wins often and falls little, but
  trades about 3 times a year and so trails simply holding NIFTY.
- **The closest is NIFTY's "09:20 straddle"**: sell the at-the-money call and
  put at 09:20, stop at 30% of the premium, take profit at 50%, out by 15:15.
  It made **+0.72% a month**, with a worst day of −4.3% and a worst fall of
  10.8%, and it beat simply holding NIFTY in both calendar years — but its lead
  could easily be luck (t = 0.24 against the 2.0 the rule asks for). One year
  is too short to tell a real edge from a good run.
- **Buying breakouts lost.** Buying a call or put when NIFTY or BANKNIFTY broke
  its first-30-minute range won 36–41% of the time and fell 35–43% from its
  high — the time decay of a bought option outran the moves.
- **BANKNIFTY is the harder market for this.** Since Nov 2024 it has only a
  monthly expiry, and a monthly option barely decays inside one day, so the
  same-day sellers paid four legs of spread and charges for very little.
- **The test window is short and calm.** It holds one year of training
  (the year after is the sealed exam), and no 2020-style crash day.

## The table

Training: NIFTY 19 Sep 2024 – 17 Oct 2025, BANKNIFTY 25 Sep 2024 – 17 Oct 2025
(the first day with real option minutes to the start of the exam year). ₹5,00,000;
nearest weekly expiry (BANKNIFTY: its monthly, the only one left); out by 15:15.
Each sized so one stop costs about 1–3% of the account: the sold straddles and
strangles up to all the margin (one or two sets), the iron fly and the bought
options at a tenth.

| strategy | index | passes? | avg month | months up | worst day | worst fall | trades/month | years beating index | luck test | ₹5 lakh became |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|
| straddle-0920 | NIFTY | no — lead could be luck (t = +0.24) | +0.72% | 43% | −4.26% | 10.8% | 18.4 | 100% | cannot be told from luck | ₹5,40,119 |
| strangle-0930 | NIFTY | no — lost money on average | −0.52% | 29% | −2.31% | 9.0% | 18.4 | 0% | cannot be told from luck | ₹4,62,482 |
| expiry-day-straddle | NIFTY | no — too slow (3.9 trades a month) | +0.28% | 43% | −2.06% | 3.8% | 3.9 | 50% | cannot be told from luck | ₹5,19,382 |
| iron-fly | NIFTY | no — beat the index in 0% of years | +0.00% | 43% | −3.83% | 10.5% | 15.5 | 0% | cannot be told from luck | ₹4,94,008 |
| orb-call | NIFTY | no — fell 38.2% | −1.34% | 29% | −3.26% | 38.2% | 10.2 | 0% | cannot be told from luck | ₹3,82,823 |
| orb-put | NIFTY | no — fell 36.6% | +0.14% | 57% | −3.15% | 36.6% | 10.2 | 50% | cannot be told from luck | ₹4,74,544 |
| straddle-0920 | BANKNIFTY | no — fell 30.2% | −1.38% | 29% | −9.39% | 30.2% | 16.9 | 0% | cannot be told from luck | ₹3,84,561 |
| strangle-0930 | BANKNIFTY | no — worst day −10.4% | −1.58% | 36% | −10.37% | 23.4% | 16.7 | 0% | cannot be told from luck | ₹3,84,534 |
| expiry-day-straddle | BANKNIFTY | no — too slow (1.2 trades a month) | +0.26% | 43% | −3.30% | 5.9% | 1.2 | 50% | cannot be told from luck | ₹5,17,974 |
| iron-fly | BANKNIFTY | no — fell 72.1% | −8.54% | 0% | −6.08% | 72.1% | 12.7 | 0% | cannot be told from luck | ₹1,39,591 |
| orb-call | BANKNIFTY | no — fell 35.4% | −1.49% | 36% | −3.18% | 35.4% | 8.6 | 0% | cannot be told from luck | ₹3,89,760 |
| orb-put | BANKNIFTY | no — fell 42.7% | −3.32% | 21% | −3.14% | 42.7% | 9.2 | 50% | cannot be told from luck | ₹3,04,815 |

## Strategy by strategy

- **09:20 straddle.** NIFTY won 56% of 258 trades and finished ₹40,119 up;
  BANKNIFTY won 50% and lost. On NIFTY a weekly option loses real value inside
  a day, which is what a same-day seller is paid for.
- **09:30 strangle (20-delta).** Won 54–57% but lost on both: the cheaper
  out-of-the-money options decay less in rupees, and the stop at 50% of a small
  premium is hit by ordinary noise.
- **Expiry-day straddle.** Small gains, but trades only on expiry days (about
  4 a month on NIFTY, 1 on BANKNIFTY) — too few for the rule, and won only
  37–47%.
- **Iron fly (ATM sold, wings 2% out).** Break-even on NIFTY. On BANKNIFTY's
  monthly contract it mostly ran to 15:15 slightly down (163 of 178 trades) —
  a monthly option's daily decay does not cover four legs of costs.
- **Opening-range breakout buys.** The worst family: most breakouts fade, and
  a bought option loses value every minute nothing happens.

## The "expensive options" test (pre-registered)

The overnight atlas (6 Oct) left one lead: selling options did well on the
few days IV rank was in its top third. Six variants were fixed **before any
was run** — each an atlas strategy with one added rule, *enter only when
`iv_rank >= 67`* — and all six are reported. Training years only
(overnight: Jan 2016 – Sep 2025; same-day: as above).

| | strategy | index | trades | won | avg month | worst day | worst fall | years beating index | luck test (t) | ₹5 lakh became | passes? |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| P1 | short-strangle-20d (overnight) | NIFTY | 31 | 84% | +0.12% | −3.65% | 3.2% | 10% | −2.30 | ₹5,72,032 | no — too slow |
| P2 | short-strangle-15d (overnight) | NIFTY | 31 | 87% | +0.11% | −3.23% | 3.0% | 10% | −2.32 | ₹5,67,056 | no — too slow |
| P3 | iron-condor (overnight) | NIFTY | 37 | 81% | +0.48% | −6.99% | 7.4% | 20% | −1.33 | ₹8,65,877 | no — too slow |
| P4 | short-strangle-20d (overnight) | BANKNIFTY | 33 | 79% | +0.11% | −5.75% | 2.9% | 0% | −1.92 | ₹5,70,851 | no — too slow |
| I1 | straddle-0920 (same-day) | NIFTY | 2 | 100% | +0.14% | 0.00% | 0.0% | 50% | −0.02 | ₹5,09,801 | no — too slow |
| I2 | strangle-0930 (same-day) | NIFTY | 2 | 50% | −0.08% | −1.51% | 1.1% | 50% | −0.23 | ₹4,94,647 | no — lost money |

In plain words:

- **The lead is real but small.** Selling only when options are expensive won
  79–87% of the time, never fell more than 7.4%, and all four overnight
  versions made money. The iron condor turned ₹5 lakh into ₹8.66 lakh over
  almost ten years.
- **But it is far too rare, and slower than just holding NIFTY.** About 3
  trades a year. The money sits idle the rest of the time, so holding the
  index beat it in 80–100% of years. The negative luck-test numbers mean it
  reliably did *worse* than the index, not better.
- **Same-day, the rule almost never fired**: options were expensive on only 2
  of about 260 days in Sep 2024 – Oct 2025, a calm year. Nothing can be
  learned from 2 trades.
- **Conclusion:** "sell when expensive" is a good *filter* to add to a strategy
  that trades often, not a strategy on its own.

## Bugs found and fixed before these numbers

- **Legs that cancel.** On BANKNIFTY 28 Nov 2024 so few strikes had traded by
  09:20 that the iron fly's 2% wing landed on its own sold strike. The legs
  cancelled, margin came out near zero, and sizing divided by it — a loss of
  trillions of percent. Such positions are now refused, and no position blocks
  less than the net premium it costs.
- **A quarter of the balance could not buy one straddle.** One NIFTY straddle
  set on expiry day needs about ₹2.5 lakh of margin; the overnight atlas's
  quarter of ₹5 lakh is ₹1.25 lakh, so every entry would have been skipped.
  Intraday sizing is set by what one stop costs instead, fixed before any
  result was seen.

## What to try next

1. **NIFTY 09:20 straddle, made safer and pickier**: same-day decay on the
   weekly is the one thing that clearly worked. Try entry rules that skip the
   riskiest days (an event day, VIX jumping at the open, a gap open), each
   pre-registered and all reported.
2. **Combine it with the expensive-options filter as a size dial**: trade
   every day, but with more sets when IV rank is high, instead of only on
   those days.
3. **Leave BANKNIFTY same-day selling alone** while it has only a monthly
   expiry.
4. **Paper-trade before trusting any of it** (step 5): one calm year cannot
   show how a seller behaves in a crash, and margin here is an estimate.

## The overnight atlas, re-run after these fixes

`python scripts/fno_atlas.py` re-run the same day, now with the pass column:
**none of the 16 overnight strategies passes** either (as on 6 Oct). Only two
numbers moved, both on NIFTY: the bull-put spread ended at ₹72,955 (was
₹68,849) and the long straddle before events at ₹1,64,823 (was ₹1,66,502).
That comes from the margin floor and the refusal of legs that cancel. Every
conclusion of `2026-10-06-fno-atlas.md` stands.

## Read with care

- One year of training. The rule's 12-month minimum (your decision of 19 Oct
  2026) is met, but a single year says little about how a strategy behaves in
  a crash; none happened in it.
- Fills at the next minute's open with half the spread; stops on each minute's
  high and low, worst case first. Margin is an uncalibrated SPAN-style
  estimate.
- 11 NIFTY and 24 BANKNIFTY days had no option minutes for the chosen expiry
  (Upstox has none for 26 Dec 2024 NIFTY and 1 Oct 2024 BANKNIFTY; BANKNIFTY's
  monthly is kept only for its final 20 sessions).
- A hand check of one real day (NIFTY, 20 Feb 2025) matched the minute files
  to the paisa: ATM 22,850, in at 75.62 / 59.90, target at 12:32 at 56.14 /
  11.96, ₹4,945 profit after ₹111 of charges.
