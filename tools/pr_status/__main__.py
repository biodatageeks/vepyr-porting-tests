"""``python -m pr_status``: what the root wrapper ``./pr_status`` executes."""

from __future__ import annotations

from pr_status import gate

raise SystemExit(gate.main())
