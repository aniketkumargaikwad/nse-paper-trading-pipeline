"""The options research prompts: the rule, the baselines, the history - and nothing from the exam."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.ai import prompts  # noqa: E402
from fno.ai.summary import OptionsSummary  # noqa: E402

ATLAS_ROW = {"name": "straddle-0920", "underlying": "NIFTY", "passes": False,
             "why_not": "its lead over holding could be luck: t = +0.24 (needs 2)",
             "headline": {"avg_month_pct": 0.72, "worst_fall_pct": 10.83, "worst_day_pct": -4.26,
                          "trades_per_month": 18.43, "win_rate_pct": 55.81}}
IVRANK_ROW = {"tag": "P1", "strategy": "short-strangle-20d", "index": "NIFTY", "won_pct": 83.87,
              "avg_month": 0.1158, "worst_day": -3.65, "worst_fall": 3.24,
              "trades_per_month": 0.26, "passes": False, "why_not": "too slow: 0.3 trades a month"}


def test_atlas_lines_read_every_baseline_file(tmp_path):
    (tmp_path / "atlas").mkdir()
    (tmp_path / "atlas" / "intraday.json").write_text(json.dumps([ATLAS_ROW]), encoding="utf-8")
    (tmp_path / "atlas" / "ivrank_test.json").write_text(json.dumps([IVRANK_ROW]), encoding="utf-8")
    lines = prompts.atlas_lines(tmp_path)
    assert lines[0].startswith("intraday atlas: straddle-0920 on NIFTY: +0.72%/month")
    assert "fails: its lead" in lines[0]
    assert lines[1].startswith("expensive-options test") and "0.3 trades/month" in lines[1]


def test_the_propose_prompt_states_the_rule_and_carries_the_history():
    text = prompts.propose_prompt(format_doc="FORMAT-DOC", atlas=["a baseline"],
                                  notes=[{"day": "2026-10-21", "idea_title": "t", "lessons": "L1"}],
                                  ideas_tried=["O-x: tried"], error="legs[0]: bad")
    for needle in ("-10%", "30%", "luck test t of 2", "10 trades a month", "12 months (intraday)",
                   "FORMAT-DOC", "a baseline", "L1", "O-x: tried", "legs[0]: bad", "BOTH NIFTY and BANKNIFTY"):
        assert needle in text, needle


def test_the_review_prompt_holds_the_summary_and_forces_stop_on_the_last_version():
    summary = OptionsSummary("intraday", [{"underlying": "NIFTY", "avg_month_pct": 0.5}])
    text = prompts.review_prompt(summary=summary, version=5, versions_left=0)
    assert '"underlying": "NIFTY"' in text and 'MUST be "stop"' in text
