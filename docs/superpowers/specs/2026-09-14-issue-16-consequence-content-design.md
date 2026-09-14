# Design: Issue #16 — Runner n=16 consequence content

**Repo:** biodatageeks/vepyr-porting-tests
**Issue:** [#16](https://github.com/biodatageeks/vepyr-porting-tests/issues/16)
**Date:** 2026-09-14, rewritten 2026-09-15 against the finalized issue
**Status:** Implemented on `data-test/issue-16-consequence-content`

## Goal

Port one curated **data-problem** test: for the fixed SNV `chr21:25585733 C>T`
(rs142513484), vepyr's emitted CSQ must match the native Ensembl VEP 116 reference
run field-for-field, under the pinned VEP 116 Hugging Face corpus
(`--add-contigs chr21`), not under a committed micro-cache.

## Owner decisions this design implements

The issue carried three open questions; all three were closed by the owner on
2026-09-15 and this document is the rewrite that follows them.

| # | Decision | Consequence here |
|---|----------|------------------|
| DECIDED 1 | Canonical oracle is the **`default`** VEP 116 run, not `--everything` | Expected values come from the 23-field `default` CSQ layout; the default `AnnotateVcfConfig` *is* the oracle, no field overrides |
| DECIDED 2 | Input contig spelling is **`chr21`** (reverses the earlier `21` proposal) | One spelling in the input VCF `#CHROM` and in `required_contigs`; no harness-level `chr21` → `21` renaming |
| DECIDED 3 | Assertion scope is the **full 34-group inventory, field-for-field** | Not "3 named transcripts + a count": 34 groups × 23 fields, all asserted |

## Non-goals

- Ledger rows n=17 (order) and n=50 (distance count) — separate issues if ever ported
- Fixing the harness follow-ups #21/#22/#25 that block `./run_tests --vepyr REF`
- Engine SHA/tag pin in the repository (`--vepyr REF` remains a run-time parameter)
- Micro-cache, smoke tests, sitekwb overlays
- The `cdna_position_follows_the_committed_shards_utr_length` parquet control —
  deliberately **not** ported: cDNA is measured directly on the pin by this test,
  so a second, parquet-reading explanation of the same number is redundant.

## Layout

| Item | Value |
|------|--------|
| Worktree | `biodatageeks-vepyr-porting-tests-issue-16` |
| Branch | `data-test/issue-16-consequence-content` |
| Source | `sitekwb-vepyr-porting-tests/tests/port_runner_consequence_content.rs` → `buffer_to_output_renders_the_three_named_consequences` |
| Ledger row | `Runner.ledger.toml` n=16 (`perl/Runner.t:244-289`), category `analog-port` |
| Target | `tests/data_runner_consequence_content.rs` (matches the `data_*.rs` discovery glob) |
| Flavour | `ensembl` (**not** `merged`: `merged` yields 41 groups at this locus) |
| Contigs | `required_contigs = ["chr21"]` — exactly the contig of the asserted locus |

## Branch dependency (read this before rebasing)

This branch is built **on top of the still-unmerged PR #20**
(`harness/issue-14-15-run-tests`, closes #14/#15), not on `master`, because every
helper it consumes lives only there. **A second rebase onto `master` is required once
#20 merges** — that step is part of this work, not an optional tidy-up.

PR #20 also carries 11 open follow-ups (#21–#31). Two matter here:

- **#21** — duplicate `datafusion-bio-format-*` crate versions break the cargo patch
  table, so `./run_tests --vepyr REF` cannot currently run the data-tests. This
  affects only the `--vepyr` wrapper path.
- **#22 / #25 / #30** — stale/unsanitized `--vepyr` engine checkout, same path.

Neither affects a direct `cargo test` against `$VEPYR_CACHE_ROOT`, which is how this
test was verified (see *Verification* below).

## Contig rule

Contigs are **not** derived by tooling; they are declared to match the CHROM values of
the asserted loci. For this row that is `chr21` only. Fetch with
`./run_tests --cache-dir DIR --add-contigs chr21 --flavours ensembl`. Lookup is per
`(entity, chrom)`; shards for other chromosomes do not affect CSQ at this locus.

## Helper surface consumed (from PR #20)

| Surface | Use here |
|---------|----------|
| `common::cache::full_cache(Flavour::Ensembl)` | Locate + revision-check the pinned `ensembl` flavour under `$VEPYR_CACHE_ROOT` |
| `common::cache::{Entity, Flavour}` | `entities = &[Entity::Transcript]` — the only entity this oracle reads |
| `common::ledger` (via `annotate_vcf`) | Reads `required_contigs` from the embedded `[[assertion]]` fragment and enforces the shards |
| `common::annotate::annotate_vcf` | `annotate_to_vcf` over the checked cache; returns `(Result<usize, String>, output text, TempDir)` |
| `common::annotate_config!` | The one construction path for the `#[non_exhaustive]` `AnnotateVcfConfig`; called as `annotate_config! {}` (DECIDED 1) |
| `common::csq::{csq_layout, csq_groups, data_lines, field, group_for, distinct}` | Parse and address the emitted CSQ |

## Data flow

1. `cache::full_cache(Flavour::Ensembl)` — panics with a repair command if
   `$VEPYR_CACHE_ROOT` is unset or the revision disagrees with `PINS.toml`.
2. `annotate::annotate_vcf(&cache, &[Entity::Transcript], ASSERTION_TOML, INPUT_VCF, &config)`
   — enforces `required_contigs = ["chr21"]` (hard panic naming
   `./run_tests … --add-contigs chr21`; never a skip), then annotates.
3. `csq::csq_layout` / `csq::csq_groups` parse the emitted CSQ.
4. Assert, in falsifier-cost order: row identity → group count → Feature set →
   every field of every group.

## Assertions

Same observation point as sitekwb (CSQ via annotate → VCF), not Perl
`_buffer_to_output` / VEP-tab.

1. `written == Ok(1)` — one record annotated and written.
2. Row identity: CHROM/POS/ID/REF/ALT = `chr21`, `25585733`, `rs142513484`, `C`, `T`.
3. `groups.len() == 34` — cheapest falsifier first.
4. `distinct(layout, groups, "Feature")` equals the sorted 34 expected Feature IDs —
   so a swapped/missing/extra transcript reports as a set difference rather than as a
   `group_for` panic inside the per-field loop.
5. For each of the 34 groups, all 23 CSQ fields via `csq::field` / `csq::group_for` —
   **including** the ones empty in every oracle row (`HGVSc`, `HGVSp`,
   `Existing_variation`, `FLAGS`), so an engine change that starts populating them
   also fails loud (DECIDED 3).

### Where the literals come from

All 34 × 23 literals are transcribed verbatim from the `default` VEP 116 CSQ block
recorded on
[issue #16 comment](https://github.com/biodatageeks/vepyr-porting-tests/issues/16#issuecomment-5671600272)
(the `chr21`-spelled run is CSQ-identical to the `21`-spelled one). Nothing is copied
from Perl v84 or from sitekwb's v115 material — in particular sitekwb's 11-entry
`EXTRA_V115_TRANSCRIPTS` is replaced outright by the full v116 inventory, since
GENCODE 50 raises the group count from 14 (v115) to 34.

The table in the test source is written through a small positional macro so each row
reads like the `|`-delimited CSQ dump it was copied from and can be diffed against the
issue by eye.

## Error handling

Missing `$VEPYR_CACHE_ROOT`, wrong provenance/revision, or missing `chr21` shards →
panic with repair text (same contract as `tests/common/cache.rs`). No `#[ignore]`,
no skip-on-missing-cache.

## Verification

```text
./run_tests --cache-dir DIR --add-contigs chr21 --flavours ensembl   # exit 0
export VEPYR_CACHE_ROOT=DIR
cargo test --test data_runner_consequence_content -- --nocapture     # exit 0
cargo check --tests                                                  # exit 0
```

The issue's end-to-end acceptance commands
(`VEPYR_CACHE_ROOT=DIR ./run_tests --vepyr REF [--list]`) stay blocked on **#21**
until that lands; the direct `cargo test` above exercises the same test binary against
the same pinned corpus and is the falsifiable proof in the meantime.

## Acceptance (human before merge)

- [ ] `required_contigs` is exactly `["chr21"]`, and the input VCF is spelled `chr21`
- [ ] All 34 × 23 asserted values come from the v116 `default` run on the issue
- [ ] No micro-cache path; no smoke test; no ported parquet cDNA control
- [ ] Green under `$VEPYR_CACHE_ROOT` (`cargo test`), and under
      `./run_tests --vepyr REF` once #21 lands
- [ ] Rebased onto `master` after #20 merges
