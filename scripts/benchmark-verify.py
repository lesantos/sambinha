#!/usr/bin/env python3
"""Measure the final verify() pass and hashing throughput on a generated tree.

Example: python scripts/benchmark-verify.py --label before --module-root /tmp/baseline \
    --workdir /tmp/tree --output docs/benchmarks/verify-2026-10-26.json
The module root must contain sambinha/ (use `git archive <rev> sambinha | tar -x -C dir` for a
baseline). The tree is generated once, deterministically; nothing outside --workdir is touched.
Results are merged under --label so before/after runs share one JSON file.
"""
import argparse
import inspect
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path


def build(tree, small, directories, large, large_mib):
    marker = tree / ".built"
    if marker.exists():
        return
    rng = random.Random(7)
    dirs = [tree / f"d{i % 10}" / f"s{i % 40}" / f"t{i}" for i in range(directories)]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    for i in range(small):
        (rng.choice(dirs) / f"f{i}.txt").write_bytes(rng.randbytes(rng.randint(100, 4000)))
    (tree / "big").mkdir(exist_ok=True)
    for i in range(large):
        with open(tree / "big" / f"b{i}.bin", "wb") as out:
            for _ in range(large_mib):
                out.write(rng.randbytes(1 << 20))
    marker.write_text("ok")


def timed(function, repeats):
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        function()
        samples.append(time.perf_counter() - started)
    return round(statistics.median(samples), 4)


def counted(function):
    """Count the filesystem syscalls issued through the os module."""
    counts = {}
    originals = {name: getattr(os, name) for name in ("stat", "lstat", "fstat", "open", "read")}

    def wrap(name):
        def inner(*args, **kwargs):
            counts[name] = counts.get(name, 0) + 1
            return originals[name](*args, **kwargs)
        return inner

    for name in originals:
        setattr(os, name, wrap(name))
    try:
        function()
    finally:
        for name, original in originals.items():
            setattr(os, name, original)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--module-root", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--small", type=int, default=6000)
    parser.add_argument("--directories", type=int, default=400)
    parser.add_argument("--large", type=int, default=4)
    parser.add_argument("--large-mib", type=int, default=64)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    tree = args.workdir / "tree"
    tree.mkdir(parents=True, exist_ok=True)
    build(tree, args.small, args.directories, args.large, args.large_mib)
    sys.path.insert(0, str(args.module_root))
    from sambinha import inventory

    cache = {}
    files = inventory.scan(tree, cache_out=cache, mode="hash")
    big = [f for f in files if f.path.startswith("big/")]
    simple = inventory.scan(tree, mode="simple")
    can_cache = "signatures" in inspect.signature(inventory.verify).parameters

    def verify(items, mode, signatures=False):
        extra = {"signatures": cache} if signatures and can_cache else {}
        return lambda: inventory.verify(tree, items, mode=mode, **extra)

    result = {"files": len(files), "large_files": len(big),
              "large_mib_each": args.large_mib, "repeats": args.repeats}
    runs = {
        "scan_hash_cold_s": lambda: inventory.scan(tree, mode="hash"),
        "verify_hash_full_s": verify(files, "hash"),
        "verify_hash_with_signatures_s": verify(files, "hash", True),
        "verify_simple_s": verify(simple, "simple"),
        "hash_large_files_s": lambda: [inventory.fingerprint(tree, f.path) for f in big],
    }
    for name, function in runs.items():
        result[name] = timed(function, args.repeats)
    result["hash_large_mib_per_s"] = round(args.large * args.large_mib / result["hash_large_files_s"], 1)
    result["verify_hash_full_syscalls"] = counted(verify(files, "hash"))
    result["verify_simple_syscalls"] = counted(verify(simple, "simple"))
    if can_cache:
        result["verify_hash_with_signatures_syscalls"] = counted(verify(files, "hash", True))
    merged = json.loads(args.output.read_text()) if args.output.exists() else {}
    merged[args.label] = result
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
