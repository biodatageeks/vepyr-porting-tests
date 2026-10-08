"""Port qualified campaign cases into data-tests, crash-safely (#226, #232, #235).

Only cases with explicit input rows and a focus are run, in ``batch_order``.
For each case the raw VCF is synthesised into the evidence directory,
``tools/normalize_input`` writes ``input.vcf``, ``./bless`` produces the VEP
116.2 oracle (the local VEP cache is used only here), and vepyr is run and
compared by ``./run_tests --cache-dir <cache> --via-cli --only <dir>`` (#231):
its exit code and ``MISMATCH`` lines decide the status (:mod:`campaign.classify`).
A failing vepyr result never changes the oracle.

Crash safety: each case is built in a staging directory under ``tests/`` and
moved into ``tests/data/<name>/`` only when every step succeeded; its evidence
is written to ``<evidence>/.<name>.partial/`` and renamed likewise; the
manifest is rewritten atomically after each case. An exception (including
Ctrl-C) leaves neither a partial data-test nor a partial manifest, and a re-run
resumes with the cases still queued.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from campaign.classify import RunOutput, classify, only_dir_name
from campaign.focus import focus_value, md5_of_body
from campaign.model import (
    ROOT,
    CampaignError,
    Case,
    Settings,
    Status,
    load_cases,
    write_cases,
)
from vcf_records import VcfFormatError, iter_records

__all__ = [
    "PortOptions",
    "Runner",
    "eligible",
    "port_case",
    "raw_vcf",
    "render_test_toml",
    "run_campaign",
    "subprocess_runner",
]

DEFAULT_COLUMN_HEADER: Final[str] = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO"
STAGING_PREFIX: Final[str] = ".campaign-"
"""Prefix of per-case staging directories under ``tests/`` (git-ignored)."""
_UNORDERED: Final[int] = 1000
"""Sort key of a case without ``batch_order`` (after every ordered case)."""


class Runner(Protocol):
    """Runs one command to completion; injected so tests stub every subprocess."""

    def __call__(
        self, argv: Sequence[str], *, cwd: Path, merge_stderr: bool
    ) -> RunOutput:
        """Run ``argv`` in ``cwd``; with ``merge_stderr`` stderr goes to stdout."""
        ...


def subprocess_runner(
    argv: Sequence[str], *, cwd: Path, merge_stderr: bool
) -> RunOutput:
    """The default :class:`Runner`: :func:`subprocess.run`, output captured."""
    proc = subprocess.run(
        list(argv),
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
    )
    return RunOutput(
        returncode=proc.returncode, stdout=proc.stdout or "", stderr=proc.stderr or ""
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class PortOptions:
    """Command-line options of ``./campaign port``."""

    vep_cache: Path
    cache_dir: Path
    fasta: Path
    evidence: Path
    limit: int
    regenerate: bool = False


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def eligible(cases: Sequence[Case], *, data_dir: Path, regenerate: bool) -> list[Case]:
    """Cases to port, in ``batch_order``.

    Normally the queued cases with rows and a focus; with ``regenerate`` the
    executed ones whose input normalization has not been verified yet.
    """

    def wanted(case: Case) -> bool:
        if not case.runnable:
            return False
        if regenerate:
            return (
                data_dir / case.directory_name / "input.vcf"
            ).exists() and not case.result.get("normalization_verified")
        return case.status is Status.QUEUED

    return sorted(
        (c for c in cases if wanted(c)),
        key=lambda c: c.raw.get("batch_order", _UNORDERED),
    )


def _contigs(case: Case) -> list[str]:
    """CHROM of the case's rows, first occurrence order.

    Raises:
        CampaignError: A row is not a VCF record.
    """
    try:
        records = iter_records(case.raw["rows"], where=f"{case.id}: rows")
        return list(dict.fromkeys(r.chrom for r in records))
    except VcfFormatError as exc:
        raise CampaignError(str(exc)) from exc


def raw_vcf(case: Case) -> str:
    """The raw VCF text of a case: contigs, extra headers, column header, rows.

    Raises:
        CampaignError: A row is not a VCF record.
    """
    rows: list[str] = list(case.raw["rows"])
    contigs = _contigs(case)
    return (
        "##fileformat=VCFv4.2\n"
        + "".join(f"##contig=<ID={c}>\n" for c in contigs)
        + "".join(h + "\n" for h in case.raw.get("headers", []))
        + case.raw.get("column_header", DEFAULT_COLUMN_HEADER)
        + "\n"
        + "\n".join(rows)
        + "\n"
    )


def _toml_value(value: object) -> str:
    match value:
        case bool():
            return "true" if value else "false"
        case int() | str():
            return json.dumps(value)
        case list() if all(isinstance(v, str) for v in value):
            return json.dumps(value)
        case _:
            raise CampaignError(f"test.toml: unsupported value {value!r}")


def _render_toml(data: Mapping[str, Any]) -> str:
    """Render scalars, then one ``[table]`` per mapping, separated by blank lines.

    JSON strings and lists of strings are valid TOML; the result is verified
    by a :mod:`tomllib` round trip.
    """
    scalars = [
        f"{k} = {_toml_value(v)}\n"
        for k, v in data.items()
        if not isinstance(v, Mapping)
    ]
    tables = [
        f"[{name}]\n" + "".join(f"{k} = {_toml_value(v)}\n" for k, v in table.items())
        for name, table in data.items()
        if isinstance(table, Mapping)
    ]
    text = "\n".join(["".join(scalars), *tables])
    if tomllib.loads(text) != data:
        raise CampaignError("test.toml: rendering does not round-trip")
    return text


def render_test_toml(case: Case, *, default_issue: int) -> str:
    """The initial ``test.toml`` of a case (``./bless`` fills ``[vep]`` and md5).

    ``[origin] issue`` is the case's ``issue`` field, else ``default_issue``.
    """
    pinned = case.raw["source_links"][0]
    contigs = _contigs(case)
    required = case.raw.get(
        "required_contigs", ["chr" + c.removeprefix("chr") for c in contigs]
    )
    return _render_toml(
        {
            "name": case.directory_name,
            "description": case.raw["description"],
            "origin": {
                "vep_test": pinned,
                "vep_test_pinned": pinned,
                "vep_subject": case.raw["implementation_links"][0]["url"],
                "issue": case.raw.get("issue", default_issue),
            },
            "vepyr": {
                "flavour": "merged",
                "required_contigs": required,
                "everything": True,
                "preserve_record_layout": True,
                "reference_fasta": True,
            },
            "vep": {"extra_flags": ["--merged"]},
            "compare": {"body_md5": ""},
        }
    )


def _step(
    runner: Runner,
    argv: Sequence[str],
    log: Path,
    final_log: Path,
    *,
    root: Path,
) -> dict[str, Any]:
    """Run a prerequisite step; record it; raise unless it exited 0."""
    out = runner(argv, cwd=root, merge_stderr=True)
    log.write_text(out.stdout)
    record = {
        "argv": [str(a) for a in argv],
        "exit": out.returncode,
        "log": str(final_log),
    }
    if out.returncode:
        raise CampaignError(f"step failed: {json.dumps(record)}")
    return record


def port_case(
    case: Case,
    options: PortOptions,
    settings: Settings,
    *,
    root: Path = ROOT,
    runner: Runner = subprocess_runner,
) -> Case:
    """Port one case and return it with its new status and ``result``.

    On success ``tests/data/<name>/`` and ``<evidence>/<name>/`` exist; on any
    exception neither was created (a regenerated directory keeps its old
    content) and the staging directory is removed.

    Raises:
        CampaignError: A prerequisite step failed, the input changed between
            engines, the Docker copy was not verified, the witness changed, or
            a destination is in the way.
    """
    name = case.directory_name
    dest = settings.data_dir / name
    final_evidence = options.evidence / name
    if dest.exists() != options.regenerate:
        state = "exists" if dest.exists() else "does not exist"
        raise CampaignError(
            f"{case.id}: {dest} {state} (status {case.status}); "
            "remove a leftover directory or use --regenerate for existing ports"
        )
    if final_evidence.exists():
        raise CampaignError(
            f"{case.id}: evidence {final_evidence} exists; "
            "use a new --evidence directory"
        )
    focus = case.focus
    if focus is None:
        raise CampaignError(f"{case.id}: no focus")
    partial = options.evidence / f".{name}.partial"
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    staging = Path(
        tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=settings.data_dir.parent)
    )
    try:
        work = staging / name
        if options.regenerate:
            shutil.copytree(dest, work)
        else:
            work.mkdir()
        raw = partial / "raw.vcf"
        raw.write_text(raw_vcf(case))
        (work / "test.toml").write_text(
            render_test_toml(case, default_issue=settings.issue)
        )
        runs = [
            _step(
                runner,
                [str(root / "tools/normalize_input"), str(raw), str(work)],
                partial / "normalize.log",
                final_evidence / "normalize.log",
                root=root,
            )
        ]
        before_vep = _sha256(work / "input.vcf")
        runs.append(
            _step(
                runner,
                [
                    str(root / "bless"),
                    "--vep-cache-dir",
                    str(options.vep_cache),
                    "--vep-fasta",
                    str(options.fasta),
                    str(work),
                ],
                partial / "vep.log",
                final_evidence / "vep.log",
                root=root,
            )
        )
        before_vepyr = _sha256(work / "input.vcf")
        if before_vep != before_vepyr:
            raise CampaignError(f"{case.id}: input changed between tools")
        if f"Docker input SHA256 {before_vep}" not in (partial / "vep.log").read_text():
            raise CampaignError(f"{case.id}: Docker copy hash was not verified")
        oracle = work / "expected_output.vcf"
        expected = focus_value(oracle, focus)
        if expected != focus.expected:
            raise CampaignError(f"{name}: witness changed: {expected!r}")
        argv = [
            "./run_tests",
            "--cache-dir",
            str(options.cache_dir),
            "--via-cli",
            "--only",
            str(work),
        ]
        out = runner(argv, cwd=root, merge_stderr=False)
        (partial / "run_tests.log").write_text(
            f"## stdout\n{out.stdout}## stderr\n{out.stderr}"
        )
        verdict = classify(out, only_dir_name(argv))
        runs.append(
            {
                "argv": argv,
                "exit": out.returncode,
                "log": str(final_evidence / "run_tests.log"),
                "verdict": str(verdict.status),
            }
        )
        oracle_md5 = md5_of_body(oracle)
        input_sha = _sha256(work / "input.vcf")
        if input_sha != before_vep:
            raise CampaignError(f"{case.id}: input changed during vepyr")
        result: dict[str, Any] = {
            "commands": runs,
            "input_sha256": input_sha,
            "oracle_body_md5": oracle_md5,
            "oracle_focus": expected,
            "normalization_verified": True,
            "input_sha256_before_vep": before_vep,
            "input_sha256_before_vepyr": before_vepyr,
            "docker_input_sha256": before_vep,
            "runnable": verdict.status is not Status.ERROR,
        }
        match verdict.status:
            case Status.PASS:
                result["vepyr_body_md5"] = oracle_md5
            case Status.FAIL:
                result["vepyr_body_md5"] = verdict.actual_md5
        result["status"] = str(verdict.status)
        (partial / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        updated = dict(case.raw)
        updated["status"] = str(verdict.status)
        updated["result"] = result
        updated["oracle_status"] = "Generated by VEP 116.2 with merged cache 116"
        updated["witness_status"] = (
            "Primary property qualified against the generated oracle"
        )
        updated["potential_vepyr_bug"] = (
            "None observed in this run"
            if verdict.status is Status.PASS
            else "Observed differential failure; cause not yet assigned"
        )
        if options.regenerate:
            dest.replace(staging / f"{name}.previous")
        work.replace(dest)
        partial.replace(final_evidence)
        return Case(updated)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(partial, ignore_errors=True)


def run_campaign(
    options: PortOptions,
    settings: Settings,
    *,
    root: Path = ROOT,
    runner: Runner = subprocess_runner,
) -> int:
    """Port up to ``options.limit`` eligible cases; 0 iff every one is ``PASS``.

    The manifest is rewritten atomically after each case, so an interrupted
    run keeps every case completed before the interruption.

    Raises:
        CampaignError: No case is eligible, or a case could not be ported
            (cases ported before it stay recorded).
    """
    cases = load_cases(settings.manifest)
    todo = eligible(cases, data_dir=settings.data_dir, regenerate=options.regenerate)[
        : options.limit
    ]
    if not todo:
        raise CampaignError("no qualified cases remain")
    index = {c.id: i for i, c in enumerate(cases)}
    statuses: list[Status] = []
    for case in todo:
        ported = port_case(case, options, settings, root=root, runner=runner)
        cases[index[case.id]] = ported
        write_cases(settings.manifest, cases)
        statuses.append(ported.status)
        print(f"{ported.id} {ported.status} {ported.directory_name}", flush=True)
    counts = {
        str(s): sum(c.status is s for c in cases)
        for s in (Status.PASS, Status.FAIL, Status.ERROR, Status.QUEUED)
    }
    print(json.dumps(counts), flush=True)
    return 0 if all(s is Status.PASS for s in statuses) else 1
