## Problem

The harness has no way to prove that a fixture corpus is complete before a data-test
run starts.

## Scope (one topic)

A precheck that compares the cache root against `PINS.toml`.

## Notes

This issue deliberately carries no acceptance-criteria section at all: it is the
negative control for the "required section present" rule. Running
`uv run python -m issue_check --body-file <this file>` must exit non-zero and say
which section is missing.

## Out of scope

Fetching the corpus itself.
