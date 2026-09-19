## Problem

The harness has no way to prove that a fixture corpus is complete before a data-test
run starts, so an incomplete cache fails late and confusingly.

## Scope (one topic)

A precheck that compares the cache root against `PINS.toml` and reports the first
missing shard.

## Acceptance criteria

1. `uv run pytest tools/test_pins.py -q` exits 0.
2. `./run_tests --list` exits 0 and prints one row per `tests/data_*.rs` target.
3. A cache root missing a pinned shard is rejected:

   ```
   ./run_tests --cache-dir /tmp/empty-cache
   ```

   exits non-zero and names the missing shard.

## Out of scope

Fetching the corpus itself.
