"""Pure evaluation of a case's primary property (focus) on an annotated VCF.

Records are read through :mod:`vcf_records`; this module never splits VCF
lines into columns itself. The CSQ field names come from the
``##INFO=<ID=CSQ,...Format: a|b|...">`` header line.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final, assert_never

from bless import BlessError
from bless.testdir import body_md5 as _bless_body_md5
from campaign.model import (
    CampaignError,
    ColumnFocus,
    CsqFocus,
    CsqValuesFocus,
    Focus,
    InfoFocus,
    RecordCountFocus,
)
from vcf_records import VcfFormatError, VcfRecord, read_records

__all__ = ["consequence_entries", "focus_value", "md5_of_body"]

INFO_COLUMN: Final[int] = 7
"""0-based index of the INFO column."""
_CSQ_HEADER: Final[str] = "##INFO=<ID=CSQ,"
_CSQ_FORMAT: Final[str] = "Format: "

type FocusValue = str | int | list[str | None] | list[str]
"""What :func:`focus_value` returns, comparable with a JSON ``expected``."""


def md5_of_body(path: Path) -> str:
    """md5 of the VCF body (lines not starting with ``#``, terminators kept).

    Raises:
        CampaignError: The file cannot be read.
    """
    try:
        return _bless_body_md5(path)
    except BlessError as exc:
        raise CampaignError(str(exc)) from exc


def _records(path: Path) -> list[VcfRecord]:
    try:
        return read_records(path)
    except (OSError, UnicodeDecodeError, VcfFormatError) as exc:
        raise CampaignError(f"{path}: cannot read VCF records: {exc}") from exc


def _column(record: VcfRecord, index: int, path: Path) -> str:
    if index >= len(record.columns):
        raise CampaignError(
            f"{path}:{record.line}: record has {len(record.columns)} columns, "
            f"column {index} requested"
        )
    return record.columns[index]


def _info(record: VcfRecord, path: Path) -> list[tuple[str, str]]:
    """INFO as ``(key, value)`` pairs; a flag has value ``""``."""
    return [
        (key, value)
        for key, _, value in (
            item.partition("=")
            for item in _column(record, INFO_COLUMN, path).split(";")
        )
    ]


def _csq_fields(path: Path) -> list[str] | None:
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.startswith("#"):
                    return None
                if line.startswith(_CSQ_HEADER) and _CSQ_FORMAT in line:
                    return line.split(_CSQ_FORMAT, 1)[1].split('"', 1)[0].split("|")
    except (OSError, UnicodeDecodeError) as exc:
        raise CampaignError(f"{path}: cannot read VCF header: {exc}") from exc
    return None


def consequence_entries(path: Path) -> list[dict[str, str]]:
    """Every CSQ entry of every record, as ``{field name: value}``, in file order.

    Raises:
        CampaignError: A record carries CSQ but the header defines no format,
            or an entry's field count differs from the header's.
    """
    fields = _csq_fields(path)
    entries: list[dict[str, str]] = []
    for record in _records(path):
        for key, value in _info(record, path):
            if key != "CSQ":
                continue
            if fields is None:
                raise CampaignError(f"{path}: missing CSQ header")
            for entry in value.split(","):
                values = entry.split("|")
                if len(values) != len(fields):
                    raise CampaignError(
                        f"{path}:{record.line}: CSQ entry has {len(values)} fields, "
                        f"header declares {len(fields)}"
                    )
                entries.append(dict(zip(fields, values, strict=True)))
    return entries


def _matching(path: Path, where: Sequence[tuple[str, str]]) -> list[dict[str, str]]:
    return [
        e for e in consequence_entries(path) if all(e.get(k) == v for k, v in where)
    ]


def focus_value(path: Path, focus: Focus) -> FocusValue:
    """Evaluate ``focus`` on the VCF at ``path``.

    Raises:
        CampaignError: The VCF is unreadable, a column is missing, or a
            single-entry CSQ focus does not select exactly one entry.
    """
    match focus:
        case ColumnFocus(column=column):
            return [_column(r, column, path) for r in _records(path)]
        case RecordCountFocus():
            return len(_records(path))
        case InfoFocus(key=key):
            return [
                next((v for k, v in _info(r, path) if k == key), None)
                for r in _records(path)
            ]
        case CsqValuesFocus(field=field, where=where, ordered=ordered):
            values = [e[field] for e in _matching(path, list(where.items()))]
            return values if ordered else sorted(values)
        case CsqFocus(field=field, where=where):
            entries = _matching(path, list(where.items()))
            if len(entries) != 1:
                raise CampaignError(
                    f"{path}: focus selects {len(entries)} entries, "
                    "expected exactly one"
                )
            return entries[0][field]
        case _:
            assert_never(focus)
