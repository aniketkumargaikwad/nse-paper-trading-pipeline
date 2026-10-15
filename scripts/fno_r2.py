"""Copy the F&O store to and from the private Cloudflare R2 bucket.

    python scripts/fno_r2.py push              # every local file R2 lacks or holds differently
    python scripts/fno_r2.py push --dry-run    # say what would go, send nothing
    python scripts/fno_r2.py pull              # everything in R2 that is missing or different here
    python scripts/fno_r2.py pull --daily-job  # only what the weekday job reads

Needs R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
(docs/FNO_SETUP.md). Never deletes anything on either side.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import IST, use_utf8_stdout  # noqa: E402
from fno import r2, store  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("direction", choices=("push", "pull"))
    parser.add_argument("--daily-job", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    config = r2.R2Config.from_env()
    s3 = r2.connect(config)
    root = store.root_dir()
    prefixes = r2.daily_job_prefixes(datetime.now(IST).date()) if args.daily_job else [""]
    if args.direction == "push":
        moved = r2.push(s3, config.bucket, root, prefixes, dry_run=args.dry_run)
        verb = "would upload" if args.dry_run else "uploaded"
    else:
        moved = r2.pull(s3, config.bucket, root, prefixes)
        verb = "downloaded"
    for rel in moved:
        print(f"  {rel}")
    print(f"{verb} {len(moved)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
