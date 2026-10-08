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
//! | `[vepyr]` | `flavour`, `required_contigs`, `everything`, `preserve_record_layout`, `reference_fasta`, `buffer_size?` |
//! | `[compare]` | `body_md5` |
//! | `[[vepyr_run]]?` | any `[vepyr]` key, overriding it for that run |
//!
//! `[vepyr]` is mapped by hand onto `AnnotateVcfConfig` (it has no serde):
//! `everything`, `preserve_record_layout` and `buffer_size` are config fields;
//! `fields` stays `None` (the full `--everything` CSQ layout); `reference_fasta = true`
//! sets `reference_fasta_path` to `cache::reference_fasta()`. `flavour` picks the
//! cache directory (`116_GRCh38_<flavour>`), never a config flag; `required_contigs`
//! goes to `cache::requires_shards`, which, for the Hub root only, checks per contig
//! the entities derived by `Entity::read_under_everything` (the selftest's synthetic
//! cache is not held to it). A `test.toml` does not declare entities: a leftover
//! entities key is rejected as an unknown key.
//!
//! # One mode: `--everything` (#143)
//!
//! `tools/vep_flags.toml` maps each flag of the fixed VEP command onto the `[vepyr]`
//! value that reproduces it. The loader panics with `[<name>] unsupported mode` when
//! a run's `[vepyr]` value differs from it (e.g. `everything = false`) or when
//! `[vep] command` lacks one of its VEP flags (an oracle made by the old command).
//!
//! # Failure messages (a fixed contract — other issues' ACs grep them)
//!
//! - `[<name>] oracle edited` — `[compare] body_md5` is not the md5 of the body of
//!   `expected_output.vcf`;
//! - `[<name>] unknown key: <key>` — panic while loading `test.toml`;
//! - `[<name>] origin.<key> is not a commit-pinned permalink: <value>` — panic while
//!   loading `test.toml` when `[origin] vep_test_pinned` or `vep_subject` is not
//!   `https://github.com/<owner>/<repo>/blob/<40 lowercase hex>/<path>` (#35);
//! - `[<name>] body md5 mismatch`, then `expected <md5>, got <md5>`, then the first
//!   differing record on a line starting `VEP:` and one starting `vepyr:`;
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
/// The VEP flag -> `[vepyr]` mapping of the one data-test mode (#143), shared with
/// `tools/bless`.
const MODE_MAPPING: &str = include_str!("../tools/vep_flags.toml");

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

/// `[origin]` keys that must be commit-pinned GitHub permalinks (#35).
///
/// `vep_test` is the readable tag link and `ledger` is not a URL, so neither is here.
const PINNED_ORIGIN_KEYS: &[&str] = &["vep_test_pinned", "vep_subject"];

/// Whether `url` is `https://github.com/<owner>/<repo>/blob/<40 lowercase hex>/<path>`.
///
/// `<path>` is non-empty and may end in a `#L..` anchor. A branch, a tag or an
/// abbreviated hash in place of the commit fails, so the link cannot drift.
fn is_commit_permalink(url: &str) -> bool {
    let Some(rest) = url.strip_prefix("https://github.com/") else {
        return false;
    };
    let mut parts = rest.splitn(5, '/');
    let (Some(owner), Some(repo), Some("blob"), Some(commit), Some(path)) = (
        parts.next(),
        parts.next(),
        parts.next(),
        parts.next(),
        parts.next(),
    ) else {
        return false;
    };
    let segment_ok = |part: &str| !part.is_empty() && !part.contains(char::is_whitespace);
    segment_ok(owner)
        && segment_ok(repo)
        && segment_ok(path)
        && commit.len() == 40
        && commit
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
}

/// Panic unless every [`PINNED_ORIGIN_KEYS`] value of `origin` is a commit permalink.
#[track_caller]
fn require_pinned_origin(name: &str, origin: &Table) {
    for key in PINNED_ORIGIN_KEYS {
        let value = origin[*key].as_str().expect("checked by check_table");
        if !is_commit_permalink(value) {
            panic!("[{name}] origin.{key} is not a commit-pinned permalink: {value}");
        }
    }
}

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
    ("required_contigs", Kind::StrList, true),
    ("everything", Kind::Bool, true),
    ("preserve_record_layout", Kind::Bool, true),
    ("reference_fasta", Kind::Bool, true),
    ("buffer_size", Kind::Int, false),
];

const COMPARE_KEYS: &[Key] = &[("body_md5", Kind::Str, true)];

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

/// One `[[mapping]]` row of `tools/vep_flags.toml`.
#[derive(Clone, Debug, PartialEq, Eq)]
struct ModeRow {
    vep_flag: String,
    vepyr_key: String,
    vepyr_value: bool,
}

/// Parse [`MODE_MAPPING`]; panics if the file does not have the expected shape.
fn mode_mapping() -> Vec<ModeRow> {
    let doc: Table = MODE_MAPPING
        .parse()
        .unwrap_or_else(|error| panic!("tools/vep_flags.toml does not parse: {error}"));
    let rows = doc
        .get("mapping")
        .and_then(Value::as_array)
        .filter(|rows| !rows.is_empty())
        .expect("tools/vep_flags.toml: [[mapping]] must list at least one row");
    rows.iter()
        .map(|row| {
            let field = |key: &str| {
                row.get(key)
                    .unwrap_or_else(|| panic!("tools/vep_flags.toml: a row lacks {key}"))
            };
            ModeRow {
                vep_flag: field("vep_flag")
                    .as_str()
                    .expect("vep_flag string")
                    .to_owned(),
                vepyr_key: field("vepyr_key")
                    .as_str()
                    .expect("vepyr_key string")
                    .to_owned(),
                vepyr_value: field("vepyr_value").as_bool().expect("vepyr_value bool"),
            }
        })
        .collect()
}

/// Panic unless `table` (a resolved `[vepyr]`) holds every mapped value.
#[track_caller]
fn require_mode(name: &str, table: &Table) {
    for row in mode_mapping() {
        let found = table[row.vepyr_key.as_str()].as_bool().expect("checked");
        if found != row.vepyr_value {
            panic!(
                "[{name}] unsupported mode: [vepyr] {} = {found}; the only data-test mode is \
                 VEP --everything, where VEP {} maps to {} = {} (tools/vep_flags.toml, \
                 README \"One mode: --everything\")",
                row.vepyr_key, row.vep_flag, row.vepyr_key, row.vepyr_value
            );
        }
    }
}

/// Panic unless the recorded `[vep] command` carries every mapped VEP flag.
#[track_caller]
fn require_mode_command(name: &str, command: &str) {
    for row in mode_mapping() {
        if !command
            .split_whitespace()
            .any(|token| token == row.vep_flag)
        {
            panic!(
                "[{name}] unsupported mode: [vep] command lacks {}; re-bless with ./bless \
                 (tools/vep_flags.toml, README \"One mode: --everything\")",
                row.vep_flag
            );
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
    required_contigs: Vec<String>,
    everything: bool,
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
        if matches!(flavour, Flavour::RefSeq) {
            panic!(
                "[{name}] [vepyr] flavour = {flavour_key:?}: the oracle is always VEP on the \
                 Ensembl or merged cache; refseq-only oracles are not supported"
            );
        }
        let required_contigs = str_list(&table["required_contigs"]);
        if required_contigs.is_empty() {
            panic!("[{name}] [vepyr] required_contigs must not be empty");
        }
        require_mode(name, table);
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
            required_contigs,
            everything: flag("everything"),
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
            fields: None,
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

        require_pinned_origin(&name, sub_table(&doc, "origin"));

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
        let vep_command = sub_table(&doc, "vep")["command"].as_str().expect("checked");
        require_mode_command(&name, vep_command);
        let compare = sub_table(&doc, "compare");
        let body_md5 = compare["body_md5"].as_str().expect("checked").to_owned();

        let base = sub_table(&doc, "vepyr");
        let runs: Vec<Run> = if entries.is_empty() {
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
        let oracle_merged = vep_command
            .split_whitespace()
            .any(|flag| flag == "--merged");
        for run in &runs {
            assert_eq!(
                matches!(run.settings.flavour, Flavour::Merged),
                oracle_merged,
                "[{name}] cache flavour mismatch between VEP command and vepyr"
            );
        }
        Self {
            name,
            dir: dir.to_path_buf(),
            runs,
            body_md5,
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
        if matches!(cache_root, CacheRoot::Env) {
            cache::requires_shards(&full, &contigs, Entity::read_under_everything);
        }
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
        if got != test.body_md5 {
            let (vep, vepyr) = first_difference(&oracle, output.as_bytes());
            let mut block = format!("[{name}] body md5 mismatch\n");
            let _ = writeln!(block, "expected {}, got {got}", test.body_md5);
            let _ = writeln!(block, "VEP: {vep}");
            let _ = write!(block, "vepyr: {vepyr}");
            println!("{block}");
            passed = false;
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

/// Length of the synthetic reference contig (the fixture's `##contig` length).
const SYNTHETIC_LENGTH: usize = 1000;

/// The synthetic reference base at 1-based `pos`: `A` everywhere except the `REF`
/// bases of the self-test fixture's other loci (`C` at 200, `G` at 300).
fn synthetic_base(pos: usize) -> u8 {
    match pos {
        200 => b'C',
        300 => b'G',
        _ => b'A',
    }
}

/// Write the synthetic reference FASTA (one line) and its `.fai` where
/// `cache::reference_fasta_at(root)` looks for them, so the self-test runs under
/// `reference_fasta = true` (#143) with no downloaded data.
fn write_synthetic_fasta(root: &Path) {
    let fasta = root.join(cache::FASTA);
    std::fs::create_dir_all(fasta.parent().expect("FASTA has a parent")).expect("fasta dir");
    let header = format!(">{SYNTHETIC_CONTIG}\n");
    let bases: Vec<u8> = (1..=SYNTHETIC_LENGTH).map(synthetic_base).collect();
    let mut text = header.clone().into_bytes();
    text.extend_from_slice(&bases);
    text.push(b'\n');
    std::fs::write(&fasta, text).expect("write synthetic FASTA");
    let fai = format!(
        "{SYNTHETIC_CONTIG}\t{SYNTHETIC_LENGTH}\t{}\t{SYNTHETIC_LENGTH}\t{}\n",
        header.len(),
        SYNTHETIC_LENGTH + 1
    );
    std::fs::write(fasta.with_extension("fa.fai"), fai).expect("write synthetic .fai");
}

/// Write a synthetic `$VEPYR_CACHE_ROOT` under `root`: `PROVENANCE.json` at the
/// `PINS.toml` ensembl revision, one `variation/chr1.parquet` shard holding a
/// single known variant at chr1:900, far from the fixture's loci, and a synthetic
/// reference FASTA ([`write_synthetic_fasta`]). No transcripts, so every fixture
/// variant is annotated `intergenic_variant`.
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
    write_synthetic_fasta(root);
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

/// Load a copy of the self-test fixture's `test.toml` with `from` replaced by `to`
/// (once; `from` must occur).
fn load_fixture_edited(from: &str, to: &str) -> TestDir {
    let fixture =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/data_dirs_selftest/case");
    let scratch = tempfile::TempDir::new().expect("tempdir");
    let dir = scratch.path().join("case");
    std::fs::create_dir(&dir).expect("create case dir");
    let text = std::fs::read_to_string(fixture.join(TOML_NAME)).expect("read fixture");
    let edited = text.replacen(from, to, 1);
    assert_ne!(edited, text, "fixture has no {from:?}");
    std::fs::write(dir.join(TOML_NAME), edited).expect("write test.toml");
    TestDir::load(&dir)
}

/// Load a copy of the self-test fixture with `line` added to its `[vep]` table.
fn load_fixture_with_vep_line(line: &str) -> TestDir {
    load_fixture_edited("[vep]\n", &format!("[vep]\n{line}\n"))
}

#[test]
fn mode_mapping_names_everything_and_fasta() {
    let flags: Vec<String> = mode_mapping().into_iter().map(|row| row.vep_flag).collect();
    assert!(flags.contains(&"--everything".to_owned()), "{flags:?}");
    assert!(flags.contains(&"--fasta".to_owned()), "{flags:?}");
}

#[test]
#[should_panic(expected = "unsupported mode: [vepyr] everything = false")]
fn everything_false_is_rejected() {
    load_fixture_edited("everything = true", "everything = false");
}

#[test]
#[should_panic(expected = "unsupported mode: [vepyr] reference_fasta = false")]
fn reference_fasta_false_is_rejected() {
    load_fixture_edited("reference_fasta = true", "reference_fasta = false");
}

#[test]
#[should_panic(expected = "unsupported mode: [vepyr] everything = false")]
fn vepyr_run_override_to_old_mode_is_rejected() {
    load_fixture_edited("[vep]\n", "[[vepyr_run]]\neverything = false\n\n[vep]\n");
}

#[test]
#[should_panic(expected = "unsupported mode: [vep] command lacks --everything")]
fn old_vep_command_is_rejected() {
    load_fixture_edited(" --everything", "");
}

#[test]
#[should_panic(expected = "refseq-only oracles are not supported")]
fn refseq_flavour_is_rejected() {
    load_fixture_edited("flavour = \"ensembl\"", "flavour = \"refseq\"");
}

#[test]
#[should_panic(expected = "cache flavour mismatch")]
fn merged_flavour_requires_matching_oracle() {
    load_fixture_edited("flavour = \"ensembl\"", "flavour = \"merged\"");
}

#[test]
fn merged_flavour_accepts_matching_oracle() {
    let original = include_str!("fixtures/data_dirs_selftest/case/test.toml");
    let edited = original
        .replace("flavour = \"ensembl\"", "flavour = \"merged\"")
        .replace(" --everything", " --everything --merged");
    let test = load_fixture_edited(original, &edited);
    assert_eq!(test.name, "case");
}

#[test]
#[should_panic(expected = "cache flavour mismatch")]
fn merged_oracle_rejects_ensembl_run_override() {
    let original = include_str!("fixtures/data_dirs_selftest/case/test.toml");
    let edited = original
        .replace("flavour = \"ensembl\"", "flavour = \"merged\"")
        .replace(" --everything", " --everything --merged")
        .replace(
            "\n[compare]\n",
            "\n[[vepyr_run]]\nflavour = \"ensembl\"\n\n[compare]\n",
        );
    load_fixture_edited(original, &edited);
}

#[test]
#[should_panic(expected = "refseq-only oracles are not supported")]
fn vepyr_run_flavour_override_is_rejected() {
    load_fixture_edited("[vep]\n", "[[vepyr_run]]\nflavour = \"refseq\"\n\n[vep]\n");
}

#[test]
fn ensembl_flavour_is_accepted() {
    // A whitespace-only edit: `load_fixture_edited` requires the text to change.
    load_fixture_edited("flavour = \"ensembl\"", "flavour  = \"ensembl\"");
}

#[test]
#[should_panic(expected = "unknown key: fields")]
fn fields_key_is_rejected() {
    load_fixture_edited("[vepyr]\n", "[vepyr]\nfields = [\"Allele\"]\n");
}

#[test]
#[should_panic(expected = "unknown key: entities")]
fn entities_key_is_rejected() {
    load_fixture_edited("[vepyr]\n", "[vepyr]\nentities = [\"variation\"]\n");
}

#[test]
fn derived_entities_for_chr21_are_all_seven() {
    let names: Vec<&str> = Entity::read_under_everything("chr21")
        .into_iter()
        .map(Entity::dir_name)
        .collect();
    assert_eq!(
        names,
        [
            "exon",
            "motif",
            "regulatory",
            "transcript",
            "translation_core",
            "translation_sift",
            "variation"
        ]
    );
}

#[test]
fn derived_entities_for_chrmt_omit_motif_and_regulatory() {
    let names: Vec<&str> = Entity::read_under_everything("chrMT")
        .into_iter()
        .map(Entity::dir_name)
        .collect();
    assert_eq!(
        names,
        [
            "exon",
            "transcript",
            "translation_core",
            "translation_sift",
            "variation"
        ]
    );
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

#[test]
fn commit_permalink_shape() {
    let commit = "57ea5c52340acc1f156267f810ad162e26597082";
    for good in [
        format!("https://github.com/Ensembl/ensembl-vep/blob/{commit}/t/Runner.t#L244-L292"),
        format!("https://github.com/Ensembl/ensembl-vep/blob/{commit}/modules/X.pm"),
    ] {
        assert!(is_commit_permalink(&good), "{good}");
    }
    for bad in [
        "https://github.com/Ensembl/ensembl-vep/blob/master/t/Runner.t#L1".to_owned(),
        "https://github.com/Ensembl/ensembl-vep/blob/release/116.0/t/Runner.t".to_owned(),
        "https://github.com/Ensembl/ensembl-vep/blob/57ea5c52/t/Runner.t".to_owned(),
        format!(
            "https://github.com/Ensembl/ensembl-vep/blob/{}/t/R.t",
            commit.to_uppercase()
        ),
        format!("https://github.com/Ensembl/ensembl-vep/tree/{commit}/t/Runner.t"),
        format!("https://github.com/Ensembl/ensembl-vep/blob/{commit}/"),
        format!("http://github.com/Ensembl/ensembl-vep/blob/{commit}/t/Runner.t"),
        format!(".../blob/{commit}/t/Runner.t"),
    ] {
        assert!(!is_commit_permalink(&bad), "{bad}");
    }
}

#[test]
#[should_panic(expected = "[t] origin.vep_subject is not a commit-pinned permalink: \
                           https://github.com/Ensembl/ensembl-vep/blob/master/m.pm")]
fn unpinned_origin_rejected() {
    let origin: Table = format!(
        "vep_test_pinned = \"https://github.com/o/r/blob/{}/t/a.t\"\n\
         vep_subject = \"https://github.com/Ensembl/ensembl-vep/blob/master/m.pm\"\n",
        "a".repeat(40)
    )
    .parse()
    .expect("toml");
    require_pinned_origin("t", &origin);
}
