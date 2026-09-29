//! `required_contigs` read from a ledger-style `[[assertion]]` TOML fragment.
//!
//! Data-tests are directories, `tests/data/<name>/`, and declare their contigs in
//! `test.toml` as `[vepyr] required_contigs`; the generic runner `tests/data_dirs.rs`
//! reads that table itself. This module is kept for `tests/common_compile.rs`.
//!
//! Contigs are a **declared contract**, not discovered at runtime from the VCF:
//!
//! - Field name: `required_contigs` (array of contig strings, e.g. `["chr21", "chrMT"]`).
//! - Value = exactly the contigs of the variant loci the test exercises — not a
//!   wider “just in case” set, not narrower than the input loci.
//! - This module only **reads** the field and feeds [`super::cache::requires_shards`].
//!
//! The sitekwb ledger (`sitekwb/vepyr-porting-tests`, `ledger/*.ledger.toml`) is the
//! source of candidate tests; a directory names its source row in `[origin] ledger`.

use super::cache::{Entity, FullCache, requires_shards};

/// Read `required_contigs` from the first `[[assertion]]` table in `toml_text`.
///
/// Panics if the document does not parse, has no assertions, or the field is missing,
/// empty, or not an array of non-empty strings.
#[track_caller]
pub fn required_contigs_from_assertion_toml(toml_text: &str) -> Vec<String> {
    required_contigs_from_assertion_toml_at(toml_text, 0)
}

/// Read `required_contigs` from the `index`-th `[[assertion]]` (0-based) in `toml_text`.
#[track_caller]
pub fn required_contigs_from_assertion_toml_at(toml_text: &str, index: usize) -> Vec<String> {
    let doc: toml::Table = toml::from_str(toml_text)
        .unwrap_or_else(|error| panic!("assertion TOML does not parse: {error}"));
    let assertions = doc
        .get("assertion")
        .and_then(|value| value.as_array())
        .unwrap_or_else(|| panic!("assertion TOML has no [[assertion]] array"));
    let assertion = assertions.get(index).unwrap_or_else(|| {
        panic!(
            "assertion TOML has {} [[assertion]] entries; index {index} is out of range",
            assertions.len()
        )
    });
    let table = assertion
        .as_table()
        .unwrap_or_else(|| panic!("[[assertion]] at index {index} is not a table"));
    let raw = table
        .get("required_contigs")
        .unwrap_or_else(|| panic!("[[assertion]] at index {index} has no required_contigs field"));
    let array = raw.as_array().unwrap_or_else(|| {
        panic!("[[assertion]] at index {index}: required_contigs must be an array of strings")
    });
    if array.is_empty() {
        panic!("[[assertion]] at index {index}: required_contigs must not be empty");
    }
    array
        .iter()
        .enumerate()
        .map(|(i, value)| match value.as_str() {
            Some(contig) if !contig.is_empty() => contig.to_owned(),
            Some(_) => {
                panic!("[[assertion]] at index {index}: required_contigs[{i}] is an empty string")
            }
            None => panic!("[[assertion]] at index {index}: required_contigs[{i}] is not a string"),
        })
        .collect()
}

/// Load `required_contigs` from `assertion_toml` and enforce those shards on `cache`.
///
/// Callable path for AC-3: ledger assertion → contig list → [`requires_shards`], with
/// the entities derived per contig by [`Entity::read_under_everything`].
#[track_caller]
pub fn requires_shards_for_assertion(cache: &FullCache, assertion_toml: &str) {
    let contigs = required_contigs_from_assertion_toml(assertion_toml);
    let refs: Vec<&str> = contigs.iter().map(String::as_str).collect();
    requires_shards(cache, &refs, Entity::read_under_everything);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    fn fixture_toml() -> String {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/required_contigs_assertion.toml");
        std::fs::read_to_string(&path).unwrap_or_else(|error| panic!("{}: {error}", path.display()))
    }

    #[test]
    fn reads_required_contigs_from_fixture() {
        let contigs = required_contigs_from_assertion_toml(&fixture_toml());
        assert_eq!(contigs, vec!["chr21".to_owned(), "chrMT".to_owned()]);
    }

    #[test]
    #[should_panic(expected = "required_contigs must not be empty")]
    fn empty_required_contigs_panics() {
        let _ = required_contigs_from_assertion_toml(
            r#"
[[assertion]]
n = 1
required_contigs = []
"#,
        );
    }

    #[test]
    #[should_panic(expected = "has no required_contigs field")]
    fn missing_required_contigs_panics() {
        let _ = required_contigs_from_assertion_toml(
            r#"
[[assertion]]
n = 1
desc = "no contigs field"
"#,
        );
    }
}
