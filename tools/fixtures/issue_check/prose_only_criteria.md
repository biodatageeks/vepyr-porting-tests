## Problem

The harness has no way to prove that a fixture corpus is complete before a data-test
run starts.

## Scope (one topic)

A precheck that compares the cache root against `PINS.toml`.

## Acceptance criteria

1. The precheck works correctly and the tests pass.
2. `uv run pytest tools/test_pins.py -q` exits 0.
3. Reviewers agree that the `PINS.toml` error message is clear enough.

## Out of scope

Fetching the corpus itself.
