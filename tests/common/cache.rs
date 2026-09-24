//! Locating the VEP 116 Parquet caches and the GRCh38 FASTA for data-problem tests.
//!
//! **Single bridge.** Every test that opens the official HF cache goes through here.
//! This module reads exactly one root: `$VEPYR_CACHE_ROOT`, the same directory
//! `./run_tests --cache-dir` materialises. Nothing here touches the network — the
//! revision check compares on-disk `PROVENANCE.json` against crate-root `PINS.toml`.
//!
//! **Fail-loud, never skip.** A missing env, missing provenance, revision mismatch,
//! or missing shard panics with a one-line message that includes a `Run:` repair
//! command naming `./run_tests` (`--cache-dir`, `--flavours`, `--add-contigs`).
//!
//! **Required contigs.** Contigs are not discovered from the VCF at runtime. Each
//! data-test directory declares them in `tests/data/<name>/test.toml` as
//! `[vepyr] required_contigs` (with the cache `entities` it reads) — exactly the
//! contigs of the loci under test (not wider, not narrower). The generic runner
//! `tests/data_dirs.rs` passes both to [`requires_shards`]; the older per-file
//! data-tests read the same field from an assertion fragment (see [`super::ledger`]).
//! The cache flavour is chosen by the directory under the root
//! ([`Flavour::dir_name`]), never by an engine config flag. This module only enforces
//! shards for the list it is given.
//!
//! ```text
//! ./run_tests --cache-dir /mnt/hf-cache --add-contigs chr21,chrMT
//! export VEPYR_CACHE_ROOT=/mnt/hf-cache
//! cargo test
//! ```

use std::path::{Path, PathBuf};

/// The three cache flavours published on the Hub, as `./run_tests` lays them out.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Flavour {
    Ensembl,
    RefSeq,
    Merged,
}

impl Flavour {
    /// Directory under the root — the name the HuggingFace README uses.
    pub fn dir_name(self) -> &'static str {
        match self {
            Self::Ensembl => "116_GRCh38_ensembl",
            Self::RefSeq => "116_GRCh38_refseq",
            Self::Merged => "116_GRCh38_merged",
        }
    }

    /// Key in `PROVENANCE.json` `datasets` and suffix of the `PINS.toml` table.
    pub fn key(self) -> &'static str {
        match self {
            Self::Ensembl => "ensembl",
            Self::RefSeq => "refseq",
            Self::Merged => "merged",
        }
    }

    /// The `PINS.toml` table carrying this flavour's revision.
    pub fn pin_name(self) -> String {
        format!("hf_cache_{}", self.key())
    }

    /// Inverse of [`Flavour::key`] — how a `test.toml` `[vepyr] flavour` names it.
    pub fn from_key(key: &str) -> Option<Self> {
        [Self::Ensembl, Self::RefSeq, Self::Merged]
            .into_iter()
            .find(|flavour| flavour.key() == key)
    }
}

/// The seven entities a flavour carries — the subdirectories of `116_GRCh38_<flavour>/`.
///
/// Not every entity has a shard for every contig: at the pinned revisions `motif/` and
/// `regulatory/` hold 24 shards (chr1–22, X, Y) while `variation/` holds 463, so a test
/// names the `(entity, contig)` pairs it reads through [`requires_shards`] rather than
/// asking for "the contig" across the board.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Entity {
    Exon,
    Motif,
    Regulatory,
    Transcript,
    TranslationCore,
    TranslationSift,
    Variation,
}

impl Entity {
    /// All seven, in directory order.
    pub const ALL: [Entity; 7] = [
        Self::Exon,
        Self::Motif,
        Self::Regulatory,
        Self::Transcript,
        Self::TranslationCore,
        Self::TranslationSift,
        Self::Variation,
    ];

    /// The subdirectory under the flavour, as `./run_tests` lays it out.
    pub fn dir_name(self) -> &'static str {
        match self {
            Self::Exon => "exon",
            Self::Motif => "motif",
            Self::Regulatory => "regulatory",
            Self::Transcript => "transcript",
            Self::TranslationCore => "translation_core",
            Self::TranslationSift => "translation_sift",
            Self::Variation => "variation",
        }
    }

    /// Inverse of [`Entity::dir_name`] — how a `test.toml` `[vepyr] entities` names it.
    pub fn from_dir_name(name: &str) -> Option<Self> {
        Self::ALL
            .into_iter()
            .find(|entity| entity.dir_name() == name)
    }
}

/// One flavour of the cache, located and revision-checked.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FullCache {
    /// `$VEPYR_CACHE_ROOT`.
    pub root: PathBuf,
    pub flavour: Flavour,
    /// The dataset commit sha both `PROVENANCE.json` and `PINS.toml` agree on.
    pub revision: String,
}

impl FullCache {
    /// `<root>/116_GRCh38_<flavour>` — what the engine takes as its cache source.
    pub fn dir(&self) -> PathBuf {
        self.root.join(self.flavour.dir_name())
    }
}

const ENV: &str = "VEPYR_CACHE_ROOT";
const PROVENANCE: &str = "PROVENANCE.json";
const FASTA: &str = "fasta/Homo_sapiens.GRCh38.dna.primary_assembly.fa";

/// `$VEPYR_CACHE_ROOT`, or panic 1.
#[track_caller]
fn root() -> PathBuf {
    match std::env::var_os(ENV) {
        Some(value) if !value.is_empty() => PathBuf::from(value),
        _ => panic!(
            "{ENV} is not set. Run: ./run_tests --cache-dir /path [--add-contigs LIST] \
             && export {ENV}=/path"
        ),
    }
}

/// The revision `PINS.toml` pins for `flavour`, read at test time from the crate root.
#[track_caller]
pub fn pinned_revision(flavour: Flavour) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("PINS.toml");
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("{}: {error}", path.display()));
    pinned_revision_in(&text, flavour)
}

/// [`pinned_revision`] over the text of a `PINS.toml`: `[hf_cache_<flavour>].sha`, parsed
/// with the `toml` crate, so a missing table, a missing key or a non-string value is a
/// panic — never an empty string reported as a fact.
#[track_caller]
pub fn pinned_revision_in(pins_toml: &str, flavour: Flavour) -> String {
    let table = flavour.pin_name();
    let pins: toml::Table = toml::from_str(pins_toml)
        .unwrap_or_else(|error| panic!("PINS.toml does not parse: {error}"));
    match pins.get(&table).and_then(|pin| pin.get("sha")) {
        Some(toml::Value::String(sha)) if !sha.is_empty() => sha.clone(),
        Some(other) => panic!("PINS.toml [{table}].sha is not a non-empty string: {other}"),
        None => panic!("PINS.toml has no `sha` under [{table}]"),
    }
}

/// The revision `PROVENANCE.json` records for `flavour`, or `None` when the root has
/// no provenance or no entry for that flavour.
fn revision_on_disk(root: &Path, flavour: Flavour) -> Option<String> {
    let text = std::fs::read_to_string(root.join(PROVENANCE)).ok()?;
    // `"ensembl": { "repo_id": ..., "revision": "<sha>", ...` — find the flavour's object,
    // then the first `"revision"` after it. Sound because `./run_tests` serialises
    // `datasets` BEFORE `fasta` and `runs`, so a flavour token inside `runs[].argv` is
    // never the first hit; the quoted key cannot match inside `"ensembl_sum"` or
    // `"…GRCh38_merged"`.
    let key = format!("\"{}\"", flavour.key());
    let start = text.find(&key)?;
    let after = &text[start..];
    let at = after.find("\"revision\"")?;
    let rest = after[at + "\"revision\"".len()..]
        .trim_start()
        .strip_prefix(':')?
        .trim_start();
    let value = rest.strip_prefix('"')?;
    Some(value[..value.find('"')?].to_owned())
}

/// Locate `flavour` under `$VEPYR_CACHE_ROOT` and check its revision — panics 1 and 2.
#[track_caller]
pub fn full_cache(flavour: Flavour) -> FullCache {
    full_cache_at(&root(), flavour)
}

/// [`full_cache`] against an explicit root (unit tests and callers that already resolved
/// the cache directory).
#[track_caller]
pub fn full_cache_at(root: &Path, flavour: Flavour) -> FullCache {
    let root = root.to_path_buf();
    let pinned = pinned_revision(flavour);
    match revision_on_disk(&root, flavour) {
        Some(found) if found == pinned => {}
        Some(found) => panic!(
            "cache at {} is revision {found}, PINS.toml pins {pinned} ({}). One root \
             holds one revision per flavour: fetch the pinned revision into a NEW root \
             (./run_tests --cache-dir <other> --flavours {}) and set {}=<other>",
            root.display(),
            flavour.pin_name(),
            flavour.key(),
            ENV
        ),
        None => panic!(
            "cache at {} has no PROVENANCE.json entry for {} (flavour never fetched). Run: \
             ./run_tests --cache-dir {} --flavours {}",
            root.display(),
            flavour.key(),
            root.display(),
            flavour.key()
        ),
    }
    FullCache {
        root,
        flavour,
        revision: pinned,
    }
}

/// Every declared `(entity, contig)` pair is on disk as `<entity>/<contig>.parquet` and
/// listed in that entity's manifest — panic 3 otherwise, for the first missing pair.
///
/// The caller names the entities it actually reads: the engine opens only what a run
/// needs, and the Hub datasets do not carry every contig in every entity (see
/// [`Entity`]). A pair that is declared and absent is a hard failure, never a skip.
///
/// Contig lists should come from the ledger field `required_contigs` (see
/// [`super::ledger`]), equal to the contigs of the loci under test.
///
/// The repair command names the flavour and the WHOLE declared contig list, not the one
/// missing contig: `./run_tests` refuses a selection some entity cannot serve
/// (`--add-contigs chrMT` alone exits 4 because `motif/` has no chrMT), while the list a
/// test declares is by construction one the tool accepts.
#[track_caller]
pub fn requires_shards(cache: &FullCache, entities: &[Entity], contigs: &[&str]) {
    let declared = contigs.join(",");
    for contig in contigs {
        for entity in entities {
            let entity = entity.dir_name();
            let shard = cache.dir().join(entity).join(format!("{contig}.parquet"));
            if !shard.is_file() {
                panic!(
                    "shard {}/{entity}/{contig}.parquet missing at {}. Run: ./run_tests \
                     --cache-dir {} --flavours {} --add-contigs {declared}",
                    cache.flavour.dir_name(),
                    cache.root.display(),
                    cache.root.display(),
                    cache.flavour.key()
                );
            }
            let manifest = cache.dir().join(entity).join("chrom_manifest.json");
            let text = std::fs::read_to_string(&manifest)
                .unwrap_or_else(|error| panic!("{}: {error}", manifest.display()));
            if !text.contains(&format!("\"{contig}.parquet\"")) {
                panic!(
                    "manifest {}/{entity}/chrom_manifest.json does not list {contig}.parquet at \
                     {} (trimmed by an earlier per-contig run?). Run: ./run_tests --cache-dir {} \
                     --flavours {} --add-contigs {declared}",
                    cache.flavour.dir_name(),
                    cache.root.display(),
                    cache.root.display(),
                    cache.flavour.key()
                );
            }
        }
    }
}

/// `$VEPYR_CACHE_ROOT/fasta/Homo_sapiens.GRCh38.dna.primary_assembly.fa`, with its `.fai`.
#[track_caller]
pub fn reference_fasta() -> PathBuf {
    reference_fasta_at(&root())
}

/// [`reference_fasta`] against an explicit root — panics with the repair command when the
/// `.fa` or its `.fai` is missing.
#[track_caller]
pub fn reference_fasta_at(root: &Path) -> PathBuf {
    let fa = root.join(FASTA);
    let fai = root.join(format!("{FASTA}.fai"));
    if !fa.is_file() || !fai.is_file() {
        panic!(
            "reference FASTA missing at {} (need .fa and .fai). Run: ./run_tests --cache-dir {}",
            fa.display(),
            root.display()
        );
    }
    fa
}
