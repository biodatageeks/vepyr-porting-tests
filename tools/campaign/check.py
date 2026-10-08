"""Validate the recorded campaign against the committed files (#226, #235).

Each invariant is a named function returning a list of violations (empty when
it holds); :func:`check` collects them and never stops at the first. The
*independent* invariants recompute a value from a committed file and compare
it with what the manifest recorded:

* :func:`input_violations` - sha256 of ``tests/data/<name>/input.vcf``;
* :func:`oracle_violations` - body md5 and focus of ``expected_output.vcf``
  against the manifest and ``test.toml [compare] body_md5``;
* :func:`vepyr_violations` - the recorded vepyr body md5 against the re-read
  vepyr output: the oracle for ``PASS``, the committed
  ``failures/<id>/actual_output.vcf`` for ``FAIL``;
* :func:`test_toml_violations` - flavour, ``--merged`` and the pinned image.

:func:`record_violations` keeps the structural rules of a run record (command
count, exits, verdict agreeing with the status). The inventory size is not
fixed: it is whatever the manifest holds (#235, open question 2).

No ``assert`` is used, so ``python -O`` checks the same things.
"""

from __future__ import annotations

import collections
import hashlib
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from campaign.classify import MISMATCH_EXIT
from campaign.focus import focus_value, md5_of_body
from campaign.model import CampaignError, Case, Settings, Status, load_cases
from vep_pin import load_pin

__all__ = [
    "CheckReport",
    "check",
    "input_violations",
    "manifest_violations",
    "oracle_violations",
    "record_violations",
    "test_toml_violations",
    "vepyr_violations",
    "via_cli_record_violations",
]

_INPUT_HASH_FIELDS: Final[tuple[str, ...]] = (
    "input_sha256_before_vep",
    "input_sha256_before_vepyr",
    "docker_input_sha256",
)
_GIT_SHA_LEN: Final[int] = 40
_COMMANDS: Final[int] = 3
"""normalize_input, bless, and the vepyr run (or ``./run_tests --via-cli``)."""
ACTUAL_OUTPUT: Final[str] = "actual_output.vcf"


@dataclass(frozen=True, slots=True, kw_only=True)
class CaseFiles:
    """A ported case with its data-test directory and parsed ``test.toml``."""

    case: Case
    directory: Path
    config: Mapping[str, Any]
    failures: Path
    require_normalized: bool

    @property
    def oracle(self) -> Path:
        """``expected_output.vcf`` of the data-test."""
        return self.directory / "expected_output.vcf"

    @property
    def actual_output(self) -> Path:
        """Committed vepyr output of a failing case."""
        return self.failures / self.case.id / ACTUAL_OUTPUT


@dataclass(slots=True)
class CheckReport:
    """Outcome of :func:`check`."""

    executed: int = 0
    counts: collections.Counter[str] = field(default_factory=collections.Counter)
    violations: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """``N executed ports; STATUS=n, ...`` (statuses sorted)."""
        return f"{self.executed} executed ports; " + ", ".join(
            f"{k}={v}" for k, v in sorted(self.counts.items())
        )


def _is_via_cli(result: Mapping[str, Any]) -> bool:
    last = result.get("commands", [{}])[-1]
    return "verdict" in last or "--via-cli" in last.get("argv", [])


def manifest_violations(cases: Sequence[Case], *, require_complete: bool) -> list[str]:
    """IDs unique, statuses known, blocked cases explained, nothing queued if asked."""
    out: list[str] = []
    ids = collections.Counter(c.id for c in cases)
    out += [f"{i}: duplicate case ID" for i, n in ids.items() if n > 1]
    known = {str(s) for s in Status}
    for case in cases:
        status = case.raw.get("status")
        if status not in known:
            out.append(f"{case.id}: unknown status {status!r}")
        elif status == Status.BLOCKED and not case.raw.get("block_reason"):
            out.append(f"{case.id}: BLOCKED without block_reason")
    queued = sum(c.raw.get("status") == Status.QUEUED for c in cases)
    if require_complete and queued:
        out.append(f"{queued} cases still queued")
    return out


def test_toml_violations(files: CaseFiles) -> list[str]:
    """``test.toml`` is a merged-cache data-test blessed with the pinned image."""
    cid, config = files.case.id, files.config
    out: list[str] = []
    if config.get("vepyr", {}).get("flavour") != "merged":
        out.append(f"{cid}: test.toml [vepyr] flavour is not merged")
    if "--merged" not in str(config.get("vep", {}).get("command", "")).split():
        out.append(f"{cid}: test.toml [vep] command lacks --merged")
    if config.get("vep", {}).get("image") != load_pin().pinned_image:
        out.append(f"{cid}: test.toml [vep] image is not the pinned image")
    return out


def input_violations(files: CaseFiles) -> list[str]:
    """Re-hashed ``input.vcf`` equals the recorded hash(es) both engines received."""
    cid, result = files.case.id, files.case.result
    out: list[str] = []
    actual = hashlib.sha256((files.directory / "input.vcf").read_bytes()).hexdigest()
    if actual != result.get("input_sha256"):
        out.append(f"{cid}: input.vcf sha256 {actual} != recorded input_sha256")
    if files.require_normalized and not result.get("normalization_verified"):
        out.append(f"{cid}: input normalization not verified")
    if result.get("normalization_verified") and any(
        result.get(k) != result.get("input_sha256") for k in _INPUT_HASH_FIELDS
    ):
        out.append(f"{cid}: the engines did not receive identical normalized bytes")
    return out


def oracle_violations(files: CaseFiles) -> list[str]:
    """Re-read oracle body md5 and focus equal the manifest and ``test.toml``."""
    cid, case = files.case.id, files.case
    out: list[str] = []
    md5 = md5_of_body(files.oracle)
    if md5 != case.result.get("oracle_body_md5"):
        out.append(f"{cid}: oracle body md5 {md5} != recorded oracle_body_md5")
    if md5 != files.config.get("compare", {}).get("body_md5"):
        out.append(f"{cid}: oracle body md5 {md5} != test.toml [compare] body_md5")
    focus = case.focus
    if focus is None:
        return [*out, f"{cid}: executed case without focus"]
    value = focus_value(files.oracle, focus)
    if value != focus.expected:
        out.append(f"{cid}: oracle focus {value!r} != focus.expected")
    if value != case.result.get("oracle_focus"):
        out.append(f"{cid}: oracle focus {value!r} != recorded oracle_focus")
    return out


def vepyr_violations(files: CaseFiles) -> list[str]:
    """The recorded vepyr body md5 matches the re-read vepyr output.

    ``PASS``: vepyr's body equals the oracle, so the md5 must equal the re-read
    oracle md5. ``FAIL``: vepyr's output is committed as
    ``failures/<id>/actual_output.vcf``; its re-read md5 must equal the record
    and differ from the oracle. ``ERROR``: no vepyr md5 may be recorded.
    """
    cid, case = files.case.id, files.case
    recorded = case.result.get("vepyr_body_md5")
    match case.status:
        case Status.PASS:
            oracle = md5_of_body(files.oracle)
            if recorded != oracle:
                return [f"{cid}: PASS but vepyr_body_md5 {recorded} != oracle {oracle}"]
        case Status.FAIL:
            path = files.actual_output
            if not path.is_file():
                return [f"{cid}: FAIL but vepyr output {path} is not committed"]
            actual = md5_of_body(path)
            if recorded != actual:
                return [
                    (
                        f"{cid}: vepyr_body_md5 {recorded} != md5 {actual} "
                        f"of re-read vepyr output {path}"
                    )
                ]
            if actual == md5_of_body(files.oracle):
                return [f"{cid}: FAIL but vepyr output body equals the oracle"]
        case Status.ERROR:
            if recorded is not None:
                return [f"{cid}: ERROR with a recorded vepyr_body_md5"]
        case other:
            return [f"{cid}: status {other} is not an executed status"]
    return []


def via_cli_record_violations(case: Case) -> list[str]:
    """Structure of a result recorded through ``./run_tests --via-cli`` (#232).

    The last command carries the classified ``verdict``; it must agree with the
    statuses and the exit code (0 PASS, 8 FAIL, other ERROR).
    """
    cid, result = case.id, case.result
    commands = result.get("commands", [])
    if len(commands) != _COMMANDS:
        return [f"{cid}: {len(commands)} commands recorded, expected {_COMMANDS}"]
    out: list[str] = []
    if any(c.get("exit") != 0 for c in commands[:2]):
        out.append(f"{cid}: normalize_input or bless exited non-zero")
    last = commands[2]
    argv = last.get("argv", [])
    if not argv or argv[0] != "./run_tests" or "--via-cli" not in argv:
        out.append(f"{cid}: last command is not ./run_tests --via-cli")
    if "verdict" not in last:
        out.append(f"{cid}: last command has no verdict")
    elif not last["verdict"] == result.get("status") == case.raw.get("status"):
        out.append(f"{cid}: verdict, result.status and status disagree")
    code, runnable = last.get("exit"), result.get("runnable")
    match case.raw.get("status"):
        case Status.PASS:
            if code != 0 or not runnable:
                out.append(f"{cid}: PASS needs exit 0 and runnable")
        case Status.FAIL:
            if code != MISMATCH_EXIT or not runnable:
                out.append(f"{cid}: FAIL needs exit {MISMATCH_EXIT} and runnable")
        case Status.ERROR:
            if code == 0 or runnable:
                out.append(f"{cid}: ERROR needs a non-zero exit and not runnable")
        case other:
            out.append(f"{cid}: unexpected status {other}")
    return out


def record_violations(case: Case) -> list[str]:
    """Structure of a run record (via ``./run_tests`` or a direct vepyr run)."""
    if _is_via_cli(case.result):
        return via_cli_record_violations(case)
    cid, result = case.id, case.result
    commands = result.get("commands", [])
    if len(commands) != _COMMANDS:
        return [f"{cid}: {len(commands)} commands recorded, expected {_COMMANDS}"]
    out: list[str] = []
    if len(str(result.get("vepyr_sha", ""))) != _GIT_SHA_LEN:
        out.append(f"{cid}: vepyr_sha is not a {_GIT_SHA_LEN}-hex commit")
    if any(c.get("exit") != 0 for c in commands[:2]):
        out.append(f"{cid}: normalize_input or bless exited non-zero")
    if "--everything" not in commands[2].get("argv", []):
        out.append(f"{cid}: vepyr was not run with --everything")
    runnable = result.get("runnable")
    if case.status is Status.ERROR:
        if runnable:
            out.append(f"{cid}: ERROR but runnable")
    elif not runnable or commands[2].get("exit") != 0:
        out.append(f"{cid}: {case.status} needs a runnable vepyr run with exit 0")
    if result.get("status") != case.raw.get("status"):
        out.append(f"{cid}: result.status disagrees with status")
    return out


_CASE_INVARIANTS: Final[tuple[Callable[[CaseFiles], list[str]], ...]] = (
    test_toml_violations,
    input_violations,
    oracle_violations,
    vepyr_violations,
)


def _case_violations(
    case: Case, *, data_dir: Path, failures: Path, require_normalized: bool
) -> list[str]:
    directory = data_dir / case.directory_name
    try:
        config = tomllib.loads((directory / "test.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [f"{case.id}: unreadable test.toml: {exc}"]
    files = CaseFiles(
        case=case,
        directory=directory,
        config=config,
        failures=failures,
        require_normalized=require_normalized,
    )
    out = record_violations(case)
    for invariant in _CASE_INVARIANTS:
        try:
            out += invariant(files)
        except (CampaignError, OSError) as exc:
            out.append(f"{case.id}: {invariant.__name__}: {exc}")
    return out


def check(
    settings: Settings,
    *,
    require_complete: bool = False,
    require_normalized: bool = False,
) -> CheckReport:
    """Check every case of the manifest; never stops at the first violation.

    Raises:
        CampaignError: The manifest cannot be read at all.
    """
    cases = load_cases(settings.manifest)
    report = CheckReport()
    report.counts.update(str(c.raw.get("status")) for c in cases)
    report.violations += manifest_violations(cases, require_complete=require_complete)
    for case in cases:
        if case.raw.get("status") not in {Status.PASS, Status.FAIL, Status.ERROR}:
            continue
        report.executed += 1
        report.violations += _case_violations(
            case,
            data_dir=settings.data_dir,
            failures=settings.failures,
            require_normalized=require_normalized,
        )
    if not report.executed:
        report.violations.append("no executed ports")
    return report
