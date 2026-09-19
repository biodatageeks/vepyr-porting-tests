## Problem

The gate's runner list contains words that are ordinary English nouns, so a criterion
could look command-verifiable while naming no invocation at all.

## Scope (one topic)

Require a command *shape*, not a keyword.

## Acceptance criteria

1. Reviewers agree the `exit code` wording is clear.
2. Reviewers see no `diff` in the rendered table.
3. The `find` helper is documented in prose only.

## Notes

Negative control for the "command-shaped, not merely keyword-bearing" rule: all three
items are prose that happens to backtick a word which is also an executable name (or,
for `exit code`, the English noun phrase behind the exit-code assertion form). Running
`./issue_check --body-file <this file>` must exit 1 and list `#1`, `#2` and `#3`.

## Out of scope

Fetching the corpus itself.
