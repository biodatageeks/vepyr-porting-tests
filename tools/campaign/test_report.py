"""Tests for :mod:`campaign.report` (#235)."""

from __future__ import annotations

import pytest

from campaign.model import CampaignError, Case, Settings, load_cases
from campaign.report import focus_cell, render

REAL = Settings.load()


def test_render_reproduces_committed_readme() -> None:
    """The committed manifest renders to the committed README byte for byte."""
    text = render(load_cases(REAL.manifest), REAL.report_head)
    assert text == REAL.readme.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("result", "cell"),
    [
        pytest.param({"focus_pass": True}, "PASS", id="pass"),
        pytest.param({"focus_pass": False}, "FAIL", id="fail"),
        pytest.param(
            {"commands": [{"argv": ["./run_tests"], "verdict": "PASS"}]},
            "not checked",
            id="via-cli",
        ),
        pytest.param({}, "—", id="none"),
    ],
)
def test_focus_cell(result: dict[str, object], cell: str) -> None:
    """Via-cli runs do not evaluate vepyr's focus and say so."""
    assert focus_cell(result) == cell


def test_unknown_placeholder_is_an_error() -> None:
    """A template naming an unknown value is a CampaignError."""
    with pytest.raises(CampaignError, match="cannot render"):
        render([], "$nope\n")


def test_rows_escape_pipes_and_newlines() -> None:
    """Cell text cannot break the table."""
    case = Case(
        {
            "id": "DT-x",
            "description": "a|b\nc",
            "status": "QUEUED",
            "old_cache_problem": "",
            "potential_vepyr_bug": "",
            "unsupported_features": "",
            "source_links": ["u"],
            "implementation_links": [{"function": "f", "url": "v"}],
        }
    )
    text = render([case], "$normalized $results\n")
    assert text.splitlines() == [
        "0 QUEUED: 1",
        "| DT-x | a\\|b c | QUEUED | — |  |  |  |  | [assertion 1](u) | [f](v) |",
    ]
