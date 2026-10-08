"""Tests for :mod:`campaign.focus`: one test per focus kind, plus its edges (#235)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from campaign.focus import consequence_entries, focus_value, md5_of_body
from campaign.model import (
    CampaignError,
    ColumnFocus,
    CsqFocus,
    CsqValuesFocus,
    InfoFocus,
    RecordCountFocus,
    parse_focus,
)

HEADER = '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Feature|AF">\n'


def vcf(tmp_path: Path, *rows: str, header: str = HEADER) -> Path:
    """Write a VCF with ``header`` and tab-joined ``rows``."""
    path = tmp_path / "case.vcf"
    path.write_text(
        header + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n" + "".join(rows)
    )
    return path


def test_focus_csq_single(tmp_path: Path) -> None:
    """One matching entry gives its field; zero is kept as the string ``0``."""
    p = vcf(tmp_path, "21\t1\t.\tC\tT\t.\t.\tCSQ=TX|0,OTHER|1\n")
    focus = parse_focus({"field": "AF", "where": {"Feature": "TX"}, "expected": "0"})
    assert isinstance(focus, CsqFocus)
    assert focus_value(p, focus) == "0"


@pytest.mark.parametrize(("entries", "count"), [("TX|0,TX|0", 2), ("OTHER|0", 0)])
def test_focus_csq_single_needs_exactly_one_entry(
    tmp_path: Path, entries: str, count: int
) -> None:
    """Two or zero matching entries are an error naming the count."""
    p = vcf(tmp_path, f"21\t1\t.\tC\tT\t.\t.\tCSQ={entries}\n")
    focus = CsqFocus(field="AF", where={"Feature": "TX"}, expected="0")
    with pytest.raises(CampaignError, match=f"selects {count} entries"):
        focus_value(p, focus)


def test_focus_csq_values_ordered(tmp_path: Path) -> None:
    """``ordered`` keeps file order across records."""
    p = vcf(
        tmp_path,
        "21\t1\t.\tC\tT\t.\t.\tCSQ=TB|1,TA|2\n",
        "21\t2\t.\tC\tT\t.\t.\tCSQ=TC|1\n",
    )
    raw = {"kind": "csq_values", "field": "Feature", "where": {"AF": "1"}}
    focus = parse_focus({**raw, "ordered": True, "expected": []})
    assert isinstance(focus, CsqValuesFocus)
    assert focus_value(p, focus) == ["TB", "TC"]


def test_focus_csq_values_sorted(tmp_path: Path) -> None:
    """Without ``ordered`` the values are sorted."""
    p = vcf(tmp_path, "21\t1\t.\tC\tT\t.\t.\tCSQ=TB|1,TA|1\n")
    focus = parse_focus(
        {"kind": "csq_values", "field": "Feature", "where": {}, "expected": []}
    )
    assert focus_value(p, focus) == ["TA", "TB"]


def test_focus_info(tmp_path: Path) -> None:
    """INFO keys match exactly (``BCSQ`` is not ``CSQ``); absent is ``None``."""
    p = vcf(
        tmp_path,
        "21\t1\t.\tC\tT\t.\t.\tBCSQ=keep;CSQ=TX|0\n",
        "21\t2\t.\tC\tT\t.\t.\tDB\n",
    )
    focus = parse_focus({"kind": "info", "key": "BCSQ", "expected": None})
    assert isinstance(focus, InfoFocus)
    assert focus_value(p, focus) == ["keep", None]
    assert focus_value(p, InfoFocus(key="CSQ", expected=None)) == ["TX|0", None]


def test_focus_column(tmp_path: Path) -> None:
    """A column is read verbatim per record, sample columns included."""
    p = vcf(tmp_path, "21\t7\t.\tC\tT\t.\t.\t.\tGT\t0/1\n", "21\t9\t.\tC\tG\t.\t.\t.\n")
    focus = parse_focus({"kind": "column", "column": 1, "expected": []})
    assert isinstance(focus, ColumnFocus)
    assert focus_value(p, focus) == ["7", "9"]
    with pytest.raises(CampaignError, match="column 9 requested"):
        focus_value(p, ColumnFocus(column=9, expected=None))


def test_focus_record_count(tmp_path: Path) -> None:
    """Header lines are not records."""
    p = vcf(tmp_path, "21\t1\t.\tC\tT\t.\t.\t.\n", "21\t2\t.\tC\tT\t.\t.\t.\n")
    focus = parse_focus({"kind": "record_count", "expected": 2})
    assert isinstance(focus, RecordCountFocus)
    assert focus_value(p, focus) == 2


def test_unknown_focus_kind_is_an_error() -> None:
    """An unknown ``kind`` or a missing key is a CampaignError, not a KeyError."""
    with pytest.raises(CampaignError, match="unknown focus kind"):
        parse_focus({"kind": "nope", "expected": 1})
    with pytest.raises(CampaignError, match="missing key"):
        parse_focus({"kind": "info", "expected": 1})


def test_csq_without_header_is_an_error(tmp_path: Path) -> None:
    """A CSQ value with no CSQ header cannot be decoded."""
    p = vcf(tmp_path, "21\t1\t.\tC\tT\t.\t.\tCSQ=TX|0\n", header="")
    with pytest.raises(CampaignError, match="missing CSQ header"):
        consequence_entries(p)


def test_body_md5_ignores_headers_but_preserves_record_order(tmp_path: Path) -> None:
    """Only body lines are hashed, in file order."""
    a, b = tmp_path / "a.vcf", tmp_path / "b.vcf"
    a.write_bytes(b"##VEP=116.2\n#CHROM\n21\t1\n21\t2\n")
    b.write_bytes(b"##VEP=other\n#CHROM\n21\t1\n21\t2\n")
    assert (
        md5_of_body(a) == md5_of_body(b) == hashlib.md5(b"21\t1\n21\t2\n").hexdigest()
    )
    b.write_bytes(b"##VEP=116.2\n#CHROM\n21\t2\n21\t1\n")
    assert md5_of_body(a) != md5_of_body(b)
