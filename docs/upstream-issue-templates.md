# Upstream issue templates (vendored)

`.github/ISSUE_TEMPLATE/data-problem.yml` ("Data problem report", title prefix `[data]`,
labels `bug`, `data`) is a **verbatim vendored copy** of the upstream form. Do not edit it:
no comment header, no reworded or added field, not even a trailing newline. Any change breaks
the check below.

| Fact | Value |
| --- | --- |
| Upstream repo | `biodatageeks/vepyr` (default branch `master`) |
| Path | `.github/ISSUE_TEMPLATE/data-problem.yml` |
| Pinned commit | `7c7a8290fc1f3e4f9fd51505d7fac5ba489c251b` (2026-09-03) |
| sha256 | `0eaa3e465d78c1df6d41881720d069e60ba6c0a79ba2fd3784a01081a5995d83` |
| Vendored on | 2026-09-27 (issue #136) |

Fetch (read only; nothing is ever written to `biodatageeks/vepyr`):

```bash
U=7c7a8290fc1f3e4f9fd51505d7fac5ba489c251b
gh api "repos/biodatageeks/vepyr/contents/.github/ISSUE_TEMPLATE/data-problem.yml?ref=$U" \
  -H 'Accept: application/vnd.github.raw' > .github/ISSUE_TEMPLATE/data-problem.yml
```

Check (bash; exit 0 = copy intact):

```bash
gh api "repos/biodatageeks/vepyr/contents/.github/ISSUE_TEMPLATE/data-problem.yml?ref=$U" \
  -H 'Accept: application/vnd.github.raw' | cmp - .github/ISSUE_TEMPLATE/data-problem.yml
test "$(shasum -a 256 .github/ISSUE_TEMPLATE/data-problem.yml | cut -d' ' -f1)" = \
  0eaa3e465d78c1df6d41881720d069e60ba6c0a79ba2fd3784a01081a5995d83
```

## Sync policy

No automatic sync. Only the pinned check above; the copy is re-pinned by hand (new commit,
new sha256 in this file) when the owner decides.

## Tracking convention for `[data]` issues

The upstream form has no acceptance criteria, so a `[data]` issue that blocks a data-test here
carries its tracking fields as a block appended to the form's *Description*:

```markdown
### Acceptance criteria
1. `<command>` -> exit 0. Negative control: `<command>` -> exit 1.

blocks #N
Cause: PROVEN | UNPROVEN
datafusion-bio-functions pin: <sha>
```

The `### Acceptance criteria` heading with a backtick satisfies `./issue_check` as is. Only
the owner files issues upstream; agents never do.
