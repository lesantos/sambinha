# Configuring and recovering backups

[Português do Brasil](usage.pt-BR.md) · [README](../README.md)

## Commands and exit codes

Omitted CLI options inherit the TOML. `backup` is optional and remains the default action. `--source`, `--destination`, `--type` and `--logic` require one selected job. `--no-dry-run` overrides simulation enabled in the file.

```bash
sambinha --job documents-s3 --max-bytes 102400 --max-files 10 --dry-run
sambinha --job documents-local --deleted-days 30 --version-days 7
sambinha --config /path/another.toml --job documents-local --destination /mnt/backup
sambinha --help
```

Exit codes: **0** success, **1** operation/notification failure, **2** configuration error, **130** interruption. Independent jobs continue after a job failure. Global preflight checks sources and dependencies before any transfer. Types: `local`, `s3`; strategies: `simple`, `batch`; archive formats: `7z`, `zip`. `compressor` accepts `7z`, `7zz` or another compatible CLI executable.

## Batch rules

1. Directories are visited in path order; files within each directory are sorted by nanosecond modification time, oldest first, with path as the tie breaker. New archives separate compression categories without disturbing this order. A directory can occupy several batches, and adjacent small directories can share one.
2. `max_bytes` limits original, uncompressed sizes, default 1 GiB. Archive overhead can exceed that size. `max_files` defaults to 1000 and must be at least 2.
3. Intact existing batches keep their original key, member order and compression settings when their limits and archive format remain compatible. Only affected members and new files are repacked. Intact batches do not lend files to fill a new batch.
4. A new trailing singleton can borrow from a preceding new batch with more than two members, within the limits and the same compression category. Oversized files, impossible pairs and category boundaries can require singleton archives.
5. Normal keys are `<common-ancestor>/batch-<hash>.7z`. Singleton keys are `<directory>/.versions/<hash>/<basename-with-new-extension>.7z`, avoiding collisions between matching stems and retaining immutable versions. Internal paths remain relative to the source root.
6. Each archive has `<key>.json`. `<state_dir>/<job>/controls/` stores control files rather than archive bodies. New controls record compressed size and MD5 in addition to the original file metadata.
7. Archive staging is temporary and cleaned after success or failure. Snapshots, 7-Zip tests and exact member path/size validation precede uploads. Concurrent workers have separate staging and subprocess reports.
8. Archives and controls finish uploading before the recovery index and local state commit. Failed uploads preserve the previous committed index; interrupted attempts can leave immutable orphan objects. Preserve local state; dedicated remote prefixes avoid interactions between overlapping jobs.

## Retention and exclusion rules

Simple backups preserve previous bytes before modifying a current file, at `.sambinha/history/version/<id>/...` or `.sambinha/history/deleted/<id>/...`. The retirement record is saved before the current file changes. Batch archives remain at their immutable keys until expiry, with current controls in `active` and historical records in `retired`.

Retention starts when a successful execution detects edits/deletions. Cleanup happens on later runs, including at the exact deadline. Zero-day retention allows cleanup in that run. A mixed edited/deleted lot receives the longest applicable retention. Active reactivated archives cannot expire through a former retirement record. Cleanup publication failures are retried using `index_pending`.

`exclude_files` uses native rclone syntax via `--exclude-from`. The rule file is frozen when its job starts. Paths are relative to the source root; anchored patterns, directories, Unicode and wildcard alternatives follow rclone's own matcher. Excluded files are neither transferred nor treated as deleted. Their history remains protected. A legacy batch containing both selected and excluded paths is retained in `excluded`, while current selected files are stored separately. Historical names are matched through temporary empty-file trees, including old file/directory replacements. Filter results are cached only within that execution.

The advisory `deletion_alert_threshold` defaults to 1000. A strictly greater number of impending permanent deletions sends a Cronitor failure notification and logs all original paths, but does not stop backup, shift deadlines or change the successful exit code. Counts are independent per job and include archived members. The warning window is two days, one day for one-day retention, and immediate for zero-day retention. Protected history is excluded. Run backups regularly to receive advance notice.

## Mass removal protection

`max_delete_percent = 50` and `deletion_min_files = 20` are per-job defaults. The guard compares the current selection with the previously confirmed selected inventory. Exclusions are removed from the baseline before computing the ratio. It blocks when the baseline reaches the minimum and the removed percentage is strictly above the limit, before history, uploads, replacement or retention cleanup. Initial backups have no baseline and are allowed. Exactly the threshold is allowed. Set the minimum to 1 to protect small sources too.

```bash
sambinha --job documents --dry-run
sambinha --job documents --allow-mass-deletion
```

The bypass applies only to that invocation; review the source and logs before using it. A blocked job reports its removed paths and Cronitor receives an error. Other independent jobs can continue.

## Disk and mount monitoring

Source disks, local destination disks and batch staging disks fail above 95% occupancy, including filesystem space unavailable to the current user. Exactly 95% is allowed. A not-yet-created destination uses its nearest existing ancestor for measurement. Checks run before backup and before destination changes; `disk_usage` events record the percentage and free bytes. For S3 only local source/staging disks have a measurable occupancy percentage. Restore also checks its output filesystem.

Per-job `check_source_disk` and `check_destination_disk` default to true. Set the source option to false for virtual filesystems (such as Seafile/FUSE) that report zero capacity. The destination option also controls restore output checks; temporary storage and mount/path protections stay enabled. `[[jobs]].continue_on_job_error` defaults to true, including per-job preflight failures; false stops on the first job error. Any job failure still returns a nonzero exit code. Errors identify the job in messages, logs and notifications. See the [complete guide](configuration.md#disk-checks-per-job) for configuration.

Optional `source_mount` and local-only `destination_mount` require `mountpoint -q` and a nonempty expected volume. The configured directory must be inside that volume. Missing, empty, unreadable or lost mounts stop the backup and notify Cronitor. Subdirectories themselves may be empty.

## Restore commands

```bash
sambinha restore --job documents --path dir/file.txt --output /mnt/recovered/file
sambinha restore --job documents --path dir --path other.txt --output /mnt/recovered/selection
sambinha restore --job documents --output /mnt/recovered/all
sambinha restore --job documents --path dir --output /mnt/recovered/preview --dry-run
```

Select one job and a new or empty output directory separate from sources, destinations, state and logs. Selections use literal active-index paths, not glob patterns. Sizes and any SHA-256/MD5 recorded in the index are validated in staging before any requested file is published. Restore creates files exclusively, rejects unsafe archives and symlinks, and restores ordinary mode bits and timestamps. It does not overwrite existing data. A later disk/write error may leave already published validated files and is reported as failure.

Simple recovery needs the local index. Batch recovery can use the local index or download `.sambinha/control.tar.gz` and read only its bounded regular `state.json` member. It does not import arbitrary archive members into the state directory. Recovery works without the original source. S3 restoration downloads selected files or entire selected batches and may incur retrieval costs; GLACIER/DEEP_ARCHIVE need prior AWS restore. History recovery remains manual: inspect `retired` and the corresponding JSON controls or simple history paths. The command restores the current active index.

## Integrity verification and scheduling

```bash
sambinha verify --job documents
sambinha --verify-interval-days 7
```

Copy verification follows `verification_mode`: the default `"simple"` compares size and nanosecond modification time; `"hash"` reads local destination contents or requests S3 MD5 metadata. Local batch checks validate archive checksums, 7-Zip integrity, member paths and indexed sizes/hashes when present. S3 checks only request metadata and also check JSON controls; batches always compare size/MD5. They never download object bodies or recovery archives, including failure paths. A multipart ETag alone is not a whole-object MD5. Required hashes, legacy archive checksums or a local index missing cause explicit errors. This metadata check does not decompress archived members or authenticate an adversarially modified index.

`verify_interval_days` is global; 0 disables automatic checks, 7 verifies after successful backups when due. The first enabled run checks immediately. Only success updates `last_verified`. Failure returns error and notifies Cronitor. The application does not install a separate scheduler; invoke backups daily through your existing timer. Manual `verify` checks immediately and does not modify the scheduled-success timestamp. Simulation reports planned verification without downloading or checking contents.

## Summary and performance

Each job emits a `summary` with total/new/modified/unchanged/removed files, reused/rebuilt batches, planned original bytes, actually transferred payload bytes, pending deletions, simulation status and success. Control/index overhead is excluded from payload bytes. Failed jobs can have partial transfers; the summary is an audit, not a distributed transaction. [Performance settings](performance.md) document P1–P6 and category profiles.

## Compatibility and operational limits

Existing 0.3.x state remains compatible; intact legacy batches stay intact. Such archives lack upload checksums and cannot receive a complete metadata integrity check until they are replaced after changes. The earlier 0.2.0 rename introduced `sambinha`, `sambinha.toml` and `.sambinha`; states from another metadata namespace are rejected. Keep an old destination and state together, and use a new destination/state to migrate an older namespace. Begin with simulation.

Symbolic links are ignored. Special files and newline-bearing source filenames are rejected. `.sambinha` at the source root is reserved. Each job must keep its own source/destination separate and all jobs must stay outside state/log directories. Cross-job source/destination overlap is allowed; see [the interaction rules](configuration.md#sequential-jobs-and-overlapping-paths). Local POSIX ownership changes are not required; S3 has no native POSIX mode bits. Without filesystem snapshots, concurrent source writes can abort a backup. In hash mode, the fingerprint cache assumes trustworthy filesystem metadata and forces periodic content scans; disable it when full reads on every run are required.

Batch reuse trusts the local mirror and exclusive remote prefix ownership. External object deletion/corruption is detected by explicit/scheduled verification, not by every unchanged backup. Copy backups compare size/time by default. In hash mode, they compare remote checksums and repair content divergence; unavailable required checksums fail. Size/time comparison intentionally misses edits preserving both attributes.

## Verification modes and terminal progress — 0.6.1

`[backup].verification_mode = "simple"` is the default, even for existing configurations without this key; `"hash"` enables SHA-256/MD5 content checks. The CLI override is `--verification-mode simple|hash`. Hash cache and periodic-full-scan options only apply to hash mode. Switching to hash recomputes absent hashes; batch mode switches can rebuild archives. A hash verification requires a hash-mode backup first if the index has no hashes. Restores check sizes and any hashes recorded in their index; archive checksums remain enabled in both modes.

Interactive stderr shows stage, directory and estimated completion, with file/byte counts for inventories, known-directory estimates during discovery and elapsed-time estimates for opaque subprocess operations. The percentage covers the entire backup across stages, directories and jobs; it never decreases and only reaches 100% after success. Per-file events stay in the audit and are hidden in the interactive terminal. Bulk copies display the actual subdirectory. Progress is never included in JSON logs or stdout, and is disabled when stderr is not a terminal. See the [complete guide](configuration.md#choose-simple-or-hash-verification).

## Symbolic links

Symbolic links in source and local destination data are always ignored. Linked directories are not traversed; links to files, broken links and cycles do not stop a normal inventory. Links and their targets are not copied through the link, compressed or represented as `.rclonelink` files. A target that also appears independently as a regular file is backed up normally.

Linked paths are treated as implicit exclusions: existing copies, batches and history stay protected while the corresponding source or destination path contains a link. A local destination link also suppresses copying a colliding source path, without changing the link or its target.

Rclone normally skips symlinks. Sambinha passes `--skip-links` to suppress warnings and `--links=false --copy-links=false` to override link-enabled environment settings. [Official rclone documentation](https://rclone.org/local/#skip-links). This behavior is unconditional and requires no configuration.

Metadata/state/lock paths and restore output remain protected against link redirection. Unsafe archive members and source/destination paths changing into links during transfer can still produce an explicit error rather than follow the new target.
