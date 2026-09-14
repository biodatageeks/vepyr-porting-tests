//! Compile harness for `tests/common` (issue #5 AC-1).
//!
//! Pulls the cache/ledger modules into a `[[test]]` target so
//! `cargo check --tests` type-checks them. Not a smoke or data-problem test.

#![allow(dead_code)]

mod common;

use common::cache::{Entity, Flavour};
use common::ledger::required_contigs_from_assertion_toml;

#[test]
fn common_modules_link() {
    assert_eq!(Flavour::Ensembl.key(), "ensembl");
    assert_eq!(Entity::Transcript.dir_name(), "transcript");
    let fixture = include_str!("fixtures/required_contigs_assertion.toml");
    let contigs = required_contigs_from_assertion_toml(fixture);
    assert_eq!(contigs, ["chr21", "chrMT"]);
}

#[test]
fn ledger_to_requires_shards_path_compiles() {
    // Type-check the AC-3 glue signature without touching a real cache root.
    let _f: fn(&common::cache::FullCache, &[Entity], &str) =
        common::ledger::requires_shards_for_assertion;
}
