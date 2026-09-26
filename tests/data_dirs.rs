//! The generic data-test runner: one directory per test, VEP 116 output as oracle.
//!
//! A data-test is a directory `tests/data/<name>/` holding
//!
//! - `input.vcf` — the normalised input, written by `tools/normalize_input` (#85);
//!   VEP and vepyr both read exactly these bytes;
//! - `expected_output.vcf` — the real output of native Ensembl VEP 116 on that input,
//!   written by `./bless` (#32);
//! - `test.toml` — provenance plus how vepyr is run and compared (schema below).
//!
//! For every directory this runner annotates `input.vcf` with vepyr as `[vepyr]` says
//! and compares the md5 of vepyr's output **body** (every line not starting with `#`)
//! with `[compare] body_md5`, which is the md5 of the body of `expected_output.vcf`.
//!
//! # `test.toml` schema
//!
//! Every table is checked against a fixed key list; any other key panics with
//! `[<name>] unknown key: <key>`.
//!
//! | table | keys (`?` = optional) |
//! |---|---|
//! | top level | `name` (= directory name), `description` |
//! | `[origin]` | `vep_test`, `vep_test_pinned`, `vep_subject`, `ledger?`, `issue?` |
//! | `[input]` | `command` (the #85 command), `bcftools_version` |
//! | `[vep]` | `image`, `command`, `date`, `cache_source`, `cache_checksum`, `fasta_source`, `fasta_checksum` — exactly what `./bless` writes |
//! | `[vepyr]` | `flavour`, `entities`, `required_contigs`, `everything`, `fields`, `preserve_record_layout`, `reference_fasta`, `buffer_size?` |
//! | `[compare]` | `body_md5`, `known_divergence?` |
//! | `[[vepyr_run]]?` | any `[vepyr]` key, overriding it for that run |
//!
//! `[vepyr]` is mapped by hand onto `AnnotateVcfConfig` (it has no serde):
//! `everything`, `fields`, `preserve_record_layout` and `buffer_size` are config
//! fields; `reference_fasta = true` sets `reference_fasta_path` to
//! `cache::reference_fasta()`. `flavour` picks the cache directory
//! (`116_GRCh38_<flavour>`), never a config flag; `entities` and `required_contigs`
//! go to `cache::requires_shards`.
//!
//! # Failure messages (a fixed contract — other issues' ACs grep them)
//!
//! - `[<name>] oracle edited` — `[compare] body_md5` is not the md5 of the body of
//!   `expected_output.vcf`;
//! - `[<name>] unknown key: <key>` — panic while loading `test.toml`;
//! - `[<name>] body md5 mismatch`, then `expected <md5>, got <md5>`, then the first
//!   differing record on a line starting `VEP:` and one starting `vepyr:`;
//! - `[<name>] known_divergence obsolete` — the bodies match although
//!   `[compare] known_divergence` says they should not;
//! - with `[[vepyr_run]]`, each run first prints `run <n>/<N>: <overrides>`.
//!
//! # Roots
//!
//! - `data_dirs` walks `tests/data` against `$VEPYR_CACHE_ROOT` (the Hub cache).
//! - `selftest` walks `tests/fixtures/data_dirs_selftest` against a synthetic
//!   one-shard cache it writes into a temp directory, so the loader and compare logic
//!   are exercised without any downloaded data.
//!
//! `$DATA_DIRS_ROOT` overrides the directory both of them walk.

mod common;

use std::fmt::Write as _;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use datafusion_bio_function_vep::vcf_sink::AnnotateVcfConfig;
use md5::{Digest, Md5};
use toml::{Table, Value};

use common::annotate::annotate_vcf_at;
use common::annotate_config;
use common::cache::{self, Entity, Flavour, FullCache};

/// Environment variable overriding the directory a runner walks.
const ROOT_ENV: &str = "DATA_DIRS_ROOT";
/// The `[input] command` `tools/normalize_input` records (#85); kept identical to
/// `tools/bless/testdir.py::NORMALIZE_COMMAND`.
const NORMALIZE_COMMAND: &str = "bcftools norm -m -both -o <out.vcf> <in.vcf.gz>";

const INPUT_NAME: &str = "input.vcf";
const ORACLE_NAME: &str = "expected_output.vcf";
const TOML_NAME: &str = "test.toml";

// ---------------------------------------------------------------------------------
// Schema
// ---------------------------------------------------------------------------------

/// The TOML type a key must have.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Kind {
    Str,
    Int,
    Bool,
    StrList,
    Table,
    TableList,
}

impl Kind {
    fn matches(self, value: &Value) -> bool {
        match (self, value) {
            (Self::Str, Value::String(_))
            | (Self::Int, Value::Integer(_))
            | (Self::Bool, Value::Boolean(_))
            | (Self::Table, Value::Table(_)) => true,
            (Self::StrList, Value::Array(items)) => items.iter().all(Value::is_str),
            (Self::TableList, Value::Array(items)) => items.iter().all(Value::is_table),
            _ => false,
        }
    }

    fn describe(self) -> &'static str {
        match self {
            Self::Str => "a string",
            Self::Int => "an integer",
            Self::Bool => "a boolean",
            Self::StrList => "an array of strings",
            Self::Table => "a table",
            Self::TableList => "an array of tables",
        }
    }
}

/// One allowed key of a table: name, type, and whether it must be present.
type Key = (&'static str, Kind, bool);

const TOP_KEYS: &[Key] = &[
    ("name", Kind::Str, true),
    ("description", Kind::Str, true),
    ("origin", Kind::Table, true),
    ("input", Kind::Table, true),
    ("vep", Kind::Table, true),
    ("vepyr", Kind::Table, true),
    ("compare", Kind::Table, true),
    ("vepyr_run", Kind::TableList, false),
];

const ORIGIN_KEYS: &[Key] = &[
    ("vep_test", Kind::Str, true),
    ("vep_test_pinned", Kind::Str, true),
    ("vep_subject", Kind::Str, true),
    ("ledger", Kind::Str, false),
    ("issue", Kind::Int, false),
];

const INPUT_KEYS: &[Key] = &[
    ("command", Kind::Str, true),
    ("bcftools_version", Kind::Str, true),
];

/// Exactly the keys `tools/bless/cli.py` writes into `[vep]` (#32, #95).
///
/// `extra_flags` (#108) is optional (absent means no extra flags); only its type
/// is checked here, the allowlist lives in `tools/bless/vep.py`.
const VEP_KEYS: &[Key] = &[
    ("image", Kind::Str, true),
    ("command", Kind::Str, true),
    ("date", Kind::Str, true),
    ("cache_source", Kind::Str, true),
    ("cache_checksum", Kind::Str, true),
    ("fasta_source", Kind::Str, true),
    ("fasta_checksum", Kind::Str, true),
    ("extra_flags", Kind::StrList, false),
];

const VEPYR_KEYS: &[Key] = &[
    ("flavour", Kind::Str, true),
    ("entities", Kind::StrList, true),
    ("required_contigs", Kind::StrList, true),
    ("everything", Kind::Bool, true),
    ("fields", Kind::StrList, true),
    ("preserve_record_layout", Kind::Bool, true),
    ("reference_fasta", Kind::Bool, true),
    ("buffer_size", Kind::Int, false),
];

const COMPARE_KEYS: &[Key] = &[
    ("body_md5", Kind::Str, true),
    ("known_divergence", Kind::Int, false),
];

/// Check `table` against `keys`: unknown keys, missing required keys, wrong types.
///
/// With `all_optional`, required keys may be absent (a `[[vepyr_run]]` entry
/// overrides any subset of `[vepyr]`).
#[track_caller]
fn check_table(name: &str, label: &str, table: &Table, keys: &[Key], all_optional: bool) {
    for (key, value) in table {
        let Some(&(_, kind, _)) = keys.iter().find(|(allowed, ..)| allowed == key) else {
            panic!("[{name}] unknown key: {key} (in {label} of {TOML_NAME})");
        };
        if !kind.matches(value) {
            panic!(
                "[{name}] {label} {key} must be {}, got {value}",
                kind.describe()
            );
        }
    }
    if !all_optional {
        for (key, _, required) in keys {
            if *required && !table.contains_key(*key) {
                panic!("[{name}] {label} is missing required key {key}");
            }
        }
    }
}

fn sub_table<'a>(doc: &'a Table, key: &str) -> &'a Table {
    doc[key].as_table().expect("checked by check_table")
}

fn str_list(value: &Value) -> Vec<String> {
    value
        .as_array()
        .expect("checked by check_table")
        .iter()
        .map(|item| item.as_str().expect("checked by check_table").to_owned())
        .collect()
}

// ---------------------------------------------------------------------------------
// Loader
// ---------------------------------------------------------------------------------

/// A fully-resolved `[vepyr]` table (after `[[vepyr_run]]` overrides).
#[derive(Clone, Debug, PartialEq, Eq)]
struct VepyrSettings {
    flavour: Flavour,
    entities: Vec<Entity>,
    required_contigs: Vec<String>,
    everything: bool,
    fields: Vec<String>,
    preserve_record_layout: bool,
    reference_fasta: bool,
    buffer_size: Option<usize>,
}

impl VepyrSettings {
    /// Parse a `[vepyr]` table already checked against [`VEPYR_KEYS`].
    #[track_caller]
    fn from_table(name: &str, table: &Table) -> Self {
        let flavour_key = table["flavour"].as_str().expect("checked");
        let flavour = Flavour::from_key(flavour_key).unwrap_or_else(|| {
            panic!("[{name}] [vepyr] flavour = {flavour_key:?} is not ensembl, refseq or merged")
        });
        let entities = str_list(&table["entities"])
            .iter()
            .map(|entity| {
                Entity::from_dir_name(entity).unwrap_or_else(|| {
                    panic!("[{name}] [vepyr] entities: {entity:?} is not a cache entity")
                })
            })
            .collect::<Vec<_>>();
        let required_contigs = str_list(&table["required_contigs"]);
        if entities.is_empty() || required_contigs.is_empty() {
            panic!("[{name}] [vepyr] entities and required_contigs must not be empty");
        }
        let fields = str_list(&table["fields"]);
        if fields.is_empty() {
            panic!("[{name}] [vepyr] fields must list the CSQ fields to emit");
        }
        let buffer_size = table.get("buffer_size").map(|value| {
            let size = value.as_integer().expect("checked");
            usize::try_from(size)
                .ok()
                .filter(|size| *size > 0)
                .unwrap_or_else(|| {
                    panic!("[{name}] [vepyr] buffer_size must be positive, got {size}")
                })
        });
        let flag = |key: &str| table[key].as_bool().expect("checked");
        Self {
            flavour,
            entities,
            required_contigs,
            everything: flag("everything"),
            fields,
            preserve_record_layout: flag("preserve_record_layout"),
            reference_fasta: flag("reference_fasta"),
            buffer_size,
        }
    }

    /// Hand-map onto the engine config. `fasta` is the resolved reference FASTA when
    /// `reference_fasta = true`.
    fn config(&self, fasta: Option<&Path>) -> AnnotateVcfConfig {
        let mut config = annotate_config! {
            everything: self.everything,
            fields: Some(self.fields.clone()),
            preserve_record_layout: self.preserve_record_layout,
            reference_fasta_path: fasta.map(|path| path.to_str().expect("utf-8 path").to_owned()),
        };
        if let Some(size) = self.buffer_size {
            config.buffer_size = size;
        }
        config
    }
}

/// One vepyr run: its `run n/N` label (only with `[[vepyr_run]]`) and settings.
#[derive(Debug)]
struct Run {
    overrides: Option<String>,
    settings: VepyrSettings,
}

/// A loaded, schema-checked data-test directory.
#[derive(Debug)]
struct TestDir {
    name: String,
    dir: PathBuf,
    runs: Vec<Run>,
    body_md5: String,
    known_divergence: Option<i64>,
}

impl TestDir {
    /// Load and validate `dir/test.toml`. Panics on any schema violation.
    #[track_caller]
    fn load(dir: &Path) -> Self {
        let name = dir
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or_else(|| panic!("{}: directory name is not UTF-8", dir.display()))
            .to_owned();
        let toml_path = dir.join(TOML_NAME);
        let text = std::fs::read_to_string(&toml_path).unwrap_or_else(|error| {
            panic!("[{name}] cannot read {}: {error}", toml_path.display())
        });
        let doc: Table = text
            .parse()
            .unwrap_or_else(|error| panic!("[{name}] {TOML_NAME} does not parse: {error}"));

        check_table(&name, "the top level", &doc, TOP_KEYS, false);
        for (key, keys) in [
            ("origin", ORIGIN_KEYS),
            ("input", INPUT_KEYS),
            ("vep", VEP_KEYS),
            ("vepyr", VEPYR_KEYS),
            ("compare", COMPARE_KEYS),
        ] {
            check_table(
                &name,
                &format!("[{key}]"),
                sub_table(&doc, key),
                keys,
                false,
            );
        }
        let entries: Vec<&Table> = doc
            .get("vepyr_run")
            .map(|runs| {
                runs.as_array()
                    .expect("checked")
                    .iter()
                    .map(|run| run.as_table().expect("checked"))
                    .collect()
            })
            .unwrap_or_default();
        for (index, entry) in entries.iter().enumerate() {
            let label = format!("[[vepyr_run]] #{}", index + 1);
            check_table(&name, &label, entry, VEPYR_KEYS, true);
        }

        let declared = doc["name"].as_str().expect("checked");
        if declared != name {
            panic!("[{name}] name = {declared:?} does not match the directory name");
        }
        let command = sub_table(&doc, "input")["command"]
            .as_str()
            .expect("checked");
        if command != NORMALIZE_COMMAND {
            panic!(
                "[{name}] [input] command = {command:?}; expected {NORMALIZE_COMMAND:?} \
                 (re-create input.vcf with tools/normalize_input)"
            );
        }
        let compare = sub_table(&doc, "compare");
        let body_md5 = compare["body_md5"].as_str().expect("checked").to_owned();
        let known_divergence = compare
            .get("known_divergence")
            .map(|value| value.as_integer().expect("checked"));

        let base = sub_table(&doc, "vepyr");
        let runs = if entries.is_empty() {
            vec![Run {
                overrides: None,
                settings: VepyrSettings::from_table(&name, base),
            }]
        } else {
            entries
                .iter()
                .map(|entry| {
                    let mut merged = base.clone();
                    merged.extend(
                        entry
                            .iter()
                            .map(|(key, value)| (key.clone(), value.clone())),
                    );
                    let overrides = entry
                        .iter()
                        .map(|(key, value)| format!("{key} = {value}"))
                        .collect::<Vec<_>>()
                        .join(", ");
                    Run {
                        overrides: Some(if overrides.is_empty() {
                            "(no overrides)".to_owned()
                        } else {
                            overrides
                        }),
                        settings: VepyrSettings::from_table(&name, &merged),
                    }
                })
                .collect()
        };
        Self {
            name,
            dir: dir.to_path_buf(),
            runs,
            body_md5,
            known_divergence,
        }
    }
}

/// Every subdirectory of `root`, sorted by name.
#[track_caller]
fn test_dirs(root: &Path) -> Vec<PathBuf> {
    let entries = std::fs::read_dir(root)
        .unwrap_or_else(|error| panic!("{ROOT_ENV}/test root {}: {error}", root.display()));
    let mut dirs: Vec<PathBuf> = entries
        .map(|entry| entry.expect("readable directory entry").path())
        .filter(|path| path.is_dir())
        .collect();
    dirs.sort();
    dirs
}

/// `$DATA_DIRS_ROOT`, or `default` under the crate root.
fn walk_root(default: &str) -> PathBuf {
    match std::env::var_os(ROOT_ENV) {
        Some(value) if !value.is_empty() => PathBuf::from(value),
        _ => Path::new(env!("CARGO_MANIFEST_DIR")).join(default),
    }
}

// ---------------------------------------------------------------------------------
// Compare
// ---------------------------------------------------------------------------------

/// Body lines of a VCF, line terminators kept: every line not starting with `#`.
fn body_lines(vcf: &[u8]) -> Vec<&[u8]> {
    vcf.split_inclusive(|byte| *byte == b'\n')
        .filter(|line| !line.starts_with(b"#"))
        .collect()
}

/// md5 of the body, as `grep -v '^#' FILE | md5sum` (and `./bless`) computes it.
fn body_md5(vcf: &[u8]) -> String {
    let mut digest = Md5::new();
    for line in body_lines(vcf) {
        digest.update(line);
    }
    format!("{:x}", digest.finalize())
}

/// The first body record where `expected` and `actual` differ, as `(VEP, vepyr)`.
fn first_difference(expected: &[u8], actual: &[u8]) -> (String, String) {
    let (vep, vepyr) = (body_lines(expected), body_lines(actual));
    let show = |line: Option<&&[u8]>| match line {
        Some(line) => {
            String::from_utf8_lossy(line.strip_suffix(b"\n").unwrap_or(line)).into_owned()
        }
        None => "<no record>".to_owned(),
    };
    (0..vep.len().max(vepyr.len()))
        .find(|&index| vep.get(index) != vepyr.get(index))
        .map(|index| (show(vep.get(index)), show(vepyr.get(index))))
        .unwrap_or_else(|| ("<bodies equal>".to_owned(), "<bodies equal>".to_owned()))
}

/// Where the cache comes from: `$VEPYR_CACHE_ROOT`, or an explicit (synthetic) root.
#[derive(Clone, Copy, Debug)]
enum CacheRoot<'a> {
    Env,
    At(&'a Path),
}

impl CacheRoot<'_> {
    #[track_caller]
    fn full_cache(self, flavour: Flavour) -> FullCache {
        match self {
            Self::Env => cache::full_cache(flavour),
            Self::At(root) => cache::full_cache_at(root, flavour),
        }
    }

    #[track_caller]
    fn reference_fasta(self) -> PathBuf {
        match self {
            Self::Env => cache::reference_fasta(),
            Self::At(root) => cache::reference_fasta_at(root),
        }
    }
}

/// Run every vepyr run of `test` and compare; print the contract messages to stdout.
///
/// Returns whether the directory passed.
async fn check_dir(test: &TestDir, cache_root: CacheRoot<'_>) -> bool {
    let name = &test.name;
    let oracle = std::fs::read(test.dir.join(ORACLE_NAME))
        .unwrap_or_else(|error| panic!("[{name}] cannot read {ORACLE_NAME}: {error}"));
    let oracle_md5 = body_md5(&oracle);
    if oracle_md5 != test.body_md5 {
        println!(
            "[{name}] oracle edited: the body md5 of {ORACLE_NAME} is {oracle_md5}, \
             [compare] body_md5 is {} (re-bless with ./bless)",
            test.body_md5
        );
        return false;
    }
    let input = std::fs::read_to_string(test.dir.join(INPUT_NAME))
        .unwrap_or_else(|error| panic!("[{name}] cannot read {INPUT_NAME}: {error}"));

    let total = test.runs.len();
    let mut passed = true;
    for (index, run) in test.runs.iter().enumerate() {
        if let Some(overrides) = &run.overrides {
            println!("run {}/{total}: {overrides}", index + 1);
        }
        let settings = &run.settings;
        let full = cache_root.full_cache(settings.flavour);
        let contigs: Vec<&str> = settings
            .required_contigs
            .iter()
            .map(String::as_str)
            .collect();
        cache::requires_shards(&full, &settings.entities, &contigs);
        let fasta = settings
            .reference_fasta
            .then(|| cache_root.reference_fasta());
        let config = settings.config(fasta.as_deref());

        let (written, output, _tmp) = annotate_vcf_at(&full.dir(), &input, &config).await;
        if let Err(error) = written {
            println!("[{name}] vepyr failed: {error}");
            passed = false;
            continue;
        }
        let got = body_md5(output.as_bytes());
        let matches = got == test.body_md5;
        match (matches, test.known_divergence) {
            (true, None) => {}
            (false, None) => {
                let (vep, vepyr) = first_difference(&oracle, output.as_bytes());
                let mut block = format!("[{name}] body md5 mismatch\n");
                let _ = writeln!(block, "expected {}, got {got}", test.body_md5);
                let _ = writeln!(block, "VEP: {vep}");
                let _ = write!(block, "vepyr: {vepyr}");
                println!("{block}");
                passed = false;
            }
            (true, Some(issue)) => {
                println!(
                    "[{name}] known_divergence obsolete: vepyr now matches VEP (body md5 {got}); \
                     remove known_divergence = {issue} from [compare] and close #{issue} if it is fixed"
                );
                passed = false;
            }
            (false, Some(issue)) => {
                println!(
                    "[{name}] known divergence #{issue} persists: expected {}, got {got}",
                    test.body_md5
                );
            }
        }
    }
    passed
}

/// Load and check every directory under `root`; panic naming the failed ones.
async fn run_all(root: &Path, cache_root: CacheRoot<'_>) {
    let dirs = test_dirs(root);
    let mut failed = Vec::new();
    for dir in &dirs {
        let test = TestDir::load(dir);
        if check_dir(&test, cache_root).await {
            println!("[{}] ok", test.name);
        } else {
            failed.push(test.name);
        }
    }
    println!(
        "data_dirs: {} directory(ies) under {}, {} failed",
        dirs.len(),
        root.display(),
        failed.len()
    );
    assert!(
        failed.is_empty(),
        "data_dirs: failing test directories: {}",
        failed.join(", ")
    );
}

// ---------------------------------------------------------------------------------
// Synthetic cache for the self-test
// ---------------------------------------------------------------------------------

/// The one contig the synthetic cache carries.
const SYNTHETIC_CONTIG: &str = "chr1";

/// Columns of a VEP 116 variation shard, in the engine's projected order
/// (`VARIATION_REQUIRED_COLUMNS` plus the derived `tier`).
const VARIATION_COLUMNS: &[&str] = &[
    "chrom",
    "start",
    "end",
    "allele_string",
    "failed",
    "variation_name",
    "clin_sig",
    "clin_sig_allele",
    "clinical_impact",
    "phenotype_or_disease",
    "pubmed",
    "somatic",
    "minor_allele",
    "minor_allele_freq",
    "AF",
    "AFR",
    "AMR",
    "EAS",
    "EUR",
    "SAS",
    "gnomADe",
    "gnomADe_AFR",
    "gnomADe_AMR",
    "gnomADe_ASJ",
    "gnomADe_EAS",
    "gnomADe_FIN",
    "gnomADe_NFE",
    "gnomADe_SAS",
    "gnomADe_MID",
    "gnomADe_REMAINING",
    "gnomADg",
    "gnomADg_AFR",
    "gnomADg_AMI",
    "gnomADg_AMR",
    "gnomADg_ASJ",
    "gnomADg_EAS",
    "gnomADg_FIN",
    "gnomADg_MID",
    "gnomADg_NFE",
    "gnomADg_SAS",
    "gnomADg_REMAINING",
    "clinvar_ids",
    "cosmic_ids",
    "dbsnp_ids",
    "tier",
];

/// Write a synthetic `$VEPYR_CACHE_ROOT` under `root`: `PROVENANCE.json` at the
/// `PINS.toml` ensembl revision and one `variation/chr1.parquet` shard holding a
/// single known variant at chr1:900, far from the fixture's loci. No transcripts, so
/// every fixture variant is annotated `intergenic_variant`.
fn write_synthetic_cache(root: &Path) {
    use arrow_array::{ArrayRef, RecordBatch, StringArray, UInt32Array, new_null_array};
    use arrow_schema::{DataType, Field, Schema};

    let flavour = Flavour::Ensembl;
    let revision = cache::pinned_revision(flavour);
    std::fs::write(
        root.join("PROVENANCE.json"),
        format!(
            "{{\"datasets\": {{\"{}\": {{\"revision\": \"{revision}\"}}}}}}\n",
            flavour.key()
        ),
    )
    .expect("write PROVENANCE.json");

    let variation = root
        .join(flavour.dir_name())
        .join(Entity::Variation.dir_name());
    std::fs::create_dir_all(&variation).expect("create variation dir");
    let fields: Vec<Field> = VARIATION_COLUMNS
        .iter()
        .map(|&column| {
            let kind = match column {
                "start" | "end" => DataType::UInt32,
                "failed" | "tier" => DataType::Int8,
                _ => DataType::Utf8,
            };
            Field::new(column, kind, true)
        })
        .collect();
    let metadata = [
        (
            "bio.vep.cache_source_type".to_owned(),
            flavour.key().to_owned(),
        ),
        ("bio.vep.cache_version".to_owned(), "116".to_owned()),
    ];
    let schema = Arc::new(Schema::new_with_metadata(fields, metadata.into()));
    let columns: Vec<ArrayRef> = schema
        .fields()
        .iter()
        .map(|field| -> ArrayRef {
            match field.name().as_str() {
                "chrom" => Arc::new(StringArray::from(vec![SYNTHETIC_CONTIG])),
                "start" | "end" => Arc::new(UInt32Array::from(vec![900_u32])),
                "allele_string" => Arc::new(StringArray::from(vec!["A/T"])),
                "variation_name" => Arc::new(StringArray::from(vec!["rs_synthetic"])),
                _ => new_null_array(field.data_type(), 1),
            }
        })
        .collect();
    let batch = RecordBatch::try_new(Arc::clone(&schema), columns).expect("synthetic batch");
    let shard = format!("{SYNTHETIC_CONTIG}.parquet");
    let file = std::fs::File::create(variation.join(&shard)).expect("create shard");
    let mut writer =
        parquet::arrow::ArrowWriter::try_new(file, schema, None).expect("parquet writer");
    writer.write(&batch).expect("write shard");
    writer.close().expect("close shard");
    std::fs::write(
        variation.join("chrom_manifest.json"),
        format!("[{{\"chrom\": \"{SYNTHETIC_CONTIG}\", \"dataset\": \"{shard}\", \"rows\": 1}}]\n"),
    )
    .expect("write chrom_manifest.json");
}

// ---------------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------------

/// Every `tests/data/<name>/` against the Hub cache at `$VEPYR_CACHE_ROOT`.
#[tokio::test]
async fn data_dirs() {
    let root = walk_root("tests/data");
    if !root.is_dir() {
        println!(
            "data_dirs: no test root at {} — nothing to run",
            root.display()
        );
        return;
    }
    run_all(&root, CacheRoot::Env).await;
}

/// The loader and compare, end to end, on `tests/fixtures/data_dirs_selftest/`
/// against a synthetic cache — no downloaded data needed.
#[tokio::test]
async fn selftest() {
    let root = walk_root("tests/fixtures/data_dirs_selftest");
    let cache_dir = tempfile::TempDir::new().expect("tempdir");
    write_synthetic_cache(cache_dir.path());
    run_all(&root, CacheRoot::At(cache_dir.path())).await;
}

#[test]
fn body_md5_hashes_only_non_header_lines_with_terminators() {
    let vcf = b"##h\n#CHROM\na\tb\nc\n";
    let mut digest = Md5::new();
    digest.update(b"a\tb\nc\n");
    assert_eq!(body_md5(vcf), format!("{:x}", digest.finalize()));
    assert_eq!(
        first_difference(vcf, b"#x\na\tb\nd\n"),
        ("c".to_owned(), "d".to_owned())
    );
    assert_eq!(
        first_difference(vcf, b"a\tb\n"),
        ("c".to_owned(), "<no record>".to_owned())
    );
}

/// Load a copy of the self-test fixture with `line` added to its `[vep]` table.
fn load_fixture_with_vep_line(line: &str) -> TestDir {
    let fixture =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/data_dirs_selftest/case");
    let scratch = tempfile::TempDir::new().expect("tempdir");
    let dir = scratch.path().join("case");
    std::fs::create_dir(&dir).expect("create case dir");
    let text = std::fs::read_to_string(fixture.join(TOML_NAME)).expect("read fixture");
    let edited = text.replacen("[vep]\n", &format!("[vep]\n{line}\n"), 1);
    assert_ne!(edited, text, "fixture has no [vep] table");
    std::fs::write(dir.join(TOML_NAME), edited).expect("write test.toml");
    TestDir::load(&dir)
}

#[test]
fn vep_extra_flags_accepted() {
    let test = load_fixture_with_vep_line(r#"extra_flags = ["--check_existing"]"#);
    assert_eq!(test.name, "case");
    let test = load_fixture_with_vep_line("extra_flags = []");
    assert_eq!(test.name, "case");
}

#[test]
#[should_panic(expected = "[vep] extra_flags must be")]
fn vep_extra_flags_wrong_type_is_rejected() {
    let not_array = std::panic::catch_unwind(|| {
        load_fixture_with_vep_line(r#"extra_flags = "--check_existing""#)
    });
    assert!(not_array.is_err(), "a string extra_flags was accepted");
    load_fixture_with_vep_line("extra_flags = [1]");
}
