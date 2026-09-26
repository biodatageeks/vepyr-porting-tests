//! Annotate a VCF body against an already-checked cache directory and return the
//! written text.

use std::path::Path;

use datafusion_bio_function_vep::vcf_sink::{self, AnnotateVcfConfig};
use tempfile::TempDir;

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
