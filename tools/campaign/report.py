"""Render the per-candidate campaign table (``README.md`` next to the manifest).

Queued, blocked and failing candidates are listed like passing ones. The prose
above the table is the ``[report] head`` template of ``campaign.toml``
(``$normalized`` and ``$results`` are filled in here); this module renders only
the computed values and the rows.
"""

from __future__ import annotations

import collections
from collections.abc import Mapping, Sequence
from string import Template
from typing import Any, Final

from campaign.model import CampaignError, Case, Settings, atomic_write_text, load_cases

__all__ = ["focus_cell", "render", "row", "write_report"]

NOT_RECORDED: Final[str] = "—"
"""Focus cell of a case with no focus outcome recorded."""


def _escape(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def focus_cell(result: Mapping[str, Any]) -> str:
    """``PASS``/``FAIL`` for a recorded vepyr focus, ``not checked`` for a run
    through ``./run_tests --via-cli`` (vepyr's focus is not evaluated there),
    otherwise :data:`NOT_RECORDED`."""
    if result.get("focus_pass"):
        return "PASS"
    if "focus_pass" in result:
        return "FAIL"
    if "verdict" in result.get("commands", [{}])[-1]:
        return "not checked"
    return NOT_RECORDED


def row(case: Case) -> str:
    """One Markdown table row of a case."""
    raw = case.raw
    name = case.id
    if "result" in raw:
        name = f"[{name}](../../../tests/data/{case.directory_name}/test.toml)"
    fields = [
        name,
        raw["description"],
        raw["status"],
        focus_cell(case.result),
        raw["old_cache_problem"],
        raw["potential_vepyr_bug"],
        raw["unsupported_features"],
        raw.get("block_reason", raw.get("witness_note", "")),
        " ".join(
            f"[assertion {i + 1}]({url})" for i, url in enumerate(raw["source_links"])
        ),
        " ".join(f"[{e['function']}]({e['url']})" for e in raw["implementation_links"]),
    ]
    return "| " + " | ".join(_escape(v) for v in fields) + " |"


def render(cases: Sequence[Case], head: str) -> str:
    """The whole ``README.md`` text: the filled ``head`` template, then the rows.

    Raises:
        CampaignError: ``head`` has an unknown placeholder or a case lacks a
            column's field.
    """
    counts = collections.Counter(str(c.raw["status"]) for c in cases)
    normalized = sum(bool(c.result.get("normalization_verified", False)) for c in cases)
    try:
        text = Template(head).substitute(
            normalized=normalized,
            results=", ".join(f"{k}: {v}" for k, v in sorted(counts.items())),
        )
        rows = [row(c) for c in cases]
    except (KeyError, ValueError) as exc:
        raise CampaignError(f"cannot render the campaign report: {exc!r}") from exc
    return text + "".join(r + "\n" for r in rows)


def write_report(settings: Settings) -> None:
    """Regenerate ``settings.readme`` from the manifest (atomic write)."""
    atomic_write_text(
        settings.readme,
        render(load_cases(settings.manifest), settings.report_head),
    )
