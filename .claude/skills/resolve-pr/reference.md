# resolve-pr — gh command forms

`OWNER/REPO` and `N` = PR number throughout. Add `--repo OWNER/REPO` to every `gh pr`/`gh issue` call when not inside the checkout.

## Gather

```bash
gh pr view N --repo OWNER/REPO \
  --json number,title,body,headRefName,baseRefName,state,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,files
gh pr diff N --repo OWNER/REPO

# Review verdicts + summary bodies (the review body is NOT in the comments list)
gh api repos/OWNER/REPO/pulls/N/reviews --paginate \
  --jq '.[] | {id, user: .user.login, state, body}'

# ALL inline review comments, with threading info
gh api repos/OWNER/REPO/pulls/N/comments --paginate \
  --jq '.[] | {id, in_reply_to_id, path, line, user: .user.login, body}'

# Root/ancestor of a reply (repeat until in_reply_to_id is null)
gh api repos/OWNER/REPO/pulls/comments/<ID> --jq '{id, in_reply_to_id, path, line, body}'

# Issue-level (non-inline) PR conversation
gh api repos/OWNER/REPO/issues/N/comments --paginate --jq '.[] | {user: .user.login, body}'
```

Thread reconstruction: build `id -> in_reply_to_id`; walk each leaf to its root; a thread is unresolved unless it was explicitly resolved or answered. GraphQL gives `isResolved` directly:

```bash
gh api graphql -f query='
{ repository(owner:"OWNER", name:"REPO") { pullRequest(number:N) {
    reviewThreads(first:100) { nodes { isResolved isOutdated path line
      comments(first:50){ nodes { databaseId author{login} body } } } } } } }'
```

## Repo policy lookup

```bash
ls .github/ISSUE_TEMPLATE/ 2>/dev/null || echo "NO ISSUE TEMPLATES"
ls .github/PULL_REQUEST_TEMPLATE* CONTRIBUTING.md docs/CONTRIBUTING.md 2>/dev/null
gh label list --repo OWNER/REPO
gh api repos/OWNER/REPO/milestones --jq '.[] | {number, title}'
```

## Reply in-thread (not a loose PR comment)

```bash
gh api repos/OWNER/REPO/pulls/N/comments \
  -f in_reply_to=<COMMENT_ID> -f body="$(cat <<'EOF'
<reply text>

— Claude-1
EOF
)"
```

## New inline review comment (fresh reviewer, step 6)

Batch findings into one review so the author gets one notification:

```bash
gh api repos/OWNER/REPO/pulls/N/reviews -X POST \
  -f commit_id="$(gh pr view N --repo OWNER/REPO --json headRefOid --jq .headRefOid)" \
  -f event=COMMENT -f body="Fresh review of the current diff. — Claude-reviewer-1" \
  -f 'comments[][path]=src/foo.rs' -F 'comments[][line]=88' \
     -f 'comments[][side]=RIGHT' -f 'comments[][body]=<finding> — Claude-reviewer-1'
```

No findings → single clean-review comment:

```bash
gh pr comment N --repo OWNER/REPO --body "Fresh review of the current diff at <sha>: verified <list> are present in the code, not just asserted. No new findings. — Claude-reviewer-1"
```

## Duplicate check before filing an issue

```bash
gh issue list --repo OWNER/REPO --state all --search "<keywords>" --limit 30 \
  --json number,title,state,labels
```

Overlaps an existing issue → comment there instead:

```bash
gh issue comment <EXISTING> --repo OWNER/REPO --body "...
— Claude-1"
```

## File a new issue

```bash
gh issue create --repo OWNER/REPO --title "..." \
  --label <label> --milestone <milestone> --body-file /path/to/body.md
```

Body must carry acceptance criteria checkable by a command with its own exit code, e.g.
`- [ ] \`cargo test --test client_timeout\` exits 0 and contains <case>`.
Template required by policy but absent → do not file silently; ask per SKILL.md step 3.

## PR body edit

```bash
gh pr view N --repo OWNER/REPO --json body --jq .body > /tmp/body.md
# edit /tmp/body.md
gh pr edit N --repo OWNER/REPO --body-file /tmp/body.md
```

## Finding your signature number

```bash
{ gh api repos/OWNER/REPO/pulls/N/comments --paginate --jq '.[].body'
  gh api repos/OWNER/REPO/issues/N/comments --paginate --jq '.[].body'
  gh api repos/OWNER/REPO/pulls/N/reviews  --paginate --jq '.[].body'
} | grep -oE '— *<YourToolName>-[0-9]+' | grep -oE '[0-9]+$' | sort -n | tail -1
```

Empty result → your N is 1.
