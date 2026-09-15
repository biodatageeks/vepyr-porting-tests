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
| `--list` | Lists `tests/data_*.rs` targets (0 until the first pilot lands); exit 0 |
| `--cache-dir DIR` | Downloads the pinned VEP 116 shards into `DIR` and writes `PROVENANCE.json`; then runs data-tests when targets exist |
| `--add-contigs LIST` | Adds the named contigs to `DIR` (not `--contigs`). Default: whole genome |
| `--flavours LIST` | Default `ensembl,refseq,merged` |
| `--dry-run` | Lists Hub files and byte totals; writes nothing; does not run tests |
| `--verify` | Checks every selected shard against the Hub sha256 |
| `--fast` | Sets `HF_XET_HIGH_PERFORMANCE=1` for the download (see below) |
| `--no-trim-manifests` | Leaves `chrom_manifest.json` naming shards that were not fetched |
| `--vepyr REF` | Resolves `REF` on biodatageeks/vepyr and path-patches that revision's dfbf/formats ladder. **Optional:** omitted, data-tests run against `master`'s current HEAD; the summary prints the full 40-char resolved sha either way. Pass `REF` whenever a pinned, reproducible run is wanted (CI, bisecting, ledger evidence) |

**"Targets" means cargo test targets** — the `tests/data_*.rs` files. One file is
one target, named by its stem (`tests/data_foo.rs` → `data_foo`), and each becomes
one `--test data_foo` argument in the `cargo test` invocation. `--list` prints
exactly that set.

**`--vepyr REF` examples.** `REF` is anything `biodatageeks/vepyr` can dereference:

```bash
./run_tests --vepyr 0.7.0     # a tag — note there is NO `v` prefix
./run_tests --vepyr 1f0c3a9   # a commit sha, short…
./run_tests --vepyr 1f0c3a9e4b7d2c5a8f6013b9d4e27ca5f80b6d31   # …or full 40-char
./run_tests --vepyr master    # a branch (resolved to its HEAD at run time)
./run_tests                   # omitted: same as `--vepyr master`, resolved per run
```

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
live as `tests/data_*.rs` and are discovered by `./run_tests --list`.

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

The sections below describe the **porting method** used to extract, classify,
and implement those tests. Code and ledger fragments are **illustrative**.

## Porting method

In the Ensembl VEP test suite there are 1970 assertions across 49 `t/*.t`
files. They were all extracted as a source corpus.

```mermaid
flowchart LR
  A["VEP test file<br/>(.t)"]
  B["VEP assertions<br/>(a few per file)"]
  C["Assertion entry<br/>in .ledger.toml"]
  D["Classification<br/>portable / non-portable"]
  E["Rust test<br/>(if applicable)"]

  A --> B
  B -->|mapped to| C
  C -->|classified| D
  D -->|if portable| E
```

**Illustrative** — original Perl test fragment:

```perl
## BASIC TESTS
##############

# use test
use_ok('Bio::EnsEMBL::VEP::CacheDir');

# need to get a config object for further tests
use_ok('Bio::EnsEMBL::VEP::Config');

my $cfg_hash = $test_cfg->base_testing_cfg;

my $cfg = Bio::EnsEMBL::VEP::Config->new($cfg_hash);
ok($cfg, 'get new config object');

my $cd = Bio::EnsEMBL::VEP::CacheDir->new({config => $cfg, root_dir => $cfg_hash->{dir}});
ok($cd, 'new is defined');

is(ref($cd), 'Bio::EnsEMBL::VEP::CacheDir', 'check class');
```

Extracted assertions are stored as ledger entries in `.toml` files (in the
source method), formatted like the example below.

**Illustrative** — ledger assertion entry:

```toml
[[assertion]]
n               = 4
perl_line       = 42
perl_kind       = "ok"
desc            = "BASIC TESTS: CacheDir->new({config, root_dir}) resolves and accepts the v84 test cache"
category        = "analog-port"
coverage_area   = "annotation-source-setup"
rust_test       = "tests/port_cache_dir.rs::provider_accepts_a_v84_shaped_cache_root"
rationale       = "Not a bare truthiness probe: `new` calls `init` (CacheDir.pm:113) whose first statement is `my $dir = $self->dir` (CacheDir.pm:221), so this line asserts that directory resolution succeeded for the base testing config. vepyr has no CacheDir object; the constructor that consumes a cache directory is `TranscriptTableProvider::new(EnsemblCacheOptions{..})`, and the defined/undef distinction becomes `Ok`/`Err` — a different channel, hence analog-port. The named test rebuilds ensembl-vep's own `homo_sapiens/84_GRCh38` layout in a tempdir, constructs the provider and asserts it resolved the three chromosome directories. It is also the positive control for n=12, n=13 and n=14, whose negative arms use the same fixture."
vep116_delta    = "unchanged"
```

Each assertion is classified into one of six categories:
`unit-port`, `analog-port`, `failed`, `deferred`, `no-feature`, `vepyr-only`.
Classification fields stored with the assertion include:

- `category` — how the assertion maps from Ensembl VEP to vepyr
- `coverage_area` — short identifier for the feature area
- `rust_test` — Rust test path/name, if any
- `rationale` — freeform justification of the classification and mapping
- `vep116_delta` — change notes since Ensembl VEP release 116

From portable classifications, corresponding Rust tests are created.

**Illustrative** — Rust test fragment:

```rust
/// Ledger n=4 — the constructor accepts a well-formed cache directory.
#[test]
fn provider_accepts_a_v84_shaped_cache_root() {
    let (_tmp, leaf) = v84_cache();
    let provider = TranscriptTableProvider::new(ensembl_options(&leaf))
        .expect("v84-shaped cache root is accepted");
    assert_eq!(
        provider.chromosomes(),
        Some(v84_chroms().as_slice()),
        "the transcript entity resolves to the three chromosome directories",
    );
}
```

This repository will carry a curated subset of those ports as data-problem
tests.

Corpus dataset pins (`PINS.toml`) are documented in
[docs/dataset-pins.md](docs/dataset-pins.md).
