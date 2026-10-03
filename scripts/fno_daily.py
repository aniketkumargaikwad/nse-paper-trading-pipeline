"""Fill the local F&O store from NSE: daily files, participant OI, India VIX.

    python scripts/fno_daily.py                      # 2016-01-01 -> today, missing days only
    python scripts/fno_daily.py --from 2026-09-01    # a narrower range
    python scripts/fno_daily.py --skip-vix

Resumable: data/fno/daily/_sessions.csv records every day already fetched or
found to have no session, and a re-run asks NSE only for the rest. Stopped
by an error, it says which day; run it again to carry on.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from config import IST, use_utf8_stdout  # noqa: E402
from fno import nse_files, participants, quality, store, vix  # noqa: E402

START = date(2016, 1, 1)
LATE_DAYS = 3            # NSE publishes in the evening, sometimes days late
PAUSE_SECONDS = 0.4      # between days: be a polite client
RETRIES = 3


def plan_days(start: date, end: date, ledger: dict[date, str]) -> list[date]:
    """Every calendar day in [start, end] the ledger does not already settle."""
    out, day = [], start
    while day <= end:
        if day not in ledger:
            out.append(day)
        day += timedelta(days=1)
    return out


def settled(day: date, today: date) -> bool:
    """Whether a missing file for `day` is final rather than not-yet-published."""
    return (today - day).days > LATE_DAYS


def _retrying(fetch, day: date):
    for attempt in range(1, RETRIES + 1):
        try:
            return fetch()
        except (nse_files.NseFileError, participants.ParticipantFileError,
                requests.RequestException) as exc:
            if attempt == RETRIES:
                raise
            print(f"  {day}: {exc} - retrying in {10 * attempt}s", flush=True)
            time.sleep(10 * attempt)
    return None


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="start", type=date.fromisoformat, default=START)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, default=None)
    parser.add_argument("--skip-participants", action="store_true")
    parser.add_argument("--skip-vix", action="store_true")
    args = parser.parse_args(argv)

    root = store.root_dir()
    today = datetime.now(IST).date()
    end = args.end or today
    days = plan_days(args.start, end, store.load_ledger(root))
    print(f"F&O store {root}: {len(days)} day(s) to ask NSE for, {args.start} -> {end}", flush=True)

    session = requests.Session()
    sessions = rows = 0
    for i, day in enumerate(days, 1):
        try:
            text = _retrying(lambda: nse_files.download(day, session), day)
            if text is None:
                if settled(day, today):
                    store.record_session(root, day, "no_session", 0)
                continue
            frame = nse_files.parse(text, day)
            store.write_day(root, frame, day)
            quality.write_issues(root, quality.check_day(frame, day))
            if not args.skip_participants:
                ptext = _retrying(lambda: participants.download(day, session), day)
                if ptext is not None:
                    participants.write_day(root, participants.parse(ptext, day), day)
        except Exception as exc:                       # noqa: BLE001 - report and stop
            print(f"STOPPED at {day}: {exc}\nRun again to resume from there.", file=sys.stderr)
            return 1
        store.record_session(root, day, "ok", len(frame))
        sessions += 1
        rows += len(frame)
        if i % 25 == 0:
            print(f"  {day}: {sessions} session(s), {rows:,} rows so far", flush=True)
        time.sleep(PAUSE_SECONDS)

    if not args.skip_vix:
        frame = vix.fetch_daily()
        vix.write(root, frame)
        print(f"India VIX: {len(frame)} days, to {frame['trade_date'].iloc[-1]}")
    print(f"done: {sessions} session(s) stored, {rows:,} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
