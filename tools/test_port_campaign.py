"""Controls for the campaign's byte comparison and primary-property selectors."""

import hashlib

import pytest
from port_campaign import body, focus_value


def test_body_md5_ignores_headers_but_preserves_record_order(tmp_path):
    a = tmp_path / "a.vcf"
    b = tmp_path / "b.vcf"
    a.write_bytes(b"##VEP=116.2\n#CHROM\n21\t1\n21\t2\n")
    b.write_bytes(b"##VEP=other\n#CHROM\n21\t1\n21\t2\n")
    assert hashlib.md5(body(a)).digest() == hashlib.md5(body(b)).digest()
    b.write_bytes(b"##VEP=116.2\n#CHROM\n21\t2\n21\t1\n")
    assert hashlib.md5(body(a)).digest() != hashlib.md5(body(b)).digest()


def test_focus_requires_one_matching_feature_and_keeps_zero(tmp_path):
    p = tmp_path / "case.vcf"
    header = '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Feature|AF">\n'
    row = "21\t1\t.\tC\tT\t.\t.\tCSQ=TX|0\n"
    p.write_text(header + row)
    focus = {"field": "AF", "where": {"Feature": "TX"}}
    assert focus_value(p, focus) == "0"
    p.write_text(header + row.replace("TX|0", "TX|0,TX|0"))
    with pytest.raises(ValueError, match="selects 2 entries"):
        focus_value(p, focus)
    p.write_text(header + row.replace("TX|0", "OTHER|0"))
    with pytest.raises(ValueError, match="selects 0 entries"):
        focus_value(p, focus)


def test_info_key_match_is_exact(tmp_path):
    p = tmp_path / "case.vcf"
    p.write_text("21\t1\t.\tC\tT\t.\t.\tBCSQ=keep;CSQ=annotation\n")
    assert focus_value(p, {"kind": "info", "key": "BCSQ"}) == ["keep"]
    assert focus_value(p, {"kind": "info", "key": "CSQ"}) == ["annotation"]
