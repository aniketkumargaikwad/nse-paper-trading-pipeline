"""Market facts from one day's chain, and the daily table a strategy reads.

Every fact is computed from that day's snapshot and earlier data only
(design §2.4). Names here are the names a strategy writes (design §3).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from fno.chain import ChainSnapshot


def _nearest_delta(rows: pd.DataFrame, typ: str, target: float) -> pd.Series | None:
    side = rows[(rows["option_type"] == typ) & rows["delta"].notna()]
    if side.empty:
        return None
    return side.loc[(side["delta"] - target).abs().idxmin()]


def max_pain(rows: pd.DataFrame) -> float:
    strikes = np.sort(rows["strike"].unique())
    calls = rows[rows["option_type"] == "CE"]
    puts = rows[rows["option_type"] == "PE"]
    best, best_pay = float("nan"), float("inf")
    for s in strikes:
        pay = (np.maximum(s - calls["strike"], 0) * calls["oi_units"]).sum() \
            + (np.maximum(puts["strike"] - s, 0) * puts["oi_units"]).sum()
        if pay < best_pay:
            best, best_pay = float(s), pay
    return best


def from_snapshot(snap: ChainSnapshot) -> dict[str, Any]:
    rows = snap.rows
    out: dict[str, Any] = {"trade_date": snap.day, "expiry": snap.expiry, "dte": snap.dte,
                           "spot": snap.spot, "forward": snap.forward}
    strikes = rows["strike"].unique()
    atm = float(strikes[np.argmin(np.abs(strikes - snap.forward))]) if len(strikes) else np.nan
    at = rows[rows["strike"] == atm]
    out["atm_strike"] = atm
    out["atm_iv"] = float(at["iv"].mean()) if len(at) else np.nan
    out["straddle"] = float(at["settle"].sum()) if len(at) == 2 else np.nan
    out["expected_move_pct"] = 100 * out["straddle"] / snap.forward if snap.forward else np.nan
    calls, puts = rows[rows["option_type"] == "CE"], rows[rows["option_type"] == "PE"]
    out["pcr_oi"] = puts["oi_units"].sum() / calls["oi_units"].sum() if calls["oi_units"].sum() else np.nan
    out["pcr_vol"] = (puts["volume_contracts"].sum() / calls["volume_contracts"].sum()
                      if calls["volume_contracts"].sum() else np.nan)
    out["max_pain"] = max_pain(rows)
    p25, c25 = _nearest_delta(rows, "PE", -0.25), _nearest_delta(rows, "CE", 0.25)
    out["skew_25d"] = float(p25["iv"] - c25["iv"]) if p25 is not None and c25 is not None else np.nan
    out["max_oi_call_strike"] = float(calls.loc[calls["oi_units"].idxmax(), "strike"]) if len(calls) else np.nan
    out["max_oi_put_strike"] = float(puts.loc[puts["oi_units"].idxmax(), "strike"]) if len(puts) else np.nan
    out["basis_pct"] = 100 * (snap.forward / snap.spot - 1) if snap.spot == snap.spot and snap.spot else np.nan
    return out


def iv_rank(atm_iv: pd.Series, *, window: int = 252) -> tuple[pd.Series, pd.Series]:
    """IV rank (position in the window's range) and percentile (share of days below), in %."""
    lo = atm_iv.rolling(window, min_periods=20).min()
    hi = atm_iv.rolling(window, min_periods=20).max()
    rank = 100 * (atm_iv - lo) / (hi - lo)
    pctile = atm_iv.rolling(window, min_periods=20).apply(
        lambda w: 100 * (w[:-1] < w[-1]).sum() / (len(w) - 1), raw=True)
    return rank, pctile


def daily_table(facts: list[dict[str, Any]], *, vix: dict[date, float],
                events_on, fii_net: dict[date, float]) -> pd.DataFrame:
    """Facts per day plus VIX, IV rank, weekday, events and FII index-future positioning."""
    table = pd.DataFrame(facts).sort_values("trade_date").reset_index(drop=True)
    table["vix"] = table["trade_date"].map(vix)
    table["vix_chg_pct"] = 100 * table["vix"].pct_change()
    table["iv_rank"], table["iv_pctile"] = iv_rank(table["atm_iv"])
    table["weekday"] = [d.weekday() for d in table["trade_date"]]
    table["event_today"] = [bool(events_on(d)) for d in table["trade_date"]]
    table["fii_index_fut_net"] = table["trade_date"].map(fii_net)
    return table
