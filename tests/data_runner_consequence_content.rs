//! Data-problem port of `Runner.ledger.toml` n=16 — consequence content.
//!
//! Source: `sitekwb-vepyr-porting-tests/tests/port_runner_consequence_content.rs`
//! (`buffer_to_output_renders_the_three_named_consequences`), ledger
//! `Runner.ledger.toml` n=16 (`perl/Runner.t:244-289`), category `analog-port`.
//! Issue: <https://github.com/biodatageeks/vepyr-porting-tests/issues/16>.
//!
//! For the fixed SNV `chr21:25585733 C>T` (rs142513484), vepyr's emitted CSQ must
//! match the native Ensembl VEP 116 **`default`** reference run field-for-field
//! across the **whole** 34-group inventory (issue #16, DECIDED 1 and DECIDED 3) —
//! not just the three transcripts the Perl original named, and not just the group
//! count. A new group, a missing group, or any changed field value fails here.
//!
//! Oracle: `ensemblorg/ensembl-vep:release_116.0`, cache `homo_sapiens/116_GRCh38`,
//! flavour `ensembl` (GENCODE 50), assembly GRCh38, `--offline --cache --vcf` with
//! no further flags. Every literal in [`EXPECTED`] is transcribed verbatim from that
//! run's CSQ block, recorded on the issue:
//! <https://github.com/biodatageeks/vepyr-porting-tests/issues/16#issuecomment-5671600272>.
//!
//! The input VCF is spelled `chr21` and so is `required_contigs` (DECIDED 2): one
//! spelling in both places, no harness-level contig renaming.

mod common;

use common::annotate_config;
use common::cache::{Entity, Flavour};
use common::{annotate, cache, csq};

/// Single-record input, contig spelled `chr21` (issue #16, DECIDED 2).
const INPUT_VCF: &str = "\
##fileformat=VCFv4.2
##contig=<ID=chr21,length=46709983>
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
chr21\t25585733\trs142513484\tC\tT\t.\tPASS\t.
";

/// The ledger assertion this test realises; `required_contigs` drives the shard check.
// TODO(#63): read from ledger/Runner.ledger.toml once the ledger corpus moves to SSOT
const ASSERTION_TOML: &str = r#"
[[assertion]]
n = 16
desc = "Runner.ledger.toml n=16 — consequence content, chr21:25585733 C>T"
required_contigs = ["chr21"]
"#;

/// One expected CSQ group: all 23 subfields of the `default` VEP 116 layout.
///
/// A field that is empty in the CSQ string is `""` (the issue's table writes `—`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Expected {
    allele: &'static str,
    consequence: &'static str,
    impact: &'static str,
    symbol: &'static str,
    gene: &'static str,
    feature_type: &'static str,
    feature: &'static str,
    biotype: &'static str,
    exon: &'static str,
    intron: &'static str,
    hgvsc: &'static str,
    hgvsp: &'static str,
    cdna_position: &'static str,
    cds_position: &'static str,
    protein_position: &'static str,
    amino_acids: &'static str,
    codons: &'static str,
    existing_variation: &'static str,
    distance: &'static str,
    strand: &'static str,
    flags: &'static str,
    symbol_source: &'static str,
    hgnc_id: &'static str,
}

impl Expected {
    /// `(CSQ subfield name, expected value)` in the oracle's declared layout order.
    const fn by_csq_field(&self) -> [(&'static str, &'static str); 23] {
        [
            ("Allele", self.allele),
            ("Consequence", self.consequence),
            ("IMPACT", self.impact),
            ("SYMBOL", self.symbol),
            ("Gene", self.gene),
            ("Feature_type", self.feature_type),
            ("Feature", self.feature),
            ("BIOTYPE", self.biotype),
            ("EXON", self.exon),
            ("INTRON", self.intron),
            ("HGVSc", self.hgvsc),
            ("HGVSp", self.hgvsp),
            ("cDNA_position", self.cdna_position),
            ("CDS_position", self.cds_position),
            ("Protein_position", self.protein_position),
            ("Amino_acids", self.amino_acids),
            ("Codons", self.codons),
            ("Existing_variation", self.existing_variation),
            ("DISTANCE", self.distance),
            ("STRAND", self.strand),
            ("FLAGS", self.flags),
            ("SYMBOL_SOURCE", self.symbol_source),
            ("HGNC_ID", self.hgnc_id),
        ]
    }
}

/// Positional table rows, so the source below reads like the `|`-delimited CSQ dump
/// it was copied from and can be diffed against it by eye.
macro_rules! expected_groups {
    ($($allele:literal | $consequence:literal | $impact:literal | $symbol:literal
      | $gene:literal | $feature_type:literal | $feature:literal | $biotype:literal
      | $exon:literal | $intron:literal | $hgvsc:literal | $hgvsp:literal
      | $cdna_position:literal | $cds_position:literal | $protein_position:literal
      | $amino_acids:literal | $codons:literal | $existing_variation:literal
      | $distance:literal | $strand:literal | $flags:literal | $symbol_source:literal
      | $hgnc_id:literal);* $(;)?) => {
        [$(Expected {
            allele: $allele,
            consequence: $consequence,
            impact: $impact,
            symbol: $symbol,
            gene: $gene,
            feature_type: $feature_type,
            feature: $feature,
            biotype: $biotype,
            exon: $exon,
            intron: $intron,
            hgvsc: $hgvsc,
            hgvsp: $hgvsp,
            cdna_position: $cdna_position,
            cds_position: $cds_position,
            protein_position: $protein_position,
            amino_acids: $amino_acids,
            codons: $codons,
            existing_variation: $existing_variation,
            distance: $distance,
            strand: $strand,
            flags: $flags,
            symbol_source: $symbol_source,
            hgnc_id: $hgnc_id,
        }),*]
    };
}

/// All 34 CSQ groups of the VEP 116 `default` run.
///
/// The 34 rows are listed in the oracle's emission order so the source can be diffed
/// by eye against the oracle's CSQ dump, but that order is **not asserted** by this
/// test: [`csq::group_for`] addresses each group by `Feature`, not by position.
/// Whether group order should be asserted at all is tracked by issue #64.
///
/// Layout (from the oracle's `##INFO=<ID=CSQ…Format: …>` header):
/// `Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|BIOTYPE|EXON|INTRON|`
/// `HGVSc|HGVSp|cDNA_position|CDS_position|Protein_position|Amino_acids|Codons|`
/// `Existing_variation|DISTANCE|STRAND|FLAGS|SYMBOL_SOURCE|HGNC_ID`
#[rustfmt::skip]
const EXPECTED: [Expected; 34] = expected_groups![
    "T" | "3_prime_UTR_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00000307301" | "protein_coding" | "11/11" | "" | "" | "" | "1130" | "" | "" | "" | "" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "missense_variant" | "MODERATE" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00000352957" | "protein_coding" | "10/10" | "" | "" | "" | "997" | "991" | "331" | "A/T" | "Gca/Aca" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "upstream_gene_variant" | "MODIFIER" | "" | "ENSG00000260583" | "Transcript" | "ENST00000567517" | "lncRNA" | "" | "" | "" | "" | "" | "" | "" | "" | "" | "" | "2407" | "-1" | "" | "" | "";
    "T" | "missense_variant" | "MODERATE" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00000875588" | "protein_coding" | "10/10" | "" | "" | "" | "990" | "940" | "314" | "A/T" | "Gca/Aca" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "missense_variant" | "MODERATE" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00000875589" | "protein_coding" | "10/10" | "" | "" | "" | "987" | "937" | "313" | "A/T" | "Gca/Aca" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "missense_variant" | "MODERATE" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00000925340" | "protein_coding" | "9/9" | "" | "" | "" | "993" | "943" | "315" | "A/T" | "Gca/Aca" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "missense_variant" | "MODERATE" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00000925341" | "protein_coding" | "10/10" | "" | "" | "" | "1044" | "994" | "332" | "A/T" | "Gca/Aca" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "missense_variant" | "MODERATE" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00000925342" | "protein_coding" | "8/8" | "" | "" | "" | "801" | "751" | "251" | "A/T" | "Gca/Aca" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "missense_variant" | "MODERATE" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00000925343" | "protein_coding" | "10/10" | "" | "" | "" | "927" | "877" | "293" | "A/T" | "Gca/Aca" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "missense_variant" | "MODERATE" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00000925344" | "protein_coding" | "10/10" | "" | "" | "" | "1002" | "952" | "318" | "A/T" | "Gca/Aca" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "missense_variant" | "MODERATE" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00000925345" | "protein_coding" | "9/9" | "" | "" | "" | "834" | "784" | "262" | "A/T" | "Gca/Aca" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "missense_variant" | "MODERATE" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00000925346" | "protein_coding" | "10/10" | "" | "" | "" | "1059" | "1009" | "337" | "A/T" | "Gca/Aca" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "missense_variant" | "MODERATE" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00000946900" | "protein_coding" | "10/10" | "" | "" | "" | "1038" | "988" | "330" | "A/T" | "Gca/Aca" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "missense_variant" | "MODERATE" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00000946901" | "protein_coding" | "10/10" | "" | "" | "" | "996" | "946" | "316" | "A/T" | "Gca/Aca" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "3_prime_UTR_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00000985907" | "protein_coding" | "9/9" | "" | "" | "" | "887" | "" | "" | "" | "" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "3_prime_UTR_variant&NMD_transcript_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00000985908" | "nonsense_mediated_decay" | "10/10" | "" | "" | "" | "952" | "" | "" | "" | "" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "3_prime_UTR_variant&NMD_transcript_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00000985909" | "nonsense_mediated_decay" | "9/9" | "" | "" | "" | "857" | "" | "" | "" | "" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "downstream_gene_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00000985910" | "protein_coding" | "" | "" | "" | "" | "" | "" | "" | "" | "" | "" | "367" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "3_prime_UTR_variant&NMD_transcript_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00001019016" | "nonsense_mediated_decay" | "10/10" | "" | "" | "" | "1023" | "" | "" | "" | "" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "downstream_gene_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00001019018" | "protein_coding" | "" | "" | "" | "" | "" | "" | "" | "" | "" | "" | "336" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "3_prime_UTR_variant&NMD_transcript_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00001078748" | "nonsense_mediated_decay" | "9/9" | "" | "" | "" | "959" | "" | "" | "" | "" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "3_prime_UTR_variant&NMD_transcript_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00001078749" | "nonsense_mediated_decay" | "10/10" | "" | "" | "" | "1023" | "" | "" | "" | "" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "3_prime_UTR_variant&NMD_transcript_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00001078750" | "nonsense_mediated_decay" | "10/10" | "" | "" | "" | "1487" | "" | "" | "" | "" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "3_prime_UTR_variant&NMD_transcript_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00001078754" | "nonsense_mediated_decay" | "9/9" | "" | "" | "" | "804" | "" | "" | "" | "" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "3_prime_UTR_variant&NMD_transcript_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00001078757" | "nonsense_mediated_decay" | "8/8" | "" | "" | "" | "703" | "" | "" | "" | "" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "3_prime_UTR_variant&NMD_transcript_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00001078759" | "nonsense_mediated_decay" | "8/8" | "" | "" | "" | "809" | "" | "" | "" | "" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "3_prime_UTR_variant&NMD_transcript_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00001101473" | "nonsense_mediated_decay" | "11/11" | "" | "" | "" | "1171" | "" | "" | "" | "" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "3_prime_UTR_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00001101474" | "protein_coding" | "11/11" | "" | "" | "" | "1143" | "" | "" | "" | "" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "intron_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00001110240" | "protein_coding" | "" | "9/9" | "" | "" | "" | "" | "" | "" | "" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "downstream_gene_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00001110241" | "nonsense_mediated_decay" | "" | "" | "" | "" | "" | "" | "" | "" | "" | "" | "1531" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "downstream_gene_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00001110242" | "nonsense_mediated_decay" | "" | "" | "" | "" | "" | "" | "" | "" | "" | "" | "3006" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "3_prime_UTR_variant&NMD_transcript_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00001124854" | "nonsense_mediated_decay" | "10/10" | "" | "" | "" | "1077" | "" | "" | "" | "" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "downstream_gene_variant" | "MODIFIER" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00001129323" | "protein_coding" | "" | "" | "" | "" | "" | "" | "" | "" | "" | "" | "2992" | "-1" | "" | "HGNC" | "HGNC:14027";
    "T" | "missense_variant" | "MODERATE" | "MRPL39" | "ENSG00000154719" | "Transcript" | "ENST00001131846" | "protein_coding" | "10/10" | "" | "" | "" | "1041" | "991" | "331" | "A/T" | "Gca/Aca" | "" | "" | "-1" | "" | "HGNC" | "HGNC:14027";
];

#[tokio::test]
async fn data_runner_consequence_content_matches_vep116_default_oracle() {
    let cache = cache::full_cache(Flavour::Ensembl);
    // Default oracle (`--offline --cache --vcf`) without colocated variation: Transcript +
    // Exon + TranslationCore are what `annotate` needs for this CSQ shape.
    let entities = &[Entity::Transcript, Entity::Exon, Entity::TranslationCore];
    // DECIDED 1: the DEFAULT `AnnotateVcfConfig` *is* the oracle — no overrides.
    let config = annotate_config! {};

    let (written, output, _tmp) =
        annotate::annotate_vcf(&cache, entities, ASSERTION_TOML, INPUT_VCF, &config).await;
    assert_eq!(written, Ok(1), "one input record must be annotated and written");

    let data_lines = csq::data_lines(&output);
    assert_eq!(data_lines.len(), 1, "one data line expected, got {data_lines:?}");
    let columns: Vec<&str> = data_lines[0].split('\t').collect();
    assert_eq!(
        columns.get(..5),
        Some(&["chr21", "25585733", "rs142513484", "C", "T"][..]),
        "the emitted row must be the input SNV, contig spelled chr21"
    );

    let layout = csq::csq_layout(&output);
    #[rustfmt::skip]
    assert_eq!(
        layout,
        [
            "Allele", "Consequence", "IMPACT", "SYMBOL", "Gene", "Feature_type", "Feature",
            "BIOTYPE", "EXON", "INTRON", "HGVSc", "HGVSp", "cDNA_position", "CDS_position",
            "Protein_position", "Amino_acids", "Codons", "Existing_variation", "DISTANCE",
            "STRAND", "FLAGS", "SYMBOL_SOURCE", "HGNC_ID",
        ],
        "the emitted CSQ layout must match the VEP 116 default 23-field layout"
    );
    let groups = csq::csq_groups(&output, 0);

    // Cheapest falsifier first: the group inventory size.
    assert_eq!(
        groups.len(),
        EXPECTED.len(),
        "expected {} CSQ groups from the VEP 116 default oracle, got {}",
        EXPECTED.len(),
        groups.len()
    );

    // Then the inventory identity, so a swapped/missing/extra transcript reports as a
    // set difference rather than as a `group_for` panic deep in the per-field loop.
    let mut expected_features: Vec<&str> = EXPECTED.iter().map(|row| row.feature).collect();
    expected_features.sort_unstable();
    assert_eq!(
        csq::distinct(&layout, &groups, "Feature"),
        expected_features,
        "the set of annotated Feature IDs must equal the oracle's 34"
    );

    // Assert all 23 CSQ subfields per group. Empty HGVSc/HGVSp/FLAGS are a real
    // default-oracle contract (no HGVS / flag sources in this config) — an engine
    // that starts filling them fails loud (DECIDED 3). Empty Existing_variation is
    // weaker: it mainly pins that this run does not require the variation shard,
    // not a colocated-variation engine contract.
    for row in &EXPECTED {
        let group = csq::group_for(&layout, &groups, row.feature);
        assert_eq!(
            group.len(),
            layout.len(),
            "CSQ group for {} has {} fields, layout declares {}",
            row.feature,
            group.len(),
            layout.len()
        );
        for (name, expected) in row.by_csq_field() {
            assert_eq!(
                csq::field(&layout, group, name),
                expected,
                "CSQ field {name} of {} disagrees with the VEP 116 default oracle",
                row.feature
            );
        }
    }
}
