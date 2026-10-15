"""The F&O store in a private Cloudflare R2 bucket: push, pull, compare (design §2.2)."""

from __future__ import annotations

import hashlib
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import r2  # noqa: E402

ENV = {"R2_ACCOUNT_ID": "acct", "R2_ACCESS_KEY_ID": "kid", "R2_SECRET_ACCESS_KEY": "shh",
       "R2_BUCKET": "trading-tool-fno"}


class FakeS3:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.uploaded, self.downloaded = [], []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        s3 = self

        class Pages:
            def paginate(self, Bucket, Prefix):
                hits = [{"Key": k, "Size": len(v), "ETag": f'"{hashlib.md5(v).hexdigest()}"'}
                        for k, v in sorted(s3.objects.items()) if k.startswith(Prefix)]
                return [{"Contents": hits}] if hits else [{}]

        return Pages()

    def upload_file(self, Filename, Bucket, Key, Config=None):
        self.objects[Key] = Path(Filename).read_bytes()
        self.uploaded.append(Key)

    def download_file(self, Bucket, Key, Filename):
        Path(Filename).write_bytes(self.objects[Key])
        self.downloaded.append(Key)


def put(root: Path, rel: str, data: bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_config_from_env_names_what_is_missing_and_hides_the_secret():
    cfg = r2.R2Config.from_env(ENV)
    assert cfg.endpoint == "https://acct.r2.cloudflarestorage.com"
    assert "shh" not in repr(cfg) and "kid" not in repr(cfg)
    with pytest.raises(r2.R2ConfigError, match="R2_BUCKET, R2_SECRET_ACCESS_KEY"):
        r2.R2Config.from_env({"R2_ACCOUNT_ID": "a", "R2_ACCESS_KEY_ID": "k"})


def test_push_uploads_new_and_changed_files_only(tmp_path):
    put(tmp_path, "daily/_sessions.csv", b"same")
    put(tmp_path, "daily/NIFTY/2026.parquet", b"new contents")
    put(tmp_path, "vix/daily.parquet", b"brand new")
    put(tmp_path, "daily/NIFTY/2026.parquet.tmp", b"half written")
    s3 = FakeS3({"fno/daily/_sessions.csv": b"same", "fno/daily/NIFTY/2026.parquet": b"old"})
    assert r2.push(s3, "b", tmp_path) == ["daily/NIFTY/2026.parquet", "vix/daily.parquet"]
    assert s3.objects["fno/daily/NIFTY/2026.parquet"] == b"new contents"
    assert r2.push(s3, "b", tmp_path) == []


def test_pull_fetches_missing_and_changed_files_within_the_prefixes(tmp_path):
    put(tmp_path, "daily/_sessions.csv", b"stale")
    s3 = FakeS3({"fno/daily/_sessions.csv": b"fresh", "fno/vix/daily.parquet": b"v",
                 "fno/minute/NIFTY/2025-04-17.parquet": b"m"})
    got = r2.pull(s3, "b", tmp_path, ["daily/_sessions.csv", "vix/"])
    assert got == ["daily/_sessions.csv", "vix/daily.parquet"]
    assert (tmp_path / "daily/_sessions.csv").read_bytes() == b"fresh"
    assert not (tmp_path / "minute").exists()
    assert r2.pull(s3, "b", tmp_path, ["daily/_sessions.csv", "vix/"]) == []


def test_the_daily_job_reads_this_year_and_last_year_only_in_january_and_february():
    june = r2.daily_job_prefixes(date(2026, 6, 15))
    assert "daily/NIFTY/2026.parquet" in june and "daily/NIFTY/2025.parquet" not in june
    assert {"daily/_sessions.csv", "minute/_expiries.csv", "vix/", "spot/",
            "index_minute/INDIAVIX/2026.parquet"} <= set(june)
    assert "participants/2025.parquet" in r2.daily_job_prefixes(date(2026, 1, 5))
    assert "daily/BANKNIFTY/2025.parquet" in r2.daily_job_prefixes(date(2026, 2, 20))
    assert "daily/BANKNIFTY/2025.parquet" not in r2.daily_job_prefixes(date(2026, 3, 2))
