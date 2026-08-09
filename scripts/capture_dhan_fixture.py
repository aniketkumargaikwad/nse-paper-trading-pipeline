"""One-off: capture a real Dhan intraday response as a test fixture.

The parser is written AGAINST this file rather than against an assumption,
because a misread timestamp would shift every candle by 5h30m and silently
corrupt every backtest.

Run once with Dhan credentials configured:
    .venv\\Scripts\\python.exe scripts\\capture_dhan_fixture.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from dotenv import load_dotenv

from dhan_auth import DHAN_API_BASE, DhanCredentials, DhanTokenManager, StoredToken

load_dotenv()


class MemoryStore:
    """Token store that lives only for this script run."""

    def __init__(self) -> None:
        self.token: StoredToken | None = None

    def get_token(self, provider: str) -> StoredToken | None:
        return self.token

    def save_token(self, provider: str, token: StoredToken) -> None:
        self.token = token


def main() -> int:
    creds = DhanCredentials.from_env()
    token = DhanTokenManager(creds, MemoryStore()).get_access_token()

    to_date = datetime.now()
    from_date = to_date - timedelta(days=5)
    response = requests.post(
        f"{DHAN_API_BASE}/charts/intraday",
        headers={"access-token": token, "client-id": creds.client_id,
                 "Content-Type": "application/json"},
        json={
            "securityId": "2885",           # RELIANCE
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "interval": "5",
            "fromDate": from_date.strftime("%Y-%m-%d"),
            "toDate": to_date.strftime("%Y-%m-%d"),
        },
        timeout=30,
    )
    print("HTTP", response.status_code)
    payload = response.json()
    print("keys:", list(payload)[:20])

    # Decode the first few timestamps BOTH ways so the correct reading is
    # obvious: the NSE session runs 09:15-15:30 IST.
    stamps = payload.get("timestamp") or []
    for raw in stamps[:3]:
        as_utc = datetime.utcfromtimestamp(raw)
        print(f"  raw={raw}  as-UTC={as_utc}  as-UTC+5:30={as_utc + timedelta(hours=5, minutes=30)}")

    out = Path("tests/fixtures/dhan_intraday_5m.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    trimmed = {k: (v[:10] if isinstance(v, list) else v) for k, v in payload.items()}
    out.write_text(json.dumps(trimmed, indent=2), encoding="utf-8")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
