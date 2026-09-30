---
name: impl-vepyr-data-test
description: Use when implementing, re-blessing, verifying or reviewing a data-test in biodatageeks/vepyr-porting-tests (tests/data/<slug>/ with input.vcf, expected_output.vcf, test.toml), when blessing a VEP 116 oracle, when a data-test PR needs acceptance-criteria evidence, or when reviewing a data-test PR ("review data-test PR", "re-bless", "verify a data-test").
---

# Implementing a vepyr data-test

`dt` = `scripts/dt` of this skill (`dt --help`: exit codes 0 pass, 1 check failed, 2 usage/config/input, 3 tool/unexpected). Config lookup: `$DT_CONFIG`, then `${XDG_CONFIG_HOME:-~/.config}/dt/local.toml`, then `local.toml` here (`DT_<KEY>` overrides; see `local.toml.example`). `dt` writes only into direct children of `tests/data/` of the checkout your cwd is in. It refuses the main checkout only as configured: `main_checkout` (legacy `repo`) is required, compared by inode (case/symlink variants caught); unset, empty, relative or missing -> `dt env` exits 2 and every write is refused. All path keys must be absolute (or `~/`); `scratch_root` inside any git checkout -> exit 2. `DT_ALLOW_MAIN=1` (owner only; implementers never set it) permits writes to the main checkout and turns that `dt env` check into a WARN.

`<scratch>` = a directory you create for this task only, e.g. `<docker_shared_root>/scratch-<N>-<ts>` (from local.toml; Docker must see it for bless). Never inside a checkout.

## Contract (origin/master; default branch is `master`, not `main`)

- One mode (#143/#149). `[vepyr]` has `everything = true`, `reference_fasta = true`, `preserve_record_layout = true`, `flavour = "ensembl"`, **no `fields`**; the runner derives the cache entities itself; `required_contigs`, optional `buffer_size` and `[[vepyr_run]]` come from the issue. Who enforces what:
  - the 3 keys mapped in `tools/vep_flags.toml`: `./bless` (`require_vepyr_mode`) and the loader reject another value (`unsupported mode`);
  - `fields`: only the loader rejects it (`unknown key`); bless accepts it;
  - `flavour = "ensembl"`: owner policy (README: the oracle is always Ensembl; #156). Bless and the loader accept `refseq`/`merged`; **`dt verify` (mode check) is the only gate**, so it must PASS.
  - Schema: `tests/data_dirs.rs` header, README "Porting method".
- `[origin] ledger` = README short form `"<Stem>.ledger.toml n=<N>"` (e.g. `"Runner.ledger.toml n=16"`); never a URL to a former source repository, even if older rows have one.
- Extra VEP flags: none. `--check_existing` is the only allowlisted one (`ALLOWED_VEP_FLAGS`, `tools/bless/vep.py`). `--everything` already enables it in VEP 116 (`Config.pm` `@OPTION_SETS`: everything -> af/pubmed -> check_existing), so adding it leaves the body and `body_md5` unchanged. It changes only the `##VEP-command-line` header and `[vep] command`/`extra_flags`. `dt` has no pass-through.
- Input: only `tools/normalize_input` (`bcftools norm -m -both`, no `-f`); VEP and vepyr read the same `input.vcf`; oracle is always VEP.

## Recipe (in order)

0. `git fetch origin && git worktree add --no-track -b issue-<N>-<slug> <scratch>/wt-<N> origin/master` (`--no-track`: the branch must not track master). cwd and env do not persist between your shell calls, so prefix EVERY shell call with `cd <scratch>/wt-<N> && export UV_PROJECT_ENVIRONMENT=<scratch>/venv-<N> && <commands>` (`export`, so it covers every command after `&&`) (no `.venv` in a checkout; `dt env` FAILs without it).
1. Gate, from the worktree: `gh issue view N --json body --jq .body > <scratch>/issue-N-body-<ts>.md && ./issue_check --body-file <scratch>/issue-N-body-<ts>.md` -> exit 0, AND your brief says the owner approved the issue. Otherwise remove the worktree (step 13), STOP and report. Then `dt env` -> all PASS (repo = your worktree, base contains origin/master).
2. Scratch raw VCF (outside the repo): `##fileformat`, `##contig=<ID=21,length=...>`, the issue's fixture records, contig `21`.
3. `dt refcheck <raw> --negative-control` (before blessing).
4. `dt raw2input --raw <raw> --dir tests/data/<slug>`.
5. Issue names a fixture: `dt fixture-match --input tests/data/<slug> --fixture <src> --records N`.
6. Hand-write the non-bless keys of `test.toml` per the Contract. `[origin]`: `issue = N`; `vep_test`, `vep_test_pinned` = the two URLs of the form field "VEP test link"; `vep_subject` (VEP module permalink at `57ea5c52`) and `ledger` (`<Stem>.ledger.toml n=<N>`, per the Contract) = the issue body's `[origin] vep_subject:` / `[origin] ledger:` bullets (under "VEP test link"; `data-test.yml` has no field of their own). A required key (`vep_test`, `vep_test_pinned`, `vep_subject`) the issue does not state -> STOP and report which one is missing; never invent it.
7. `dt bless tests/data/<slug>`.
8. `dt verify --reproduce tests/data/<slug>` (background; checks mode, prints the engine sha from `Cargo.lock`: cite that sha, never a vepyr version). Also `./check_normalised_input` -> exit 0, and the issue's own cargo AC (`cargo test --test data_dirs data_dirs -- --nocapture` on a scratch `DATA_DIRS_ROOT`) verbatim, with `VEPYR_CACHE_ROOT` (the `env vepyr_cache_root` line) and `CARGO_TARGET_DIR` (the `# CARGO_TARGET_DIR` line) from `dt env` set.
9. `tools/build_test_index` regenerates `tests/INDEX.csv`; `tools/build_test_index --check` -> 0. Negative: `sed '$d' tests/INDEX.csv > <scratch>/idx.csv && tools/build_test_index --check --out <scratch>/idx.csv` -> 1. Never hand-edit the CSV.
10. `dt report tests/data/<slug>` -> PR body table. Commit only the directory and `tests/INDEX.csv`, with exactly the attribution lines given by your session. Publish (never to master, never `--force`, only your own branch): `git push -u origin issue-<N>-<slug>`, then `gh pr create -R biodatageeks/vepyr-porting-tests --base master --head issue-<N>-<slug> --title "[data-test] <issue title> (#N)" --label data-test --milestone <issue's> --body-file <scratch>/pr-<N>-body-<ts>.md`; body English, `Closes #N`, the `dt report` table, ending with the attribution line your session gives (label/title per `gh pr list -R biodatageeks/vepyr-porting-tests --state merged --limit 8 --json labels,title`: the issue's template label).
11. Sticky status comment (`AGENTS.md`, "Issue and pull request lifecycle"): right after `gh pr create`, `./set_state pr <PR> implementing`. Then ONE issue comment on the PR whose first line is `### pr-status:v1`: a table of EVERY local check run in steps 0-9, not only the ACs (`./issue_check` gate, `dt env`/`refcheck`/`raw2input`/`verify`, `./check_normalised_input`, `tools/build_test_index --check` and its negative, the cargo AC, `./bless --check`/`--reproduce`), each with command, exit code, expected exit, head sha, evidence level (scaffold vs full: real VEP/Docker/cargo), failed checks too; below it the fenced `json` block of the same rows (format: `resolve-pr/reference.md`). Write it to a unique `<scratch>/pr-<N>-status-<ts>.md`, read it back, post it once with `gh pr comment <PR> --body-file`, and verify with `gh api repos/<o>/<r>/issues/comments/<id> --jq .body`. Later results edit that same comment in place (`gh api -X PATCH repos/<o>/<r>/issues/comments/<id> -F body=@<file>`); never a second one. Sign `— Claude-<n>` (rule defined by the `resolve-pr` skill, step 2: n = highest existing `— Claude-<n>` on the PR + 1, else 1).
12. Before any later push, with `<PR>` = the PR number (not the issue `N`); fails closed in bash and zsh:
    ```sh
    labels=$(gh pr view <PR> -R biodatageeks/vepyr-porting-tests --json labels --jq '.labels[].name') \
      || { echo "gh failed: STOP, do not push"; exit 2; }
    printf '%s\n' "$labels" | grep -qx state:awaiting-merge && { echo "state:awaiting-merge: frozen, STOP, ask the owner"; exit 1; }
    printf '%s\n' "$labels" | grep -qxE 'state:(implementing|fixing)' || { echo "not in state:implementing or state:fixing: run ./set_state pr <PR> fixing first"; exit 1; }
    echo "push allowed"
    ```
    Only "push allowed" allows the push. `state:awaiting-merge` means frozen: STOP, ask the owner, then `./set_state pr <PR> fixing`. Any other state: `./set_state pr <PR> fixing` first. Before the push set `"stale": true` in the sticky status comment; after it re-run the checks and rewrite the sticky rows with the new sha.
13. Cleanup, then report `du -sh` of what you removed: `git worktree remove <wt>`, the `CARGO_TARGET_DIR` `dt env` printed, leftover `<docker_shared_root>/.bless-work-<slug>-*`, `<scratch>/venv-<N>` (from step 0), and last, after the evidence is posted and read back, `<scratch>` itself. Never delete `.bless-cache-e2e/` or the main checkout's `target/`.
14. STOP and report to the manager: PR URL, sticky status comment URL, cleanup done. The manager dispatches the fresh reviewer. Never merge, never `--delete-branch`.

Every negative control must run only after its positive passed (`dt` marks it FAIL "not run" otherwise).

## Quick reference

| command | proves |
|---|---|
| `dt env` | config, repo = cwd checkout (not main), base; then `./check_env` with the configured paths: `UV_PROJECT_ENVIRONMENT` outside checkouts, uv/cargo/git, bcftools pin, docker daemon, vepyr cache (provenance, pinned revisions, FASTA, dataset directory per recorded flavour), VEP cache, VEP FASTA (+ `.fai`) |
| `dt refcheck <vcf\|dir> [--negative-control]` | every REF = GRCh38 base(s) at POS |
| `dt raw2input --raw R --dir D` | `tools/normalize_input`, raw->input diff, `cmp` idempotence |
| `dt fixture-match --input I --fixture F --records N [--by-pos] [--rust-const NAME]` | first N records = fixture; F = path, URL or `git:<repo>:<rev>:<path>` |
| `dt bless D` | `./bless` in a temporary Docker-shared dir |
| `dt verify D [--reproduce] [--no-cargo]` | `./check_test_dir` (files, input-records, order, oracle-meta, one-to-one; no opt-out), mode, idempotence, md5, REF, `bless --check` (+tamper), runner (+flip) |
| `dt report D` | size, sha256, origin table |

zsh: always brace, `${REPO}:...`; `$REPO:c`, `:h`, `:t`, `:r` (and `:e :a :A :l :u :q`) are modifiers. `--fixture git:https://...` breaks (split at the URL colon): pass a raw.githubusercontent URL or a local path.

## STOP and report options (never decide)

- vepyr body md5 != VEP: report `VEP:`/`vepyr:` lines; never touch the oracle. Never file or comment upstream (vepyr, dfbf, Ensembl); the owner decides.
- REF != FASTA.
- Issue asks for a value outside the Contract (another flag, flavour, `fields`, `everything = false`) or leaves `required_contigs`/`[origin]` values open.
- Issue contradicts code; `dt` exits 2/3 and the cause is not your input.

## Red flags

| thought | reality |
|---|---|
| "Copy `[vepyr]`/`fields` from the old test" | Pre-#149 values; the loader rejects them. Use the Contract. |
| "Edit the oracle so md5 matches" | Only `./bless` writes it. |
| "REF mismatch is harmless" | Oracle pins garbage (#17). |
| "Use chr21 so vepyr is happy" | Owner decided `21`; `required_contigs` names shards (`chr21`). |
| "`cargo test <slug>`" / real `tests/data` as `DATA_DIRS_ROOT` | No per-dir filter; breaks the selftest. Always a scratch copy. |
| "`dt verify` has no `[slug] ok`, so the cargo AC is unproven" | Run the issue's cargo AC itself and cite its output. |
| "Reuse the main `target/`" | Binary embeds its checkout path -> PINS.toml panic. |
| "Scratch files in the repo / commit raw.vcf" | Scratch lives outside; raw not committed (#90). |
| "Plain `bcftools norm` by hand" / "add `-f`" | Not idempotent / forbidden. Use `tools/normalize_input`. |
