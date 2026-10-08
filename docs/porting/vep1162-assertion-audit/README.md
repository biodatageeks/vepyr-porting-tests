# VEP 116.2: complete assertion classification and atomic port backlog

This is the pre-execution classification snapshot. The [execution table](../vep1162-merged/README.md) records the subsequent normalized merged-cache runs. Of the 203 feature-scope candidates below, the campaign selects 202 strict `--everything` candidates: `DT-10710668d8` additionally requires `--buffer_size 10` and remains outside this campaign. Snapshot statements about zero oracle runs describe this audit stage only.

All **2,103 assertion sites in 51 upstream test files** are classified. The
decomposition contains **831 single-purpose VCF port candidates**, ordered SNVs
first, then small indels. It supersedes the earlier 40-family seed inventory.

| Deliverable | Contents |
|---|---|
| [Full assertion table](all-assertions.md) | All 2,103 rows, exact upstream lines, original descriptions, classification, case mappings, exclusions, and the three requested problem columns |
| [Per-file assertion index](assertions.md) | The same audit split into 51 smaller tables, with reconciled counts |
| [Full atomic test table](atomic-tests.md) | All 831 proposed tests, one primary property per row, input recipes, options, test links, implementation entrypoints, and blockers |
| [TOML port manifest](port-manifest.toml) | Suggested directory names and short descriptions, with provenance and problem columns; a backlog manifest, **not executable `test.toml` files** |
| [Assertion JSON](assertions.json) / [candidate JSON](atomic-tests.json) | Complete machine-readable mappings, source excerpts, code links, support evidence, and related existing fixtures |
| [Reconciled counts](manifest.json) | Source pins, counts, classifications, and explicit zero-oracle-run status |

## What the counts mean

| Assertion disposition | Sites |
|---|---:|
| Mapped to one or more observable VCF properties | 566 |
| Excluded from this VCF contract, with an explicit reason | 1,537 |
| **Total** | **2,103** |
| Unclassified | **0** |

The 566 mapped sites expand to 831 candidates because compound hashes, selected
loop cases, and output lines contain multiple independent properties. Conversely,
identical observable properties across cache backends and output writers share
case IDs. This is a many-to-many mapping, not a conversion of 2,103 statements
into 2,103 distinct data tests.

| Candidate scope | Count |
|---|---:|
| SNVs | 637 |
| Small indels, including mixed-ALT cases containing an indel | 88 |
| Nonvariant records | 3 |
| MNVs / padded substitutions | 9 |
| Structural variants / symbolic repeats | 94 |
| **Total** | **831** |

**203 candidates fit the `--everything` feature scope without an identified
extra-option/harness blocker:** 186 SNVs, 15 small indels, one nonvariant case,
and one MNV. That count does **not** mean 203 are runnable now: some still need
a concrete current-cache witness or source-input extraction. The other 628
remain in the table with their feature, harness, normalization, or scope blocker.

## Interpretation and limits

Every candidate has one **primary observable property**. For example, a coding
hash is split into separate HGVSc, HGVSp, CDS position, protein position, amino
acid, and codon tests. Each population frequency and motif field has its own
entry. The regulatory extension loops become separate position/option cases;
the 20-record nearest-symbol assertion is split by record; BAM edit arrays are
split by edit site. The GFF/GTF biotype maps contribute 165 separate transcript/model cases; all require custom-source support and a concrete SNV witness.

The example fixture format compares the **entire non-header VCF body**. Giving
two fixtures different descriptions while retaining identical input, options,
and expected output does not create independent field-level coverage. The list
is a decomposition of purposes; the existing whole-body comparator still checks
incidental fields. Strictly isolated field assertions would require an explicit
harness extension. No unsupported comparison keys were invented in executable
fixture metadata. Identical inputs/options can share one oracle computation.

An object-to-VCF **projection** tests only the observable property named in its
row. It does not establish Perl class identity, internal cache state, a tabix
backend choice, a complete transcript sequence, or JSON/tab serialization.
Those distinctions remain visible in the original assertion description and
classification. A synthetic cache/transcript mutation is marked as requiring
a real witness or controlled auxiliary asset; it is not claimed to work by
changing only the input VCF.

The three problem columns deliberately use different evidence:

- **Old cache problem:** release-84 literals, obsolete identifiers, allele or
  reference discrepancies, and the need to qualify a release-116 witness.
- **Potential vepyr bug:** an identified prior issue where relevant; otherwise
  “unassessed.” No issue is asserted to remain reproducible on current code.
- **Unsupported features / flags:** public API/CLI gaps, fixture-harness gaps,
  auxiliary-source requirements, and normalization conflicts. These are not
  automatically engine defects.

Picking, distance limits, field selection, `failed`, `all_refseq`,
`gencode_basic`, and HGVS shifting have public vepyr API options. The fixture
runner does not expose those options. RefSeq/merged cache flavours and
`allow_non_variant` also have harness limitations rather than an automatic
“unsupported by vepyr” verdict. See the pinned
[Python API](https://github.com/biodatageeks/vepyr/blob/b14bda3d4cf80c0f16d62ab371c28866ae589c33/src/vepyr/__init__.py#L1011-L1058),
[CLI](https://github.com/biodatageeks/vepyr/blob/b14bda3d4cf80c0f16d62ab371c28866ae589c33/src/vepyr/cli.py#L81-L134),
and [fixture loader](https://github.com/biodatageeks/vepyr-porting-tests/blob/2cdf45b73f31c6001a434cd76e5863ab41fac53b/tests/data_dirs.rs).

Some exclusions are substantive: VEP declares `--most_severe` and `--summary`
incompatible with VCF output. Non-VCF input parsing, error messages, file
handles, statistics, Recoder, and Haplosaurus do not become equivalent tests
by replacing their input or output with VCF. The exact
[configuration rule](https://github.com/Ensembl/ensembl-vep/blob/2cb0bbe216bb31c75de8f8000e2da7ff4fb7b451/modules/Bio/EnsEMBL/VEP/Config.pm#L616-L619)
is linked for the summary-mode exclusions.

## Required oracle procedure

**No VEP 116.2 expected VCFs or vepyr differential results were generated by
this audit.** Every candidate explicitly records that status. Current upstream
tests still configure a
[release-84 microcache](https://github.com/Ensembl/ensembl-vep/blob/2cb0bbe216bb31c75de8f8000e2da7ff4fb7b451/t/VEPTestingConfig.pm#L24-L48).
Its expectation literals must not be copied into new expected VCFs.

1. Qualify the smallest input that demonstrates the named property against
   release-116 Ensembl GRCh38 cache and its matched FASTA. Retain intentional
   bad-REF inputs; any corrected-reference analog must be separately labelled.
2. Record raw input and normalize with `bcftools norm -m -both`. Do not silently
   add FASTA-based left alignment or REF repair. Cases whose original ALT
   indices/rejoin behavior disappear after splitting need a separately supported
   unsplit-input lane; they remain blocked in the table.
3. Run the identical normalized input through pinned **VEP release/116.2** with
   `--offline --cache --cache_version 116 --assembly GRCh38 --fasta ...
   --everything --vcf`, plus only the case-specific compatible options. Database
   and external-source cases need a separately documented configuration.
4. Record the actual executable/container digest, VEP version output, cache and
   FASTA checksums, command, date, input checksum and expected-body checksum.
   The inspected
   [blessing tool](https://github.com/biodatageeks/vepyr-porting-tests/blob/2cdf45b73f31c6001a434cd76e5863ab41fac53b/tools/bless/vep.py#L30)
   still selects 116.0 and must be updated before blessing 116.2 fixtures.
5. Compare vepyr against that oracle, then triage an observed mismatch into the
   three problem columns. Store `input.vcf`, `expected_output.vcf`, and real
   `test.toml` provenance in the intended fixture directory.

The [16 existing fixtures](existing-fixtures.json) are reuse leads. Their source
ranges are attached to related candidates in JSON; that association does not
claim they already isolate the new candidate's property. They also need 116.2
oracles.

## Audit scope and reproducibility

The source is the immutable tag commit
[`2cb0bbe216bb31c75de8f8000e2da7ff4fb7b451`](https://github.com/Ensembl/ensembl-vep/tree/2cb0bbe216bb31c75de8f8000e2da7ff4fb7b451).
This audits the entire current top-level `t/*.t` assertion universe, **not every
historical assertion across all 3,015 reachable commits**. The target's 22-name
line-start enumeration rule counts a loop assertion once; it is not a TAP
execution count. All 1,965 legacy sites were matched to 116.2 source lines,
and the 138 additional sites were included. Legacy classifications were not
inherited as verdicts.

The [builder](build_audit.py) contains the curated decomposition and exclusion
policies. It re-enumerates the source snapshot, verifies the exact 2,103 sites,
resolves implementation links to existing functions, and generates the tables
and manifests. Implementation links are entrypoints; biological calculations
can delegate to Ensembl dependencies.

```sh
git -C ../ensembl-vep archive release/116.2 > /tmp/vep-116.2-audit.tar
mkdir -p /tmp/vep-116.2-audit
tar -xf /tmp/vep-116.2-audit.tar -C /tmp/vep-116.2-audit
python3 docs/porting/vep1162-assertion-audit/build_audit.py \
  --source /tmp/vep-116.2-audit
```

To extend discovery through history efficiently, inspect commits touching a
test or implementation path only after the current-tree mapping exposes a
specific gap. Batch by changed path; deduplicate cherry-picks; distinguish
removed historical regressions from assertions already present in this audit.
Do not fetch and reread thousands of GitHub commit pages individually.
