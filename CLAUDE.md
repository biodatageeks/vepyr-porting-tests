# CLAUDE.md — biodatageeks/vepyr-porting-tests (project requirements, binding)

This file complements `AGENTS.md`. It applies to the repo `biodatageeks/vepyr-porting-tests`.

## Normalisation of data-test input (hard requirement, for ALL data-tests)

The input file (`input.vcf`) of every data-test, exactly the same file byte for byte that **both** VEP and vepyr receive, MUST be produced from the raw VCF by one known bcftools command with specific flags, following Marek's sketch (`vepyr/e2e-testing/scripts/run_annotation_fast.py`, function `normalize_vcf`, line ~288):

```
bcftools norm -m -both -o <out.vcf> <in.vcf.gz>
```

- Exactly the flags `-m -both`, **without** `-f/--fasta-ref` (splits multiallelic records, does no left-align). Do not change the flags or add `-f` without an explicit decision by the owner: `-f` changes VEP's results for unparsimonious indels.
- The command and its version (`bcftools --version`) are recorded in `test.toml` (input section) and are **identical for all tests**; there is one place in the repo that runs it (one script, `tools/normalize_input`), not by hand in each test.
- The same normalised file goes to VEP (oracle generation) and to vepyr (the test). Never two different copies.
- A data-test directory does not store the raw pre-normalisation file (#90); VEP and the test read only the normalised `input.vcf`.
- Every data-test AC and issue checks this with a command: re-running `tools/normalize_input` on the committed `input.vcf` reproduces it byte for byte (`./check_normalised_input`, exit 0).

## Data-tests compare VEP with vepyr (not vepyr with itself)

Every data-test compares the output of native VEP 116.2 (the file `expected_output.vcf` in the test directory) with vepyr's output: the body (lines not starting with `#`), md5 of the body. Tests that today compare vepyr with itself (e.g. #17, invariance with respect to buffer size) are reworked so that the oracle is the file from VEP; additional vepyr runs (e.g. another `buffer_size`) are compared with the same VEP file.

## Test directory model (approved by Marek)

`tests/data/<name>/` is a shared fixture containing `input.vcf`,
`expected_output.vcf`, and `test.toml`. Each `[[tests]]` entry has a unique id,
description and one tagged `vep_test` URL; tests with the same input, oracle and
runtime configuration share one fixture. The generic runner executes that
fixture once per run configuration and compares its complete output body.
All committed data fixtures use `flavour = "merged"` and a native VEP 116.2
oracle generated with `--merged`. The runner defaults to fetching only that cache.
An optional top-level `skip_reason` disables annotation for an unsupported
feature. It must be a non-empty string; skipped fixtures stay visible in the
index and runner reports and never count as passing. Remove the key to re-enable.

Source fields are URLs: `cache_source` for the pinned vepyr dataset, `vep_cache`
for the native VEP archive, and `fasta_source` for the reference download.
Checksums remain separate evidence; a URL does not make an unverified local
file verified. Per-test issue, ledger and duplicate upstream links are omitted.
`tools/check_unique_dirs` rejects repeated fixture comparisons.

## Issue acceptance criteria (reminder)

Every issue has numbered acceptance criteria with backticked commands (machine-checkable or semi-machine-checkable, with a negative control). We do not start implementation work until the owner has verified the issues by hand.
