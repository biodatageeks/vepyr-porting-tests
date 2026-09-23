## Problem

The harness has no way to prove that a fixture corpus is complete before a data-test
run starts.

## Scope (one topic)

A precheck that compares the cache root against the pins file.

## Acceptance criteria

1. The precheck works correctly and the tests pass.
2. Reviewers agree that the pins error message is clear enough.
3. The maintainer is satisfied with the result.

This fixture contains no backtick anywhere, so the acceptance-criteria section has
neither a code span nor a fenced code block: the negative control for rule (b).

## Out of scope

Fetching the corpus itself.
