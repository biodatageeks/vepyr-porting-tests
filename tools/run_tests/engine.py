"""``--vepyr REF`` — resolve biodatageeks/vepyr and path-patch its dfbf/formats ladder.

No sitekwb forks, no ``test-internals`` overlays. The engine is a run-time parameter:
committed ``Cargo.toml`` floats on biodatageeks ``master``; this module materialises the
exact revisions named by ``REF``'s ``Cargo.toml`` and writes a ``cargo --config`` file
with path ``[patch]`` tables. ``Cargo.lock`` is snapshotted and restored around the run.
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
_SHA: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{7,40}$")


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


def _git(run: Runner, argv: Sequence[str]) -> str:
    completed = run(
        list(argv),
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        check=False,
    )
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "git failed").strip()
        raise RunTestsError(Exit.ENGINE, f"git {' '.join(argv[1:4])}…: {err[:300]}")
    return completed.stdout.strip()


def _resolve_sha(api: GhApi, ref: str) -> str:
    """Dereference ``ref`` (tag / branch / sha) on biodatageeks/vepyr."""
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


def _checkout_repo(
    *,
    name: str,
    git_url: str,
    rev: str,
    target: Path,
    run: Runner,
    offline: bool,
) -> Checkout:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not (target / ".git").exists():
        if offline:
            raise RunTestsError(
                Exit.ENGINE,
                f"--offline: no checkout of {name} at {target}; run once online",
            )
        _git(
            run,
            ["git", "clone", "--quiet", "--no-checkout", "--", git_url, str(target)],
        )
    git = ["git", "-C", str(target)]
    # What to detach onto. Offline runs have nothing fresher than the local ref.
    target_ref = rev
    if not offline:
        # Fetch the named rev (sha / tag / branch). Tags need --tags for some hosts.
        fetch = run(
            [*git, "fetch", "--quiet", "--tags", "origin", rev],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
        if fetch.returncode == 0:
            # Detach onto what this fetch just retrieved, never onto the possibly
            # stale clone-time local ref of the same name (issue #22).
            target_ref = "FETCH_HEAD"
        else:
            # Plain sha may need a broader fetch.
            _git(run, [*git, "fetch", "--quiet", "--tags", "origin"])
    _git(run, [*git, "checkout", "--quiet", "--detach", target_ref, "--"])
    head = _git(run, [*git, "rev-parse", "HEAD"])
    return Checkout(name=name, path=target, git_url=git_url, rev=rev, head=head)


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
    """Snapshot ``Cargo.lock`` before an override run and restore it afterwards."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.lock_path = repo_root / "Cargo.lock"
        self._snapshot: bytes | None = None

    @contextmanager
    def held(self) -> Iterator[LockGuard]:
        if self.lock_path.is_file():
            self._snapshot = self.lock_path.read_bytes()
        try:
            yield self
        finally:
            if self._snapshot is None:
                if self.lock_path.exists():
                    self.lock_path.unlink()
            else:
                self.lock_path.write_bytes(self._snapshot)
