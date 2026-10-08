import argparse
import json
import time
from pathlib import Path

import polars as pl

parser = argparse.ArgumentParser(
    description="Read-only witness scan of the supplied release-116 merged cache."
)
parser.add_argument("--cache-root", type=Path, required=True)
parser.add_argument("--output-dir", type=Path, required=True)
args = parser.parse_args()
ROOT = args.cache_root
OUT = args.output_dir
for entity in ["transcript", "regulatory", "motif", "variation"]:
    if not any((ROOT / entity).rglob("*.parquet")):
        parser.error(f"no {entity} parquet shards in {ROOT}")
OUT.mkdir(parents=True, exist_ok=True)
report = {"root": str(ROOT), "entities": {}, "findings": {}}
t = time.time()


def record(key, df, limit=30):
    entry = report["findings"].setdefault(key, {"count": 0, "samples": []})
    entry["count"] += df.height
    if len(entry["samples"]) < limit:
        entry["samples"].extend(df.head(limit - len(entry["samples"])).to_dicts())


def save():
    report["seconds"] = round(time.time() - t, 2)
    (OUT / "scan.json").write_text(json.dumps(report, indent=2) + "\n")


for entity in ["transcript", "regulatory", "motif"]:
    paths = sorted((ROOT / entity).rglob("*.parquet"))
    total = 0
    output_fields = [
        "stable_id",
        "gene_stable_id",
        "gene_symbol",
        "gene_symbol_source",
        "gene_hgnc_id",
        "gene_hgnc_id_native",
        "refseq_id",
        "display_xref_id",
        "source",
        "refseq_match",
        "appris",
        "mane_select",
        "mane_plus_clinical",
        "ccds",
        "swissprot",
        "trembl",
        "uniparc",
        "uniprot_isoform",
        "flags_str",
        "motif_id",
        "transcription_factors",
        "cell_types",
        "feature_type",
    ]
    hgnc = []
    for path in paths:
        schema = pl.read_parquet_schema(path)
        cols = ["chrom", "start", "end"] + [
            c for c in output_fields if c in schema and schema[c] == pl.String
        ]
        d = pl.read_parquet(path, columns=cols).with_columns(
            pl.lit(str(path.relative_to(ROOT))).alias("shard")
        )
        total += d.height
        record(entity + "_one_base", d.filter(pl.col("start") == pl.col("end")))
        for char, pat in [
            ("whitespace", r"\s"),
            ("semicolon", ";"),
            ("comma", ","),
            ("pipe", r"\|"),
        ]:
            for col in cols[3:]:
                hits = d.filter(pl.col(col).str.contains(pat)).select(
                    "chrom",
                    "start",
                    "end",
                    "shard",
                    pl.lit(col).alias("field"),
                    pl.col(col).alias("value"),
                )
                record(entity + "_" + char, hits)
        if entity == "transcript":
            missing = d.filter(
                (
                    pl.col("gene_hgnc_id_native").is_null()
                    | (pl.col("gene_hgnc_id_native") == "")
                )
                & pl.col("gene_hgnc_id").is_not_null()
                & (pl.col("gene_hgnc_id") != "")
            )
            record("hgnc_native_missing_promoted_present", missing, 80)
            hgnc.append(
                d.select(
                    "chrom",
                    "start",
                    "end",
                    "stable_id",
                    "gene_symbol",
                    "gene_hgnc_id",
                    "gene_hgnc_id_native",
                    "shard",
                )
            )
    if hgnc:
        d = pl.concat(hgnc)
        donors = (
            d.filter(
                pl.col("gene_hgnc_id_native").is_not_null()
                & (pl.col("gene_hgnc_id_native") != "")
            )
            .select("chrom", "gene_symbol", "gene_hgnc_id_native")
            .unique()
        )
        missing = d.filter(
            (
                pl.col("gene_hgnc_id_native").is_null()
                | (pl.col("gene_hgnc_id_native") == "")
            )
            & pl.col("gene_symbol").is_not_null()
            & (pl.col("gene_symbol") != "")
        )
        record(
            "hgnc_missing_with_same_chrom_symbol_donor",
            missing.join(
                donors, on=["chrom", "gene_symbol"], how="inner", suffix="_donor"
            ),
            100,
        )
    report["entities"][entity] = {"files": len(paths), "rows": total}
    save()
    print(entity, total, "done", flush=True)
paths = sorted(
    (ROOT / "variation").rglob("*.parquet"),
    key=lambda p: ("chr21" not in str(p), str(p)),
)
total = 0
for i, path in enumerate(paths):
    d = pl.read_parquet(
        path,
        columns=[
            "chrom",
            "start",
            "end",
            "allele_string",
            "failed",
            "variation_name",
            "dbsnp_ids",
            "af_global_alleles",
            "af_global_freqs",
        ],
    ).with_columns(
        pl.coalesce("variation_name", "dbsnp_ids").alias("effective_name"),
        pl.lit(str(path.relative_to(ROOT))).alias("shard"),
    )
    total += d.height
    small = d.drop("af_global_alleles", "af_global_freqs")
    record(
        "failed_named",
        small.filter(pl.col("failed") & pl.col("effective_name").is_not_null()),
    )
    record(
        "failed_named_simple_snv",
        small.filter(
            pl.col("failed")
            & pl.col("effective_name").is_not_null()
            & pl.col("allele_string").str.contains(r"^[ACGT]/[ACGT]$")
        ),
        60,
    )
    record(
        "unknown_named",
        small.filter(
            ~pl.col("failed")
            & pl.col("effective_name").is_not_null()
            & ~pl.col("allele_string").str.contains("/")
        ),
    )
    record(
        "unknown_named_point",
        small.filter(
            ~pl.col("failed")
            & pl.col("effective_name").is_not_null()
            & ~pl.col("allele_string").str.contains("/")
            & (pl.col("start") == pl.col("end"))
        ),
        60,
    )
    # First listed cache allele is the reference; AF contains only this allele.
    record(
        "reference_only_af",
        d.filter(
            ~pl.col("failed")
            & pl.col("allele_string").str.contains(r"^[ACGT]/[ACGT]$")
            & (pl.col("af_global_alleles").list.len() == 1)
            & (
                pl.col("af_global_alleles").list.first()
                == pl.col("allele_string").str.slice(0, 1)
            )
        ),
        60,
    )
    for char, pat in [
        ("whitespace", r"\s"),
        ("semicolon", ";"),
        ("comma", ","),
        ("pipe", r"\|"),
    ]:
        record(
            "variation_name_" + char,
            small.filter(pl.col("effective_name").str.contains(pat)),
        )
    report["entities"]["variation"] = {
        "files_completed": i + 1,
        "files_total": len(paths),
        "rows": total,
    }
    if i % 20 == 0:
        save()
        print("variation", i + 1, len(paths), total, flush=True)
save()
print("DONE", round(time.time() - t, 1), flush=True)
