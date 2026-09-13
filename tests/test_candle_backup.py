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
