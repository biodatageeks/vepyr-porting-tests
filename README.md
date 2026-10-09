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
./run_tests --cache-dir /mnt/hf-cache --add-contigs chr1,chr21,chr22
./run_tests --cache-dir /mnt/hf-cache --vepyr 0.7.0
# or, after a fetch:
export VEPYR_CACHE_ROOT=/mnt/hf-cache
./run_tests --vepyr 0.7.0
# omit --vepyr to run against biodatageeks/vepyr master HEAD as it stands now:
./run_tests
# run only chosen data-test directories (repeatable; may be scratch copies):
./run_tests --only tests/data/intergenic_variant_single_record --vepyr master
# annotate through the user-facing `python -m vepyr annotate` CLI instead of cargo:
./run_tests --cache-dir /mnt/hf-cache --via-cli --only tests/data/intergenic_variant_single_record
```

| Flag | Status in this commit |
|------|------------------------|
| `--help` | Exit 0 |
| `--list` | Lists the data-test directories `tests/data/<name>/` present in the working tree; exit 0 |
| `--cache-dir DIR` | Downloads the pinned VEP cache 116 shards into `DIR` and writes `PROVENANCE.json`; then runs data-tests when targets exist |
| `--add-contigs LIST` | Adds the named contigs to `DIR` (not `--contigs`). Default: whole genome |
| `--flavours LIST` | Default `merged`, the cache used by every committed data fixture. Other flavours remain available explicitly for tooling or external fixtures |
| `--dry-run` | Lists Hub files and byte totals; writes nothing; does not run tests |
| `--verify` | Checks every selected shard against the Hub sha256 |
| `--fast` | Sets `HF_XET_HIGH_PERFORMANCE=1` for the download (see below) |
| `--no-trim-manifests` | Leaves `chrom_manifest.json` naming shards that were not fetched |
| `--vepyr REF` | Resolves `REF` on biodatageeks/vepyr and path-patches that revision's dfbf/formats ladder. **Optional:** omitted, data-tests run against `master`'s current HEAD; the summary prints the full 40-char resolved sha either way. Pass `REF` whenever a pinned, reproducible run is wanted (CI, bisecting, ledger evidence) |
| `--only DIR` | Repeatable. Runs only the named data-test directories (each holds `test.toml`; may be outside `tests/data`, e.g. a scratch copy): they are copied into a fresh temporary root that `DATA_DIRS_ROOT` names, cargo runs only the exact `data_dirs` test, and the root is deleted afterwards; `tests/data` is never touched. The summary's `targets` line lists exactly these names. A missing directory, one without `test.toml`, or two with the same basename is a usage error (exit 2, `not a data-test directory`). The engine is still `--vepyr REF` (or its default) |
| `--via-cli` | Annotates each selected data-test (all of `tests/data`, or the `--only` directories, which may be outside `tests/data`) through the user-facing `python -m vepyr annotate` CLI instead of `cargo test --test data_dirs`; it complements the Rust loop, it does not replace it. See "`--via-cli` mode" below |
| `--old-vepyr-cache` | Consent to a vepyr cache that is not provably the newest (see "Cache freshness guard" below): the run proceeds and the summary records `old cache: YES (consented)` |

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
pieces, `5` verification failure, `6` engine resolve/checkout failure (with
`--via-cli` also a failed vepyr build or run), `7` stale cache (see below;
`--old-vepyr-cache` consents), `8` `--via-cli` body MISMATCH. Every run
ends with a summary of effective flags, the cache directory, targets, the
contigs accumulated per flavour, and the `vepyr sha` line naming the exact
40-char revision the run tested against.

**`--via-cli` mode** (#231). The vepyr CLI is built from `biodatageeks/vepyr` at
the sha that `--vepyr REF` (default: `master` HEAD) resolves to, once per sha, into
`<cache root>/.vepyr_cli/<sha>/` (a shallow git fetch of that sha, `uv build --wheel`,
`uv venv` + `uv pip install`); `BUILD.json` there and the summary detail record the
vepyr sha and the wheel's sha256. For each directory, `tools/run_tests/cli_argv.py`
derives the argv from `test.toml` `[vepyr]` and each `[[vepyr_run]]` override:
`flavour` -> `--dir_cache <root>/116_GRCh38_<flavour>`, `reference_fasta = true` ->
`--fasta <root>/fasta/<pinned .fa>`, `everything = true` -> `--everything`, plus
`--cache_version 116 --no_progress`; `preserve_record_layout = true` and
`buffer_size = 5000` are the CLI's defaults (no flag); `required_contigs` emits no
flag. A value the CLI cannot express (`buffer_size != 5000`,
`preserve_record_layout = false`, `everything = false`, `reference_fasta = false`)
or an unknown key is an `UnmappableKey`: that directory is not run, each such run is
named on stderr, and the exit code is `2`. At vepyr `af305aff` this applies to 4 of
the 5 runs of `runner_buffer_size_invariance` (`buffer_size = 1, 2, 3, 5`), which
therefore cannot run via the CLI; the default cargo path still runs them.

Each output is compared by the same rule as `tests/data_dirs.rs`: md5 of the body
(every line not starting with `#`, line terminators kept) against
`[compare] body_md5`. Body-mismatch contract: a directory whose every run matched
prints `PASS <dir-name>` on stdout; a mismatching one prints exactly
`MISMATCH <dir-name> expected=<md5> actual=<md5>` on stdout (one line per
directory, the first mismatching run), and the run exits `8` (`Exit.MISMATCH`).
Precedence: any other error wins and sets the exit code (`2` unmappable or unusable
`test.toml`, `6` vepyr build failure or a vepyr run that exits non-zero or writes no
output, `3`/`4` cache errors, `7` stale cache), while the `MISMATCH` lines of the compared directories
are still printed. Exit `8` therefore means every selected run was compared and at
least one mismatched.

**Cache freshness guard.** Whenever a cache root is given (`--cache-dir` or
`$VEPYR_CACHE_ROOT`), before any download or data-test, `./run_tests` asks the
Hugging Face Hub, once per flavour selected by `--flavours` (one `dataset_info`
call, 10 s timeout), which commit the pin's `ref` (`main`) points to now, and
compares it with the `sha` in `PINS.toml`. The run exits `7` when:

- the Hub HEAD differs from the pinned `sha` (a newer dataset exists);
- the Hub cannot be reached or the HEAD cannot be resolved (`freshness unknown`);
- the root is used as-is (no `--cache-dir`, no `--dry-run`) and `PROVENANCE.json`
  has no record for a selected flavour (freshness cannot be established).

The message names the pinned and HEAD shas. Bump `PINS.toml` deliberately in a
separate PR, or pass `--old-vepyr-cache` to proceed with the old cache. The guard
never changes what is fetched and never edits `PINS.toml`. It also applies under
`--dry-run` and `--only`; `--list` and `--help` do not query the Hub. The summary
then carries `old cache: no` when the guard passed, or `old cache: YES (consented)`
when the flag let an old cache through, or
`old cache: YES (refused; pass --old-vepyr-cache to consent)` on an exit-7 refusal,
followed by one line per flavour with its
pinned and HEAD shas. Offline and CI runs must pass `--old-vepyr-cache`
explicitly.

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
./pr_status --handover 158                                        # hand-over stage
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
label, `state:manual-reviewing` or `state:awaiting-merge`; with `--handover`, the
hand-over stage run before the move, `state:auto-reviewing` or
`state:auto-superreviewing`, every other check unchanged). Checks that need the sticky
data are skipped when it is missing or duplicated, the review-verdict checks when there
is no review verdict, the super-review checks when there is no super-review, so a
failure never cascades.
Known limits of the tier: a file renamed out of it, and a list capped at 100 files.
`gh pr view --json files` shows only the new path of a renamed file (moving
`tests/data/x` elsewhere skips the super-review) and is limited to 100 files; the
reviewer checks `git diff --name-status -M origin/master...HEAD` and requests the
super-review by hand.

Exit codes: `0` ready, `1` not ready, `2` usage or tool error (no argument, non-numeric
`N`, unreadable or non-JSON input, `gh` missing or failing, `README.md` changed but no
README diff in the input, JSON nested too deeply to parse, or malformed input: every field the gate reads is
type-checked, so a missing key, a wrong type, `null`, a string where a boolean
belongs or a boolean where an integer belongs is one `pr_status: malformed input:`
line, never a pass). A sticky or verdict `json` block nested too deeply to parse
(`RecursionError`) counts as invalid, like a broken one: `FAIL sticky-missing` or a
tool error, never a traceback. A verdict other than exactly `APPROVE` fails its check.
`--help` exits 0. Fixture mode reads one JSON document: the
`gh pr view` output plus `issues` (closing issues with labels) and, when `README.md`
changed, `readme_diff` and `readme`. Fixtures: `tools/fixtures/pr_status/` (two valid,
one failing fixture per check id, `not-json.txt`); `tools/fixtures/gh_stub/gh` is an
offline `gh` that serves one fixture from `$GH_STUB_STATE` and logs every call to
`$GH_STUB_STATE.log`. Tests: `tools/test_pr_status.py`.

## ./issue_status (read-only issue hand-over gate)

`./issue_status N` answers whether issue `N` may be handed over to the owner
(`state:manual-reviewing-issue`; #178, the rules are in `AGENTS.md`, "Issue and pull
request lifecycle"). It only reads: one `gh issue view N --json
number,body,labels,comments`. It never changes a label, a comment or the body.

```bash
./issue_status 178                                                       # real mode
./issue_status --from-json tools/fixtures/issue_status/ready.json        # READY, exit 0
./issue_status --from-json tools/fixtures/issue_status/status-stale.json # FAIL status-stale: ..., exit 1
```

It prints one `FAIL <check>: <reason>` line per failed check (all of them) or `READY`.
A record is pinned to the body by `body_sha256`, the sha256 of the UTF-8 bytes of the
`body` string `gh issue view N --json body` returns (nothing appended). The checks:
`status-missing`, `status-duplicate` (exactly one issue comment whose first non-empty
line is `### issue-status:v1`, with a valid fenced `json` block), `status-stale`
(`"stale": true`, or its `body_sha256` is not the current body's), `issue-check` (its
`issue_check` is not 0), `ac-passes-on-master` (no AC rows, or a row with
`master_exit` = `expected` that is not a `regression_guard`), `verdict-missing` (no
`### issue-review:v1` verdict whose `body_sha256` is the current body's: a verdict for
an older body never counts), `verdict-findings` (the latest such verdict is not exactly
`CLEAN`), `dry-run-mismatch` (a status AC id missing from the verdict's `ac_dry_run`, a
dry-run exit that differs from the row's `master_exit`, or the verdict's `master`
differing from the status `master`), and `state-label` (exactly one `state:*` label,
`state:auto-reviewing-issue` or `state:manual-reviewing-issue`, so the gate passes
before the hand-over move). Checks that need the status data are skipped when it is
missing or duplicated, the verdict checks when there is no current verdict.

The verdicts are ordered by `createdAt`; with equal `createdAt` the comment later in the `comments` list is the later verdict (a stable sort). A status comment without a valid `json` block is `FAIL status-missing` (exit 1), but a `### issue-review:v1` comment without a valid block, or with a block that does not follow the schema, is a tool error (exit 2) naming the comment by `createdAt` and URL, even when a compliant verdict sits next to it. A review written before #178 under that heading (`"role":"review"`, verdict `APPROVE`/`CHANGES_REQUESTED`) is such a comment: change its first line to `### issue-review-legacy:v0`, which the gate ignores, or delete it (the `gh api` command is in `AGENTS.md`, "Issue records").

Exit codes: `0` ready, `1` not ready, `2` usage or tool error (no argument, non-numeric
`N`, unreadable or non-JSON input, `gh` missing or failing, or malformed input: every
field the gate reads is type-checked, including `body_sha256` as 64 and `master` as 40
lowercase hex characters, and a verdict comment without a valid `json` block is one
`issue_status: malformed input:` line, never a pass). Fixtures:
`tools/fixtures/issue_status/` (`ready.json`, one failing fixture per check id,
`not-json.txt`); real-mode tests serve the issue through `tools/fixtures/gh_stub/gh`.
Tests: `tools/test_issue_status.py`. The parsing helpers and exit codes are shared with
`./pr_status` (`tools/pr_status/gate.py`), which is unchanged.

## ./set_state (state-label writer)

`./set_state` is the only tool that writes the `state:*` labels (#158). It reads the
current labels, refuses any move that is not one of the 20 legal transitions (the same
table is in `AGENTS.md`), and runs the `./pr_status` gate and refuses unless it is READY
before two PR moves: `state:manual-reviewing` (the hand-over stage,
`./pr_status --handover N`) and `state:awaiting-merge` (the owner's stage,
`./pr_status N`); and it runs the `./issue_status N` gate and refuses unless it is READY
before the issue hand-over `state:manual-reviewing-issue` (#178). Then it makes one `gh <kind> edit` call per moved item and reads the
labels back.

A PR move is mirrored to each closing issue (#177): the PR's
`closingIssuesReferences` (closing keywords and manual links only; none for a PR whose
base is not the default branch). A closing issue carrying the PR's old state gets the
same move; one already at the new state is left as is; a closed issue, or one with no
`state:*` or an `-issue` label, is skipped with a `note:` line on stderr; any other
label is out of sync and refuses the whole move. Everything is checked before the first
edit; the issues are edited first, the PR last, and a failed edit undoes the ones
already made (`nothing changed`, exit 2). `--no-mirror` moves the PR only. An issue
enters the PR family only by `./set_state issue N implementing`.

```bash
./set_state pr 158 auto-reviewing                 # STATE with or without the state: prefix
./set_state issue 158 manual-reviewing-issue
./set_state issue 158 implementing                # "implement issue #158": follows its PR from now on
./set_state issue 158 --clear                     # owner approved the issue, not implemented now
./set_state pr 158 fixing --dry-run               # prints every DRY-RUN gh ... edit, edits nothing
./set_state pr 158 fixing --no-mirror             # the PR only, closing issues untouched
./set_state --print-transitions                   # the 20 legal moves
```

A PR takes the six PR states (`implementing`, `auto-reviewing`, `fixing`,
`auto-superreviewing`, `manual-reviewing`, `awaiting-merge`), an issue the four `-issue`
states and, through `implementing` and the mirror, the PR states. `--repo OWNER/REPO` is passed to `gh`; without it `gh` resolves the repository
from the clone. Exit codes: `0` done (or dry-run ok), `1` refused (illegal transition,
more than one state label, a current label of the other kind, gate not READY), `2` usage
or tool error (no argument, unknown kind, non-numeric `N`, unknown state or one of the
other kind, `gh` failing or printing JSON nested too deeply, read-back mismatch, a
failed edit undone: `nothing changed`). Tests: `tools/test_set_state.py`, against
the offline `gh` stub.

## tools/check_ledger (assertion ledger coverage)

`tools/check_ledger` checks that the assertion ledger CSV (`ledger/assertions.csv`,
schema of #109; committed by #109: 1965 assertions of 49 files, one row each) has
exactly one row per assertion of the 49 upstream `t/*.t` files of Ensembl VEP at
the upstream tag pinned in `tools/check_ledger` (`DEFAULT_REF`, #110), and nothing
else. The ledger is its own axis: it stays at that tag (VEP 116.0) while the
data-test oracles use the VEP 116.2 pin of `tools/vep_pin.toml` (#239).

```bash
tools/check_ledger --csv ledger/assertions.csv               # schema + coverage, clones upstream
tools/check_ledger --csv ledger/assertions.csv --upstream UP  # offline, an existing checkout
tools/check_ledger --upstream UP --list                       # vep_file<TAB>n<TAB>perl_line<TAB>perl_kind
tools/check_ledger --upstream UP --sweep                      # names the enumerator did not count
tools/check_ledger --upstream UP --sweep --glob 't/*.pm'      # the 8 support modules
```

Without `--upstream` it makes a partial sparse clone of the tag into a temp dir
(`t/*.t`, `t/*.pm` and `modules/Bio/EnsEMBL/VEP/Config.pm`, about 1 MB); with
`--upstream DIR` it uses that checkout. Either way `git rev-parse HEAD` must be
`PINNED_COMMIT` of `tools/check_ledger` and `git status --porcelain` empty;
`--ref` only picks the tag to clone, the pin does not move. The files the glob
selects on disk must also equal those in `git ls-tree -r HEAD` at the pin: a clean,
pinned but sparse checkout that omits or adds a file exits 2 (`files matching ...
differ from the pinned tree`), so a missing file cannot pass as covered. `DIR` must
be the top level of the work tree (`git rev-parse --show-prefix` empty): a
subdirectory such as `ensembl-vep/t` exits 2 (`not the top level of the work tree`),
and so does a glob that selects no file of the pinned tree (`the pinned tree has no
file matching ...`), so an empty enumeration cannot pass as covered.

The default mode checks the schema (the 13 columns of #109, optionally followed by
`data_test_verdict`; field counts, enums, integer `n`/`perl_line`, conditional
columns, `issue` empty or `https://`, no newline in a field, rows sorted by
`vep_file` bytes then `n`, unique `(vep_file, n)`, bytes equal to their canonical
RFC 4180 form with LF and no BOM), then compares the CSV keys with the upstream
enumeration both ways: `missing row`, `orphan row`, `perl_line` and `perl_kind`
mismatches. The last stdout line is `rows R, files F, missing M, orphan O`.
`--sweep` prints ``file:line: uncounted `name`: text`` for every documented Test::*
function name the enumerator did not count and no fixed rule explains, and a tally
of the explained ones on stderr; `--sweep-dir DIR` does the same on a plain
directory without the pin check (for fixtures; its summary says `unpinned`).

Exit codes: `0` ok, `1` violations (one `file:n: reason` line each), `2` cannot
measure (CSV missing, unreadable or not UTF-8, a CSV field over 131072 bytes (the
Python `csv` field size limit; fails closed), upstream unavailable, another commit,
dirty tree, file set differing from the pinned tree, no `git`). `.github/workflows/ledger-check.yml` runs the unit tests, the
two sweeps and, once `ledger/assertions.csv` exists, the CSV check (Actions is
disabled, see `AGENTS.md`). Tests: `tools/test_check_ledger.py` (offline, against a
synthetic upstream repository).

## tools/check_vep_version (one VEP software pin)

Every data-test oracle is produced by **VEP software 116.2** against **VEP cache
116** (VEP point releases reuse the release-116 cache; there is no 116.2 cache).
The pin is defined once, in `tools/vep_pin.toml` (`[vep]` `image_tag`,
`image_digest`, `upstream_tag`, `upstream_commit`, `cache_version`); `./bless`,
`tools/check_campaign.py` and the `tests/data_dirs.rs` self-test read it, and no
other code spells the digest or the commit (#239).

```bash
tools/check_vep_version          # exit 0 consistent, 1 a violation, 2 pin/allow-list/git unusable
```

It requires every `tests/data/*/test.toml` to record the pinned `[vep] image`
digest and every `[[tests]] vep_test` at the pinned release tag, with no VEP 116.0 literal in the file, and
`git grep`s the rest of the repo for VEP 116.0 literals (the ledger axis,
`tests/INDEX.csv`, `docs/porting/**` and the checker's own two files are skipped).
All committed data fixtures use the pinned VEP image and merged cache;
the legacy image allowlist has been removed. The `test-index` workflow runs the check and its unit tests
(`tools/test_check_vep_version.py`).

## ./check_env (local prerequisites)

`./check_env` (issue #170) answers "can the repo tools run on this machine" with
one `PASS`/`FAIL`/`SKIP <name>: <detail>` line per check and a summary line:

```bash
./check_env [--vepyr-cache-root DIR] [--vep-cache-dir DIR] [--vep-fasta FILE] [--docker-timeout SECONDS]
```

Checks, each reusing the code of the tool that depends on it: `UV_PROJECT_ENVIRONMENT`
(set, absolute, outside every git checkout), `tool uv` / `tool cargo` / `tool git` (on
`PATH`), `bcftools pin` (`tools/normalize_input`'s own version check; the pin has no
second copy), `docker daemon` (`./bless`'s probe with a wall-clock limit, default 30 s),
and with their flags `vepyr cache` (`./run_tests`'s precheck: `PROVENANCE.json`, pinned
revisions, pinned FASTA name, plus a `116_GRCh38_<flavour>` dataset directory for each
flavour recorded in `PROVENANCE.json`), `vep cache` and `vep fasta` (`./bless`'s cache and FASTA
checks, plus `<fasta>.fai`); without a flag that check prints `SKIP`. Exit codes: 0
every check passed (`SKIP` does not fail), 1 a check failed, 2
`UV_PROJECT_ENVIRONMENT` unset, relative or inside a checkout (or bad usage), 3
unexpected. It never writes; it runs with `uv run --no-project`, so it creates no
environment before checking `UV_PROJECT_ENVIRONMENT`. The skill helper `dt env` calls it.

## ./bless

`./bless` makes and checks the oracle of a data-test directory `tests/data/<name>/`:
`expected_output.vcf`, the real output of native VEP 116.2 on the directory's
normalised `input.vcf` (made by `tools/normalize_input`, #85). It runs Ensembl's
official image `ensemblorg/ensembl-vep:release_116.2`, with the same fixed command plus
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
  exactly on the whole token; today these are `--check_existing` and `--merged`. Aliases (`--fa`),
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
`ensemblorg/ensembl-vep:release_116.2`; the real run resolves it to a digest first.

**What a bless records** in `test.toml`:

```toml
[vep]
image = "ensemblorg/ensembl-vep@sha256:..."   # the digest that ran, never the tag
command = "vep --offline --cache --dir_cache /opt/vep/.vep ..."  # generated from extra_flags; paths inside the container
date = "2026-09-23"
cache_source = "https://huggingface.co/datasets/biodatageeks/vepyr_116_GRCh38_merged/tree/5b83dd8d249106c6cc3f1c04c522b4bec716cc97"
vep_cache = "https://ftp.ensembl.org/pub/release-116/variation/indexed_vep_cache/homo_sapiens_merged_vep_116_GRCh38.tar.gz"
vep_cache_checksum = "unverified"
fasta_source = "https://ftp.ensembl.org/pub/release-116/fasta/homo_sapiens/dna/Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz"
fasta_checksum = "sha256:... sum:22450 861294"
extra_flags = ["--merged"]  # the source of truth; key order is not significant
[compare]
body_md5 = "..."
```

`cache_source` is the vepyr dataset URL at the revision in `PINS.toml`.
`vep_cache` and `fasta_source` are the native VEP cache and FASTA download URLs.
`vep_cache_checksum` and `fasta_checksum` come from the corresponding
`.bless-source.toml` download receipts. Without a valid receipt, `bless` records
the declared Ensembl download URL and keeps the checksum `unverified`. These
URLs identify the intended data; they do not establish that a local file was
downloaded from that URL. No machine-local paths are stored in source fields.

**Create a merged fixture** using Docker, an existing native merged cache
under `<VEP_CACHE>/homo_sapiens_merged/116_GRCh38`, and the GRCh38 FASTA.
After normalization, complete `test.toml` using the merged schema below:

```bash
tools/normalize_input raw.vcf.gz tests/data/my_test        # writes input.vcf + [input]
# Complete test.toml, including flavour = "merged" and extra_flags = ["--merged"].
./bless --vep-cache-dir "$VEP_CACHE" --vep-fasta "$VEP_FASTA" tests/data/my_test
./bless --check tests/data/my_test                           # exit 0
./bless --check --reproduce --vep-cache-dir "$VEP_CACHE" --vep-fasta "$VEP_FASTA" tests/data/my_test
```

The native cache downloader currently downloads Ensembl-only data; it does not
provision the merged cache required by the committed suite.

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
fields in VEP 116.2, regulatory and motif fields included), as VEP does, and the
loader rejects `fields` as an unknown key. The other VEP flags of the fixed command
(`--offline`, `--cache`, `--dir_cache`, `--species`, `--cache_version`,
`--assembly`, input/output names) select the cache and files, not annotation, and
have no `[vepyr]` counterpart; `flavour` and `required_contigs` pick vepyr's cache.
Every committed data fixture uses `flavour = "merged"` and records
`extra_flags = ["--merged"]` in `[vep]`; the recorded VEP command and every
`[[vepyr_run]]` must use the same cache flavour. The loader still accepts
Ensembl-only external and synthetic fixtures; RefSeq-only fixtures remain
unsupported. For merged oracles, pass `--vep-cache-dir` pointing to the parent
of `homo_sapiens_merged/116_GRCh38`; merged-cache downloads through `./bless`
are not implemented. The default `./run_tests` selection fetches and checks
freshness only for the merged dataset, so unused Ensembl or RefSeq pins cannot
block the suite.
Before each run the runner checks that every cache entity vepyr reads in
`--everything` mode (all seven; `motif` and `regulatory` excepted on `chrMT`) has a
shard for each `required_contigs` entry.

Extra flags stay as described under [./bless](#bless): the allowlist
`ALLOWED_VEP_FLAGS` accepts `--check_existing` (#18) and `--merged`, and `[vep] extra_flags` (#108)
remains the mechanism that records them. Neither is part of the vepyr CLI docs;
they are appended to the `--everything` command, never replace it.

## tests/common (cache + assertion helpers)

Fetch a cache, then point `$VEPYR_CACHE_ROOT` at the same directory (or pass
`--cache-dir` to `./run_tests` together with `--vepyr`):

```bash
./run_tests --cache-dir /mnt/hf-cache --add-contigs chr1,chr21,chr22
export VEPYR_CACHE_ROOT=/mnt/hf-cache
./run_tests --vepyr 0.7.0
# helpers type-check (and floating engine deps resolve) with:
cargo check --tests
```

Shared modules under `tests/common/`: `cache` / `ledger` (issue #5), plus
`annotate`, `csq`, and `provenance` for data-problem pilots (issue #14). Data-tests
live as directories `tests/data/<name>/`, run by `tests/data_dirs.rs` and listed
by `./run_tests --list`.

`tests/INDEX.csv` lists one row per named test. The `dir` column identifies its
shared fixture; `id` and `description` identify the test inside `[[tests]]`.
Other columns are `vep_test`, `cache_source`, `vep_cache`, `fasta_source`,
`required_contigs` (`;`-joined), `vepyr_runs`, `body_md5`, and `skip_reason`
(empty for enabled fixtures).
It is generated by `tools/build_test_index` and never edited by hand:
after adding or changing a test, run `tools/build_test_index` and commit the index
with the change (the file stays tracked; `.gitattributes` marks it
`linguist-generated`, so GitHub collapses it in diffs; a merge conflict in it is
resolved by regeneration, never by hand: the recipe and the merge order are in
`AGENTS.md`, "`tests/INDEX.csv` in parallel PRs", #151; `dt verify` runs the check
as its `build_test_index` step). `tools/build_test_index --check` changes nothing
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
`ensemblorg/ensembl-vep@sha256:<64 hex>`) and `one-to-one` (#193: the oracle
body is the input's records minus those whose every ALT allele is `.`, which VEP
116 skips without `--allow_non_variant`, in input order, compared line by line
on columns 1-5 verbatim; a lost, extra, reordered or substituted line fails, and
so does an input whose every record has ALT `.`, since nothing would be compared;
there is no option or `test.toml` key to skip it). It prints
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

`tools/workspace_guard` (issue #163) holds the workspace safety rules for agents
working in clones; it never writes or deletes anything and prints one
`OK <what>` or `REFUSED <what>: <reason>` line. `write-target DIR --protect PATH
[--protect PATH ...] [--expect-checkout PATH]` allows a write only if `DIR` is
absolute and a direct child of `<toplevel>/tests/data` of the git checkout
containing it (compared after `realpath`), the cwd is in that checkout, it is
the `--expect-checkout` checkout if given, and it is none of the `--protect`
paths, compared by inode (case-folded fallback, so symlink and APFS case
variants are caught); `DT_ALLOW_MAIN=1` is the only override. At least one
`--protect` is required, and a relative, empty or missing one is exit 2, never
resolved against the cwd. `outside-checkouts PATH...` refuses a path whose
nearest existing ancestor is inside any git checkout or worktree (e.g.
`"$UV_PROJECT_ENVIRONMENT"` or a scratch root). `base [--ref origin/master]`
checks that `HEAD` contains the ref; `upstream --not REF` refuses a current
branch that tracks `REF` (no upstream or a detached `HEAD` is fine). Exit 0
allowed/ok, 1 refused or check failed, 2 usage, unusable input or a git failure
(e.g. a missing ref). The data-test skill's `dt` runs it for `raw2input`,
`bless`, `verify` (scratch root) and `env` (repo, scratch root, base,
upstream), supplying the policy (`main_checkout`, `scratch_root`) from its
config; `UV_PROJECT_ENVIRONMENT` is checked by `./check_env`.

### Caveats

**Windows.** `./run_tests` is a bash script (it bootstraps `uv` and then runs
`tools/run_tests/`), so it needs a POSIX shell: use Git Bash or WSL. `cmd.exe` and
PowerShell cannot execute it directly.

**Accumulation.** `--add-contigs` only adds shards; it never removes earlier
ones. `chr21,chr22` then `chr15,chrY` leaves all four on disk. A per-contig
run checks each `<entity>/chrom_manifest.json` against the shards it requests:
a manifest that names a shard absent from disk, or misses a requested shard
that is on disk, is re-fetched from the Hub and trimmed to the shards on disk
(here all four). Otherwise it is left untouched. Shards on disk that the run
does not request are not checked, so a shard the Hub manifest itself omits
(e.g. `exon/GL000009.2.parquet` of a whole-flavour download) stays unlisted and
forces no Hub call. A requested shard the Hub manifest omits is different: it
is on disk but never listed, so every run that requests it makes one
manifests-only Hub call, and the shard stays unusable. An older root whose
manifests are stale (shards of a later contig, manifests trimmed to the first
set) is repaired only by rerunning `--add-contigs` with the declared list (the
list the `requires_shards` panic prints); a narrower rerun, e.g. only an
already-listed contig, leaves the stale manifests as they are. A whole-genome
run (no `--add-contigs`) on a root whose `PROVENANCE.json` records
`manifests_trimmed: true` for a flavour (left by a per-contig run) re-fetches
that flavour's full manifests from the Hub once and does not trim them, so they
match the `contigs: "ALL"` it records; later whole-genome runs on that root make
no such call. For a
wholly different set, use a fresh `--cache-dir` or clean the directory yourself.

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
  expected_output.vcf   # real VEP 116.2 output on input.vcf: ./bless (#32)
  test.toml             # provenance, how vepyr runs, the body md5
```

**Making one.** Pick a candidate, write its raw VCF, then:

```bash
tools/normalize_input raw.vcf.gz tests/data/<name>   # input.vcf + [input]
./bless --vep-cache-dir ~/vep116 --vep-fasta ~/GRCh38.fa tests/data/<name>   # oracle + [vep] + [compare]
# then fill name, description, [[tests]] and [vepyr] by hand
VEPYR_CACHE_ROOT=/mnt/hf-cache cargo test --test data_dirs
```

VEP and vepyr read the same `input.vcf`, byte for byte. Candidates come from the
assertion ledger `ledger/assertions.csv`; each named test links directly to the
corresponding upstream assertion using its release tag.

**One fixture, several tests.** Each directory stores one distinct input,
configuration and expected output. All committed fixtures use `[[tests]]`,
including fixtures with only one test. Each test has a globally unique `id`, a
description and one `vep_test` URL. One test id equals the fixture directory name.
The top-level description describes the shared fixture.

A **run** executes a fixture with one configuration. Without `[[vepyr_run]]`,
the runner uses `[vepyr]` once. With overrides, it executes once per override;
there is no additional base run. The 70 fixtures configure 74 runs because
`runner_buffer_size_invariance` supplies five buffer sizes. Named `[[tests]]`
entries describe coverage and do not create additional runs.

```toml
name = "example_fixture"
description = "Fixture covering two tests."

[input]
command = "bcftools norm -m -both -o <out.vcf> <in.vcf.gz>"
bcftools_version = "bcftools 1.23"

[vepyr]
flavour = "merged"
required_contigs = ["chr21"]
everything = true
preserve_record_layout = true
reference_fasta = true

[vep]
# Written by ./bless; source URLs and checksum meanings are documented above.
image = "ensemblorg/ensembl-vep@sha256:..."
command = "vep ..."
date = "2026-10-09"
cache_source = "https://huggingface.co/datasets/biodatageeks/vepyr_116_GRCh38_merged/tree/<revision>"
vep_cache = "https://ftp.ensembl.org/pub/release-116/variation/indexed_vep_cache/homo_sapiens_merged_vep_116_GRCh38.tar.gz"
vep_cache_checksum = "unverified"
fasta_source = "https://ftp.ensembl.org/pub/release-116/fasta/homo_sapiens/dna/Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz"
fasta_checksum = "unverified"
extra_flags = ["--merged"]

[compare]
body_md5 = "..."

[[tests]]
id = "example_fixture"
description = "Report the expected transcript consequence."
vep_test = "https://github.com/Ensembl/ensembl-vep/blob/release/116.2/t/Runner.t#L244-L292"

[[tests]]
id = "example_second_test"
description = "Report the expected transcript identifier."
vep_test = "https://github.com/Ensembl/ensembl-vep/blob/release/116.2/t/Runner.t#L244-L292"
```

The loader rejects unknown keys and requires a tagged Ensembl VEP `.t` URL.
`vep_test_pinned`, `vep_subject`, `ledger`, and `issue` are no longer fixture
metadata. The software commit and Docker digest remain in `tools/vep_pin.toml`.
All committed source links use that pin's release tag. The loader also
accepts `[origin]` containing only `vep_test` for external single-test fixtures;
it cannot be combined with `[[tests]]`. The old `[[property]]` spelling is rejected.

Optional runtime overrides remain `[[vepyr_run]]`, with keys from `[vepyr]`.
Optional `[vep] extra_flags` is governed by the blessing allowlist. A test may
carry `focus` metadata with kinds `csq`, `csq_values`, `column`, `info` or
`record_count`. This change does not add separate field evaluation to the runner:
each fixture still executes once per run configuration and compares the entire
output body. The named tests explain the coverage of that comparison.

To disable annotation for an unsupported feature, set a top-level reason,
before any TOML table:

```toml
skip_reason = "Symbolic deletions (<DEL> with END) are not supported by vepyr yet."
```

The reason must be a non-empty string. Both the default Rust runner and
`--via-cli` print `SKIP <fixture>: <reason>` and count the fixture separately
from passes. All its runs and named tests are skipped; the fixture remains in
`--list` and `tests/INDEX.csv`. Remove `skip_reason` to enable it again, including
when using `--only`. Cache setup and freshness checks still apply to the invocation.
Structural and normalization checks still cover skipped fixtures; the Rust
runner also validates their schema, reads the input and checks oracle integrity
before skipping annotation. Only `sv_deletion_end_feature_truncation` is
currently skipped; literal sequence deletion fixtures remain enabled.

```bash
tools/check_unique_dirs tests/data
tools/merge_duplicate_dirs --index tests/INDEX.csv tests/data
```

The duplicate key is input body, oracle body, `[vepyr]`, `[[vepyr_run]]`, and
`[vep] command`. The idempotent merger keeps the first directory in sort order,
moves every test into its `[[tests]]` list and preserves retained input/oracle
bytes and all test ids. Add a test to the matching fixture instead of duplicating
the directory. The merger refuses fixtures with different `skip_reason` values
(including a skipped fixture paired with an enabled one).
The committed suite has **70 fixtures and 205 named tests**, with
no duplicate comparisons. The remaining 15 Ensembl-only fixtures were re-blessed
with native VEP 116.2 and the merged cache, using byte-identical original inputs.
Four additional pairs became identical comparisons and were grouped, preserving
every input body and all named tests. Equivalent contig headers can differ between
grouped inputs. The five buffer-size configurations are retained. One fixture, one run
and one named test are explicitly skipped for unsupported symbolic deletion.
The [migration audit](docs/porting/merged-fixture-migration/audit.json) records
old/new oracle hashes, retained test ids and property checks for every re-bless.

**What the runner checks**, per directory:

1. *Self-check:* `[compare] body_md5` equals the md5 of the body of
   `expected_output.vcf` (body = every line not starting with `#`), otherwise
   `[<name>] oracle edited`.
2. A fixture with `skip_reason` is reported as skipped. Otherwise, vepyr
   annotates `input.vcf` once per run (one run from `[vepyr]`, or one per
   `[[vepyr_run]]` entry, each printed as `run <n>/<N>: <overrides>`).
3. The md5 of vepyr's body must equal `body_md5`. Otherwise
   `[<name>] body md5 mismatch`, `expected <md5>, got <md5>`, and the first
   differing record on a `VEP:` and a `vepyr:` line. CSQ group order is part of the
   body, so it is asserted too.
4. An executed comparison that mismatches always fails. An explicit skip for an
   unsupported feature disables annotation; it never turns a mismatch into a pass.

`everything`, `preserve_record_layout` and `reference_fasta` must hold the values
of [One mode: --everything](#one-mode---everything); there is no `fields` key, so
vepyr emits its full `--everything` CSQ layout (86 fields for the merged oracles).

`DATA_DIRS_ROOT` overrides the walked directory. `cargo test --test data_dirs
selftest` runs the same loader and compare on the synthetic fixture
`tests/fixtures/data_dirs_selftest/` against a synthetic one-shard cache with a
synthetic one-contig reference FASTA, with no downloaded data.

Corpus dataset pins (`PINS.toml`) are documented in
[docs/dataset-pins.md](docs/dataset-pins.md).

### How do we know there are no more assertions in the Perl files?

**How they are enumerated.** The upstream test files are the 49 `t/*.t` of Ensembl
VEP at the tag and commit pinned in `tools/check_ledger` (`DEFAULT_REF`,
`PINNED_COMMIT`; the ledger axis, still VEP 116.0). A line is
one assertion if it matches a line-start regex over 22 function names:
`ok is isnt like unlike is_deeply cmp_ok isa_ok can_ok new_ok pass fail use_ok
require_ok` (Test::More), `throws_ok dies_ok lives_ok lives_and` (Test::Exception)
and `cmp_deeply cmp_bag cmp_set cmp_methods` (Test::Deep). All 22 are assertion
functions. The Test::Warnings functions `warning` / `warnings` are capture functions,
not assertions (they run a block and return its warnings), and are deliberately not
in the rule, so the 5 bare `warning { ... };` statements are not rows. `n` is the
ordinal of the assertion in its file, `perl_line` the line it starts on.
`tools/check_ledger --list` prints the enumeration.

**Why it is trusted.** The rule gives 1965 assertions (is 841, is_deeply 588, ok 272,
use_ok 148, throws_ok 99, like 11, cmp_deeply 3, dies_ok 2, isa_ok 1). An earlier
independent Rust lexer from the deprecated porting repository found the same
`(file, line, kind)` rows, with 0 differences in 49 files apart from its 5 `warning`
capture rows. `tools/check_ledger --sweep` searches the same files for every function
name documented by Test::More, Test::Exception, Test::Deep and Test::Warnings as a
bare word, at line start or mid-line, after removing comments, strings, regex
literals, sigiled names, every `{ word }` and every `word =>`, and reports every
occurrence the rule did not count; on the pinned files the only ones are explained
non-assertions (`use`/`no warnings`, `done_testing`, `skip`, `diag`, Test::Deep
comparators as arguments, import lists and the 6 `warning {` captures), and nothing
unexplained. The CSV is compared with the enumeration in both directions (missing
and orphan rows, `perl_line`, `perl_kind`), and the tool refuses any other commit,
so a moved tag or an edited file cannot pass silently.

**Limits.** It is not a general Perl parser and is correct for
these 49 files at this ref only: a line-start scan does not see assertions reached through helper subs or
names outside the documented lists, and a line run many times in a loop is counted
once; support files such as `t/VEPTestingConfig.pm` are not test files and are not
read for rows (none of the 8 top-level `t/*.pm` contains an assertion). The sweep
does not see three forms, none of which occurs in the pinned files: an assertion
alone in a block, `if (1) { fail }`, and `eval { pass };` (every `{ word }` is
blanked as a hash key), and a call with the `&` sigil, `&ok(1, "x");` (blanked as
a variable).
The tool ignores inherited `GIT_*` variables and compares the bytes it reads with
the pinned tree: every selected file's content is hashed in Python and must equal
its blob id in `git ls-tree -r HEAD`, so an edit that `git status` does not show
(through the checkout's own `core.worktree` or `core.fsmonitor`) is refused; a file
flagged assume-unchanged or skip-worktree in the index is refused by name. Git
runs with `--no-replace-objects`, so a `refs/replace/*` ref in the checkout cannot
swap the pinned tree that the blob ids are read from. A
`core.autocrlf=true` checkout is accepted: a file also passes when its bytes after
git's autocrlf CRLF-to-LF conversion hash to the blob id, which changes no line
number or assertion kind.

## Agent setup (per machine)

**Platform.** `dt` and the repo's local tooling (the scripts at the repo root, such as
`./run_tests`, `./bless` and `./check_env`, and those under `tools/`) have so far been run
and tested only on macOS (Darwin, Docker Desktop, `uv`, bash/zsh). Linux is untested, and
no CI run covers it (the GitHub workflows are disabled).

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

## Release 116.2 merged-cache campaign

The [campaign table](docs/porting/vep1162-merged/README.md) tracks each atomic
candidate, its source assertions and implementation lines, and separate old-cache,
vepyr-difference and unsupported-feature columns. Queued and blocked candidates
are excluded from ported counts.

`tools/port_campaign.py` runs qualified cases in batches of ten. It first writes
raw rows to the external evidence directory, runs `tools/normalize_input`, then
passes the resulting `input.vcf` to both engines. `./bless` verifies the SHA-256 of
its Docker input copy; the campaign verifies that the normalized file stays
unchanged before VEP, before vepyr and after vepyr. The expected VCF comes only
from VEP 116.2. Its body MD5 is recorded in `test.toml`. vepyr is run and compared
by `./run_tests --cache-dir <cache> --via-cli --only <dir>`; the campaign records
`PASS` on exit 0, `FAIL` on exit 8 with a `MISMATCH <dir> expected=<md5>
actual=<md5>` line on stdout (that `actual` is the recorded vepyr body MD5), and
`ERROR` on any other exit. All commands, their exit codes and the `./run_tests`
output are recorded in `cases.json` and the evidence directory, and the campaign
exits non-zero unless every processed case passes. Header lines are excluded
from comparison.
`--regenerate` repeats normalization and both runs for existing fixtures that
have not completed this input-identity audit. Use a new external evidence directory
for that pass; prior run evidence is retained.

`--vep-cache` (the native cache used by `./bless` for the oracle) may be a local
cache. `--cache-dir` is passed to `./run_tests` and must be a Hub-layout cache
root with `PROVENANCE.json` (populate it with
`./run_tests --cache-dir <cache> --add-contigs chr21`).

```bash
python tools/port_campaign.py --limit 10 \
  --vep-cache /path/to/native-cache-parent \
  --cache-dir /path/to/hub-layout-cache \
  --fasta /path/to/Homo_sapiens.GRCh38.dna.primary_assembly.fa \
  --evidence /path/to/run-evidence
python tools/check_campaign.py --require-complete --require-normalized
```

Primary-property checks select the case's specific field or record property;
the existing body comparison also checks all incidental fields. A focus pass
with a body failure remains a failing data test. For runs made through
`./run_tests --via-cli` the primary property of vepyr's output is not checked:
the campaign does not keep vepyr's output file, so only the whole-body md5
verdict and the oracle's own focus witness are recorded, and the status table
shows the focus as `not checked`.

This campaign contains 189 executed ports (171 SNVs, 15 small indels, two
nonvariant cases and one MNV): 177 whole-body passes, 10 differences and two
execution errors on the recorded master revision. Thirteen further candidates
remain unported: ten have data/configuration or cache-conversion blockers, and
three are outside this normalized VCF contract. See the
[unblocking assessment](docs/porting/vep1162-merged/unblocking/README.md).
The 16 pre-existing fixtures are outside this campaign; their inputs were
regenerated unchanged and their prior oracles were retained.
See the [failure evidence](docs/porting/vep1162-merged/FAILURES.md) and the
[complete assertion audit](docs/porting/vep1162-assertion-audit/README.md).
