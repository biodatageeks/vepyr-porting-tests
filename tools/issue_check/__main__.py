"""``python -m issue_check`` — what ``.github/workflows/issue-check.yml`` executes."""

from __future__ import annotations

from issue_check import cli

raise SystemExit(cli.main())
