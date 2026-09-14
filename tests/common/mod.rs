//! Shared helpers for data-problem tests.
//!
//! - [`cache`] — sole bridge to `$VEPYR_CACHE_ROOT` / `PROVENANCE.json` / FASTA
//! - [`ledger`] — reads assertion field `required_contigs` and feeds `requires_shards`

pub mod cache;
pub mod ledger;
