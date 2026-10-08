"""``python -m issue_status``: what the root wrapper ``./issue_status`` executes."""

from __future__ import annotations

from issue_status import gate

raise SystemExit(gate.main())
