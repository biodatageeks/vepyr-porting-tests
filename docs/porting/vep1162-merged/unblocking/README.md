# Remaining-candidate assessment

The follow-up ports three previously blocked SNVs. All three were normalized with
bcftools 1.23 (`norm -m -both`) before either engine read them. VEP 116.2 Docker
and remote vepyr master `b14bda3d4cf80c0f16d62ab371c28866ae589c33` received identical
bytes and produced identical VCF bodies. This is a final partial batch of three,
not a batch padded to ten with unrelated tests.

The campaign now contains **189 ports: 177 PASS, 10 FAIL, 2 ERROR**. Thirteen
candidates remain unported: ten blockers and three outside the data-test contract.
They retain `BLOCKED` in the campaign schema; `blocker_category` distinguishes
their dispositions. No previously failing fixture or expected output was changed.

## Newly qualified inputs

| Candidate | Normalized input | Primary assertion | Both body MD5s |
|---|---|---|---|
| [DT-70fe973dcf](../../../../tests/data/exclude_a_failed_cache_record_by_default_70fe97/test.toml) | `21:5230609 A>G` | Failed known variant `rs1979841086` is excluded by default. | `e183a908bc4fa5fba1d3ad3b3b8b884b` |
| [DT-eee329ca1e](../../../../tests/data/include_a_coordinate_matched_known_record_with_unknown_alleles_eee329/test.toml) | `21:10521658 G>A` | Coordinate matching includes unknown-allele record `COSV53365562`. | `1c0932086f43d0851f16c318bfc36b10` |
| [DT-4d3b3ca340](../../../../tests/data/recover_a_missing_hgnc_id_from_sibling_transcripts_4d3b3c/test.toml) | `21:5499023 C>T` | `XR_951086.3` receives restored `HGNC:52458`. | `37fab4a6056170d19f6710b4667477af` |

The first discovery was too restrictive in three ways:

- Failed records can have their identifier in `dbsnp_ids` while `variation_name`
  is null. The selected record has `failed=true` and `allele_string=dbSNP_novariation`.
  The same normalized input with diagnostic VEP `--failed 1` emits the identifier;
  see the [positive-control VCF](failed-positive-control.vcf) and
  [run log](failed-positive-control.log). This control is evidence for filtering,
  not an extra flag in the committed fixture.
- Unknown alleles include the sentinel `COSMIC_MUTATION`. It exercises the same
  no-slash matching branch as the synthetic `NULL` in the upstream assertion.
  Restricting the search to missing values or literal `NULL` missed this record.
- HGNC restoration happens at annotation time; conversion need not populate the
  field. The unmodified native cache lacks the ID for `XR_951086.3`.
  [Native before/after results](hgnc-native.json) show it restored by
  [merge_features](https://github.com/Ensembl/ensembl-vep/blob/2cb0bbe216bb31c75de8f8000e2da7ff4fb7b451/modules/Bio/EnsEMBL/VEP/AnnotationType/Transcript.pm#L266-L308).
  Removing same-symbol donor IDs in an in-memory diagnostic prevents restoration.
  [The diagnostic](hgnc_native.pl) mounts the original cache read-only; no cache
  file or ported fixture uses the mutated objects.

[Qualification commands and hashes](qualification.json) precede the independent
port runs recorded in [cases.json](../cases.json). The three new REF alleles were
looked up in the supplied FASTA and match it. Cache fingerprints already cover
chromosome 21 and the FASTA; this follow-up adds no annotation contig.

## Read-only cache scan

[scan.json](scan.json) records counts, bounded candidate samples and exact shard
paths. Counts are cache rows, **not unique variants**; no synthetic cache was made.

| Entity | Files scanned | Rows scanned |
|---|---:|---:|
| Transcripts | 1,869 | 903,421 |
| Regulatory features | 24 | 380,956 |
| Motifs | 24 | 999,828 |
| Known variants | 463 | 1,487,415,922 |

The scan includes supplied alternate/LRG shards. It found 171,796 named failed
records, 17,019,852 nonfailed named point records without slash-separated alleles,
and no one-base transcript, regulatory or motif features. These are discovery
counts, not additional qualified or ported tests. HGNC candidates were matched by
chromosome and symbol before qualifying the single native-cache witness above.

Run [scan.py](scan.py) with the vepyr environment, which supplies Polars:

```bash
/path/to/vepyr/.venv/bin/python docs/porting/vep1162-merged/unblocking/scan.py \
  --cache-root /path/to/cache/116_GRCh38_merged \
  --output-dir /path/to/new-scan-evidence
```

The string search covers the named scalar columns in the script, not arbitrary
plugin output. [field-scan.json](field-scan.json) separates the transcript fields;
[clinical-scan.json](clinical-scan.json) lists all 360 distinct nonnull `clin_sig`
values found across the 463 variation shards. The only scanned transcript scalar
containing whitespace was the internal `source` value `Curated Genomic`, which is
not the merged `SOURCE=RefSeq` output. Commas in cached lists are delimiters, not
evidence that a scalar escaping branch was reached.

## Disposition of the remaining thirteen

Every individual candidate retains its pinned source-test and implementation
links in the [complete campaign table](../README.md).

| Candidates | Count | Remaining obstacle | What would unblock a faithful test |
|---|---:|---|---|
| DT-6adabc8798, DT-d88b9f082d, DT-aeb77516bf, DT-866260258b | 4 | Whitespace, semicolon, comma and pipe escaping: upstream injects non-VCF scalar `Allele` values. No emitted scalar witness qualified in the supplied cache. | A real annotation value containing the relevant character, or a separately supported matched custom-annotation mode. List splitting/joining is not a substitute. [Source](https://github.com/Ensembl/ensembl-vep/blob/2cb0bbe216bb31c75de8f8000e2da7ff4fb7b451/t/OutputFactory_VCF.t#L226-L247) |
| DT-c3cf584eb6 | 1 | Upstream requests the reference allele's frequency complement. Ordinary consequence output concerns ALT; REF=ALT does not produce the required CSQ. No nonfailed simple biallelic SNV with global AF only for its first/reference allele was found in the converted cache. | A real VCF witness that reaches interpolation while preserving the intended assertion; otherwise keep the direct frequency-helper test. [Source](https://github.com/Ensembl/ensembl-vep/blob/2cb0bbe216bb31c75de8f8000e2da7ff4fb7b451/t/OutputFactory.t#L713-L717) |
| DT-7622311251 | 1 | Exact one-base overlap query; no one-base transcript/regulatory/motif feature in the supplied cache. | A matched annotation source containing such a feature; an ordinary SNV overlapping a longer feature does not prove this boundary case. [Source](https://github.com/Ensembl/ensembl-vep/blob/2cb0bbe216bb31c75de8f8000e2da7ff4fb7b451/t/InputBuffer.t#L134-L138) |
| DT-02516cae06, DT-e9dbf34492 | 2 | M-to-MT and bare-input/chr-prefixed-source configurations are not provided by the current native cache and harness mode. | An independently prepared matching source/synonym configuration plus explicit support in both runners. Shard filenames alone do not prove the source configuration. |
| DT-6dc0ecf2c5, DT-a49e2146f6 | 2 | Variation strand is lost during conversion and assumed positive during lookup. | Implement [vepyr#156](https://github.com/biodatageeks/vepyr/issues/156), rebuild matched caches, then qualify a real reverse-strand witness. The fix is necessary but does not itself supply that witness. |
| DT-c93dc8f127, DT-0521713a8a | 2 | Readthrough/artifact filtering assertions operate on a test database with positive controls. Offline cache absence proves neither. | Keep as database integration tests; outside this offline VCF-to-VCF campaign. |
| DT-a62ed30be3 | 1 | CRLF format detection occurs before annotation; required normalization rewrites CRLF to LF. | Keep as a raw-parser test; outside this normalized VCF-to-VCF campaign. |

Only the two strand cases currently establish an additional engine capability
gap, already tracked in #156. No further vepyr issue is justified by the other
eleven candidates: the missing ingredient is data/configuration, or the original
assertion belongs to a different test layer. The nine previously filed issues
for the twelve executable FAIL/ERROR cases remain separate and unchanged.
