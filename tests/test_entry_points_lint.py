"""The scheduled research entry points must not reference names that do not exist.

An undefined name only fails when its line runs, which for the end of main() is after a
whole day of AI work. A static check catches it before the schedule does.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ENTRY_POINTS = ["research/rotation_day.py", "fno/ai/day.py"]


def test_the_scheduled_entry_points_have_no_undefined_names():
    if importlib.util.find_spec("ruff") is not None:
        cmd = [sys.executable, "-m", "ruff", "check", "--no-cache", "--isolated", "--select", "F821,F841", *ENTRY_POINTS]
    elif importlib.util.find_spec("pyflakes") is not None:
        cmd = [sys.executable, "-m", "pyflakes", *ENTRY_POINTS]
    else:
        pytest.skip("neither ruff nor pyflakes is installed")
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
