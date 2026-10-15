# Backup performance

[Português do Brasil](performance.pt-BR.md) · [README](../README.md)

Local simple sync checks destination entries in directories shared with the source before starting rclone, to replace conflicting symlinks safely. This adds a destination directory pass on filesystems that can contain links, but does not build a source file inventory or read file contents; only found links and directory counterparts require source metadata checks. On native exFAT/FAT, this pass is skipped when no link-capable filesystem is mounted below the destination; root checks and mount-table detection still run. Unknown filesystems use the scan. Native rclone exclusions are evaluated only for found link conflicts. Batch writes check their planned output paths directly.

P1–P6 were implemented in the requested order; 0.9.0 replaces P3 cross-job sharing with independent inventories. These settings improve different bottlenecks; gains depend on file count, disks, CPUs and network. Tests verify reduced reads/process calls and bounded concurrency, rather than promising a fixed speedup.

| Priority | Improvement | Setting and behavior |
| --- | --- | --- |
| P1 | Fingerprint cache | In `verification_mode = "hash"`, `fingerprint_cache = true` reuses hashes only if device, inode, size, mtime, ctime and mode match. `fingerprint_full_scan_days = 7` forces full reads periodically; `--no-fingerprint-cache` disables reuse. Default `"simple"` uses only size/mtime and never hashes the inventory. |
| P2 | Native simple sync | Simple jobs use one `rclone sync` operation for traversal, exclusions, updates and deletions; replaced/deleted files go to `--backup-dir`. `transfer_workers = 4` controls rclone concurrency. No Sambinha recursive inventory is built for simple jobs. |
| P2 | Batch discovery with exclusions | `lsjson` lists one directory per non-recursive call to limit each response size. Sambinha still combines the results into one global inventory before planning batches. |
| P3 | Independent per-job inventories (0.9.0) | Each job scans its source when it starts using its own persistent cache. Cross-job scan sharing was removed so later jobs observe earlier changes. |
| P4 | Batch concurrency | `batch_workers = 2`; each worker compresses and uploads in private staging. The main thread publishes the index only after every new batch finishes successfully. |
| P5 | Filter cache | Repeated historical-name selections reuse immutable results within one execution. Rules are still evaluated by rclone and frozen before work. |
| P6 | Uniform compression | One per-job level/thread count for every file; no category boundaries. |

## Uniform compression defaults

Every file uses `compression_level = 3` and `compression_threads = 2`, configurable per job. Levels range from 0–9 and threads from 1–64; actual parallelism depends on the 7-Zip format/algorithm. Extensions do not choose compression or batch boundaries. Replace obsolete category settings; intact groups retain their archive revisions.

```toml
[backup]
verification_mode = "simple" # Default; use "hash" for content comparisons.
fingerprint_cache = true
fingerprint_full_scan_days = 7
transfer_workers = 4
batch_workers = 2

[[jobs]]
name = "files"
source = "~/Files"
destination = "/mnt/backup/files"
logic = "batch"
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
