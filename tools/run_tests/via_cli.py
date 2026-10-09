"""``./run_tests --via-cli``: annotate data-tests through ``python -m vepyr annotate``.

Complements the Rust ``data_dirs`` loop (#231): instead of calling the engine
library in-process, each selected data-test directory is annotated by the
user-facing vepyr command line, built at the engine REF ``engine.py`` resolves,
and the md5 of the output body (every line not starting with ``#``, line
terminators kept, exactly the ``tests/data_dirs.rs`` rule) is compared with
``[compare] body_md5``.

Body-mismatch contract (#231, relied on by #232): one stdout line per mismatching
named test, exactly ``MISMATCH <test-id> expected=<md5> actual=<md5>``, and exit
:attr:`Exit.MISMATCH` (8). Any other error (an :class:`UnmappableKey` is
:attr:`Exit.USAGE`, a failing vepyr build or run is :attr:`Exit.ENGINE`) wins and
sets the exit code; the ``MISMATCH`` lines of the compared directories are still
printed. A directory whose every run matched prints ``PASS <test-id>``.
An explicit top-level ``skip_reason`` prints ``SKIP <test-id>: <reason>``
instead of annotating; skipped directories never enter the passed list.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, TextIO

from fixture_skip import skip_reason

from run_tests.cli_argv import UnmappableKey, annotate_argv, effective_runs
from run_tests.verdict import Exit, RunTestsError

__all__ = [
    "BUILD_DIR",
    "CliBuild",
    "CliRunner",
    "Report",
    "VepyrBuilder",
    "body_md5",
    "build_vepyr",
    "default_runner",
    "run_dirs",
]

BUILD_DIR: Final[str] = ".vepyr_cli"
"""Per-REF builds live under ``<cache-dir>/.vepyr_cli/<vepyr sha>/``."""
_BUILD_JSON: Final[str] = "BUILD.json"
_VEPYR_GIT: Final[str] = "https://github.com/biodatageeks/vepyr.git"

CliRunner = Callable[[Sequence[str]], int]
"""``argv -> exit code`` of one ``<python> -m vepyr annotate ...`` run; injectable.

It may raise :class:`RunTestsError` (e.g. :attr:`Exit.ENGINE`) itself.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class CliBuild:
    """The vepyr build ``--via-cli`` runs: an interpreter with vepyr installed."""

    python: Path
    vepyr_sha: str
    wheel_sha256: str

    @property
    def label(self) -> str:
        """One line for the summary detail."""
        return (
            f"vepyr CLI {self.vepyr_sha[:12]} via {self.python} "
            f"(wheel sha256 {self.wheel_sha256})"
        )


VepyrBuilder = Callable[[str, Path], CliBuild]
"""``(vepyr sha, cache root) -> CliBuild``; injected by tests (no build, no network)."""


def body_md5(vcf: bytes) -> str:
    """md5 of the body: lines not starting with ``#``, terminators kept.

    The same rule as ``tests/data_dirs.rs`` (``body_lines``/``body_md5``) and
    ``grep -v '^#' FILE | md5sum``.
    """
    digest = hashlib.md5()
    for line in vcf.splitlines(keepends=True):
        if not line.startswith(b"#"):
            digest.update(line)
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check(argv: Sequence[str], *, cwd: Path | None = None) -> None:
    """Run one build step; a failure is :attr:`Exit.ENGINE`."""
    completed = subprocess.run(
        list(argv), cwd=cwd, check=False, capture_output=True, text=True
    )
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "failed").strip()
        raise RunTestsError(
            Exit.ENGINE,
            f"vepyr CLI build: {' '.join(argv[:3])}… exited "
            f"{completed.returncode}: {err[-300:]}",
        )


def build_vepyr(sha: str, cache_root: Path) -> CliBuild:
    """Build vepyr at ``sha`` once into ``<cache_root>/.vepyr_cli/<sha>/`` and reuse it.

    Steps (each failure is :attr:`Exit.ENGINE`): fetch exactly ``sha`` of
    biodatageeks/vepyr into ``src/``, ``uv build --wheel`` (maturin, using the
    dfbf/formats revisions vepyr's own ``Cargo.lock`` pins), ``uv venv`` and
    ``uv pip install`` the wheel; ``BUILD.json`` records the sha and the wheel's
    sha256. A directory with a ``BUILD.json`` for ``sha`` is reused as is.
    """
    base = cache_root / BUILD_DIR / sha
    record = base / _BUILD_JSON
    python = base / "venv" / "bin" / "python"
    if record.is_file() and python.is_file():
        data = json.loads(record.read_text(encoding="utf-8"))
        if data.get("vepyr_sha") == sha:
            return CliBuild(
                python=python, vepyr_sha=sha, wheel_sha256=str(data["wheel_sha256"])
            )
    src, wheels = base / "src", base / "wheel"
    src.mkdir(parents=True, exist_ok=True)
    if not (src / ".git").is_dir():
        _check(["git", "init", "-q", str(src)])
    _check(["git", "fetch", "-q", "--depth", "1", _VEPYR_GIT, sha], cwd=src)
    _check(["git", "checkout", "-q", "--detach", "FETCH_HEAD"], cwd=src)
    _check(["uv", "build", "--wheel", "--out-dir", str(wheels), str(src)])
    built = sorted(wheels.glob("vepyr-*.whl"), key=lambda p: p.stat().st_mtime)
    if not built:
        raise RunTestsError(Exit.ENGINE, f"vepyr CLI build: no wheel in {wheels}")
    wheel = built[-1]
    _check(["uv", "venv", "-q", "--allow-existing", str(base / "venv")])
    _check(["uv", "pip", "install", "-q", "--python", str(python), str(wheel)])
    build = CliBuild(python=python, vepyr_sha=sha, wheel_sha256=_sha256(wheel))
    record.write_text(
        json.dumps(
            {"vepyr_sha": sha, "wheel": wheel.name, "wheel_sha256": build.wheel_sha256},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return build


def default_runner(argv: Sequence[str]) -> int:
    """Run one ``vepyr annotate`` subprocess; its stderr goes to ours."""
    return subprocess.run(list(argv), check=False).returncode


@dataclass(slots=True)
class Report:
    """What :func:`run_dirs` saw, in named-test order."""

    passed: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)
    errors: list[tuple[str, RunTestsError]] = field(default_factory=list)

    @property
    def code(self) -> Exit:
        """The exit code: first non-mismatch error, else MISMATCH, else OK."""
        if self.errors:
            return self.errors[0][1].code
        return Exit.MISMATCH if self.mismatched else Exit.OK

    @property
    def detail(self) -> str:
        """One-line summary detail."""
        return (
            f"via-cli: {len(self.passed)} pass, {len(self.mismatched)} mismatch, "
            f"{len(self.errors)} error, {len(self.skipped)} skipped"
        )


def _load(dir_: Path) -> tuple[list[dict[str, object]], str, str | None, list[str]]:
    """Effective runs, expected body md5, skip reason and named test IDs."""
    try:
        doc = tomllib.loads((dir_ / "test.toml").read_text(encoding="utf-8"))
        vepyr = doc["vepyr"]
        expected = doc["compare"]["body_md5"]
        reason = skip_reason(doc)
        entries = doc.get("tests")
        ids = [dir_.name] if entries is None else [entry["id"] for entry in entries]
        if not ids or any(not isinstance(id_, str) or not id_.strip() for id_ in ids):
            raise ValueError("tests must contain non-empty test IDs")
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate test IDs")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RunTestsError(
            Exit.USAGE, f"[{dir_.name}] test.toml unusable for --via-cli: {exc!r}"
        ) from exc
    return effective_runs(vepyr, doc.get("vepyr_run", ())), str(expected), reason, ids


@dataclass(frozen=True, slots=True, kw_only=True)
class _Context:
    """What every directory's runs share."""

    build: CliBuild
    cache_root: Path
    fasta: Path
    runner: CliRunner
    out: TextIO


def _argvs(
    dir_: Path, runs: Sequence[dict[str, object]], ctx: _Context, scratch: Path
) -> list[list[str]]:
    """Map every run, or raise :attr:`Exit.USAGE` naming each unmappable run."""
    argvs: list[list[str]] = []
    problems: list[str] = []
    for index, settings in enumerate(runs, start=1):
        try:
            mapped = annotate_argv(
                settings,
                input_vcf=dir_ / "input.vcf",
                output_vcf=scratch / f"run{index}.vcf",
                cache_root=ctx.cache_root,
                fasta=ctx.fasta,
            )
        except UnmappableKey as exc:
            problems.append(f"[{dir_.name}] run {index}/{len(runs)}: {exc}")
        else:
            argvs.append([str(ctx.build.python), "-m", "vepyr", *mapped])
    if problems:
        raise RunTestsError(Exit.USAGE, "\n".join(problems))
    return argvs


def _compare_dir(
    dir_: Path,
    ctx: _Context,
    runs: Sequence[dict[str, object]],
    expected: str,
    ids: Sequence[str],
) -> tuple[str, str | None]:
    """Run every run of ``dir_``: ``(expected md5, first mismatching md5 or None)``.

    Raises:
        RunTestsError: exit 2 for an unusable or unmappable ``test.toml``, exit 6
            when vepyr fails or writes no output.
    """
    with tempfile.TemporaryDirectory(prefix="run_tests-via-cli-") as scratch:
        argvs = _argvs(dir_, runs, ctx, Path(scratch))
        mismatch: str | None = None
        for index, argv in enumerate(argvs, start=1):
            where = f"[{dir_.name}] run {index}/{len(argvs)}"
            if len(argvs) > 1:
                for id_ in ids:
                    print(
                        f"RUN {id_} | run {index}/{len(argvs)}",
                        file=ctx.out,
                        flush=True,
                    )
            if (code := ctx.runner(argv)) != 0:
                raise RunTestsError(
                    Exit.ENGINE, f"{where}: vepyr annotate exited {code}"
                )
            output = Path(argv[argv.index("--output_file") + 1])
            try:
                actual = body_md5(output.read_bytes())
            except OSError as exc:
                raise RunTestsError(Exit.ENGINE, f"{where}: no output: {exc}") from exc
            if actual != expected and mismatch is None:
                mismatch = actual
    return expected, mismatch


def _progress(total: int, report: Report, status: str, out: TextIO) -> None:
    """A line-oriented bar of completed named tests."""
    passed = len(report.passed)
    failed = len(report.mismatched) + len(report.errors)
    skipped = len(report.skipped)
    done = passed + failed + skipped
    filled = done * 20 // total if total else 0
    if status not in {"START", "DONE"}:
        print(status, file=out, flush=True)
        status = ""
    suffix = f" | {status}" if status else ""
    print(
        f"[{'#' * filled}{'-' * (20 - filled)}] {done}/{total} tests | "
        f"{passed} passed, {failed} failed, {skipped} skipped{suffix}",
        file=out,
        flush=True,
    )


def run_dirs(
    dirs: Iterable[Path],
    *,
    build: CliBuild,
    cache_root: Path,
    fasta: Path,
    runner: CliRunner,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> Report:
    """Annotate each of ``dirs`` via the vepyr CLI and compare body md5s.

    Prints RUN and a progress result for every named test. Tests sharing a fixture
    reuse its annotation and inherit the complete body comparison result.
    Every error goes to ``err`` (stderr); a directory with an
    unmappable run is not run at all (each unmappable run is reported). Every
    directory is attempted, whatever happened to the previous ones.
    """
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    ctx = _Context(
        build=build, cache_root=cache_root, fasta=fasta, runner=runner, out=out
    )
    cases = []
    for dir_ in dirs:
        try:
            data = _load(dir_)
            cases.append((dir_, data[3], data))
        except RunTestsError as exc:
            cases.append((dir_, [dir_.name], exc))
    total = sum(len(ids) for _, ids, _ in cases)
    report = Report()
    _progress(total, report, "START", out)
    for dir_, ids, data in cases:
        for id_ in ids:
            print(f"RUN {id_}", file=out, flush=True)
        try:
            if isinstance(data, RunTestsError):
                raise data
            runs, expected, reason, _ = data
            if reason is not None:
                for id_ in ids:
                    report.skipped.append((id_, reason))
                    _progress(total, report, f"SKIP {id_}: {reason}", out)
                continue
            expected, mismatch = _compare_dir(dir_, ctx, runs, expected, ids)
        except RunTestsError as exc:
            for line in str(exc).splitlines():
                print(
                    f"run_tests: error ({exc.code.name.lower()}, exit "
                    f"{int(exc.code)}): {line}",
                    file=err,
                    flush=True,
                )
            for id_ in ids:
                report.errors.append((id_, exc))
                _progress(total, report, f"ERROR {id_}", out)
            continue
        for id_ in ids:
            if mismatch is None:
                report.passed.append(id_)
                _progress(total, report, f"PASS {id_}", out)
            else:
                report.mismatched.append(id_)
                _progress(
                    total,
                    report,
                    f"MISMATCH {id_} expected={expected} actual={mismatch}",
                    out,
                )
    _progress(total, report, "DONE", out)
    return report
