//! The generic data-test runner: one directory per test, VEP 116.2 output as oracle.
//!
//! A data-test is a directory `tests/data/<name>/` holding
//!
//! - `input.vcf` — the normalised input, written by `tools/normalize_input` (#85);
//!   VEP and vepyr both read exactly these bytes;
//! - `expected_output.vcf` — the real output of native Ensembl VEP 116.2 on that input,
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
//! | top level | `name` (= directory name), `description`, `skip_reason?` |
//! | `[origin]`? | `vep_test` |
//! | `[[tests]]`? | `id`, `description`, `vep_test`, `focus?` |
//! | `[input]` | `command` (the #85 command), `bcftools_version` |
//! | `[vep]` | `image`, `command`, `date`, `cache_source`, `vep_cache`, `vep_cache_checksum`, `fasta_source`, `fasta_checksum` — exactly what `./bless` writes |
//! | `[vepyr]` | `flavour`, `required_contigs`, `everything`, `preserve_record_layout`, `reference_fasta`, `buffer_size?` |
//! | `[compare]` | `body_md5` |
//! | `[[vepyr_run]]?` | any `[vepyr]` key, overriding it for that run |
//!
//! # One directory per distinct (input, oracle) (#238)
//!
//! A fixture holds one comparison and one or more named tests in `[[tests]]`.
//! External single-test fixtures may use `description` + `[origin]` instead;
//! exactly one of the two forms is present. Test `id`s are unique within
//! the fixture and one of them is the directory name, so every former directory
//! name survives as a test id. `focus` (optional) records the selector of
//! `tools/port_campaign.py` `focus_value` (`kind` + `field`/`where`/`column`/`key`/
//! `ordered`); it is metadata only: the runner still compares the whole body.
//! `tools/check_unique_dirs` fails when two directories share (input body, oracle
//! body, `[vepyr]`, `[[vepyr_run]]`, `[vep] command`).
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
//! - An invalid `vep_test` release-tag URL or non-HTTP(S) source fails at load time;
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

use std::collections::HashMap;
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
/// The VEP software pin (#239), shared with `tools/vep_pin.py`; the self-tests
/// read the upstream tag and commit from it instead of spelling them.
#[cfg(test)]
const VEP_PIN: &str = include_str!("../tools/vep_pin.toml");

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
    ("skip_reason", Kind::Str, false),
    ("origin", Kind::Table, false),
    ("tests", Kind::TableList, false),
    ("input", Kind::Table, true),
    ("vep", Kind::Table, true),
    ("vepyr", Kind::Table, true),
    ("compare", Kind::Table, true),
    ("vepyr_run", Kind::TableList, false),
];

const ORIGIN_KEYS: &[Key] = &[("vep_test", Kind::Str, true)];

/// Keys of one `[[tests]]` table (#238): an `[origin]` plus its own id and
/// description, the upstream assertions it stands for, and an optional selector.
const TEST_KEYS: &[Key] = &[
    ("id", Kind::Str, true),
    ("description", Kind::Str, true),
    ("vep_test", Kind::Str, true),
    ("focus", Kind::Table, false),
];

/// Keys of a `[[tests]]` `focus` table: the selector of `tools/port_campaign.py`
/// `focus_value`.
const FOCUS_KEYS: &[Key] = &[
    ("kind", Kind::Str, true),
    ("field", Kind::Str, false),
    ("where", Kind::Table, false),
    ("column", Kind::Int, false),
    ("key", Kind::Str, false),
    ("ordered", Kind::Bool, false),
];

/// The `focus` kinds `tools/port_campaign.py` `focus_value` implements.
const FOCUS_KINDS: &[&str] = &["csq", "csq_values", "column", "info", "record_count"];

/// Upstream test links use the readable Ensembl release tag.
const PINNED_ORIGIN_KEYS: &[&str] = &["vep_test"];

/// A tagged Ensembl VEP test file, optionally with a line anchor.
fn is_tagged_test_url(url: &str) -> bool {
    let Some(rest) = url.strip_prefix("https://github.com/Ensembl/ensembl-vep/blob/release/")
    else {
        return false;
    };
    let Some((version, path)) = rest.split_once("/t/") else {
        return false;
    };
    let components: Vec<_> = version.split('.').collect();
    let file = path.split('#').next().unwrap_or("");
    components.len() == 2
        && components
            .iter()
            .all(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()))
        && file.ends_with(".t")
        && file.len() > 2
        && !path.contains('/')
        && !url.contains(char::is_whitespace)
}

fn is_download_url(url: &str) -> bool {
    let Some(rest) = url
        .strip_prefix("https://")
        .or_else(|| url.strip_prefix("http://"))
    else {
        return false;
    };
    let host = rest.split(['/', '?', '#']).next().unwrap_or("");
    !host.is_empty() && !host.contains('@') && !url.contains(char::is_whitespace)
}

/// Panic unless every [`PINNED_ORIGIN_KEYS`] value of `origin` is a tagged test URL.
#[track_caller]
fn require_pinned_origin(name: &str, origin: &Table) {
    require_pinned(name, "origin", origin);
}

/// Panic unless every [`PINNED_ORIGIN_KEYS`] value of `table` is a tagged test URL;
/// `label` names the table in the message (`origin` or `test <id>`).
#[track_caller]
fn require_pinned(name: &str, label: &str, table: &Table) {
    for key in PINNED_ORIGIN_KEYS {
        let value = table[*key].as_str().expect("checked by check_table");
        if !is_tagged_test_url(value) {
            panic!("[{name}] {label}.{key} is not a tagged Ensembl VEP test URL: {value}");
        }
    }
}

/// Validate the provenance of one directory and return its test ids (#238).
///
/// Exactly one of `[origin]` and a non-empty `[[tests]]` list is present. With
/// `[origin]` the one test id is `name`. With `[[tests]]` every table is
/// checked against [`TEST_KEYS`] (and its `focus` against [`FOCUS_KEYS`]), its
/// links must use release tags, ids are unique and one of them is `name`.
#[track_caller]
fn check_tests(name: &str, doc: &Table) -> Vec<String> {
    let tests: Vec<&Table> = doc
        .get("tests")
        .map(|list| {
            list.as_array()
                .expect("checked")
                .iter()
                .map(|item| item.as_table().expect("checked"))
                .collect()
        })
        .unwrap_or_default();
    match (doc.get("origin"), tests.is_empty()) {
        (Some(_), false) => {
            panic!("[{name}] has both [origin] and [[tests]]; keep exactly one (#238)")
        }
        (None, true) => panic!("[{name}] needs [origin] or at least one [[tests]] (#238)"),
        (Some(origin), true) => {
            let origin = origin.as_table().expect("checked");
            check_table(name, "[origin]", origin, ORIGIN_KEYS, false);
            require_pinned_origin(name, origin);
            return vec![name.to_owned()];
        }
        (None, false) => {}
    }
    let mut ids: Vec<String> = Vec::with_capacity(tests.len());
    for (index, test) in tests.iter().enumerate() {
        check_table(
            name,
            &format!("[[tests]] #{}", index + 1),
            test,
            TEST_KEYS,
            false,
        );
        let id = test["id"].as_str().expect("checked").to_owned();
        if ids.contains(&id) {
            panic!("[{name}] duplicate [[tests]] id: {id}");
        }
        require_pinned(name, &format!("test {id}"), test);
        if let Some(focus) = test.get("focus") {
            check_focus(name, &id, focus.as_table().expect("checked"));
        }
        ids.push(id);
    }
    if !ids.iter().any(|id| id == name) {
        panic!("[{name}] no [[tests]] has id = the directory name (#238)");
    }
    ids
}

/// Check one `[[tests]]` `focus` table: keys, a known `kind`, string `where`.
#[track_caller]
fn check_focus(name: &str, id: &str, focus: &Table) {
    let label = format!("[[tests]] {id} focus");
    check_table(name, &label, focus, FOCUS_KEYS, false);
    let kind = focus["kind"].as_str().expect("checked");
    if !FOCUS_KINDS.contains(&kind) {
        panic!("[{name}] {label} kind = {kind:?} is not one of {FOCUS_KINDS:?}");
    }
    if let Some(selector) = focus.get("where") {
        let selector = selector.as_table().expect("checked");
        if let Some((key, value)) = selector.iter().find(|(_, value)| !value.is_str()) {
            panic!("[{name}] {label} where.{key} must be a string, got {value}");
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
    ("vep_cache", Kind::Str, true),
    ("vep_cache_checksum", Kind::Str, true),
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
    skip_reason: Option<String>,
    dir: PathBuf,
    runs: Vec<Run>,
    body_md5: String,
    /// The test ids this directory's one comparison covers (#238).
    tests: Vec<String>,
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
        let skip_reason = doc.get("skip_reason").map(|value| {
            let reason = value.as_str().expect("checked");
            assert!(
                !reason.trim().is_empty(),
                "[{name}] skip_reason must be a non-empty string"
            );
            reason.to_owned()
        });
        for (key, keys) in [
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
        for key in ["cache_source", "vep_cache", "fasta_source"] {
            let value = sub_table(&doc, "vep")[key].as_str().expect("checked");
            if !is_download_url(value) {
                panic!("[{name}] [vep] {key} must be an HTTP(S) URL: {value}");
            }
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

        let tests = check_tests(&name, &doc);

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
            skip_reason,
            dir: dir.to_path_buf(),
            runs,
            body_md5,
            tests,
        }
    }
}

/// NamedTest ids are unique across directories, not only within one (#238).
#[track_caller]
fn check_unique_test_ids(tests: &[TestDir]) {
    let mut owners: HashMap<&str, &str> = HashMap::new();
    for test in tests {
        for id in &test.tests {
            if let Some(owner) = owners.insert(id, &test.name) {
                panic!(
                    "test id {id} is declared by both [{owner}] and [{}]",
                    test.name
                );
            }
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

#[derive(Debug, PartialEq, Eq)]
enum Outcome {
    Passed,
    Failed,
    Skipped,
}

/// Validate fixture files, then skip or compare every configured vepyr run.
async fn check_dir(test: &TestDir, cache_root: CacheRoot<'_>) -> Outcome {
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
        return Outcome::Failed;
    }
    let input = std::fs::read_to_string(test.dir.join(INPUT_NAME))
        .unwrap_or_else(|error| panic!("[{name}] cannot read {INPUT_NAME}: {error}"));
    if let Some(reason) = &test.skip_reason {
        println!("SKIP {name}: {reason}");
        return Outcome::Skipped;
    }

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
    if passed {
        Outcome::Passed
    } else {
        Outcome::Failed
    }
}

/// Load and check every directory under `root`; panic naming the failed ones.
async fn run_all(root: &Path, cache_root: CacheRoot<'_>) {
    let dirs = test_dirs(root);
    let mut failed = Vec::new();
    let mut tests = 0;
    let mut passed = 0;
    let mut skipped = 0;
    let mut skipped_tests = 0;
    let mut skipped_runs = 0;
    for dir in &dirs {
        let test = TestDir::load(dir);
        tests += test.tests.len();
        match check_dir(&test, cache_root).await {
            Outcome::Passed => {
                passed += 1;
                println!("[{}] ok", test.name);
            }
            Outcome::Failed => failed.push(test.name),
            Outcome::Skipped => {
                skipped += 1;
                skipped_tests += test.tests.len();
                skipped_runs += test.runs.len();
            }
        }
    }
    println!(
        "data_dirs: {} directory(ies) ({tests} named tests) under {}, \
         {passed} passed, {} failed, {skipped} skipped \
         ({skipped_tests} named tests, {skipped_runs} runs skipped)",
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

/// Columns of a VEP cache 116 variation shard, in the engine's projected order
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
fn fixture_skip_reason_is_optional() {
    let fixture =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/data_dirs_selftest/case");
    assert!(TestDir::load(&fixture).skip_reason.is_none());
}

#[test]
#[should_panic(expected = "skip_reason must be a non-empty string")]
fn blank_skip_reason_is_rejected() {
    load_fixture_edited("name =", "skip_reason = \"  \"\nname =");
}

#[test]
#[should_panic(expected = "skip_reason")]
fn non_string_skip_reason_is_rejected() {
    load_fixture_edited("name =", "skip_reason = false\nname =");
}

#[tokio::test]
async fn skipped_fixture_never_reads_cache_and_is_not_a_pass() {
    let fixture =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/data_dirs_selftest/case");
    let scratch = tempfile::TempDir::new().expect("tempdir");
    let dir = scratch.path().join("case");
    std::fs::create_dir(&dir).unwrap();
    for file in [TOML_NAME, INPUT_NAME, ORACLE_NAME] {
        std::fs::copy(fixture.join(file), dir.join(file)).unwrap();
    }
    let toml = std::fs::read_to_string(dir.join(TOML_NAME)).unwrap();
    std::fs::write(
        dir.join(TOML_NAME),
        format!("skip_reason = \"Unsupported symbolic deletion\"\n{toml}"),
    )
    .unwrap();
    let cache = scratch.path().join("absent-cache");
    let mut test = TestDir::load(&dir);
    assert_eq!(
        check_dir(&test, CacheRoot::At(&cache)).await,
        Outcome::Skipped
    );
    run_all(scratch.path(), CacheRoot::At(&cache)).await;
    // A skip must not hide a damaged oracle.
    test.body_md5 = "0".repeat(32);
    assert_eq!(
        check_dir(&test, CacheRoot::At(&cache)).await,
        Outcome::Failed
    );
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

/// `[vep] <key>` of `tools/vep_pin.toml`.
#[cfg(test)]
fn vep_pin(key: &str) -> String {
    let pin: Table = VEP_PIN
        .parse()
        .unwrap_or_else(|error| panic!("tools/vep_pin.toml does not parse: {error}"));
    pin.get("vep")
        .and_then(Value::as_table)
        .and_then(|vep| vep.get(key))
        .and_then(Value::as_str)
        .unwrap_or_else(|| panic!("tools/vep_pin.toml: [vep] {key} must be a string"))
        .to_owned()
}

#[test]
fn tagged_test_url_shape() {
    let tag = vep_pin("upstream_tag");
    let prefix = "https://github.com/Ensembl/ensembl-vep/blob";
    assert!(is_tagged_test_url(&format!(
        "{prefix}/{tag}/t/Runner.t#L244-L292"
    )));
    for bad in [
        format!("{prefix}/master/t/Runner.t"),
        format!("{prefix}/{}/t/Runner.t", vep_pin("upstream_commit")),
        format!("{prefix}/{tag}/modules/Runner.pm"),
        format!("{prefix}/{tag}/t/"),
        format!("{prefix}/release/latest/t/Runner.t"),
        "https://example.org/release/116.2/t/Runner.t".to_owned(),
    ] {
        assert!(!is_tagged_test_url(&bad), "{bad}");
    }
}

#[test]
#[should_panic(expected = "origin.vep_test is not a tagged Ensembl VEP test URL")]
fn unpinned_origin_rejected() {
    let origin: Table =
        "vep_test = \"https://github.com/Ensembl/ensembl-vep/blob/master/t/Runner.t\""
            .parse()
            .expect("toml");
    require_pinned_origin("t", &origin);
}

#[test]
fn fixture_source_urls_required() {
    let text = include_str!("fixtures/data_dirs_selftest/case/test.toml");
    for key in ["cache_source", "vep_cache", "fasta_source"] {
        let prefix = format!("{key} =");
        let before = text
            .lines()
            .find(|line| line.starts_with(&prefix))
            .expect("fixture source");
        let after = format!("{key} = \"local:/tmp/private-data\"");
        let result = std::panic::catch_unwind(|| load_fixture_edited(before, &after));
        assert!(
            result.is_err(),
            "{key}: a machine-local source was accepted"
        );
    }
}

// ---------------------------------------------------------------------------------
// [[tests]] (#238)
// ---------------------------------------------------------------------------------

/// The self-test fixture's `[origin]` block, verbatim (header to the next table).
fn fixture_origin() -> String {
    let text = include_str!("fixtures/data_dirs_selftest/case/test.toml");
    let start = text
        .find(
            "
[origin]
",
        )
        .expect("fixture has [origin]")
        + 1;
    let end = start
        + text[start..]
            .find(
                "
[",
            )
            .expect("a table follows [origin]")
        + 1;
    text[start..end].to_owned()
}

/// One `[[tests]]` table with id `id`, pinned links, and `extra` lines appended.
fn test_table(id: &str, extra: &str) -> String {
    // The version checker separately checks the exact release from the pin.
    let tag = vep_pin("upstream_tag");
    format!(
        "[[tests]]\n\
         id = \"{id}\"\n\
         description = \"NamedTest {id}.\"\n\
         vep_test = \"https://github.com/Ensembl/ensembl-vep/blob/{tag}/t/Runner.t#L1-L2\"\n\
         {extra}"
    )
}

/// Load the fixture with its `[origin]` replaced by `tables`.
fn load_fixture_with_tests(tables: &str) -> TestDir {
    load_fixture_edited(&fixture_origin(), tables)
}

#[test]
fn origin_dir_has_its_name_as_the_one_test() {
    let test = load_fixture_edited("# Self-test", "# Loader self-test");
    assert_eq!(test.tests, ["case"]);
}

#[test]
fn test_tables_accepted() {
    let tables = [
        test_table("case", ""),
        test_table(
            "case_symbol",
            "[tests.focus]\nkind = \"csq\"\nfield = \"SYMBOL\"\n\
             where = { Feature = \"ENST1\" }\n",
        ),
        test_table("case_count", "focus = { kind = \"record_count\" }\n"),
    ]
    .join("\n");
    let test = load_fixture_with_tests(&tables);
    assert_eq!(test.tests, ["case", "case_symbol", "case_count"]);
}

#[test]
#[should_panic(expected = "unknown key")]
fn test_unknown_key_panics() {
    let table = test_table("case", "").replace("vep_test", "vep_tset");
    load_fixture_with_tests(&table);
}

#[test]
#[should_panic(expected = "unknown key: issue")]
fn per_test_issue_is_rejected() {
    load_fixture_with_tests(&test_table("case", "issue = 226\n"));
}

#[test]
#[should_panic(expected = "unknown key: property")]
fn old_property_spelling_is_rejected() {
    load_fixture_with_tests(&test_table("case", "").replace("[[tests]]", "[[property]]"));
}

#[test]
#[should_panic(expected = "unknown key: descripton")]
fn test_misspelt_description_panics() {
    let table = test_table("case", "descripton = \"typo\"\n");
    load_fixture_with_tests(&table);
}

#[test]
#[should_panic(expected = "is missing required key vep_test")]
fn test_missing_required_key_panics() {
    let table = test_table("case", "")
        .lines()
        .filter(|line| !line.starts_with("vep_test ="))
        .collect::<Vec<_>>()
        .join("\n")
        + "\n";
    load_fixture_with_tests(&table);
}

#[test]
#[should_panic(expected = "has both [origin] and [[tests]]")]
fn origin_and_test_together_rejected() {
    let tables = format!("{}\n{}", fixture_origin(), test_table("case", ""));
    load_fixture_with_tests(&tables);
}

#[test]
#[should_panic(expected = "needs [origin] or at least one [[tests]]")]
fn no_origin_and_no_test_rejected() {
    load_fixture_with_tests("");
}

#[test]
#[should_panic(expected = "duplicate [[tests]] id: case_x")]
fn duplicate_test_id_rejected() {
    let tables = [
        test_table("case", ""),
        test_table("case_x", ""),
        test_table("case_x", ""),
    ]
    .join("\n");
    load_fixture_with_tests(&tables);
}

#[test]
fn distinct_test_ids_across_dirs_accepted() {
    let second = TestDir {
        name: "other".to_owned(),
        tests: vec!["other".to_owned()],
        ..load_fixture_with_tests(&test_table("case", ""))
    };
    check_unique_test_ids(&[load_fixture_with_tests(&test_table("case", "")), second]);
}

#[test]
#[should_panic(expected = "test id case is declared by both [case] and [other]")]
fn duplicate_test_id_across_dirs_rejected() {
    let first = load_fixture_with_tests(&test_table("case", ""));
    let second = TestDir {
        name: "other".to_owned(),
        ..load_fixture_with_tests(&test_table("case", ""))
    };
    check_unique_test_ids(&[first, second]);
}

#[test]
#[should_panic(expected = "no [[tests]] has id = the directory name")]
fn test_ids_must_name_the_directory() {
    load_fixture_with_tests(&test_table("other", ""));
}

#[test]
#[should_panic(expected = "[case] test case.vep_test is not a tagged Ensembl VEP test URL")]
fn test_unpinned_link_rejected() {
    let table = test_table("case", "").replace(&vep_pin("upstream_tag"), "master");
    load_fixture_with_tests(&table);
}

#[test]
#[should_panic(expected = "focus kind = \"fields\" is not one of")]
fn test_focus_unknown_kind_rejected() {
    let table = test_table("case", "focus = { kind = \"fields\" }\n");
    load_fixture_with_tests(&table);
}

#[test]
#[should_panic(expected = "unknown key: wehre")]
fn test_focus_unknown_key_rejected() {
    let table = test_table("case", "focus = { kind = \"csq\", wehre = {} }\n");
    load_fixture_with_tests(&table);
}

/// Every committed `tests/data` directory passes the loader (schema, pins, mode,
/// `[[tests]]` rules) without any cache (#238).
#[test]
fn committed_dirs_load() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/data");
    let dirs = test_dirs(&root);
    let tests: Vec<TestDir> = dirs.iter().map(|dir| TestDir::load(dir)).collect();
    check_unique_test_ids(&tests);
    for test in &tests {
        assert!(
            test.runs
                .iter()
                .all(|run| matches!(run.settings.flavour, Flavour::Merged)),
            "[{}] committed data fixtures must use the merged cache",
            test.name
        );
    }
    let tests: usize = tests.iter().map(|test| test.tests.len()).sum();
    println!("{} directories, {tests} tests", dirs.len());
    assert!(!dirs.is_empty());
}
