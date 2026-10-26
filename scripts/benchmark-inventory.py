#!/usr/bin/env python3
"""Compare read-only inventories against a saved pre-change package tree.

Example: python scripts/benchmark-inventory.py --baseline-root /tmp/before \
    --source /path/to/sample --exclude-file /path/to/rules --output /tmp/result.json
The baseline directory must contain sambinha/. No backup or S3 operation runs.
"""
import argparse
import hashlib
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path


def worker(payload):
    sys.path.insert(0, payload['module_root'])
    from sambinha.config import Config
    from sambinha.discovery import Discovery
    from sambinha.filtering import Selection
    from sambinha.inventory import scan
    from sambinha.process import Runner

    source = Path(payload['source'])
    rules = Path(payload['rules']) if payload['rules'] else None
    calls = {}
    original = Discovery._call
    def call(self, operation, values, *args, **kwargs):
        key = operation
        if operation == 'operations/list':
            key += ' (recursive)' if values['opt']['recurse'] else ' (flat)'
        calls[key] = calls.get(key, 0) + 1
        return original(self, operation, values, *args, **kwargs)
    Discovery._call = call
    selection = Selection(rules, Config(jobs=()), Runner(timeout=30))
    fingerprints = {}
    started = time.perf_counter()
    if payload['legacy'] and not payload['same_flow']:
        selected, directories = selection.source(source)
        files = scan(source, selected, directories, mode=payload['mode'], cache_out=fingerprints)
    else:
        directories = set() if rules else None
        with selection.source_listing(source) as listing:
            files = scan(source, listing=listing, selected_dirs_out=directories,
                         mode=payload['mode'], cache_out=fingerprints)
    elapsed = time.perf_counter() - started
    result = {'files': [f.metadata() for f in files],
              'directories': sorted(directories) if directories is not None else None,
              'fingerprints': fingerprints}
    digest = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    print(json.dumps({'seconds': elapsed, 'files': len(files),
                      'selected_directories': len(directories or ()),
                      'calls': calls, 'digest': digest}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-root', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--exclude-file', type=Path)
    parser.add_argument('--mode', choices=('simple', 'hash'), default='simple')
    parser.add_argument('--same-flow', action='store_true',
                        help='run the baseline through source_listing() too (isolates discovery changes)')
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error('--repetitions must be positive')
    current_root = Path(__file__).resolve().parents[1]
    samples = {'before': [], 'after': []}
    digest = None
    # Warm up both versions; then alternate order to reduce cache/order bias.
    for iteration in range(args.repetitions + 1):
        order = ('before', 'after') if iteration % 2 == 0 else ('after', 'before')
        for variant in order:
            payload = {'module_root': str(args.baseline_root.resolve() if variant == 'before' else current_root),
                       'source': str(args.source.resolve()),
                       'rules': str(args.exclude_file.resolve()) if args.exclude_file else None,
                       'legacy': variant == 'before', 'mode': args.mode,
                       'same_flow': args.same_flow}
            completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker'],
                                       input=json.dumps(payload), text=True, capture_output=True,
                                       check=True, timeout=300)
            sample = json.loads(completed.stdout)
            if digest is None:
                digest = sample['digest']
            if sample['digest'] != digest:
                raise RuntimeError('Inventories differ: paths/order, metadata, directories or fingerprints')
            if iteration:
                samples[variant].append(sample)
            print(f"{variant} {iteration if iteration else 'warmup'}: "
                  f"{sample['seconds']:.3f}s, {sample['files']} files", flush=True)
    calls = {v: samples[v][0]['calls'] for v in samples}
    medians = {variant: statistics.median(s['seconds'] for s in values)
               for variant, values in samples.items()}
    result = {'source': str(args.source.resolve()), 'mode': args.mode, 'repetitions': args.repetitions,
              'warmups_per_version': 1, 'same_flow': args.same_flow, 'identical_inventories': True, 'calls': calls, 'samples': samples,
              'median_seconds': medians, 'speedup': medians['before'] / medians['after'],
              'time_reduction_percent': 100 * (1 - medians['after'] / medians['before'])}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'samples'}, indent=2))


if __name__ == '__main__':
    if sys.argv[1:] == ['--worker']:
        worker(json.load(sys.stdin))
    else:
        main()
