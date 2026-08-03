"""Morning Kite login — run this ONCE each trading morning, on your machine.

SEBI mandates that Kite API sessions expire daily, so this step cannot be
automated away: it completes Zerodha's interactive 2FA login in your browser
and stores the day's access token in Supabase, where the cloud paper engine
picks it up. Takes under a minute once you've done it twice.

HOW TO RUN
----------
Windows (PowerShell, from the project folder):
    .venv\\Scripts\\python.exe login.py

Mac/Linux (from the project folder):
    .venv/bin/python login.py

To only CHECK whether today's token exists and still works:
    python login.py --check

WHAT IT DOES, STEP BY STEP
--------------------------
1. Opens the Zerodha login page in your browser (URL also printed, in case
   the browser doesn't open).
2. You log in with your Zerodha credentials + 2FA (TOTP/app code).
3. Zerodha redirects to your app's registered redirect URL. THE PAGE MAY
   FAIL TO LOAD — that is completely fine. Only the address bar matters:
   it contains `request_token=...`.
4. You paste the full redirected URL (or just the token) back here.
5. The script exchanges it for the day's access token, verifies it with a
   read-only profile call, and stores it in Supabase.

SECURITY NOTES
--------------
* Your Zerodha password/2FA are typed ONLY into Zerodha's own website —
  never into this script.
* The access token is never printed or logged; it goes straight to the
  daily_token table (which the dashboard's key cannot read).
* This system is paper-trading only, but treat the token like a password
  anyway: it grants API access to your account.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from kiteconnect import KiteConnect
from kiteconnect.exceptions import KiteException, TokenException

from config import IST, UTC, get_settings
from db import DatabaseError, SupabaseStore

# Zerodha's interactive login endpoint (Kite Connect v3).
LOGIN_URL_TEMPLATE = "https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"

MAX_PASTE_ATTEMPTS = 3


def extract_request_token(pasted: str) -> str:
    """Pull the request_token out of whatever the user pasted.

    Accepts the full redirect URL (the normal case), any URL containing a
    request_token query parameter, or the bare token itself.

    Raises:
        ValueError: with a beginner-friendly explanation of what to paste.
    """
    text = pasted.strip().strip('"').strip("'")
    if not text:
        raise ValueError(
            "Nothing was pasted. Copy the FULL address from your browser's "
            "address bar after logging in (it contains request_token=...)."
        )

    if "request_token=" in text:
        query = urlparse(text).query or text.split("?", 1)[-1]
        tokens = parse_qs(query).get("request_token", [])
        if tokens and tokens[0].strip():
            return tokens[0].strip()
        raise ValueError(
            "The pasted URL mentions request_token but no value could be "
            "read from it. Paste the complete URL, not a fragment."
        )

    # Bare token: Kite request tokens are short opaque strings. Reject
    # anything that is clearly not one (spaces, a URL without the param).
    if "/" in text or " " in text or "=" in text:
        raise ValueError(
            "That doesn't look like a redirect URL with request_token=... "
            "nor a bare token. After logging in, copy the ENTIRE address "
            "from the address bar and paste it here."
        )
    return text


def check_today(store: SupabaseStore, api_key: str) -> int:
    """--check mode: does today's stored token exist and still work?"""
    today_ist = datetime.now(tz=UTC).astimezone(IST).date()
    token = store.get_daily_token(today_ist)
    if not token:
        print(f"NO TOKEN stored for {today_ist.isoformat()}. Run: python login.py")
        return 1
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(token)
    try:
        profile = kite.profile()  # read-only verification call
    except TokenException:
        print(
            f"Token for {today_ist.isoformat()} is stored but REJECTED by "
            "Kite (expired or invalidated by a login elsewhere). "
            "Run: python login.py"
        )
        return 1
    except KiteException as exc:
        print(f"Could not verify the token (Kite error): {exc}", file=sys.stderr)
        return 1
    print(
        f"OK: token for {today_ist.isoformat()} is valid "
        f"(account: {profile.get('user_name', 'unknown')})."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily Kite login (paper pipeline).")
    parser.add_argument(
        "--check", action="store_true",
        help="only verify today's stored token; don't log in",
    )
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except RuntimeError as exc:
        print(f"SETUP PROBLEM: {exc}", file=sys.stderr)
        return 1

    # On the free provider there is no daily session to create — say so
    # plainly instead of walking the user through a login they don't need.
    if not settings.requires_daily_login:
        print(
            f"Nothing to do: DATA_PROVIDER={settings.data_provider!r} needs no "
            "daily login.\n"
            "This step only exists for the paid Kite Connect provider "
            "(DATA_PROVIDER=kite).\n"
            "You can skip login.py entirely — the scheduled engine runs on "
            "its own."
        )
        return 0

    try:
        store = SupabaseStore.connect(settings)
    except (RuntimeError, DatabaseError) as exc:
        print(f"SETUP PROBLEM: {exc}", file=sys.stderr)
        return 1

    if args.check:
        return check_today(store, settings.kite_api_key)

    today_ist = datetime.now(tz=UTC).astimezone(IST).date()
    login_url = LOGIN_URL_TEMPLATE.format(api_key=settings.kite_api_key)

    print("=" * 72)
    print(f"KITE MORNING LOGIN — {today_ist.isoformat()} (IST)")
    print("=" * 72)
    print(
        "\nStep 1: A browser window will open with Zerodha's login page."
        "\n        Log in with your Zerodha credentials and 2FA."
        "\n\nStep 2: After login, Zerodha redirects your browser. The page may"
        "\n        show an error — THAT IS NORMAL. Look at the ADDRESS BAR:"
        "\n        it contains 'request_token=...'."
        "\n\nStep 3: Copy the ENTIRE address and paste it below."
        f"\n\nIf the browser did not open, visit this URL yourself:\n  {login_url}\n"
    )
    try:
        webbrowser.open(login_url)
    except Exception:
        pass  # URL is printed above; a headless/odd environment is fine

    kite = KiteConnect(api_key=settings.kite_api_key)

    for attempt in range(1, MAX_PASTE_ATTEMPTS + 1):
        try:
            pasted = input("Paste the redirected URL (or request_token): ")
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled. No token was stored.")
            return 1

        try:
            request_token = extract_request_token(pasted)
        except ValueError as exc:
            print(f"\n  Problem: {exc}\n")
            continue

        try:
            # Exchanges the one-time request token for the day's access token.
            session = kite.generate_session(request_token, api_secret=settings.kite_api_secret)
        except TokenException as exc:
            print(
                f"\n  Kite rejected the token: {exc}"
                "\n  Request tokens are SINGLE-USE and expire in a few minutes."
                "\n  Log in again in the browser to get a fresh one, then paste"
                "\n  the new URL here.\n"
            )
            if attempt < MAX_PASTE_ATTEMPTS:
                try:
                    webbrowser.open(login_url)
                except Exception:
                    pass
            continue
        except KiteException as exc:
            print(
                f"\nFAILED to exchange the token: {exc}"
                "\nIf this mentions 'api_key' or 'checksum', re-check "
                "KITE_API_KEY / KITE_API_SECRET in your .env file.",
                file=sys.stderr,
            )
            return 1

        access_token = session.get("access_token", "")
        if not access_token:
            print("FAILED: Kite returned no access token. Try again.", file=sys.stderr)
            return 1

        # Verify with a harmless read-only call BEFORE storing, so a broken
        # token can never poison the day's runs.
        kite.set_access_token(access_token)
        try:
            profile = kite.profile()
        except KiteException as exc:
            print(f"FAILED: token verification call errored: {exc}", file=sys.stderr)
            return 1

        try:
            store.save_daily_token(today_ist, access_token)
        except DatabaseError as exc:
            print(f"FAILED to store the token in Supabase: {exc}", file=sys.stderr)
            return 1

        print(
            f"\nSUCCESS — logged in as {profile.get('user_name', 'unknown')}."
            f"\nToken for {today_ist.isoformat()} stored in Supabase."
            "\nThe paper engine's scheduled runs can now fetch market data all day."
            "\n(The token expires tonight; run this script again tomorrow morning.)"
        )
        return 0

    print(
        f"\nGiving up after {MAX_PASTE_ATTEMPTS} attempts. Run 'python login.py' "
        "to try again.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
