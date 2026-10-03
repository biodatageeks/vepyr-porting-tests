"""Run curated VEP 116.2 merged-cache ports, retaining positive and negative evidence.

Only cases with explicit input rows and a qualified focus assertion are run.
The default batch size is ten. A failing vepyr result never changes the oracle.
Local caches are accepted as requested for this campaign; no Hub provenance is
invented for them. Engine revision is run evidence, not a repository pin.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / 'docs/porting/vep1162-merged/cases.json'


def body(path):
    return b''.join(x for x in path.read_bytes().splitlines(keepends=True) if not x.startswith(b'#'))


def csq(path):
    fields = None
    result = []
    for line in path.read_text().splitlines():
        if line.startswith('##INFO=<ID=CSQ,'):
            fields = line.split('Format: ', 1)[1].split('"', 1)[0].split('|')
        if line.startswith('#'):
            continue
        columns = line.split('\t')
        for item in columns[7].split(';'):
            if item.startswith('CSQ='):
                if fields is None:
                    raise ValueError(f'{path}: missing CSQ header')
                result.extend(dict(zip(fields, e.split('|'), strict=True)) for e in item[4:].split(','))
    return result


def focus_value(path, focus):
    entries = [e for e in csq(path) if all(e.get(k) == v for k, v in focus['where'].items())]
    if len(entries) != 1:
        raise ValueError(f'focus selects {len(entries)} entries, expected exactly one')
    return entries[0][focus['field']]


def run(argv, log, cwd=ROOT):
    with log.open('w') as stream:
        result = subprocess.run(argv, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT)
    return {'argv': [str(a) for a in argv], 'exit': result.returncode, 'log': str(log)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vep-cache', type=Path, required=True)
    p.add_argument('--vepyr-cache', type=Path, required=True)
    p.add_argument('--fasta', type=Path, required=True)
    p.add_argument('--vepyr-python', type=Path, required=True)
    p.add_argument('--vepyr-source', type=Path, required=True)
    p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--limit', type=int, default=10)
    args = p.parse_args()
    if args.limit <= 0:
        p.error('--limit must be positive')
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=args.vepyr_source, text=True).strip()
    if subprocess.check_output(['git', 'diff', 'HEAD', '--', 'src', 'Cargo.toml', 'Cargo.lock'], cwd=args.vepyr_source):
        p.error('vepyr source has tracked modifications')
    cases = json.loads(MANIFEST.read_text())
    todo = sorted((c for c in cases if c['status'] == 'QUEUED' and 'focus' in c and 'rows' in c), key=lambda c:c.get('batch_order', 1000))[:args.limit]
    if not todo:
        p.error('no qualified cases remain')
    for case in todo:
        name = case['directory_name']
        dest = ROOT / 'tests/data' / name
        dest.mkdir(exist_ok=False)
        evidence = args.evidence / name
        evidence.mkdir(parents=True, exist_ok=False)
        contigs = list(dict.fromkeys(r.split('\t')[0] for r in case['rows']))
        raw = evidence / 'raw.vcf'
        raw.write_text('##fileformat=VCFv4.2\n' + ''.join(f'##contig=<ID={c}>\n' for c in contigs) + '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n' + '\n'.join(case['rows']) + '\n')
        q = json.dumps
        pinned = case['source_links'][0]
        subject = case['implementation_links'][0]['url']
        (dest / 'test.toml').write_text(
            f'name = {q(name)}\ndescription = {q(case["description"])}\n\n'
            f'[origin]\nvep_test = {q(pinned)}\nvep_test_pinned = {q(pinned)}\nvep_subject = {q(subject)}\nissue = 226\n\n'
            f'[vepyr]\nflavour = "merged"\nrequired_contigs = {q(["chr" + c.removeprefix("chr") for c in contigs])}\n'
            'everything = true\npreserve_record_layout = true\nreference_fasta = true\n\n'
            '[vep]\nextra_flags = ["--merged"]\n\n[compare]\nbody_md5 = ""\n'
        )
        runs = []
        runs.append(run([str(ROOT/'tools/normalize_input'), str(raw), str(dest)], evidence/'normalize.log'))
        if runs[-1]['exit']:
            raise RuntimeError(runs[-1])
        runs.append(run([str(ROOT/'bless'), '--vep-cache-dir', str(args.vep_cache), '--vep-fasta', str(args.fasta), str(dest)], evidence/'vep.log'))
        if runs[-1]['exit']:
            raise RuntimeError(runs[-1])
        oracle = dest/'expected_output.vcf'
        expected = focus_value(oracle, case['focus'])
        if expected != case['focus']['expected']:
            raise ValueError(f'{name}: witness changed: {expected!r}')
        output = evidence/'vepyr.vcf'
        runs.append(run([str(args.vepyr_python), '-m', 'vepyr', 'annotate', '--input_file', str(dest/'input.vcf'), '--output_file', str(output), '--dir_cache', str(args.vepyr_cache), '--fasta', str(args.fasta), '--cache_version', '116', '--everything', '--no_progress'], evidence/'vepyr.log', args.vepyr_source))
        result = {'vepyr_sha': sha, 'commands': runs, 'input_sha256': hashlib.sha256((dest/'input.vcf').read_bytes()).hexdigest(), 'oracle_body_md5': hashlib.md5(body(oracle)).hexdigest(), 'oracle_focus': expected}
        result['runnable'] = runs[-1]['exit'] == 0 and output.is_file()
        case['status'] = 'ERROR'
        if result['runnable']:
            result['vepyr_body_md5'] = hashlib.md5(body(output)).hexdigest()
            case['status'] = 'PASS' if body(oracle) == body(output) else 'FAIL'
            try:
                result['vepyr_focus'] = focus_value(output, case['focus'])
                result['focus_pass'] = result['vepyr_focus'] == expected
            except (ValueError, KeyError) as exc:
                result['focus_error'] = str(exc)
                result['focus_pass'] = False
        result['status'] = case['status']
        case['result'] = result
        case['oracle_status'] = 'Generated by VEP 116.2 with merged cache 116'
        case['witness_status'] = 'Primary property qualified against the generated oracle'
        case['potential_vepyr_bug'] = 'None observed in this run' if case['status'] == 'PASS' else 'Observed differential failure; cause not yet assigned'
        (evidence/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        MANIFEST.write_text(json.dumps(cases, indent=2)+'\n')
        print(f'{case["id"]} {case["status"]} focus={result.get("focus_pass")} {name}', flush=True)
    print(json.dumps({status: sum(c['status']==status for c in cases) for status in ['PASS','FAIL','ERROR','QUEUED']}), flush=True)


if __name__ == '__main__':
    main()
