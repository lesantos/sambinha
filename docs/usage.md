# Configuring and recovering backups

[Português do Brasil](usage.pt-BR.md) · [README](../README.md)

## Commands and exit codes

Omitted CLI options inherit the TOML. `backup` is optional and remains the default action. `--source`, `--destination`, `--type` and `--logic` require one selected job. `--no-dry-run` overrides simulation enabled in the file.

```bash
sambinha --job documents-s3 --max-megabytes 100 --dry-run
sambinha --job documents-local --deleted-days 30 --version-days 7
sambinha --config /path/another.toml --job documents-local --destination /mnt/backup
sambinha --help
```

Exit codes: **0** success, **1** operation/notification failure, **2** configuration error, **130** interruption. Independent jobs continue after a job failure. Global preflight checks sources and dependencies before any transfer. Types: `local`, `s3`; strategies: `simple`, `batch`; archive formats: `7z`, `zip`. `compressor` accepts `7z`, `7zz` or another compatible CLI executable.

## Batch rules

1. Sort files and subdirectories together by mtime/name inside each directory, visiting subtrees depth first before the next sibling.
2. New normal batches accumulate original bytes up to the `max_megabytes` limit in MB (default 1024; 1 MB = 1,048,576 bytes), with no member-count/category boundaries. Compressed overhead may exceed this limit.
3. Divert files reaching `individual_batch_percent` (default 100%) without closing the open normal batch; never split files.
4. Keep confirmed group membership and member order through growth/mtime changes; new files form new groups. Empty groups leave active state subject to history retention.
5. All types use `compression_level = 3` and `compression_threads = 2` on create/rebuild. Immutable revision names, JSON controls and logical `batch_id` support history without active duplicates.
6. Aggregate nonindividual growth alerts at `batch_size_alert_percent` (200%; 0 disables), sending final Cronitor failure without interrupting jobs or changing successful operational exit 0.
7. `rebalance` is manual and source independent; change only oversized groups, preserving conforming/individual archives. Do not consolidate every healthy small group.
8. Validate snapshots/content before uploads, publish confirmed recovery generations after complete archives/controls and reconcile publication journals. Recover missing/invalid state from the destination before planning mutations. See [complete semantics and migration](batch-lifecycle.md).

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

Batch downloads, extraction and staging use a temporary directory inside `--output`. Archives and unselected extracted members are removed after each batch. Selected files are kept on the output disk until all requested content is validated, then published without overwriting. On filesystems without hard links, publication temporarily needs an additional file's space. The machine's default temporary disk is not used for batch restoration.

## Integrity verification and scheduling

```bash
sambinha verify-local-metadata --job documents
sambinha --verify-interval-days 7
```

Batch `verify-local-metadata` compares source paths/size/time with local JSON mirrors and reports needed updates, without archive reads, hashes or S3 requests. `verify-destination-metadata` separately checks local/destination JSON agreement and archive size metadata; S3 JSONs and recovery indexes use STANDARD and only metadata files are downloaded. Manual `verify-destination-integrity` lists/tests archives through 7-Zip without extracted files; S3 archives are downloaded one at a time. Simple `verify-local-metadata` still runs `rclone check`. Configure `metadata_dir = "./metadata"` beside state/logs. See [batch activities, timestamp precision and publication guarantees](batch-checks.md).

`verify_interval_days` schedules indexed batch checks; it does not apply to simple jobs. 0 disables scheduled source checks. Integrity tests are manual. Only success updates `last_verified`. Manual verification checks immediately. Simple-job `verify-local-metadata` runs `rclone check` with that job's exclusions.

## Summary and performance

Batch jobs emit indexed file and batch counts. Simple jobs log the `rclone sync` strategy and per-job absolute `max_delete`; rclone may have partially applied a sync when an error occurs. [Performance settings](performance.md) document native sync and batch profiles.

## Compatibility and operational limits

Existing 0.3.x state remains compatible; intact legacy batches stay intact. Legacy archives without a recorded upload size cannot receive the monthly archive-size check until replaced after changes; daily source checks and manual 7-Zip tests remain available. The earlier 0.2.0 rename introduced `sambinha`, `sambinha.toml` and `.sambinha`; states from another metadata namespace are rejected. Keep an old destination and state together, and use a new destination/state to migrate an older namespace. Begin with simulation.

Symbolic links are ignored. Special files and newline-bearing source filenames are rejected. `.sambinha` at the source root is reserved. Each job must keep its own source/destination separate and all jobs must stay outside state/log directories. Destinations cannot overlap each other; one job's destination may be a later job's source. See [the interaction rules](configuration.md#sequential-jobs-and-overlapping-paths). Local POSIX ownership changes are not required; S3 has no native POSIX mode bits. Without filesystem snapshots, concurrent source writes can abort a backup. In hash mode, the fingerprint cache assumes trustworthy filesystem metadata and forces periodic content scans; disable it when full reads on every run are required.

Batch reuse trusts the local mirror and exclusive remote prefix ownership. Daily checks detect source updates; monthly metadata checks detect missing/different controls or archive sizes; manual integrity tests detect archive corruption. Simple sync comparison behavior follows rclone and its backend settings.

## Verification modes and terminal progress — 0.6.1

Interactive copies and syncs read rclone's structured JSON statistics. The terminal shows checked/copied file counts, removals, moves and elapsed time; an activity indicator continues updating while waiting for the next sample. The transfer percentage uses byte counters (file counts for zero-byte transfers). During discovery and deletions before copying, there may be no reliable total: the terminal shows activity without a percentage until transfer totals are known. This fast mode performs no extra inventory scan. The line adapts to terminal width, abbreviating paths and counters to avoid wrapping; resizing clears any reflowed rows. Moves into backup history alone do not count as completed transfer progress. These terminal details remain outside the JSON audit/stdout.

`verification_mode = "simple"` inside each job is the default, even for existing configurations without this key; `"hash"` enables SHA-256/MD5 content checks. The CLI override is `--verification-mode simple|hash`. Hash cache and periodic-full-scan options only apply to hash mode. Switching to hash recomputes absent hashes; batch mode switches can rebuild archives. Daily batch verification deliberately uses size/time regardless of the backup inventory mode. Restores check sizes and any hashes recorded in their index; archive checksums remain enabled in both modes.

Interactive stderr shows stage, directory and backup completion, with file/byte counts for inventories, known-directory estimates during discovery, rclone's transfer percentage for bulk copies and elapsed-time estimates for other opaque subprocess operations. The percentage covers the entire backup across stages, directories and jobs; it never decreases and only reaches 100% after success. Per-file events appear in JSONL or stdout only when their respective log level is `detailed`. Bulk copies display the actual subdirectory. Progress is never included in JSON logs or stdout, and is disabled when stderr is not a terminal. See the [complete guide](configuration.md#log-retention).

## Symbolic links

Source symbolic links are always ignored. Linked directories are not traversed; links to files, broken links and cycles do not stop a normal inventory. Sambinha never creates destination links or translates them into `.rclonelink` files. A target that also appears independently as a regular source file is backed up normally.

In a local destination, a link that conflicts with a selected source file or directory is unlinked and replaced by a real file or directory. Only the link is removed; its target is untouched. Links without a conflict, including excluded paths and paths corresponding only to source links, are left intact. Dry runs leave every destination link intact. Batch uploads apply this policy to archive/control paths and rebuild active archives whose paths have become links.

In batch jobs, indexed copies/history at source-link paths stay protected. In simple syncs, a source link is absent from the source tree, so a same-named regular destination file can be classified as removed and moved into `--backup-dir`; the normal history expiration deadline still applies. Simple run histories are purged as whole directories, including any newly excluded paths within an expired run.

Rclone normally skips symlinks. Sambinha passes `--skip-links` to suppress warnings and `--links=false --copy-links=false` to override link-enabled environment settings. [Official rclone documentation](https://rclone.org/local/#skip-links). This behavior is unconditional and requires no configuration.

Destination roots, metadata/state/lock paths and restore output remain protected against link redirection. Link replacement is limited to data paths below the job root; directory components are opened without following links during preparation. Unsafe archive members and detected concurrent path changes still cause an explicit error.


## Batches and recovery in 0.13.0

See [stable batches, uniform compression, final Cronitor alerts, manual rebalance and generation recovery](batch-lifecycle.md) for current behavior and migration from 0.12.0.
