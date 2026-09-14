//! Compile harness for `tests/common` (issues #5 / #14).
//!
//! Pulls cache/ledger/csq/provenance/annotate modules into a `[[test]]` target so
//! `cargo check --tests` type-checks them. Not a smoke or data-problem test.

#![allow(dead_code)]

mod common;

use common::annotate_config;
use common::cache::{Entity, Flavour};
use common::csq::{csq_layout, data_lines};
use common::ledger::required_contigs_from_assertion_toml;
use common::provenance::is_provenance_line;

#[test]
fn common_modules_link() {
    assert_eq!(Flavour::Ensembl.key(), "ensembl");
    assert_eq!(Entity::Transcript.dir_name(), "transcript");
    let fixture = include_str!("fixtures/required_contigs_assertion.toml");
    let contigs = required_contigs_from_assertion_toml(fixture);
    assert_eq!(contigs, ["chr21", "chrMT"]);
    assert!(csq_layout("##INFO=<ID=CSQ,Description=\"x Format: A|B\">\n").len() == 2);
    assert!(data_lines("#CHROM\n21\t1\n").len() == 1);
    assert!(is_provenance_line("##datafusion-bio-function-vep=\"1\""));
    let _cfg = annotate_config! {};
}

#[test]
fn ledger_to_requires_shards_path_compiles() {
    let _f: fn(&common::cache::FullCache, &[Entity], &str) =
        common::ledger::requires_shards_for_assertion;
}
