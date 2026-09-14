# Design: Issue #16 — Runner n=16 consequence content

**Repo:** biodatageeks/vepyr-porting-tests  
**Issue:** [#16](https://github.com/biodatageeks/vepyr-porting-tests/issues/16)  
**Date:** 2026-09-14  
**Status:** Approved for implementation (human Step 1 gate closed on the issue)

## Goal

Port one curated **data-problem** test: for a fixed SNV, vepyr’s emitted CSQ carries predictable consequence **content** for named transcripts under the pinned VEP 116 Hugging Face corpus (full or `--add-contigs`), not under a committed micro-cache.

## Non-goals

- Ledger rows n=17 (order) and n=50 (distance count) — separate issues if ever ported
- Harness work (#14 helpers, #15 `./run_tests` run path) — parallel PR; this change only consumes that API
- Engine SHA/tag pin in the repository (`--vepyr REF` remains a run-time parameter)
- Micro-cache, smoke tests, sitekwb overlays

## Approach (locked)

**Thin consumer (Approach A):** one `tests/data_*.rs` file with n=16 assertions only. Call shared helpers from #14 (`cache`, `ledger`, `annotate`, `csq`). No inlined CSQ/annotate duplicates. Worktree branched from `master`; rebase onto harness after #14+#15 merge; re-measure literals on VEP 116 before hardening expects.

## Layout

| Item | Value |
|------|--------|
| Worktree | `biodatageeks-vepyr-porting-tests-issue-16` |
| Branch | `data-test/issue-16-consequence-content` (from `master`) |
| Source | `sitekwb-vepyr-porting-tests/tests/port_runner_consequence_content.rs` → `buffer_to_output_renders_the_three_named_consequences` |
| Target | `tests/data_runner_consequence_content.rs` (matches #15 discovery glob `data_*.rs`) |
| Flavour | `ensembl` |
| Contigs | `required_contigs = ["chr21"]` — exactly the contig of the asserted locus, not a wider genome |

## Contig rule

Contigs are **not** algorithmically derived by tooling. They are declared to match the CHROM values of asserted loci.

For this row the Perl input is `21 25585733 25585733 C/T + rs142513484` → required contig **`chr21`** only. Fetch with `./run_tests --cache-dir DIR --add-contigs chr21`. Lookup is per `(entity, chrom)`; shards for other chromosomes do not affect CSQ on this locus.

## Data flow

1. Open the `ensembl` flavour under `$VEPYR_CACHE_ROOT` via `tests/common` (#5).
2. Enforce `required_contigs = ["chr21"]` (hard panic + repair command naming `./run_tests … --add-contigs chr21` if shards/manifests are missing). Never skip.
3. Annotate a single-record VCF for `21 25585733 rs142513484 C T` through #14 annotate helpers.
4. Parse CSQ via #14 `csq` helpers.
5. Assert content for the three named transcripts and an exact Feature inventory measured on the **pinned VEP 116** corpus.

## Assertions (shape)

Keep the same observation point as sitekwb (CSQ via annotate → VCF), not Perl `_buffer_to_output` / VEP-tab:

- Row identity: CHROM/POS/ID/REF/ALT = `21`, `25585733`, `rs142513484`, `C`, `T`
- Per Feature `ENST00000307301`, `ENST00000352957`, `ENST00000567517`: Gene, Feature, Feature_type, Consequence, cDNA/CDS/protein positions, Amino_acids, Codons, IMPACT, STRAND (empty slots where Perl had blanks)
- `DISTANCE` on `ENST00000567517` — expect `2407` only if re-measure confirms the same transcript end on v116
- Exact sorted set of CSQ `Feature` values = the three named transcripts plus any extras present on the **v116** pin (do **not** copy `EXTRA_V115_TRANSCRIPTS` or cDNA `997` from sitekwb without re-measurement)

## Re-measure protocol

After #14+#15 are available on the branch tip:

```text
./run_tests --cache-dir DIR --add-contigs chr21 --vepyr REF
# or: VEPYR_CACHE_ROOT=DIR ./run_tests --vepyr REF
```

Dump CSQ groups for this locus; fill EXTRA inventory and missense cDNA (and DISTANCE if needed); only then commit hardened `assert_eq!` literals. Until measured, leave explicit TODOs or intentionally failing placeholders — never silent v115 copy.

## Optional control (out of default scope)

`cdna_position_follows_the_committed_shards_utr_length` (parquet `cdna_coding_start`) is **not** part of the first commit. Revisit only if v116 cDNA still disagrees with Perl and needs an executable fixture explanation.

## Error handling

Missing `$VEPYR_CACHE_ROOT`, wrong provenance/revision, or missing `chr21` shards → panic with repair text (same contract as #5/#14). No `#[ignore]`, no skip-on-missing-cache.

## Git / PR workflow

1. Implement only in the issue-16 worktree.
2. Small English commits: scaffold + assertion shape; then post-rebase re-measure fill.
3. Rebase onto `master` after harness merge; wire real helper paths; green run.
4. Draft PR → Bugbot → fixes → ready for human review. `Closes #16`. Do not merge without owner OK.
5. Update issue #16 Step 2 on GitHub (SSOT): not provisional; flavour `ensembl`; AC matching this doc. README only if user-facing CLI changes (a lone `data_*.rs` usually needs no README churn once #15 documents discovery).

## Acceptance (human before merge)

- [ ] `required_contigs` is exactly `chr21`
- [ ] Three named ENST\* field tuples + DISTANCE + exact Feature inventory come from **v116** measurement
- [ ] No micro-cache path; no smoke
- [ ] Green under `$VEPYR_CACHE_ROOT` / `--cache-dir` + `--vepyr REF`
- [ ] Owner compared asserted fields to a known-good annotate on the same pin

## Dependency on parallel harness

| Surface (from #14+#15 plan) | Use in #16 |
|-----------------------------|------------|
| `tests/common/annotate.rs` | Annotate wrapper / config |
| `tests/common/csq.rs` | Layout, groups, `field`, `group_for` |
| `tests/common` cache + ledger | Root + `required_contigs` |
| `./run_tests --vepyr REF` | Engine ladder + `cargo test --test data_*` |
| Glob `tests/data_*.rs` | Discovery / `--list` |

Until that PR lands, this branch may not compile against `master` alone; that is accepted. Integration testing waits for the harness ship, then rebase + re-measure.
