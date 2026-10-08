# Witness qualification runs

These are exploratory VEP 116.2 merged-cache runs, retained to explain replacement witnesses and blocked candidates. They are not ported tests and do not assert vepyr parity. Each executable port was subsequently normalized and run individually through both engines; see `../cases.json` for that evidence.

- `discovery`: original and replacement SNV loci.
- `discovery2`: feature boundaries, contig aliases and cache-region boundaries.
- `discovery3`: indels, lowercase alleles and mitochondrial names.
- `discovery4`: a current-cache insertion with a nonzero AMR frequency.

The annotated `M` and `MT` rows are deliberately retained: under the recorded configuration, only `MT` resolves to mitochondrial transcript consequences. The original chr21:25005812 insertion has no colocated frequency; the replacement chr21:8668876 insertion has AMR_AF=0.5634.
