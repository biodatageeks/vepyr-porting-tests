# AGENTS.md — biodatageeks/vepyr-porting-tests

## Goal

Gradually bring **validated** pieces of the VEP-to-vepyr porting work into this repository.

We port **only data-problem tests (tests that check whether vepyr gives predictable results on data)**. Other test types are not essential — we do not port them now.

Everything must be **curated**: short, concrete, human-readable, understood by a human before merge. Nothing anyone could pick holes in.

---

## Procedure (every piece)

1. **Issue** on `biodatageeks/vepyr-porting-tests` — short, to the point, validated by hand (what we port and why it is a data-problem, or an organisational kind of issue, e.g. moving the skeleton, code, documentation).
2. **Branch from `master`** — locally move over and commit only pieces validated by an agent **and** by hand, which certainly work. Small, working pieces.
3. **Push + PR** — only when you are sure.
4. **Babysit the PR → manual merge** — merge only after your own, deliberate OK.

One issue = one logical piece (e.g. one test). No bulk dumping.

### CI checks and workflows (since 2026-09-27, until revoked by the owner; #115 closed as NOT_PLANNED)

GitHub Actions workflows in `biodatageeks/vepyr-porting-tests` are **disabled** so as not to use up the organisation's minutes. Until revoked:

1. **We run all checks ourselves, locally** (among others `./issue_check --body-file`, `./check_normalised_input`, `tools/build_test_index --check`, tests and the issue's AC) — commands with exit codes, together with negative controls.
2. **The result is always recorded in a comment on the issue or the PR**: which command, which exit code, the key output — **whether the result was positive or negative**. Do not write "passed" without evidence, do not skip a check that failed. State the evidence level explicitly (scaffold vs full: real VEP/Docker/cargo).
3. **A PR in `state:awaiting-merge` = frozen branch.** While a PR carries this label, **we do not push** to its branch. If something must be fixed after it: first `./set_state pr N fixing` (with the owner's consent, since the owner approved the PR in `awaiting-merge`), only then push, and record the check results again in a comment (the sticky status comment, see "Issue and pull request lifecycle").
4. We do not start workflows (e.g. `gh workflow enable`) without the owner's consent; if an issue needs evidence from a real CI run, we ask for consent to enable it temporarily.

### Triage: severity label on every issue (since 2026-09-29)

Every issue in `biodatageeks/vepyr-porting-tests` has **exactly one** label `severity:low`, `severity:medium`, `severity:high` or `severity:critical`. We add it when the issue is filed (together with the template's label) and check it at every review of the issue; an issue with no severity label or with two is an error to fix right away.

Severity = the harm if the problem stays unfixed (not the effort and not the order of work):

- `severity:critical`: test results or the oracle are unreliable today (false greens, wrong ground truth), or the repo is completely blocked.
- `severity:high`: a check passes without checking anything; a real bug in delivered tooling; an open vepyr-vs-VEP discrepancy found by a data-test.
- `severity:medium`: gaps in tooling and documentation that contradict actual behaviour; a real vepyr-vs-VEP behaviour not covered by a data-test.
- `severity:low`: convenience, wording, cosmetics, "nice to have" proposals.

Rules: the label is assigned from the issue's content, not its title; be careful with `critical` and `high`; when torn between two levels, pick the lower one, and add a comment with the justification only if the owner asks for it. Changing the severity after the issue's content changed is part of reviewing it. Labels are created once (`gh label list` shows whether they exist).

### Issue and pull request lifecycle (since 2026-09-29, #158)

Where an issue or a PR is, and whether a PR is ready, is visible without reading the comment log: a state label, one sticky status comment, one verdict comment per reviewer, and two tools that enforce the rules.

**State labels.** Every open issue and PR in work carries **exactly one state label** (`state:*`; the prefix mirrors `severity:`). Issues: `state:implementing-issue` (the issue text is being written), `state:auto-reviewing-issue`, `state:fixing-issue`, `state:manual-reviewing-issue`. PRs: `state:implementing`, `state:auto-reviewing`, `state:fixing`, `state:auto-superreviewing`, `state:manual-reviewing`, `state:awaiting-merge`. An issue under implementation carries its PR's labels (see "Issue follows its PR" below). Only `./set_state` changes them (`./set_state pr N STATE [--no-mirror] [--dry-run]`, `./set_state issue N STATE [--dry-run]`, `./set_state issue N --clear`); it refuses any move not in this table (`./set_state --print-transitions` prints the same lines), refuses `state:manual-reviewing` unless `./pr_status --handover N` is READY, and refuses `state:awaiting-merge` unless `./pr_status N` is READY:

```text
issue (none) -> state:implementing-issue
issue state:implementing-issue -> state:auto-reviewing-issue
issue state:auto-reviewing-issue -> state:fixing-issue
issue state:auto-reviewing-issue -> state:manual-reviewing-issue
issue state:fixing-issue -> state:auto-reviewing-issue
issue state:manual-reviewing-issue -> state:fixing-issue
issue (none) -> state:implementing
issue state:manual-reviewing-issue -> state:implementing
pr (none) -> state:implementing
pr state:implementing -> state:auto-reviewing
pr state:auto-reviewing -> state:fixing
pr state:auto-reviewing -> state:auto-superreviewing
pr state:auto-reviewing -> state:manual-reviewing
pr state:fixing -> state:auto-reviewing
pr state:auto-superreviewing -> state:fixing
pr state:auto-superreviewing -> state:manual-reviewing
pr state:manual-reviewing -> state:fixing
pr state:manual-reviewing -> state:awaiting-merge
pr state:awaiting-merge -> state:fixing
issue state:manual-reviewing-issue -> (none)
```

Meaning: `auto-reviewing -> fixing` on a `CHANGES_REQUESTED` verdict; `auto-reviewing -> auto-superreviewing` when the PR is in the super-review tier, otherwise straight to `manual-reviewing`; `manual-reviewing` = the owner reviews the PR; `manual-reviewing -> fixing` when the owner asks for changes; `manual-reviewing -> awaiting-merge` when the owner approves; the owner merges (never an agent). When the owner says "implement issue #N", the manager runs `./set_state issue N implementing` (from `state:manual-reviewing-issue` or no label); an issue the owner approved but that is not implemented now drops its label instead (`./set_state issue N --clear`). **Pushes to a PR branch only in `state:implementing` or `state:fixing`**; from any other state first `./set_state pr N fixing` (`awaiting-merge` is the frozen branch: ask the owner first). Merged or closed items keep their last label; queues use `--state open`. Issue side: the writer sets `state:implementing-issue` when filing, `state:auto-reviewing-issue` once `./issue_check --body-file` exits 0 and the fresh issue review starts, `state:fixing-issue` for findings, and `state:manual-reviewing-issue` when the review is clean (the owner's hand check; implementation waits for the owner's word).

**Issue follows its PR (#177).** Once an issue is in `state:implementing`, every `./set_state pr N STATE` mirrors the move to the PR's closing issues, read from `gh pr view N --json closingIssuesReferences` (GitHub's closing keywords and manual "Development" links, not plain `#N` mentions; empty for a PR whose base is not the default branch, which then mirrors nothing). A closing issue whose single `state:*` label is the PR's old state gets the same move; one already at the PR's new state is in sync (no edit); a closed one, or one with no `state:*` label or an `-issue` label, is not opted in (skipped, one `note:` line on stderr); any other label is out of sync and the whole move is refused (exit 1, no edit). All or nothing: `./set_state` checks the transition, the gate and every issue first, edits the issues, then the PR, undoes the edits already made if one fails (`nothing changed`, exit 2), and reads every label back. `--no-mirror` moves the PR only (e.g. the second of two open PRs closing one issue). The `./pr_status` gates read the PR's labels only; after the merge the issue keeps its last label (`state:awaiting-merge`).

**Owner queues.** To review: `gh pr list --repo biodatageeks/vepyr-porting-tests --label state:manual-reviewing --state open`. Approved, waiting for the merge: `gh pr list --repo biodatageeks/vepyr-porting-tests --label state:awaiting-merge --state open`. Issues to hand-check: `gh issue list --repo biodatageeks/vepyr-porting-tests --label state:manual-reviewing-issue --state open`. Issues being implemented: `gh issue list --repo biodatageeks/vepyr-porting-tests --label state:implementing --state open` (the other PR states likewise, e.g. `--label state:manual-reviewing`).

**Acceptance criteria are the spec, and they must fail on master.** They stay visible to the implementer and the reviewer. Each AC quotes what it returns on the current master; an AC that already passes on master gates nothing, unless it is labelled a regression guard.

**Sticky status comment.** Each PR has exactly one issue comment (not a review body: only issue comments can be edited in place) whose first non-empty line is `### pr-status:v1`: a human table of every AC (command, exit, expected, head sha, evidence level) and, below it, one fenced `json` block with the same data (`{"v":1,"head":...,"stale":...,"ac":[{"id","cmd","exit","expected","sha","evidence","manual"}]}`; a manual row has `exit`/`expected` `null` and a `reviewer`). The implementer edits it in place (`gh api -X PATCH repos/OWNER/REPO/issues/comments/<id> -F body=@file`), never posts a second one. An AC must not call ./pr_status on its own PR (#177): the gate is the hand-over precondition, not an AC, so it is never a row of its own sticky; the agent runs `./pr_status --handover N` before the move and quotes its output in a separate hand-over comment. An issue's own dogfooding AC is structural (the PR carries exactly one `### pr-status:v1` comment and exactly one `state:*` label, read with `gh pr view N --json comments,labels`), with no gate call and no provisional manual row. Before any push: move the PR to `state:fixing`, set `"stale": true`, push, re-run the AC, then rewrite the rows with the new sha. The state label, not this comment, holds the state.

**Fresh review with independent probes.** The first reviewer is a fresh `opus-low` with no implementation context. It (a) runs every AC as written; (b) runs 2-3 **independent probes** of its own that are not in the AC list, quoting each command and exit code; (c) must **mutate** what each AC protects (delete the line, flip the value, edit the fixture; in a scratch copy) and confirm the AC then fails. An AC that does not fail on its mutation is a finding against the AC, not a pass. A `"manual": true` row is exempt from (c) and names its human reviewer. It posts one verdict (an issue comment or a COMMENT review) whose first non-empty line is `### pr-review:v1`, its text, then a fenced `json` block `{"v":1,"role":"review","model":"opus-low","verdict":"APPROVE"|"CHANGES_REQUESTED","sha":"<head>","probes":N,"mutations":[{"ac":id,"exit":code}]}`. Agents post through the owner's account, so a native APPROVE review is impossible (GitHub forbids approving your own PR); the verdict lives in this block.

**Super-review by a different model (risk tier only).** After the first reviewer's APPROVE with all AC green, a risky PR gets exactly one more fresh reviewer on a different model (`model: sonnet` on the Agent tool; the first is `opus-low`), `"role":"superreview"`, with the issue, the diff and the evidence table but not the first verdict, and an adversarial brief (what do the AC not cover, does the diff realise the issue's intent, what could break the oracle or a result silently). One pass, no rounds. Its finding blocks only if the comment quotes a command that reproduces it (`CHANGES_REQUESTED`); anything else is a note for the owner. Fixes after it go back to the first reviewer on the new head; the super-review is repeated only if its own blocking finding was fixed and the owner asks. `./pr_status` computes the tier itself from the PR's files, README diff and closing issues:

Super-review tier: a changed path under `tests/data/`, or `tests/data_dirs.rs`, `./bless` (the root wrapper), under `tools/bless/`, `tools/vep_flags.toml`, `tools/normalize_input`, under `tools/run_tests/`; or a changed line of `README.md` inside the sections `## ./bless` or `## One mode: --everything`; or a closing issue labelled `severity:high` or `severity:critical`.

Known limits of the tier: a tier file renamed out of the tier shows only its new path in `gh pr view --json files`, so moving `tests/data/x` elsewhere skips the super-review; and `gh pr view --json files` is limited to 100 files. The reviewer checks `git diff --name-status -M origin/master...HEAD` for renames out of `tests/data/` and `tools/bless/` and requests the super-review by hand.

**Hand-over.** The gate has two stages that differ only in the state-label check (#177). Before the move the agent runs `./pr_status --handover N` (the PR is in `state:auto-reviewing` or `state:auto-superreviewing`); when it exits 0, `./set_state pr N manual-reviewing`, which runs the same hand-over stage and refuses otherwise. `./pr_status N` (the owner's stage) wants `state:manual-reviewing` or `state:awaiting-merge`. `./pr_status` is read-only: it prints one `FAIL <check>: <reason>` line per failed check (sticky comment, sha, AC exits, verdict, probes, mutations, super-review, state label) or `READY`. On the owner's approval the agent runs `./set_state pr N awaiting-merge`, which runs the owner's stage and so catches any push since the hand-over.


### `tests/INDEX.csv` in parallel PRs (since 2026-10-02, #151)

`tests/INDEX.csv` is generated by `tools/build_test_index` from `tests/data/*/test.toml` and stays tracked (reviewers read the table on GitHub; `.gitattributes` marks it `linguist-generated`, so GitHub collapses it in diffs). Parallel data-test PRs that add rows at the same sorted position, or a change that edits many rows, conflict on it. Before the hand-over of a PR that touches `tests/data/` or `tests/INDEX.csv` (never in `state:awaiting-merge`, the frozen branch), bring its branch up to date with `master`: if the PR is not in `state:implementing` or `state:fixing`, first `./set_state pr N fixing`; in either case set `"stale": true` in the sticky status comment ("Before any push" above), then merge `master` into the branch (`git fetch origin && git merge origin/master`: a merge commit, no rebase, no force-push), run `tools/build_test_index`, commit the file if it changed, push, and `tools/build_test_index --check` must exit 0 (record the command and exit code in the sticky; `dt verify` runs this check as its `build_test_index` step). A conflict in `tests/INDEX.csv` is never resolved by hand: `git checkout <master> -- tests/INDEX.csv && tools/build_test_index && git add tests/INDEX.csv` (`<master>` = `origin/master`), then continue the merge (`git commit --no-edit`). Mass changes of the index (schema or column change, re-bless of oracles) merge first and in-flight PRs merge `master` after them.

---

## Goal for today

As fast as possible on the new repo:

1. A **working skeleton** of the project.
2. **10 data-problem tests validated by hand** — each with its own issue, understood end to end, certainty before merge.

---

## Quality rules (non-negotiable)

| Rule | Means |
|------|-------|
| Curated | Only what works and is needed. Zero "I'll throw it in and see". |
| Human-readable | Issues, PRs, commits, comments — technical but readable for a human, to the point. |
| Understood | The author must be able to explain every ported piece. |
| Data-problem only | Other test categories — out of scope. |
| Small steps | A working piece > a large unfinished batch. |
| Manual validation | The agent helps; final certainty = a human. |

---

## Design decisions (owner — binding)

Record of decisions from conversations about `biodatageeks/vepyr-porting-tests`. The agent does **not** propose solutions that contradict the following.

### Cache: only full or `--add-contigs` (no micro-cache)

- **Forbidden:** a committed micro-cache (`tests/data/*_micro_cache*`), build/slice into git, tests that read a local slice instead of the pinned HF corpus.
- **Allowed:** the full VEP 116 cache from HuggingFace **or** the same corpus with `--add-contigs` at download (a subset of shards from **the same** datasets / revisions; flag name = accumulation in `--cache-dir`).
- Goal: reproducible behaviour as in the production environment; provenance = Hub + `PROVENANCE.json`, not a hand-made fixture.
- **Per-contig data-problem tests (by design):** every assertion declares a field of **required contigs** (e.g. `chr21,chrMT`). These must be exactly the contigs of the range in which the tested variants lie (VCF / assertion loci) — not a wider genome "just in case" and not narrower than the variants. The cache is downloaded with the matching `--add-contigs` (manifests trimmed to the shards on disk). The annotate lookup is **per `(entity, chrom)`** — shards of other chromosomes do **not** affect the CSQ of variants on the present contigs. This is not 100 % "root byte-identical = full genome", but for ordinary SNV/indel data-problems it is safe.
- **Per-contig limitations:** missing shards → variants on the missing contig are dropped / the run refuses; `motif`/`regulatory` have no `chrMT` (`--add-contigs chrMT` alone is illegal — the README must say so); the FASTA for HGVS stays full; BND / a mate on another contig — outside the slice's scope; do not assert "the full list of the genome's contigs".
- CLI/AC details: **SSOT = the GitHub issue** (e.g. #4), not this file.

### Engine: no pin to a specific vepyr / dfbf sha or tag

- The target repo must work on **every** vepyr sha and **every** vepyr tag (and the dependency ladder matching it).
- We do **not** port the model "PINS.toml = the only allowed vepyr/dfbf revision on which the tests are green".
- The version under test is a **run parameter** (e.g. `--vepyr REF`), not a frozen contract of the repository. Pinning the cache datasets (HF) is a separate matter — it concerns the data corpus, not the engine version.

### No smoke tests — only working scripts / data-problem tests

- **Forbidden:** `*_smoke.rs`, "smoke gates", dummy tests for agents ("so that something is green").
- **Allowed:** entry scripts (`./run_tests` and similar) and proper data-problem tests with a clear verdict on data.
- Smoke tests from earlier porting work (micro-cache, full-cache smoke etc.) do **not** enter the curated repo.

### Language: English only (target repo)

In `biodatageeks/vepyr-porting-tests` **everything is in English**: code, comments, README, docs, agent rule files and skills, issue titles and bodies, PR titles and bodies, commit messages, milestone names, labels, user-facing CI logs, GitHub templates. No Polish titles/descriptions in this repo.

### README = the current state of the repo

`README.md` in `biodatageeks/vepyr-porting-tests` must **always reflect the actual state of the tree** after a given PR: only what already works in the code (e.g. `./run_tests` — flags, behaviour, limitations). Documenting future options "in advance" is forbidden. Every issue/PR that changes the CLI or user-facing behaviour updates the README in the same PR.

---

## Repository map

| Path | Role |
|------|------|
| `biodatageeks/vepyr-porting-tests` (this repo) | Target (clean, curated repo) |

This file describes the agent workflow; it does not replace the README. The per-machine setup (links from a workspace directory and from the user skill directory into this checkout) is in `README.md`, section "Agent setup (per machine)".

---

## For agents

- Do not port anything a human has not validated by hand as a data-problem.
- Do not open PRs "in advance"; do not merge without the owner's explicit request.
- Prefer: issue → small commit on a branch → evidence that it works → only then a PR.
- Write material (issue/PR/commit messages) concretely: *what*, *why a data-problem*, *how verified*.
- Scope creep (non–data-problem, refactors beyond the piece, docs "for later") — reject.
- Follow the **Design decisions** section: zero micro-cache, zero smoke tests, engine unpinned; **English-only**; **README = the current state of the repo** (do not document the future).
- Write issues/PRs/commits on `biodatageeks/vepyr-porting-tests` in English; **SSOT for issue content = GitHub**, not this file.
- Milestone 1: orient yourself with the terse overview below; take scope/AC from the issue (AGENTS.md may be out of date).
- Do not copy or "synchronise" long descriptions from AGENTS.md into issues — edit only GitHub.

---

## Milestone 1 — Skeleton (overview)

**SSOT:** the milestone and issues on GitHub — https://github.com/biodatageeks/vepyr-porting-tests/milestone/1  
The descriptions in this section are **only indicative** (one sentence). Change scope, AC, depends and the order of details **only** in the issue; do not treat the list below as a contract.

**Goal (short):** a curated skeleton for data-problem tests (HF cache full/`--add-contigs`, `./run_tests`, the engine as a parameter; no micro-cache and no smoke tests).

| Issue | Indicatively |
|-------|--------------|
| [#1](https://github.com/biodatageeks/vepyr-porting-tests/issues/1) | Bootstrap of the repo's identity (LICENSE, toolchain, short README). |
| [#2](https://github.com/biodatageeks/vepyr-porting-tests/issues/2) | Pins of the HF datasets (+ FASTA), no engine pin as a contract. |
| [#3](https://github.com/biodatageeks/vepyr-porting-tests/issues/3) | `./run_tests` skeleton (entry + flags, incl. `--add-contigs`; fetch later). |
| [#4](https://github.com/biodatageeks/vepyr-porting-tests/issues/4) | Fetch through `./run_tests` (`--add-contigs`, accumulation, summary; owner: self-hosted runner). |
| [#5](https://github.com/biodatageeks/vepyr-porting-tests/issues/5) | `tests/common` helpers for `$VEPYR_CACHE_ROOT`. |
| [#6](https://github.com/biodatageeks/vepyr-porting-tests/issues/6) | Data-problem form + labels on GitHub. |
| [#7](https://github.com/biodatageeks/vepyr-porting-tests/issues/7) | README Quick start closing Milestone 1. |

After M1 → Milestone 2: a separate issue for every data-problem test (goal: 10); details only on GitHub.
