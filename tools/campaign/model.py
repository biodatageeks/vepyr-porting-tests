"""Data model of the VEP 116.2 merged-cache port campaign (#226, #235).

The manifest (``cases.json``) stays the single source of recorded data: a
:class:`Case` keeps the raw JSON object it was read from (so a rewrite keeps
every key and its order) and exposes typed views of the fields the tools use.
Campaign settings that used to be literals in code live in ``campaign.toml``
next to the manifest (:class:`Settings`).
"""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

__all__ = [
    "DEFAULT_SETTINGS",
    "ROOT",
    "CampaignError",
    "Case",
    "ColumnFocus",
    "CsqFocus",
    "CsqValuesFocus",
    "Focus",
    "InfoFocus",
    "RecordCountFocus",
    "Settings",
    "Status",
    "atomic_write_text",
    "load_cases",
    "parse_focus",
    "write_cases",
]

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
"""Repository root (``tools/campaign/model.py`` -> ``.``)."""

DEFAULT_SETTINGS: Final[Path] = ROOT / "docs/porting/vep1162-merged/campaign.toml"
"""The campaign settings file; its ``manifest`` is resolved relative to it."""

_NEW_FILE_MODE: Final[int] = 0o644
"""Mode of a file :func:`atomic_write_text` creates (``mkstemp`` would use 0600)."""


class CampaignError(Exception):
    """A campaign input, record or step is invalid; the message says which."""


class Status(StrEnum):
    """Status of one campaign candidate."""

    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    QUEUED = "QUEUED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True, kw_only=True)
class CsqFocus:
    """Exactly one CSQ entry matching ``where``; its ``field`` is the value."""

    field: str
    where: Mapping[str, str]
    expected: Any


@dataclass(frozen=True, slots=True, kw_only=True)
class CsqValuesFocus:
    """``field`` of every CSQ entry matching ``where``, in file order or sorted."""

    field: str
    where: Mapping[str, str]
    ordered: bool
    expected: Any


@dataclass(frozen=True, slots=True, kw_only=True)
class InfoFocus:
    """Per record, the value of INFO key ``key`` (``None`` when absent)."""

    key: str
    expected: Any


@dataclass(frozen=True, slots=True, kw_only=True)
class ColumnFocus:
    """Per record, column ``column`` (0-based) verbatim."""

    column: int
    expected: Any


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordCountFocus:
    """The number of body records."""

    expected: Any


type Focus = CsqFocus | CsqValuesFocus | InfoFocus | ColumnFocus | RecordCountFocus
"""The primary property a case pins in the oracle (tagged by ``kind`` in JSON)."""


def parse_focus(raw: Mapping[str, Any], *, where: str = "focus") -> Focus:
    """Build a :data:`Focus` from its JSON object; ``kind`` defaults to ``csq``.

    Raises:
        CampaignError: Unknown ``kind`` or a missing/mistyped key.
    """
    try:
        expected = raw["expected"]
        match raw.get("kind", "csq"):
            case "csq":
                return CsqFocus(
                    field=str(raw["field"]), where=dict(raw["where"]), expected=expected
                )
            case "csq_values":
                return CsqValuesFocus(
                    field=str(raw["field"]),
                    where=dict(raw["where"]),
                    ordered=bool(raw.get("ordered", False)),
                    expected=expected,
                )
            case "info":
                return InfoFocus(key=str(raw["key"]), expected=expected)
            case "column":
                column = raw["column"]
                if not isinstance(column, int) or column < 0:
                    raise CampaignError(f"{where}: column must be an int >= 0")
                return ColumnFocus(column=column, expected=expected)
            case "record_count":
                return RecordCountFocus(expected=expected)
            case other:
                raise CampaignError(f"{where}: unknown focus kind {other!r}")
    except KeyError as exc:
        raise CampaignError(f"{where}: missing key {exc}") from exc


@dataclass(frozen=True, slots=True)
class Case:
    """One manifest entry: the raw JSON object plus typed accessors."""

    raw: Mapping[str, Any]

    @property
    def id(self) -> str:
        """The case ID (``DT-...``)."""
        return str(self.raw["id"])

    @property
    def status(self) -> Status:
        """The recorded :class:`Status`.

        Raises:
            CampaignError: The status is not one of :class:`Status`.
        """
        try:
            return Status(self.raw["status"])
        except (KeyError, ValueError) as exc:
            raise CampaignError(f"{self.id}: unknown status {exc}") from exc

    @property
    def directory_name(self) -> str:
        """Basename of the case's ``tests/data/`` directory."""
        return str(self.raw["directory_name"])

    @property
    def focus(self) -> Focus | None:
        """The parsed focus, or ``None`` for a case without one."""
        raw = self.raw.get("focus")
        return None if raw is None else parse_focus(raw, where=f"{self.id}: focus")

    @property
    def result(self) -> Mapping[str, Any]:
        """The recorded result (empty for queued/blocked cases)."""
        return self.raw.get("result", {})

    @property
    def runnable(self) -> bool:
        """Whether the case has explicit rows and a focus (can be ported)."""
        return "focus" in self.raw and "rows" in self.raw


@dataclass(frozen=True, slots=True, kw_only=True)
class Settings:
    """``campaign.toml``: where the manifest and data-tests are, and the constants."""

    manifest: Path
    data_dir: Path
    batch_size: int
    issue: int
    report_head: str

    @property
    def readme(self) -> Path:
        """The generated campaign table, next to the manifest."""
        return self.manifest.with_name("README.md")

    @property
    def failures(self) -> Path:
        """Committed failure evidence: ``failures/<case id>/actual_output.vcf``."""
        return self.manifest.with_name("failures")

    @classmethod
    def load(cls, path: Path = DEFAULT_SETTINGS) -> Settings:
        """Read and validate the settings file.

        Raises:
            CampaignError: The file is missing, not TOML, or lacks a key.
        """
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            settings = cls(
                manifest=path.parent / data["manifest"],
                data_dir=(path.parent / data["data_dir"]).resolve(),
                batch_size=data["batch_size"],
                issue=data["issue"],
                report_head=data["report"]["head"],
            )
        except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
            raise CampaignError(f"{path}: unusable campaign settings: {exc}") from exc
        if not isinstance(settings.batch_size, int) or settings.batch_size <= 0:
            raise CampaignError(f"{path}: batch_size must be a positive int")
        return settings


def load_cases(manifest: Path) -> list[Case]:
    """Read the manifest (a JSON list of objects).

    Raises:
        CampaignError: Unreadable, not JSON, not a list of objects, or an
            object without ``id``.
    """
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignError(f"{manifest}: unreadable manifest: {exc}") from exc
    if not isinstance(data, list) or not all(
        isinstance(c, dict) and "id" in c for c in data
    ):
        raise CampaignError(f"{manifest}: expected a JSON list of objects with an id")
    return [Case(c) for c in data]


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a sibling temp file and :func:`os.replace`.

    A crash leaves either the old or the new content, never a partial file.
    """
    mode = path.stat().st_mode & 0o777 if path.exists() else _NEW_FILE_MODE
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_cases(manifest: Path, cases: Sequence[Case]) -> None:
    """Atomically rewrite the manifest in its committed format (indent 2, LF)."""
    atomic_write_text(
        manifest, json.dumps([dict(c.raw) for c in cases], indent=2) + "\n"
    )
