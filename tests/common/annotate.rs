//! Annotate a VCF body against a [`FullCache`] and return the written text.

use std::path::Path;

use datafusion_bio_function_vep::vcf_sink::{self, AnnotateVcfConfig};
use tempfile::TempDir;

use super::cache::{Entity, FullCache};
use super::ledger::requires_shards_for_assertion;

/// Enforce `required_contigs` from `assertion_toml`, then annotate `input_body`.
///
/// Returns `(Ok(row_count) or Err(message), output VCF text, TempDir)`. Keep the
/// `TempDir` alive while reading paths derived from it.
pub async fn annotate_vcf(
    cache: &FullCache,
    entities: &[Entity],
    assertion_toml: &str,
    input_body: &str,
    config: &AnnotateVcfConfig,
) -> (Result<usize, String>, String, TempDir) {
    requires_shards_for_assertion(cache, entities, assertion_toml);
    annotate_vcf_at(&cache.dir(), input_body, config).await
}

/// Annotate `input_body` against an already-checked cache directory.
pub async fn annotate_vcf_at(
    cache_dir: &Path,
    input_body: &str,
    config: &AnnotateVcfConfig,
) -> (Result<usize, String>, String, TempDir) {
    let tmp = TempDir::new().expect("tempdir");
    let input = tmp.path().join("input.vcf");
    let output = tmp.path().join("annotated.vcf");
    std::fs::write(&input, input_body).expect("write input vcf");
    let written = vcf_sink::annotate_to_vcf(
        input.to_str().expect("utf-8 path"),
        cache_dir.to_str().expect("utf-8 path"),
        "parquet",
        output.to_str().expect("utf-8 path"),
        config,
    )
    .await
    .map_err(|error| error.to_string());
    let text = std::fs::read_to_string(&output).unwrap_or_default();
    (written, text, tmp)
}
