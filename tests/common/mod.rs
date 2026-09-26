//! Shared helpers for data-problem tests.
//!
//! A data-test is a directory `tests/data/<name>/` (`input.vcf`,
//! `expected_output.vcf`, `test.toml`), run by the one generic runner
//! `tests/data_dirs.rs`; these modules are what that runner shares with
//! `tests/common_compile.rs`.
//!
//! - [`cache`] — sole bridge to `$VEPYR_CACHE_ROOT` / `PROVENANCE.json` / FASTA
//! - [`ledger`] — reads `required_contigs` from an assertion fragment; used by `tests/common_compile.rs`
//! - [`annotate`] — thin `annotate_to_vcf` wrapper (`annotate_vcf_at`) over a checked cache directory
//! - [`csq`] — CSQ layout / data-line helpers
//! - [`provenance`] — elide run-specific VCF header lines for comparisons
//!
//! # `annotate_config!`
//!
//! `AnnotateVcfConfig` is `#[non_exhaustive]`; this macro is the one construction
//! path shared by data-tests (`default`-then-assign, or override an existing base).

#![allow(dead_code, unused_macros, unused_imports)]

pub mod annotate;
pub mod cache;
pub mod csq;
pub mod ledger;
pub mod provenance;

/// Build an [`AnnotateVcfConfig`] from named field values.
///
/// * `annotate_config! { a: 1, b: 2 }` starts from `Default::default()`
/// * `annotate_config! { a: 1, ..base }` starts from `base` (trailing comma required)
///
/// [`AnnotateVcfConfig`]: datafusion_bio_function_vep::vcf_sink::AnnotateVcfConfig
macro_rules! annotate_config {
    ($($field:ident : $value:expr,)* .. $base:expr) => {{
        #[allow(unused_mut)]
        let mut config: ::datafusion_bio_function_vep::vcf_sink::AnnotateVcfConfig = $base;
        $(config.$field = $value;)*
        config
    }};
    ($($field:ident : $value:expr),* $(,)?) => {
        annotate_config!(
            $($field: $value,)*
            .. <::datafusion_bio_function_vep::vcf_sink::AnnotateVcfConfig
                as ::core::default::Default>::default()
        )
    };
}

pub(crate) use annotate_config;
