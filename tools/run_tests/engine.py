"""``--vepyr REF`` — resolve biodatageeks/vepyr and path-patch its dfbf/formats ladder.

Only the public biodatageeks crates named by ``REF``'s own ``Cargo.toml``; no
``test-internals`` overlays. The engine is a run-time parameter:
committed ``Cargo.toml`` floats on biodatageeks ``master``; this module materialises the
exact revisions named by ``REF``'s ``Cargo.toml`` and writes a ``cargo --config`` file
with path ``[patch]`` tables. ``Cargo.lock`` is restored from git around the run.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tomllib
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from run_tests.verdict import Exit, RunTestsError

__all__ = [
    "DFBF_GIT",
    "FORMATS_GIT",
    "VEPYR_REPO",
    "Checkout",
    "EnginePlan",
    "GhApi",
    "GhCli",
    "LockGuard",
    "default_src_root",
    "engine_toml",
    "materialise",
    "resolve",
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
    """Checkout cache: ``$RUN_TESTS_SRC`` or ``<repo>/.run_tests/src``."""
    env = os.environ if environ is None else environ
    if raw := env.get("RUN_TESTS_SRC"):
        return Path(raw).expanduser()
    return Path(__file__).resolve().parents[2] / ".run_tests" / "src"


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


def _git(
    run: Runner, argv: Sequence[str], *, env: Mapping[str, str] | None = None
) -> str:
    """Run one git command; ``env`` (if given) is overlaid on ``os.environ``.

    Passing ``env=None`` leaves the child environment untouched — no ``env=``
    keyword reaches ``run`` at all — so every existing call site is unaffected.
    """
    extra = {} if env is None else {"env": {**os.environ, **env}}
    completed = run(
        list(argv),
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        check=False,
        **extra,
    )
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


def _mirror_sha(
    *,
    name: str,
    git_url: str,
    rev: str,
    mirror: Path,
    run: Runner,
    offline: bool,
) -> str:
    """Update the shared bare mirror of ``git_url`` and resolve ``rev`` to a sha."""
    if not mirror.exists():
        if offline:
            raise RunTestsError(
                Exit.ENGINE,
                f"--offline: no mirror of {name} at {mirror}; run once online",
            )
        mirror.parent.mkdir(parents=True, exist_ok=True)
        _git(run, ["git", "clone", "--quiet", "--mirror", git_url, str(mirror)])
    git = ["git", "-C", str(mirror)]
    verify = [*git, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"]
    if not offline:
        # A mirror fetch updates every ref, so named branches/tags cannot go stale.
        _git(run, [*git, "fetch", "--quiet", "--prune", "--tags", "origin"])
    try:
        return _git(run, verify)
    except RunTestsError as exc:
        if offline:
            raise RunTestsError(
                Exit.ENGINE,
                f"--offline: {name} mirror {mirror} has no rev {rev!r}; "
                "run once online",
            ) from exc
    # A sha on no branch (e.g. a PR head) needs an explicit single-rev fetch.
    run(
        [*git, "fetch", "--quiet", "origin", rev],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        check=False,
    )
    try:
        return _git(run, verify)
    except RunTestsError as exc:
        raise RunTestsError(
            Exit.ENGINE, f"{name}: cannot resolve rev {rev!r} in {mirror}: {exc}"
        ) from exc


def _checkout_repo(
    *,
    name: str,
    git_url: str,
    rev: str,
    target: Path,
    run: Runner,
    offline: bool,
) -> Checkout:
    """Materialise ``rev`` of ``git_url`` in a worktree keyed by its resolved sha.

    ``target`` is the per-repo cache root. Objects live once in ``target/git`` (a bare
    mirror); each revision gets its own immutable tree at ``target/<sha>`` cloned
    ``--shared`` from it. Nothing is ever re-checked-out in place, so two runs of
    different revisions — sequential or overlapping — cannot swap files under each
    other's live ``cargo`` build.
    """
    sha = _mirror_sha(
        name=name,
        git_url=git_url,
        rev=rev,
        mirror=target / "git",
        run=run,
        offline=offline,
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
            f"dfbf checkout at {dfbf.path} missing crates: "
            f"{', '.join(missing_dfbf)}",
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
    offline: bool = False,
) -> EnginePlan:
    """Resolve ``ref``, checkout the ladder, and build the cargo config text."""
    runner: Runner = run or _default_run
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
        offline=offline,
    )
    formats = _checkout_repo(
        name="formats",
        git_url=fmt_url,
        rev=fmt_rev,
        target=src_root / "datafusion-bio-formats",
        run=runner,
        offline=offline,
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
    offline: bool = False,
) -> tuple[EnginePlan, Path]:
    """Write ``<repo>/.run_tests/engine.toml``; return ``(plan, config_path)``."""
    plan = resolve(
        ref,
        api=api or GhCli(),
        src_root=src_root or default_src_root(),
        run=run,
        offline=offline,
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
