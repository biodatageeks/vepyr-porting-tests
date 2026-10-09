"""Engine checkout tests: per-sha trees, and reported sha == compiled sha.

Regression cover for #23 — ``.run_tests/src/<repo>`` used to be one mutable
directory that was ``git checkout --detach``'d in place on every run, so two runs
of different revisions shared (and clobbered) one tree.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import pytest

from run_tests import engine

_MEMBERS: Final[dict[str, tuple[str, ...]]] = {
    "dfbf": ("datafusion/bio-function-vep",),
    "formats": ("datafusion/bio-format-ensembl-cache", "datafusion/bio-format-vcf"),
}
_CRATES: Final[dict[str, tuple[str, ...]]] = {
    "dfbf": ("datafusion-bio-function-vep",),
    "formats": ("datafusion-bio-format-ensembl-cache", "datafusion-bio-format-vcf"),
}


def _git(cwd: Path, *argv: str) -> str:
    out = subprocess.run(
        ["git", *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _workspace(root: Path, kind: str, marker: str) -> None:
    members = _MEMBERS[kind]
    root.joinpath("Cargo.toml").write_text(
        "[workspace]\nmembers = [" + ", ".join(f'"{m}"' for m in members) + "]\n",
        encoding="utf-8",
    )
    for member, crate in zip(members, _CRATES[kind], strict=True):
        crate_dir = root / member
        crate_dir.mkdir(parents=True, exist_ok=True)
        crate_dir.joinpath("Cargo.toml").write_text(
            f'[package]\nname = "{crate}"\nversion = "0.0.0"\n', encoding="utf-8"
        )
    root.joinpath("MARKER").write_text(marker, encoding="utf-8")


def _origin(tmp_path: Path, kind: str) -> tuple[Path, str, str]:
    """A local git origin for ``kind`` with two commits; returns (path, sha1, sha2)."""
    root = tmp_path / f"origin-{kind}"
    root.mkdir()
    _git(root, "init", "--quiet", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    shas: list[str] = []
    for marker in ("one", "two"):
        _workspace(root, kind, marker)
        _git(root, "add", "-A")
        _git(root, "commit", "--quiet", "-m", marker)
        shas.append(_git(root, "rev-parse", "HEAD"))
    return root, shas[0], shas[1]


class _FakeGh:
    """GitHub stub: vepyr sha per ref, and a Cargo.toml pinning local origins."""

    def __init__(self, *, shas: dict[str, str], manifest: dict[str, str]) -> None:
        self.shas = shas
        self.manifest = manifest

    def get(self, path: str) -> Any:
        if path.startswith(f"repos/{engine.VEPYR_REPO}/commits/"):
            ref = path.rsplit("/", 1)[1]
            return {"sha": self.shas[ref]}
        if "contents/Cargo.toml" in path:
            ref = path.rsplit("ref=", 1)[1]
            import base64

            return {
                "encoding": "base64",
                "content": base64.b64encode(
                    self.manifest[ref].encode("utf-8")
                ).decode("ascii"),
            }
        raise engine.GhError(path, "unexpected")


def _dep(crate: str, url: Path, rev: str) -> str:
    return f"{crate} = {{ git = {json.dumps(url.as_uri())}, rev = {json.dumps(rev)} }}"


def _manifest(*, dfbf_url: Path, dfbf_rev: str, fmt_url: Path, fmt_rev: str) -> str:
    return "\n".join(
        [
            "[dependencies]",
            _dep("datafusion-bio-function-vep", dfbf_url, dfbf_rev),
            _dep("datafusion-bio-format-ensembl-cache", fmt_url, fmt_rev),
            _dep("datafusion-bio-format-vcf", fmt_url, fmt_rev),
            "",
        ]
    )


@pytest.fixture
def ladder(tmp_path: Path) -> dict[str, Any]:
    dfbf, dfbf1, dfbf2 = _origin(tmp_path, "dfbf")
    fmt, fmt1, fmt2 = _origin(tmp_path, "formats")
    manifests = {
        "a" * 40: _manifest(dfbf_url=dfbf, dfbf_rev=dfbf1, fmt_url=fmt, fmt_rev=fmt1),
        "b" * 40: _manifest(dfbf_url=dfbf, dfbf_rev=dfbf2, fmt_url=fmt, fmt_rev=fmt2),
    }
    api = _FakeGh(shas={"old": "a" * 40, "new": "b" * 40}, manifest=manifests)
    return {
        "api": api,
        "src_root": tmp_path / "src",
        "dfbf": (dfbf1, dfbf2),
        "formats": (fmt1, fmt2),
    }


def _run(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), **kwargs)


def test_sequential_runs_of_different_revs_keep_independent_trees(
    ladder: dict[str, Any],
) -> None:
    """AC1: two runs, different shas → two trees, neither mutated by the other."""
    src_root: Path = ladder["src_root"]
    old = engine.resolve("old", api=ladder["api"], src_root=src_root, run=_run)
    old_dfbf_head = _git(old.dfbf.path, "rev-parse", "HEAD")
    new = engine.resolve("new", api=ladder["api"], src_root=src_root, run=_run)

    assert old.dfbf.path != new.dfbf.path
    assert old.formats.path != new.formats.path
    assert old.dfbf.path.name == ladder["dfbf"][0]
    assert new.dfbf.path.name == ladder["dfbf"][1]
    # The first run's tree is untouched by the second run.
    assert _git(old.dfbf.path, "rev-parse", "HEAD") == old_dfbf_head
    assert (old.dfbf.path / "MARKER").read_text(encoding="utf-8") == "one"
    assert (new.dfbf.path / "MARKER").read_text(encoding="utf-8") == "two"
    assert old.config_text != new.config_text


def test_reported_head_matches_the_checkout_that_was_compiled(
    ladder: dict[str, Any],
) -> None:
    """AC2: every sha the run reports is ``rev-parse HEAD`` of the tree it patched in.

    ``vepyr`` itself is never checked out (the committed ``Cargo.toml`` floats and only
    the dfbf/formats ladder is path-patched), so the trees a run compiles are the
    ladder checkouts named in ``plan.config_text``.
    """
    plan = engine.resolve(
        "new", api=ladder["api"], src_root=ladder["src_root"], run=_run
    )
    assert plan.vepyr_sha == "b" * 40
    for checkout, expected in (
        (plan.dfbf, ladder["dfbf"][1]),
        (plan.formats, ladder["formats"][1]),
    ):
        assert checkout.head == _git(checkout.path, "rev-parse", "HEAD")
        assert checkout.head == expected
        assert checkout.path.name == checkout.head
        assert str(checkout.path) in plan.config_text


def test_same_rev_reuses_its_tree(ladder: dict[str, Any]) -> None:
    """Re-running the same revision reuses the per-sha tree instead of re-cloning."""
    src_root: Path = ladder["src_root"]
    first = engine.resolve("old", api=ladder["api"], src_root=src_root, run=_run)
    stamp = first.dfbf.path / "BUILD_ARTIFACT"
    stamp.write_text("kept", encoding="utf-8")
    second = engine.resolve("old", api=ladder["api"], src_root=src_root, run=_run)
    assert second.dfbf.path == first.dfbf.path
    assert stamp.read_text(encoding="utf-8") == "kept"


# --- #256: mirror timeout, atomic clone, shared mirror cache, heartbeat ----------

_SHA40: Final[str] = "a" * 40
_URL: Final[str] = "https://example.invalid/dfbf.git"


class _FakeGit:
    """Fake ``run``: records argv/kwargs; ``clone --mirror`` "takes" ``clone_s``.

    ``clone --mirror`` FIRST creates its destination (the last argv element) with a
    ``HEAD`` file, THEN raises :class:`subprocess.TimeoutExpired` when the passed
    ``timeout`` is below ``clone_s`` — like a real git killed half-way. No real
    sleep happens.
    """

    def __init__(self, *, clone_s: float = 1.0) -> None:
        self.clone_s = clone_s
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(
        self, argv: Sequence[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        self.calls.append((args, kwargs))
        out = ""
        if "clone" in args and "--mirror" in args:
            dest = Path(args[-1])
            dest.mkdir(parents=True)
            (dest / "HEAD").write_text("ref: refs/heads/main\n")
            if kwargs["timeout"] < self.clone_s:
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        elif "clone" in args:
            (Path(args[-1]) / ".git").mkdir(parents=True)
        elif "rev-parse" in args:
            out = _SHA40
        return subprocess.CompletedProcess(args, 0, out, "")

    def argvs(self, *words: str) -> list[list[str]]:
        """Recorded argv lists that contain every one of ``words``."""
        return [a for a, _ in self.calls if all(w in a for w in words)]


def _checkout(fake: _FakeGit, target: Path) -> engine.Checkout:
    return engine._checkout_repo(
        name="dfbf", git_url=_URL, rev="main", target=target, run=fake
    )


def test_mirror_clone_default_timeout_exceeds_old_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 600 s clone succeeds under the default mirror timeout (180 s would kill it)."""
    monkeypatch.delenv(engine.GIT_TIMEOUT_ENV, raising=False)
    fake = _FakeGit(clone_s=600)
    checkout = _checkout(fake, tmp_path / "dfbf")
    assert checkout.head == _SHA40
    assert (tmp_path / "dfbf" / "git" / "HEAD").is_file()
    [(_, kwargs)] = [c for c in fake.calls if "--mirror" in c[0]]
    assert kwargs["timeout"] == engine.DEFAULT_GIT_TIMEOUT > 600 > engine._TIMEOUT


def test_mirror_timeout_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``RUN_TESTS_GIT_TIMEOUT=10`` fails the 600 s clone, naming the limit."""
    monkeypatch.setenv(engine.GIT_TIMEOUT_ENV, "10")
    with pytest.raises(engine.RunTestsError) as info:
        _checkout(_FakeGit(clone_s=600), tmp_path / "dfbf")
    assert "timed out after 10 s" in str(info.value)
    assert engine.GIT_TIMEOUT_ENV in str(info.value)
    # The flag wins over the variable.
    assert engine.mirror_timeout(700.0, {engine.GIT_TIMEOUT_ENV: "10"}) == 700.0
    with pytest.raises(engine.RunTestsError):
        engine.mirror_timeout(None, {engine.GIT_TIMEOUT_ENV: "0"})


def test_short_calls_keep_short_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``rev-parse`` and ``clone --shared`` keep ``timeout <= 180``."""
    monkeypatch.delenv(engine.GIT_TIMEOUT_ENV, raising=False)
    fake = _FakeGit()
    _checkout(fake, tmp_path / "dfbf")
    short = [
        kw["timeout"]
        for argv, kw in fake.calls
        if "rev-parse" in argv or "--shared" in argv
    ]
    assert len(short) >= 3
    assert all(t <= 180 for t in short)
    assert all(
        kw["timeout"] == engine.DEFAULT_GIT_TIMEOUT
        for argv, kw in fake.calls
        if "--mirror" in argv or "fetch" in argv
    )


def test_timed_out_clone_leaves_no_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clone killed half-way leaves no ``<target>/git`` and no ``.tmp-*`` sibling."""
    monkeypatch.setenv(engine.GIT_TIMEOUT_ENV, "10")
    target = tmp_path / "dfbf"
    fake = _FakeGit(clone_s=600)
    with pytest.raises((engine.RunTestsError, subprocess.TimeoutExpired)):
        _checkout(fake, target)
    assert fake.argvs("--mirror"), "the fake clone never ran"
    assert not (target / "git").exists()
    assert not list(target.glob("git.tmp-*"))
    # A stale temp dir of a killed run is swept by the next run, which then clones.
    (target / "git.tmp-99999999").mkdir()
    monkeypatch.delenv(engine.GIT_TIMEOUT_ENV)
    _checkout(_FakeGit(clone_s=600), target)
    assert (target / "git" / "HEAD").is_file()
    assert not list(target.glob("git.tmp-*"))


def test_second_run_reuses_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs from two checkouts share one XDG mirror: one clone, then fetches."""
    monkeypatch.delenv(engine.SRC_ENV, raising=False)
    monkeypatch.delenv(engine.GIT_TIMEOUT_ENV, raising=False)
    cache = tmp_path / "xdg"
    fake = _FakeGit()
    for checkout_root in (tmp_path / "wt1", tmp_path / "wt2"):
        checkout_root.mkdir()
        monkeypatch.chdir(checkout_root)
        src = engine.default_src_root({"XDG_CACHE_HOME": str(cache)})
        _checkout(fake, src / "datafusion-bio-functions")
    mirror = cache / "vepyr-porting-tests/run_tests/src/datafusion-bio-functions/git"
    assert mirror.is_dir()
    assert mirror.is_relative_to(cache)
    assert len(fake.argvs("clone", "--mirror")) == 1
    assert len(fake.argvs("fetch")) >= 1


def test_default_src_root_outside_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """XDG cache by default (not under the repo); ``RUN_TESTS_SRC`` wins when set."""
    repo_root = Path(engine.__file__).resolve().parents[2]
    monkeypatch.delenv(engine.SRC_ENV, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    root = engine.default_src_root()
    assert root.is_relative_to(tmp_path)
    assert not root.is_relative_to(repo_root)
    home = engine.default_src_root({"HOME": str(tmp_path / "home")})
    assert home == tmp_path / "home/.cache/vepyr-porting-tests/run_tests/src"
    monkeypatch.setenv(engine.SRC_ENV, str(tmp_path / "explicit"))
    assert engine.default_src_root() == tmp_path / "explicit"


class _FakeProcess:
    """A child that finishes once the fake clock reaches ``done_at``."""

    def __init__(self, clock: list[float], done_at: float) -> None:
        self.clock, self.done_at = clock, done_at
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if self.clock[0] >= self.done_at:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0


def test_mirror_heartbeat_printed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A 65 s fake clone prints the start line and at least two heartbeats."""
    monkeypatch.delenv(engine.GIT_TIMEOUT_ENV, raising=False)
    clock = [0.0]

    def spawn(argv: Sequence[str], **_: Any) -> _FakeProcess:
        if "--mirror" in argv:
            Path(argv[-1]).mkdir(parents=True)
            (Path(argv[-1]) / "HEAD").write_text("x" * 2048)
            return _FakeProcess(clock, clock[0] + 65)
        return _FakeProcess(clock, clock[0])

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    watched = engine.WatchedRunner(spawn=spawn, clock=lambda: clock[0], sleep=sleep)
    engine._checkout_repo(
        name="dfbf",
        git_url=_URL,
        rev="main",
        target=tmp_path / "dfbf",
        run=_FakeGit(),
        mirror_run=watched,
    )
    err = capsys.readouterr().err
    assert f"run_tests: cloning {_URL} mirror into {tmp_path / 'dfbf' / 'git'}" in err
    assert "(timeout 3600 s)" in err
    beats = [ln for ln in err.splitlines() if "still running after" in ln]
    assert len(beats) >= 2, err
    assert "still running after 30 s" in beats[0]
    assert (tmp_path / "dfbf" / "git" / "HEAD").is_file()


def test_watched_runner_kills_on_timeout() -> None:
    """``WatchedRunner`` raises ``TimeoutExpired`` and kills the child at the limit."""
    clock = [0.0]
    procs: list[_FakeProcess] = []

    def spawn(argv: Sequence[str], **_: Any) -> _FakeProcess:
        procs.append(_FakeProcess(clock, 10_000))
        return procs[-1]

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    watched = engine.WatchedRunner(spawn=spawn, clock=lambda: clock[0], sleep=sleep)
    with pytest.raises(subprocess.TimeoutExpired):
        watched(["git", "-C", "/nonexistent", "fetch"], timeout=5)
    assert procs[0].returncode == -9
