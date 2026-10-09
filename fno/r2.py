"""The F&O store in Cloudflare R2: publish local files, fetch them back (design §2.2).

The bucket is PRIVATE - broker data is licensed for the owner's own use and
the repository is public - and mirrors data/fno exactly under the key prefix
`fno/`. Files are compared by size and MD5: R2's ETag for a single-part upload
is the MD5 of the bytes, and every upload here is single-part (the threshold
is set far above any file this store writes). Push never deletes anything.

Credentials come from the environment (docs/FNO_SETUP.md 3c and 4) and are
never printed: R2Config's repr hides them.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from fno.contracts import INDEX_UNDERLYINGS
from fno.index_minutes import INDEXES

KEY_PREFIX = "fno/"
SINGLE_PART_UP_TO = 512 * 1024 * 1024
ENV_NAMES = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")


class R2ConfigError(RuntimeError):
    """An R2 setting is missing."""


@dataclass(frozen=True)
class R2Config:
    account_id: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    bucket: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "R2Config":
        source = os.environ if env is None else env
        values = {name: source.get(name, "").strip() for name in ENV_NAMES}
        missing = sorted(name for name, value in values.items() if not value)
        if missing:
            raise R2ConfigError(f"missing {', '.join(missing)}: see docs/FNO_SETUP.md sections 3 and 4")
        return cls(*(values[name] for name in ENV_NAMES))

    @property
    def endpoint(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


def connect(config: R2Config) -> Any:
    import boto3

    return boto3.client("s3", endpoint_url=config.endpoint, region_name="auto",
                        aws_access_key_id=config.access_key_id,
                        aws_secret_access_key=config.secret_access_key)


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _wanted(rel: str, prefixes: Iterable[str]) -> bool:
    return any(rel.startswith(p) for p in prefixes)


def local_files(root: Path, prefixes: Iterable[str] = ("",)) -> dict[str, Path]:
    root = Path(root)
    prefixes = list(prefixes)
    return {p.relative_to(root).as_posix(): p for p in sorted(root.rglob("*"))
            if p.is_file() and not p.name.endswith(".tmp")
            and _wanted(p.relative_to(root).as_posix(), prefixes)}


def remote_objects(s3: Any, bucket: str, prefixes: Iterable[str] = ("",)) -> dict[str, tuple[int, str]]:
    out: dict[str, tuple[int, str]] = {}
    for prefix in prefixes:
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=KEY_PREFIX + prefix):
            for obj in page.get("Contents", []):
                out[obj["Key"][len(KEY_PREFIX):]] = (int(obj["Size"]), obj["ETag"].strip('"'))
    return out


def _same(path: Path, remote: tuple[int, str] | None) -> bool:
    return remote is not None and path.stat().st_size == remote[0] and _md5(path) == remote[1]


def push(s3: Any, bucket: str, root: Path, prefixes: Iterable[str] = ("",), *,
         dry_run: bool = False) -> list[str]:
    """Upload every local file that R2 lacks or holds differently; the paths uploaded."""
    from boto3.s3.transfer import TransferConfig

    prefixes = list(prefixes)
    remote = remote_objects(s3, bucket, prefixes)
    sent = []
    for rel, path in local_files(root, prefixes).items():
        if _same(path, remote.get(rel)):
            continue
        if not dry_run:
            s3.upload_file(str(path), bucket, KEY_PREFIX + rel,
                           Config=TransferConfig(multipart_threshold=SINGLE_PART_UP_TO))
        sent.append(rel)
    return sent


def pull(s3: Any, bucket: str, root: Path, prefixes: Iterable[str] = ("",)) -> list[str]:
    """Download every object under `prefixes` that is missing or different locally."""
    got = []
    for rel, remote in sorted(remote_objects(s3, bucket, list(prefixes)).items()):
        path = Path(root) / rel
        if path.exists() and _same(path, remote):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        s3.download_file(bucket, KEY_PREFIX + rel, str(tmp))
        os.replace(tmp, path)
        got.append(rel)
    return got


def daily_job_prefixes(today: date) -> list[str]:
    """What the weekday job reads and appends to: this year's files (and last year's
    in January and February - a monthly expiry's 20-session window and its 45-day
    check can reach back into December), the ledgers, VIX and the index closes -
    not the whole 3 GB store."""
    years = [today.year - 1, today.year] if today.month <= 2 else [today.year]
    out = ["daily/_sessions.csv", "minute/_expiries.csv", "vix/", "spot/"]
    for y in years:
        out += [f"daily/{u}/{y}.parquet" for u in INDEX_UNDERLYINGS]
        out += [f"participants/{y}.parquet", f"quality/{y}.csv"]
        out += [f"index_minute/{name}/{y}.parquet" for name in INDEXES]
    return out
