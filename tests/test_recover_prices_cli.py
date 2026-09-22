"""Tests for the recovery command's wiring, with a fake store and fake Yahoo.

This command runs once, against a deadline, on data that cannot be fetched
again afterwards. So the parts that are easy to get wrong and impossible to
notice - which window is asked for, what is read back to compare against,
whether a dry run really writes nothing - are pinned here rather than found
on the day.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import recover_prices  # noqa: E402
from recover_prices import recover_one, resolve_symbols, summarise, window_report  # noqa: E402
from price_recovery import RecoveryReport  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

NOW = datetime(2026, 9, 22, 4, 0, tzinfo=UTC)
HOLIDAYS = frozenset({date(2026, 8, 15), date(2026, 9, 14)})
STORE_ENDS = date(2026, 7, 31)


def frame(days, close_by_day, *, bars=6, at_ist=time(9, 15), step=5):
    parts = []
    for day in days:
        start = datetime.combine(day, at_ist, tzinfo=IST)
        index = pd.DatetimeIndex(
            [(start + timedelta(minutes=step * i)).astimezone(UTC)
             for i in range(bars)],
            name="ts",
        )
        c = close_by_day[day]
        parts.append(pd.DataFrame(
            {"open": np.full(bars, c), "high": np.full(bars, c * 1.01),
             "low": np.full(bars, c * 0.99), "close": np.full(bars, c),
             "volume": np.full(bars, 1000.0)},
            index=index,
        ))
    return pd.concat(parts).sort_index() if parts else pd.DataFrame()


class FakeBackend:
    """The candle store, in memory, recording what it was asked for."""

    def __init__(self, stored: pd.DataFrame) -> None:
        self._stored = stored
        self.reads: list[tuple[int, str, datetime, datetime]] = []
        self.writes: list[tuple[int, str, pd.DataFrame]] = []

    def instrument_id(self, symbol: str) -> int:
        return 1

    def read_candles(self, instrument_id, timeframe, from_utc, to_utc):
        self.reads.append((instrument_id, timeframe, from_utc, to_utc))
        if self._stored.empty:
            return None
        window = self._stored[
            (self._stored.index >= from_utc) & (self._stored.index <= to_utc)
        ]
        return window

    def write_candles(self, instrument_id, timeframe, df):
        self.writes.append((instrument_id, timeframe, df))


class FakeYahoo:
    """Yahoo, with its real 58-day wall and nothing else."""

    def __init__(self, served: pd.DataFrame, max_days: int = 58) -> None:
        self._served = served
        self._max_days = max_days
        self.requests: list[tuple[str, str, datetime, datetime]] = []

    def max_history_days(self, timeframe: str) -> int:
        return self._max_days

    def resolve_instrument_tokens(self, instruments, today_ist):
        return {i: f"{i.split(':')[1]}.NS" for i in instruments}

    def fetch_historical_candles(self, token, timeframe, from_utc, to_utc,
                                 *, closed_only=True, now_utc=None):
        self.requests.append((token, timeframe, from_utc, to_utc))
        now = now_utc or NOW
        # The real client silently clamps to its window; a fake that served
        # older candles would hide exactly the failure this guards.
        wall = now - timedelta(days=self._max_days)
        if self._served.empty:
            return self._served
        return self._served[
            (self._served.index >= max(from_utc, wall))
            & (self._served.index <= to_utc)
        ]


JULY = [date(2026, 7, 27), date(2026, 7, 28), date(2026, 7, 29),
        date(2026, 7, 30), date(2026, 7, 31)]
AUGUST = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]


def stored_and_served(store_close=103.0, yahoo_close=100.0):
    stored = frame(JULY, {d: store_close for d in JULY})
    served = frame(JULY + AUGUST, {d: yahoo_close for d in JULY + AUGUST})
    return stored, served


# ---------------------------------------------------------------------------
# The window report
# ---------------------------------------------------------------------------


def test_window_report_counts_every_missing_session_not_just_august():
    # The gap is not "August". Dhan lapsed on 11 September, so it runs from
    # 1 August to today, and a report that says "August" understates it.
    text = window_report(STORE_ENDS, NOW, HOLIDAYS, 58)
    assert "missing sessions        36" in text
    assert "still recoverable       36" in text
    assert "already unrecoverable   0" in text


def test_window_report_leads_with_the_date_the_overlap_runs_out():
    # The overlap is what makes the recovery checkable, and it is made of
    # sessions OLDER than the ones being recovered - so it expires first.
    text = window_report(STORE_ENDS, NOW, HOLIDAYS, 58)
    assert "2026-09-27" in text              # 31 Jul + 58 days
    assert "5 days from today" in text
    assert "nothing is left to check them against" in text


def test_window_report_names_the_oldest_missing_session_and_its_expiry():
    text = window_report(STORE_ENDS, NOW, HOLIDAYS, 58)
    assert "oldest missing session 2026-08-03 expires 2026-09-30" in text


def test_window_report_separates_what_is_already_gone():
    # Run the same report a month later: the early sessions are simply lost.
    later = NOW + timedelta(days=30)
    text = window_report(STORE_ENDS, later, HOLIDAYS, 58)
    assert "already unrecoverable   16" in text
    assert "NONE LEFT" in text


# ---------------------------------------------------------------------------
# One symbol, end to end
# ---------------------------------------------------------------------------


def test_recover_one_reads_back_far_enough_to_find_the_overlap():
    # Reading only the gap would leave no shared session at all, and every
    # symbol would be refused for want of a basis.
    stored, served = stored_and_served()
    backend, yahoo = FakeBackend(stored), FakeYahoo(served)
    recover_one("NSE:RELIANCE", "5m", backend, yahoo, "RELIANCE.NS", HOLIDAYS, NOW)

    _, _, read_from, _ = backend.reads[0]
    assert read_from <= NOW - timedelta(days=58)


def test_recover_one_puts_yahoos_prices_onto_the_stored_basis():
    stored, served = stored_and_served(store_close=103.0, yahoo_close=100.0)
    fresh, report = recover_one(
        "NSE:RELIANCE", "5m", FakeBackend(stored), FakeYahoo(served),
        "RELIANCE.NS", HOLIDAYS, NOW,
    )
    assert report.ok
    assert report.rebasing.factor == pytest.approx(1.03)
    assert fresh["close"].iloc[0] == pytest.approx(103.0)
    assert [ts.tz_convert(IST).date() for ts in fresh.index][:1] == [AUGUST[0]]


def test_recover_one_counts_the_sessions_yahoo_never_served():
    stored, _ = stored_and_served()
    served = frame(JULY + AUGUST[:1], {d: 100.0 for d in JULY + AUGUST[:1]})
    _, report = recover_one(
        "NSE:RELIANCE", "5m", FakeBackend(stored), FakeYahoo(served),
        "RELIANCE.NS", HOLIDAYS, NOW,
    )
    # Everything from 4 August to today is expected and absent - the point of
    # the report is that this is stated, not silently shorter.
    assert len(report.sessions_missing) == 35
    assert report.sessions_missing[0] == AUGUST[1]


def test_recover_one_refuses_a_symbol_with_nothing_stored_to_compare_against():
    # A newly added symbol has no overlap, so its basis is unknowable. It is
    # refused rather than written on Yahoo's basis alongside symbols on the
    # store's.
    served = frame(JULY + AUGUST, {d: 100.0 for d in JULY + AUGUST})
    fresh, report = recover_one(
        "NSE:NEWCO", "5m", FakeBackend(pd.DataFrame()), FakeYahoo(served),
        "NEWCO.NS", HOLIDAYS, NOW,
    )
    assert fresh.empty
    assert not report.ok


def test_recover_one_handles_the_window_having_closed_entirely():
    # Two months later Yahoo serves none of the overlap, so there is nothing
    # to measure and the command says so instead of writing something.
    stored, served = stored_and_served()
    much_later = NOW + timedelta(days=60)
    fresh, report = recover_one(
        "NSE:RELIANCE", "5m", FakeBackend(stored), FakeYahoo(served),
        "RELIANCE.NS", HOLIDAYS, much_later,
    )
    assert fresh.empty
    assert not report.ok


# ---------------------------------------------------------------------------
# The command's own decisions
# ---------------------------------------------------------------------------


def test_a_dry_run_writes_nothing(monkeypatch, capsys):
    stored, served = stored_and_served()
    backend, yahoo = FakeBackend(stored), FakeYahoo(served)
    _wire(monkeypatch, backend, yahoo)

    code = recover_prices.main(
        ["--symbols", "NSE:RELIANCE", "--timeframes", "5m", "--dry-run"]
    )
    assert code == 0
    assert backend.writes == []
    assert "nothing (dry run)" in capsys.readouterr().out


def test_a_real_run_writes_the_recovered_candles(monkeypatch, capsys):
    stored, served = stored_and_served()
    backend, yahoo = FakeBackend(stored), FakeYahoo(served)
    _wire(monkeypatch, backend, yahoo)

    code = recover_prices.main(["--symbols", "NSE:RELIANCE", "--timeframes", "5m"])
    assert code == 0
    assert len(backend.writes) == 1
    _, timeframe, written = backend.writes[0]
    assert timeframe == "5m"
    assert written["close"].iloc[0] == pytest.approx(103.0)
    # Writing locally is not the same as publishing, and the command says so.
    assert "backup_candles_to_storage.py" in capsys.readouterr().out


def test_a_refused_symbol_is_never_written_and_fails_the_run(monkeypatch, capsys):
    served = frame(JULY + AUGUST, {d: 100.0 for d in JULY + AUGUST})
    backend, yahoo = FakeBackend(pd.DataFrame()), FakeYahoo(served)
    _wire(monkeypatch, backend, yahoo)

    code = recover_prices.main(["--symbols", "NSE:RELIANCE", "--timeframes", "5m"])
    assert code == 1
    assert backend.writes == []
    assert "Refused" in capsys.readouterr().out


def test_the_command_refuses_a_timeframe_that_is_resampled_not_stored(monkeypatch, capsys):
    stored, served = stored_and_served()
    _wire(monkeypatch, FakeBackend(stored), FakeYahoo(served))
    code = recover_prices.main(["--symbols", "NSE:RELIANCE", "--timeframes", "15m"])
    assert code == 1
    assert "resampled from 5m" in capsys.readouterr().err


def test_the_window_flag_needs_no_credentials(monkeypatch, capsys):
    # "How long have I got" must be answerable on a laptop with nothing set
    # up, or it gets asked too late.
    monkeypatch.setattr(recover_prices, "load_holidays", lambda *a, **k: HOLIDAYS)
    monkeypatch.setattr(recover_prices, "covered_years", lambda *a, **k: frozenset({2026}))
    monkeypatch.setitem(
        sys.modules, "yfinance_client",
        type(sys)("yfinance_client"),
    )
    sys.modules["yfinance_client"].YFinanceMarketDataClient = lambda: FakeYahoo(pd.DataFrame())
    assert recover_prices.main(["--window"]) == 0
    assert "still recoverable" in capsys.readouterr().out


def test_resolve_symbols_rejects_a_malformed_one():
    args = type("A", (), {"symbols": "NSE:RELIANCE,not a symbol", "universe": "NIFTY200"})()
    with pytest.raises(ValueError):
        resolve_symbols(args)


def test_summarise_separates_recovered_from_refused(capsys):
    good = RecoveryReport(symbol="NSE:A", timeframe="5m", candles=75)
    bad = RecoveryReport(symbol="NSE:B", timeframe="5m")
    bad.problems.append("no overlap")
    assert summarise([good, bad], written=75, dry_run=False) == 1
    out = capsys.readouterr().out
    assert "recovered   1 of 2" in out
    assert "refused     1" in out


def _wire(monkeypatch, backend, yahoo):
    """Point the command at the fakes instead of Supabase and Yahoo."""
    monkeypatch.setattr(recover_prices, "load_holidays", lambda *a, **k: HOLIDAYS)
    monkeypatch.setattr(recover_prices, "covered_years", lambda *a, **k: frozenset({2026}))
    monkeypatch.setattr(recover_prices, "datetime", _FrozenClock)

    fake_yf = type(sys)("yfinance_client")
    fake_yf.YFinanceMarketDataClient = lambda: yahoo
    monkeypatch.setitem(sys.modules, "yfinance_client", fake_yf)

    fake_db = type(sys)("db")
    fake_db.SupabaseStore = type(
        "SupabaseStore", (), {"connect": staticmethod(lambda settings: type("S", (), {"_client": None})())}
    )
    monkeypatch.setitem(sys.modules, "db", fake_db)

    fake_factory = type(sys)("dhan_factory")
    fake_factory.create_candle_backend = lambda client: backend
    monkeypatch.setitem(sys.modules, "dhan_factory", fake_factory)

    monkeypatch.setattr(recover_prices, "get_settings", lambda: object())


class _FrozenClock(datetime):
    """`datetime` with now() pinned, so the run is reproducible."""

    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW


def test_recover_one_does_not_ask_yahoo_for_twenty_seven_years_of_daily():
    # Yahoo serves daily candles for 10,000 days. Taking the provider's own
    # reach as the context window fetched all of them, per symbol, to measure
    # an overlap ninety days covers many times over.
    stored = frame(JULY, {d: 100.0 for d in JULY}, bars=1, at_ist=time(15, 30))
    served = frame(JULY + AUGUST, {d: 100.0 for d in JULY + AUGUST},
                   bars=1, at_ist=time(0, 0))
    yahoo = FakeYahoo(served, max_days=10_000)
    recover_one("NSE:RELIANCE", "day", FakeBackend(stored), yahoo,
                "RELIANCE.NS", HOLIDAYS, NOW)

    _, _, asked_from, _ = yahoo.requests[0]
    assert asked_from >= NOW - timedelta(days=100)


def test_summarise_names_the_symbols_written_with_a_step_at_the_join(capsys):
    # Written, because the window to refetch them closes - but never buried
    # in the per-symbol output.
    warned = RecoveryReport(symbol="NSE:A", timeframe="5m", candles=75)
    warned.join_ratio = 0.5
    assert summarise([warned], written=75, dry_run=False) == 0
    out = capsys.readouterr().out
    assert "step at the join" in out
    assert "NSE:A" in out and "-50.0%" in out


def test_today_is_not_an_expected_daily_session_until_the_market_closes():
    # A daily candle does not exist until 15:30 IST, so listing today reports
    # "absent from the feed" every morning about a candle nobody could have.
    from recover_prices import last_expected_session

    morning = datetime(2026, 9, 22, 4, 0, tzinfo=UTC)        # 09:30 IST
    after_close = datetime(2026, 9, 22, 10, 30, tzinfo=UTC)  # 16:00 IST
    assert last_expected_session("day", morning) == date(2026, 9, 21)
    assert last_expected_session("day", after_close) == date(2026, 9, 22)
    # Intraday is the opposite case: a part-finished session is real data and
    # its shortness is worth saying.
    assert last_expected_session("5m", morning) == date(2026, 9, 22)


def test_daily_recovery_mid_morning_does_not_report_today_as_missing():
    stored = frame(JULY, {d: 100.0 for d in JULY}, bars=1, at_ist=time(15, 30))
    served = frame(JULY + AUGUST, {d: 100.0 for d in JULY + AUGUST},
                   bars=1, at_ist=time(0, 0))
    _, report = recover_one("NSE:RELIANCE", "day", FakeBackend(stored),
                            FakeYahoo(served, max_days=10_000), "RELIANCE.NS",
                            HOLIDAYS, NOW)
    assert date(2026, 9, 22) not in report.sessions_missing
    assert date(2026, 9, 21) in report.sessions_missing


def test_universe_symbols_come_back_already_qualified():
    # They arrive as 'NSE:SYMBOL' from parse_constituent_csv. Prefixing again
    # gave 'NSE:NSE:TATASTEEL' and refused all 200 symbols - invisible to the
    # --symbols path, which never reaches this branch.
    from instruments import SYMBOL_RE

    args = type("A", (), {"symbols": None, "universe": "NIFTY200"})()
    symbols = resolve_symbols(args)

    assert len(symbols) > 100
    assert all(SYMBOL_RE.match(s) for s in symbols)
    assert all(s.count(":") == 1 for s in symbols)
    assert "NSE:TATASTEEL" in symbols
