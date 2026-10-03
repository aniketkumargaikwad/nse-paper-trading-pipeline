"""Which F&O contracts this platform researches, and their lot sizes.

Index derivatives only (design §0). Five underlyings are kept end-of-day for
positional research; two of them are also kept minute by minute (step 3).
"""

from __future__ import annotations

INDEX_UNDERLYINGS: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50")
MINUTE_UNDERLYINGS: tuple[str, ...] = ("NIFTY", "BANKNIFTY")

# Listed before 2016 and traded every session since, so a day's file without
# them is a fault rather than history. FINNIFTY (2021), MIDCPNIFTY (2022) and
# NIFTYNXT50 (2024) started later.
ALWAYS_LISTED: tuple[str, ...] = ("NIFTY", "BANKNIFTY")
