//! CSQ layout and data lines of an annotated VCF string.
//!
//! Fail loud when the expected CSQ header structure is absent.

/// The `Format:` field list declared by the emitted `##INFO=<ID=CSQ…>` header.
pub fn csq_layout(vcf: &str) -> Vec<String> {
    vcf.lines()
        .find_map(|line| line.strip_prefix("##INFO=<ID=CSQ,"))
        .map(|rest| {
            rest.split("Format: ")
                .nth(1)
                .expect("the CSQ header must declare `Format: `")
                .trim_end_matches(['"', '>'])
                .split('|')
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default()
}

/// Non-header lines of an emitted VCF.
pub fn data_lines(vcf: &str) -> Vec<&str> {
    vcf.lines().filter(|line| !line.starts_with('#')).collect()
}
