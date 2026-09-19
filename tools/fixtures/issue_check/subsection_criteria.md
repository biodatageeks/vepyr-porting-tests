## Problem

The ledger tooling has no gate that rejects an issue whose acceptance criteria are not
commands, so work starts against prose.

## Scope (one topic)

A pre-work check over the issue body.

## Acceptance criteria

All commands from the repo root on this issue's branch.

### AC-1 — the checker is green on its own tests

```
uv run --frozen pytest tools/test_issue_check.py
```

exits 0.

### AC-2 — a prose-only body is rejected

`./issue_check --body-file tools/fixtures/issue_check/prose_only_criteria.md` exits 1.

## Out of scope

Running anything the criteria mention.
