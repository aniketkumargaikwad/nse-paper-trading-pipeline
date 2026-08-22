"""Re-run a stored backtest, exactly as it was run the first time.

WHY THIS EXISTS
---------------
The audit's finding was "no re-run of a saved config — config is not stored as
an entity". That was half right. A `backtest_runs` row already records the
window, the universe, the exact symbol list, the cost model and the strategy
VERSION. The config was there all along; nothing ever read it back.

WHAT "EXACTLY" MEANS, AND WHY EACH PART MATTERS
-----------------------------------------------
Three things are pinned, and each one would otherwise silently answer a
different question than the one being asked:

* **The strategy version, not the strategy.** Re-running from the current
  definition tests what the strategy says today. The moment it is edited that
  is a different strategy, and the comparison becomes meaningless while still
  producing two numbers that look comparable.

* **The symbol list, not the universe.** NIFTY50 is not the same fifty stocks
  it was six months ago. Re-resolving would test a different universe and
  attribute the difference to the strategy.

* **The window, not "the last two years".** `--years 2` means something
  different every day it is run, which is why two runs of "the same" backtest
  can honestly disagree.

WHAT A DIFFERENCE MEANS
-----------------------
If a re-run does not reproduce, the strategy is not what changed — it was
pinned. Something underneath moved: candles were backfilled or corrected, a
data-quality fix landed, or the engine itself changed. All three are worth
knowing, and none of them are visible any other way.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from config import IST, UTC


@dataclass(frozen=True)
class StoredConfig:
    """The config behind one stored batch, ready to run again."""

    batch_id: str
    strategy_name: str
    timeframe: str
    from_utc: datetime
    to_utc: datetime
    symbols: tuple[str, ...]
    universe_name: str | None
    strategy_version_id: int | None
    version_number: int | None
    definition: dict[str, Any]
    # The format the snapshot is IN, from the version row. Authoritative:
    # a v2 definition carries no `version` key at all, so sniffing the
    # document would rely on the absence of something.
    format_version: int
    cost_model: str | None
    recorded_net_pnl: float | None
    recorded_total_trades: int | None


class RerunError(RuntimeError):
    """A stored batch cannot be reproduced, and why."""


def _as_utc(value: Any, *, end_of_day: bool) -> datetime:
    """A stored date as a UTC datetime bounding the IST trading day.

    The rows store DATES. Reading them back as midnight UTC would shift the
    window by five and a half hours and quietly drop or add a session at each
    end, which is exactly the kind of one-bar difference a reproduction check
    is supposed to detect rather than create.
    """
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    text = str(value)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC)
    wall = time(23, 59, 59) if end_of_day else time(0, 0)
    return datetime.combine(parsed.date(), wall, tzinfo=IST).astimezone(UTC)


def load_config(store: Any, batch_id: str) -> list[StoredConfig]:
    """Every strategy config stored under one batch id."""
    rows = store.backtest_run_rows(batch_id)
    if not rows:
        raise RerunError(
            f"no backtest run found for batch {batch_id!r}. Check the id on "
            "the Backtest page, or in the CSV filename of the original run."
        )

    configs: list[StoredConfig] = []
    for row in rows:
        version_id = row.get("strategy_version_id")
        if version_id is None:
            raise RerunError(
                f"batch {batch_id!r} ({row.get('strategy_name')}) predates "
                "strategy versioning, so the rules it used were never "
                "snapshotted. It cannot be reproduced — only re-tested with "
                "whatever the strategy says now, which is a different question."
            )
        version = store.strategy_version_by_id(int(version_id))
        if version is None:
            raise RerunError(
                f"strategy version {version_id} is missing, so the rules "
                f"behind batch {batch_id!r} cannot be recovered."
            )

        symbols = tuple(row.get("symbols") or ())
        if not symbols:
            raise RerunError(
                f"batch {batch_id!r} recorded no symbol list, so what it was "
                "computed over cannot be recovered."
            )

        configs.append(StoredConfig(
            batch_id=batch_id,
            strategy_name=str(row["strategy_name"]),
            timeframe=str(row["timeframe"]),
            from_utc=_as_utc(row["start_date"], end_of_day=False),
            to_utc=_as_utc(row["end_date"], end_of_day=True),
            symbols=symbols,
            universe_name=row.get("universe_name"),
            strategy_version_id=int(version_id),
            version_number=version.get("version"),
            definition=dict(version["definition"]),
            format_version=int(version.get("format_version") or 2),
            cost_model=row.get("cost_model"),
            recorded_net_pnl=(
                float(row["net_pnl"]) if row.get("net_pnl") is not None else None
            ),
            recorded_total_trades=(
                int(row["total_trades"]) if row.get("total_trades") is not None else None
            ),
        ))
    return configs


def parse_strategy_for(config: StoredConfig):
    """The strategy as it was, from the snapshot rather than from today."""
    from strategy.parse import parse_strategy_dict
    from strategy.v3 import is_v3_document, parse_machine

    definition = dict(config.definition)
    if config.format_version >= 3 or is_v3_document(definition):
        return parse_machine(definition)
    # A v2 snapshot never stores a `version` key, but a hand-edited row might
    # carry one. Dropping it beats failing a reproduction on a key that says
    # nothing the format_version has not already said.
    definition.pop("version", None)
    return parse_strategy_dict(
        definition, where=f"strategy {config.strategy_name!r} "
                          f"version {config.version_number}"
    )


def compare(config: StoredConfig, run_row: dict[str, Any]) -> tuple[bool, str]:
    """Did the re-run reproduce the stored verdict?

    Compared on trade count and net P&L: a difference in either means the
    same rules over the same symbols and window produced a different answer,
    which is a fact about the DATA or the ENGINE, never about the strategy.
    """
    new_trades = int(run_row.get("total_trades") or 0)
    new_pnl = float(run_row.get("net_pnl") or 0.0)

    if config.recorded_total_trades is None or config.recorded_net_pnl is None:
        return True, "original run recorded no verdict to compare against"

    same_trades = new_trades == config.recorded_total_trades
    # Rupees, rounded: a sub-paisa float difference is not a finding.
    same_pnl = abs(new_pnl - config.recorded_net_pnl) < 0.01

    if same_trades and same_pnl:
        return True, (
            f"reproduced exactly — {new_trades} trades, "
            f"net {new_pnl:,.2f}"
        )

    parts = []
    if not same_trades:
        parts.append(
            f"trades {config.recorded_total_trades} -> {new_trades}"
        )
    if not same_pnl:
        parts.append(
            f"net {config.recorded_net_pnl:,.2f} -> {new_pnl:,.2f}"
        )
    return False, "; ".join(parts)


def describe(config: StoredConfig) -> str:
    """One line saying exactly what is about to be reproduced."""
    scope = (
        f"universe {config.universe_name}" if config.universe_name
        else f"{len(config.symbols)} instrument(s)"
    )
    return (
        f"{config.strategy_name} v{config.version_number} · "
        f"{config.timeframe} · {scope} pinned to {len(config.symbols)} symbol(s) · "
        f"{config.from_utc.astimezone(IST).date()} -> "
        f"{config.to_utc.astimezone(IST).date()}"
    )
