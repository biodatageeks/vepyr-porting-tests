---
name: resolve-pr
description: Use when a pull request has open review feedback, CHANGES_REQUESTED, unresolved review threads, or reviewer comments that must be answered and driven to a mergeable state; also when invoked as /resolve-pr <PR-number> [repo].
---

# Resolve PR Review Feedback

Iterate a PR to "no open threads, tests pass, fresh review clean, acceptance criteria met" — without guessing on ambiguity and without inflating evidence.

**Core principle: every claim you post to GitHub must be one you can back with output you actually saw.**

## Owner options (opt-in, not committed)

Both options are off by default. Each person enables them on their own machine through environment variables (shell profile, or the `env` block of the git-ignored `.claude/settings.local.json`); nothing about them is committed. Check them with `printenv RESOLVE_PR_OPEN_CMD RESOLVE_PR_OWNER_QUEUE` before the first hand-over.

### `RESOLVE_PR_OPEN_CMD`: open hand-overs automatically

Unset: give the URL as a text link, nothing else. Set (to a command that takes one URL argument): whenever you tell the owner a PR is done/mergeable/ready for their review — including right after step 6's fresh re-review comes back clean, or step 8's stop condition is met — also run `$RESOLVE_PR_OPEN_CMD <PR-url>` in that same turn. Don't wait to be asked. This applies every time, not just once per PR: if the owner sends it back with changes and you resolve them again, run it again once the PR is mergeable again. The same applies when you hand over an issue (`$RESOLVE_PR_OPEN_CMD <issue-url>`).

### `RESOLVE_PR_OWNER_QUEUE=1`: the issue-driven queue

Unset: only the loop below applies. Set to `1`: for work driven from an issue (not an ad-hoc PR someone else opened), the cycle is:

1. **Review the issue's title and body** against what's actually landed in the repo (don't just proofread — verify claims, cross-check schemas/fields/links against real code and real data where possible). Fix real errors directly on the issue (edit body, post a summary comment explaining what changed and why). Hand the issue over (and open it per `RESOLVE_PR_OPEN_CMD`, if set).
2. **Stop and wait** — do not implement until the owner explicitly says to (e.g. "implement issue #N"). This is a hard gate, not a suggestion.
3. Once told to implement: delegate implementation (issue → PR), verify every acceptance criterion for real, post AC results as a PR comment, then run this skill's loop (fresh review → fix → re-review) until the PR is genuinely mergeable.
4. **Hand the PR over** (and open it per `RESOLVE_PR_OPEN_CMD`, if set) to the owner for final manual review and merge — do not merge it yourself unless the owner has separately said to.

Treat "take the next issue" as step 1 of this cycle for whichever issue is next in the owner's stated order, not as an instruction to implement.

## The loop

Repeat until the stop condition or until blocked on a user decision.

### 1. Gather (never partial)
Fetch: PR metadata + body + diff, review bodies/verdicts, and **all** inline comments with `--paginate`.
For any comment with `in_reply_to_id`, fetch the **root and every ancestor**. A reply ("do it the second way", "yes, that") is meaningless alone. If the root still doesn't pin down the referent — **ask, don't infer**.
Read repo policy: `.github/ISSUE_TEMPLATE/`, CONTRIBUTING, CLAUDE.md, user memory — for issue templates, acceptance-criteria rules, and **content language** (e.g. English-only).
Command forms: see `reference.md`.

### 2. Pick your signature — before posting anything
Format `<ToolName>-<N>`. `<ToolName>` is the agent/tool actually doing this iteration (`Claude`, `Cursor`, `Codex`…). Scan existing PR comments for `— <YourToolName>-<n>` and take highest + 1; none found → 1. A different tool starts its own sequence.
**Every** posted artifact — thread replies, PR comment, review comments, issue bodies, issue comments, PR body edits — ends with `— <ToolName>-<N>`.

### 3. Triage each thread
| Thread type | Action |
|---|---|
| Mechanical / unambiguous | Fix it in code |
| Two+ valid approaches, unclear scope, conflicting guidance, "your call" | **AskUserQuestion with concrete options.** Do not decide. |
| "File a separate issue" | Duplicate-check first (`gh issue list --search`, incl. closed). Overlaps an existing issue → comment there instead. Template required but missing → **ask** (add template vs. file now + flag gap). |
| "Confirm you ran X" | Run X or say plainly you could not |
| "Say the same in the PR body" | Edit the PR body |

Reply to **every** thread in its own thread (`in_reply_to`), in the repo's language, signed. Post issue numbers only after the issue exists.

### 4. Test, and grade your own evidence
State the level reached: **scaffold** (compiles, cheap unit tests) vs. **full** (data/fixture/cache/env-backed). Paste real output. Missing fixture/env → say so and ask; never let "tests pass" stand in for the run you didn't do.

**No AskUserQuestion tool (e.g. you are a subagent)?** That does not promote you to decision-maker. Hand the question up to your caller as labelled options and stop on that thread — never convert "I can't ask" into "so I'll pick".

### 5. Commit & push
One coherent commit per topic (not per comment) to the PR branch.

### 6. Fresh re-review — mandatory
Spawn a **new subagent with no prior conversation context**. Give it the current diff and the thread list marked *context only — do not re-litigate resolved threads*. It must independently verify claimed fixes exist in the code, add its own analysis, post genuine findings as **inline** review comments (file+line), signed with **its own** `<ToolName>-<N>`. Nothing new → one clean-review comment. **Filler findings are a failure, not thoroughness.**

### 7. Acceptance criteria
PR closes an issue → fetch it. AC checkable by a command with its own exit code → run them, report pass/fail with output. AC missing or merely descriptive → say so.

### 8. Stop or loop
Stop when: no unresolved threads AND tests at the honest level AND fresh review clean (or its findings resolved in a later iteration) AND AC satisfied. New findings/threads → loop to 1 with N+1. Blocked on a user-only decision → stop and ask.

## Red flags — STOP

- "I'll pick the approach that seems best" → that's the ambiguous case. Ask.
- "The reviewer said it's my call" → still ask; "my call" means *a rationale is owed*, not *guess silently*.
- "The parent comment is probably about X" → fetch it.
- "No template exists, I'll just file it" / "I'll add one myself" → ask which.
- "Close enough to passing" → grade the evidence.
- "I'll hand it back to the human reviewer instead" → step 6 is not optional and is not the same thing.
- "Nothing to sign, it's just a reply" → sign it.
- "Deadline / release cut / lead said don't block on questions" → a deadline is a reason to ask *fast*, never a licence to guess. Missing a release is recoverable; a merged guess with a reviewer's name on it is not.
- "The reviewer is offline, so I have to decide" → wrong escalation target. Ask *your user*, not the absent reviewer.

## Rationalizations

| Excuse | Reality |
|---|---|
| "Asking wastes a round trip" | A wrong implementation wastes the whole iteration and misleads the reviewer. |
| "One fix is obviously better" | Then say so as option A in AskUserQuestion; the user confirms in seconds. |
| "Unit tests are basically the integration tests" | They are not. Name the level you reached. |
| "A fresh reviewer will just repeat what I fixed" | Then tell it not to. Skipping it means nobody checked your fixes exist. |
| "Signing is cosmetic" | It's how the next iteration finds its own N and who said what. |
| "I have no way to ask, so deciding is the only option" | Stopping and reporting the options is the other option. Take it. |
| "Shipping something beats shipping nothing before the cut" | Not when "something" contains a coin-flip you told the reviewer was resolved. |
