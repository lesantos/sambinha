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

Backups and restores detect the destination filesystem before requesting ownership and permissions. On exFAT, FAT and mounts without known POSIX support, rclone copies contents and modification times without `--metadata`; batch restore also skips `chmod`. Filesystems with native POSIX attributes, such as ext4 and Btrfs, continue preserving metadata. Detection reads the mount table without creating probe files; if support cannot be confirmed, these attributes are skipped. Metadata stored in S3 remains enabled.

Simple backups use rclone's `--backup-dir` under `.sambinha/history/<run-id>` for replaced and deleted files. Expired run directories are purged after the larger of `deleted_days` and `version_days`; since rclone does not distinguish deletion from replacement in this history, simple items share that longer period. Batch archives remain at immutable keys until expiry, with current controls in `active` and historical records in `retired`.

Simple jobs pass `--update` to rclone, preserving destination files it considers newer than the source. By default Sambinha requests only rclone errors; with rclone 1.60, the per-file skip notice is available only at `DEBUG`, so a preserved newer file does not fail the job in the default mode. Set `log_file_level = "detailed"` or `log_terminal_level = "detailed"` to report those paths and fail the job for review. Batch jobs continue to report newer-destination conflicts independently of log detail.

Batch retention starts when a successful execution detects edits/deletions. Cleanup happens on later runs, including at the exact deadline. Zero-day retention allows cleanup in that run. A mixed edited/deleted lot receives the longest applicable retention. Active reactivated archives cannot expire through a former retirement record. Cleanup publication failures are retried using `index_pending`.

`exclude_files` uses native rclone syntax via `--exclude-from`. The rule file is frozen when its job starts. Paths are relative to the source root; anchored patterns, directories, Unicode and wildcard alternatives follow rclone's own matcher. Excluded files are neither transferred nor treated as deleted. Their history remains protected. A legacy batch containing both selected and excluded paths is retained in `excluded`, while current selected files are stored separately. Historical names are matched through temporary empty-file trees, including old file/directory replacements. Filter results are cached only within that execution.

The batch-only advisory `deletion_alert_threshold` defaults to 1000. It counts impending permanent removals from batch history; simple `--backup-dir` history is not counted or pruned by Sambinha.

## Mass removal protection

Simple jobs set an absolute `max_delete` per job (default 100); it is passed to `rclone sync --max-delete`. If the cap is exceeded, rclone stops the sync with an error. The sync is not retried as a whole so retries cannot multiply the per-run cap; files may already have been transferred or moved to `--backup-dir`. Batch jobs retain the `max_delete_percent`/`deletion_min_files` guard based on their previous index.

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

Select one job and a new or empty output directory separate from sources, destinations, state and logs. `--path` accepts repeated literal source-relative files or directories for simple and batch jobs. Simple jobs select current destination data via rclone; batch selections use active-index paths, not glob patterns. Batch sizes and any SHA-256/MD5 recorded in the index are validated in staging before requested files are published. Restore does not overwrite existing data. A later disk/write error may leave already published validated files and is reported as failure.

Simple restore copies the current tree when no `--path` is supplied and does not require an index. Batch recovery can use the local index or download `.sambinha/control.tar.gz` and read only its bounded regular `state.json` member. It does not import arbitrary archive members into the state directory. Recovery works without the original source. S3 restoration downloads selected files or entire selected batches and may incur retrieval costs; GLACIER/DEEP_ARCHIVE need prior AWS restore. Restore historical simple versions with rclone from `.sambinha/history/<run-id>`.

## Integrity verification and scheduling

```bash
sambinha verify --job documents
sambinha --verify-interval-days 7
```

Simple verification runs rclone's `check` command with the job filters. Batch verification follows `verification_mode` and validates archive checksums, 7-Zip integrity, member paths and indexed sizes/hashes when present. S3 batch checks only request metadata and check JSON controls; batches compare size/MD5. They never download object bodies or recovery archives, including failure paths. A multipart ETag alone is not a whole-object MD5. Required hashes, legacy archive checksums or a local index missing cause explicit errors. This metadata check does not decompress archived members or authenticate an adversarially modified index.

`verify_interval_days` schedules indexed batch checks; it does not apply to simple jobs. 0 disables scheduled checks. Only success updates `last_verified`. Manual verification checks immediately. Simple-job `verify` runs `rclone check` with that job's exclusions.

## Summary and performance

Batch jobs emit indexed file and batch counts. Simple jobs log the `rclone sync` strategy and per-job absolute `max_delete`; rclone may have partially applied a sync when an error occurs. [Performance settings](performance.md) document native sync and batch profiles.

## Compatibility and operational limits

Existing 0.3.x state remains compatible; intact legacy batches stay intact. Such archives lack upload checksums and cannot receive a complete metadata integrity check until they are replaced after changes. The earlier 0.2.0 rename introduced `sambinha`, `sambinha.toml` and `.sambinha`; states from another metadata namespace are rejected. Keep an old destination and state together, and use a new destination/state to migrate an older namespace. Begin with simulation.

Symbolic links are ignored. Special files and newline-bearing source filenames are rejected. `.sambinha` at the source root is reserved. Each job must keep its own source/destination separate and all jobs must stay outside state/log directories. Destinations cannot overlap each other; one job's destination may be a later job's source. See [the interaction rules](configuration.md#sequential-jobs-and-overlapping-paths). Local POSIX ownership changes are not required; S3 has no native POSIX mode bits. Without filesystem snapshots, concurrent source writes can abort a backup. In hash mode, the fingerprint cache assumes trustworthy filesystem metadata and forces periodic content scans; disable it when full reads on every run are required.

Batch reuse trusts the local mirror and exclusive remote prefix ownership. External object deletion/corruption is detected by explicit/scheduled verification, not by every unchanged backup. Simple sync comparison behavior follows rclone and its backend settings. Batch hash mode compares remote checksums and unavailable required checksums fail.

## Verification modes and terminal progress — 0.6.1

Interactive copies and syncs read rclone's structured JSON statistics. The terminal shows checked/copied file counts, removals, moves and elapsed time; an activity indicator continues updating while waiting for the next sample. The transfer percentage uses byte counters (file counts for zero-byte transfers). During discovery and deletions before copying, there may be no reliable total: the terminal shows activity without a percentage until transfer totals are known. This fast mode performs no extra inventory scan. The line adapts to terminal width, abbreviating paths and counters to avoid wrapping; resizing clears any reflowed rows. Moves into backup history alone do not count as completed transfer progress. These terminal details remain outside the JSON audit/stdout.

`[backup].verification_mode = "simple"` is the default, even for existing configurations without this key; `"hash"` enables SHA-256/MD5 content checks. The CLI override is `--verification-mode simple|hash`. Hash cache and periodic-full-scan options only apply to hash mode. Switching to hash recomputes absent hashes; batch mode switches can rebuild archives. A hash verification requires a hash-mode backup first if the index has no hashes. Restores check sizes and any hashes recorded in their index; archive checksums remain enabled in both modes.

Interactive stderr shows stage, directory and backup completion, with file/byte counts for inventories, known-directory estimates during discovery, rclone's transfer percentage for bulk copies and elapsed-time estimates for other opaque subprocess operations. The percentage covers the entire backup across stages, directories and jobs; it never decreases and only reaches 100% after success. Per-file events appear in JSONL or stdout only when their respective log level is `detailed`. Bulk copies display the actual subdirectory. Progress is never included in JSON logs or stdout, and is disabled when stderr is not a terminal. See the [complete guide](configuration.md#log-retention).

## Symbolic links

Symbolic links in source and local destination data are always ignored. Linked directories are not traversed; links to files, broken links and cycles do not stop a normal inventory. Links and their targets are not copied through the link, compressed or represented as `.rclonelink` files. A target that also appears independently as a regular file is backed up normally.

Rclone skips source and destination links without following them. In batch jobs, indexed copies/history for those paths stay protected. In simple syncs, a source link is absent from the source tree, so a same-named destination file can be classified as removed and moved into `--backup-dir`; the normal history expiration deadline still applies. Simple run histories are purged as whole directories, including any newly excluded paths within an expired run. A local destination link suppresses copying a colliding source path, without changing the link or its target.

Rclone normally skips symlinks. Sambinha passes `--skip-links` to suppress warnings and `--links=false --copy-links=false` to override link-enabled environment settings. [Official rclone documentation](https://rclone.org/local/#skip-links). This behavior is unconditional and requires no configuration.

Metadata/state/lock paths and restore output remain protected against link redirection. Unsafe archive members and source/destination paths changing into links during transfer can still produce an explicit error rather than follow the new target.
