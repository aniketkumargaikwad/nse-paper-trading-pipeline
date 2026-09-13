# Research Loop — Piece 4: Going Hosted — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A research day runs at 06:00 IST on GitHub's machines with the laptop off, and arrives as a Telegram message with a link to a hosted dashboard — at no cost beyond the Claude Pro plan already paid for.

**Architecture:** Four new pieces and one decision. `candle_backup.py` holds the bucket-listing logic both the existing uploader and a new downloader share; `research/message.py` turns a stored run into text (pure, no network); `research/notify.py` sends that text to Telegram and optionally email; `.github/workflows/research.yml` wires prices, the day, the journal commit and the message together. The decision is making the repository public, which is what buys 4-CPU runners for free.

**Tech Stack:** GitHub Actions (ubuntu-latest, 4 CPUs, free for public repos), Supabase Storage (the candle backup, already populated), Telegram Bot API, Gmail SMTP, Streamlit Community Cloud, Python 3.11.

**Spec:** `docs/superpowers/specs/2026-09-11-autonomous-research-loop-design.md` §2.1, §4 steps 1–2 and 13–14, §7.2, §7.3, §7.4, §8 — build piece 4 of 4, the last one.

---

## Context for the implementer

**What already exists (pieces 1–3, on `feat/research-piece3`):**
- `python -m research.run_day --max-versions 3` runs a whole day end to end. Measured 2026-09-13: **40 minutes** on this laptop's 8 workers, six Opus calls, three sweeps of 500 s / 858 s / 605 s.
- `research/brain.py` calls `claude -p`. The exact flags are load-bearing and were measured against Claude Code **2.1.251**; read that module's docstring before touching the workflow's install step.
- `scripts/backup_candles_to_storage.py` uploads `data/candles` to the Supabase Storage bucket `candles`. **Verified 2026-09-13: 5,359 files, 663.4 MB, bucket matches local exactly.** There is no downloader — that is Task 1.
- `app_pages/research_page.py` renders the grid and the detail view, but never reads `research_versions`, so §7.2's version timeline is missing. That is Task 2.
- `run_day` already accepts `--commit-journal`, which commits `research/journal/YYYY-MM-DD.md` but does not push.

**Measured facts that must not be contradicted:**
- `data/candles` is 663.4 MB in 5,359 parquet files, laid out `{timeframe}/{instrument_id}/{year}.parquet`. Of that, **631 MB is `5m`** — every intraday timeframe is resampled from it — 42 MB is `day`, 1.4 MB is `60m` (the Yahoo index history) and 0.9 MB is `1m`, which research never reads.
- Supabase free tier: 1 GB Storage and **5 GB egress a month**. A full restore is 663 MB, so roughly **seven full restores a month** before it bills. The Actions cache must carry the normal day.
- GitHub Actions cache: 10 GB per repository, entries evicted after **7 days** unused.
- DATA_END is 2026-07-31; the locked year is 2025-08-01 → 2026-07-31.

**The trap that will silently ruin a run:** `config.DEFAULT_CANDLE_STORE` is `"supabase"`, not `"parquet"`. The laptop only reads the parquet files because `.env` sets `CANDLE_STORE=parquet`. GitHub Actions has no `.env`, so **the workflow must set `CANDLE_STORE=parquet` explicitly** or the runner will ignore the 663 MB it just downloaded and read candles from the database instead.

**The decision to confirm before Task 9:** free 4-CPU runners and 6-hour jobs require a **public repository**. Private repos get 2 CPUs and 2,000 minutes a month (~66 a day), which will not fit three versions. Public means the strategies, the research notes and Opus's reasoning are readable by anyone. Secrets are not exposed (they are unavailable to fork pull requests, and this workflow triggers only on `schedule` and `workflow_dispatch`). §10 of the spec says the owner confirms again before this happens — Task 9 is where that confirmation belongs.

**Environment:** Windows 11, Bash tool with Git Bash syntax, `./.venv/Scripts/python.exe` from the repo root. Tests are flat files in `tests/`, each inserting the repo root on `sys.path`. Branch off `feat/research-piece3`.

## File structure

| File | Status | Responsibility |
|---|---|---|
| `candle_backup.py` | create | bucket paths and listing, shared by upload and download |
| `scripts/backup_candles_to_storage.py` | modify | import the shared listing instead of defining it |
| `scripts/restore_candles_from_storage.py` | create | download what the local store is missing |
| `app_pages/research_page.py` | modify | the version timeline (§7.2 item 2) |
| `research/message.py` | create | a stored run as Telegram and email text; pure |
| `research/notify.py` | create | send it; one channel failing does not stop the other |
| `research/run_day.py` | modify | `--run-id-file` and `--fallback-file`, so a day survives a refused write |
| `.github/workflows/research.yml` | create | the whole morning |
| `docs/DEPLOYING.md` | modify | secrets, variables, Streamlit Cloud, going public |
| `tests/test_candle_backup.py` | create | |
| `tests/test_research_message.py` | create | |
| `tests/test_research_notify.py` | create | |
| `tests/test_research_page.py` | modify | the timeline |

---

### Task 1: Bring the candles down

The bucket has been a one-way backup. A runner starts with an empty `data/candles`, so it needs the other direction — resumable, because 5,359 files over a network will be interrupted, and because re-running must cost a listing rather than 663 MB.

**Files:**
- Create: `candle_backup.py`
- Modify: `scripts/backup_candles_to_storage.py`
- Create: `scripts/restore_candles_from_storage.py`
- Test: `tests/test_candle_backup.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_candle_backup.py`:

```python
"""Listing and comparing the candle store against its Supabase bucket."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from candle_backup import local_files, missing_locally, remote_files  # noqa: E402


class FakeBucket:
    """Storage lists one directory level at a time, 100 entries per page."""

    def __init__(self, tree):
        self.tree = tree            # prefix -> list of entry dicts
        self.listed = []

    def list(self, prefix, options=None):
        self.listed.append(prefix)
        return list(self.tree.get(prefix, []))


def a_file(name, size):
    return {"name": name, "metadata": {"size": size}}


def a_folder(name):
    return {"name": name, "metadata": None}


def test_local_files_are_keyed_by_their_bucket_path(tmp_path):
    year = tmp_path / "5m" / "10272"
    year.mkdir(parents=True)
    (year / "2019.parquet").write_bytes(b"x" * 11)
    (year / "notes.txt").write_bytes(b"ignored")
    assert local_files(str(tmp_path)) == {"5m/10272/2019.parquet": 11}


def test_remote_files_walks_the_three_levels_of_the_bucket():
    bucket = FakeBucket({
        "": [a_folder("5m")],
        "5m": [a_folder("10272")],
        "5m/10272": [a_file("2019.parquet", 11), a_file("2020.parquet", 22)],
    })
    assert remote_files(bucket) == {
        "5m/10272/2019.parquet": 11,
        "5m/10272/2020.parquet": 22,
    }


def test_a_file_present_at_the_same_size_is_not_downloaded_again():
    remote = {"5m/1/2019.parquet": 11, "5m/1/2020.parquet": 22}
    local = {"5m/1/2019.parquet": 11}
    assert missing_locally(remote, local) == ["5m/1/2020.parquet"]


def test_a_file_present_at_a_different_size_is_downloaded_again():
    remote = {"5m/1/2019.parquet": 99}
    local = {"5m/1/2019.parquet": 11}
    assert missing_locally(remote, local) == ["5m/1/2019.parquet"]


def test_only_the_wanted_timeframes_are_downloaded():
    """1m is 0.9 MB the research loop never reads, and egress is metered."""
    remote = {"5m/1/2019.parquet": 11, "1m/1/2019.parquet": 22}
    assert missing_locally(remote, {}, timeframes=("5m",)) == ["5m/1/2019.parquet"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_candle_backup.py -q -p no:cacheprovider`
Expected: `ModuleNotFoundError: No module named 'candle_backup'`.

- [ ] **Step 3: Create the shared module**

Create `candle_backup.py` at the repo root:

```python
"""Where the candle store lives on both sides: this disk, and the bucket.

The uploader and the downloader need the same three things - what is here,
what is there, and which paths differ - so they live here rather than in one
script that the other has to import across the `scripts/` boundary.

Sizes, not checksums: Storage does not return a content hash cheaply, and a
year file that changed content without changing length is not a thing that
happens - it grows as candles are added.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

LOCAL_ROOT = "data/candles"
BUCKET = "candles"

# What a research day actually reads. Every intraday timeframe is resampled
# from 5m, and `1m` is never touched - listing it costs nothing but
# downloading it spends metered egress on data no sweep will open.
RESEARCH_TIMEFRAMES: tuple[str, ...] = ("5m", "60m", "day")

# Supabase Storage returns at most 100 entries per list() call and gives no
# hint that it truncated. With 200 instrument directories under 5m/, an
# unpaged listing silently sees half of them. Paginate explicitly.
_PAGE = 1000


def local_files(root: str) -> dict[str, int]:
    """Every parquet file under `root`, keyed by its bucket path."""
    found: dict[str, int] = {}
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            if not name.endswith(".parquet"):
                continue
            full = os.path.join(dirpath, name)
            key = os.path.relpath(full, root).replace(os.sep, "/")
            found[key] = os.path.getsize(full)
    return found


def _list_all(bucket, prefix: str) -> list:
    """Every entry under `prefix`, following pages to the end."""
    entries: list = []
    offset = 0
    while True:
        try:
            page = bucket.list(prefix, {"limit": _PAGE, "offset": offset})
        except Exception:      # noqa: BLE001 - an absent prefix is normal
            return entries
        if not page:
            return entries
        entries.extend(page)
        if len(page) < _PAGE:
            return entries
        offset += len(page)


def remote_files(bucket) -> dict[str, int]:
    """Every object in the bucket, keyed by path, valued by size.

    Storage lists one directory level at a time, so this walks the same
    {timeframe}/{instrument_id}/{year}.parquet shape the writer produces.
    """
    found: dict[str, int] = {}

    def walk(prefix: str, depth: int) -> None:
        for entry in _list_all(bucket, prefix):
            name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", "")
            if not name:
                continue
            path = f"{prefix}/{name}" if prefix else name
            meta = entry.get("metadata") if isinstance(entry, dict) else None
            size = (meta or {}).get("size")
            if size is not None:
                found[path] = int(size)
            elif depth < 3:
                walk(path, depth + 1)

    walk("", 0)
    return found


def missing_locally(
    remote: Mapping[str, int],
    local: Mapping[str, int],
    *,
    timeframes: Sequence[str] | None = None,
) -> list[str]:
    """Bucket paths this disk does not have, or has at a different size."""
    wanted = tuple(timeframes) if timeframes is not None else None
    return sorted(
        path for path, size in remote.items()
        if (wanted is None or path.split("/", 1)[0] in wanted)
        and local.get(path) != size
    )
```

- [ ] **Step 4: Run to verify the tests pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_candle_backup.py -q -p no:cacheprovider`
Expected: 5 passed.

- [ ] **Step 5: Point the uploader at the shared module**

In `scripts/backup_candles_to_storage.py`, delete the `LOCAL_ROOT`, `BUCKET`, `_PAGE`, `local_files`, `_list_all` and `remote_files` definitions, and add this import beside the existing `from config import use_utf8_stdout` line:

```python
from candle_backup import BUCKET, LOCAL_ROOT, local_files, remote_files  # noqa: E402
```

Leave the rest of that file alone — the comment block above `_PAGE` moved to `candle_backup.py` with the code it explains.

- [ ] **Step 6: Verify the uploader still agrees with the bucket**

Run: `./.venv/Scripts/python.exe scripts/backup_candles_to_storage.py --verify`
Expected: `local: 5359 files, 663.4 MB`, `bucket: 5359 files, 663.4 MB`, and `VERIFIED: every local file is in the bucket at the same size.`

- [ ] **Step 7: Write the downloader**

Create `scripts/restore_candles_from_storage.py`:

```python
"""Bring the Parquet candle store down from Supabase Storage.

WHY
---
A GitHub runner starts with an empty disk. The research sweep reads nine
years of candles, so before anything can run they have to arrive - 663 MB of
them, from the bucket `scripts/backup_candles_to_storage.py` fills.

RESUMABLE, AND CHEAP TO RE-RUN
------------------------------
Every file already on disk at the same size is skipped, so a second run after
an interrupted first costs a listing rather than 663 MB. That matters more
here than for the uploader: Supabase's free tier meters egress at 5 GB a
month, which is about seven full restores. The Actions cache is what keeps a
normal day from spending any of it at all.

By default only the timeframes a research day reads are fetched (5m, 60m,
day) - `1m` exists in the bucket and no sweep ever opens it.

    python scripts/restore_candles_from_storage.py --dry-run
    python scripts/restore_candles_from_storage.py
    python scripts/restore_candles_from_storage.py --all-timeframes
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from candle_backup import (  # noqa: E402
    BUCKET,
    LOCAL_ROOT,
    RESEARCH_TIMEFRAMES,
    local_files,
    missing_locally,
    remote_files,
)
from config import use_utf8_stdout  # noqa: E402


def main() -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description="Download the candle store from Supabase Storage.")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be downloaded, write nothing")
    parser.add_argument("--all-timeframes", action="store_true",
                        help="include timeframes the research loop never reads")
    parser.add_argument("--root", default=LOCAL_ROOT)
    args = parser.parse_args()

    try:
        from config import get_settings
        from db import SupabaseStore

        client = SupabaseStore.connect(get_settings())._client
        bucket = client.storage.from_(BUCKET)
    except Exception as exc:      # noqa: BLE001
        print(f"SETUP PROBLEM: {exc}", file=sys.stderr)
        return 1

    print("listing the bucket (one request per directory, this takes a moment)...")
    remote = remote_files(bucket)
    if not remote:
        print(f"The bucket {BUCKET!r} is empty. Run "
              "scripts/backup_candles_to_storage.py from a machine that has the "
              "candles first.", file=sys.stderr)
        return 1

    local = local_files(args.root) if os.path.isdir(args.root) else {}
    timeframes = None if args.all_timeframes else RESEARCH_TIMEFRAMES
    todo = missing_locally(remote, local, timeframes=timeframes)

    print(f"bucket: {len(remote):>5} files, {sum(remote.values())/1024/1024:>7.1f} MB")
    print(f"local:  {len(local):>5} files, {sum(local.values())/1024/1024:>7.1f} MB")
    if not todo:
        print("\nNothing to download - the local store already matches.")
        return 0

    total = sum(remote[path] for path in todo)
    print(f"\ndownloading {len(todo)} file(s), {total/1024/1024:.1f} MB")
    if args.dry_run:
        for path in todo[:20]:
            print(f"  {path}")
        if len(todo) > 20:
            print(f"  ... and {len(todo) - 20} more")
        return 0

    done = 0
    for path in todo:
        destination = Path(args.root) / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.write_bytes(bucket.download(path))
        except Exception as exc:      # noqa: BLE001 - one file, not the run
            print(f"FAILED {path}: {exc}", file=sys.stderr)
            continue
        done += 1
        if done % 250 == 0:
            print(f"  {done}/{len(todo)}")

    print(f"\ndownloaded {done} of {len(todo)} file(s) into {args.root}")
    # A partial restore is a failure: a sweep on half a candle store produces
    # numbers that look real.
    return 0 if done == len(todo) else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 8: Prove the downloader works, without touching the real store**

Run:
```bash
./.venv/Scripts/python.exe scripts/restore_candles_from_storage.py --root /tmp/candle-restore-check --dry-run
```
Expected: the bucket listing, `local: 0 files`, and `downloading 5348 file(s), 662.5 MB` — 5,348 rather than 5,359 because the eleven `1m` files are excluded.

Then fetch a real slice and check it opens:
```bash
./.venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, '.')
from candle_backup import BUCKET, remote_files
from config import get_settings
from db import SupabaseStore
import pandas as pd, io
bucket = SupabaseStore.connect(get_settings())._client.storage.from_(BUCKET)
path = sorted(p for p in remote_files(bucket) if p.startswith('day/'))[0]
frame = pd.read_parquet(io.BytesIO(bucket.download(path)))
print(path, frame.shape, list(frame.columns))
"
```
Expected: a path like `day/10272/2015.parquet`, a non-zero row count, and OHLCV columns.

- [ ] **Step 9: Full suite, then commit**

Run: `./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`

```bash
git add candle_backup.py scripts/backup_candles_to_storage.py scripts/restore_candles_from_storage.py tests/test_candle_backup.py
git commit -m "feat(candles): bring the store down from the bucket, not just up

A runner starts with an empty disk, so the backup needed its other half.
Resumable by size like the uploader, and limited by default to the three
timeframes a research day reads - 1m is in the bucket and no sweep opens it,
and Supabase meters egress at about seven full restores a month.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: The version timeline the detail view promised

§7.2 item 2 asks for it and the page has never shown it — `research_versions` was unwritten when the page was built, and piece 3 started filling it. Without this, a hosted run's link opens a page that cannot say what the day tried.

**Files:**
- Modify: `app_pages/research_page.py`
- Test: `tests/test_research_page.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_research_page.py`:

```python
def test_version_rows_become_a_readable_timeline():
    rows = [
        {"idea_no": 1, "version_no": 1, "strategy_name": "R-A-v1", "valid": True,
         "change_note": "", "decision": "next_version", "lessons": "too few trades",
         "training_summary": {"combos_tested": 1177, "combos_profitable": 53,
                              "combos_beating_hold": 0}},
        {"idea_no": 1, "version_no": 2, "strategy_name": "R-A-v2", "valid": False,
         "change_note": "widened the stop", "decision": None, "error": "entry: unknown indicator",
         "training_summary": None},
    ]
    timeline = version_timeline(rows)
    assert timeline[0]["Version"] == "1.1"
    assert timeline[0]["Training"] == "53 of 1177 profitable, 0 beat holding"
    assert timeline[0]["Decision"] == "next_version"
    assert timeline[1]["Version"] == "1.2"
    assert timeline[1]["Training"] == "rejected: entry: unknown indicator"


def test_a_run_with_no_versions_has_no_timeline():
    assert version_timeline([]) == []
```

Change that file's import line to:

```python
from app_pages.research_page import (  # noqa: E402
    GRID_COLUMNS,
    grid_frame,
    verdict_label,
    version_timeline,
)
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_page.py -q -p no:cacheprovider`
Expected: `ImportError: cannot import name 'version_timeline'`.

- [ ] **Step 3: Implement the builder**

Add to `app_pages/research_page.py`, above `_detail`:

```python
def version_timeline(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One line per version the day tried, valid or not (design 7.2).

    A rejected version is still a result about the idea, so it keeps its row
    and says what the checker refused rather than showing an empty training
    line that would read as a flat outcome.
    """
    timeline = []
    for row in sorted(rows, key=lambda r: (r.get("idea_no") or 0, r.get("version_no") or 0)):
        summary = row.get("training_summary") or {}
        if row.get("valid") and summary:
            training = (
                f"{summary.get('combos_profitable', 0)} of "
                f"{summary.get('combos_tested', 0)} profitable, "
                f"{summary.get('combos_beating_hold', 0)} beat holding"
            )
        elif row.get("valid"):
            training = "tested, no summary stored"
        else:
            training = f"rejected: {(row.get('error') or 'no reason recorded').splitlines()[0]}"
        timeline.append({
            "Version": f"{row.get('idea_no')}.{row.get('version_no')}",
            "Strategy": row.get("strategy_name") or "—",
            "Changed": row.get("change_note") or "—",
            "Training": training,
            "Decision": row.get("decision") or "—",
            "Lessons": row.get("lessons") or "—",
        })
    return timeline
```

That file imports `Any` already; add the other two beside it:

```python
from collections.abc import Mapping, Sequence
```

- [ ] **Step 4: Run to verify the tests pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_page.py -q -p no:cacheprovider`

- [ ] **Step 5: Show it on the page**

In `_detail`, insert this immediately **before** the `Review` section. It uses the same `_children` helper the equity, combination and trade sections use, which reads children filtered BY RUN — never a whole table, which is what stopped older runs rendering once there were more than a few:

```python
    versions = _children(ctx.client, "research_versions", run_id, "id")
    timeline = version_timeline(versions.to_dict("records") if not versions.empty else [])
    if timeline:
        st.markdown("#### Versions tried")
        st.caption(
            "Every version the day tried, in order. Training numbers only — "
            "the locked year was opened once, after the last of them."
        )
        st.dataframe(pd.DataFrame(timeline), use_container_width=True, hide_index=True)
```

- [ ] **Step 6: Look at it**

Boot the dashboard and open Research, then select the 13 Sep 2026 row:
```bash
./.venv/Scripts/python.exe -m streamlit run dashboard.py --server.headless true --server.port 8501
```
Expected: a **Versions tried** table above the review with three rows — `1.1` prior-bar high breakout (`new_idea`), `2.1` Bollinger breakout (`new_idea`), `3.1` EMA20 reclaim (`stop`).

- [ ] **Step 7: Full suite, then commit**

```bash
git add app_pages/research_page.py tests/test_research_page.py
git commit -m "feat(research): show the versions a day tried

Design 7.2 asked for the timeline and the page never had one: the versions
table was unwritten when the page was built, and piece 3 started filling it.
A rejected version keeps its row and says what the checker refused.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The message, as text

Pure and testable: a stored run in, the words out. No network here, so every truncation rule and every "no pick today" case is checked without a bot token.

**Files:**
- Create: `research/message.py`
- Test: `tests/test_research_message.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_message.py`:

```python
"""What the morning message says, for every shape a run can take."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.message import TELEGRAM_LIMIT, rupees, telegram_text  # noqa: E402


def a_run(**overrides):
    run = {
        "started_at": "2026-09-13T00:30:00+00:00",
        "status": "completed",
        "data_end": date(2026, 7, 31),
        "locked_from": date(2025, 8, 1),
        "final_strategy_name": "R-20260913-ema20-reclaim-v1",
        "versions_tried": 3,
        "ideas_dropped": 2,
        "pick_symbol": "NSE:VMM",
        "pick_timeframe": "25m",
        "lakh_end_value": 78845.67,
        "hold_end_value": 75707.46,
        "locked_trades": 48,
        "win_rate_pct": 19.0,
        "worst_dip_pct": 21.1,
        "verdict_passed": False,
        "beat_holding": True,
        "ai_review": "Judge every result against hold_return_pct. Gross was negative.",
    }
    run.update(overrides)
    return run


def test_rupees_are_grouped_the_way_they_are_read_here():
    assert rupees(100000) == "₹1,00,000"
    assert rupees(78845.67) == "₹78,846"
    assert rupees(12345678) == "₹1,23,45,678"
    assert rupees(None) == "n/a"


def test_the_message_leads_with_the_two_numbers_that_matter():
    text = telegram_text(a_run(), title="EMA20 reclaim in an uptrend")
    assert "EMA20 reclaim in an uptrend" in text
    assert "₹78,846" in text and "₹75,707" in text
    assert "NSE:VMM" in text and "25m" in text


def test_the_verdict_says_both_halves():
    text = telegram_text(a_run(), title="t")
    assert "Failed" in text and "Beat holding" in text


def test_a_run_that_beat_nothing_says_so():
    text = telegram_text(a_run(verdict_passed=True, beat_holding=False), title="t")
    assert "Passed" in text and "Did not beat holding" in text


def test_a_day_with_no_pick_is_a_result_not_a_gap():
    text = telegram_text(
        a_run(pick_symbol=None, pick_timeframe=None, lakh_end_value=None,
              hold_end_value=None, locked_trades=None),
        title="t",
    )
    assert "No qualifying pick" in text
    assert "₹" not in text.split("No qualifying pick")[1].split("\n")[0]


def test_a_day_cut_short_says_why_at_the_top():
    text = telegram_text(a_run(status="stopped_limit"), title="t")
    assert "cut short" in text.lower()


def test_the_dashboard_link_is_included_when_there_is_one():
    assert "https://example.test/x" in telegram_text(
        a_run(), title="t", dashboard_url="https://example.test/x")
    assert "Details" not in telegram_text(a_run(), title="t")


def test_a_very_long_review_is_truncated_to_telegrams_limit():
    text = telegram_text(a_run(ai_review="x" * 8000), title="t")
    assert len(text) <= TELEGRAM_LIMIT
    assert text.endswith("…")
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_message.py -q -p no:cacheprovider`
Expected: `ModuleNotFoundError: No module named 'research.message'`.

- [ ] **Step 3: Implement**

Create `research/message.py`:

```python
"""One stored run, as the words that arrive on a phone (design 7.3).

Pure: a mapping in, a string out. No client, no clock, no network - so every
shape a day can take (no pick, cut short, a review longer than Telegram will
carry) is tested without a bot token.

This is the one place a locked-year number is ALLOWED to be formatted for a
human. The prompt builder never reads it; see design 2.4.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

TELEGRAM_LIMIT = 4096
# Why a day ended, in the words the owner would use.
_CUT_SHORT = {
    "stopped_limit": "cut short - the Claude allowance ran out",
    "stopped_time": "cut short - the time budget ran out",
    "failed": "cut short - the run failed",
}


def rupees(value: float | None) -> str:
    """Indian grouping: 1,00,000 rather than 100,000."""
    if value is None:
        return "n/a"
    whole = f"{int(round(value)):d}"
    sign, digits = ("-", whole[1:]) if whole.startswith("-") else ("", whole)
    if len(digits) <= 3:
        body = digits
    else:
        head, tail, parts = digits[:-3], digits[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        body = ",".join([*parts, tail])
    return f"{sign}₹{body}"


def _day(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    text = str(value or "")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%d %b %Y")
    except ValueError:
        return text


def telegram_text(
    run: Mapping[str, Any], *, title: str | None = None, dashboard_url: str | None = None
) -> str:
    """The morning message, inside Telegram's 4,096-character limit."""
    lines = [f"\U0001f4ca Research run — {_day(run.get('started_at'))}"]

    cut = _CUT_SHORT.get(str(run.get("status")))
    if cut:
        lines.append(f"⚠️ The day was {cut}. What it finished was still kept.")

    versions = run.get("versions_tried") or 0
    dropped = run.get("ideas_dropped") or 0
    lines.append(
        f"Idea: {title or run.get('final_strategy_name') or 'none'} "
        f"({versions} version(s) tried, {dropped} idea(s) dropped)"
    )

    if not run.get("pick_symbol"):
        lines.append(
            "No qualifying pick: nothing had 30+ training trades and a "
            "survivable worst dip, so the locked year was not opened."
        )
    else:
        lines.append(
            f"Best pick: {run['pick_symbol']} · {run.get('pick_timeframe')} "
            "(chosen on training years)"
        )
        lines.append(f"Locked year: {rupees(100000)} → {rupees(run.get('lakh_end_value'))}")
        lines.append(f"Just holding: {rupees(100000)} → {rupees(run.get('hold_end_value'))}")
        trades = run.get("locked_trades") or 0
        lines.append(
            f"Won {run.get('win_rate_pct') or 0:.0f}% of {trades} trades · "
            f"worst dip {run.get('worst_dip_pct') or 0:.1f}%"
        )
        passed = "✅ Passed" if run.get("verdict_passed") else "❌ Failed"
        beat = "Beat holding" if run.get("beat_holding") else "Did not beat holding"
        lines.append(f"Verdict: {passed} · {beat}")

    if run.get("ai_review"):
        lines.append(f"Why: {run['ai_review']}")
    if dashboard_url:
        lines.append(f"Details: {dashboard_url}")
    lines.append(
        f"⚠️ Prices frozen at {_day(run.get('data_end'))} — every day "
        "tests a new idea against the same window, not new data."
    )

    text = "\n".join(lines)
    if len(text) <= TELEGRAM_LIMIT:
        return text
    return text[: TELEGRAM_LIMIT - 1] + "…"
```

- [ ] **Step 4: Run to verify the tests pass, then the full suite**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_message.py -q -p no:cacheprovider`
Expected: 8 passed.

- [ ] **Step 5: Read one for real**

```bash
./.venv/Scripts/python.exe -c "
from config import get_settings
from db import SupabaseStore
from research.message import telegram_text
client = SupabaseStore.connect(get_settings())._client
run = client.table('research_runs').select('*').order('started_at', desc=True).limit(1).execute().data[0]
title = (client.table('strategies').select('title').eq('name', run['final_strategy_name']).execute().data or [{}])[0].get('title')
print(telegram_text(run, title=title, dashboard_url='https://example.test'))
"
```
Expected: the 13 Sep run as a readable message. Read it as the owner would — if a line needs explaining, fix the line.

- [ ] **Step 6: Commit**

```bash
git add research/message.py tests/test_research_message.py
git commit -m "feat(research): the morning message, as text

Pure, so every shape a day can take - no pick, cut short, a review longer
than Telegram carries - is tested without a bot token.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Sending it

Telegram is the channel that matters; email is the same text plus the version timeline. §8 requires one failing not to stop the other.

**Files:**
- Create: `research/notify.py`
- Test: `tests/test_research_notify.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_notify.py`:

```python
"""Sending the morning message, and surviving a channel that is down."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.notify import NotifyResult, send_all, send_telegram  # noqa: E402


class FakePost:
    def __init__(self, status=200, boom=None):
        self.status, self.boom, self.calls = status, boom, []

    def __call__(self, url, json=None, timeout=None):
        self.calls.append((url, json, timeout))
        if self.boom:
            raise self.boom
        return type("Response", (), {"status_code": self.status, "text": "ok"})()


def test_the_bot_token_stays_out_of_the_message_body():
    post = FakePost()
    send_telegram("SECRET-TOKEN", "42", "hello", post=post)
    url, body, _ = post.calls[0]
    assert "SECRET-TOKEN" in url            # the API puts it in the path
    assert "SECRET-TOKEN" not in str(body)
    assert body["chat_id"] == "42" and body["text"] == "hello"


def test_a_telegram_failure_is_reported_not_raised():
    post = FakePost(status=401)
    result = send_telegram("t", "42", "hello", post=post)
    assert result.sent is False and "401" in result.detail


def test_a_network_error_is_reported_not_raised():
    post = FakePost(boom=OSError("no route to host"))
    result = send_telegram("t", "42", "hello", post=post)
    assert result.sent is False and "no route to host" in result.detail


def test_email_failing_does_not_stop_telegram():
    post = FakePost()

    def broken_email(*_args, **_kwargs):
        raise OSError("smtp refused")

    results = send_all(
        "hello", "subject", "body",
        telegram=("t", "42"), email=("user@example.test", "pw", "to@example.test"),
        post=post, send_mail=broken_email,
    )
    assert results["telegram"].sent is True
    assert results["email"].sent is False and "smtp refused" in results["email"].detail


def test_a_channel_with_no_credentials_is_skipped_not_failed():
    results = send_all("hello", "s", "b", telegram=None, email=None, post=FakePost())
    assert results["telegram"] == NotifyResult(sent=False, detail="not configured")
    assert results["email"] == NotifyResult(sent=False, detail="not configured")


@pytest.mark.parametrize("status", [200, 201])
def test_any_success_status_counts(status):
    assert send_telegram("t", "42", "hi", post=FakePost(status=status)).sent is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_notify.py -q -p no:cacheprovider`
Expected: `ModuleNotFoundError: No module named 'research.notify'`.

- [ ] **Step 3: Implement**

Create `research/notify.py`:

```python
"""Send the morning message. A channel that is down loses its message, not the day.

    python -m research.notify --run-id-file run-id.txt
    python -m research.notify                       # the newest run

Read from the database rather than handed the numbers, so it can be re-run
by hand after a delivery failure without re-running the day.
"""

from __future__ import annotations

import argparse
import os
import smtplib
import sys
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import requests

TIMEOUT_SECONDS = 30
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"


@dataclass(frozen=True)
class NotifyResult:
    sent: bool
    detail: str


def send_telegram(
    token: str, chat_id: str, text: str, *, post: Callable[..., Any] = requests.post
) -> NotifyResult:
    """One POST. The token travels in the URL path, never in the body."""
    try:
        response = post(
            TELEGRAM_URL.format(token=token),
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=TIMEOUT_SECONDS,
        )
    except Exception as exc:        # noqa: BLE001 - a channel, not the day
        return NotifyResult(False, str(exc))
    status = getattr(response, "status_code", 0)
    if 200 <= status < 300:
        return NotifyResult(True, "sent")
    return NotifyResult(False, f"Telegram returned {status}: {getattr(response, 'text', '')[:200]}")


def send_email(
    user: str, password: str, to: str, subject: str, body: str,
    *, host: str = "smtp.gmail.com", port: int = 465,
) -> None:
    """Gmail SMTP with an app password. Raises; the caller decides what that means."""
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = user, to, subject
    message.set_content(body)
    with smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_SECONDS) as server:
        server.login(user, password)
        server.send_message(message)


def send_all(
    text: str,
    subject: str,
    body: str,
    *,
    telegram: tuple[str, str] | None,
    email: tuple[str, str, str] | None,
    post: Callable[..., Any] = requests.post,
    send_mail: Callable[..., Any] = send_email,
) -> dict[str, NotifyResult]:
    """Every configured channel, each one's failure kept to itself."""
    results = {"telegram": NotifyResult(False, "not configured"),
               "email": NotifyResult(False, "not configured")}
    if telegram:
        results["telegram"] = send_telegram(telegram[0], telegram[1], text, post=post)
    if email:
        try:
            send_mail(email[0], email[1], email[2], subject, body)
            results["email"] = NotifyResult(True, "sent")
        except Exception as exc:    # noqa: BLE001 - a channel, not the day
            results["email"] = NotifyResult(False, str(exc))
    return results


def _pair(*names: str) -> tuple[str, ...] | None:
    """Every one of these environment variables, or None if any is missing."""
    values = tuple((os.environ.get(name) or "").strip() for name in names)
    return values if all(values) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send the morning research message.")
    parser.add_argument("--run-id-file", default="",
                        help="a file holding the run id, written by run_day")
    parser.add_argument("--run-id", default="")
    args = parser.parse_args(argv)

    from config import get_settings, use_utf8_stdout
    from db import SupabaseStore
    from research.message import telegram_text

    use_utf8_stdout()
    run_id = args.run_id
    if not run_id and args.run_id_file and Path(args.run_id_file).exists():
        run_id = Path(args.run_id_file).read_text(encoding="utf-8").strip()

    client = SupabaseStore.connect(get_settings(require_supabase=True))._client
    query = client.table("research_runs").select("*")
    query = query.eq("id", run_id) if run_id else query.order("started_at", desc=True)
    rows = query.limit(1).execute().data or []
    if not rows:
        # The day crashed before it stored anything. That is exactly when a
        # message matters most, so send one saying so.
        text = ("\U0001f4ca Research run\n⚠️ The run finished without storing "
                "a result. Check the workflow log.")
        run = None
    else:
        run = rows[0]
        title = None
        if run.get("final_strategy_name"):
            found = (client.table("strategies").select("title")
                     .eq("name", run["final_strategy_name"]).execute().data or [])
            title = (found[0] if found else {}).get("title")
        text = telegram_text(run, title=title,
                             dashboard_url=(os.environ.get("DASHBOARD_URL") or "").strip() or None)

    warning = token_warning(os.environ.get("CLAUDE_TOKEN_CREATED", ""))
    if warning:
        text = f"{text}\n{warning}"

    results = send_all(
        text,
        subject=f"Research run — {(run or {}).get('started_at', '')}"[:120],
        body=text,
        telegram=_pair("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
        email=_pair("GMAIL_USER", "GMAIL_APP_PASSWORD", "NOTIFY_EMAIL"),
    )
    for channel, result in results.items():
        print(f"{channel:9s} {'sent' if result.sent else result.detail}")
    # A day that ran but could not be delivered is still a day. Only say
    # nothing worked when nothing worked.
    return 0 if any(r.sent for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

`token_warning` arrives in Task 6; until then, add this placeholder-free stub at the bottom of the module so the file imports cleanly:

```python
def token_warning(created: str) -> str:
    """Filled in by Task 6."""
    return ""
```

- [ ] **Step 4: Run to verify the tests pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_notify.py -q -p no:cacheprovider`
Expected: 7 passed.

- [ ] **Step 5: Create the Telegram bot and send one for real**

1. In Telegram, message `@BotFather`, send `/newbot`, follow the prompts, and copy the token it gives you.
2. Send any message to your new bot (a bot cannot start a conversation).
3. Get your chat id:
   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | python -c "import json,sys; print(json.load(sys.stdin)['result'][0]['message']['chat']['id'])"
   ```
4. Add both to `.env` as `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
5. Run: `./.venv/Scripts/python.exe -m research.notify`

Expected: `telegram  sent`, `email     not configured`, and the message about the 13 Sep run on your phone.

- [ ] **Step 6: Commit**

```bash
git add research/notify.py tests/test_research_notify.py
git commit -m "feat(research): send the morning message

Telegram, and email when it is configured. A channel that is down loses its
own message and nothing else; a run that stored nothing still sends, because
that is when a message matters most.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Name the run the workflow just made

`run_day` prints `saved as run <id>`, which a shell would have to scrape. A file is exact.

**Files:**
- Modify: `research/run_day.py`
- Test: `tests/test_research_run_day.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_run_day.py`:

```python
"""The day's wiring that can be checked without a sweep."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.run_day import write_run_id  # noqa: E402


def test_the_run_id_is_written_for_the_next_step(tmp_path):
    target = tmp_path / "deep" / "run-id.txt"
    write_run_id(str(target), "abc-123")
    assert target.read_text(encoding="utf-8") == "abc-123"


def test_no_path_means_no_file(tmp_path):
    write_run_id("", "abc-123")
    assert list(tmp_path.iterdir()) == []


def test_a_run_that_stored_nothing_writes_nothing(tmp_path):
    target = tmp_path / "run-id.txt"
    write_run_id(str(target), None)
    assert not target.exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_run_day.py -q -p no:cacheprovider`
Expected: `ImportError: cannot import name 'write_run_id'`.

- [ ] **Step 3: Implement**

In `research/run_day.py`, add beside `commit_journal`:

```python
def write_run_id(path: str, run_id: str | None) -> None:
    """Leave the run id where the next workflow step can read it.

    Scraping "saved as run <id>" out of stdout would work until a warning
    line moved.
    """
    if not path or not run_id:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(run_id), encoding="utf-8")
```

Add the argument beside `--commit-journal`:

```python
    parser.add_argument("--run-id-file", default="",
                        help="write the stored run's id here, for the next step")
```

Then call it at each of the four places that print `saved as run {saved}` — immediately before the print:

```python
        write_run_id(args.run_id_file, saved)
```

- [ ] **Step 4: Run to verify the tests pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_run_day.py -q -p no:cacheprovider`

- [ ] **Step 5: Prove the whole chain locally**

```bash
./.venv/Scripts/python.exe -m research.run_day --dry-run --max-stocks 5 --stock-timeframes day --workers 2 --run-id-file run-id.txt
cat run-id.txt
./.venv/Scripts/python.exe -m research.notify --run-id-file run-id.txt
rm run-id.txt
```
Expected: a run id in the file, and that run's message on your phone.

- [ ] **Step 6: Commit**

```bash
git add research/run_day.py tests/test_research_run_day.py
git commit -m "feat(research): write the run id for the next step

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Say when the Claude token is about to expire

§8's last row. A token that dies silently turns every morning into `stopped_limit` until somebody notices.

**Files:**
- Modify: `research/notify.py`
- Test: `tests/test_research_notify.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_research_notify.py`:

```python
from datetime import date  # noqa: E402 - added with the tests below

from research.notify import token_warning  # noqa: E402


def test_a_fresh_token_says_nothing():
    assert token_warning("2026-09-01", today=date(2026, 9, 13)) == ""


def test_a_token_inside_thirty_days_of_expiry_warns_with_the_date():
    warning = token_warning("2026-06-20", today=date(2026, 9, 13))
    assert "2026-09-18" in warning and "5 day" in warning


def test_an_expired_token_says_so_plainly():
    assert "expired" in token_warning("2026-01-01", today=date(2026, 9, 13)).lower()


def test_an_unset_or_unreadable_date_says_nothing():
    assert token_warning("", today=date(2026, 9, 13)) == ""
    assert token_warning("not a date", today=date(2026, 9, 13)) == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_notify.py -q -p no:cacheprovider`
Expected: failures on the four new tests — the stub takes no `today` argument and always returns `""`.

- [ ] **Step 3: Replace the stub**

In `research/notify.py`, replace the Task 4 stub with:

```python
# `claude setup-token` issues a credential good for 90 days. The repository
# variable CLAUDE_TOKEN_CREATED holds the day it was made, because nothing
# else knows: a dead token looks exactly like a usage limit from here.
TOKEN_LIFETIME_DAYS = 90
TOKEN_WARN_WITHIN_DAYS = 30


def token_warning(created: str, *, today: date | None = None) -> str:
    """A line for every message once the Claude token is nearly out of time."""
    try:
        made = date.fromisoformat((created or "").strip())
    except ValueError:
        return ""
    expires = made + timedelta(days=TOKEN_LIFETIME_DAYS)
    left = (expires - (today or date.today())).days
    if left < 0:
        return ("⚠️ The Claude token expired on "
                f"{expires}. Run `claude setup-token` and update the secret.")
    if left <= TOKEN_WARN_WITHIN_DAYS:
        return (f"⚠️ The Claude token expires on {expires} ({left} day(s)). "
                "Run `claude setup-token` and update the secret.")
    return ""
```

Add to that module's imports:

```python
from datetime import date, timedelta
```

- [ ] **Step 4: Run to verify the tests pass, then the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`

- [ ] **Step 5: Commit**

```bash
git add research/notify.py tests/test_research_notify.py
git commit -m "feat(research): warn before the Claude token expires

A dead token looks exactly like a usage limit from inside the run, so
without this every morning would quietly become stopped_limit.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Keep the day when Supabase refuses it

§8: a failed database write must not lose the run. Forty minutes of sweeping and six Opus calls are too expensive to throw away over a transient network error, so the day is written to a file the workflow keeps as an artifact.

**Files:**
- Modify: `research/run_day.py`
- Test: `tests/test_research_run_day.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_research_run_day.py`:

```python
import json  # noqa: E402 - added with the tests below
from datetime import date  # noqa: E402

from research.run_day import write_fallback  # noqa: E402


def test_a_refused_write_leaves_the_whole_day_on_disk(tmp_path):
    target = tmp_path / "out" / "research-fallback.json"
    payload = {"run": {"status": "completed"}, "versions": [{"idea_no": 1}], "notes": []}
    write_fallback(str(target), payload)
    assert json.loads(target.read_text(encoding="utf-8")) == payload


def test_dates_survive_the_dump(tmp_path):
    """A payload full of dates must not fail to serialise on the way out."""
    target = tmp_path / "research-fallback.json"
    write_fallback(str(target), {"run": {"data_end": date(2026, 7, 31)}})
    assert "2026-07-31" in target.read_text(encoding="utf-8")


def test_no_fallback_path_means_no_file(tmp_path):
    write_fallback("", {"run": {}})
    assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_run_day.py -q -p no:cacheprovider`
Expected: `ImportError: cannot import name 'write_fallback'`.

- [ ] **Step 3: Implement the writer**

In `research/run_day.py`, add beside `write_run_id`:

```python
def write_fallback(path: str, payload: Mapping[str, Any]) -> None:
    """The whole day as JSON, for when the database will not take it.

    Forty minutes of sweeping and six Opus calls are not worth losing to a
    transient network error. The workflow keeps this as an artifact, and the
    message says the run stored nothing (design 8).
    """
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(payload), indent=2, default=str), encoding="utf-8")
```

Add `import json` to that module's imports, and extend the existing `from collections.abc import Sequence` line to `from collections.abc import Mapping, Sequence`.

Add the argument beside `--run-id-file`:

```python
    parser.add_argument("--fallback-file", default="",
                        help="write the whole day here if the database refuses it")
```

- [ ] **Step 4: Build the payload before writing anything**

Inside `store_day`, the version rows are currently built inline in the `save_versions(...)` call. Lift them into a local first, so they exist before any network call:

```python
        version_rows = [
            {
                "idea_no": v.idea_no, "version_no": v.version_no,
                "strategy_name": (v.checked.document["name"] if v.checked else None),
                "strategy_version_id": version_ids.get(id(v)),
                "valid": v.valid, "error": v.error, "change_note": v.change_note,
                "why_failed": v.review.get("why_failed"),
                "why_worked": v.review.get("why_worked"),
                "lessons": v.review.get("lessons"),
                "decision": v.review.get("decision"),
                "training_summary": v.summary.as_dict() if v.summary is not None else None,
            }
            for v in outcome.versions
        ]
        payload = {
            "run": dict(run),
            "versions": version_rows,
            "notes": note_rows(_PENDING, day, entries),
            "combos": len(children.get("combos", [])),
        }
```

Then replace the three write calls and the return with one guarded block:

```python
        try:
            run_id = save_run(store._client, run=run, **children)
            save_versions(store._client, run_id, version_rows)
            save_notes(store._client, note_rows(run_id, day, entries))
        except ResearchStoreError:
            write_fallback(args.fallback_file, payload)
            raise
        return run_id
```

Delete the old `save_versions(...)`, `save_notes(...)` and `return run_id` lines that followed, so each write happens exactly once.

- [ ] **Step 5: Run to verify the tests pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_run_day.py -q -p no:cacheprovider`
Expected: 6 passed.

- [ ] **Step 6: Prove it against a database that refuses**

```bash
SUPABASE_SERVICE_ROLE_KEY=definitely-not-a-key ./.venv/Scripts/python.exe -m research.run_day --dry-run --max-stocks 5 --stock-timeframes day --workers 2 --fallback-file /tmp/research-fallback.json
```
Expected: the run reaches the locked year, then `WARNING: the day was NOT stored:` and exit code 1 — with the whole day sitting in `/tmp/research-fallback.json`, its `run` block carrying the locked-year numbers. Read the first twenty lines and check they are the day you just watched.

Then confirm a healthy run writes no fallback:
```bash
./.venv/Scripts/python.exe -m research.run_day --dry-run --max-stocks 5 --stock-timeframes day --workers 2 --fallback-file /tmp/should-not-exist.json
```
Expected: `saved as run <id>`, and `/tmp/should-not-exist.json` does not exist.

- [ ] **Step 7: Full suite, then commit**

```bash
git add research/run_day.py tests/test_research_run_day.py
git commit -m "feat(research): keep the day when the database refuses it

Design 8. Forty minutes of sweeping and six Opus calls are too expensive to
lose to a transient write error, so the whole day goes to a JSON file the
workflow keeps as an artifact.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: The morning, as a workflow

**Files:**
- Create: `.github/workflows/research.yml`

- [ ] **Step 1: Write it**

Create `.github/workflows/research.yml`:

```yaml
# One research day, on GitHub's machines, with the laptop off.
#
# Triggers are `schedule` and `workflow_dispatch` ONLY. Never add
# pull_request or pull_request_target: this repository is public, and those
# would let an outside contribution run with the secrets.
name: research

on:
  schedule:
    # 00:30 UTC = 06:00 IST. GitHub's cron is best-effort and is often 5-30
    # minutes late at busy times, which does not matter for research.
    - cron: "30 0 * * *"
  workflow_dispatch:
    inputs:
      max_versions:
        description: "How many versions the day may try"
        default: "7"

# One at a time. GitHub holds one running and one waiting; a third request
# replaces the waiting one.
concurrency:
  group: research
  cancel-in-progress: false

permissions:
  contents: write        # the journal note is committed and pushed

jobs:
  day:
    runs-on: ubuntu-latest
    timeout-minutes: 330      # under the 6-hour ceiling, over the 5-hour budget
    env:
      SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
      SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
      # WITHOUT THIS the default candle store is "supabase" and the run
      # ignores the 663 MB it just downloaded, reading candles from the
      # database instead. See config.DEFAULT_CANDLE_STORE.
      CANDLE_STORE: parquet
      CANDLE_ROOT: data/candles

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - run: pip install -r requirements.txt

      # Pinned: research/brain.py's flags were measured against this exact
      # version, and two of them do not behave as documented. Read that
      # module's docstring before bumping this.
      - name: Install Claude Code
        run: npm install -g @anthropic-ai/claude-code@2.1.251

      - name: Restore the candles from the cache
        uses: actions/cache@v4
        with:
          path: data/candles
          # DATA_END moves only when prices are topped up, so this key is
          # stable for months. Cache entries are evicted after 7 days unused.
          key: candles-${{ vars.DATA_END }}
          restore-keys: candles-

      # Always run: it only fetches what is missing, so a cache hit costs a
      # listing and a miss costs 663 MB of the 5 GB monthly egress.
      - name: Fill any gaps from Supabase Storage
        run: python scripts/restore_candles_from_storage.py

      - name: Run the day
        run: |
          python -m research.run_day \
            --max-versions "${{ inputs.max_versions || 7 }}" \
            --run-id-file run-id.txt \
            --fallback-file research-fallback.json \
            --commit-journal
        env:
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}

      # Also what stops GitHub disabling this schedule: a public repository's
      # scheduled workflows are turned off after 60 days without activity.
      # Only exists when the database refused the day. Keeping it means a
      # refused write costs a re-upload, not a re-run.
      - name: Keep the day if it could not be stored
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: research-fallback
          path: research-fallback.json
          if-no-files-found: ignore
          retention-days: 30

      - name: Push the journal note
        if: always()
        run: |
          git push || echo "nothing to push"

      - name: Send the message
        if: always()
        run: python -m research.notify --run-id-file run-id.txt
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          GMAIL_USER: ${{ secrets.GMAIL_USER }}
          GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
          NOTIFY_EMAIL: ${{ secrets.NOTIFY_EMAIL }}
          DASHBOARD_URL: ${{ vars.DASHBOARD_URL }}
          CLAUDE_TOKEN_CREATED: ${{ vars.CLAUDE_TOKEN_CREATED }}
```

`--commit-journal` needs an identity on the runner. In `research/run_day.py`'s `commit_journal`, before the `git add`, add:

```python
    # A runner has no git identity, and `git commit` refuses without one.
    for key, value in (("user.name", "research loop"),
                       ("user.email", "noreply@anthropic.com")):
        subprocess.run(["git", "config", key, value], check=False, capture_output=True)
```

- [ ] **Step 2: Add the secrets and variables**

On GitHub → Settings → Secrets and variables → Actions.

Secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and optionally `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `NOTIFY_EMAIL`.

Variables: `DATA_END` = `2026-07-31`, `DASHBOARD_URL` = the Streamlit URL from Task 9, `CLAUDE_TOKEN_CREATED` = the day `claude setup-token` was run, as `YYYY-MM-DD`.

Generate the Claude token with `claude setup-token` in a terminal, not the `.env` value — an interactive login cannot be refreshed on a runner.

- [ ] **Step 3: First run, deliberately small**

Push the branch, then from the Actions tab run the workflow with `max_versions` = **1**.

Expected, in order: pip install, Claude Code install, a cache miss, a ~663 MB download taking a few minutes, one sweep of roughly 20-25 minutes (4 CPUs against the laptop's 8), one propose and one review call, `saved as run <id>`, a pushed journal commit, and a Telegram message.

Watch for these three specifically:
- `prices frozen at 2026-07-31 (DATA_END, complete for 200 of 200 stocks)` — anything less means the download was partial.
- `testing 1213 combinations` (or 1200 when the idea reads volume) — a smaller number means `CANDLE_STORE` was wrong and it read the database.
- The message arriving even if the day itself failed.

- [ ] **Step 4: Second run, full size**

Run it again from the Actions tab with `max_versions` = 7, and confirm the cache hit makes the restore step finish in under a minute.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/research.yml research/run_day.py
git commit -m "feat(research): run the day on GitHub, at 06:00 IST

Schedule and manual dispatch only - never pull_request, which on a public
repository would hand the secrets to any contributor.

CANDLE_STORE=parquet is set explicitly: the default is supabase, and without
it the run would ignore the 663 MB it just downloaded.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: The dashboard, and going public

The last step, and the only one that cannot be undone. Do it deliberately.

**Files:**
- Modify: `docs/DEPLOYING.md`

- [ ] **Step 1: Check what would become public**

```bash
git log --all -p | grep -nE "SUPABASE_SERVICE_ROLE_KEY=eyJ|DHAN_API_SECRET=|TELEGRAM_BOT_TOKEN=[0-9]{6,}|sk-ant-" | head -20
```
Expected: only the documentation placeholders in `docs/` — the three `SUPABASE_SERVICE_ROLE_KEY=eyJh...` lines the spec already recorded. **Any real credential here means stop**: making the repository public exposes the whole history, not just the current files, and a rotated key is the only fix.

Then confirm `.env` was never committed:
```bash
git log --all --oneline -- .env | head
```
Expected: no output.

- [ ] **Step 2: Ask the owner**

§10 of the spec requires this confirmation, and it is a real decision, not a formality. Public means anyone can read the strategies, the journal notes and Opus's reasoning. Private costs roughly ₹0 but caps the day at about one version. Do not proceed without an explicit yes.

- [ ] **Step 3: Make it public**

GitHub → Settings → General → Danger Zone → Change repository visibility → Public.

Then check the schedule survived: Actions → research → the run history should still be there, and the "This workflow has no runs yet" banner should not appear.

- [ ] **Step 4: Host the dashboard**

1. share.streamlit.io → New app → this repository, branch `main`, main file `dashboard.py`.
2. Advanced settings → Secrets, in TOML:
   ```toml
   SUPABASE_URL = "https://<project>.supabase.co"
   SUPABASE_ANON_KEY = "<the anon key, NOT the service role key>"
   APP_PASSWORD = "<a password you choose>"
   CANDLE_STORE = "supabase"
   ```
   The **anon** key is deliberate: §7.4, and the research tables are anon-readable by `sql/011_research.sql`. The service-role key would give a public web page write access to everything.
3. Deploy, open the URL, confirm the password gate appears and the Research page lists the runs.
4. Put that URL in the `DASHBOARD_URL` repository variable so it reaches the messages.

- [ ] **Step 5: Write it down**

Add a "Daily research run" section to `docs/DEPLOYING.md` covering: the six secrets and three variables with what each is for; that `CLAUDE_CODE_OAUTH_TOKEN` comes from `claude setup-token` and lasts 90 days, with `CLAUDE_TOKEN_CREATED` tracking it; that the candles live in the Supabase bucket `candles` and are cached by `DATA_END`; that Supabase's free egress is about seven full restores a month; and that the daily journal commit is what keeps GitHub from disabling the schedule after 60 days.

- [ ] **Step 6: Commit**

```bash
git add docs/DEPLOYING.md
git commit -m "docs(deploy): how the daily research run is hosted

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Mark piece 4 built**

In `docs/superpowers/specs/2026-09-11-autonomous-research-loop-design.md` §10, append to item 4 a **BUILT** line in the style of items 1-3, recording the measured wall-clock time of the first scheduled run, how long the cached restore took against the uncached one, and whether the 4-CPU runner's sweep time matched the estimate of roughly twice the laptop's.

```bash
git add docs/superpowers/specs/2026-09-11-autonomous-research-loop-design.md
git commit -m "docs(spec): piece 4 built

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## What this plan does not do

- **Restore-from-cache correctness is trusted, not tested.** There is no way to exercise `actions/cache` locally; Task 8 Step 4 is the only check, and it is a human reading a log.
- **`research_runs.failed_step` is never written.** §8 wants a price failure recorded as `failed` / `prices`, but a day that dies before `run_day` starts has nothing to write a row with. What the owner actually gets is the workflow's red cross and a message saying the run stored nothing, which answers the same question. Writing the row would mean a second database path that only runs when the first one is broken.
- **The 5 GB egress ceiling is not enforced.** Seven cache misses in a month would exhaust it, and the failure would show as a download error, not a warning. If the cache turns out to miss often, the fix is a second cache key on the workflow file's own hash — but measure before building it.
- **No retry on a failed day.** §8 treats a cut-short day as a result, and the message says which. A second attempt would spend allowance the next morning needs.
