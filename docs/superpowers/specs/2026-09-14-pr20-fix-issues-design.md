# Design: sub-issues fixing PR #20 review findings

- Repo: `biodatageeks/vepyr-porting-tests`
- Target PR: [#20](https://github.com/biodatageeks/vepyr-porting-tests/pull/20) `[harness] Data-test package + ./run_tests runs with --vepyr`
- Target branch for all fixes: `harness/issue-14-15-run-tests` (PR #20's branch — **not** `main`, PR is unmerged)
- Milestone: `2 — First data-test pilot`
- Review chain that produced these findings: Cursor/Bugbot → Claude (10 findings + verdict) → sitekwb (Q&A, pushback, decisions) → Claude-fixer (confirmed 8/10 as described, found the *actual* root-cause blocker not in the original 10, explained the rest)

## Source of truth

Every fix below traces to a specific PR #20 thread comment. Nothing here is invented; where Claude-fixer's investigation superseded or corrected the original Claude finding, the corrected version is what's designed.

## Open policy gap (flagging, not deciding)

This repo has **no CI workflows** (`.github/workflows/` does not exist). Your global instruction is "issue → falsifiable CI check green → implementation." I'm not adding a CI-prerequisite issue on my own initiative since that's scope beyond what PR #20's reviewers asked for — but implementing any issue below without a check gate breaks your own stated policy. Flagging for your call; my recommendation is issue #0 below, off by default (see options at the end).

## Ordered issue list

Order = blocker first, then engine.py-region fixes grouped to avoid merge conflicts, then cli.py-region fixes grouped, then the accepted behavior change, then docs. Only issue 1 is a *true* technical blocker for the others (nothing about `--vepyr` can be manually verified until it resolves); the rest are independent bugs and can be branched/reviewed in parallel if you'd rather not go strictly sequential.

| # | Title | Type | Root file(s) | Depends on |
|---|-------|------|---------------|------------|
| 1 | Resolve duplicate `datafusion-bio-format-*` crate versions blocking `--vepyr` | bug, **blocker** | `Cargo.toml`, `tools/run_tests/engine.py`, `tools/run_tests/cli.py` | — |
| 2 | Fix stale engine checkout on branch/tag refs + missing `--` separator | bug | `tools/run_tests/engine.py:272,286` | — |
| 3 | Avoid live checkout-directory reuse across `--vepyr` runs | bug | `tools/run_tests/engine.py:256` | — |
| 4 | Make `Cargo.lock` restore crash-safe | bug | `tools/run_tests/engine.py:398` | — |
| 5 | Sanitize `--vepyr REF` before the GitHub API call | hardening | `tools/run_tests/engine.py:152` | — |
| 6 | Resolve cache root to an absolute path once | bug | `tools/run_tests/cli.py:277` | — |
| 7 | Honor `--dry-run` when cache root comes from `$VEPYR_CACHE_ROOT` | bug | `tools/run_tests/cli.py:370` | 6 |
| 8 | Derive FASTA filename from the PINS.toml pin, not a hardcoded constant | bug | `tools/run_tests/tests.py:30` | — |
| 9 | Refactor `run_tests` `main()` to reduce nesting | cleanup | `tools/run_tests/cli.py:403` | 6, 7 |
| 10 | Redefine #15 AC4: default `--vepyr` to master HEAD when omitted | **decision, accepted this session** | `tools/run_tests/cli.py:462`, issue #15 | 9 |
| 11 | Harness docs pass (+2 trivial one-line cleanups) | docs | `README.md`, `docs/dynamic-vepyr-version-resolving.md` (new), `tools/run_tests/engine.py:3`, `tests/common_compile.rs:1,6` | — |

## Per-issue detail

### 1 — Resolve duplicate formats crate versions blocking `--vepyr` (BLOCKER)

Claude's original findings #1 (`engine.py:45` patch table) and #2 (`Cargo.toml:23` floating branch) were **not reproduced as stated** by Claude-fixer — `cargo check --tests` is green because `Cargo.lock` is committed and locks the float. But Claude-fixer found the *real* failure one step later: `cargo update --config engine.toml -p datafusion-bio-function-vep -p datafusion-bio-format-ensembl-cache -p datafusion-bio-format-vcf` (`cli.py:305-318`) exits 101 with `specification ... is ambiguous`, because the lockfile carries two sources for the same formats crates (dev-deps `branch=master` vs. dfbf's pinned `tag=v1.12.1`). **Every real `--vepyr` invocation currently fails before `cargo test` ever runs.** The `pytest tools/` suite never caught this because it fakes the cargo runner.

Fix direction (implementer's call, both are on the table): derive the `cargo update -p` specs as fully-qualified URLs (`-p 'https://.../datafusion-bio-formats.git#datafusion-bio-format-ensembl-cache'`) instead of bare names, *or* drop the `cargo update -p ...` step entirely since Claude-fixer confirmed plain `cargo check --config engine.toml` re-locks the patched packages on its own, *or* align the dev-dep revision with whatever formats revision the resolved `--vepyr REF` actually pins so only one copy of each crate ever exists. Whichever direction: the dev-deps' floating `branch = "master"` should stop being independent of the engine-resolution logic — right now it's a second, uncoordinated source of truth for "which formats revision."

**Acceptance criteria:**
1. `./run_tests --vepyr master ...` (or the project's existing pytest-with-real-cargo integration point) gets past the patch/update step without "is ambiguous".
2. A regression test (integration-level, not a faked cargo runner) asserts no two `[[package]]` entries for the same formats crate name with different sources survive a `--vepyr` run.
3. `cargo check --tests` stays green.

### 2 — Fix stale engine checkout + missing `--` separator

Confirmed by Claude-fixer with a local-remote repro: `git checkout --quiet --detach master` after `git clone --no-checkout` + `git fetch origin master` resolves the **clone-time local branch ref**, not the fetched head — so every `--vepyr <branch>` run after the first for a given engine repo can silently test a stale revision while the summary reports it as current. Fix: `git fetch origin <rev> && git checkout --detach FETCH_HEAD`.

Bundled in the same issue (same lines, same function, trivial): insert `--` before positional args in both the `git clone` (`:272`) and `git checkout` (`:286`) invocations. sitekwb pushed back that this doesn't change auto-resolution behavior for valid input — correct, and Claude-fixer confirmed it's hardening against a hostile `Cargo.toml` in the *target* `--vepyr REF` repo, not a behavior change for normal refs.

**Acceptance criteria:** automate Claude-fixer's manual repro (local git remote, second commit pushed to the tracked branch between two runs) as a test; assert the second run's resolved sha matches the new HEAD. Valid sha/tag/branch refs still resolve identically before and after the `--` insertion.

### 3 — Avoid live checkout-directory reuse across `--vepyr` runs

`.run_tests/src/<repo>` is one mutable directory, `checkout --detach`'d in place per run. Two runs with different refs (sequential, or overlapping if a prior `cargo test` is still compiling) can have the second checkout swap files under a live build — best case a compile error, worst case a green run whose reported `vepyr=<ref>@<sha>` doesn't describe what was actually compiled, which matters a lot in a repo whose purpose is a trustworthy assertion ledger.

**Acceptance criteria:** per-resolved-sha checkout directories (or a lock file with a clear error on concurrent use); two sequential runs with different resolved shas leave independent checkouts; `plan.vepyr_sha` in the summary matches the code actually compiled for that run.

### 4 — Make `Cargo.lock` restore crash-safe

`LockGuard` snapshots `Cargo.lock` in Python memory and restores it in a `finally`. A SIGKILL/OOM-kill mid-run leaves the checked-in lock permanently rewritten with no recovery path. Fix: restore via `git checkout -- Cargo.lock` (survives process death) instead of, or as a fallback after, the in-memory snapshot.

**Acceptance criteria:** simulate a hard kill mid-`cargo test` window in a test harness; `Cargo.lock` is unchanged (git-clean) on the next invocation without manual recovery.

### 5 — Sanitize `--vepyr REF` before the GitHub API call

`_resolve_sha` interpolates the raw `--vepyr` argument into `repos/{VEPYR_REPO}/commits/{ref}` with no validation (only the *resulting* sha is regex-checked). Low real risk since `ref` is the invoking user's own argument, but worth a sanity check/allowlist so a mistyped ref fails clearly instead of via an obscure `gh api` error — and so a `ref` containing `/`, `..`, or a query-string suffix can't redirect the call. **Allowlist must still accept `/`** (branch names like `feature/x` are legitimate).

**Acceptance criteria:** unit test — refs containing `..`, a leading `-`, or query-string-like suffixes are rejected before the `gh api` call with a clear usage error; valid branch names containing `/` still resolve.

### 6 — Resolve cache root to an absolute path once

`_resolve_cache_root` returns `inv.cache_dir` or `$VEPYR_CACHE_ROOT` as-is, no `.resolve()`. Python's `precheck_cache` stats it relative to the shell's cwd; cargo is spawned with `cwd=_repo_root()` and gets the same relative string via the `VEPYR_CACHE_ROOT` env var, resolved by the Rust side relative to the *repo root*. A relative `--cache-dir` from outside the repo root gets checked against two different directories.

**Acceptance criteria:** from a cwd different from the repo root, `./run_tests --cache-dir ./relative-cache ...`'s Python precheck and the cargo-run's Rust-side resolution report the same absolute path (regression test).

### 7 — Honor `--dry-run` with `$VEPYR_CACHE_ROOT` *(builds on #6)*

Guard is `if inv.cache_dir is not None and inv.dry_run` — with the cache root supplied via `$VEPYR_CACHE_ROOT` (the flow the README documents), `inv.cache_dir` is `None` even though a root does resolve, so `--dry-run` is silently ignored and a real clone + `cargo test` happens, contradicting the README's "`--dry-run` writes nothing; does not run tests." No existing test covers this combination. Fix: gate on the *resolved* root (`_resolve_cache_root(inv) is not None`), reusing #6's resolution point.

**Acceptance criteria:** `VEPYR_CACHE_ROOT=<dir> ./run_tests --dry-run --vepyr <ref>` writes nothing and runs nothing; add the missing pytest case.

### 8 — Derive FASTA filename from the PINS.toml pin

`FASTA_NAME` in `tests.py:30` is a hardcoded literal, independent of the `[grch38_fasta]` pin `fetch.py` actually uses to name the fetched FASTA. They agree only by coincidence today; a pin bump would make a valid, complete cache look "missing" to `precheck_cache`, with no way out since re-fetching writes the pinned name again. The PR's own tests already monkeypatch around this — a live symptom of the coupling.

**Acceptance criteria:** bump a test fixture's `[grch38_fasta].fa_name` pin; a fully-fetched cache under the new name passes `precheck_cache` with no `FASTA_NAME` involved; remove the `monkeypatch.setattr(tests, "FASTA_NAME", ...)` workaround from the existing suite.

### 9 — Refactor `main()` *(after #6, #7 to avoid churn)*

Your direct comment on `cli.py:403`: too many nested ifs, not idiomatic. Extract parse → resolve → precheck → dispatch into named functions / early returns. No behavior change — this is why it's sequenced after the two small `main()`-adjacent bugfixes above rather than before.

**Acceptance criteria:** implementer proposes and states a concrete complexity threshold (e.g., radon/flake8 `C901`) in the issue body; existing `pytest tools/` stays green with unchanged behavior.

### 10 — Redefine #15 AC4: default `--vepyr` to master HEAD *(your decision this session)*

You confirmed: omitting `--vepyr` should resolve `biodatageeks/vepyr`'s current master HEAD rather than failing with `MISSING_VEPYR`. This **supersedes #15's existing AC4** ("Data-test runs without `--vepyr` fail with usage pointing at the required engine parameter") — the issue body must say so explicitly and amend that AC rather than silently drift from it. Tradeoff worth stating in the issue: a default run's engine version becomes implicit/floating, which cuts against reproducibility — the design should still print the resolved sha prominently in the summary, and `--vepyr REF` remains available whenever a pinned/reproducible run is wanted.

**Acceptance criteria:** `./run_tests` (no `--vepyr`, cache + targets present) resolves and runs against `biodatageeks/vepyr`'s current master sha, printing that sha prominently in the summary; `--vepyr REF` still overrides; #15's AC4 text is updated in the same PR that lands this.

### 11 — Harness docs pass + 2 trivial cleanups

Every remaining PR #20 thread comment was a clarity/documentation question already answered correctly by Claude-fixer inline — batching them into one docs-only issue (no logic risk, one coherent topic: "make the harness self-explanatory"):

- New `docs/dynamic-vepyr-version-resolving.md`: the resolve flow Claude-fixer described end-to-end (`gh api commits` → `gh api contents Cargo.toml` → clone dfbf+formats → `engine.toml` patch table → `cargo update`/`test` → `LockGuard` restore).
- README: explain "targets" = cargo test targets = `tests/data_*.rs` files (one file, one target).
- `--vepyr` help/README: examples — tag has no `v` prefix, sha may be full or short, behavior when omitted (update to match #10 once that lands).
- `cli.py:94` docstring: "also used as `$VEPYR_CACHE_ROOT` for the run" instead of the current opaque phrasing.
- README: Windows caveat — `./run_tests` is a bash script, needs Git Bash/WSL.
- `engine.py:3`: reword "No sitekwb forks" → "only the public biodatageeks crates named by REF's Cargo.toml" (the current wording is meaningless to a biodatageeks-repo reader with no sitekwb context).
- Cleanup: delete the issue-number comment in `tests/common_compile.rs:1` (per your explicit "remove it"); drop the redundant `#![allow(dead_code, unused_macros, unused_imports)]` on `tests/common_compile.rs:6` — confirmed a no-op by Claude-fixer since `tests/common/mod.rs:14` already covers it and the two items in that file are `#[test]` fns, never dead.

**Not a separate issue:** the `Cargo.lock` package-count / `tokio` questions. Both are already-adequate answers (543 packages = real transitive closure of the annotate engine; `tokio` is deliberate prep for #16's first async data-test) — resolved as a side effect of #1's dedup and captured in this docs pass, not worth their own issue.

## What I did *not* turn into an issue

- `tests/common/mod.rs:14` and `tests/common/provenance.rs:45` — sitekwb's questions, both answered correctly inline by Claude-fixer with no code change implied (the `allow` is correctly scoped to the shared module; `"0.17.2"` is an arbitrary literal, not a pin). No action.

## Decision needed from you (only remaining open item)

Whether to add a CI-prerequisite issue (see "Open policy gap" above) before any of the 11 land, or treat this harness-fix batch as continuing the existing no-CI skeleton-phase exception (like #14/#15/#7 before it).
