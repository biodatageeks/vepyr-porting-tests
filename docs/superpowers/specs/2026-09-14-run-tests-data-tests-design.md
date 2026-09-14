# Design: `./run_tests` data-tests + `--vepyr` (#14 / #15)

## Goal

One entry point that materialises the pinned HF cache, resolves an engine via
`--vepyr REF`, and runs curated `tests/data_*.rs` data-problem targets against
`$VEPYR_CACHE_ROOT`. Shared Rust helpers (`tests/common`) open that cache and
parse CSQ without each pilot inventing the same glue.

## Non-goals

- Porting ledger rows (#16–#19)
- Smoke gates / committed micro-cache
- sitekwb `test-internals` overlays or fork rebase
- Engine SHAs in `PINS.toml`

## CLI

| Invocation | Behaviour |
|------------|-----------|
| `--list` | Glob `tests/data_*.rs`; print targets + count |
| `--cache-dir DIR` (+ fetch flags) | Fetch as today; then run when targets exist |
| `$VEPYR_CACHE_ROOT` only | Precheck + run (no Hub fetch) |
| Neither cache root | Exit 2 with repair |
| Run with targets, no `--vepyr` | Exit 2 (engine is required) |
| `--dry-run` | Fetch plan only |

## `--vepyr REF`

1. Resolve `REF` on `biodatageeks/vepyr` (tag / branch / sha).
2. Read that revision’s `Cargo.toml` for `datafusion-bio-function-vep` and the
   formats crates.
3. Detached checkout under `.run_tests/src/` (or `$RUN_TESTS_SRC`).
4. Emit path `[patch]` via `cargo --config .run_tests/engine.toml`.
5. Snapshot/restore `Cargo.lock`. Failures → exit 6.

Committed `Cargo.toml` uses floating `branch = "master"` on public biodatageeks
git URLs; path patches from `--vepyr` win at run time.

## Helpers (#14)

`annotate_config!`, thin annotate wrapper, CSQ field helpers, provenance
normalisation — enough for the three HF cache pilots. Fail loud via #5 cache
repairs naming `./run_tests --add-contigs`.

## Discovery

Targets = stems of `tests/data_*.rs` (not `common_compile`). Empty list is valid
until the first pilot lands.
