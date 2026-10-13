# Backup performance

[Português do Brasil](performance.pt-BR.md) · [README](../README.md)

P1–P6 were implemented in the requested order; 0.9.0 replaces P3 cross-job sharing with independent inventories. These settings improve different bottlenecks; gains depend on file count, disks, CPUs and network. Tests verify reduced reads/process calls and bounded concurrency, rather than promising a fixed speedup.

| Priority | Improvement | Setting and behavior |
| --- | --- | --- |
| P1 | Fingerprint cache | In `verification_mode = "hash"`, `fingerprint_cache = true` reuses hashes only if device, inode, size, mtime, ctime and mode match. `fingerprint_full_scan_days = 7` forces full reads periodically; `--no-fingerprint-cache` disables reuse. Default `"simple"` uses only size/mtime and never hashes the inventory. |
| P2 | Native simple sync | Simple jobs use one `rclone sync` operation for traversal, exclusions, updates and deletions; replaced/deleted files go to `--backup-dir`. `transfer_workers = 4` controls rclone concurrency. No Sambinha recursive inventory is built for simple jobs. |
| P2 | Batch discovery with exclusions | `lsjson` lists one directory per non-recursive call to limit each response size. Sambinha still combines the results into one global inventory before planning batches. |
| P3 | Independent per-job inventories (0.9.0) | Each job scans its source when it starts using its own persistent cache. Cross-job scan sharing was removed so later jobs observe earlier changes. |
| P4 | Batch concurrency | `batch_workers = 2`; each worker compresses and uploads in private staging. The main thread publishes the index only after every new batch finishes successfully. |
| P5 | Filter cache | Repeated historical-name selections reuse immutable results within one execution. Rules are still evaluated by rclone and frozen before work. |
| P6 | Compression profiles | Per-job levels and thread counts for text, videos, photos and other files; new batches do not mix categories. |

## File categories and default settings

| Category | Examples | Level | Threads |
| --- | --- | ---: | ---: |
| Text | `.md`, `.txt`, `.csv`, `.json`, `.yaml`, `.toml`, source code and extensionless README/LICENSE | 3 | 2 |
| Video | `.mp4`, `.mkv`, `.mov`, `.avi`, `.webm` | 0 | 1 |
| Photo | `.jpg`, `.jpeg`, `.png`, `.webp`, `.heic`, `.tiff`, `.raw`, `.dng` | 1 | 1 |
| Other | Extensions outside the configured built-in categories | 3 | 2 |

Classification uses case-insensitive extensions and known extensionless documentation names. These are heuristics; content is not inspected to identify formats. RAW/TIFF images can benefit from raising `compression_photo_level`, while already compressed video usually benefits from level 0. Level 0 means no compression, though the archive still packages and checks files. Levels 1–9 increase effort; support and actual thread use depend on 7-Zip's format/algorithm. At most 64 threads may be configured per archive. Legacy mixed archives use the other profile if they need rebuilding as a mixed group; intact archives are preserved with their original settings.

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
compression_text_level = 3
compression_text_threads = 2
compression_video_level = 0
compression_video_threads = 1
compression_photo_level = 1
compression_photo_threads = 1
compression_other_level = 3
compression_other_threads = 2
```

Keep the oldest-first order within a directory. A category boundary starts a new batch, so interleaved categories can produce more singleton archives. Existing compatible batches keep their original membership and keys. Settings affect newly created archives rather than forcing a reupload of intact data.

Concurrency increases temporary space, CPU and memory use. A batch worker stages original bytes and an archive; two workers can need roughly twice the staging space of one. Disk checks fail above 95% occupancy. Measure total backup time and uploaded size before raising worker/thread counts; increasing CPU concurrency can hurt a slow shared disk.

Simple jobs delegate comparison behavior to rclone and do not build a Sambinha fingerprint inventory. `verification_mode = "simple"` uses rclone size/time comparison; `"hash"` passes `--checksum`. `max_delete` sets an absolute per-job deletion limit; rclone aborts when the limit is exceeded. Batch jobs continue to use the inventory cache, selected verification mode, and source consistency checks. S3 integrity checks never download object bodies. Archives and their controls retain checksums in both modes.

Interactive terminal progress shows stage, working subdirectory and one overall estimated backup percentage on stderr, never in JSON logs or stdout. Simple sync progress follows rclone's reported transfer statistics; batch phases and concurrent batches use weighted counters and bounded subprocess estimates. Only successful completion displays 100%. Per-file events are retained in the audit and hidden in the interactive console; summaries/errors remain visible. Redirected/nonterminal stderr disables progress. See [verification modes](../README.md#choose-simple-or-hash-verification).

References: [restic change detection](https://restic.readthedocs.io/en/stable/040_backup.html), [rclone concurrency](https://rclone.org/docs/).
