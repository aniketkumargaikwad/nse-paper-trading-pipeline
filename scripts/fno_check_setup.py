"""Check the owner's step-3 keys, one by one: works, or fails and why. Never prints a value.

    python scripts/fno_check_setup.py

Upstox: lists NIFTY expiries, then asks for one expired contract's minutes -
the second call is the real test, because whether the analytics token covers
expired contracts is the open question in docs/FNO_SETUP.md.
Dhan: one day of NIFTY minutes. R2: lists the bucket, writes and deletes a
small test file.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import IST, UTC, use_utf8_stdout  # noqa: E402
from fno import index_minutes, r2, upstox  # noqa: E402


def check_upstox() -> str:
    client = upstox.UpstoxClient(upstox.token_from_env())
    served = client.expiries("NIFTY")
    if not served:
        return "token accepted, but Upstox lists no expired NIFTY expiries"
    contracts = client.option_contracts("NIFTY", served[-1])
    middle = sorted(contracts, key=lambda c: c.strike)[len(contracts) // 2]
    candles = client.minute_candles(middle.key, served[-1], served[-1])
    return (f"works - {len(served)} NIFTY expiries served, {served[0]} -> {served[-1]}; "
            f"{len(candles)} minutes for one contract on {served[-1]}")


def check_dhan() -> str:
    provider = index_minutes.create_provider()
    end = datetime.now(UTC)
    frame = provider.fetch_minutes("NIFTY", end - timedelta(days=5), end)
    if frame.empty:
        return "token accepted, but no NIFTY minutes in the last 5 days"
    first = frame.index.min().tz_convert(IST)
    return f"works - {len(frame)} NIFTY minutes; the earliest reads {first:%Y-%m-%d %H:%M} IST"


def check_r2() -> str:
    config = r2.R2Config.from_env()
    s3 = r2.connect(config)
    held = len(r2.remote_objects(s3, config.bucket))
    key = r2.KEY_PREFIX + "_setup_check.txt"
    s3.put_object(Bucket=config.bucket, Key=key, Body=b"ok")
    s3.delete_object(Bucket=config.bucket, Key=key)
    return f"works - bucket {config.bucket} holds {held} F&O file(s); write and delete both allowed"


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    failed = 0
    for name, check in (("Upstox", check_upstox), ("Dhan", check_dhan), ("Cloudflare R2", check_r2)):
        try:
            print(f"{name}: {check()}")
        except Exception as exc:
            failed += 1
            # Exception TEXT only: every error here is written to name a fix, not a value.
            print(f"{name}: FAILS - {type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
