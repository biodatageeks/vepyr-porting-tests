"""Run curated VEP 116.2 merged-cache ports, retaining positive and negative evidence.

Only cases with explicit input rows and a qualified focus assertion are run.
The default batch size is ten. A failing vepyr result never changes the oracle.
The local VEP cache is used only for the oracle (``./bless``). vepyr is run and
compared by the Python ``run_selection()`` entry point on
a Hub-layout cache; its exit code and ``MISMATCH`` lines decide the verdict.
The process exits non-zero when any processed case is not ``PASS``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from vep_pin import load_pin

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs/porting/vep1162-merged/cases.json"

type Runner = Callable[..., subprocess.CompletedProcess[Any]]
"""``subprocess.run``-compatible callable; injectable so tests stub subprocesses."""

MISMATCH_EXIT = 8
"""``Exit.MISMATCH`` of ``./run_tests --via-cli`` (the #231 mismatch contract)."""

_MISMATCH = re.compile(
    r"^MISMATCH (\S+) expected=([0-9a-f]{32}) actual=([0-9a-f]{32})$"
)


class Verdict(StrEnum):
    """Campaign status of the step delegated to ``./run_tests``."""

    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class RunTestsOutcome:
    """Classified result of one ``./run_tests --via-cli`` subprocess."""

    verdict: Verdict
    actual_md5: str | None
    record: dict[str, Any]


def classify(
    exit_code: int, stdout: str, dir_name: str | None = None
) -> tuple[Verdict, str | None]:
    """Map a ``./run_tests --via-cli`` exit code and stdout to a verdict.

    Exit 0 is ``PASS``; exit 8 with at least one well-formed ``MISMATCH`` line
    on stdout naming ``dir_name`` (any name when ``None``) is ``FAIL`` (that
    line's ``actual`` md5 is returned); exit 8 without such a line (malformed
    report) and any other non-zero exit are ``ERROR``.
    """
    mismatches = [
        m
        for line in stdout.splitlines()
        if (m := _MISMATCH.match(line)) and dir_name in (None, m[1])
    ]
    match exit_code:
        case 0:
            return Verdict.PASS, None
        case code if code == MISMATCH_EXIT and mismatches:
            return Verdict.FAIL, mismatches[0][3]
        case _:
            return Verdict.ERROR, None


def only_dir_name(argv: Sequence[str]) -> str | None:
    """Return the directory name of the ``--only`` value in ``argv``, if any."""
    args = list(argv)
    if "--only" in args and (i := args.index("--only") + 1) < len(args):
        return Path(args[i]).name
    if len(args) == 6 and args[1] == "-c" and "run_selection" in args[2]:
        return Path(args[-1]).name
    return None


def run_tests_for(
    argv: Sequence[str], log: Path | None = None, runner: Runner = subprocess.run
) -> RunTestsOutcome:
    """Run ``./run_tests`` (``argv``) from the repo root and classify the result.

    stdout and stderr are captured separately (``MISMATCH`` lines are read from
    stdout only); both are written to ``log`` when given.
    """
    proc = runner(list(argv), cwd=ROOT, capture_output=True, text=True)
    stdout, stderr = proc.stdout or "", proc.stderr or ""
    if log is not None:
        log.write_text(f"## stdout\n{stdout}## stderr\n{stderr}")
    verdict, actual = classify(proc.returncode, stdout, only_dir_name(argv))
    record = {
        "argv": [str(a) for a in argv],
        "exit": proc.returncode,
        "log": str(log),
        "verdict": str(verdict),
    }
    return RunTestsOutcome(verdict, actual, record)


def classify_run_tests(argv: Sequence[str], runner: Runner = subprocess.run) -> Verdict:
    """Run ``./run_tests --via-cli`` (``argv``) and return its campaign verdict."""
    return run_tests_for(argv, runner=runner).verdict


def body(path):
    return b"".join(
        x for x in path.read_bytes().splitlines(keepends=True) if not x.startswith(b"#")
    )


def csq(path):
    fields = None
    result = []
    for line in path.read_text().splitlines():
        if line.startswith("##INFO=<ID=CSQ,"):
            fields = line.split("Format: ", 1)[1].split('"', 1)[0].split("|")
        if line.startswith("#"):
            continue
        columns = line.split("\t")
        for item in columns[7].split(";"):
            if item.startswith("CSQ="):
                if fields is None:
                    raise ValueError(f"{path}: missing CSQ header")
                result.extend(
                    dict(zip(fields, e.split("|"), strict=True))
                    for e in item[4:].split(",")
                )
    return result


def focus_value(path, focus):
    kind = focus.get("kind", "csq")
    rows = [
        line.split("\t")
        for line in path.read_text().splitlines()
        if not line.startswith("#")
    ]
    if kind == "column":
        return [row[focus["column"]] for row in rows]
    if kind == "record_count":
        return len(rows)
    if kind == "info":
        return [
            next(
                (
                    v.partition("=")[2]
                    for v in row[7].split(";")
                    if v.partition("=")[0] == focus["key"]
                ),
                None,
            )
            for row in rows
        ]
    entries = [
        e for e in csq(path) if all(e.get(k) == v for k, v in focus["where"].items())
    ]
    if kind == "csq_values":
        values = [e[focus["field"]] for e in entries]
        return values if focus.get("ordered") else sorted(values)
    if len(entries) != 1:
        raise ValueError(f"focus selects {len(entries)} entries, expected exactly one")
    return entries[0][focus["field"]]


def run(argv, log, runner: Runner = subprocess.run):
    with log.open("w") as stream:
        result = runner(argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
    return {"argv": [str(a) for a in argv], "exit": result.returncode, "log": str(log)}


def main(argv: Sequence[str] | None = None, runner: Runner = subprocess.run) -> int:
    """Run the campaign; return 0 iff every processed case is ``PASS``."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--vep-cache", type=Path, required=True)
    p.add_argument(
        "--cache-dir",
        type=Path,
        required=True,
        help="Hub-layout cache root with PROVENANCE.json, passed to ./run_tests",
    )
    p.add_argument("--vepyr", required=True, help="PyPI version or full vepyr Git SHA")
    p.add_argument("--fasta", type=Path, required=True)
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument(
        "--regenerate",
        action="store_true",
        help="Normalize and rerun existing ports, once per input-identity audit",
    )
    args = p.parse_args(argv)
    if args.limit <= 0:
        p.error("--limit must be positive")
    cases = json.loads(MANIFEST.read_text())

    def eligible(c):
        if "focus" not in c or "rows" not in c:
            return False
        if args.regenerate:
            return (
                ROOT / "tests/data" / c["directory_name"] / "input.vcf"
            ).exists() and not c.get("result", {}).get("normalization_verified")
        return c["status"] == "QUEUED"

    todo = sorted(
        (c for c in cases if eligible(c)), key=lambda c: c.get("batch_order", 1000)
    )[: args.limit]
    if not todo:
        p.error("no qualified cases remain")
    for case in todo:
        name = case["directory_name"]
        dest = ROOT / "tests/data" / name
        dest.mkdir(exist_ok=args.regenerate)
        evidence = args.evidence / name
        evidence.mkdir(parents=True, exist_ok=False)
        contigs = list(dict.fromkeys(r.split("\t")[0] for r in case["rows"]))
        raw = evidence / "raw.vcf"
        raw.write_text(
            "##fileformat=VCFv4.2\n"
            + "".join(f"##contig=<ID={c}>\n" for c in contigs)
            + "".join(h + "\n" for h in case.get("headers", []))
            + case.get("column_header", "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO")
            + "\n"
            + "\n".join(case["rows"])
            + "\n"
        )
        q = json.dumps
        pinned = case["source_links"][0]
        pin = load_pin()
        tagged = pinned.replace(pin.upstream_commit, pin.upstream_tag)
        required_contigs = case.get(
            "required_contigs", ["chr" + c.removeprefix("chr") for c in contigs]
        )
        (dest / "test.toml").write_text(
            f"name = {q(name)}\ndescription = {q(case['description'])}\n\n"
            f"[[tests]]\nid = {q(name)}\ndescription = {q(case['description'])}\n"
            f"vep_test = {q(tagged)}\n\n"
            f'[vepyr]\nflavour = "merged"\nrequired_contigs = {q(required_contigs)}\n'
            "everything = true\npreserve_record_layout = true\n"
            "reference_fasta = true\n\n"
            '[vep]\nextra_flags = ["--merged"]\n\n[compare]\nbody_md5 = ""\n'
        )
        runs = []
        runs.append(
            run(
                [str(ROOT / "tools/normalize_input"), str(raw), str(dest)],
                evidence / "normalize.log",
                runner,
            )
        )
        if runs[-1]["exit"]:
            raise RuntimeError(runs[-1])
        input_sha_before_vep = hashlib.sha256(
            (dest / "input.vcf").read_bytes()
        ).hexdigest()
        runs.append(
            run(
                [
                    str(ROOT / "bless"),
                    "--vep-cache-dir",
                    str(args.vep_cache),
                    "--vep-fasta",
                    str(args.fasta),
                    str(dest),
                ],
                evidence / "vep.log",
                runner,
            )
        )
        if runs[-1]["exit"]:
            raise RuntimeError(runs[-1])
        input_sha_before_vepyr = hashlib.sha256(
            (dest / "input.vcf").read_bytes()
        ).hexdigest()
        assert input_sha_before_vep == input_sha_before_vepyr, (
            "input changed between tools"
        )
        assert (
            f"Docker input SHA256 {input_sha_before_vep}"
            in (evidence / "vep.log").read_text()
        ), "Docker copy hash was not verified"
        oracle = dest / "expected_output.vcf"
        expected = focus_value(oracle, case["focus"])
        if expected != case["focus"]["expected"]:
            raise ValueError(f"{name}: witness changed: {expected!r}")
        outcome = run_tests_for(
            [
                sys.executable,
                "-c",
                (
                    "import sys; from pathlib import Path; "
                    "sys.path.insert(0, 'tools'); "
                    "from run_tests.cli import run_selection; "
                    "raise SystemExit(run_selection(sys.argv[1], [Path(sys.argv[3])], "
                    "root=Path(sys.argv[2])))"
                ),
                args.vepyr,
                str(args.cache_dir),
                str(dest),
            ],
            evidence / "run_tests.log",
            runner,
        )
        runs.append(outcome.record)
        oracle_md5 = hashlib.md5(body(oracle)).hexdigest()
        result = {
            "commands": runs,
            "input_sha256": hashlib.sha256(
                (dest / "input.vcf").read_bytes()
            ).hexdigest(),
            "oracle_body_md5": oracle_md5,
            "oracle_focus": expected,
        }
        assert result["input_sha256"] == input_sha_before_vep, (
            "input changed during vepyr"
        )
        result.update(
            normalization_verified=True,
            input_sha256_before_vep=input_sha_before_vep,
            input_sha256_before_vepyr=input_sha_before_vepyr,
            docker_input_sha256=input_sha_before_vep,
        )
        case["status"] = str(outcome.verdict)
        result["runnable"] = outcome.verdict is not Verdict.ERROR
        match outcome.verdict:
            case Verdict.PASS:
                result["vepyr_body_md5"] = oracle_md5
            case Verdict.FAIL:
                result["vepyr_body_md5"] = outcome.actual_md5
        result["status"] = case["status"]
        case["result"] = result
        case["oracle_status"] = "Generated by VEP 116.2 with merged cache 116"
        case["witness_status"] = (
            "Primary property qualified against the generated oracle"
        )
        case["potential_vepyr_bug"] = (
            "None observed in this run"
            if case["status"] == "PASS"
            else "Observed differential failure; cause not yet assigned"
        )
        (evidence / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        MANIFEST.write_text(json.dumps(cases, indent=2) + "\n")
        print(f"{case['id']} {case['status']} {name}", flush=True)
    print(
        json.dumps(
            {
                status: sum(c["status"] == status for c in cases)
                for status in ["PASS", "FAIL", "ERROR", "QUEUED"]
            }
        ),
        flush=True,
    )
    return 0 if all(c["status"] == Verdict.PASS for c in todo) else 1


if __name__ == "__main__":
    raise SystemExit(main())
