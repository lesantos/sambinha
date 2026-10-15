# Backup performance

[Português do Brasil](performance.pt-BR.md) · [README](../README.md)

Local simple sync checks destination entries in directories shared with the source before starting rclone, to replace conflicting symlinks safely. This adds a destination directory pass on filesystems that can contain links, but does not build a source file inventory or read file contents; only found links and directory counterparts require source metadata checks. On native exFAT/FAT, this pass is skipped when no link-capable filesystem is mounted below the destination; root checks and mount-table detection still run. Unknown filesystems use the scan. Native rclone exclusions are evaluated only for found link conflicts. Batch writes check their planned output paths directly.

P1–P6 were implemented in the requested order; 0.9.0 replaces P3 cross-job sharing with independent inventories. These settings improve different bottlenecks; gains depend on file count, disks, CPUs and network. Tests verify reduced reads/process calls and bounded concurrency, rather than promising a fixed speedup.

| Priority | Improvement | Setting and behavior |
| --- | --- | --- |
| P1 | Fingerprint cache | In `verification_mode = "hash"`, `fingerprint_cache = true` reuses hashes only if device, inode, size, mtime, ctime and mode match. `fingerprint_full_scan_days = 7` forces full reads periodically; `--no-fingerprint-cache` disables reuse. Default `"simple"` uses only size/mtime and never hashes the inventory. |
| P2 | Native simple sync | Simple jobs use one `rclone sync` operation for traversal, exclusions, updates and deletions; replaced/deleted files go to `--backup-dir`. `transfer_workers = 4` controls rclone concurrency. No Sambinha recursive inventory is built for simple jobs. |
| P2 | Batch discovery with exclusions (0.14.0) | One authenticated local `rclone rcd` serves non-recursive directory listings over HTTP. Native filters retain the original source root; no rclone restart or dummy tree per directory. Each listing has its own `timeout` deadline. The complete inventory is still required before batch planning. |
| P3 | Independent per-job inventories (0.9.0) | Each job scans its source when it starts using its own persistent cache. Cross-job scan sharing was removed so later jobs observe earlier changes. |
| P4 | Batch concurrency | `batch_workers = 2`; each worker compresses and uploads in private staging. The main thread publishes the index only after every new batch finishes successfully. |
| P5 | Filter cache | Repeated historical-name selections reuse immutable results within one execution. Rules are still evaluated by rclone and frozen before work. |
| P6 | Uniform compression | One per-job level/thread count for every file; no category boundaries. |

## Bounded source discovery

Batch jobs with `exclude_files` start one private rclone RC process for discovery and stop it when discovery finishes. The client uses authenticated direct loopback HTTP calls, not a new `rclone rc` command per directory. The configured global `timeout` applies separately to each directory; server startup is limited to the smaller of that timeout and 10 seconds. Failed, timed-out or interrupted discovery cancels pending work, terminates/reaps the server and never plans deletions or commits a partial inventory. Discovery is sequential to avoid excessive seeks on hard disks. `batch_workers` and `transfer_workers` do not control discovery.

Native directory filters can avoid excluded subtrees when rclone can infer those rules. Embedded `{{...}}` regex patterns disable that optimization. For Finder/macOS Icon files, use the glob `Icon[\x0d␍]`, which handles CR and its visible rclone representation while preserving other names such as `Icon.png` and `Icon1`. Rules are frozen per job; edits affect the next execution. Historical-name filtering still uses temporary trees because those files may no longer exist.

A synthetic comparison on 2026-10-15 used 100 child directories, each with one included file and one excluded `*.tmp` file. The previous discovery launched 201 rclone processes and took 92.819 s; persistent discovery launched one process and took 1.700 s, selecting the same 100 files. This small local benchmark measures discovery only, not compression/upload speed or the completion time of a multi-terabyte backup.

## Combined batch inventory

Discovery and inventory now share one sequential DFS traversal in modification-time/name order. For jobs with exclusions, each directory uses one synchronous `operations/list` request; there are no `job/status` polls or polling sleeps. Selected children receive local metadata checks immediately, without a second recursive source walk or a global candidate list. Unfiltered inventories also inspect files during their first walk. Daily local metadata verification uses the same path.

Source symlinks are ignored, including file links, directory links, broken links and cycles. Real directories stay open during their DFS visit; relative no-follow operations avoid repeating ancestor-link checks per file while preventing content reads through replacement links. File signatures and directory identity checks still reject detected changes. Full inventory completion remains mandatory before batch planning, uploads or deletion handling; this does not overlap uploads with scanning or add discovery workers.

The total file/byte count is unknown during the combined walk, so terminal progress displays directory/file counters (and bytes for the current hash read) without an inventory percentage until completion.

Reproduce a read-only before/after comparison with a saved package tree from before the change:

```sh
python scripts/benchmark-inventory.py \
  --baseline-root /tmp/sambinha-before \
  --source /path/to/sample \
  --exclude-file /path/to/excludes.txt \
  --output /tmp/inventory-benchmark.json
```

The baseline directory must contain the original `sambinha/` package. The script warms up each version, alternates their order for three measured repetitions, and compares the complete ordered metadata, selected directories and fingerprint signatures. It never runs backup or contacts S3. `--mode hash` additionally reads sample contents; the default is `simple`. Results measure inventory only, with filesystem caches warmed and any ambient machine load; they do not predict whole-backup speed.

A comparison on 2026-10-15 against commit `b9402f7` (Python 3.12.3, rclone v1.60.1-DEV) produced identical inventories in every warmup and measured run:

| Sample | Before (median) | After (median) | Time reduction |
| --- | ---: | ---: | ---: |
| Synthetic, 10,000 selected files, 421 listed directories | 33.984 s | 13.302 s | 60.9% |
| Read-only exFAT source sample, 604 selected files, 199 listed directories | 12.152 s | 4.357 s | 64.1% |

In the synthetic measured runs, HTTP calls fell from 922–932 to 421; in the exFAT sample, from 546–556 to 199. The same ordered paths, full metadata, selected-directory sets and fingerprint-cache signatures were checked. Other tests overlapped the synthetic measurement; the exFAT measurement ran separately. These are warmed-cache samples under ambient host load, not a cold-disk or full-source benchmark. [Raw timings and results](benchmarks/inventory-2026-10-15.json).

## Uniform compression defaults

Every file uses `compression_level = 3` and `compression_threads = 2`, configurable per job. Levels range from 0–9 and threads from 1–64; actual parallelism depends on the 7-Zip format/algorithm. Extensions do not choose compression or batch boundaries. Replace obsolete category settings; intact groups retain their archive revisions.

```toml
[[jobs]]
name = "files"
source = "~/Files"
destination = "/mnt/backup/files"
logic = "batch"
verification_mode = "simple" # Default; use "hash" for content comparisons.
fingerprint_cache = true
fingerprint_full_scan_days = 7
transfer_workers = 4
batch_workers = 2
compression_level = 3
compression_threads = 2
individual_batch_percent = 100
batch_size_alert_percent = 200
```

Creation uses DFS mtime order without type/count boundaries. Divert individual files without closing the normal batch. Confirmed groups keep their members and may grow; compression changes affect creation/rebuilds. See [stable batches](batch-lifecycle.md).

Concurrency increases temporary space, CPU and memory use. `batch_workers` bounds the entire preparation/compression/upload/cleanup cycle; `compression_threads` controls each compressor, and `transfer_workers` controls simple-mode rclone transfers. After any batch failure, stop scheduling pending batches and wait only for in-flight workers to finish and clean up. Publish the index/state only if all required uploads succeed.

Each worker stages originals and an archive while compressing. After archive integrity and members pass validation, remove the original snapshot before uploading. An upload therefore keeps only its archive and small control/list files locally. Temporary files use Python's default temporary directory, configurable with `TMPDIR`.

Before starting workers, check available space against a conservative estimate for the largest `min(batch_workers, pending batches)` staging requirements: twice each batch's actual original size, plus overhead of at least 1 MiB per batch (increased for file count and control size). Recheck each batch before copying originals. Use actual sizes because dedicated files and existing groups can exceed `max_megabytes`. Checks also reject occupancy above 95%, even when source/destination disk checks are disabled. This is an estimate, not an operating-system space reservation; concurrent external writes or unusual archive/filesystem overhead can still cause write failures. Measure backup time and uploaded size before increasing concurrency.

Simple jobs delegate comparison behavior to rclone and do not build a Sambinha fingerprint inventory. `verification_mode = "simple"` uses rclone size/time comparison; `"hash"` passes `--checksum`. `max_delete` sets an absolute per-job deletion limit; rclone aborts when the limit is exceeded. Batch jobs continue to use the inventory cache, selected verification mode, and source consistency checks. Daily batch verification reads only source metadata and local JSON controls. Monthly `verify-destination-metadata` downloads only metadata, while manual S3 `verify-destination-integrity` downloads archives one at a time without writing extracted files. Archives and their controls retain checksums in both modes. See [batch checks](batch-checks.md) for disk usage and scheduling.

Interactive terminal progress shows stage, working subdirectory and one overall estimated backup percentage on stderr, never in JSON logs or stdout. Simple sync progress follows rclone's reported transfer statistics; batch phases and concurrent batches use weighted counters and bounded subprocess estimates. Only successful completion displays 100%. JSON logs and stdout show errors and summaries by default; simple sync asks rclone to generate only `ERROR` messages. Set `log_file_level` or `log_terminal_level` to `detailed` to request rclone `DEBUG` and include per-file events. Periodic progress statistics are emitted separately at `ERROR` without enabling other messages. Redirected/nonterminal stderr disables progress. See [verification modes](../README.md#choose-simple-or-hash-verification).

References: [restic change detection](https://restic.readthedocs.io/en/stable/040_backup.html), [rclone concurrency](https://rclone.org/docs/).


## Batches and recovery in 0.13.0

See [stable batches, uniform compression, final Cronitor alerts, manual rebalance and generation recovery](batch-lifecycle.md) for current behavior and migration from 0.12.0.
