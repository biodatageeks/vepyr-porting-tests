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
3. **The `merge_ready` label on a PR = frozen branch.** While a PR has this label, **we do not push** to its branch. If something must be fixed after the label: first remove the label (or ask the owner), only then push, and record the check results again in a comment.
4. We do not start workflows (e.g. `gh workflow enable`) without the owner's consent; if an issue needs evidence from a real CI run, we ask for consent to enable it temporarily.

### Triage: severity label on every issue (since 2026-09-29)

Every issue in `biodatageeks/vepyr-porting-tests` has **exactly one** label `severity:low`, `severity:medium`, `severity:high` or `severity:critical`. We add it when the issue is filed (together with the template's label) and check it at every review of the issue; an issue with no severity label or with two is an error to fix right away.

Severity = the harm if the problem stays unfixed (not the effort and not the order of work):

- `severity:critical`: test results or the oracle are unreliable today (false greens, wrong ground truth), or the repo is completely blocked.
- `severity:high`: a check passes without checking anything; a real bug in delivered tooling; an open vepyr-vs-VEP discrepancy found by a data-test.
- `severity:medium`: gaps in tooling and documentation that contradict actual behaviour; a real vepyr-vs-VEP behaviour not covered by a data-test.
- `severity:low`: convenience, wording, cosmetics, "nice to have" proposals.

Rules: the label is assigned from the issue's content, not its title; be careful with `critical` and `high`; when torn between two levels, pick the lower one, and add a comment with the justification only if the owner asks for it. Changing the severity after the issue's content changed is part of reviewing it. Labels are created once (`gh label list` shows whether they exist).

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
