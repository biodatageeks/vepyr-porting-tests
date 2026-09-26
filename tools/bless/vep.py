"""The pinned VEP docker image and the one fixed VEP command.

The command is the one from the #16 reference comment (5671600272). Inside the
container every path is fixed — cache at :data:`CACHE_MOUNT`, FASTA at
:data:`FASTA_MOUNT`, input and output in the working directory — so the
recorded ``[vep] command`` is the same string on every host. Extra flags are
recorded as data in ``[vep] extra_flags``; the command is generated from them.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from bless import BlessError
from bless.testdir import INPUT_NAME, ORACLE_NAME

IMAGE_TAG: Final[str] = "ensemblorg/ensembl-vep:release_116.0"
"""Ensembl's official image; ``bless`` resolves and records its digest."""

IMAGE_REPO: Final[str] = IMAGE_TAG.split(":", 1)[0]
CACHE_MOUNT: Final[str] = "/opt/vep/.vep"
FASTA_MOUNT: Final[str] = "/opt/vep/fasta/Homo_sapiens.GRCh38.dna.primary_assembly.fa"
WORK_MOUNT: Final[str] = "/work"

VEP_ARGV: Final[tuple[str, ...]] = (
    "vep",
    "--offline",
    "--cache",
    "--dir_cache", CACHE_MOUNT,
    "--species", "homo_sapiens",
    "--cache_version", "116",
    "--assembly", "GRCh38",
    "--fasta", FASTA_MOUNT,
    "--vcf",
    "--input_file", INPUT_NAME,
    "--output_file", ORACLE_NAME,
    "--force_overwrite",
)  # fmt: skip

VEP_COMMAND: Final[str] = shlex.join(VEP_ARGV)
"""What ``[vep] command`` records when no extra flag is given."""

ALLOWED_VEP_FLAGS: Final[tuple[str, ...]] = (
    "--check_existing",  # needed by #18
)
"""The only flags ``--vep-flag=`` accepts, matched exactly on the whole token.

VEP's Getopt::Long accepts aliases and unique-prefix abbreviations, so a
denylist could never be complete; an exact allowlist cannot be bypassed.
Adding a flag is one line here plus a data-test that needs it. Boolean flags
only: a flag with a value needs its own design.
"""


def parse_extra(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Validate extra VEP flags against :data:`ALLOWED_VEP_FLAGS`.

    Args:
        values: Flags in the order given (``--vep-flag=`` values or
            ``[vep] extra_flags``).

    Returns:
        The flags, unchanged and in order.

    Raises:
        BlessError: On a flag not in the allowlist (exact match) or a duplicate.
    """
    allowed = ", ".join(ALLOWED_VEP_FLAGS)
    seen: set[str] = set()
    for flag in values:
        if flag not in ALLOWED_VEP_FLAGS:
            raise BlessError(f"--vep-flag: {flag} is not allowed; allowed: {allowed}")
        if flag in seen:
            raise BlessError(f"--vep-flag: {flag} is given twice")
        seen.add(flag)
    return tuple(values)


def vep_command(extra: tuple[str, ...] = ()) -> str:
    """Return the ``[vep] command`` string for the fixed argv plus ``extra``."""
    return shlex.join((*VEP_ARGV, *extra))


def recorded_flags(table: Mapping[str, object], *, where: str) -> tuple[str, ...]:
    """Read and validate ``[vep] extra_flags`` (absent means no extra flags).

    Args:
        table: The ``[vep]`` table of ``test.toml``.
        where: File named in error messages.

    Returns:
        The flags in their recorded order, checked by :func:`parse_extra`.

    Raises:
        BlessError: If the key is not an array of strings or a flag is not
            allowed or repeated.
    """
    match table.get("extra_flags", []):
        case list() as flags if all(isinstance(flag, str) for flag in flags):
            pass
        case other:
            raise BlessError(
                f"{where}: [vep] extra_flags must be an array of strings, got {other!r}"
            )
    try:
        return parse_extra(flags)
    except BlessError as exc:
        raise BlessError(f"{where}: [vep] extra_flags: {exc}") from exc


def require_command(command: object, extra: tuple[str, ...], *, where: str) -> None:
    """Require the recorded ``[vep] command`` to be generated from ``extra``.

    The command is an audit record, never parsed: it must equal
    :func:`vep_command` of ``[vep] extra_flags`` exactly (canonical form).

    Args:
        command: The recorded ``[vep] command`` value.
        extra: The validated ``[vep] extra_flags``.
        where: File named in error messages.

    Raises:
        BlessError: If the command is missing, not a string, or differs.
    """
    if not isinstance(command, str):
        raise BlessError(f"{where}: [vep] command is missing")
    if command != (canonical := vep_command(extra)):
        raise BlessError(
            f"{where}: [vep] command is not the canonical command for [vep] "
            f"extra_flags = {list(extra)!r}; recorded: {command!r}; "
            f"expected exactly: {canonical!r}"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Mounts:
    """Host paths bound into the container.

    Attributes:
        cache_dir: VEP ``--dir_cache`` root (read-only).
        fasta: Uncompressed FASTA (read-only; its ``.fai`` next to it).
        work_dir: Directory holding ``input.vcf``; VEP writes its output here.
    """

    cache_dir: Path
    fasta: Path
    work_dir: Path


def docker_argv(image: str, mounts: Mounts, extra: tuple[str, ...] = ()) -> list[str]:
    """Build the full ``docker run`` argv.

    Args:
        image: Image reference (tag for a dry run, digest for a real one).
        mounts: Host paths.
        extra: Validated extra VEP flags, appended after :data:`VEP_ARGV`.

    Returns:
        The argv, ready for :func:`subprocess.run` or :func:`shlex.join`.
    """
    fai = mounts.fasta.with_name(mounts.fasta.name + ".fai")
    return [
        "docker", "run", "--rm",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{mounts.cache_dir}:{CACHE_MOUNT}:ro",
        "-v", f"{mounts.fasta}:{FASTA_MOUNT}:ro",
        "-v", f"{fai}:{FASTA_MOUNT}.fai:ro",
        "-v", f"{mounts.work_dir}:{WORK_MOUNT}",
        "-w", WORK_MOUNT,
        image,
        *VEP_ARGV,
        *extra,
    ]  # fmt: skip


def require_docker() -> str:
    """Return the docker executable, or fail with a readable message.

    Raises:
        BlessError: If docker is missing or its daemon does not answer.
    """
    exe = shutil.which("docker")
    if exe is None:
        raise BlessError(
            "docker is not on PATH; install Docker (or run "
            "`./bless --check <test-dir>`, "
            "which needs no docker)"
        )
    probe = subprocess.run(
        [exe, "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True
    )
    if probe.returncode != 0:
        raise BlessError(
            "docker is installed but its daemon does not answer: "
            f"{probe.stderr.strip()[:200]}"
        )
    return exe


def resolve_digest(docker: str, reference: str) -> str:
    """Pull ``reference`` and return it pinned as ``repo@sha256:...``.

    Args:
        docker: Docker executable.
        reference: Tag or digest reference to pull.

    Raises:
        BlessError: If the pull fails or the image has no registry digest.
    """
    pull = subprocess.run(
        [docker, "pull", "--quiet", reference], capture_output=True, text=True
    )
    if pull.returncode != 0:
        raise BlessError(f"docker pull {reference} failed: {pull.stderr.strip()[:300]}")
    inspect = subprocess.run(
        [docker, "image", "inspect", "--format", "{{json .RepoDigests}}", reference],
        capture_output=True,
        text=True,
    )
    digests = [
        d for d in inspect.stdout.strip().strip("[]").replace('"', "").split(",") if d
    ]
    pinned = next((d for d in digests if d.startswith(f"{IMAGE_REPO}@sha256:")), None)
    if inspect.returncode != 0 or pinned is None:
        raise BlessError(
            f"could not read the registry digest of {reference} "
            "from `docker image inspect`"
        )
    return pinned


def require_mountable(docker: str, image: str, paths: list[Path]) -> None:
    """Fail early unless docker can bind-mount every path in ``paths``.

    Docker Desktop only shares the host directories listed in its settings.
    A path outside them either fails when a container starts ("Mounts
    denied") or, for paths that also exist inside Docker's VM such as
    ``/var/folders``, is silently mounted as an *empty* VM directory. So the
    probe lists each directory from inside a container and compares that with
    the host's listing; an empty directory gets a short-lived marker file so
    the comparison means something. Probing first means a non-shared
    ``--download-vep-cache-to-dir`` is refused before a 27 GB download, not
    after it.

    Args:
        docker: Docker executable.
        image: An image already present locally.
        paths: Existing host directories.

    Raises:
        BlessError: Naming the first path the container cannot see.
    """
    fix = (
        "share it in Docker Desktop (Settings > Resources > File sharing) or pick "
        "a path inside a shared directory"
    )
    for path in paths:
        marker: Path | None = None
        if not any(path.iterdir()):
            marker = path / ".bless-mount-probe"
            marker.touch()
        try:
            host = {p.name for p in path.iterdir()}
            argv = [docker, "run", "--rm", "--entrypoint", "ls"]
            argv += ["-v", f"{path}:/probe:ro", image, "-1A", "/probe"]
            probe = subprocess.run(
                argv,
                capture_output=True,
                text=True,
            )
        finally:
            if marker is not None:
                marker.unlink(missing_ok=True)
        if probe.returncode != 0:
            reason = next(
                (ln for ln in probe.stderr.splitlines() if "not shared" in ln),
                probe.stderr.strip().splitlines()[-1] if probe.stderr.strip() else "",
            )
            raise BlessError(f"docker cannot mount {path} ({reason.strip()}); {fix}")
        if not host <= set(probe.stdout.splitlines()):
            raise BlessError(
                f"docker mounts {path} but the container does not see its files "
                f"(Docker Desktop does not share that path); {fix}"
            )


def run(docker: str, image: str, mounts: Mounts, extra: tuple[str, ...] = ()) -> None:
    """Run VEP with ``extra`` flags; its own output streams to the terminal.

    Raises:
        BlessError: If the container exits non-zero or writes no output.
    """
    argv = docker_argv(image, mounts, extra)
    argv[0] = docker
    done = subprocess.run(argv)
    if done.returncode != 0:
        raise BlessError(
            f"VEP in {image} exited with {done.returncode}; its own messages are above"
        )
    if not (mounts.work_dir / ORACLE_NAME).is_file():
        raise BlessError(f"VEP in {image} exited 0 but wrote no {ORACLE_NAME}")
