"""``./campaign {port,check,report}`` — the VEP 116.2 merged-cache port campaign.

Exit codes: ``port`` 0 when every processed case is ``PASS``, 1 otherwise;
``check`` 0 when every invariant holds, 1 with one line per violation;
``report`` 0 after rewriting the table. Any command exits 2 on an unusable
input (settings, manifest, a failed prerequisite step), with one
``campaign: error: ...`` line on stderr.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from campaign.check import check
from campaign.model import DEFAULT_SETTINGS, CampaignError, Settings
from campaign.port import PortOptions, run_campaign
from campaign.report import write_report

EXIT_ERROR = 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="campaign", description=__doc__)
    parser.add_argument(
        "--settings",
        type=Path,
        default=DEFAULT_SETTINGS,
        help="campaign settings (default: %(default)s)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    port = sub.add_parser("port", help="port the next batch of queued cases")
    port.add_argument("--vep-cache", type=Path, required=True)
    port.add_argument(
        "--cache-dir",
        type=Path,
        required=True,
        help="Hub-layout cache root with PROVENANCE.json, passed to ./run_tests",
    )
    port.add_argument("--fasta", type=Path, required=True)
    port.add_argument("--evidence", type=Path, required=True)
    port.add_argument(
        "--limit", type=int, default=None, help="cases (default: batch_size)"
    )
    port.add_argument(
        "--regenerate",
        action="store_true",
        help="Normalize and rerun existing ports, once per input-identity audit",
    )
    chk = sub.add_parser("check", help="validate records against committed files")
    chk.add_argument("--require-complete", action="store_true")
    chk.add_argument("--require-normalized", action="store_true")
    sub.add_parser("report", help="regenerate the campaign README table")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one subcommand; return its exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        settings = Settings.load(args.settings)
        match args.command:
            case "port":
                limit = settings.batch_size if args.limit is None else args.limit
                if limit <= 0:
                    parser.error("--limit must be positive")
                return run_campaign(
                    PortOptions(
                        vep_cache=args.vep_cache,
                        cache_dir=args.cache_dir,
                        fasta=args.fasta,
                        evidence=args.evidence,
                        limit=limit,
                        regenerate=args.regenerate,
                    ),
                    settings,
                )
            case "check":
                report = check(
                    settings,
                    require_complete=args.require_complete,
                    require_normalized=args.require_normalized,
                )
                for violation in report.violations:
                    print(f"check: {violation}")
                if report.violations:
                    print(f"check: {len(report.violations)} violation(s)")
                    return 1
                print(report.summary())
                return 0
            case "report":
                write_report(settings)
                return 0
            case other:
                parser.error(f"unknown command {other}")
    except CampaignError as exc:
        print(f"campaign: error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
