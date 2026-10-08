"""Validate fixtures and results, reporting queued cases explicitly."""

import argparse
import collections
import hashlib
import json
import tomllib

from port_campaign import MANIFEST, ROOT, body, focus_value

from vep_pin import load_pin


def check(require_complete=False, require_normalized=False):
    cases = json.loads(MANIFEST.read_text())
    assert len(cases) == 202, "candidate inventory changed"
    assert len({c["id"] for c in cases}) == len(cases), "duplicate case ID"
    states = collections.Counter(c["status"] for c in cases)
    assert not states.keys() - {"PASS", "FAIL", "ERROR", "QUEUED", "BLOCKED"}, states
    ported = 0
    for case in cases:
        if case["status"] in {"QUEUED", "BLOCKED"}:
            if case["status"] == "BLOCKED":
                assert case.get("block_reason"), case["id"]
            continue
        ported += 1
        path = ROOT / "tests/data" / case["directory_name"]
        config = tomllib.loads((path / "test.toml").read_text())
        assert config["vepyr"]["flavour"] == "merged", case["id"]
        assert "--merged" in config["vep"]["command"].split(), case["id"]
        assert config["vep"]["image"] == load_pin().pinned_image, case["id"]
        result = case["result"]
        if require_normalized:
            assert result.get("normalization_verified"), case["id"]
        if result.get("normalization_verified"):
            assert all(
                result.get(key) == result["input_sha256"]
                for key in (
                    "input_sha256_before_vep",
                    "input_sha256_before_vepyr",
                    "docker_input_sha256",
                )
            ), f"{case['id']}: the engines did not receive identical normalized bytes"
        assert len(result["vepyr_sha"]) == 40, case["id"]
        assert (
            hashlib.sha256((path / "input.vcf").read_bytes()).hexdigest()
            == result["input_sha256"]
        ), case["id"]
        oracle = path / "expected_output.vcf"
        assert (
            hashlib.md5(body(oracle)).hexdigest()
            == result["oracle_body_md5"]
            == config["compare"]["body_md5"]
        ), case["id"]
        assert (
            focus_value(oracle, case["focus"])
            == case["focus"]["expected"]
            == result["oracle_focus"]
        ), case["id"]
        assert len(result["commands"]) == 3, case["id"]
        assert all(c["exit"] == 0 for c in result["commands"][:2]), case["id"]
        assert "--everything" in result["commands"][2]["argv"], case["id"]
        if case["status"] != "ERROR":
            assert result["runnable"] and result["commands"][2]["exit"] == 0, case["id"]
            equal = result["oracle_body_md5"] == result["vepyr_body_md5"]
            assert equal == (case["status"] == "PASS"), case["id"]
            if "focus_error" in result:
                assert result["focus_pass"] is False, case["id"]
            else:
                assert result["focus_pass"] == (
                    result["vepyr_focus"] == result["oracle_focus"]
                ), case["id"]
            if case["status"] == "PASS":
                assert result["focus_pass"], case["id"]
        else:
            assert not result["runnable"], case["id"]
        assert result["status"] == case["status"], case["id"]
    assert ported, "no executed ports"
    if require_complete:
        assert not states["QUEUED"], f"{states['QUEUED']} cases still queued"
    print(
        f"{ported} executed ports; "
        + ", ".join(f"{k}={v}" for k, v in sorted(states.items()))
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--require-normalized", action="store_true")
    args = parser.parse_args()
    check(args.require_complete, args.require_normalized)
