# vepyr-porting-tests

This repository is the curated home for porting tests from
[Ensembl VEP](https://github.com/Ensembl/ensembl-vep) to
[vepyr](https://github.com/biodatageeks/vepyr).

Ensembl VEP annotates variants from genome sequencing data (Perl).
vepyr is a Rust port of Ensembl VEP. Here we land **data-problem** tests
one curated assertion at a time: each checks that vepyr produces predictable
results on real annotation data.

## ./run_tests

`./run_tests` is the single entry point for this repository's tooling
(`uv` + `tools/run_tests/`).

```bash
./run_tests --help
./run_tests --list
./run_tests --cache-dir /mnt/hf-cache --add-contigs chr21,chrMT
./run_tests --cache-dir /mnt/hf-cache --vepyr 0.7.0
# or, after a fetch:
export VEPYR_CACHE_ROOT=/mnt/hf-cache
./run_tests --vepyr 0.7.0
# omit --vepyr to run against biodatageeks/vepyr master HEAD as it stands now:
./run_tests
```

| Flag | Status in this commit |
|------|------------------------|
| `--help` | Exit 0 |
| `--list` | Lists the data-test directories `tests/data/<name>/` present in the working tree; exit 0 |
| `--cache-dir DIR` | Downloads the pinned VEP 116 shards into `DIR` and writes `PROVENANCE.json`; then runs data-tests when targets exist |
| `--add-contigs LIST` | Adds the named contigs to `DIR` (not `--contigs`). Default: whole genome |
| `--flavours LIST` | Default `ensembl,refseq,merged` |
| `--dry-run` | Lists Hub files and byte totals; writes nothing; does not run tests |
| `--verify` | Checks every selected shard against the Hub sha256 |
| `--fast` | Sets `HF_XET_HIGH_PERFORMANCE=1` for the download (see below) |
| `--no-trim-manifests` | Leaves `chrom_manifest.json` naming shards that were not fetched |
| `--vepyr REF` | Resolves `REF` on biodatageeks/vepyr and path-patches that revision's dfbf/formats ladder. **Optional:** omitted, data-tests run against `master`'s current HEAD; the summary prints the full 40-char resolved sha either way. Pass `REF` whenever a pinned, reproducible run is wanted (CI, bisecting, ledger evidence) |

**"Targets" means data-test directories** — `tests/data/<name>/` holding a
`test.toml` (see [Porting method](#porting-method)). `--list` prints their names.
They all run inside the one generic cargo target, so the `cargo test` invocation
carries a single `--test data_dirs`.

**`--vepyr REF` examples.** `REF` is anything `biodatageeks/vepyr` can dereference:

```bash
./run_tests --vepyr 0.7.0     # a tag — note there is NO `v` prefix
./run_tests --vepyr 1f0c3a9   # a commit sha, short…
./run_tests --vepyr 1f0c3a9e4b7d2c5a8f6013b9d4e27ca5f80b6d31   # …or full 40-char
./run_tests --vepyr master    # a branch (resolved to its HEAD at run time)
./run_tests                   # omitted: same as `--vepyr master`, resolved per run
```

Engine ladder checkouts run with `GIT_LFS_SKIP_SMUDGE=1`: the crates are built from
Rust source only, so the fetch never depends on unrelated git-lfs-hosted content.

Omitting the flag is **not** "no engine": it resolves `biodatageeks/vepyr`'s
`master` HEAD as it stands at that moment. The summary prints the resolved 40-char
sha in every case. How that resolution works end to end is documented in
[docs/dynamic-vepyr-version-resolving.md](docs/dynamic-vepyr-version-resolving.md).

Every real fetch (not `--dry-run`) also downloads the GRCh38 FASTA into
`DIR/fasta/`, checks it against the `[grch38_fasta]` pin, and writes the `.fai`
index.

`--fast` needs no Hugging Face login for these public pins: just pass the flag
(e.g. `./run_tests --cache-dir DIR --add-contigs chr21 --fast`). It only raises
Xet download concurrency/buffers via `hf_xet` (already pulled in with
`huggingface-hub`). Use it on a high-bandwidth host with plenty of RAM
(Hugging Face recommends about 64 GB); on a smaller machine leave it off.

Exit codes: `0` ok, `1` data-tests failed, `2` usage (missing cache),
`3` revision clash vs `PINS.toml`, `4` incomplete selection or missing cache
pieces, `5` verification failure, `6` engine resolve/checkout failure. Every run
ends with a summary of effective flags, the cache directory, targets, the
contigs accumulated per flavour, and the `vepyr sha` line naming the exact
40-char revision the run tested against.

Without a cache root (`--cache-dir` or `$VEPYR_CACHE_ROOT`), an invocation
(without `--help` / `--list`) exits 2. With targets present, the cargo run needs
no `--vepyr`: the ref defaults to `biodatageeks/vepyr`'s `master` HEAD, resolved
per run and reported as `vepyr sha` in the summary. That default is deliberately
floating — a run today and a run tomorrow can test different engine code — so
pin `--vepyr REF` for anything that must be reproducible.

## ./issue_check (pre-work issue gate)

`./issue_check --body-file PATH` reads an issue body and answers one question: does it
have a heading matching `/acceptance criteria/i`, and does that section contain at
least one code span or fenced code block? The section runs to the next heading of the
same or a higher level, so `### AC-N` sub-sections — their headings included — are part
of it.

```bash
./issue_check --body-file tools/fixtures/issue_check/valid.md               # exit 0
./issue_check --body-file tools/fixtures/issue_check/prose_only_criteria.md # exit 1
gh issue view 74 --json body --jq .body > body.md && ./issue_check --body-file body.md
```

Exit codes describe the checker: `0` compliant, `1` not compliant, `2` invoked wrong
(bad flag, unreadable or non-UTF-8 `--body-file`). It is a presence check, not a
meaning check: it does not judge whether the criteria are good, whether a backticked
span is a command, and it never runs them — a criterion may expect any exit code or be
semi-manual. `.github/workflows/issue-check.yml` runs it on `issues`
(opened/edited/labeled) and on `workflow_dispatch`; it blocks nothing, the result is
visible in Actions only.

## ./pr_status (read-only PR readiness gate)

`./pr_status N` answers whether PR `N` is ready for the owner (#158; the rules are in
`AGENTS.md`, "Issue and pull request lifecycle"). It only reads: `gh pr view N --json
headRefOid,labels,comments,reviews,files,closingIssuesReferences`, one `gh issue view`
per closing issue, and, only when `README.md` changed, `gh pr diff N` and one `gh api`
read of `README.md` at the head. It never changes a label, a comment or a review.

```bash
./pr_status 158                                                   # real mode
./pr_status --from-json tools/fixtures/pr_status/ready.json       # READY, exit 0
./pr_status --from-json tools/fixtures/pr_status/probes.json      # FAIL probes: ..., exit 1
```

It prints one `FAIL <check>: <reason>` line per failed check (all of them) or `READY`.
The checks: `sticky-missing`, `sticky-duplicate` (exactly one issue comment starting
with `### pr-status:v1`, with a valid fenced `json` block), `sticky-stale`,
`sha-mismatch` (sticky `head` and every AC row `sha` = the PR head), `ac-exit` (every
non-manual AC row has `exit` = `expected`), `verdict-missing`, `verdict-changes`,
`probes` (the latest `### pr-review:v1` verdict with `"role":"review"` for the head:
`APPROVE`, at least 2 own probes), `mutation` (every non-manual AC row has a mutation
whose exit differs from `expected`), `superreview-missing`, `superreview-model`,
`superreview-blocking` (the super-review tier, computed from the changed paths, the
README diff and the closing issues' severity labels; the list is the
`Super-review tier:` line of `AGENTS.md`), and `state-label` (exactly one `state:*`
label, `state:manual-reviewing` or `state:awaiting-merge`). Checks that need the sticky
data are skipped when it is missing or duplicated, the review-verdict checks when there
is no review verdict, the super-review checks when there is no super-review, so a
failure never cascades.

Exit codes: `0` ready, `1` not ready, `2` usage or tool error (no argument, non-numeric
`N`, unreadable or non-JSON input, `gh` missing or failing, `README.md` changed but no
README diff in the input, or malformed input: every field the gate reads is
type-checked, so a missing key, a wrong type, `null`, a string where a boolean
belongs or a boolean where an integer belongs is one `pr_status: malformed input:`
line, never a pass). A verdict other than exactly `APPROVE` fails its check.
`--help` exits 0. Fixture mode reads one JSON document: the
`gh pr view` output plus `issues` (closing issues with labels) and, when `README.md`
changed, `readme_diff` and `readme`. Fixtures: `tools/fixtures/pr_status/` (two valid,
one failing fixture per check id, `not-json.txt`); `tools/fixtures/gh_stub/gh` is an
offline `gh` that serves one fixture from `$GH_STUB_STATE` and logs every call to
`$GH_STUB_STATE.log`. Tests: `tools/test_pr_status.py`.

## ./set_state (state-label writer)

`./set_state` is the only tool that writes the `state:*` labels (#158). It reads the
current labels, refuses any move that is not one of the 18 legal transitions (the same
table is in `AGENTS.md`), and for `state:awaiting-merge` runs the `./pr_status` gate and
refuses unless it is READY. Then it makes exactly one `gh <kind> edit` call and reads the
labels back.

```bash
./set_state pr 158 auto-reviewing                 # STATE with or without the state: prefix
./set_state issue 158 manual-reviewing-issue
./set_state issue 158 --clear                     # owner approved the issue by hand
./set_state pr 158 fixing --dry-run               # prints DRY-RUN gh pr edit ..., edits nothing
./set_state --print-transitions                   # the 18 legal moves
```

A PR takes the six PR states (`implementing`, `auto-reviewing`, `fixing`,
`auto-superreviewing`, `manual-reviewing`, `awaiting-merge`), an issue the four `-issue`
states. `--repo OWNER/REPO` is passed to `gh`; without it `gh` resolves the repository
from the clone. Exit codes: `0` done (or dry-run ok), `1` refused (illegal transition,
more than one state label, a current label of the other kind, gate not READY), `2` usage
or tool error (no argument, unknown kind, non-numeric `N`, unknown state or one of the
other kind, `gh` failing, read-back mismatch). Tests: `tools/test_set_state.py`, against
the offline `gh` stub.

## ./bless

`./bless` makes and checks the oracle of a data-test directory `tests/data/<name>/`:
`expected_output.vcf`, the real output of native VEP 116 on the directory's
normalised `input.vcf` (made by `tools/normalize_input`, #85). It runs Ensembl's
official image `ensemblorg/ensembl-vep:release_116.0`, with the same fixed command plus
the flags listed in `[vep] extra_flags`:

```
vep --offline --cache --dir_cache <CACHE> --species homo_sapiens --cache_version 116 \
    --assembly GRCh38 --fasta <FASTA> --everything --vcf --input_file input.vcf \
    --output_file expected_output.vcf --force_overwrite
```

That is the one data-test mode, `--everything`; see
[One mode: --everything](#one-mode---everything). A bless and `--check --reproduce`
exit 1 with `unsupported mode` when `[vepyr]` does not match it.

Three modes:

| Command | Needs | Does |
|---------|-------|------|
| `./bless CACHE FASTA <dir>` | Docker, cache, FASTA | Runs VEP, writes `expected_output.vcf`, fills `[vep]` and `[compare] body_md5` in `test.toml` |
| `./bless --check <dir>` | only the repo | Recomputes the md5 of the body (lines not starting with `#`) of `expected_output.vcf` on disk and compares it with `[compare] body_md5`. No Docker, no cache, changes nothing |
| `./bless --check --reproduce CACHE FASTA <dir>` | Docker, cache, FASTA | First runs the drift check of `--check` (stops with exit 1 before any Docker call if the file drifted), then re-runs the image recorded in `[vep] image` with the fixed command plus `[vep] extra_flags` into a temp directory and compares that fresh body md5 with `[compare] body_md5`. Changes nothing |

`--check` is the cheap integrity check anyone can run when reviewing a PR: it
catches an oracle that was hand-edited or corrupted after it was blessed.
`--check --reproduce` is the expensive audit: it runs the same drift check first,
before any Docker call, and then proves the file is still derivable from the
pinned image, cache and input, not just unedited. It is opt-in.

**Extra VEP flags.** The extra flags of a test are data: the list `[vep] extra_flags`
in `test.toml` (absent means none). `--vep-flag=FLAG` (repeatable) fills it at the
first bless; each flag is appended after the fixed command in the order given, the
bless writes the list as `[vep] extra_flags` and generates `[vep] command` from it
(an audit record, never parsed back):

```bash
./bless --vep-cache-dir ~/vep-cache --vep-fasta ~/GRCh38.fa --vep-flag=--check_existing tests/data/NAME
```

- Use the `=` form: `--vep-flag --check_existing` is read by argparse as two options
  and exits 2.
- Only flags in `ALLOWED_VEP_FLAGS` (`tools/bless/vep.py`) are accepted, matched
  exactly on the whole token; today that is `--check_existing`. Aliases (`--fa`),
  abbreviations (`--input_f`), single-dash tokens, `--name=value`, unknown flags and
  a flag given twice exit 1 with `bless: --vep-flag: FLAG is not allowed; allowed: ...`.
  It is an allowlist because VEP's Getopt::Long accepts aliases and abbreviations,
  so no denylist can be complete.
- Policy: adding a flag is one line in `ALLOWED_VEP_FLAGS` plus a data-test that
  needs it. Boolean flags only; a flag with a value needs its own design.
- A re-bless without `--vep-flag=` uses the recorded `extra_flags`; a typed list
  equal to it is fine; a different one exits 1 (edit `extra_flags` in `test.toml`
  to change the flags). An empty list is not written, so a test without extra
  flags has no `extra_flags` key.
- `--check` (with or without `--reproduce`) refuses `--vep-flag` (exit 1): a check
  replays only what is recorded. `--check --reproduce` reads `[vep] extra_flags`
  (an array of strings, else exit 1), passes it through the same allowlist (a flag
  removed from `ALLOWED_VEP_FLAGS` stops replaying, exit 1) and hands it to
  `docker run` in order. It also requires `[vep] command` to be exactly the
  canonical command generated from the list (`vep.vep_command`): a command that
  differs, even only in quoting or whitespace, exits 1 naming the file and both
  strings.
- The Rust loader (`tests/data_dirs.rs`) accepts `extra_flags` as an optional
  array of strings and checks only its type; the allowlist is enforced by
  `./bless`.

**Cache and FASTA.** A bless and `--check --reproduce` need exactly one flag from each
pair. There is no default path and no environment variable; neither or both flags of
a pair exits 1 with a message naming the pair.

| Flag | Meaning |
|------|---------|
| `--vep-cache-dir PATH` | An existing VEP cache root (the directory that holds `homo_sapiens/116_GRCh38/`). It must be complete: `info.txt` and the directories `1`–`22`, `X`, `Y`, `MT`. An incomplete cache exits 1 naming what is missing; nothing is fetched or written into it |
| `--download-vep-cache-to-dir PATH` | Fetches `homo_sapiens_vep_116_GRCh38.tar.gz` (27.6 GB) from `https://ftp.ensembl.org/pub/release-116/variation/indexed_vep_cache/`, checks it against Ensembl's `CHECKSUMS` value pinned in `tools/bless/ensembl.py`, unpacks it into `PATH`, then uses it, all in the same run |
| `--vep-fasta PATH` | An existing uncompressed GRCh38 FASTA. A missing, unreadable or gzipped file exits 1. A `.fai` index is written next to it when absent |
| `--download-vep-fasta-to PATH` | Fetches `Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz` from `https://ftp.ensembl.org/pub/release-116/fasta/homo_sapiens/dna/`, checks it the same way, decompresses it to `PATH`, then uses it |

```bash
# an existing cache and FASTA (e.g. on vepyr-tests-01, or a previous download)
./bless --vep-cache-dir ~/vep-cache --vep-fasta ~/GRCh38.fa tests/data/NAME
# fetch both first, in the same run
./bless --download-vep-cache-to-dir ~/vep-cache --download-vep-fasta-to ~/GRCh38.fa tests/data/NAME
# mixed: existing cache, fetched FASTA
./bless --vep-cache-dir ~/vep-cache --download-vep-fasta-to ~/GRCh38.fa tests/data/NAME
```

Downloads run as parallel HTTP range requests (Ensembl's FTP is slow per
connection) and resume: re-running the same command after an interruption keeps the
chunks already fetched. A path filled by a `--download-...` flag is ordinary local
state afterwards; pass it to `--vep-cache-dir`/`--vep-fasta` next time and nothing is
downloaded. `bless` does not remember paths; the flags are the only state.

**`--dry-run`** prints the planned steps (`# fetch ...` for each download, the copy of
`input.vcf` into a temp directory) and the exact `docker run ... vep ...` command,
then exits 0 without running anything. For a bless it shows the tag
`ensemblorg/ensembl-vep:release_116.0`; the real run resolves it to a digest first.

**What a bless records** in `test.toml`:

```toml
[vep]
image = "ensemblorg/ensembl-vep@sha256:..."   # the digest that ran, never the tag
command = "vep --offline --cache --dir_cache /opt/vep/.vep ..."  # generated from extra_flags; paths inside the container
date = "2026-09-23"
cache_source = "https://ftp.ensembl.org/pub/release-116/variation/indexed_vep_cache/homo_sapiens_vep_116_GRCh38.tar.gz"
cache_checksum = "sha256:... sum:56036 26996736"
fasta_source = "https://ftp.ensembl.org/pub/release-116/fasta/homo_sapiens/dna/Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz"
fasta_checksum = "sha256:... sum:22450 861294"
extra_flags = ["--check_existing"]  # only when non-empty; the source of truth; key order is not significant
[compare]
body_md5 = "..."
```

The source and checksum come from a `.bless-source.toml` record that a download
leaves next to the cache/FASTA. A cache or FASTA that `bless` did not download
is recorded as `local:<path>` with checksum `unverified`.

**From scratch on a plain machine** (Docker, network, about 60 GB free; no
`vepyr-tests-01`, no existing cache):

```bash
git clone https://github.com/biodatageeks/vepyr-porting-tests && cd vepyr-porting-tests
tools/normalize_input raw.vcf.gz tests/data/my_test        # writes input.vcf + [input]
./bless --download-vep-cache-to-dir ~/vep116 --download-vep-fasta-to ~/GRCh38.fa tests/data/my_test
./bless --check tests/data/my_test                           # exit 0
./bless --check --reproduce --vep-cache-dir ~/vep116 --vep-fasta ~/GRCh38.fa tests/data/my_test
```

**Work directory.** Each VEP run copies `input.vcf` into its own `bless-*`
directory created under `--docker-work-dir PATH`, which is bind-mounted into the
container and removed afterwards. Without the flag it is `<repo-root>/.bless/`
(located from the script, not the current directory; listed in `.gitignore`),
mirroring `./run_tests`'s `<repo-root>/.run_tests/`. There is no environment
fallback (`TMPDIR` is not used). If the repo checkout itself is not in a
Docker-Desktop-shared location, pass `--docker-work-dir` at a path that is, just as
`--vep-cache-dir` and `--vep-fasta` must be.

**Docker Desktop (macOS).** The cache, the FASTA's directory and the work
directory are bind-mounted into the container, so they must be inside a directory
listed under Settings > Resources > File sharing. Before any download, `bless`
probes each path from inside a container and exits 1 naming the first one Docker
cannot see, so an unshared `--download-vep-cache-to-dir` fails in seconds, not
after a 27 GB fetch.

`bless` refuses a directory whose `test.toml` `[input]` table does not carry the
`tools/normalize_input` command. It does not read issue bodies and never logs into
another machine: to use `vepyr-tests-01`'s cache, log in there and run the same
command with its local paths.

Exit codes: `0` success (for `--check`, the hash matches), `1` any failure, always
with one `bless: ...` line on stderr naming the problem, `2` usage (unknown flag,
no `<test-dir>`).

## One mode: --everything

Every data-test runs in exactly one mode: VEP with `--everything` and a reference
FASTA on one side, vepyr with the matching `[vepyr]` values on the other. That is
the configuration the [vepyr CLI docs](https://biodatageeks.org/vepyr/cli/)
describe as validated against Ensembl VEP. A run without `--everything` is
unsupported and disabled: `./bless` refuses it, and the Rust loader panics with
`[<name>] unsupported mode` on a `[vepyr]` (or `[[vepyr_run]]`) value that differs
from the table below, or on a `[vep] command` that lacks one of its VEP flags (an
oracle made by the old command).

The mapping has one source of truth, `tools/vep_flags.toml`, read by `./bless`
(`tools/bless/vep.py`) and by `tests/data_dirs.rs`.
`uv run --frozen pytest tools/test_bless.py -k everything_mode` fails when that
file, `VEP_ARGV` and this table disagree.

| VEP flag | `[vepyr]` in `test.toml` | Why |
|---|---|---|
| `--everything` | `everything = true` | all annotation features, the full `--everything` CSQ layout |
| `--fasta` | `reference_fasta = true` | reference FASTA, required by `--everything`; vepyr reads `$VEPYR_CACHE_ROOT`'s GRCh38 FASTA |
| `--vcf` | `preserve_record_layout = true` | VCF output: VEP copies each input line and only appends CSQ to INFO |

`[vepyr]` has no `fields` key: vepyr emits its full `--everything` CSQ layout (80
fields in VEP 116, regulatory and motif fields included), as VEP does, and the
loader rejects `fields` as an unknown key. The other VEP flags of the fixed command
(`--offline`, `--cache`, `--dir_cache`, `--species`, `--cache_version`,
`--assembly`, input/output names) select the cache and files, not annotation, and
have no `[vepyr]` counterpart; `flavour` and `required_contigs` pick vepyr's cache.
`flavour` must be `"ensembl"` (the oracle is VEP on the Ensembl cache): the loader
accepts only "ensembl", in `[vepyr]` and in every `[[vepyr_run]]` override.
Before each run the runner checks that every cache entity vepyr reads in
`--everything` mode (all seven; `motif` and `regulatory` excepted on `chrMT`) has a
shard for each `required_contigs` entry.

Extra flags stay as described under [./bless](#bless): the allowlist
`ALLOWED_VEP_FLAGS` keeps `--check_existing` (#18), and `[vep] extra_flags` (#108)
remains the mechanism that records them. Neither is part of the vepyr CLI docs;
they are appended to the `--everything` command, never replace it.

## tests/common (cache + assertion helpers)

Fetch a cache, then point `$VEPYR_CACHE_ROOT` at the same directory (or pass
`--cache-dir` to `./run_tests` together with `--vepyr`):

```bash
./run_tests --cache-dir /mnt/hf-cache --add-contigs chr21,chrMT
export VEPYR_CACHE_ROOT=/mnt/hf-cache
./run_tests --vepyr 0.7.0
# helpers type-check (and floating engine deps resolve) with:
cargo check --tests
```

Shared modules under `tests/common/`: `cache` / `ledger` (issue #5), plus
`annotate`, `csq`, and `provenance` for data-problem pilots (issue #14). Data-tests
live as directories `tests/data/<name>/`, run by `tests/data_dirs.rs` and listed
by `./run_tests --list`.

`tests/INDEX.csv` (issue #92) lists every data-test, one row per directory, with
columns taken from its `test.toml`: `name`, `description`, `vep_test_pinned`,
`vep_subject`, `ledger`, `issue`, `required_contigs` (`;`-joined), `vepyr_runs`,
`body_md5`. It is generated by `tools/build_test_index` and never edited by hand:
after adding or changing a test, run `tools/build_test_index` and commit the index
with the change. `tools/build_test_index --check` changes nothing
and exits 0 if the committed file is current, 1 if it is stale, 2 if a `test.toml`
is missing or lacks a key a column needs; CI (`test-index.yml`) runs it on every PR
and push to `master`.

`tools/normalize_input <raw> <test-dir>` writes a data-test's `input.vcf` with
the one fixed `bcftools norm -m -both` command (issue #85) and requires exactly
**bcftools 1.23 on htslib 1.23.1** — the toolchain the oracles were generated
with — refusing any other version before it touches the test directory. VEP and vepyr
always read that same normalised `input.vcf`; a data-test directory does not
store the raw pre-normalisation file (rationale: issue #90).

`./check_normalised_input [DATA_DIR]` (issue #89, default `tests/data`) re-runs
`tools/normalize_input` on every committed `input.vcf` in a temporary directory
and compares `input.vcf` and `test.toml` byte for byte with the committed files.
It prints `OK <dir>` or `MISMATCH <dir>` (plus a unified diff) per test and exits
0 only if every test matches and at least one was found. `DATA_DIR` may also be
one data-test directory (it holds a `test.toml`), which checks only that test
(#166). It never writes under
`DATA_DIR`, and needs the same pinned bcftools 1.23 / htslib 1.23.1. It checks
that `input.vcf` is already normalised (a fixed point of `normalize_input`) and
that `[input]` matches what the script writes; it does not verify the raw source
file (raw inputs are not committed, #90; raw provenance: #111). CI runs it in the
`input-normalised-check` workflow (`.github/workflows/input-normalised-check.yml`).

`./check_test_dir [DIR]` (issue #164) checks the structure of data-test
directories. `DIR` is a data root (default `tests/data`; every immediate
subdirectory holding a `test.toml` is checked) or one data-test directory. Five
checks run on every directory, all of them every time: `files` (exactly
`input.vcf`, `expected_output.vcf` and `test.toml`, each non-empty, nothing
else), `input-records` (`input.vcf` has at least one record), `order` (POS
ascends within each contig and each contig is one contiguous block),
`oracle-meta` (exactly one `##VEP=` line in the oracle and `[vep] image` pinned as
`ensemblorg/ensembl-vep@sha256:<64 hex>`) and `one-to-one` (one oracle body line
per input record; there is no option or `test.toml` key to skip it). It prints
`OK <dir>`, or one `FAIL <dir> <check>: <detail>` line per failing check, and
exits 0 only if every directory is OK and at least one was found; 1 on any
failure, no test found, or `DIR` not a directory; 2 on bad usage. It never
writes, needs no VEP, cache or bcftools, and reads records with the shared
`tools/vcf_records.py`. It does not check the `test.toml` schema (the loader),
the body md5 (`./bless --check`) or REF against the FASTA. It is not part of
`./run_tests` and no CI workflow runs it (workflows are disabled).

`tools/fixture_match --input PATH --fixture SRC --records N [--rust-const NAME]
[--by-pos] [--negative-control]` (issue #171) checks that the first `N` records
of an input equal an upstream fixture's (CHROM, POS, ID, REF, ALT). `PATH` is a
VCF (plain or gzip, detected by magic bytes) or a data-test directory (its
`input.vcf`); `SRC` is a local path or an `http(s)://` URL (there is no
`git:<repo>:<rev>:<path>` form). `--rust-const NAME` reads the fixture as Rust
source and takes `const NAME: &str = "...";` (line continuations and the
escapes `\n`, `\t`, `\\`, `\"`; raw strings are rejected). Records are compared
in order; with `--by-pos` each input record is compared with the fixture record
at the same CHROM:POS, so the fixture may hold other rows. It prints one
`PASS|FAIL fixture-match first N: <detail>` line; `--negative-control` also
appends `A` to the first input record's ALT in memory, expects a mismatch
(`PASS|FAIL fixture-match-negative: ...`) and prints a `summary` line. Exit 0
match, 1 mismatch (including fewer than `N` records on either side), 2 usage or
unreadable/invalid input, 3 network error or anything unexpected. Records are
read with the shared `tools/vcf_records.py`. `dt fixture-match` of the
data-test skill runs it with `--negative-control`.

### Caveats

**Windows.** `./run_tests` is a bash script (it bootstraps `uv` and then runs
`tools/run_tests/`), so it needs a POSIX shell: use Git Bash or WSL. `cmd.exe` and
PowerShell cannot execute it directly.

**Accumulation.** `--add-contigs` only adds shards; it never removes earlier
ones. `chr21,chr22` then `chr15,chrY` leaves all four on disk. For a wholly
different set, use a fresh `--cache-dir` or clean the directory yourself.

**Illegal / incomplete contig sets.** Every cache entity must get at least one
requested contig. `motif` and `regulatory` have no `chrMT`, so
`--add-contigs chrMT` alone is refused (exit 4). Legal minimal examples:
`chrY`, or `chr21,chrMT` (`chr21` covers the entities that lack `chrMT`).

## Porting method

A data-test is a **directory**, `tests/data/<name>/`, compared against the real
output of native Ensembl VEP 116. One generic cargo test, `tests/data_dirs.rs`,
walks the directories; there is no hand-typed expected table in Rust code.

```
tests/data/<name>/
  input.vcf             # normalised input: tools/normalize_input (#85)
  expected_output.vcf   # real VEP 116 output on input.vcf: ./bless (#32)
  test.toml             # provenance, how vepyr runs, the body md5
```

**Making one.** Pick a candidate, write its raw VCF, then:

```bash
tools/normalize_input raw.vcf.gz tests/data/<name>   # input.vcf + [input]
./bless --vep-cache-dir ~/vep116 --vep-fasta ~/GRCh38.fa tests/data/<name>   # oracle + [vep] + [compare]
# then fill name, description, [origin] and [vepyr] by hand
VEPYR_CACHE_ROOT=/mnt/hf-cache cargo test --test data_dirs
```

VEP and vepyr read the same `input.vcf`, byte for byte. Candidates come from the
assertion ledger in [sitekwb/vepyr-porting-tests](https://github.com/sitekwb/vepyr-porting-tests)
(`ledger/*.ledger.toml`); a directory names its source row in `[origin] ledger`.

**`test.toml`**, one line per key (`?` = optional). Every table is checked against
this list, and any other key fails the test with `[<name>] unknown key: <key>`:

```toml
name = "runner_consequence_content"   # equals the directory name
description = "..."                    # one sentence
[origin]
vep_test        = "https://github.com/Ensembl/ensembl-vep/blob/release/116.0/t/Runner.t#L244-L292"
vep_test_pinned = ".../blob/57ea5c52340acc1f156267f810ad162e26597082/t/Runner.t#L244-L292"
vep_subject     = ".../blob/57ea5c52.../modules/Bio/EnsEMBL/VEP/Runner.pm#L396"
ledger          = "Runner.ledger.toml n=16"   # ? source row in the sitekwb ledger
issue           = 16                           # ? issue that introduced the test
[input]                                        # written by tools/normalize_input
command          = "bcftools norm -m -both -o <out.vcf> <in.vcf.gz>"
bcftools_version = "bcftools 1.23"
[vep]                                          # written by ./bless
image = "..."  command = "..."  date = "..."
cache_source = "..."  cache_checksum = "..."  fasta_source = "..."  fasta_checksum = "..."
[vepyr]
flavour                = "ensembl"             # picks the cache directory, never a config flag
required_contigs       = ["chr21"]
everything             = true              # the one mode: see "One mode: --everything"
preserve_record_layout = true
reference_fasta        = true              # $VEPYR_CACHE_ROOT's GRCh38 FASTA
buffer_size            = 5000                  # ?
[compare]
body_md5 = "..."                               # written by ./bless
[[vepyr_run]]                                  # ? repeatable; overrides [vepyr] keys
buffer_size = 1
```

(The `[vep]` line above is abbreviated; in a real file each key is on its own line.)

**What the runner checks**, per directory:

1. *Self-check:* `[compare] body_md5` equals the md5 of the body of
   `expected_output.vcf` (body = every line not starting with `#`), otherwise
   `[<name>] oracle edited`.
2. vepyr annotates `input.vcf` once per run (one run from `[vepyr]`, or one per
   `[[vepyr_run]]` entry, each printed as `run <n>/<N>: <overrides>`).
3. The md5 of vepyr's body must equal `body_md5`. Otherwise
   `[<name>] body md5 mismatch`, `expected <md5>, got <md5>`, and the first
   differing record on a `VEP:` and a `vepyr:` line. CSQ group order is part of the
   body, so it is asserted too.
4. A mismatch always fails the test; an engine bug that causes it is tracked by
   an issue in `biodatageeks/vepyr`, and the test stays red until it is fixed.

`everything`, `preserve_record_layout` and `reference_fasta` must hold the values
of [One mode: --everything](#one-mode---everything); there is no `fields` key, so
vepyr emits its full `--everything` CSQ layout, the 80 fields VEP writes.

`DATA_DIRS_ROOT` overrides the walked directory. `cargo test --test data_dirs
selftest` runs the same loader and compare on the synthetic fixture
`tests/fixtures/data_dirs_selftest/` against a synthetic one-shard cache with a
synthetic one-contig reference FASTA, with no downloaded data.

Corpus dataset pins (`PINS.toml`) are documented in
[docs/dataset-pins.md](docs/dataset-pins.md).

## Agent setup (per machine)

The agent rules and skills are versioned here and nowhere else: `AGENTS.md`
(workflow rules), `CLAUDE.md` (project requirements) and the project skills in
`.claude/skills/` (`impl-vepyr-data-test` with its `scripts/dt`, and
`resolve-pr`). Claude Code loads `.claude/skills/` of the directory a session
starts in and of its parents up to the repository root; skills of a checkout
*below* the start directory load only once the session reads a file there
([docs](https://code.claude.com/docs/en/skills#discovery-from-parent-and-nested-directories)).
If you start sessions in a workspace directory that contains the checkout,
link the files once per machine (`WS` = that directory, `CO` = the checkout):

```bash
WS=/path/to/workspace; CO=$WS/biodatageeks-vepyr-porting-tests
# move any old real copies out of the way first (e.g. mv "$WS/AGENTS.md" "$WS/AGENTS.md.old")
ln -s "$CO/AGENTS.md" "$WS/AGENTS.md"
ln -s "$CO/CLAUDE.md" "$WS/CLAUDE.md"
mkdir -p ~/.claude/skills
ln -s "$CO/.claude/skills/resolve-pr"           ~/.claude/skills/resolve-pr
ln -s "$CO/.claude/skills/impl-vepyr-data-test" ~/.claude/skills/impl-vepyr-data-test
# verify: each link exists, is not dangling, and points into $CO
co=$(cd "$CO" && pwd -P) \
  && test -L "$WS/AGENTS.md" && test -e "$WS/AGENTS.md" && cmp -s "$WS/AGENTS.md" "$CO/AGENTS.md" \
  && test -L "$WS/CLAUDE.md" && test -e "$WS/CLAUDE.md" && cmp -s "$WS/CLAUDE.md" "$CO/CLAUDE.md" \
  && test -L ~/.claude/skills/resolve-pr && test -d ~/.claude/skills/resolve-pr/ \
  && test "$(readlink -f ~/.claude/skills/resolve-pr)" = "$co/.claude/skills/resolve-pr" \
  && test -L ~/.claude/skills/impl-vepyr-data-test && test -d ~/.claude/skills/impl-vepyr-data-test/ \
  && test "$(readlink -f ~/.claude/skills/impl-vepyr-data-test)" = "$co/.claude/skills/impl-vepyr-data-test" \
  && echo linked
```

The user-level links make both skills available in every session, wherever it
starts. Nothing about the links is tracked; after a `git pull` in `$CO` every
linked copy is current.

**`dt` machine config.** `.claude/skills/impl-vepyr-data-test/scripts/dt` reads
its machine-specific paths from one file, looked up in this order:

1. `$DT_CONFIG`: when this variable is set, the file it names must exist; if it
   does not, `dt` stops with exit code 2 and does not fall through to the next
   locations
2. `${XDG_CONFIG_HOME:-~/.config}/dt/local.toml` (recommended: one file for
   every clone and worktree)
3. `.claude/skills/impl-vepyr-data-test/local.toml` (next to the skill; git-ignored)

Without `$DT_CONFIG`, the first of 2 and 3 that exists is used.

Any single key can be overridden with `DT_<KEY>`. Create the config from the
tracked example and replace every `/path/to/...` placeholder:

```bash
mkdir -p ~/.config/dt
cp .claude/skills/impl-vepyr-data-test/local.toml.example ~/.config/dt/local.toml
"$EDITOR" ~/.config/dt/local.toml
.claude/skills/impl-vepyr-data-test/scripts/dt env   # run from a clone, not the main checkout
```

`local.toml` is never committed (`.gitignore`: `.claude/skills/*/local.toml`).

**`resolve-pr` owner options** are opt-in and never committed: set
`RESOLVE_PR_OPEN_CMD` (a command that opens a URL) and/or
`RESOLVE_PR_OWNER_QUEUE=1` in your shell profile or in the `env` block of the
git-ignored `.claude/settings.local.json`.
