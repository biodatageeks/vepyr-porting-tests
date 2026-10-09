"""``--vepyr REF`` — resolve biodatageeks/vepyr and path-patch its dfbf/formats ladder.

Only the public biodatageeks crates named by ``REF``'s own ``Cargo.toml``; no
``test-internals`` overlays. The engine is a run-time parameter:
committed ``Cargo.toml`` floats on biodatageeks ``master``; this module materialises the
exact revisions named by ``REF``'s ``Cargo.toml`` and writes a ``cargo --config`` file
with path ``[patch]`` tables. ``Cargo.lock`` is restored from git around the run.
"""

from __future__ import annotations

import base64
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol, TextIO

from run_tests.verdict import Exit, RunTestsError

__all__ = [
    "DEFAULT_GIT_TIMEOUT",
    "DFBF_GIT",
    "FORMATS_GIT",
    "GIT_TIMEOUT_ENV",
    "SRC_ENV",
    "VEPYR_REPO",
    "Checkout",
    "EnginePlan",
    "GhApi",
    "GhCli",
    "LockGuard",
    "WatchedRunner",
    "default_src_root",
    "engine_toml",
    "materialise",
    "mirror_timeout",
    "resolve",
    "resolve_sha",
    "validate_ref",
    "workspace_crate_dirs",
]

VEPYR_REPO: Final[str] = "biodatageeks/vepyr"
DFBF_GIT: Final[str] = "https://github.com/biodatageeks/datafusion-bio-functions.git"
FORMATS_GIT: Final[str] = "https://github.com/biodatageeks/datafusion-bio-formats.git"
_DFBF_CRATE: Final[str] = "datafusion-bio-function-vep"
_FORMATS_CRATES: Final[tuple[str, ...]] = (
    "datafusion-bio-format-ensembl-cache",
    "datafusion-bio-format-vcf",
)
# Every crate of the formats workspace is path-patched, not just the two named above
# (issue #21): `datafusion-bio-function-vep` also pulls siblings such as
# `datafusion-bio-format-core`/`-bed`/`-gff`, and patching only a subset leaves the
# rest resolved from git — two copies of one crate name in a single graph.
_FORMATS_PREFIX: Final[str] = "datafusion-bio-format-"
_TIMEOUT: Final[int] = 180
"""Seconds allowed for a *short* subprocess (``gh api``, ``rev-parse``, ...)."""
DEFAULT_GIT_TIMEOUT: Final[float] = 3600.0
"""Default seconds allowed for one mirror ``clone --mirror``/``fetch``."""
GIT_TIMEOUT_ENV: Final[str] = "RUN_TESTS_GIT_TIMEOUT"
SRC_ENV: Final[str] = "RUN_TESTS_SRC"
HEARTBEAT_EVERY: Final[float] = 30.0
"""Seconds between two heartbeat lines while a mirror network call runs (#256)."""
_POLL_EVERY: Final[float] = 1.0
# Ladder checkouts need Rust source only. `datafusion-bio-functions` also carries
# git-lfs-tracked benchmark fixtures (`vep-benchmark/data/golden/cache/**`) that no
# workspace crate reads; smudging them made a transient LFS download failure abort the
# whole `--vepyr` run (issue #61). Leave every LFS path as its pointer file instead.
_NO_SMUDGE: Final[Mapping[str, str]] = {"GIT_LFS_SKIP_SMUDGE": "1"}
_SHA: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{7,40}$")

#: Characters a git ref may consist of. ``/`` is deliberately allowed —
#: ``feature/x`` is the common case — but every shape that could redirect the
#: ``repos/<repo>/commits/<ref>`` API path elsewhere (``..``, ``?``, ``#``,
#: a leading ``-``) is rejected by :func:`validate_ref` below.
_REF_CHARS: Final[re.Pattern[str]] = re.compile(r"\A[A-Za-z0-9._/+-]+\Z")
_REF_MAX: Final[int] = 255


def validate_ref(ref: str) -> str:
    """Return ``ref`` if it is a well-formed git ref, else raise a usage error.

    The allowlist follows ``git check-ref-format`` closely enough to keep the
    GitHub API path ``repos/<repo>/commits/<ref>`` intact: slashes are legal
    (``feature/x``), while path traversal (``..``), a leading ``-``, and any
    URL-significant character (``?``, ``#``, ``%``, ``&``, whitespace) are not.

    Args:
        ref: The raw value of ``--vepyr`` as given on the command line.

    Returns:
        The same string, unchanged, once it has been accepted.

    Raises:
        RunTestsError: With :attr:`Exit.USAGE` when ``ref`` is malformed.
    """

    def reject(why: str) -> RunTestsError:
        return RunTestsError(
            Exit.USAGE, f"--vepyr {ref!r}: not a valid git ref ({why})"
        )

    if not ref:
        raise reject("empty")
    if len(ref) > _REF_MAX:
        raise reject(f"longer than {_REF_MAX} characters")
    if not _REF_CHARS.match(ref):
        raise reject("only letters, digits and '. _ / + -' are allowed")
    if ref.startswith("-"):
        raise reject("starts with '-'")
    if ".." in ref:
        raise reject("contains '..'")
    if ref.startswith("/") or ref.endswith("/") or "//" in ref:
        raise reject("malformed '/' component")
    if ref.endswith(".") or ref.endswith(".lock"):
        raise reject("ends with '.' or '.lock'")
    for part in ref.split("/"):
        if part.startswith(".") or part.endswith(".lock"):
            raise reject(f"bad path component {part!r}")
    return ref


class GhApi(Protocol):
    """Minimal GitHub contents/commits API used by :func:`resolve`."""

    def get(self, path: str) -> Any: ...


class GhError(Exception):
    """A GitHub API failure with a short message."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


@dataclass(frozen=True, slots=True)
class GhCli:
    """``gh api`` backed :class:`GhApi` (default production client)."""

    run: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = subprocess.run

    def get(self, path: str) -> Any:
        completed = self.run(
            ["gh", "api", path],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or "gh api failed").strip()
            raise GhError(path, err[:300])
        return json.loads(completed.stdout)


def _default_run(
    argv: Sequence[str], **kwargs: Any
) -> subprocess.CompletedProcess[str]:
    """``subprocess.run`` with a stable signature for injection in tests."""
    return subprocess.run(list(argv), **kwargs)


Runner = Callable[..., subprocess.CompletedProcess[str]]


def default_src_root(environ: Mapping[str, str] | None = None) -> Path:
    """Shared engine-mirror cache, outside any checkout (#256).

    ``$RUN_TESTS_SRC`` wins when set; otherwise
    ``${XDG_CACHE_HOME:-$HOME/.cache}/vepyr-porting-tests/run_tests/src``, so every
    worktree and every run reuses the same bare mirrors and a second run is an
    incremental ``git fetch`` rather than a fresh ``clone --mirror``.

    Args:
        environ: Environment to read; ``None`` means :data:`os.environ`.

    Returns:
        The source root (not created here).
    """
    env = os.environ if environ is None else environ
    if raw := env.get(SRC_ENV):
        return Path(raw).expanduser()
    match env.get("XDG_CACHE_HOME"):
        case str(xdg) if xdg:
            cache = Path(xdg).expanduser()
        case _:
            home = env.get("HOME")
            cache = (Path(home) if home else Path.home()) / ".cache"
    return cache / "vepyr-porting-tests" / "run_tests" / "src"


def mirror_timeout(
    flag: float | None = None, environ: Mapping[str, str] | None = None
) -> float:
    """Seconds allowed for one mirror ``clone --mirror`` / ``fetch`` (#256).

    Precedence: ``--git-timeout`` (``flag``), then ``$RUN_TESTS_GIT_TIMEOUT``, then
    :data:`DEFAULT_GIT_TIMEOUT`. Short calls keep the fixed 180 s ``_TIMEOUT``.

    Args:
        flag: The ``--git-timeout`` value, or ``None`` when not given.
        environ: Environment to read; ``None`` means :data:`os.environ`.

    Returns:
        A positive number of seconds.

    Raises:
        RunTestsError: With :attr:`Exit.USAGE` for a non-numeric or non-positive value.
    """
    if flag is not None:
        source, raw = "--git-timeout", str(flag)
    else:
        environ = os.environ if environ is None else environ
        if not (raw := environ.get("RUN_TESTS_GIT_TIMEOUT", "").strip()):
            return DEFAULT_GIT_TIMEOUT
        source = GIT_TIMEOUT_ENV
    try:
        value = float(raw)
    except ValueError:
        value = float("nan")
    if not value > 0 or value == float("inf"):
        raise RunTestsError(
            Exit.USAGE, f"{source}={raw!r}: expected a positive number of seconds"
        )
    return value


class Process(Protocol):
    """The slice of :class:`subprocess.Popen` that :class:`WatchedRunner` polls."""

    returncode: int | None

    def poll(self) -> int | None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


def _tree_bytes(path: Path) -> int:
    """Total size of the regular files under ``path`` (0 when it does not exist)."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def _watched_path(argv: Sequence[str]) -> Path | None:
    """The directory a mirror git call grows: ``-C <dir>``, else a clone's last arg."""
    if "-C" in argv[:-1]:
        return Path(argv[list(argv).index("-C") + 1])
    if "clone" in argv:
        return Path(argv[-1])
    return None


@dataclass(frozen=True, slots=True, kw_only=True)
class WatchedRunner:
    """A :data:`Runner` for long mirror calls that prints a heartbeat to stderr (#256).

    ``subprocess.run`` blocks silently, so a 10-minute ``clone --mirror`` on a slow
    link looked like a hang. This runner spawns the child, polls it, and every
    ``interval`` seconds prints ``elapsed`` and the current size of the directory the
    call grows. Clock, sleep, spawn and stream are injectable so tests need no real
    time or process. On ``timeout`` the child is killed and
    :class:`subprocess.TimeoutExpired` is raised, exactly like ``subprocess.run``.
    """

    spawn: Callable[..., Process] = subprocess.Popen
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    stream: TextIO | None = None
    interval: float = HEARTBEAT_EVERY
    poll_every: float = _POLL_EVERY

    def __call__(
        self, argv: Sequence[str], *, timeout: float | None = None, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        """Run ``argv`` to completion (stderr captured, text mode).

        Args:
            argv: The command line.
            timeout: Seconds before the child is killed; ``None`` waits forever.
            **kwargs: ``env`` is forwarded; the ``subprocess.run`` style flags
                (``capture_output``, ``text``, ``check``) are accepted and ignored.

        Returns:
            The completed process; ``stdout`` is empty, ``stderr`` holds git's stderr.

        Raises:
            subprocess.TimeoutExpired: When ``timeout`` elapses first.
        """
        out = self.stream if self.stream is not None else sys.stderr
        watched = _watched_path(argv)
        extra = {"env": kwargs["env"]} if "env" in kwargs else {}
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as err:
            proc = self.spawn(
                list(argv), stdout=subprocess.DEVNULL, stderr=err, text=True, **extra
            )
            start = self.clock()
            beat = start + self.interval
            while (code := proc.poll()) is None:
                now = self.clock()
                if timeout is not None and now - start >= timeout:
                    proc.kill()
                    proc.wait()
                    raise subprocess.TimeoutExpired(list(argv), timeout)
                if now >= beat:
                    size = _tree_bytes(watched) if watched is not None else 0
                    print(
                        f"run_tests: ... still running after {now - start:.0f} s "
                        f"({size / 2**20:.1f} MiB in {watched})",
                        file=out,
                        flush=True,
                    )
                    beat += self.interval
                self.sleep(self.poll_every)
            err.seek(0)
            return subprocess.CompletedProcess(list(argv), code, "", err.read())


@dataclass(frozen=True, slots=True, kw_only=True)
class Checkout:
    """One local detached checkout of an engine repo."""

    name: str
    path: Path
    git_url: str
    rev: str
    head: str


@dataclass(frozen=True, slots=True, kw_only=True)
class EnginePlan:
    """Resolved ``--vepyr`` ladder ready for path patches."""

    ref: str
    vepyr_sha: str
    dfbf: Checkout
    formats: Checkout
    config_text: str

    @property
    def label(self) -> str:
        v = f"vepyr={self.ref}@{self.vepyr_sha[:12]}"
        d = f"dfbf={self.dfbf.head[:12]}"
        f = f"formats={self.formats.head[:12]}"
        return f"{v} {d} {f}"


class GitTimeout(RunTestsError):
    """A mirror network call exceeded :func:`mirror_timeout` (exit 6)."""


def _git(
    run: Runner,
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout: float = _TIMEOUT,
) -> str:
    """Run one git command; ``env`` (if given) is overlaid on ``os.environ``.

    Passing ``env=None`` leaves the child environment untouched — no ``env=``
    keyword reaches ``run`` at all — so every existing call site is unaffected.
    ``timeout`` defaults to the short ``_TIMEOUT``; only the mirror network calls
    pass the longer :func:`mirror_timeout`, and for those a
    :class:`subprocess.TimeoutExpired` becomes an exit-6 :class:`RunTestsError`
    naming the limit and how to raise it.
    """
    extra = {} if env is None else {"env": {**os.environ, **env}}
    try:
        completed = run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            **extra,
        )
    except subprocess.TimeoutExpired as exc:
        if timeout == _TIMEOUT:
            raise
        raise GitTimeout(
            Exit.ENGINE,
            f"git {' '.join(argv[1:4])}…: timed out after {timeout:g} s "
            f"(raise it with --git-timeout SECONDS or {GIT_TIMEOUT_ENV})",
        ) from exc
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "git failed").strip()
        raise RunTestsError(Exit.ENGINE, f"git {' '.join(argv[1:4])}…: {err[:300]}")
    return completed.stdout.strip()


def _resolve_sha(api: GhApi, ref: str) -> str:
    """Dereference ``ref`` (tag / branch / sha) on biodatageeks/vepyr."""
    validate_ref(ref)
    try:
        payload = api.get(f"repos/{VEPYR_REPO}/commits/{ref}")
    except GhError as exc:
        raise RunTestsError(
            Exit.ENGINE, f"--vepyr {ref}: cannot resolve on {VEPYR_REPO}: {exc.message}"
        ) from exc
    sha = payload.get("sha") if isinstance(payload, dict) else None
    if not isinstance(sha, str) or not _SHA.match(sha):
        raise RunTestsError(Exit.ENGINE, f"--vepyr {ref}: commits API returned no sha")
    return sha


def resolve_sha(api: GhApi, ref: str) -> str:
    """Public :func:`_resolve_sha`: the 40-char sha ``ref`` names on biodatageeks/vepyr.

    Raises:
        RunTestsError: exit 2 for a malformed ref, exit 6 when it does not resolve.
    """
    return _resolve_sha(api, ref)


def _read_cargo_toml(api: GhApi, sha: str) -> dict[str, Any]:
    try:
        payload = api.get(f"repos/{VEPYR_REPO}/contents/Cargo.toml?ref={sha}")
    except GhError as exc:
        raise RunTestsError(
            Exit.ENGINE, f"--vepyr: Cargo.toml@{sha[:12]} unreadable: {exc.message}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("encoding") != "base64":
        raise RunTestsError(
            Exit.ENGINE,
            f"--vepyr: Cargo.toml@{sha[:12]} not base64 content",
        )
    raw = base64.b64decode(payload["content"]).decode("utf-8")
    try:
        return tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise RunTestsError(
            Exit.ENGINE, f"--vepyr: Cargo.toml@{sha[:12]} does not parse: {exc}"
        ) from exc


def _dep_spec(manifest: Mapping[str, Any], name: str, *, ref: str) -> dict[str, Any]:
    deps = manifest.get("dependencies")
    if not isinstance(deps, dict) or name not in deps:
        raise RunTestsError(
            Exit.ENGINE,
            f"--vepyr {ref}: Cargo.toml has no dependencies.{name}",
        )
    entry = deps[name]
    if not isinstance(entry, dict):
        raise RunTestsError(
            Exit.ENGINE, f"--vepyr {ref}: dependencies.{name} is not a table"
        )
    return entry


def _git_rev(entry: Mapping[str, Any], *, name: str, ref: str) -> tuple[str, str]:
    """Return ``(git_url, rev_or_tag)`` for a git dependency entry."""
    git = entry.get("git")
    if not isinstance(git, str) or not git:
        raise RunTestsError(
            Exit.ENGINE, f"--vepyr {ref}: dependencies.{name} has no git = URL"
        )
    for key in ("rev", "tag", "branch"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return git, value
    raise RunTestsError(
        Exit.ENGINE,
        f"--vepyr {ref}: dependencies.{name} has no rev/tag/branch",
    )


def workspace_crate_dirs(checkout: Path) -> dict[str, Path]:
    """Crate name → directory for every package in the workspace at ``checkout``."""
    root_manifest = checkout / "Cargo.toml"
    if not root_manifest.is_file():
        raise RunTestsError(Exit.ENGINE, f"{checkout}: no Cargo.toml")
    try:
        manifest = tomllib.loads(root_manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RunTestsError(
            Exit.ENGINE,
            f"{checkout}: Cargo.toml unreadable: {exc}",
        ) from exc
    workspace = manifest.get("workspace", {})
    members = workspace.get("members", []) if isinstance(workspace, dict) else []
    excludes = workspace.get("exclude", []) if isinstance(workspace, dict) else []
    exclude = {(checkout / pattern).resolve() for pattern in excludes}
    dirs: list[Path] = [checkout] if "package" in manifest else []
    if isinstance(members, list):
        for pattern in members:
            if not isinstance(pattern, str):
                continue
            for candidate in checkout.glob(pattern):
                if candidate.is_dir() and (candidate / "Cargo.toml").is_file():
                    if candidate.resolve() not in exclude:
                        dirs.append(candidate)
    out: dict[str, Path] = {}
    for crate_dir in dirs:
        try:
            member_text = (crate_dir / "Cargo.toml").read_text(encoding="utf-8")
            member = tomllib.loads(member_text)
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise RunTestsError(
                Exit.ENGINE, f"{crate_dir}: member Cargo.toml unreadable: {exc}"
            ) from exc
        pkg = member.get("package")
        if isinstance(pkg, dict) and isinstance(name := pkg.get("name"), str):
            out[name] = crate_dir
    return out


@contextmanager
def _mirror_lock(mirror: Path) -> Iterator[None]:
    """Hold an exclusive ``fcntl.flock`` on ``<mirror>.lock`` for the block.

    The mirror cache is shared by every worktree (#256), so two overlapping runs
    must not clone into, or fetch into, the same bare repo at once. The per-sha
    trees need no lock: they are immutable once created.
    """
    mirror.parent.mkdir(parents=True, exist_ok=True)
    lock = mirror.with_name(f"{mirror.name}.lock")
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _clone_mirror(*, git_url: str, mirror: Path, run: Runner, timeout: float) -> None:
    """Clone ``git_url`` atomically: into ``<mirror>.tmp-<pid>``, then rename.

    A clone that fails or times out leaves no ``mirror`` behind, so the next run
    clones again instead of fetching into a half-written repo. Stale ``.tmp-*``
    siblings of crashed runs are removed first (safe: the caller holds the lock).
    """
    for stale in mirror.parent.glob(f"{mirror.name}.tmp-*"):
        shutil.rmtree(stale, ignore_errors=True)
    tmp = mirror.with_name(f"{mirror.name}.tmp-{os.getpid()}")
    print(
        f"run_tests: cloning {git_url} mirror into {mirror} (timeout {timeout:g} s)",
        file=sys.stderr,
        flush=True,
    )
    try:
        _git(
            run,
            ["git", "clone", "--quiet", "--mirror", git_url, str(tmp)],
            timeout=timeout,
        )
        if tmp.exists():  # a stub runner may report success without a directory
            tmp.rename(mirror)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _mirror_sha(
    *,
    name: str,
    git_url: str,
    rev: str,
    mirror: Path,
    run: Runner,
    mirror_run: Runner | None = None,
    git_timeout: float | None = None,
) -> str:
    """Update the shared bare mirror of ``git_url`` and resolve ``rev`` to a sha.

    Network calls (``clone --mirror`` and both fetches) go through ``mirror_run``
    (default ``run``) with :func:`mirror_timeout`; ``rev-parse`` keeps ``_TIMEOUT``.
    """
    net = run if mirror_run is None else mirror_run
    limit = mirror_timeout(git_timeout)
    git = ["git", "-C", str(mirror)]
    verify = [*git, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"]
    with _mirror_lock(mirror):
        if not mirror.exists():
            _clone_mirror(git_url=git_url, mirror=mirror, run=net, timeout=limit)
        print(
            f"run_tests: fetching {git_url} into mirror {mirror} (timeout {limit:g} s)",
            file=sys.stderr,
            flush=True,
        )
        # A mirror fetch updates every ref, so named branches/tags cannot go stale.
        fetch = [*git, "fetch", "--quiet", "--prune", "--tags", "origin"]
        _git(net, fetch, timeout=limit)
        try:
            return _git(run, verify)
        except RunTestsError:
            pass
        # A sha on no branch (e.g. a PR head) needs an explicit single-rev fetch.
        # Its failure is not fatal by itself: the verify below names the problem.
        with _suppress_non_timeout():
            _git(net, [*git, "fetch", "--quiet", "origin", rev], timeout=limit)
        try:
            return _git(run, verify)
        except RunTestsError as exc:
            raise RunTestsError(
                Exit.ENGINE, f"{name}: cannot resolve rev {rev!r} in {mirror}: {exc}"
            ) from exc


@contextmanager
def _suppress_non_timeout() -> Iterator[None]:
    """Swallow a failed git call, but let a :class:`GitTimeout` propagate."""
    try:
        yield
    except GitTimeout:
        raise
    except RunTestsError:
        pass


def _checkout_repo(
    *,
    name: str,
    git_url: str,
    rev: str,
    target: Path,
    run: Runner,
    mirror_run: Runner | None = None,
    git_timeout: float | None = None,
) -> Checkout:
    """Materialise ``rev`` of ``git_url`` in a worktree keyed by its resolved sha.

    ``target`` is the per-repo cache root. Objects live once in ``target/git`` (a bare
    mirror); each revision gets its own immutable tree at ``target/<sha>`` cloned
    ``--shared`` from it. Nothing is ever re-checked-out in place, so two runs of
    different revisions — sequential or overlapping — cannot swap files under each
    other's live ``cargo`` build.

    ``mirror_run`` (default ``run``) executes the mirror network calls with
    :func:`mirror_timeout` (``git_timeout`` = the ``--git-timeout`` flag, if any).
    """
    sha = _mirror_sha(
        name=name,
        git_url=git_url,
        rev=rev,
        mirror=target / "git",
        run=run,
        mirror_run=mirror_run,
        git_timeout=git_timeout,
    )
    tree = target / sha
    fresh = not (tree / ".git").exists()
    if fresh:
        _git(
            run,
            [
                "git",
                "clone",
                "--quiet",
                "--shared",
                "--no-checkout",
                "--",
                str(target / "git"),
                str(tree),
            ],
            env=_NO_SMUDGE,
        )
    git = ["git", "-C", str(tree)]
    if fresh or _git(run, [*git, "rev-parse", "HEAD"]) != sha:
        _git(run, [*git, "checkout", "--quiet", "--detach", sha, "--"], env=_NO_SMUDGE)
    head = _git(run, [*git, "rev-parse", "HEAD"])
    if head != sha:
        raise RunTestsError(
            Exit.ENGINE, f"{name}: checkout {tree} is at {head}, expected {sha}"
        )
    return Checkout(name=name, path=tree, git_url=git_url, rev=rev, head=head)


def engine_toml(*, dfbf: Checkout, formats: Checkout) -> str:
    """Path-patch config for ``cargo --config`` (byte-stable)."""
    dfbf_crates = workspace_crate_dirs(dfbf.path)
    formats_crates = workspace_crate_dirs(formats.path)
    missing_dfbf = [c for c in (_DFBF_CRATE,) if c not in dfbf_crates]
    missing_fmt = [c for c in _FORMATS_CRATES if c not in formats_crates]
    if missing_dfbf:
        raise RunTestsError(
            Exit.ENGINE,
            f"dfbf checkout at {dfbf.path} missing crates: {', '.join(missing_dfbf)}",
        )
    if missing_fmt:
        raise RunTestsError(
            Exit.ENGINE,
            f"formats checkout at {formats.path} missing crates: "
            f"{', '.join(missing_fmt)}",
        )
    patched = sorted(n for n in formats_crates if n.startswith(_FORMATS_PREFIX))
    header = "# generated by ./run_tests — engine path-patch config"
    lines = [
        header,
        f"# dfbf@{dfbf.head[:12]} formats@{formats.head[:12]}",
        f"[patch.{json.dumps(DFBF_GIT)}]",
        f"{_DFBF_CRATE} = {{ path = {json.dumps(str(dfbf_crates[_DFBF_CRATE]))} }}",
        f"[patch.{json.dumps(FORMATS_GIT)}]",
    ]
    width = max(len(c) for c in patched)
    for name in patched:
        path_json = json.dumps(str(formats_crates[name]))
        lines.append(f"{name.ljust(width)} = {{ path = {path_json} }}")
    return "\n".join(lines) + "\n"


def resolve(
    ref: str,
    *,
    api: GhApi,
    src_root: Path,
    run: Runner | None = None,
    git_timeout: float | None = None,
) -> EnginePlan:
    """Resolve ``ref``, checkout the ladder, and build the cargo config text.

    Without an injected ``run``, mirror clone/fetch go through
    :class:`WatchedRunner` (heartbeat on stderr); an injected ``run`` serves both.
    """
    runner: Runner = run or _default_run
    mirror_runner: Runner = run or WatchedRunner()
    validate_ref(ref)
    sha = _resolve_sha(api, ref)
    manifest = _read_cargo_toml(api, sha)
    dfbf_entry = _dep_spec(manifest, _DFBF_CRATE, ref=ref)
    # Formats: prefer ensembl-cache entry's git/rev; vcf must agree.
    fmt_entry = _dep_spec(manifest, _FORMATS_CRATES[0], ref=ref)
    fmt_vcf = _dep_spec(manifest, _FORMATS_CRATES[1], ref=ref)
    dfbf_url, dfbf_rev = _git_rev(dfbf_entry, name=_DFBF_CRATE, ref=ref)
    fmt_url, fmt_rev = _git_rev(fmt_entry, name=_FORMATS_CRATES[0], ref=ref)
    vcf_url, vcf_rev = _git_rev(fmt_vcf, name=_FORMATS_CRATES[1], ref=ref)
    if (fmt_url, fmt_rev) != (vcf_url, vcf_rev):
        raise RunTestsError(
            Exit.ENGINE,
            f"--vepyr {ref}: formats crates disagree "
            f"({_FORMATS_CRATES[0]}={fmt_rev} vs {_FORMATS_CRATES[1]}={vcf_rev})",
        )
    dfbf = _checkout_repo(
        name="dfbf",
        git_url=dfbf_url,
        rev=dfbf_rev,
        target=src_root / "datafusion-bio-functions",
        run=runner,
        mirror_run=mirror_runner,
        git_timeout=git_timeout,
    )
    formats = _checkout_repo(
        name="formats",
        git_url=fmt_url,
        rev=fmt_rev,
        target=src_root / "datafusion-bio-formats",
        run=runner,
        mirror_run=mirror_runner,
        git_timeout=git_timeout,
    )
    return EnginePlan(
        ref=ref,
        vepyr_sha=sha,
        dfbf=dfbf,
        formats=formats,
        config_text=engine_toml(dfbf=dfbf, formats=formats),
    )


def materialise(
    ref: str,
    *,
    repo_root: Path,
    api: GhApi | None = None,
    src_root: Path | None = None,
    run: Runner | None = None,
    git_timeout: float | None = None,
) -> tuple[EnginePlan, Path]:
    """Write ``<repo>/.run_tests/engine.toml``; return ``(plan, config_path)``.

    ``git_timeout`` is ``--git-timeout``; ``None`` falls back to
    ``$RUN_TESTS_GIT_TIMEOUT``, then 3600 s (see :func:`mirror_timeout`).
    """
    plan = resolve(
        ref,
        api=api or GhCli(),
        src_root=src_root or default_src_root(),
        run=run,
        git_timeout=git_timeout,
    )
    report = repo_root / ".run_tests"
    report.mkdir(parents=True, exist_ok=True)
    config_path = report / "engine.toml"
    config_path.write_text(plan.config_text, encoding="utf-8")
    return plan, config_path


class LockGuard:
    """Keep the checked-in ``Cargo.lock`` pristine across an override run.

    Restoration goes through git rather than an in-process snapshot: the pristine
    copy lives in the index, so it survives a ``SIGKILL`` or an OOM-kill that skips
    every ``finally`` block. Entering the guard therefore also sweeps a lockfile that
    an earlier killed run left rewritten — recovery needs no manual step. Outside a
    git checkout (or when ``Cargo.lock`` is untracked) the guard falls back to the
    in-memory snapshot, which is the best that can be done there.
    """

    def __init__(self, repo_root: Path, *, run: Runner | None = None) -> None:
        self.repo_root = repo_root
        self.lock_path = repo_root / "Cargo.lock"
        self._run: Runner = run or _default_run
        self._snapshot: bytes | None = None

    def _git(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        """Run ``git <argv>`` in the repo root; never raises on a non-zero exit."""
        return self._run(
            ["git", "-C", str(self.repo_root), *argv],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )

    @property
    def tracked(self) -> bool:
        """Is ``Cargo.lock`` a tracked file of a git checkout at ``repo_root``?"""
        probe = self._git(["ls-files", "--error-unmatch", "--", "Cargo.lock"])
        return probe.returncode == 0

    @property
    def dirty(self) -> bool:
        """Does ``git status --porcelain Cargo.lock`` report anything?"""
        completed = self._git(["status", "--porcelain", "--", "Cargo.lock"])
        return completed.returncode == 0 and bool(completed.stdout.strip())

    def restore(self) -> bool:
        """Return ``Cargo.lock`` to its committed state. ``True`` if git did it."""
        if not self.tracked:
            return False
        return self._git(["checkout", "--", "Cargo.lock"]).returncode == 0

    @contextmanager
    def held(self) -> Iterator[LockGuard]:
        """Guard ``Cargo.lock`` for the duration of the block."""
        git_backed = self.tracked
        if git_backed:
            # Crash recovery: undo a rewrite left behind by a killed earlier run.
            self.restore()
            self._snapshot = None
        else:
            self._snapshot = (
                self.lock_path.read_bytes() if self.lock_path.is_file() else None
            )
        try:
            yield self
        finally:
            if git_backed:
                self.restore()
            elif self._snapshot is None:
                self.lock_path.unlink(missing_ok=True)
            else:
                self.lock_path.write_bytes(self._snapshot)
