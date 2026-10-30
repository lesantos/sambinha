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
| P6 | Three-tier compression | Per-job levels for text, other and already-compressed files (chosen by extension) appended to one archive; one thread count; batch boundaries ignore types. |

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

## exFAT/FAT identity correction (0.14.3)

The combined sequential traversal remains in place. Native FAT/exFAT may renumber an inode after the object leaves the kernel cache, so an older discovery inode is not a persistent identity. Known native devices allow only that inode field to differ before opening if every other observed metadata field agrees. Directories use their open descriptors as the baseline for final pathname checks. Files remain open through inspection and final checks; metadata-only scans use `O_PATH` and never read file contents. Hash mode can reuse cached fingerprints only with an exact signature match to the opened file; renumbered inodes cause rehashing. Mount information is consulted only for otherwise identical observations with differing historical inodes; nested mounts use their own device policy. No concurrency or additional recursive source pass is introduced. The performance numbers above are historical 0.14.2 measurements, not a new timing claim for 0.14.3. See [source identity checks and diagnostics](usage.md#exfatfat-source-identities).

## Fingerprint cache across machines

Cache signatures include `st_dev` and `st_ino`, so a cache built on one machine never matches on another (different device numbers, different inodes) and is also invalidated when a disk is remounted with a new device number. The first hash-mode backup of a job on a new machine therefore reads every file once, exactly like `--no-fingerprint-cache`; later runs on that machine reuse their own cache. When a machine re-adopts the remote index because another machine published a newer generation, its own cache is preserved rather than replaced by the publisher's. No stale hash can be reused through a foreign entry: signature mismatch always means rehash.

## Uniform compression defaults

Defaults are `compression_text_level = 1`, `compression_other_level = 1`, `compression_compressed_level = 0` and `compression_threads = 2`, configurable per job (see [three-tier compression](configuration.md#three-tier-compression-0180)). Levels range from 0–9 and threads from 1–64; actual parallelism depends on the 7-Zip format/algorithm. Extensions choose the compression tier but never batch boundaries. Replace obsolete category settings; intact groups retain their archive revisions.

```toml
[[jobs]]
name = "files"
source = "~/Files"
destination = "/mnt/backup/files"
logic = "batch"
verification_mode = "simple" # Default; use "hash" for content comparisons.
# These two options only apply with verification_mode = "hash"; they are ignored in "simple" mode.
fingerprint_cache = true
fingerprint_full_scan_days = 7
transfer_workers = 4
batch_workers = 2
compression_text_level = 1
compression_other_level = 1
compression_compressed_level = 0
compression_threads = 2
individual_batch_percent = 100
batch_size_alert_percent = 200
```

Creation uses DFS mtime order without type/count boundaries. Divert individual files without closing the normal batch. Confirmed groups keep their members and may grow; compression changes affect creation/rebuilds. See [stable batches](batch-lifecycle.md).

Concurrency increases temporary space, CPU and memory use. `batch_workers` bounds the entire preparation/compression/upload/cleanup cycle; `compression_threads` controls each compressor, and `transfer_workers` controls simple-mode rclone transfers. After any batch failure, stop scheduling pending batches and wait only for in-flight workers to finish and clean up. Publish the index/state only if all required uploads succeed.

Each worker compresses directly from the source; only the archive and small control/list files use temporary disk space. `verify_source_changes = true` checks source consistency before and after compression, retrying races up to three times; `false` skips these checks. This per-job setting defaults to `true` and applies to batch jobs. An optional `[backup].temp_dir`, placed next to the other configured directories, selects one existing writable directory shared by all jobs; relative paths start from the TOML file. Without it, Python's default temporary directory (`TMPDIR`) is used.

Before starting workers, check available space against a conservative estimate for the largest simultaneous needs: each batch's original size (assuming an incompressible archive), plus at least 1 MiB of overhead per batch (increased for file count and control size). Recheck each batch before creating its archive. Batches exceeding `max_megabytes` run one at a time, with no other batch occupying temporary storage. Use actual sizes because dedicated files and existing groups can exceed `max_megabytes`. Checks also reject occupancy above 95%, even when source/destination disk checks are disabled. This is an estimate, not an operating-system space reservation; concurrent external writes or unusual archive/filesystem overhead can still cause write failures. Measure backup time and uploaded size before increasing concurrency.

When space is insufficient, the error lists the batches that determine the peak estimate, each batch's original size, and up to its three largest files.

Simple jobs delegate comparison behavior to rclone and do not build a Sambinha fingerprint inventory. `verification_mode = "simple"` uses rclone size/time comparison; `"hash"` passes `--checksum`. `max_delete` sets an absolute per-job deletion limit; rclone aborts when the limit is exceeded. Batch jobs continue to use the inventory cache, selected verification mode, and source consistency checks. Daily batch verification reads only source metadata and local JSON controls. On-demand `verify-destination-metadata` downloads only metadata, while manual S3 `verify-destination-integrity` downloads archives one at a time without writing extracted files. Archives and their controls retain checksums in both modes. See [batch checks](batch-checks.md) for disk usage and scheduling.

Interactive terminal progress shows stage, working subdirectory and one overall estimated backup percentage on stderr, never in JSON logs or stdout. Simple sync progress follows rclone's reported transfer statistics; batch phases and concurrent batches use weighted counters and bounded subprocess estimates. Only successful completion displays 100%. JSON logs and stdout show errors and summaries by default; simple sync asks rclone to generate only `ERROR` messages. Set `log_file_level` or `log_terminal_level` to `detailed` to request rclone `DEBUG` and include per-file events. Periodic progress statistics are emitted separately at `ERROR` without enabling other messages. Redirected/nonterminal stderr disables progress. See [verification modes](../README.md#choose-simple-or-hash-verification).

References: [restic change detection](https://restic.readthedocs.io/en/stable/040_backup.html), [rclone concurrency](https://rclone.org/docs/).


## Batches and recovery in 0.13.0

## Three-tier compression (0.18.0)

Appending to an existing 7z archive does not recompress what is already inside, so a batch is compressed as text, then other, then already-compressed members, each with its own `-mx` ([how](batch-lifecycle.md#how-a-batch-is-compressed-0180)). One synthetic measurement with 7z 23.01 on a mixed 117 MB data set (text, executables and already-compressed media; not a general benchmark, your mix will differ):

| Configuration | Time | Archive size |
|---|---|---|
| Uniform `-mx=3` (previous default) | 16.3 s | 62.2 MB |
| Text `-mx=1`, everything else `-mx=0` | 3.9 s | 85.5 MB (executables stored: bad) |
| Three tiers: text 1, other 1, compressed 0 (new defaults) | 5.5 s | 62.7 MB |

The three-tier defaults take about a third of the time for 0.8% more space. Migration: `compression_level` (default 3) was removed in 0.18.0; its successor for unlisted extensions is `compression_other_level` (default 1). Existing archives are unaffected.

See [stable batches, uniform compression, final Cronitor alerts, manual rebalance and generation recovery](batch-lifecycle.md) for current behavior and migration from 0.12.0.


## Final verification, hashing and controls (0.17.0)

Measured with `scripts/benchmark-verify.py` (6000 small files in 400 directories plus four 64 MiB files; raw numbers in `docs/benchmarks/verify-2026-10-26.json`). The measuring host was shared and overloaded (load average 7-10 on 4 cores), so wall-clock times varied by ±50% between identical runs; **syscall counts are exact, timings are indicative only**.

| Final `verify()` over 6005 files | before | after |
| --- | --- | --- |
| `stat` calls, simple mode | 42,024 | 12,912 |
| `stat` calls, hash mode | 42,024 | 12,912 (full re-hash) |
| hash mode, fingerprint cache, no full scan due | full re-read (`read` 16,102) | 0 reads, 1 `stat` per file |
| simple mode, median of 5 runs | 2.86 s | 1.67 s |
| hash mode with cache, median of 5 runs | 5.33 s (cache ignored) | 1.18 s |

- **Descriptor walk.** `verify()` now opens every directory once with `O_NOFOLLOW` relative to its parent and validates files with `dir_fd`, so the per-file ancestor `lstat` chain is gone. A directory replaced by a symlink or another directory, and a swap while a directory is being checked, still raise `BackupError`/`SourceChangedError`.
- **Scan signatures.** With `verification_mode = "hash"`, `fingerprint_cache = true` and no full scan due, `verify()` (final pass and the checks around each 7-Zip run) compares the stat signature (dev, ino, size, mtime, ctime, mode) with the one the scan recorded and re-reads only files that differ. `fingerprint_cache = false` and the full-scan run keep the full re-hash. A same-size, same-mtime edit still changes `ctime` and is caught.
- **Hashing.** Reads use 1 MiB blocks; for files of 4 MiB or more md5 runs in a helper thread beside sha256 (bounded queue, joined on every exit path, errors re-raised). The generated archive is fingerprinted with md5 only. On this overloaded host the overlap measured only ~12% in a pure in-memory test (2.77 s → 2.43 s for 128 MiB) and was cancelled end to end by the extra CRC32 pass (2.20 s vs 2.41 s for 256 MiB): no net gain was measurable here; it is kept because it is simple and bounded.
- **CRC32 cross-check.** In hash mode the inventory also computes CRC32 (in memory only, never persisted). After `7z l -slt`, a member whose CRC differs raises an error naming the path; members without a CRC (empty files, cache hits) are skipped. This closes the gap between what the inventory read and what 7-Zip read, but only for files hashed in that run.
- **Controls.** Local control mirrors are validated once per execution (memoized per `Context`, invalidated when a control is written or an expired mirror is removed) instead of twice; this saves one JSON read and validation per control and was not separately timed.

## Recursive discovery per subtree (0.17.0)

With `exclude_files`, discovery used to issue one non-recursive `operations/list` per directory. It now issues one non-recursive call for the source root plus one `recurse: true` call per first-level subtree, and indexes the answer by parent directory. The walk is unchanged: names still come only from the listing, every `stat`/`open` goes through `dir_fd` with `O_NOFOLLOW`, `IsDir` is validated against the local type, ordering is by mtime/name, and rclone applies the exclusion rules natively. The deadline (`timeout`) applies per request, i.e. per subtree. Any failure stops the whole discovery and the `rcd` server is reaped.

Memory trade-off: the JSON of a subtree is parsed incrementally and only `(path, IsDir)` is kept per entry (roughly 150-250 bytes each in CPython), so 500k entries cost on the order of 100 MiB until discovery ends. If a subtree exceeds 500,000 entries, that subtree falls back to one flat request per directory (no configuration needed).

Measured with `scripts/benchmark-inventory.py --same-flow` on a generated tree (441 directories, 10,420 files, `*.tmp` and one subtree excluded), local rclone, 5 repetitions, identical inventories ([data](benchmarks/discovery-2026-10-26.json)): `operations/list` calls 419 -> 20 (1 flat + 19 recursive); median discovery + inventory 4.21 s -> 3.70 s (-12%). On this local loopback each call costs only ~9 ms; the gain grows with per-call latency (e.g. ~30 ms/call on slower disks or network mounts) and with directory count.

## Destination listings per run (0.17.0)

Publishing a recovery generation no longer lists the whole destination to prune old `.sambinha/indexes` generations: `finish()` lists only that prefix (`lsjson --include "/.sambinha/indexes/**"`, so rclone skips every other directory). The orphan report now makes one explicit full listing per run, reusing the planner's listing (taken before the run's uploads) when S3 planning already made one. Previously every `finish()` listed the whole destination, up to twice per run.

## rclone options for S3 (0.17.0)

`fast_list`, `s3_chunk_size` and `s3_upload_concurrency` are optional per-job settings ([configuration](configuration.md#optional-rclone-tuning-for-s3-0170)). They only change the rclone command line (S3 jobs only), so no benchmark was run: the effect depends on bucket size, object sizes and bandwidth, and defaults are unchanged. Note that `verification_mode = "hash"` makes rclone MD5 the whole source on every simple sync.

## Filter reclassification of indexed files (0.17.0)

With `exclude_files`, every run used to rebuild a temporary tree of N empty files and run `rclone lsjson --recursive` over it to re-classify *every* indexed path (mass-deletion guard, history forecast/expiry, excluded-batch detection). Files in the current inventory were already selected by the same frozen rules during discovery, so they are now included by construction; only paths that are not in the current inventory (removed files, files a changed rule now excludes, retired members) go through the dummy tree. Semantics are unchanged: a file that left the inventory because a new rule excludes it is still classified as excluded (its batch moves to `state.excluded`), not as removed. The per-run cache is kept (keyed by the classified set). `rebalance` does not use the shortcut: its "inventory" is rebuilt from controls, not discovered.

Measured with 20,000 indexed paths and a 3-rule exclude file ([data](benchmarks/state-2026-10-26.json)): unchanged run 5.15 s -> 0.03 s; 10 removed files 5.41 s -> 0.23 s.

## Hash cache outside `state.json` (0.17.0)

`state.json` used to embed a `fingerprints` entry for every file, even in `verification_mode = "simple"`, which never reads it. That map was also published inside the recovery index (64 MiB limit, roughly 200-250k files per job), written with `indent=2` plus `fsync` on every save (2-3 per run), validated entry by entry on load and deep-copied twice per run. Now:

- Simple mode persists no fingerprints at all (a leftover `fingerprints.json` is removed).
- Hash mode keeps the cache in `state/<job>/fingerprints.json` (compact JSON, atomic write, same 0700 directory rules). It is per machine (`st_dev`/`st_ino`), so it is never part of `state.json` or the published index. A missing, unreadable or invalid file only costs a full rehash and is rewritten; the file is rewritten only when its content changed.
- A `state.json` from an older version that still embeds `fingerprints` loads normally: the embedded cache serves that run, `fingerprints.json` is written, and the next save drops the embedded copy (the index is republished once without it). Indexes published by older versions keep working for recovery and restore; their embedded cache belongs to another machine and is ignored. The state schema is unchanged (older versions simply see no cache and rehash once).
- `--fresh-start` deletes `fingerprints.json` (not in dry runs).
- "Inventory changed" (which forces a publication on an otherwise unchanged run) now means only: `last_full_scan` moved or a legacy embedded cache has to be migrated. Fingerprint churn alone (for example a `ctime` change) rewrites `fingerprints.json` but does not republish.
- The two `copy.deepcopy(ctx.state)` calls became a snapshot that copies only what a run mutates (the `active`/`excluded` maps, `retired` entries, `origins`) and shares the immutable controls.

Measured on a synthetic state with 50,000 indexed files ([data](benchmarks/state-2026-10-26.json)): `state.json` 35.4 MB -> 14.5 MB; one `state.json` save 2.93 s -> 0.99 s (done 2-3 times per run); `load_state` 4.9 s -> 2.9 s; state copy per snapshot 0.89 s -> under 1 ms. In hash mode the cache costs one 13.1 MB `fingerprints.json` read per run (about 2.0 s, mostly validation) and one write (about 1.6 s) only when it changed. The published index no longer carries those 20.9 MB, so the same job fits many more files under the 64 MiB limit.
