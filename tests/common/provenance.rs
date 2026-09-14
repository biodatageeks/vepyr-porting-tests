//! Provenance header lines that dfbf embeds in annotated VCFs.
//!
//! Two runs with different temp paths or knobs differ only in those lines; ports
//! that assert byte-stable CSQ / headers replace or strip them here.

/// Prefix of the header lines that record a run's provenance.
pub const PROVENANCE_PREFIX: &str = "##datafusion-bio-function-vep";

const ELIDED: &str = "##<provenance elided by tests/common>";

/// Whether `line` is one of the provenance header lines.
pub fn is_provenance_line(line: &str) -> bool {
    line.starts_with(PROVENANCE_PREFIX)
}

/// The VCF with every provenance header line replaced by a fixed placeholder.
///
/// Replacing (not deleting) keeps position and count under comparison while
/// dropping volatile temp paths and option JSON.
pub fn provenance_normalised(vcf: &str) -> String {
    vcf.split_inclusive('\n')
        .map(|line| match line.strip_suffix('\n') {
            Some(body) if is_provenance_line(body) => format!("{ELIDED}\n"),
            None if is_provenance_line(line) => ELIDED.to_owned(),
            _ => line.to_owned(),
        })
        .collect()
}

/// Header lines of `vcf` with provenance lines removed, in file order.
pub fn header_without_provenance(vcf: &str) -> Vec<&str> {
    vcf.lines()
        .take_while(|line| line.starts_with('#'))
        .filter(|line| !is_provenance_line(line))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn provenance_lines_are_recognised() {
        assert!(is_provenance_line(
            "##datafusion-bio-function-vep=\"0.17.2\" cache=…"
        ));
        assert!(!is_provenance_line("##INFO=<ID=CSQ,"));
    }

    #[test]
    fn normalisation_elides_only_provenance() {
        let vcf = "##fileformat=VCFv4.2\n\
             ##datafusion-bio-function-vep=\"x\"\n\
             #CHROM\tPOS\n\
             21\t1\n";
        let out = provenance_normalised(vcf);
        assert!(out.contains(ELIDED));
        assert!(!out.contains("##datafusion-bio-function-vep=\"x\""));
        assert!(out.contains("##fileformat=VCFv4.2"));
    }
}
