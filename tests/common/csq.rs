//! Parse CSQ layout and groups from an annotated VCF string.
//!
//! Shared by data-problem pilots that assert consequence content / invariance /
//! colocated fields. Fail loud when the expected CSQ structure is absent.

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

/// The `|`-split CSQ groups of the data line at index `row`.
#[track_caller]
pub fn csq_groups(vcf: &str, row: usize) -> Vec<Vec<String>> {
    let lines = data_lines(vcf);
    let line = lines.get(row).unwrap_or_else(|| {
        panic!(
            "VCF has {} data line(s); requested CSQ groups for row {row}",
            lines.len()
        )
    });
    line.split('\t')
        .nth(7)
        .expect("INFO column")
        .split(';')
        .find_map(|entry| entry.strip_prefix("CSQ="))
        .expect("the INFO column must carry a CSQ entry")
        .split(',')
        .map(|group| group.split('|').map(str::to_owned).collect())
        .collect()
}

/// The value of CSQ subfield `name` in `group`, empty when unset.
#[track_caller]
pub fn field<'a>(layout: &[String], group: &'a [String], name: &str) -> &'a str {
    let index = layout
        .iter()
        .position(|f| f == name)
        .unwrap_or_else(|| panic!("CSQ layout has no `{name}` field"));
    group.get(index).map(String::as_str).unwrap_or_default()
}

/// The CSQ group whose `Feature` equals `feature`.
#[track_caller]
pub fn group_for<'a>(
    layout: &[String],
    groups: &'a [Vec<String>],
    feature: &str,
) -> &'a [String] {
    groups
        .iter()
        .find(|group| field(layout, group, "Feature") == feature)
        .map(Vec::as_slice)
        .unwrap_or_else(|| {
            let seen: Vec<&str> = groups.iter().map(|g| field(layout, g, "Feature")).collect();
            panic!("no CSQ group for {feature}; saw {seen:?}")
        })
}

/// Distinct values of subfield `name` across every group (sorted, deduped).
pub fn distinct<'a>(layout: &[String], groups: &'a [Vec<String>], name: &str) -> Vec<&'a str> {
    let mut seen: Vec<&str> = groups.iter().map(|group| field(layout, group, name)).collect();
    seen.sort_unstable();
    seen.dedup();
    seen
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = "##fileformat=VCFv4.2\n\
         ##INFO=<ID=CSQ,Number=.,Type=String,Description=\"Consequence. Format: Allele|Consequence|Feature\">\n\
         #CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n\
         21\t1\t.\tA\tG\t.\tPASS\tCSQ=G|missense_variant|ENST1,G|upstream_gene_variant|ENST2\n";

    #[test]
    fn layout_and_groups_round_trip() {
        let layout = csq_layout(SAMPLE);
        assert_eq!(layout, ["Allele", "Consequence", "Feature"]);
        let groups = csq_groups(SAMPLE, 0);
        assert_eq!(groups.len(), 2);
        assert_eq!(field(&layout, &groups[0], "Feature"), "ENST1");
        assert_eq!(group_for(&layout, &groups, "ENST2")[1], "upstream_gene_variant");
        assert_eq!(distinct(&layout, &groups, "Allele"), ["G"]);
    }
}
