"""What the engine may read about one underlying, and when (design §2.5, §6)."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.market import training_window  # noqa: E402
from fno_synthetic import (  # noqa: E402
    D1, D2, D3, D4, D5, D6, DAYS, EXPIRY_SPOT, FORWARD, LOT, MONTHLY, facts, market, spot,
)
from strategy.expr import parse_expression  # noqa: E402


def test_sessions_and_the_next_one():
    m = market()
    assert m.sessions(D2, D4) == [D2, D3, D4]
    assert m.next_session(D4) == D5 and m.next_session(D6) is None


def test_a_day_s_rows_are_that_day_only():
    assert set(market().rows(D3)["trade_date"]) == {D3}


def test_until_cuts_everything_later_out():
    m = market(until=D3)
    assert m.sessions_all[-1] == D3
    assert m.rows(D4).empty
    assert D4 not in m.closes()


def test_final_settlement_the_expiring_future_then_the_index_close():
    m = market()
    assert m.final_settlement(MONTHLY, m.rows(D1)) == FORWARD       # a future expiring then
    assert m.final_settlement(D4, m.rows(D4)) == EXPIRY_SPOT         # a weekly: the index close
    bare = market(spot_frame=spot().iloc[:0])
    with pytest.raises(LookupError, match="no final settlement"):
        bare.final_settlement(D4, bare.rows(D4))


def test_lot_size_from_the_file_when_it_says_else_the_table():
    m = market()
    assert m.lot_size(D1, m.rows(D1)) == LOT
    assert m.lot_size(D1, m.rows(D1).assign(lot_size=75.0)) == 75


def test_days_to_the_strategy_s_own_expiry():
    m = market()
    weekly = m.expiry_dte("weekly", 0)
    assert (weekly[D1], weekly[D4]) == (3.0, 7.0)
    assert m.expiry_dte("monthly", 0)[D1] == 24.0


def test_sessions_to_event_counts_sessions_not_days():
    s = market(events=((D5, "rbi"),)).sessions_to_event()
    assert (s[D3], s[D4], s[D5]) == (2.0, 1.0, 0.0)
    assert np.isnan(s[D6])


def test_a_signal_reads_facts_candles_and_extra_facts():
    m = market(facts_frame=facts(vix=[12.0, 14.0, 16.0, 18.0, 20.0, 22.0]))
    on = m.signal(parse_expression("vix > 15 and close > 0 and expiry_dte < 5"),
                  {"expiry_dte": {d: 3.0 for d in DAYS}})
    assert [on[d] for d in DAYS] == [False, False, True, True, True, True]
    assert all(m.signal(None, {}).values())


def test_event_today_is_true_or_false_never_missing():
    m = market(facts_frame=facts(event_today=[False, True, False, False, False, False]))
    on = m.signal(parse_expression("not event_today"), {})
    assert [on[d] for d in DAYS] == [True, False, True, True, True, True]


def test_trend_from_the_fifty_day_average():
    assert market().trend(D1) == "unknown"
    days = pd.bdate_range(end=pd.Timestamp(D6), periods=90)
    rising = pd.DataFrame({"trade_date": [d.date() for d in days],
                           **{c: [100.0 * 1.01 ** i for i in range(90)] for c in ("open", "high", "low", "close")}})
    assert market(spot_frame=rising).trend(D6) == "rising"


def test_the_training_window_seals_the_last_365_days():
    sessions = [date(2016, 1, 1) + timedelta(days=i) for i in range(4000)]
    start, end = training_window(sessions)
    assert start == date(2016, 1, 1)
    assert end == sessions[-1] - timedelta(days=365)
