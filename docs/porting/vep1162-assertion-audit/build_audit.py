"""Rebuild the assertion ledger and atomic port backlog from the pinned inventory.

No VEP or vepyr executions are performed here. Curated mappings below separate
observable projections from the implementation assertions which motivated them.
Run: python3 build_audit.py [--source /path/to/116.2/snapshot]
"""

from pathlib import Path
from collections import Counter, defaultdict
import argparse
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parent
PIN = "2cb0bbe216bb31c75de8f8000e2da7ff4fb7b451"
PORT_PIN = "2cdf45b73f31c6001a434cd76e5863ab41fac53b"
VEPYR_PIN = "b14bda3d4cf80c0f16d62ab371c28866ae589c33"
BASE = f"https://github.com/Ensembl/ensembl-vep/blob/{PIN}/"
API = f"https://github.com/biodatageeks/vepyr/blob/{VEPYR_PIN}/src/vepyr/__init__.py#L1011-L1058"
CLI = (
    f"https://github.com/biodatageeks/vepyr/blob/{VEPYR_PIN}/src/vepyr/cli.py#L81-L134"
)
HARNESS = f"https://github.com/biodatageeks/vepyr-porting-tests/blob/{PORT_PIN}/tests/data_dirs.rs"
OLD = "Release-84 microcache expectation: rediscover the witness and regenerate with cache 116; old literal is not an oracle."
NO_OLD = "No old-cache value is needed for this property; generate a fresh 116.2 oracle nevertheless."
UNKNOWN_BUG = (
    "Unassessed: no 116.2 differential run. A candidate is not evidence of a bug."
)
ASSERTIONS = json.loads((ROOT / "source_assertions.json").read_text())
assert len(ASSERTIONS) == 2103
ROWS = {r["id"]: dict(r, atomic_tests=[], related_tests=[]) for r in ASSERTIONS}
CASES = {}


def numbers(spec):
    if isinstance(spec, int):
        return [spec]
    if not isinstance(spec, str):
        return list(spec)
    out = []
    for part in spec.split(","):
        ends = list(map(int, part.split("-")))
        out.extend(range(ends[0], ends[-1] + 1))
    return out


def rows(file, spec):
    return [ROWS[f"{file}:{n:03}"] for n in numbers(spec)]


def exclude(file, spec, reason, category="implementation-only"):
    for r in rows(file, spec):
        r.update(classification=category, reason=reason)


# Explicit file-scope policy for assertions which have no faithful VCF oracle.
# Per-assertion exceptions below override these policies; descriptions and source
# excerpts are retained in full, including the exact getter/diagnostic inspected.
for r in ROWS.values():
    f = Path(r["file"]).stem
    if r["kind"] in {"use_ok", "require_ok"}:
        cat, reason = (
            "module-load",
            "Module loading/compilation has no input-VCF/output-VCF assertion.",
        )
    elif f.startswith("Haplo_"):
        cat, reason = (
            "different-product",
            "Haplosaurus groups phased variants into transcript haplotypes; its object/report assertion is outside per-variant VEP VCF annotation.",
        )
    elif f in {
        "Parser_CAID",
        "Parser_HGVS",
        "Parser_ID",
        "Parser_Region",
        "Parser_SPDI",
        "Parser_VEP_input",
    }:
        cat, reason = (
            "non-vcf-input",
            "Tests resolution/parsing of a non-VCF input syntax. Converting the input to VCF removes the behavior under test.",
        )
    elif f == "VariantRecoder":
        cat, reason = (
            "different-product",
            "Variant Recoder identifier/representation conversion is not a VEP annotation VCF result.",
        )
    elif f == "FilterSet":
        cat, reason = (
            "filter-language-unit",
            "Tests filter grammar/evaluation on synthetic hashes, including Perl undef/type distinctions. This is not an annotator VCF oracle; real transcript filtering is listed separately.",
        )
    elif f == "Stats":
        cat, reason = (
            "side-output",
            "Tests statistics counters or summary output, which are not part of the annotated VCF.",
        )
    elif f.startswith("OutputFactory_") and f != "OutputFactory_VCF":
        cat, reason = (
            "non-vcf-output",
            "Tests the named writer's JSON/tab/Extra-column representation. Observable shared annotation values are mapped separately below; this representation itself cannot be ported to VCF.",
        )
    elif r["kind"] in {"throws_ok", "dies_ok"}:
        cat, reason = (
            "error-contract",
            "Tests an exception/failure contract; a successful expected-output VCF cannot express the asserted error.",
        )
    elif re.search(
        r"warning|status_msg|STDERR|warning_msg|warns", r["description"], re.I
    ):
        cat, reason = (
            "diagnostic",
            "Tests a message or diagnostic channel, not VCF annotation content.",
        )
    else:
        cat, reason = (
            "implementation-only",
            "Tests internal state, a helper return value, object identity, setup, or resource handling as described; no faithful VCF-only assertion of that implementation detail. Observable analogs, when useful, are listed separately.",
        )
    r.update(classification=cat, reason=reason)

RECIPES = {
    "baseline": "21:25585733 C>T (rs142513484); take one row from t/testdata/test.vcf and preserve its sample columns when relevant.",
    "app": "21:25982445 C>T; final row of t/testdata/test.vcf.",
    "intron": "21:25587701 T>C (rs187353664).",
    "domains": "21:25587758 G>A (rs116645811).",
    "flags": "21:25588859 C>T (rs199510789).",
    "colocated": "21:25891796 C>T; keep only this input row.",
    "intergenic": "21:25832817 C>A; confirm it remains intergenic in release-116 cache.",
    "mirna_stem": "21:25573985 C>T; confirm release-116 miRNA hairpin overlap.",
    "mirna_loop": "21:25573994 C>T; confirm release-116 miRNA loop overlap.",
    "mirna_ins": "21:25573987 C>CG; confirm release-116 miRNA hairpin overlap.",
    "motif": "21:25735354 C>T; confirm release-116 motif overlap and PWM.",
    "regulatory": "21:25734924 C>T; confirm release-116 promoter overlap.",
    "reg_count": "21:25606638 A>G (rs3989369); confirm release-116 regulatory overlap.",
    "deletion": "21:25585732 GC>G; ordinary anchored one-base deletion.",
    "mismatch_del": "21:25587759 AC>A; preserve intentional REF mismatch (GRCh38 reference here is TA), do not repair REF.",
    "mismatch_ins": "21:25587759 A>AC; preserve original REF; check/reference discrepancy is part of provenance, not silently corrected.",
    "minimal": "21:25741665 CAGAAGAAAG>TAGAAGAAAG,C; retain raw multi-ALT source for original rejoin/index behavior.",
    "same_alt": "21:25606454 G>GC,C; two different original ALTs produce the same reduced allele.",
    "refeqalt": "Two ordered rows: 21:25585733 C>C, then 22:25769065 G>T (t/testdata/test6.vcf).",
    "alt_dot": "Four ordered rows: 21:25587759 C>A; 25587760 C>.; 25587761 C>.; 25587762 C>A. Preserve source REF spelling.",
    "whole": "t/testdata/test.vcf (132 rows): reduce to the smallest ordered subset which preserves this one property; pin row IDs and normalized input hash.",
    "source": "Extract only the input/configuration of this linked assertion, including preceding setup. Resolve any synthetic object/cache values to a real release-116 witness before calling it a runnable fixture.",
    "synthetic": "A synthetic Perl object is the stimulus. Find a release-116 cache witness with the stated property (or explicitly provide a custom annotation source); no concrete valid-VCF witness is established yet.",
}

API_FLAGS = set(
    "shift_hgvs distance gencode_basic gencode_primary all_refseq exclude_predicted pick pick_allele per_gene pick_allele_gene flag_pick flag_pick_allele flag_pick_allele_gene pick_order failed fields allow_non_variant".split()
)
EVERYTHING_FLAGS = set(
    "everything hgvs hgvsc hgvsp sift_b polyphen_b ccds symbol numbers domains regulatory canonical protein biotype af af_1kg af_gnomad af_gnomadg max_af pubmed uniprot mane tsl appris variant_class gene_phenotype mirna check_existing".split()
)


def flag_status(flags, scope, header=False, raw=False):
    entries = []
    for flag in flags:
        key = flag.removeprefix("--").split("=", 1)[0].split(" ", 1)[0]
        if key in EVERYTHING_FLAGS:
            continue
        if key in API_FLAGS:
            entries.append(
                f"Harness/CLI gap: {flag} has a vepyr Python API option; fixture schema must expose it."
            )
        elif key in {"refseq", "merged"}:
            entries.append(
                f"Harness gap: {key} cache flavour is supported by vepyr; runner currently accepts Ensembl only."
            )
        elif key in {"fork", "workers"}:
            entries.append(
                "Harness gap: vepyr workers is supported; add run overrides and indexed input."
            )
        elif key == "buffer_size":
            continue
        elif key == "keep_csq":
            entries.append(
                "Harness/API mapping to verify: vepyr skip_csq=False is exposed, but equivalence to VEP --keep_csq is not established."
            )
        else:
            entries.append(
                f"No public vepyr annotate/CLI equivalent found for {flag}; engine-only capability unverified."
            )
    if scope == "SV/repeat":
        entries.append(
            "Outside SNV/small-indel priority; structural/repeat parity needs separate verification."
        )
    if header:
        entries.append(
            "Harness gap: body-MD5 comparator ignores headers; add semantic header assertions."
        )
    if raw:
        entries.append(
            "Normalization conflict: -m -both removes joint ALT indexing/rejoin behavior; original test requires a separate unsplit-input lane."
        )
    return (
        " ".join(dict.fromkeys(entries))
        or "None identified for --everything parity; runtime unverified."
    )


def implementation(file):
    if file.startswith("OutputFactory"):
        name = "OutputFactory/VCF" if file == "OutputFactory_VCF" else "OutputFactory"
    elif (
        file.startswith("AnnotationSource_Cache_Variation")
        or file == "AnnotationSource_Database_Variation"
    ):
        name = "AnnotationType/Variation"
    elif file.endswith("Transcript") and file.startswith("AnnotationSource"):
        name = "AnnotationType/Transcript"
    elif file.startswith("AnnotationSource_File_"):
        name = "AnnotationSource/File/" + file.split("File_", 1)[1].replace(
            "_phase2", ""
        )
    elif file.startswith("AnnotationSource_"):
        name = file.replace("_", "/")
    else:
        name = file.replace("_", "/")
    return "modules/Bio/EnsEMBL/VEP/" + name + ".pm"


def add(
    file,
    spec,
    checks,
    *,
    recipe="source",
    scope="SNV",
    flags=(),
    mode="projection",
    old=True,
    raw=False,
    header=False,
    bug=None,
    key=None,
    needs=None,
):
    """Each check is one independently observable property; never a whole hash."""
    src = rows(file, spec)
    if isinstance(checks, str):
        checks = [checks]
    for check in checks:
        signature = json.dumps(
            [check, recipe, scope, flags, raw, header], sort_keys=True
        )
        cid = (
            key
            if key and len(checks) == 1
            else "DT-" + hashlib.sha256(signature.encode()).hexdigest()[:10]
        )
        if cid not in CASES:
            witness = needs or (
                "release-116 witness required"
                if recipe == "synthetic"
                else "extract/minimize source input"
                if recipe in {"source", "whole"}
                else "input recipe identified; verify release-116 witness"
            )
            CASES[cid] = dict(
                id=cid,
                description=check,
                variant_class=scope,
                input_recipe=RECIPES.get(recipe, recipe),
                vep_flags=["--everything", *flags],
                source_assertions=[],
                source_links=[],
                implementation_paths=[],
                old_cache_problem=OLD if old else NO_OLD,
                potential_vepyr_bug=bug or UNKNOWN_BUG,
                unsupported_features=flag_status(flags, scope, header, raw),
                witness_status=witness,
                oracle_status="NOT GENERATED: run VEP 116.2",
                raw_multiallelic_required=raw,
                header_comparison_required=header,
                relation=mode,
            )
        c = CASES[cid]
        for r in src:
            if cid not in r["atomic_tests"]:
                r["atomic_tests"].append(cid)
            if r["id"] not in c["source_assertions"]:
                c["source_assertions"].append(r["id"])
                c["source_links"].append(r["source_url"])
            path = implementation(file)
            if path not in c["implementation_paths"]:
                c["implementation_paths"].append(path)
            r.update(
                classification="vcf-direct" if mode == "direct" else "vcf-projection",
                reason="Port the listed observable VCF properties. "
                + (
                    "The original assertion is a VCF/body check."
                    if mode == "direct"
                    else "This projects an object/helper/other-format assertion to VCF; it does not test Perl representation, backend choice, or object identity."
                ),
            )
    return cid


def alias(file, spec, target_file, target_spec, *, reason=None):
    targets = list(
        dict.fromkeys(
            t for r in rows(target_file, target_spec) for t in r["atomic_tests"]
        )
    )
    assert targets, (file, spec, target_file, target_spec)
    for r in rows(file, spec):
        for cid in targets:
            if cid not in r["atomic_tests"]:
                r["atomic_tests"].append(cid)
            c = CASES[cid]
            if r["id"] not in c["source_assertions"]:
                c["source_assertions"].append(r["id"])
                c["source_links"].append(r["source_url"])
        r.update(
            classification="vcf-shared-projection",
            reason=reason
            or "Same observable property is already listed; share its atomic fixture. The source's backend/format/internal representation is not thereby covered.",
        )


def related(file, spec, target_file, target_spec, reason):
    for r in rows(file, spec):
        r["related_tests"] = list(
            dict.fromkeys(
                t for x in rows(target_file, target_spec) for t in x["atomic_tests"]
            )
        )
        r["reason"] += " " + reason


# Core annotation fields: full hash assertions become one row per field.
OF = "OutputFactory"
add(
    OF,
    8,
    "Report allele-matched Existing_variation for the baseline SNV.",
    recipe="baseline",
)
add(
    OF,
    9,
    "Report the selected frequency-filter population in FREQS.",
    recipe="synthetic",
    flags=["--check_frequency"],
)
add(
    OF,
    10,
    [
        "Merge colocated identifiers in VEP order.",
        "Merge colocated CLIN_SIG values.",
        "Align PHENO values with colocated identifiers.",
    ],
    recipe="colocated",
)
add(
    OF,
    11,
    "Report PUBMED citations for a known SNV.",
    recipe="21:25272769 C>T; verify cache-116 citation-bearing witness.",
)
add(
    OF,
    12,
    "Align SOMATIC flags with colocated identifiers.",
    recipe="21:25891785 G>A; do not substitute G>T from the broad fixture.",
)
add(
    OF,
    13,
    ["Preserve the input SNV ID column.", "Preserve the input SNV POS column."],
    recipe="baseline",
    old=False,
)
add(
    OF,
    14,
    "Report VARIANT_CLASS=SNV for a single-base substitution.",
    recipe="baseline",
    old=False,
)
add(
    OF,
    15,
    "Report overlapping known structural-variant identifiers.",
    recipe="synthetic",
    flags=["--check_svs"],
    scope="SV/repeat",
)
add(
    OF,
    16,
    ["Report the selected sample in IND.", "Report the selected sample's ZYG value."],
    flags=["--individual all"],
)
add(
    OF,
    17,
    [
        "Report a variant-wide custom value.",
        "Omit an allele-specific custom value on an unmatched ALT.",
        "Report a custom value on its matched ALT.",
    ],
    recipe="synthetic",
    flags=["--custom"],
)
add(
    OF,
    18,
    "Report the nearest feature identifier in NEAREST.",
    recipe="synthetic",
    flags=["--nearest"],
)
add(
    OF,
    19,
    "Report an IUPAC ambiguity code for a two-base allele pair.",
    recipe="21:25607429 A>G with sample dave GT 0/1.",
    flags=["--ambiguity", "--individual all"],
)
for n, check, flag in [
    (21, "Select one transcript using the default pick ranking.", "--pick"),
    (
        22,
        "Select one transcript using consequence-first pick_order.",
        "--pick --pick_order rank",
    ),
    (23, "Resolve a pick tie using APPRIS rank.", "--pick"),
    (24, "Resolve a pick tie using canonical/biotype priorities.", "--pick"),
    (25, "Select the fallback transcript when no candidate is canonical.", "--pick"),
    (26, "Keep one selected transcript per gene.", "--per_gene"),
    (
        27,
        "Apply custom pick_order independently within each gene.",
        "--per_gene --pick_order rank",
    ),
    (29, "Preserve all consequence entries when no pick filter is active.", None),
    (32, "Preserve consequence entry count with flag_pick.", "--flag_pick"),
    (
        33,
        "Mark only the selected consequence with PICK under flag_pick.",
        "--flag_pick",
    ),
    (34, "Select one consequence per original ALT with pick_allele.", "--pick_allele"),
    (
        35,
        "Preserve consequence entry count with flag_pick_allele.",
        "--flag_pick_allele",
    ),
    (
        36,
        "Mark the selected consequence independently for each original ALT.",
        "--flag_pick_allele",
    ),
    (37, "Select one consequence per original ALT and gene.", "--pick_allele_gene"),
    (
        38,
        "Preserve consequence entry count with flag_pick_allele_gene.",
        "--flag_pick_allele_gene",
    ),
    (39, "Mark one PICK entry per original ALT and gene.", "--flag_pick_allele_gene"),
]:
    add(
        OF,
        n,
        check,
        recipe="baseline" if n in {21, 26, 29, 32, 33} else "source",
        flags=[flag] if flag else [],
        raw=n in range(34, 40),
    )
alias(OF, 30, OF, 21)
alias(OF, 31, OF, 26)
add(
    OF,
    40,
    "Emit the baseline SNV's transcript consequence set once each.",
    recipe="baseline",
)
add(
    OF,
    41,
    "Exclude non-coding consequence entries under coding_only.",
    recipe="baseline",
    flags=["--coding_only"],
)
add(
    OF,
    42,
    "Emit an intergenic consequence for a SNV with no feature overlap.",
    recipe="intergenic",
)
add(
    OF,
    43,
    "Suppress intergenic consequences under no_intergenic.",
    recipe="intergenic",
    flags=["--no_intergenic"],
)
add(
    OF,
    44,
    "Exclude reference-homozygous sample annotations under individual mode.",
    flags=["--individual all"],
)
add(
    OF,
    45,
    "Retain reference-homozygous sample annotations with process_ref_homs.",
    flags=["--individual all", "--process_ref_homs"],
)
add(
    OF,
    46,
    "Emit only the most severe consequence term.",
    recipe="baseline",
    flags=["--most_severe"],
)
add(
    OF,
    47,
    "Emit the distinct ordered consequence-term summary.",
    recipe="baseline",
    flags=["--summary"],
)
add(
    OF,
    48,
    [
        f"Report {field} for the baseline missense allele."
        for field in ["Consequence", "IMPACT", "Allele"]
    ],
    recipe="baseline",
)
add(
    OF,
    49,
    "Report ALLELE_NUM for a biallelic SNV.",
    recipe="baseline",
    flags=["--allele_number"],
)
alias(OF, 50, OF, 33)
add(OF, 51, "Report HGVSg for the baseline SNV.", recipe="baseline", flags=["--hgvsg"])
add(
    OF,
    52,
    "Select the custom-annotation value belonging to each consequence allele.",
    recipe="synthetic",
    flags=["--custom"],
)
add(OF, 53, "Report global AF for the matched ALT.", recipe="baseline")
add(
    OF, 54, "Leave AF empty when the ALT has no matching frequency.", recipe="synthetic"
)
add(
    OF,
    55,
    "Compute a reference-derived frequency by complementing alternate frequencies.",
    recipe="synthetic",
    needs="Find a real reverse-reference/allele-matching cache witness; REF==ALT is not a faithful stimulus.",
)
add(
    OF,
    56,
    [
        f"Report {pop}_AF for the matched ALT."
        for pop in ["AFR", "AMR", "EAS", "EUR", "SAS"]
    ],
    recipe="baseline",
)
related(
    OF,
    57,
    OF,
    "53,56",
    "VCF carries genomic-forward alleles, so the negative input-strand branch itself is not exercised by these frequency fixtures.",
)
add(
    OF,
    58,
    [
        f"Report gnomADe{pop}_AF for the matched ALT."
        for pop in [
            "",
            "_AFR",
            "_AMR",
            "_ASJ",
            "_EAS",
            "_FIN",
            "_NFE",
            "_SAS",
            "_MID",
            "_REMAINING",
        ]
    ],
    recipe="baseline",
)
add(
    OF,
    59,
    [
        "Report MAX_AF across available populations.",
        "Report MAX_AF_POPS identifying the maximum-frequency population.",
    ],
    recipe="21:25594005 A>G; old MAX_AF 0.8223 is not a cache-116 expectation.",
)
add(
    OF,
    60,
    [
        f"Report {field} for the upstream transcript consequence."
        for field in ["DISTANCE", "STRAND", "Feature_type", "Feature", "Gene"]
    ],
    recipe="baseline",
)
add(
    OF,
    61,
    "Append transcript version to Feature under transcript_version.",
    recipe="baseline",
    flags=["--transcript_version"],
)
add(OF, 62, "Report cds_end_NF in FLAGS for an incomplete CDS.", recipe="flags")
add(OF, 63, "Report the exon ordinal and total in EXON.", recipe="baseline")
add(
    OF,
    64,
    [
        "Report the intron ordinal and total in INTRON.",
        "Leave EXON empty for an intronic consequence.",
    ],
    recipe="intron",
)
add(
    OF,
    65,
    "Report the overlapping protein-domain annotations in DOMAINS.",
    recipe="domains",
)
add(
    OF,
    66,
    [
        f"Report {field} for the transcript's gene."
        for field in ["SYMBOL", "SYMBOL_SOURCE", "HGNC_ID"]
    ],
    recipe="baseline",
)
add(
    OF,
    67,
    "Report GENE_PHENO for a gene with phenotype evidence.",
    recipe="21:25881766 G>A (rs145277462); verify release-116 gene phenotype evidence.",
)
add(
    OF,
    68,
    [
        f"Report {field} for the selected transcript."
        for field in ["CCDS", "ENSP", "CANONICAL", "BIOTYPE", "TSL"]
    ],
    recipe="baseline",
)
add(
    OF,
    69,
    "Report RefSeq cross-reference accessions on an Ensembl transcript.",
    recipe="baseline",
    flags=["--xref_refseq"],
)
add(
    OF,
    69,
    [f"Report {field} protein cross-references." for field in ["SWISSPROT", "UNIPARC"]],
    recipe="baseline",
)
add(OF, 70, "Report miRNA stem overlap for a SNV.", recipe="mirna_stem")
add(
    OF,
    71,
    "Report miRNA stem overlap for an insertion without duplicate hairpin entries.",
    recipe="mirna_ins",
    scope="small indel",
)
add(OF, 72, "Report miRNA loop overlap for a SNV.", recipe="mirna_loop")
for n, flag, field, desc in [
    (73, "refseq", "REFSEQ_MATCH", "a RefSeq cache"),
    (74, "merged", "REFSEQ_MATCH", "a merged cache"),
    (75, "merged", "SOURCE", "the RefSeq half of a merged cache"),
    (76, "merged", "SOURCE", "the Ensembl half of a merged cache"),
]:
    add(OF, n, f"Report {field} from {desc}.", recipe="baseline", flags=["--" + flag])
add(
    OF,
    77,
    [
        f"Report {field} for the coding transcript consequence."
        for field in [
            "Feature_type",
            "Feature",
            "Gene",
            "STRAND",
            "CDS_position",
            "cDNA_position",
            "Protein_position",
            "Amino_acids",
            "Codons",
        ]
    ],
    recipe="baseline",
)
alias(OF, 77, OF, 48)
add(
    OF,
    78,
    [
        f"Report {field} as position/total_length."
        for field in ["cDNA_position", "CDS_position", "Protein_position"]
    ],
    recipe="baseline",
    flags=["--total_length"],
)
add(OF, 79, "Report HGVSc for the coding transcript consequence.", recipe="baseline")
add(OF, 80, "Report HGVSp for the coding transcript consequence.", recipe="baseline")
add(
    OF,
    81,
    [
        "Report the 3-prime-UTR consequence term.",
        "Report cDNA_position for the UTR consequence.",
        *[
            f"Leave {field} empty on the UTR consequence."
            for field in ["CDS_position", "Protein_position", "Amino_acids", "Codons"]
        ],
    ],
    recipe="baseline",
)
add(
    OF,
    82,
    "Report zero SHIFT_LENGTH when HGVS placement is unchanged.",
    recipe="baseline",
    flags=["--shift_hgvs 1"],
)
for pred in ["SIFT", "PolyPhen"]:
    for setting, desc in [
        ("b", "prediction and score as one formatted field"),
        ("p", "prediction only"),
        ("s", "score only"),
    ]:
        add(
            OF,
            83,
            f"Report {pred} {desc}.",
            recipe="baseline",
            flags=[] if setting == "b" else [f"--{pred.lower()} {setting}"],
        )
add(
    OF,
    84,
    [
        f"Report {field} for a promoter regulatory consequence."
        for field in [
            "IMPACT",
            "Consequence",
            "Feature_type",
            "Feature",
            "BIOTYPE",
            "Allele",
        ]
    ],
    recipe="regulatory",
)
add(
    OF,
    85,
    "Report HUVEC activity in CELL_TYPE for a regulatory overlap.",
    recipe="regulatory",
    flags=["--cell_type HUVEC"],
)
add(
    OF,
    86,
    [
        f"Report {field} for a motif consequence."
        for field in [
            "IMPACT",
            "Consequence",
            "Feature_type",
            "Feature",
            "Allele",
            "STRAND",
            "MOTIF_NAME",
            "MOTIF_POS",
            "HIGH_INF_POS",
            "MOTIF_SCORE_CHANGE",
            "TRANSCRIPTION_FACTORS",
        ]
    ],
    recipe="motif",
)
add(
    OF,
    87,
    [
        "Report MODIFIER impact for an intergenic SNV.",
        "Report the input ALT as the intergenic CSQ allele.",
        *[
            f"Leave {field} empty for an intergenic consequence."
            for field in ["Feature", "Feature_type", "Gene"]
        ],
    ],
    recipe="intergenic",
)
alias(OF, 87, OF, 42)

# Structural cases are retained in the full list, after the requested two waves.
SVRECIPE = "Use the linked assertion's symbolic duplication at 21:25606614; recover END from its setup, and keep symbolic ALT plus span INFO intact."
add(
    OF,
    88,
    "Use END to determine the structural variant annotation span.",
    recipe=SVRECIPE,
    scope="SV/repeat",
)
for n, check, flag in [
    (89, "Select the worst structural-overlap consequence.", "--pick"),
    (90, "Select one structural-overlap consequence per gene.", "--per_gene"),
    (91, "Retain all structural-overlap consequence entries by default.", None),
    (92, "Select one structural-overlap consequence under pick.", "--pick"),
    (
        93,
        "Select one structural-overlap consequence under pick_allele.",
        "--pick_allele",
    ),
    (94, "Retain all structural consequence entries under flag_pick.", "--flag_pick"),
    (95, "Set PICK on one selected structural consequence.", "--flag_pick"),
    (
        96,
        "Retain structural consequence entries under flag_pick_allele.",
        "--flag_pick_allele",
    ),
    (97, "Set PICK per structural ALT.", "--flag_pick_allele"),
    (
        98,
        "Retain entries under structural flag_pick_allele_gene.",
        "--flag_pick_allele_gene",
    ),
    (
        99,
        "Set PICK independently for structural transcript and regulatory groups.",
        "--flag_pick_allele_gene",
    ),
    (100, "Emit each structural-overlap consequence once.", None),
    (101, "Filter structural overlaps to coding transcripts.", "--coding_only"),
    (102, "Emit an intergenic structural consequence.", None),
    (103, "Suppress intergenic structural consequences.", "--no_intergenic"),
]:
    add(OF, n, check, recipe=SVRECIPE, scope="SV/repeat", flags=[flag] if flag else [])
for n, fields in [
    (104, ["IMPACT", "Consequence", "Allele"]),
    (106, ["Feature_type", "Feature", "OverlapBP", "OverlapPC"]),
    (109, ["motif Feature_type", "motif OverlapPC"]),
    (
        110,
        ["regulatory Feature_type", "regulatory OverlapPC", "regulatory Consequence"],
    ),
    (112, ["intergenic IMPACT", "intergenic Consequence", "intergenic Allele"]),
    (113, ["CDS_position", "cDNA_position", "Protein_position"]),
]:
    for field in fields:
        add(
            OF,
            n,
            f"Report {field} for a symbolic duplication.",
            recipe=SVRECIPE,
            scope="SV/repeat",
            flags=["--overlaps"] if "Overlap" in field else [],
        )
add(
    OF,
    "105,107",
    "Report ALLELE_NUM for a structural consequence.",
    recipe=SVRECIPE,
    scope="SV/repeat",
    flags=["--allele_number"],
)
add(
    OF,
    108,
    "Set PICK on the selected structural transcript entry.",
    recipe=SVRECIPE,
    scope="SV/repeat",
    flags=["--flag_pick"],
)
alias(OF, 108, OF, 106)
add(
    OF,
    111,
    "Report CELL_TYPE on a structural regulatory consequence.",
    recipe=SVRECIPE,
    scope="SV/repeat",
    flags=["--cell_type HUVEC"],
)
add(
    OF,
    116,
    "Rejoin minimalized ALTs to one output row per original record.",
    recipe="minimal",
    scope="small indel",
    flags=["--minimal"],
    raw=True,
)
add(
    OF,
    117,
    "Restore original ALT spelling after minimal-allele annotation.",
    recipe="minimal",
    scope="small indel",
    flags=["--minimal"],
    raw=True,
)
for allele, fields in [
    (
        "T",
        [
            "Allele",
            "Consequence",
            "cDNA_position",
            "CDS_position",
            "Protein_position",
            "Amino_acids",
            "Codons",
        ],
    ),
    (
        "deletion",
        [
            "Allele",
            "Consequence",
            "cDNA_position",
            "CDS_position",
            "Protein_position",
            "Amino_acids",
            "Codons",
            "SHIFT_LENGTH",
        ],
    ),
]:
    add(
        OF,
        118,
        [
            f"Report {field} on the {allele} ALT after minimal annotation and rejoin."
            for field in fields
        ],
        recipe="minimal",
        scope="small indel",
        flags=["--minimal"],
        raw=True,
    )
add(
    OF,
    120,
    "Preserve the original intergenic record count after minimal rejoin.",
    scope="small indel",
    flags=["--minimal"],
    raw=True,
)
add(
    OF,
    122,
    "Preserve intergenic consequences after minimal rejoin.",
    scope="small indel",
    flags=["--minimal"],
    raw=True,
)
add(
    OF,
    123,
    "Keep the common CSQ allele spelling when different original ALTs reduce identically.",
    recipe="same_alt",
    scope="small indel",
    flags=["--minimal"],
    raw=True,
)
for n, term in [(124, "frameshift"), (125, "missense")]:
    add(
        OF,
        n,
        f"Map the {term} consequence to its original ALLELE_NUM despite identical reduced alleles.",
        recipe="same_alt",
        scope="small indel",
        flags=["--minimal", "--allele_number"],
        raw=True,
    )
for n, desc in [
    (127, "Include the declared custom annotation source in the VCF header."),
    (128, "Preserve the order of two distinct custom source declarations."),
    (129, "Merge paths for custom sources sharing a short_name."),
    (130, "Declare a plugin-added CSQ field in the VCF header."),
]:
    add(
        OF,
        n,
        desc,
        recipe="baseline",
        flags=["--plugin TestPlugin"] if n == 130 else ["--custom"],
        header=True,
        old=False,
    )
add(
    OF,
    131,
    "Emit the TestPlugin value on the corresponding consequence entry.",
    recipe="baseline",
    flags=["--plugin TestPlugin"],
    needs="Requires a supported equivalent to the Perl TestPlugin, not an arbitrary existing plugin cache.",
)
for n, value in [(137, 1), (138, 0)]:
    add(
        OF,
        n,
        "Report HGVSg duplication notation with shift_hgvs=" + str(value) + ".",
        recipe="21:25592985 A>ATAAA",
        scope="small indel",
        flags=["--hgvsg", f"--shift_hgvs {value}"],
    )
    add(
        OF,
        n,
        "Report the intronic consequence for the TAAA insertion.",
        recipe="21:25592985 A>ATAAA",
        scope="small indel",
    )
    add(
        OF,
        n,
        "Report the unanchored TAAA insertion allele in CSQ.",
        recipe="21:25592985 A>ATAAA",
        scope="small indel",
    )
for n, desc in [
    (140, "Report sorted per-sample zygosities for all samples."),
    (142, "Report only requested samples in the sorted zygosity list."),
    (143, "Report homozygous-reference zygosity for an ALT-dot record."),
]:
    add(
        OF,
        n,
        desc,
        flags=["--individual_zyg all" if n != 142 else "--individual_zyg dave,barry"],
        scope="nonvariant" if n == 143 else "SNV",
    )
for n, alt, desc in [(144, "C", "deletion"), (145, "CTT", "duplication")]:
    add(
        OF,
        n,
        f"Report HGVSg {desc} for the corresponding ALT.",
        recipe=f"21:25769083 CT>{alt}; derived from original C,CTT row, preserve original as provenance.",
        scope="small indel",
        flags=["--hgvsg"],
    )

VCF = "OutputFactory_VCF"
for n, checks in [
    (5, ["Emit a valid VCF fileformat header.", "Declare the CSQ INFO field."]),
    (
        6,
        [
            "Declare a plugin-added CSQ field in VCF output.",
            "Preserve the sample FORMAT column header with a plugin enabled.",
        ],
    ),
    (7, ["Declare RefSeq-specific CSQ columns in the header."]),
    (8, ["Emit the ordered default CSQ field schema."]),
    (9, ["Declare SIFT in the CSQ schema when enabled."]),
    (10, ["Declare SOURCE only once with merged cache plus custom source."]),
    (11, ["Emit only the requested ordered CSQ fields."]),
]:
    flags = (
        ["--plugin TestPlugin"]
        if n == 6
        else ["--refseq"]
        if n == 7
        else ["--fields core"]
        if n == 8
        else ["--merged", "--custom"]
        if n == 10
        else ["--fields Allele,Consequence"]
        if n == 11
        else []
    )
    add(
        VCF,
        n,
        checks,
        recipe="baseline",
        flags=flags,
        header=True,
        old=False,
        mode="direct",
    )
add(
    VCF,
    13,
    "Place the Allele value in the first CSQ field.",
    recipe="intergenic",
    old=False,
)
exclude(
    VCF,
    14,
    "The helper's negative-strand argument is not expressible as an input VCF strand. Forward-strand allele output is tested separately.",
)
add(
    VCF,
    15,
    "Represent a missing non-Allele value with an empty CSQ slot.",
    recipe="synthetic",
    old=False,
)
add(
    VCF,
    16,
    "Represent a sequence deletion as '-' in CSQ.Allele.",
    recipe="deletion",
    scope="small indel",
    old=False,
)
for n, char, action in [
    (17, "semicolon", "encode"),
    (18, "pipe", "replace"),
    (19, "comma", "replace"),
    (20, "whitespace", "collapse"),
]:
    add(
        VCF,
        n,
        f"{action.capitalize()} {char} characters inside an annotation value for VCF serialization.",
        recipe="synthetic",
        old=False,
        needs="Source injects an output value directly; needs a real cache/custom value carrying the character, not an invalid nucleotide ALT.",
    )
add(
    VCF,
    "21,22",
    "Preserve zero-valued CSQ annotations instead of treating them as missing.",
    recipe="domains",
    old=False,
)
add(
    VCF,
    23,
    "Preserve empty CSQ field positions using pipe delimiters.",
    recipe="intergenic",
    old=False,
)
add(
    VCF,
    24,
    "Emit one VCF row per input record despite multiple transcript consequences.",
    recipe="baseline",
    mode="direct",
    old=False,
)
for field in [
    "CHROM",
    "ID",
    "POS",
    "REF",
    "ALT",
    "QUAL",
    "FILTER",
    "FORMAT",
    "sample GT phasing",
]:
    add(
        VCF,
        25,
        f"Preserve the input {field} value during VCF annotation.",
        recipe="baseline",
        mode="direct",
        old=False,
    )
alias(VCF, 25, OF, "77,81")
add(
    VCF,
    26,
    [
        "Emit all APP missense transcript entries on one VCF row.",
        "Report cds_start_NF on the corresponding APP transcript.",
        "Report combined cds_start_NF/cds_end_NF on the corresponding APP transcript.",
    ],
    recipe="app",
    mode="direct",
)
add(
    VCF,
    26,
    [
        f"Report {field} on the APP missense consequence."
        for field in [
            "Consequence",
            "IMPACT",
            "Gene",
            "Feature",
            "Feature_type",
            "cDNA_position",
            "CDS_position",
            "Protein_position",
            "Amino_acids",
            "Codons",
            "STRAND",
        ]
    ],
    recipe="app",
    mode="direct",
)
exclude(
    VCF,
    27,
    "The five-column stub is not a valid eight-column VCF. Padding it before execution removes the parser-tolerance behavior.",
    "malformed-input",
)
alias(VCF, 28, VCF, "17-20")
alias(VCF, 29, OF, "8,14,48,53,56,58,60,63,66,68,69,77,79-81")
add(
    VCF,
    29,
    "Report APPRIS on the corresponding coding transcript.",
    recipe="baseline",
    mode="direct",
)
add(
    VCF,
    29,
    ["Report MAX_AF for the baseline SNV.", "Report MAX_AF_POPS for the baseline SNV."],
    recipe="baseline",
    mode="direct",
)
for pred in ["SIFT", "PolyPhen"]:
    add(
        VCF,
        29,
        f"Report {pred} prediction and score as one formatted field.",
        recipe="baseline",
        mode="direct",
    )
add(
    VCF,
    31,
    [
        "Emit the exact-matched custom VCF record ID.",
        "Emit the requested custom FOO value.",
    ],
    recipe="baseline",
    flags=["--custom"],
    mode="direct",
)
add(
    VCF,
    32,
    "Emit all coordinate-overlapping custom VCF record IDs.",
    recipe="21:25585733 CATG>TACG plus t/testdata/custom/test.vcf.gz; verify REF before use.",
    scope="MNV",
    flags=["--custom"],
    mode="direct",
)
add(
    VCF,
    33,
    "Report the original uploaded allele spelling in UPLOADED_ALLELE.",
    scope="small indel",
    flags=["--uploaded_allele"],
)
exclude(
    VCF,
    "34,35",
    "The assertion converts Ensembl-format input to VCF; feeding VCF bypasses the conversion being asserted.",
    "non-vcf-input",
)
for n, position in [(36, "middle"), (37, "first"), (38, "last")]:
    add(
        VCF,
        n,
        f"Replace an existing CSQ entry in the {position} INFO position.",
        recipe=f"Baseline SNV with a deliberately stale CSQ value in the {position} INFO position; include unrelated INFO entries and declarations.",
        mode="direct",
        old=False,
    )
add(
    VCF,
    39,
    "Retain the input CSQ value when keep_csq is requested.",
    recipe="baseline",
    flags=["--keep_csq"],
    mode="direct",
    old=False,
)
add(
    VCF,
    40,
    "Preserve BCSQ while writing VEP CSQ.",
    recipe="Baseline SNV with BCSQ=sentinel and matching INFO declaration.",
    mode="direct",
    old=False,
)
add(
    VCF,
    41,
    "Write annotation under a configured alternative INFO key.",
    recipe="baseline",
    flags=["--vcf_info_field EFF"],
    mode="direct",
    old=False,
)
add(
    VCF,
    42,
    ["Preserve declared INFO header lines.", "Preserve declared FORMAT header lines."],
    recipe="baseline",
    mode="direct",
    old=False,
    header=True,
)
exclude(
    VCF,
    43,
    "VEP implementation/version/command provenance cannot be required to equal vepyr provenance.",
    "implementation-specific-header",
)
add(
    VCF,
    44,
    "Emit no fabricated CSQ when a record has no consequences.",
    recipe="synthetic",
    old=False,
    needs="Original mutates vep_skip internally. Establish a naturally no-consequence valid record; do not assume REF==ALT has this output without a VEP run.",
)
add(VCF, 45, "Preserve structural END in INFO.", scope="SV/repeat", old=False)
add(
    VCF,
    46,
    ["Report structural OverlapBP in CSQ.", "Report structural OverlapPC in CSQ."],
    scope="SV/repeat",
    flags=["--overlaps"],
    mode="direct",
)
alias(VCF, 49, OF, 116)
alias(VCF, 50, OF, 117)
add(
    VCF,
    51,
    [
        "Preserve input contig header declarations.",
        "Preserve input ALT header declarations.",
        "Preserve input INFO header declarations in order.",
    ],
    recipe="baseline",
    mode="direct",
    old=False,
    header=True,
)
add(
    VCF,
    52,
    "Place the CSQ declaration before the final column header.",
    recipe="baseline",
    mode="direct",
    old=False,
    header=True,
)
add(
    VCF,
    53,
    "Preserve sample names and their order in the VCF column header.",
    recipe="baseline",
    mode="direct",
    old=False,
    header=True,
)
exclude(
    VCF,
    "54-56",
    "Tests web-output sidecar/JSON index behavior; the sidecar is not the output VCF.",
    "side-output",
)
add(
    VCF,
    57,
    "Retain the mitochondrial RefSeq transcript annotation across identifier conventions.",
    recipe="MT:12848 C>T; legacy numeric transcript 4558/rna-prefixed IDs must be requalified against cache 116.",
    flags=["--refseq", "--use_given_ref"],
    mode="direct",
)

RUN = "Runner"
alias(RUN, "16,17,53", OF, "48,60,77,81")
add(RUN, 55, "Include the default-distance upstream consequence.", recipe="baseline")
add(
    RUN,
    56,
    "Include additional upstream/downstream entries at distance=10000.",
    recipe="baseline",
    flags=["--distance 10000"],
)
add(
    RUN,
    57,
    "Report DISTANCE for a transcript newly included by distance=10000.",
    recipe="baseline",
    flags=["--distance 10000"],
)
add(
    RUN,
    58,
    "Apply separate upstream and downstream distance limits.",
    recipe="baseline",
    flags=["--distance 10000,20000"],
)
add(
    RUN,
    62,
    "Continue annotation after frequency filtering empties a one-record buffer.",
    recipe="Use the source's three ordered SNVs; buffer_size=1, filter_common and freq_freq=0.0012. Requalify a middle record which fails with cache 116.",
    flags=["--filter_common", "--freq_freq 0.0012", "--buffer_size 1"],
)
add(
    RUN,
    63,
    "Continue a forked run after frequency filtering empties a buffer.",
    recipe="Use the source's three ordered SNVs and requalify the cache-116 filter outcome.",
    flags=["--filter_common", "--freq_freq 0.0012", "--buffer_size 1", "--fork 2"],
)
add(
    RUN,
    83,
    "Annotate a SNV against a BAM-corrected transcript.",
    recipe="21:10524654 C>A; upstream bam_edit.gff plus bam_edit.bam and matching FASTA.",
    flags=["--gff", "--bam"],
)
add(
    RUN,
    84,
    "Preserve the ordered consequence identities across a multi-gene input.",
    recipe="Source's four VCF records over three genes; minimize only while retaining a gene boundary.",
)
for n, w, recipe in [
    (85, 2, "source"),
    (86, 4, "source"),
    (87, 2, "whole"),
    (89, 4, "whole"),
]:
    add(
        RUN,
        n,
        f"Preserve VCF body order and content with {w} workers on {'the multi-gene subset' if n < 87 else 'the HGVS-bearing source input'}.",
        recipe=recipe,
        flags=[f"--fork {w}"],
        old=False,
    )
add(
    RUN,
    100,
    "Continue to the next SNV after filtering a reference-homozygous sample record.",
    recipe="t/testdata/test5.vcf: reference-homozygous first record followed by 22:25769065 G>T.",
    flags=["--individual all"],
)
add(
    RUN,
    102,
    "Continue to the next SNV after a REF-equals-ALT record.",
    recipe="refeqalt",
    mode="projection",
    old=False,
    bug="Previously reported #207 REF==ALT annotation discrepancy; status on current vepyr versus VEP 116.2 is untested. https://github.com/biodatageeks/vepyr-porting-tests/issues/207",
)
add(
    RUN,
    104,
    "Annotate the reference-homozygous record in individual_zyg mode.",
    recipe="t/testdata/test5.vcf; isolate the first GT=0/0 row.",
    flags=["--individual_zyg all"],
)
add(
    RUN,
    105,
    "Annotate the heterozygous record once per consequence in individual_zyg mode.",
    recipe="t/testdata/test5.vcf; isolate 22:25769065 G>T.",
    flags=["--individual_zyg all"],
)
related(
    RUN,
    "51,99,101,103",
    RUN,
    102,
    "The source asserts run() truthiness, not VCF content. The linked continuation candidate is additional behavioral coverage, not a port of that return value.",
)
exclude(
    RUN,
    106,
    "Tests run_rest's Perl/REST schema. Shared annotation values have VCF candidates; REST serialization is not covered by them.",
    "non-vcf-output",
)
exclude(
    RUN,
    112,
    "GA4GH VRS object serialization is not a VCF annotation contract in this fixture format.",
    "non-vcf-output",
)

P = "Parser_VCF"
alias(P, 7, VCF, "42,51,53")
alias(P, 8, VCF, 25)
add(
    P, 10, "Retain the leading SNV before ALT-dot records.", recipe="alt_dot", old=False
)
add(
    P,
    11,
    "Continue to the trailing SNV after consecutive ALT-dot records.",
    recipe="alt_dot",
    old=False,
)
for n, recipe, check in [
    (
        12,
        "mismatch_del",
        "Annotate an anchored deletion as the deleted base, without its anchor.",
    ),
    (
        13,
        "mismatch_ins",
        "Annotate an anchored insertion as the inserted sequence, without its anchor.",
    ),
    (
        14,
        "21:25587759 AC>A with INFO SVTYPE=DEL; preserve original REF mismatch.",
        "Treat a literal deletion as a sequence allele despite INFO/SVTYPE.",
    ),
    (
        15,
        "21:25587759 A>AC with INFO SVLEN=1; preserve original REF mismatch.",
        "Treat a literal insertion as a sequence allele despite INFO/SVLEN.",
    ),
]:
    add(
        P,
        n,
        check,
        recipe=recipe,
        scope="small indel",
        old=False,
        bug="Reference-mismatch case: prior issue #221 describes missing coding output; VEP 116.2/current vepyr outcome untested. https://github.com/biodatageeks/vepyr-porting-tests/issues/221"
        if n == 12
        else None,
    )
for n, recipe, check, scope in [
    (16, "21:25587759 A>C,G", "Associate each SNV ALT with its own annotation.", "SNV"),
    (
        17,
        "21:25587759 A>C,GG",
        "Keep allele associations for a mixed SNV/insertion record without a common anchor.",
        "small indel",
    ),
    (
        18,
        "21:25587759 G>GC,GT",
        "Remove the shared anchor from both insertion alleles.",
        "small indel",
    ),
    (
        22,
        "21:25587759 G>C,*",
        "Handle a spanning-deletion star alongside a SNV.",
        "SNV",
    ),
    (
        23,
        "21:25587759 G>C,<DEL:*>",
        "Handle a symbolic spanning-deletion placeholder alongside a SNV.",
        "SNV",
    ),
    (
        24,
        "21:25587759 GC>G,*",
        "Handle a star placeholder alongside an anchored deletion.",
        "small indel",
    ),
    (
        25,
        "21:25587759 G>GC,*",
        "Handle a star placeholder alongside an anchored insertion.",
        "small indel",
    ),
    (
        27,
        "21:25587758 C>T,CAA",
        "Maintain original ALT associations when minimal mode defers multi-ALT splitting.",
        "small indel",
    ),
]:
    add(
        P,
        n,
        check,
        recipe=recipe,
        scope=scope,
        raw=True,
        flags=["--minimal"] if n == 27 else [],
        old=False,
    )
exclude(
    P,
    19,
    "A five-column stub is malformed VCF; adding required columns erases this parser-tolerance test.",
    "malformed-input",
)
add(
    P,
    20,
    "Retain an ALT-dot record when allow_non_variant is enabled.",
    recipe="21:25587759 G>.",
    scope="nonvariant",
    flags=["--allow_non_variant"],
    old=False,
)
add(
    P,
    21,
    "Skip annotation of an ALT-dot record by default.",
    recipe="21:25587759 G>.",
    scope="nonvariant",
    old=False,
)
add(
    P,
    26,
    "Trim the common prefix and suffix before annotating a padded substitution.",
    recipe="21:25587758 CAT>CCT",
    scope="MNV",
    flags=["--minimal"],
    old=False,
)
for n, checks in [
    (
        28,
        [
            "Classify symbolic DUP using SVTYPE.",
            "Derive the symbolic duplication span from END.",
        ],
    ),
    (29, ["Classify ALT-dot SV input using INFO/SVTYPE."]),
    (30, ["Infer duplication class from ALT when SVTYPE is absent."]),
    (31, ["Derive the symbolic duplication span from SVLEN."]),
    (
        32,
        [
            "Use CIPOS for uncertain start overlap.",
            "Use CIEND for uncertain end overlap.",
        ],
    ),
    (33, ["Classify symbolic DEL using its declared length."]),
    (40, ["Classify Alu insertion subtype."]),
    (41, ["Classify generic mobile-element insertion."]),
    (42, ["Classify mobile-element deletion."]),
    (43, ["Classify LINE1 deletion subtype."]),
    (44, ["Interpret CN=0 as deletion."]),
    (45, ["Interpret CN2 as duplication."]),
    (46, ["Interpret mixed copy-number alleles as CNV."]),
    (47, ["Interpret generic CNV symbolic ALT."]),
    (48, ["Ignore mate-chromosome END when locating a breakend."]),
    (49, ["Preserve breakend ALT allele associations."]),
    (50, ["Resolve a symbolic breakend's mate from CHR2/END2."]),
    (51, ["Resolve a symbolic breakend's mate from fallback CHR2/END."]),
    (52, ["Determine a single-breakend annotation span."]),
    (53, ["Classify a symbolic tandem repeat without sequence expansion."]),
    (54, ["Expand tandem-repeat units using RUC counts."]),
    (55, ["Expand tandem-repeat units using RB lengths."]),
    (56, ["Use N bases for missing repeat-unit sequence."]),
    (57, ["Determine tandem-repeat reference span from SVLEN when END is absent."]),
    (58, ["Select a shared reference span when ALT-specific SVLEN values differ."]),
    (60, ["Use the anchor reference when repeat END and SVLEN are absent."]),
    (61, ["Use implicit repeat-unit assignments when RN is absent."]),
    (62, ["Retain symbolic tandem-repeat annotation above the expansion limit."]),
]:
    add(
        P,
        n,
        checks,
        scope="SV/repeat",
        old=False,
        raw=n in {46, 49, 53, 54, 55, 56, 57, 58, 60, 61},
    )
add(
    P,
    36,
    "Continue to a SNV after skipping an incomplete symbolic deletion.",
    scope="SV/repeat",
    old=False,
)
add(
    P,
    38,
    "Continue to a SNV after skipping an oversized structural variant.",
    scope="SV/repeat",
    flags=["--max_sv_size 1000"],
    old=False,
)
add(P, 63, "Annotate at the coordinate supplied by INFO/GP.", flags=["--gp"], old=False)
for n, checks in [
    (
        65,
        [
            "Report the selected heterozygous sample's IND.",
            "Report the selected heterozygous sample's ZYG.",
        ],
    ),
    (66, ["Report homozygous-alt zygosity for the selected sample."]),
    (67, ["Suppress reference-homozygous annotation in individual mode."]),
    (68, ["Annotate reference-homozygous input with process_ref_homs."]),
    (69, ["Continue after a selected sample has a missing genotype."]),
    (70, ["Map homozygous insertion GT to the unanchored CSQ allele."]),
    (71, ["Map homozygous deletion GT to the unanchored CSQ allele."]),
    (72, ["Report all samples' zygosities on one variant row."]),
    (73, ["Retain a record with missing GT in individual_zyg mode."]),
    (74, ["Continue to the next called record in individual_zyg mode."]),
    (75, ["Report HOM for the selected homozygous-alt genotype."]),
    (76, ["Report HOMREF for a phased reference-homozygous genotype."]),
]:
    flags = ["--individual_zyg all"] if n >= 72 else ["--individual all"]
    if n == 68:
        flags.append("--process_ref_homs")
    add(
        P,
        n,
        checks,
        scope="small indel" if n in {70, 71} else "SNV",
        flags=flags,
        old=False,
    )
exclude(
    P,
    "77,78",
    "POS=foo cannot be expressed as a valid VCF. This is malformed-input tolerance, not a normalized valid-VCF annotation test.",
    "malformed-input",
)

PBASE = "Parser"
for n, desc in [
    (18, "Trim a shared prefix in minimal mode."),
    (19, "Trim both shared ends in minimal mode."),
    (20, "Trim long shared context in minimal mode."),
]:
    add(PBASE, n, desc, recipe="synthetic", scope="MNV", flags=["--minimal"], old=False)
add(
    PBASE,
    33,
    "Use uppercase alleles for annotation when input bases are lowercase.",
    recipe="Baseline SNV with lowercase REF=c and ALT=t; first check whether bcftools normalization preserves this stimulus.",
    old=False,
    needs="Normalization may uppercase the input; preserve proof that the case reaches the annotators.",
)
for n, recipe, desc in [
    (
        35,
        "chr21:25585733 C>T",
        "Resolve a chr-prefixed input against bare-contig annotation data.",
    ),
    (
        36,
        "21:25585733 C>T against chr-prefixed custom data",
        "Resolve a bare input contig against a chr-prefixed source.",
    ),
    (38, "M:4472 T>A", "Resolve M as mitochondrial MT for annotation."),
    (
        41,
        "NC_000021.9:25585733 C>T",
        "Resolve a RefSeq chromosome accession through synonyms.",
    ),
]:
    add(PBASE, n, desc, recipe=recipe, old=False)
related(
    PBASE,
    "39,42",
    PBASE,
    "38,41",
    "Internal chromosome rewriting is not the VCF CHROM preservation contract; related alias-annotation cases are listed without equating the two.",
)
add(
    PBASE,
    32,
    "Exclude annotation for a contig absent from the selected chromosome set.",
    recipe="One valid SNV on 21 and one on 22; select only 21.",
    flags=["--chr 21"],
    old=False,
)
for n, recipe, desc, scope in [
    (
        57,
        "21:25585733 C>CT",
        "Accept an anchored insertion under check_ref.",
        "small indel",
    ),
    (58, "baseline", "Accept a matching SNV REF under check_ref.", "SNV"),
    (
        59,
        "21:25585733 G>T",
        "Reject annotation for a mismatching SNV REF under check_ref.",
        "SNV",
    ),
    (
        61,
        "21:25585733 CTT>C",
        "Validate every base of a matching multi-base REF.",
        "small indel",
    ),
    (
        62,
        "21:25585733 TTT>T",
        "Reject a multi-base REF mismatch beyond the first base.",
        "small indel",
    ),
]:
    add(PBASE, n, desc, recipe=recipe, scope=scope, flags=["--check_ref"], old=False)
add(
    PBASE,
    "64,65",
    "Use FASTA reference bases for annotation under lookup_ref.",
    recipe="21:25585733 N>T; retain original N in provenance.",
    flags=["--lookup_ref"],
    old=False,
)
add(
    PBASE,
    "68,69",
    "Backfill an unknown deletion REF under lookup_ref.",
    recipe="21:25585732 GN>G; pad the source's N/- at 25585733 using the true preceding anchor.",
    scope="small indel",
    flags=["--lookup_ref"],
    old=False,
)
add(
    PBASE,
    "70,71",
    "Leave the inserted sequence unchanged under lookup_ref.",
    recipe="21:25585733 C>CT",
    scope="small indel",
    flags=["--lookup_ref"],
    old=False,
)
add(
    PBASE,
    75,
    "Read CRLF-terminated VCF records without losing their annotation.",
    recipe="Baseline VCF encoded with CRLF; retain an explicit post-normalization encoding step.",
    old=False,
)
exclude(
    PBASE,
    "76-92",
    "Tests non-VCF format sniffing or an incomplete record; a normalized VCF cannot exercise the input-format distinction.",
    "non-vcf-input",
)
for spec, scope, desc in [
    (
        "100-102",
        "small indel",
        "Annotate an insertion supplied on an assembly component contig.",
    ),
    (
        "103-105",
        "SNV",
        "Annotate a substitution supplied on an assembly component contig.",
    ),
]:
    add(
        PBASE,
        spec,
        desc,
        flags=["--database"],
        scope=scope,
        needs="Requires a release-matched assembly-component mapping and FASTA; using the already-mapped chr21 input would bypass the asserted transform.",
    )
for spec, desc in [
    (
        "110,111",
        "Include the LRG projection when annotation starts on chromosome coordinates.",
    ),
    (
        "112,113",
        "Include the chromosome projection when annotation starts on LRG coordinates.",
    ),
]:
    add(PBASE, spec, desc, flags=["--lrg", "--database"])

IB = "InputBuffer"
add(
    IB,
    17,
    "Preserve input order across a buffer boundary.",
    recipe="Use the first 11 source SNVs with buffer_size=10; the eleventh record must follow the first ten.",
    flags=["--buffer_size 10"],
    old=False,
)
add(
    IB,
    56,
    "Emit an intergenic consequence when no annotation source claims a variant.",
    recipe="intergenic",
)
for spec, desc in [
    ("61,64,67", "Preserve variant order across chromosome boundaries."),
    ("70", "Annotate the SNV ALT independently after minimal splitting."),
    ("72", "Preserve a non-minimisable joint ALT representation."),
]:
    add(
        IB,
        spec,
        desc,
        flags=["--minimal"] if spec in {"70", "72"} else [],
        raw=spec in {"70", "72"},
        scope="small indel" if spec in {"70", "72"} else "SNV",
        old=False,
    )
# Boundary helpers are projected only with an explicit real-feature witness gap.
for spec, desc, scope in [
    (
        "23,36",
        "Include a feature overlap at an exactly coincident SNV coordinate.",
        "SNV",
    ),
    ("24,37", "Include an overlap touching the feature's last base.", "SNV"),
    ("25,38", "Include an overlap touching the feature's first base.", "SNV"),
    ("26,39", "Include a SNV strictly inside a feature span.", "SNV"),
    ("29,42", "Exclude an overlap one base before a feature.", "SNV"),
    ("30,43", "Exclude an overlap one base after a feature.", "SNV"),
    (
        "33,46",
        "Exclude an insertion from a feature touching only its left flank.",
        "small indel",
    ),
    (
        "34,47",
        "Exclude an insertion from a feature touching only its right flank.",
        "small indel",
    ),
    (
        "35,48",
        "Include an insertion when the feature spans both flanks.",
        "small indel",
    ),
    (
        "31,44",
        "Find a long structural overlap from an interior 3-Mb query.",
        "SV/repeat",
    ),
    (
        "32,45",
        "Find a long structural overlap from an interior 5-Mb query.",
        "SV/repeat",
    ),
]:
    add(
        IB,
        spec,
        desc,
        recipe="synthetic",
        scope=scope,
        old=False,
        needs="Synthetic interval query: find release-116 feature boundaries or declare a controlled custom feature; public output only tests the overlap outcome, not the tree/fallback implementation.",
    )

CV = "AnnotationSource_Cache_Variation"
for n, desc, flags in [
    (5, "Include a non-failed cache record in Existing_variation.", []),
    (6, "Exclude a failed cache record by default.", []),
    (7, "Include failed cache records when requested.", ["--failed"]),
    (9, "Match a cache variant by the same forward-strand ALT.", []),
    (10, "Match only the shared ALT of a multi-ALT cache lookup.", []),
    (11, "Exclude a colocated record whose ALT does not match.", []),
    (
        12,
        "Include a colocated allele mismatch under no_check_alleles.",
        ["--no_check_alleles"],
    ),
    (13, "Match a reverse-strand cache allele by complementing it.", []),
    (14, "Reject a literal-base match which fails reverse-strand complementation.", []),
    (
        15,
        "Include a reverse-strand mismatch under no_check_alleles.",
        ["--no_check_alleles"],
    ),
    (16, "Include a coordinate-matched known record with unknown alleles.", []),
    (
        17,
        "Include a null-allele known record under no_check_alleles.",
        ["--no_check_alleles"],
    ),
    (
        18,
        "Exclude unknown-allele known records under exclude_null_alleles.",
        ["--exclude_null_alleles"],
    ),
]:
    add(CV, n, desc, recipe="synthetic", flags=flags, raw=n == 10)
alias(CV, 46, OF, "8,53,56,58")
add(
    CV,
    46,
    [
        "Report SOMATIC on the baseline matched variant.",
        "Report PHENO on the baseline matched variant.",
        "Report CLIN_SIG on the baseline matched variant.",
    ],
    recipe="baseline",
)
add(
    CV,
    47,
    "Attach a known-variant identifier to each source record having a match.",
    recipe="whole",
    needs="Minimize to one positive matched record; the aggregate 132-record count is not an atomic fixture.",
)
add(
    CV,
    48,
    "Do not reuse a known-variant match after moving one base away.",
    recipe="21:25585734; obtain REF from FASTA and choose an ALT not matching any known record. Keep rs142513484 absent even if other IDs match.",
)
alias(CV, 49, OF, 10)
Nasty = {
    50: (
        "21:8987005 A>AGCG (literal upstream REF mismatch); separately qualify 21:8987005 T>TGCG as a modern-cache analog.",
        "Match a left-anchored GCG insertion to Existing_variation.",
        False,
    ),
    51: (
        "21:8987004 TA>C,TAGCG",
        "Match the insertion ALT while leaving the unmatched ALT unassociated.",
        True,
    ),
    52: (
        "21:8987004 TAT>TAGCGT",
        "Match an insertion after removing shared prefix and suffix.",
        False,
    ),
    53: (
        "21:8987004 TAT>TAGCGT,TAGTGT",
        "Associate two padded insertion ALTs with their respective known records.",
        True,
    ),
}
for n, (recipe, desc, raw) in Nasty.items():
    add(
        CV,
        n,
        desc,
        recipe=recipe,
        scope="small indel",
        raw=raw,
        bug="Cache-witness problem first: literal old matched_alleles is not a current oracle. Issue #224 proposes a corrected-reference analog; current 116.2 parity untested. https://github.com/biodatageeks/vepyr-porting-tests/issues/224",
    )
add(
    CV,
    54,
    "Render legacy allele-frequency values under old_maf.",
    recipe="baseline",
    flags=["--old_maf"],
)
TV = "AnnotationSource_Cache_VariationTabix"
alias(TV, "13,16,44,47", CV, 46)
add(
    TV,
    "14,45",
    "Resolve a RefSeq chromosome accession for Existing_variation lookup.",
    recipe="NC_000021.9:25585733 C>T",
)
alias(TV, "15,46", CV, 48)
alias(TV, "17,48", CV, 47)
alias(TV, 18, CV, 49)
add(
    TV,
    23,
    "Report global AF from the colocated clinical variant's matched allele.",
    recipe="colocated",
)
for spec, desc, flags in [
    (
        "24,25",
        "Filter the known allele using the default frequency threshold.",
        ["--check_frequency"],
    ),
    (
        "26,27",
        "Reverse frequency selection using freq_gt_lt=lt.",
        ["--check_frequency", "--freq_gt_lt lt"],
    ),
    (
        "28,29",
        "Apply an explicit frequency threshold to allele selection.",
        ["--check_frequency", "--freq_freq 0.0001"],
    ),
    (
        "30",
        "Use the AMR population for frequency-based allele selection.",
        ["--check_frequency", "--freq_pop 1KG_AMR"],
    ),
    (
        "32",
        "Exclude alleles matching the frequency filter.",
        ["--check_frequency", "--freq_filter exclude"],
    ),
    (
        "33",
        "Include only alleles matching the frequency filter.",
        ["--check_frequency", "--freq_filter include"],
    ),
    (
        "34",
        "Remove only the failing ALT in exclude mode.",
        ["--check_frequency", "--freq_filter exclude"],
    ),
    (
        "35",
        "Retain only the passing ALT in include mode.",
        ["--check_frequency", "--freq_filter include"],
    ),
]:
    add(TV, spec, desc, recipe="source", flags=flags, raw=spec in {"34", "35"})
exclude(
    TV,
    31,
    "Negative input strand in a Perl VariationFeature is not a VCF input parameter; forward-strand frequency tests cannot exercise that branch.",
)
for spec, n in [("36,49", 50), ("37,50", 51), ("38,51", 52), ("39,52", 53)]:
    alias(TV, spec, CV, n)
add(
    TV,
    40,
    "Look up AMR frequency using the reduced insertion allele.",
    recipe="21:25005812 CA>CAAA,CAAA; splitting produces duplicate rows, do not deduplicate.",
    scope="small indel",
)

CT = "AnnotationSource_Cache_Transcript"
add(
    CT,
    "31,38",
    "Remove non-basic transcripts with gencode_basic.",
    recipe="baseline",
    flags=["--gencode_basic"],
)
for n, desc, flags in [
    (32, "Exclude a non-RefSeq accession from a RefSeq-only source.", ["--refseq"]),
    (
        33,
        "Apply the RefSeq whitelist only to the RefSeq half of a merged cache.",
        ["--merged"],
    ),
    (34, "Retain a standard NR accession without all_refseq.", ["--refseq"]),
    (35, "Retain a legacy numeric mitochondrial RefSeq identifier.", ["--refseq"]),
    (36, "Retain an rna-prefixed mitochondrial RefSeq identifier.", ["--refseq"]),
    (37, "Retain a bare-symbol mitochondrial RefSeq identifier.", ["--refseq"]),
]:
    add(
        CT,
        n,
        desc,
        recipe="synthetic",
        flags=flags,
        needs="Identifier-style witness required: cache 116 may not contain the legacy style; use an explicit source fixture if absent, not a renamed transcript claim.",
    )
for n, desc, flags in [
    (40, "Recover a missing HGNC_ID from sibling transcripts.", []),
    (
        41,
        "Recover a missing RefSeq gene symbol from sibling transcripts.",
        ["--refseq"],
    ),
    (42, "Recover RefSeq SYMBOL_SOURCE with the sibling-derived symbol.", ["--refseq"]),
    (43, "Recover a missing RefSeq HGNC_ID from sibling transcripts.", ["--refseq"]),
    (45, "Deduplicate repeated RefSeq transcript annotations.", ["--refseq"]),
    (
        47,
        "Deduplicate same-source repeated RefSeq transcript annotations.",
        ["--refseq"],
    ),
]:
    add(CT, n, desc, recipe="synthetic", flags=flags)
add(
    CT,
    69,
    "Emit the baseline SNV's transcript consequence set once each.",
    recipe="baseline",
)
add(
    CT,
    70,
    "Include the baseline SNV's most-severe missense consequence.",
    recipe="baseline",
)
add(
    CT,
    71,
    "Remove the named transcript using a transcript filter expression.",
    recipe="baseline",
    flags=["--transcript_filter stable_id ne ENST00000352957"],
)
add(
    CT,
    72,
    "Recompute the surviving consequence set after transcript filtering.",
    recipe="baseline",
    flags=["--transcript_filter stable_id ne ENST00000352957"],
)
for n, kind in [(82, "transcript"), (83, "gene"), (84, "symbol")]:
    add(
        CT,
        n,
        f"Report the nearest {kind} for a distant intergenic SNV.",
        recipe="chr21 near position 1: find a valid reference base and a release-116 nearest-feature witness.",
        flags=[f"--nearest {kind}"],
    )
for n, desc, scope in [
    (85, "Leave NEAREST empty on a contig without indexed transcripts.", "SNV"),
    (86, "Report an overlapping transcript as nearest.", "SNV"),
    (
        87,
        "Report the nearest transcript for a query crossing its boundary.",
        "small indel",
    ),
    (
        88,
        "Resolve nearest transcript for insertion before a feature edge.",
        "small indel",
    ),
    (
        89,
        "Resolve nearest transcript for insertion after a feature edge.",
        "small indel",
    ),
]:
    add(CT, n, desc, recipe="synthetic", flags=["--nearest transcript"], scope=scope)
add(
    CT,
    90,
    "Report nearest gene symbol for each annotated record.",
    recipe="whole",
    flags=["--nearest symbol"],
    needs="Split the 20-row assertion into one record per distinct nearest-symbol boundary scenario after verifying current transcript boundaries.",
)
CR = "AnnotationSource_Cache_RegFeat"
add(
    CR,
    44,
    "Emit one regulatory overlap entry without duplicate features.",
    recipe="reg_count",
)
add(
    CR,
    45,
    "Report the regulatory-region consequence at the selected overlap.",
    recipe="reg_count",
)
DBT = "AnnotationSource_Database_Transcript"
for spec, target in [
    ("15-17", 66),
    ("19", 68),
    ("20", 69),
    ("22,23", 69),
    ("24", 68),
    ("81", 40),
]:
    alias(DBT, spec, OF, target)
alias(DBT, 82, CT, 70)
alias(DBT, 83, CT, 71)
alias(DBT, 84, CT, 72)
add(
    DBT,
    27,
    "Report the protein-domain identifiers on a domain-overlapping consequence.",
    recipe="domains",
)
add(
    DBT,
    49,
    "Label the Ensembl source in a merged-cache consequence.",
    recipe="baseline",
    flags=["--merged"],
)
add(DBT, 52, "Exclude artifact-biotype transcript consequences.", recipe="synthetic")
add(
    DBT,
    54,
    "Exclude readthrough transcript consequences under the source's filtering policy.",
    recipe="synthetic",
    needs="DB filter is not proof cache mode filters the same way. Qualify a 116.2 cache-mode witness before accepting this analog.",
)
add(
    DBT,
    59,
    "Report APP gene symbol from a RefSeq transcript.",
    recipe="app",
    flags=["--refseq"],
)
add(
    DBT,
    91,
    "Include predicted RefSeq transcripts with all_refseq.",
    recipe="source",
    flags=["--refseq", "--all_refseq"],
)
DBV = "AnnotationSource_Database_Variation"
alias(DBV, 24, CV, 46)
alias(DBV, 25, CV, 47)
alias(DBV, 26, CV, 48)
alias(DBV, 27, CV, 49)
alias("AnnotationSource_Database_RegFeat", 32, CR, 44)
alias("AnnotationSource_Database_RegFeat", 33, CR, 45)
add(
    "AnnotationSource_Database_StructuralVariation",
    19,
    "Report known overlapping structural-variant IDs for a SNV.",
    recipe="baseline",
    flags=["--check_svs", "--database"],
)
add(
    "AnnotationSource_Database_StructuralVariation",
    20,
    "Report at least one known structural overlap for a source SNV.",
    recipe="whole",
    flags=["--check_svs", "--database"],
)

# Custom sources: these are retained, with explicit extra-asset/flag blockers.
BED = "AnnotationSource_File_BED"
for n, checks in [
    (11, ["Report all overlapping BED names."]),
    (
        12,
        [
            "Report only an exactly matching BED interval.",
            "Preserve the earlier custom source when adding a second source.",
        ],
    ),
    (13, ["Preserve a BED feature named 0."]),
    (14, ["Exclude a BED exact match one base before its boundary."]),
    (15, ["Exclude a BED exact match one base after its boundary."]),
    (16, ["Read BED column 5 for minimum-score aggregation."]),
    (17, ["Preserve minimum BED score 0."]),
    (18, ["Preserve maximum BED score 0."]),
]:
    add(
        BED,
        n,
        checks,
        flags=["--custom BED"],
        old=False,
        needs="Input in linked setup plus t/testdata/custom BED asset; target fixture schema needs an auxiliary-source declaration.",
    )
BW = "AnnotationSource_File_BigWig"
for n, checks in [
    (12, ["Report the overlapping bigWig score as the custom value."]),
    (13, ["Omit unrequested bigWig summary statistics."]),
    (14, ["Preserve both bigWig sources when adding a differently named source."]),
    (
        15,
        [
            f"Report bigWig {x} for a single score."
            for x in ["min", "mean", "max", "sum", "count"]
        ],
    ),
    (16, ["Preserve the order of overlapping bigWig interval values."]),
    (
        17,
        [
            f"Report bigWig {x} over multiple intervals."
            for x in ["min", "mean", "max", "sum", "count"]
        ],
    ),
    (18, ["Leave a bigWig annotation empty one base before coverage."]),
    (19, ["Leave a bigWig annotation empty one base after coverage."]),
    (20, ["Read a score from a fixedStep bigWig section."]),
    (
        21,
        [
            "Use coordinates as the bigWig custom identifier.",
            "Retain the score when reporting bigWig coordinates.",
        ],
    ),
    (22, ["Report exact maximum score across a large bigWig span."]),
    (23, ["Report exact minimum score across a large bigWig span."]),
    (24, ["Preserve the first per-record value across a large bigWig span."]),
    (25, ["Mark truncation of a large per-record bigWig annotation list."]),
    (
        26,
        [
            f"Report exact {x} over a large bigWig span."
            for x in ["sum", "mean", "count"]
        ],
    ),
]:
    add(
        BW,
        n,
        checks,
        flags=["--custom bigwig"],
        scope="SV/repeat" if n in {16, 17, 22, 23, 24, 25, 26} else "SNV",
        old=False,
        needs="Requires linked bigWig asset and custom-source options. The per-base/summary backend itself cannot be tested by VCF equality.",
    )
FVCF = "AnnotationSource_File_VCF"
for n, checks in [
    (11, ["Report the overlapping custom VCF record ID."]),
    (12, ["Report a coordinate-overlapping custom VCF insertion record."]),
    (13, ["Use custom VCF QUAL for minimum-score aggregation."]),
    (14, ["Omit a custom score when QUAL is missing."]),
    (15, ["Use source coordinates when custom VCF ID is missing."]),
    (16, ["Use source coordinates instead of the custom VCF ID when requested."]),
    (
        17,
        [
            "Copy a requested scalar custom INFO value.",
            "Preserve the complete multi-value custom INFO under overlap mode.",
            "Render a valueless custom INFO flag as 1.",
            "Report custom FILTER when suppression is off.",
        ],
    ),
    (
        18,
        [
            "Choose the matched ALT's custom INFO chunk in exact mode.",
            "Accumulate exact and overlap sources under distinct names.",
        ],
    ),
    (
        20,
        [
            "Report each custom record matching the same normalized deletion.",
            "Choose the matched deletion ALT's INFO chunk.",
            "Preserve multiple custom FILTER values.",
            "Leave an absent requested custom INFO value empty.",
        ],
    ),
    (
        21,
        ["Select the insertion-specific custom INFO chunk from a mixed source record."],
    ),
    (22, ["Select the SNV-specific custom INFO chunk from a mixed source record."]),
    (23, ["Match a custom insertion after removing one prefix anchor."]),
    (24, ["Match a custom insertion after removing two shared prefix bases."]),
    (25, ["Match a custom SNV after trimming one shared suffix base."]),
    (26, ["Match a custom SNV after trimming two shared suffix bases."]),
    (27, ["Match a custom SNV after trimming both shared ends."]),
    (28, ["Match a custom deletion after trimming the source ALT."]),
    (29, ["Match an equivalent shifted deletion within a homopolymer."]),
    (
        30,
        ["Use coordinate overlap for structural input against an exact custom source."],
    ),
    (31, ["Skip the REF chunk when custom INFO includes one value for REF."]),
    (32, ["Preserve the raw custom INFO list when cardinality cannot map to ALT."]),
    (33, ["Deduplicate a repeated COSMIC custom identifier across transcript rows."]),
    (34, ["Omit custom FILTER when custom_suppress_filter is enabled."]),
    (35, ["Report custom FILTER when suppression is off."]),
]:
    add(
        FVCF,
        n,
        checks,
        flags=["--custom VCF", "--custom_suppress_filter"]
        if n == 34
        else ["--custom VCF"],
        scope="SV/repeat"
        if n == 30
        else "small indel"
        if n in {12, 14, 20, 21, 23, 24, 28, 29, 33}
        else "MNV"
        if n in {25, 26, 27}
        else "SNV",
        old=False,
        needs="Extract source VCF row from linked setup; preserve and checksum the referenced custom/test.vcf.gz or cosmic.vcf.gz asset and options.",
    )
exclude(
    FVCF,
    19,
    "Negative-strand input is supplied as an Ensembl-format record. A forward-oriented VCF can test ALT-specific INFO matching, but cannot preserve this reverse-input-strand parser assertion.",
    "non-vcf-input",
)
GFF = "AnnotationSource_File_GFF"
GTF = "AnnotationSource_File_GTF"
for f, start, field_nums in [
    (
        GFF,
        12,
        [(12, "Feature"), (13, "Gene"), (14, "SYMBOL"), (15, "ENSP"), (16, "TSL")],
    ),
    (GTF, 11, [(11, "Feature"), (12, "Gene"), (13, "SYMBOL"), (14, "ENSP")]),
]:
    for n, field in field_nums:
        add(
            f,
            n,
            f"Report {field} from the {'GFF' if f == GFF else 'GTF'} transcript model.",
            recipe="baseline",
            flags=["--gff" if f == GFF else "--gtf"],
            old=False,
            needs="Use the pinned custom transcript file plus matched FASTA; this projects the model attribute to one CSQ field.",
        )
for n, desc in [
    (21, "Resolve GFF Gene from parent ID when gene_id is missing."),
    (24, "Omit a GFF transcript whose biotype is missing."),
    (26, "Omit a GFF transcript with overlapping invalid exons."),
]:
    add(
        GFF,
        n,
        desc,
        recipe="baseline",
        flags=["--gff"],
        old=False,
        needs="Materialize the exact source mutation as a separate controlled GFF fixture. The unmodified GFF does not exercise this case.",
    )
add(
    GFF,
    34,
    "Report the missense consequence from the custom GFF transcript model.",
    recipe="baseline",
    flags=["--gff"],
    old=False,
)
add(
    GFF,
    35,
    "Remove the named GFF transcript with a transcript filter.",
    recipe="baseline",
    flags=["--gff", "--transcript_filter stable_id ne ENST00000352957"],
    old=False,
)
add(
    GFF,
    36,
    "Recompute the GFF consequence set after transcript filtering.",
    recipe="baseline",
    flags=["--gff", "--transcript_filter stable_id ne ENST00000352957"],
    old=False,
)
for n, contig in [(38, "21"), (39, "chr21"), (40, "NC_000021.9"), (41, "Foo")]:
    add(
        GFF,
        n,
        f"Resolve input contig {contig} against the GFF source naming convention.",
        recipe=f"{contig}:25585733 C>T; use the source's matching contig/synonym setup.",
        flags=["--gff"],
        old=False,
        needs="For synthetic Foo/chrFoo, provide explicitly renamed GFF and FASTA assets; do not claim stock-cache coverage.",
    )
for n, field in [(46, "Feature"), (47, "Gene"), (48, "SYMBOL"), (49, "ENSP")]:
    add(
        GFF,
        n,
        f"Report {field} from a RefSeq GFF transcript.",
        recipe="baseline",
        flags=["--gff RefSeq"],
        old=False,
    )
add(
    GFF,
    54,
    "Report the missense consequence from a RefSeq GFF model.",
    recipe="baseline",
    flags=["--gff RefSeq"],
    old=False,
)
add(
    GFF,
    55,
    "Retain output for a contig absent from the custom GFF with dont_skip.",
    flags=["--gff", "--dont_skip"],
    old=False,
)
for field in ["name", "feature_id", "associated_gene", "type"]:
    add(
        GFF,
        59,
        f"Report custom GENCODE promoter {field}.",
        recipe="baseline",
        flags=["--custom GFF gff_type=gencode_promoter"],
        old=False,
    )
add(
    GTF,
    24,
    "Report the missense consequence from the custom GTF transcript model.",
    recipe="baseline",
    flags=["--gtf"],
    old=False,
)
add(
    GTF,
    27,
    "Use mitochondrial translation when reporting an MT amino-acid substitution.",
    recipe="MT:4472 T>A plus pinned MT GTF and FASTA.",
    flags=["--gtf"],
    old=False,
)
related(
    GTF,
    26,
    GTF,
    27,
    "A codon-table number is internal; the linked amino-acid case is its observable analog.",
)

RF = "AnnotationSource_File_RegFeat"
RM = "AnnotationSource_File_RegFeat_phase2"
RF_RECIPE = "21:25585733 C>T plus the pinned regulatory GFF used by t/AnnotationSource_File_RegFeat.t."
for n, check in [
    (11, "Annotate regulatory GFF overlaps even when cell_type is requested."),
    (16, "Use the regulatory GFF ID attribute as CSQ.Feature."),
    (17, "Use regulatory GFF column 3 as CSQ.BIOTYPE."),
    (20, "Report CTCF_binding_site BIOTYPE from regulatory GFF."),
    (21, "Report enhancer BIOTYPE from regulatory GFF."),
    (22, "Report open_chromatin_region BIOTYPE from regulatory GFF."),
    (25, "Fall back to the regulatory GFF Name attribute for Feature."),
    (26, "Generate a deterministic regulatory Feature ID when ID and Name are absent."),
    (27, "Ignore unsupported regulatory GFF feature types."),
    (35, "Deduplicate a regulatory feature spanning two fetched regions."),
    (36, "Emit each regulatory stable ID once after merging overlapping regions."),
    (42, "Emit one regulatory consequence entry for the source promoter overlap."),
    (43, "Associate the SNV with the source promoter's Feature ID."),
    (44, "Report regulatory_region_variant from the regulatory GFF overlap."),
    (45, "Associate the SNV with the source CTCF binding-site ID."),
    (46, "Distinguish CTCF binding-site BIOTYPE in regulatory output."),
    (55, "Annotate a chr-prefixed variant against the bare-contig regulatory GFF."),
]:
    recipe = (
        RF_RECIPE
        if n in {11, 16, 17, 42, 43, 44}
        else "21:25587701 T>C plus source regulatory GFF."
        if n in {20, 45, 46}
        else "chr21:25585733 C>T plus source regulatory GFF."
        if n == 55
        else "source"
    )
    add(
        RF,
        n,
        check,
        recipe=recipe,
        flags=["--regulatory_gff", "--cell_type HUVEC"]
        if n == 11
        else ["--regulatory_gff"],
        old=False,
        needs="Requires the pinned regulatory GFF auxiliary asset; minimize the variant within the particular feature tested.",
    )
# Expand the two assertion loops into 2 + 4 distinct mode/location cases.
for ext in [0, 1]:
    add(
        RF,
        47,
        f"{'Include' if ext else 'Exclude'} an extended-only promoter overlap with extended_promoters={ext}.",
        recipe="21:25585100 A>G plus source regulatory GFF.",
        flags=["--regulatory_gff"] + (["--extended_promoters"] if ext else []),
        old=False,
    )
    for pos, side in [(29999800, "left"), (35000200, "right")]:
        add(
            RF,
            48,
            f"{'Include' if ext else 'Exclude'} the promoter extension across the {side} cache-bin boundary with extended_promoters={ext}.",
            recipe=f"21:{pos} A>G plus source regulatory GFF.",
            flags=["--regulatory_gff"] + (["--extended_promoters"] if ext else []),
            old=False,
        )
for spec, side in [("28", "left"), ("29", "right")]:
    add(
        RF,
        spec,
        f"Extend promoter overlap beyond its {side} core boundary.",
        recipe="source",
        flags=["--regulatory_gff", "--extended_promoters"],
        old=False,
    )
for spec, side in [("30", "left"), ("31", "right")]:
    add(
        RF,
        spec,
        f"Leave the enhancer {side} boundary unchanged under extended_promoters.",
        recipe="source",
        flags=["--regulatory_gff", "--extended_promoters"],
        old=False,
    )
add(
    RF,
    50,
    "Emit regulatory consequences for a symbolic deletion overlapping the custom promoter.",
    recipe="21:25585400 A><DEL>; INFO SVTYPE=DEL;END=25586000; source regulatory GFF.",
    scope="SV/repeat",
    flags=["--regulatory_gff"],
    old=False,
)
for n, target in [(58, 42), (59, 44), (60, 43), (63, 43), (64, 17), (65, 44)]:
    alias(RF, n, RF, target)
add(
    RF,
    61,
    "Report Feature_type=RegulatoryFeature for a regulatory GFF overlap.",
    recipe=RF_RECIPE,
    flags=["--regulatory_gff"],
    old=False,
)
exclude(
    RF,
    62,
    "The JSON regulatory_feature_consequences array name is output-format specific; its individual annotation values are mapped separately.",
    "non-vcf-output",
)
add(
    RF,
    74,
    "Use regulatory GFF features without also emitting cache regulatory entries.",
    recipe=RF_RECIPE,
    flags=["--regulatory_gff"],
    old=False,
)
add(
    RF,
    75,
    "Keep transcript consequences when regulatory GFF overrides regulatory cache data.",
    recipe=RF_RECIPE,
    flags=["--regulatory_gff"],
    old=False,
)
add(
    RF,
    76,
    "Keep regulatory cache annotation when no regulatory GFF is supplied.",
    recipe="regulatory",
)
MOTIF_RECIPE = "21:25585730 C>T plus the pinned motif GFF from t/AnnotationSource_File_RegFeat_phase2.t."
for n, check in [
    (9, "Report the motif GFF stable ID as Feature."),
    (10, "Report motif strand from GFF."),
    (12, "Report MOTIF_NAME from the GFF binding_matrix_id."),
    (14, "Report the GFF transcription-factor complex list."),
    (19, "Emit one motif consequence for the source GFF overlap."),
    (21, "Report TF_binding_site_variant from the motif GFF."),
    (22, "Report MOTIF_NAME from the GFF binding_matrix_id."),
    (23, "Report the GFF transcription-factor complex list."),
    (25, "Leave HIGH_INF_POS empty when motif GFF has no PWM."),
    (26, "Leave MOTIF_SCORE_CHANGE empty when motif GFF has no PWM."),
]:
    add(
        RM,
        n,
        check,
        recipe=MOTIF_RECIPE,
        flags=["--regulatory_gff motifs=<file>"],
        old=False,
    )
related(
    RM,
    15,
    RM,
    "25,26",
    "Absence of PWM elements in the internal object is represented only by these two observable missing-field analogs.",
)
add(
    RM,
    45,
    "Report EMAR BIOTYPE from an EMAR regulatory GFF source.",
    recipe="Source EMAR GFF interval; choose a valid overlapping SNV using matched FASTA.",
    flags=["--regulatory_gff emars=<file>"],
    old=False,
)
add(
    RM,
    49,
    "Annotate EMAR overlaps even when unsupported cell_type selection is requested.",
    recipe="Source EMAR GFF interval; choose a valid overlapping SNV using matched FASTA.",
    flags=["--regulatory_gff emars=<file>", "--cell_type HUVEC"],
    old=False,
)

# Other serializers: map shared values, do not credit their schema/line layouts.
J = "OutputFactory_JSON"
alias(J, "8,12", OF, "40,48,60,77,81")
alias(J, 9, OF, "8,53,56,58")
add(
    J,
    10,
    "Omit a colocated frequency belonging only to a non-input ALT.",
    recipe="21: source position of rs145564988 G>A; recover position from assertion setup, not its other T allele.",
)
alias(J, 13, OF, 84)
alias(J, 14, VCF, 29)
alias(J, 19, OF, 118)
add(
    J,
    19,
    "Report original ALT index for the minimized deletion.",
    recipe="minimal",
    scope="small indel",
    flags=["--minimal", "--allele_number"],
    raw=True,
)
add(
    J,
    19,
    "Report original ALT index for the minimized SNV.",
    recipe="minimal",
    scope="small indel",
    flags=["--minimal", "--allele_number"],
    raw=True,
)
for field in [
    "GIVEN_REF",
    "USED_REF",
    "REFSEQ_MATCH",
    "UPLOADED_ALLELE",
    "Amino_acids",
    "Consequence",
]:
    add(
        J,
        20,
        f"Report {field} for the RefSeq frameshift insertion.",
        recipe="Source G>GA insertion on NM_000484.3; recover genomic position from linked setup.",
        scope="small indel",
        flags=["--refseq", "--uploaded_allele"],
    )
alias(J, 21, OF, 78)
for field in ["cDNA_position", "OverlapBP", "OverlapPC", "Consequence"]:
    add(
        J,
        22,
        f"Report {field} for a deletion extending outside the transcript.",
        recipe="21:25585652 N><DEL>; END=25585700; obtain the true anchor REF from FASTA.",
        scope="SV/repeat",
        flags=["--overlaps"] if field.startswith("Overlap") else [],
    )
alias(J, 24, VCF, 31)
alias(J, 25, VCF, 32)
for f, spec in [("OutputFactory_Tab", "12"), ("OutputFactory_VEP_output", "16")]:
    add(
        f,
        spec,
        "Report REF_ALLELE when show_ref_allele is requested.",
        recipe="baseline",
        flags=["--show_ref_allele"],
    )
    add(
        f,
        spec,
        "Report UPLOADED_ALLELE for a baseline VCF SNV.",
        recipe="baseline",
        flags=["--uploaded_allele"],
    )
    alias(f, spec, OF, 81)
for f, n in [("OutputFactory_Tab", 13), ("OutputFactory_VEP_output", 17)]:
    alias(f, n, VCF, 26)
for f, n in [("OutputFactory_Tab", 16), ("OutputFactory_VEP_output", 18)]:
    alias(f, n, VCF, 29)
for f, n in [("OutputFactory_Tab", 15), ("OutputFactory_VEP_output", 20)]:
    alias(f, n, VCF, 31)
add(
    "OutputFactory_VEP_output",
    20,
    "Emit custom FILTER in the annotation output.",
    recipe="baseline",
    flags=["--custom VCF"],
    old=False,
)
add(
    "OutputFactory_Tab",
    17,
    "Project to the requested CSQ field order.",
    recipe="baseline",
    flags=["--fields Location,HGVSc"],
    needs="VCF has no default CSQ Location slot. Map Location to CHROM/POS separately; do not claim the tabular two-column shape is preserved.",
)

# Externally supplied BAM edits have useful analogs, but not stock-cache witnesses.
for n, op, scope in [
    (17, "substitution", "SNV"),
    (21, "insertion", "small indel"),
    (25, "soft-clip", "small indel"),
]:
    add(
        "bam_edit",
        n,
        f"Reflect a BAM-derived {op} transcript edit in HGVSc.",
        recipe="synthetic",
        scope=scope,
        flags=["--gff", "--bam"],
        needs="Map each source edit from transcript to genomic coordinates and select one variant whose HGVSc changes; separate one fixture per edit site. No witness is yet established.",
    )
add(
    "bam_edit",
    30,
    "Omit HGVS when BAM transcript reconciliation fails.",
    recipe="synthetic",
    flags=["--gff", "--bam"],
    needs="Materialize the source's damaged-exon GFF/FASTA scenario as auxiliary assets; changing only the input VCF cannot induce the source mutation.",
)

# Honest exclusions for snapshots of metadata / whole sequences: a single SNV
# cannot verify an entire peptide, all exon coordinates, or a region-fetch list.
exclude(
    GFF,
    "8-11,17-20,43-45,50-52",
    "Tests fetched GFF records, complete model construction, exon counts, or whole sequences. One VCF annotation cannot establish those complete structures; specific exposed fields and translation witnesses are separate candidates.",
)
exclude(
    GTF,
    "7-10,15-18",
    "Tests fetched GTF records, complete model construction, exon counts, or whole sequences. Specific exposed fields and the mitochondrial amino-acid witness are separate candidates.",
)
exclude(
    "bam_edit",
    16,
    "Compares the complete 2.7-kb transcript sequence. A local variant consequence cannot establish equality of that whole sequence.",
)


def remove_candidates(file, spec, reason, category="non-vcf-output"):
    for r in rows(file, spec):
        for cid in list(r["atomic_tests"]):
            c = CASES[cid]
            i = c["source_assertions"].index(r["id"])
            c["source_assertions"].pop(i)
            c["source_links"].pop(i)
            if not c["source_assertions"]:
                del CASES[cid]
        r["atomic_tests"] = []
    exclude(file, spec, reason, category)


# Config.pm explicitly makes these options incompatible with --vcf.
remove_candidates(
    OF,
    "46,47",
    "VEP Config.pm:616-617 declares most_severe and summary incompatible with vcf. The summary hash cannot be blessed through the required VCF command; ordinary consequence terms have separate VCF candidates.",
    "vcf-incompatible-option",
)
remove_candidates(CT, 90, "Expanded by record below.")
for match in re.finditer(r"'(\d+)'\s*=>\s*'([^']+)'", ROWS[f"{CT}:090"]["excerpt"]):
    pos, symbol = match.groups()
    add(
        CT,
        90,
        f"Report NEAREST symbol for the source SNV at 21:{pos} (legacy witness {symbol}).",
        recipe=f"Take only 21:{pos} from t/testdata/test.vcf; use its REF/ALT, and regenerate the symbol with cache 116.",
        flags=["--nearest symbol"],
    )
remove_candidates("bam_edit", "17,21,25", "Expanded by edit site below.")
for n, edits in [
    (17, [("transcript 0", 256, "X"), ("transcript 0", 1547, "X")]),
    (
        21,
        [
            ("NM_001286476.1", 2966, "I13"),
            ("NM_001286476.1", 2941, "I1"),
            ("NM_001286476.1", 2618, "I1"),
            ("NM_001286476.1", 920, "I1"),
        ],
    ),
    (25, [("NM_032195.2", 3660, "X"), ("NM_032195.2", 7504, "S16")]),
]:
    for tr, pos, op in edits:
        add(
            "bam_edit",
            n,
            f"Reflect the {op} BAM edit at {tr} transcript position {pos} in HGVSc.",
            recipe="synthetic",
            scope="SNV" if op == "X" else "small indel",
            flags=["--gff", "--bam"],
            needs="Map this one transcript edit to genomic coordinates, then choose a variant that exposes it; preserve the pinned BAM/GFF/FASTA assets. No concrete VCF witness is established.",
        )
for pos, desc, scope in [
    (999999, "before the cache boundary", "SNV"),
    (1000000, "at the last base of a cache region", "SNV"),
    (1000001, "at the first base of the next cache region", "SNV"),
]:
    add(
        "AnnotationSource",
        13,
        f"Retain annotation for a SNV {desc}.",
        recipe=f"1:{pos} A>G from the source; verify REF and find an annotation witness crossing this boundary.",
        scope=scope,
        needs="Output analog of region selection: requires a feature/known-variant witness at this boundary; no assertion of the internal region tuple itself.",
    )
for desc, recipe, scope in [
    ("an MNV crossing a cache-region boundary", "1:1000000 AC>GT", "MNV"),
    ("an insertion crossing a cache-region boundary", "1:1000000 C>CT", "small indel"),
    (
        "a deletion spanning several cache regions",
        "1:1 N><DEL>; END=3000001; obtain real anchor from FASTA",
        "SV/repeat",
    ),
    (
        "two consecutive SNVs on opposite sides of a cache-region boundary",
        "1:1000000 A>G then 1:1000001 A>G",
        "SNV",
    ),
]:
    add(
        "AnnotationSource",
        13,
        f"Retain the annotations for {desc}.",
        recipe=recipe,
        scope=scope,
        needs="Output analog: establish a real feature witness for each queried region; VCF does not expose cache-region tuple construction.",
    )
for n, check in [
    (35, "Report the nearer left-hand transcript."),
    (36, "Report the nearer right-hand transcript."),
    (37, "Report both nearest transcripts for an equidistant SNV."),
    (38, "Report both nearest transcripts for an equidistant multi-base query."),
]:
    add(
        "TranscriptTree",
        n,
        check,
        recipe="synthetic",
        flags=["--nearest transcript"],
        scope="small indel" if n == 38 else "SNV",
    )

# The large GFF/GTF model maps are compound assertions too. Their complete
# in-memory model shape is not a VCF contract, but each named transcript's
# BIOTYPE is observable with a SNV inside that model. Keep these conditional
# cases individually, including the explicit fake multi-parent transcript.
for file, number, source_kind, flag in [
    (GFF, 20, "Ensembl GFF", "--gff"),
    (GFF, 52, "RefSeq GFF", "--gff RefSeq"),
    (GTF, 18, "Ensembl GTF", "--gtf"),
]:
    pairs = re.findall(
        r"'([A-Za-z0-9_.]+)'\s*=>\s*'([A-Za-z0-9_]+)'",
        ROWS[f"{file}:{number:03}"]["excerpt"],
    )
    assert len(pairs) == (49 if number == 52 else 58)
    for transcript, biotype in pairs:
        add(
            file,
            number,
            f"Report BIOTYPE for {transcript} from the {source_kind} model (source value {biotype}).",
            recipe=f"Use the pinned {source_kind} asset from this assertion. Select one genomic-forward SNV in an exon of {transcript}; derive its REF from the matched GRCh38 FASTA. Retain this exact model ID, including a synthetic ID if present.",
            flags=[flag],
            old=False,
            needs="One transcript-model witness still needs concrete genomic coordinates/REF/ALT. This checks the exposed BIOTYPE, not the complete region-fetch hash or transcript object.",
        )

# Corrections from source-level review, rather than inherited 116.0 labels.
ROWS["Runner:029"]["description"] = (
    "A failing compressor's nonzero exit status makes run() throw rather than report success."
)
ROWS["Runner:030"]["description"] = (
    "Real gzip failure on /dev/full (ENOSPC) makes run() throw."
)
ROWS[f"{RF}:008"]["description"] = (
    "Constructing a regulatory GFF source without a file throws 'No file given'."
)
ROWS[f"{RM}:036"]["description"] = (
    "A regulatory_gff key with an empty value throws 'expected key=value'."
)
ROWS[f"{RF}:047"]["description"] = (
    "Loop over extended_promoters=0,1: include the extended-only promoter SNV only when extension is enabled."
)
ROWS[f"{RF}:048"]["description"] = (
    "Two boundary positions x extended_promoters=0,1: promoter extension reaches across the adjacent cache bin in either direction."
)
for r in ROWS.values():
    r["legacy_category_not_adopted"] = r.pop("legacy_category")


# Avoid mapping sibling fields which a narrower assertion does not inspect.
def retain_checks(file, spec, predicate):
    for r in rows(file, spec):
        for cid in list(r["atomic_tests"]):
            if predicate(CASES[cid]):
                continue
            c = CASES[cid]
            i = c["source_assertions"].index(r["id"])
            c["source_assertions"].pop(i)
            c["source_links"].pop(i)
            r["atomic_tests"].remove(cid)
            if not c["source_assertions"]:
                del CASES[cid]


for n, field in [
    (15, "SYMBOL "),
    (16, "SYMBOL_SOURCE"),
    (17, "HGNC_ID"),
    (19, "CCDS"),
    (20, "RefSeq cross-reference"),
    (22, "SWISSPROT"),
    (23, "UNIPARC"),
    (24, "ENSP"),
]:
    retain_checks(DBT, n, lambda c, field=field: field in c["description"])
retain_checks(VCF, 29, lambda c: "RefSeq cross-reference" not in c["description"])
retain_checks(J, 14, lambda c: "RefSeq cross-reference" not in c["description"])
retain_checks(RUN, 17, lambda c: "UTR" in c["description"])
retain_checks(
    DBV,
    24,
    lambda c: not any(word in c["description"] for word in ["_AF", " AF "]),
)
for f, n in [("OutputFactory_Tab", 16), ("OutputFactory_VEP_output", 18)]:
    retain_checks(
        f,
        n,
        lambda c: (
            not any(
                word in c["description"]
                for word in [
                    "RefSeq cross-reference",
                    "coding transcript",
                    "baseline missense",
                    "upstream transcript",
                    "APPRIS",
                    "SIFT",
                    "PolyPhen",
                ]
            )
        ),
    )
for f, n in [("OutputFactory_Tab", 13), ("OutputFactory_VEP_output", 17)]:
    retain_checks(
        f,
        n,
        lambda c: (
            not any(
                word in c["description"]
                for word in ["all APP", "combined cds_start_NF"]
            )
        ),
    )
for c in CASES.values():
    if "--fields core" in c["vep_flags"]:
        c["vep_flags"] = [
            "--everything",
            "--fields <the ordered field list at the linked assertion>",
        ]
        c["witness_status"] = (
            "Requires explicit field list plus the source's synthetic custom_test column; header-only projection, not a runnable default --everything fixture."
        )
        c["unsupported_features"] = (
            "Harness gap: ordered fields are supported by the vepyr API. The source's synthetic custom_test column needs an auxiliary plugin equivalent; body comparator does not validate the header."
        )
    if c["source_assertions"] == ["OutputFactory:015"]:
        c["variant_class"] = "SNV"
    if any(
        rid in {f"{CV}:{n:03}" for n in range(50, 54)} for rid in c["source_assertions"]
    ):
        c["old_cache_problem"] = (
            "Old anchored-repeat match depends on v84 cache alleles and includes REF discrepancies. Rediscover the cache-116 witness; #224 documents a proposed corrected-reference analog, not a 116.2 oracle. https://github.com/biodatageeks/vepyr-porting-tests/issues/224"
        )
        c["potential_vepyr_bug"] = UNKNOWN_BUG
    if any(
        rid in {"OutputFactory_JSON:022", "Parser_VCF:033"}
        for rid in c["source_assertions"]
    ):
        c["potential_vepyr_bug"] = (
            "Prior issue #218 reports a symbolic DEL treated as a one-base allele. Reproduce on current vepyr versus VEP 116.2 before calling it an active defect. https://github.com/biodatageeks/vepyr-porting-tests/issues/218"
        )

PRIORITY = {"SNV": 0, "small indel": 1, "nonvariant": 2, "MNV": 3, "SV/repeat": 4}
ASSERT_PATTERN = re.compile(
    r"^\s*(ok|is|isnt|like|unlike|is_deeply|cmp_ok|isa_ok|can_ok|new_ok|pass|fail|use_ok|require_ok|throws_ok|dies_ok|lives_ok|lives_and|cmp_deeply|cmp_bag|cmp_set|cmp_methods)\b"
)


def code_link(path, source, case):
    # Entry points are labelled as such. Consequence calculation can delegate to
    # ensembl-variation; this audit does not invent links to another repo's code.
    if path.endswith("AnnotationSource/Cache/RegFeat.pm"):
        path = "modules/Bio/EnsEMBL/VEP/AnnotationType/RegFeat.pm"
    p = source / path
    if not p.exists():
        if "RegFeat" in path:
            path = "modules/Bio/EnsEMBL/VEP/AnnotationType/RegFeat.pm"
        elif "/bam/" in path:
            path = "modules/Bio/EnsEMBL/VEP/AnnotationType/Transcript.pm"
        p = source / path
    text = p.read_text()
    desc = case["description"].lower()
    if path.endswith("OutputFactory.pm"):
        method = (
            "add_colocated_frequency_data"
            if any(x in desc for x in ["frequency", "_af", " af "])
            else "add_colocated_variant_info"
            if any(
                x in desc
                for x in [
                    "colocated",
                    "pubmed",
                    "somatic",
                    "clin_sig",
                    "pheno",
                    "existing_variation",
                ]
            )
            else "MotifFeatureVariationAllele_to_output_hash"
            if "motif" in desc
            else "RegulatoryFeatureVariationAllele_to_output_hash"
            if "regulatory" in desc or "promoter" in desc
            else "filter_VariationFeatureOverlapAlleles"
            if "pick" in desc
            else "rejoin_variants_in_InputBuffer"
            if "rejoin" in desc
            else "get_all_output_hashes_by_InputBuffer"
        )
    elif path.endswith("OutputFactory/VCF.pm"):
        method = (
            "headers"
            if case["header_comparison_required"]
            else "output_hash_to_vcf_info_chunk"
            if any(
                x in desc
                for x in [
                    "serialize",
                    "semicolon",
                    "pipe",
                    "whitespace",
                    "comma",
                    "zero-valued",
                    "empty csq",
                ]
            )
            else "get_all_lines_by_InputBuffer"
        )
    elif path.endswith("AnnotationSource/File/VCF.pm"):
        method = (
            "_get_score"
            if "score" in desc
            else "_record_overlaps_VF"
            if any(x in desc for x in ["match", "trim", "shift"])
            else "_create_records"
        )
    elif path.endswith("AnnotationSource.pm"):
        method = "get_all_regions_by_InputBuffer"
    elif path.endswith("Runner.pm"):
        method = "run"
    elif path.endswith("Parser/VCF.pm"):
        method = "create_VariationFeatures"
    elif path.endswith("Parser.pm"):
        method = "validate_vf"
    elif path.endswith("InputBuffer.pm"):
        method = "get_overlapping_vfs" if "overlap" in desc else "next"
    elif path.endswith("TranscriptTree.pm"):
        method = "nearest"
    elif "bam" in desc:
        method = "apply_edits"
    elif path.endswith("AnnotationType/Variation.pm"):
        method = "compare_existing" if "match" in desc else "annotate_InputBuffer"
    elif path.endswith("AnnotationType/Transcript.pm"):
        method = (
            "filter_transcript"
            if any(x in desc for x in ["basic", "refseq", "exclude"])
            else "annotate_InputBuffer"
        )
    else:
        method = "annotate_InputBuffer"
    m = re.search(r"^sub\s+" + method + r"\b", text, re.M)
    if not m:
        m = re.search(r"^sub\s+(\w+)", text, re.M)
        method = m.group(1) if m else "module"
    line = text[: m.start()].count("\n") + 1 if m else 1
    return dict(
        path=path,
        line=line,
        function=method,
        url=BASE + path + f"#L{line}",
        relation="implementation entrypoint, potentially delegating to Ensembl dependencies",
    )


def md(value):
    return str(value).replace("|", "&#124;").replace("\n", "<br>")


def link_case(cid):
    return f"[{cid}](atomic-tests.md#{cid.lower()})"


def finish(source):
    found = []
    for path in sorted((source / "t").glob("*.t")):
        for line, text in enumerate(path.read_text().splitlines(), 1):
            m = ASSERT_PATTERN.match(text)
            if m:
                found.append((str(path.relative_to(source)), line, m.group(1)))
    expected = [(r["file"], r["line_start"], r["kind"]) for r in ROWS.values()]
    assert sorted(found) == sorted(expected), "Snapshot assertion enumeration differs"
    assert len(found) == 2103 and len(set(r["file"] for r in ROWS.values())) == 51
    for r in ROWS.values():
        assert (
            bool(r["atomic_tests"]) == r["classification"].startswith("vcf-")
            or r["classification"] == "vcf-incompatible-option"
        ), r["id"]
        assert all(cid in CASES for cid in r["atomic_tests"] + r["related_tests"]), r[
            "id"
        ]
        # More precise links: trim overlong extracted blocks at the actual
        # parenthesized assertion's closing line when there is a clear terminator.
        excerpt = r["excerpt"].splitlines()
        if re.match(r"\s*\w+\(", excerpt[0]):
            end = next(
                (
                    i
                    for i, line in enumerate(excerpt)
                    if re.search(r"\);\s*(?:#.*)?$", line)
                ),
                None,
            )
            if end is not None:
                r["line_end"] = r["line_start"] + end
                r["excerpt"] = "\n".join(excerpt[: end + 1])
        r["source_url"] = BASE + r["file"] + f"#L{r['line_start']}-L{r['line_end']}"
        r["excerpt_sha256"] = hashlib.sha256(r["excerpt"].encode()).hexdigest()
        if r["atomic_tests"]:
            for key in [
                "old_cache_problem",
                "potential_vepyr_bug",
                "unsupported_features",
            ]:
                r[key] = " / ".join(
                    dict.fromkeys(CASES[cid][key] for cid in r["atomic_tests"])
                )
        else:
            r["old_cache_problem"] = (
                "N/A for a VCF port; see the exact assertion and exclusion reason."
            )
            r["potential_vepyr_bug"] = "N/A: no VCF parity claim for this assertion."
            r["unsupported_features"] = (
                "Excluded by test contract, not automatically a missing vepyr feature."
            )
    for c in CASES.values():
        c["source_links"] = [ROWS[rid]["source_url"] for rid in c["source_assertions"]]
        c["implementation_links"] = [
            code_link(p, source, c) for p in c.pop("implementation_paths")
        ]
        c["vepyr_support_evidence"] = [API, CLI, HARNESS]
        c["primary_property"] = c["description"]
        c["directory_name"] = (
            re.sub(r"[^a-z0-9]+", "_", c["description"].lower()).strip("_")[:88]
            + "_"
            + c["id"][3:9]
        )
        c["comparison_note"] = (
            "One primary property per proposed fixture; current whole-body MD5 also checks incidental fields. Property-scoped comparisons would require a harness extension."
        )
        c["priority"] = PRIORITY[c["variant_class"]]
        c["porting_lane"] = (
            "extra feature/harness work"
            if not c["unsupported_features"].startswith("None identified")
            else "everything parity"
        )
    # Existing fixtures are reuse leads only; they are not 116.2 executions.
    prior = json.loads(
        (ROOT.parent / "2026-10-03-vep-data-test-candidates.json").read_text()
    )
    existing = prior["existing_data_fixtures"]
    (ROOT / "existing-fixtures.json").write_text(json.dumps(existing, indent=2) + "\n")
    for c in CASES.values():
        c["related_existing_fixtures"] = [
            dict(
                name=e["name"],
                url=e["url"],
                status="Related source range only; inspect focus before reuse. Regenerate the 116.0 oracle with 116.2.",
            )
            for e in existing
            if any(
                e["upstream"]["file"] == ROWS[rid]["file"]
                and e["upstream"]["line_start"]
                <= ROWS[rid]["line_start"]
                <= e["upstream"]["line_end"]
                for rid in c["source_assertions"]
            )
        ]
    cs = sorted(
        CASES.values(),
        key=lambda c: (
            c["priority"],
            c["porting_lane"] != "everything parity",
            c["witness_status"].startswith("release-116 witness required"),
            c["description"],
            c["id"],
        ),
    )
    rs = list(ROWS.values())
    counts = Counter(r["classification"] for r in rs)
    manifest = dict(
        upstream_pin=PIN,
        porting_repo_pin=PORT_PIN,
        vepyr_pin=VEPYR_PIN,
        assertion_sites=len(rs),
        test_files=51,
        atomic_candidates=len(cs),
        mapped_assertion_sites=sum(bool(r["atomic_tests"]) for r in rs),
        classification_counts=dict(sorted(counts.items())),
        candidate_variant_classes=dict(Counter(c["variant_class"] for c in cs)),
        candidate_lanes=dict(Counter(c["porting_lane"] for c in cs)),
        oracle_runs=0,
        unclassified=0,
        scope="All top-level t/*.t assertion sites at release/116.2 under the target ledger enumeration rule; not every historical assertion in 3,015 commits.",
    )
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (ROOT / "assertions.json").write_text(json.dumps(rs, indent=2) + "\n")
    (ROOT / "atomic-tests.json").write_text(json.dumps(cs, indent=2) + "\n")
    (ROOT / "assertions").mkdir(exist_ok=True)
    by_file = defaultdict(list)
    for r in rs:
        by_file[r["file"]].append(r)
    index = [
        "# All 2,103 assertion sites",
        "",
        "Every source assertion is represented once below. [Open the complete 2,103-row table](all-assertions.md), or use the per-file tables. Each includes exact descriptions, classifications, atomic-case mappings, and the three problem columns.",
        "",
        "| File | Assertions | Mapped to VCF properties | Excluded from VCF contract |",
        "|---|---:|---:|---:|",
    ]
    all_rows = [
        "# Complete assertion classification",
        "",
        "All 2,103 assertion sites at the pinned VEP 116.2 source. A mapped object assertion is a projection of observable properties, not coverage of every private member of the object.",
        "",
        "| Assertion / source lines | Original assertion | Classification / reason | Atomic tests | Old cache problem | Potential vepyr bug | Unsupported features / flags |",
        "|---|---|---|---|---|---|---|",
    ]
    for file, group in by_file.items():
        stem = Path(file).stem
        mapped = sum(bool(r["atomic_tests"]) for r in group)
        index.append(
            f"| [{file}](assertions/{stem}.md) | {len(group)} | {mapped} | {len(group) - mapped} |"
        )
        out = [
            f"# {file}",
            "",
            f"{len(group)} assertion sites; {mapped} mapped to observable VCF properties. A mapping can be partial: it does not claim equivalence of the complete Perl object or its runtime backend.",
            "",
            "| Assertion / source lines | Original assertion | Classification / reason | Atomic tests | Old cache problem | Potential vepyr bug | Unsupported features / flags |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in group:
            tests = (
                ", ".join(
                    f"[{cid}](../atomic-tests.md#{cid.lower()})"
                    for cid in r["atomic_tests"]
                )
                or "—"
            )
            if r["related_tests"]:
                tests += " Related only: " + ", ".join(
                    f"[{cid}](../atomic-tests.md#{cid.lower()})"
                    for cid in r["related_tests"]
                )
            row = (
                "| "
                + " | ".join(
                    [
                        f"[{r['id']} L{r['line_start']}]({r['source_url']})",
                        md(r["description"]),
                        md(r["classification"] + ": " + r["reason"]),
                        tests,
                        md(r["old_cache_problem"]),
                        md(r["potential_vepyr_bug"]),
                        md(r["unsupported_features"]),
                    ]
                )
                + " |"
            )
            out.append(row)
            all_rows.append(row.replace("../atomic-tests.md", "atomic-tests.md"))
        (ROOT / "assertions" / (stem + ".md")).write_text("\n".join(out) + "\n")
    index.extend(
        [
            f"| **Total** | **{len(rs)}** | **{manifest['mapped_assertion_sites']}** | **{len(rs) - manifest['mapped_assertion_sites']}** |",
            "",
            "[Complete machine-readable ledger](assertions.json). All rows retain the upstream source excerpt and its SHA-256.",
        ]
    )
    (ROOT / "assertions.md").write_text("\n".join(index) + "\n")
    (ROOT / "all-assertions.md").write_text("\n".join(all_rows) + "\n")
    out = [
        "# Atomic VCF port candidates",
        "",
        f"{len(cs)} single-purpose candidates, ordered SNVs first, then small indels. These are a classified backlog, not {len(cs)} executed fixtures. No 116.2 expected VCFs were generated by this audit. Conditional witnesses, unsupported options, and unsplit-input cases remain visible.",
        "",
        "Each row is one primary observable property. A full-body golden VCF checks incidental fields too; the current fixture runner cannot isolate one CSQ field. Reuse oracle computation for identical inputs/options, but do not claim separate behaviors from duplicate bytes alone.",
        "",
        "| Test | Short description / one property | Input recipe / readiness | VEP options beyond common setup | Specific upstream assertions | Implementation entrypoints | Old cache problem | Potential vepyr bug | Unsupported features / flags |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in cs:
        src = "; ".join(
            f"[{rid}]({ROWS[rid]['source_url']})" for rid in c["source_assertions"]
        )
        code = "; ".join(
            f"[{p['path'].split('VEP/')[-1]}:{p['line']} ({p['function']})]({p['url']})"
            for p in c["implementation_links"]
        )
        out.append(
            "| "
            + " | ".join(
                [
                    f'<a id="{c["id"].lower()}"></a>**{c["id"]}**<br>{c["variant_class"]}',
                    md(c["description"]),
                    md(c["input_recipe"] + " Readiness: " + c["witness_status"]),
                    md(" ".join(c["vep_flags"])),
                    src,
                    code,
                    md(c["old_cache_problem"]),
                    md(c["potential_vepyr_bug"]),
                    md(c["unsupported_features"]),
                ]
            )
            + " |"
        )
    (ROOT / "atomic-tests.md").write_text("\n".join(out) + "\n")
    # This is a review manifest, intentionally not named test.toml or accepted by
    # the fixture harness: there is no invented image digest or expected hash.
    toml = [
        "# Atomic port backlog, NOT executable fixture metadata.",
        "# Generate input.vcf and real VEP 116.2 expected_output.vcf before making test.toml.",
        f"upstream_revision = {json.dumps(PIN)}",
        "expected_vcfs_generated = false",
        "",
    ]
    for c in cs:
        toml.extend(
            [
                "[[test]]",
                f"id = {json.dumps(c['id'])}",
                f"name = {json.dumps(c['directory_name'])}",
                f"description = {json.dumps(c['description'])}",
                f"variant_class = {json.dumps(c['variant_class'])}",
                f"input_recipe = {json.dumps(c['input_recipe'])}",
                f"witness_status = {json.dumps(c['witness_status'])}",
                f"source_assertions = {json.dumps(c['source_assertions'])}",
                f"vep_test_pinned = {json.dumps(c['source_links'][0])}",
                f"vep_subject = {json.dumps(c['implementation_links'][0]['url'])}",
                f"old_cache_problem = {json.dumps(c['old_cache_problem'])}",
                f"potential_vepyr_bug = {json.dumps(c['potential_vepyr_bug'])}",
                f"unsupported_features = {json.dumps(c['unsupported_features'])}",
                "",
            ]
        )
    (ROOT / "port-manifest.toml").write_text("\n".join(toml))
    print(json.dumps(manifest, indent=2))
    return manifest, cs, rs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        required=True,
        type=Path,
        help="Immutable release/116.2 snapshot to verify and link",
    )
    args = parser.parse_args()
    finish(args.source)
