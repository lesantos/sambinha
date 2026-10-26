# Sambinha changes

## 0.17.0 — 2026-10-26

Faster backups and a cleaner architecture. The final source verification no longer rereads every file in hash mode when the stat signature recorded by the scan still matches, and walks directories through open descriptors (about 3x fewer `stat` calls); hashing uses 1 MiB blocks with md5 on a helper thread, the generated archive is fingerprinted with md5 only, and in hash mode the CRC32 reported by 7-Zip is cross-checked against the one computed during the inventory. Source discovery with exclusions asks rclone for one recursive listing per first-level subtree instead of one request per directory. Only paths missing from the current inventory are reclassified against the exclusion rules, the fingerprint cache moves out of `state.json` and the published recovery index into a private `fingerprints.json` (and is no longer kept at all in simple mode), the state snapshots no longer deep-copy the whole index, local controls are validated once per run, and index pruning lists only `.sambinha/indexes`. New optional job settings `fast_list`, `s3_chunk_size` and `s3_upload_concurrency` tune S3 transfers. Breaking changes: the CLI now uses subcommands and options go after the action (`sambinha restore --job x --output dir`; `sambinha` alone and `sambinha backup` still run a backup, and the old order fails with a hint); `sambinha.api` is the public facade and `engine`, `recovery` and `config` no longer re-export internal names; `Config` and `Job` are frozen, slotted and keyword-only (`Job` keeps name, source and destination positional), and the per-job override fields of `Config` are replaced by `with_job_overrides`. Internally: `StorageBackend` protocol with `SimpleOperations` composition instead of a mixin, a uniform strategy registry (`simple` and `batch`), an injectable `storage_factory`, `Context.worker_view` instead of `copy.copy`, a single CLI option table, and the test suite split into `tests/unit` (no rclone or 7-Zip needed) and `tests/integration` with a reusable `FakeRunner` and about 80 new tests for previously uncovered branches.

## 0.16.0 — 2026-10-26

Run the same backup from several machines and resume interrupted batch backups. A job is now identified by its name (state schema 3, identity schema 2) instead of the absolute source and destination paths, so a Linux box and a macOS box with different mount points can back up the same directories to the same destination without losing history; paths are kept only as informational `origins`. State created by an earlier version migrates automatically on the machine that created it; a job copied to another machine with different paths, or a renamed job, needs `--adopt-identity` once. A `.sambinha/job.json` marker at the destination refuses a different job sharing the prefix, and detects when another machine published a newer index. Interrupted batch backups now keep an upload journal (`uploads.json`): the next run reuses batches that were already uploaded and still match the source instead of recompressing them. After each commit the backup reports orphaned archives, controls and unpaired index generations at the destination (event `orphans`); `--prune-orphans` removes those older than one day, never touching `.sambinha/history` and respecting the mass-deletion guard. `--fresh-start` (or `resume = "fresh"` in a job) discards the fingerprint cache, journals and leftovers while keeping the indexed state, so changed files are still detected. Temporary directories carry the process id and leftovers of dead processes older than one hour are swept at startup. Fix a timing-dependent cancellation test.

## 0.15.1 — 2026-10-26

Fix seven bugs found in a code audit and make interruptions clean. Batch archives now accept file names with backslashes (restore and integrity checks refused whole batches), trailing spaces or tabs (7-Zip trimmed them from the listfile and the job failed forever; members are now passed on the command line after `--`), and names that are not valid UTF-8 fail early naming the file instead of crashing with a serialization error. `verify-destination-integrity` no longer raises an AttributeError on local passthrough batches, and unexpected per-job errors in recovery commands are logged with the job name. Local control mirrors of expired batches are removed only after the state is saved, and publication tolerates a missing mirror, closing a window that could block a job permanently. Passthrough batches are verified once instead of twice. Credential-like environment values shorter than six characters are no longer redacted from error messages. SIGTERM now unwinds like Ctrl+C: the end event is logged, the lock and temporary directories are released, every child process (rclone, 7-Zip, the discovery server) is terminated by process group, batch workers stop before starting a compression or upload whose result would be discarded, and the CLI exits 143 (130 for SIGINT). Maintainability: shared constants and literal types in `names`, one `reject_symlinks` helper, a shared `session` for backup and recovery setup, typed `ForeignStateError`/`UnsupportedStateError` instead of message matching, ruff and mypy configuration, pytest as the test runner (parallel with xdist), pre-commit, Dependabot, a lint job and a Python 3.12/3.13 matrix in CI, and a Makefile with the developer commands.

## 0.15.0 — 2026-10-26

Run on macOS in addition to Linux. Read the mount table from the BSD `mount` command when `/proc/self/mountinfo` is unavailable, classify APFS and HFS+ as POSIX volumes and `msdos` as FAT, and verify `source_mount`/`destination_mount` by comparing devices with the parent directory where util-linux `mountpoint` does not exist. Fall back from `O_PATH` to a non-blocking read-only descriptor for metadata-only inventories. Resolve the default temporary directory once per invocation, because macOS places it under `/var` (a symlink to `/private/var`) and metadata work directories refuse symlinked ancestors. Add `tests/test_macos.py` with simulated-platform tests that run everywhere and live tests that run only on a Mac, plus an informative macOS job in GitHub Actions; the live tests, including end-to-end simple and batch backups, pass on the `macos-latest` runner. Document Homebrew installation, `/Volumes` mount points, case-insensitive APFS, Full Disk Access and `launchd` scheduling. Linux behavior is unchanged.

## 0.14.9 — 2026-10-26

Check the versions of the external programs before using them. Backups, restores and verifications now stop with a clear error when rclone is older than 1.60, and batch operations stop when the compressor is older than 7-Zip 22.00 (the first release whose listing keeps sub-second modification times; 21.07 and p7zip 16.02/17.x do not). Versions that cannot be read are accepted. Correct the documented minimum from 7-Zip 21 to 22.00, state both minimums in the READMEs and installation guides, and stop recommending the outdated `rclone`/`p7zip-full` apt packages on Ubuntu 22.04. Add unit and integration tests with fake executables.

## 0.14.8 — 2026-10-26

Make `verify_source_changes = false` also skip the directory identity checks during source inventory; on CIFS mounts with `serverino` a directory inode can change between listing and opening, which aborted backups with "Diretório mudou durante leitura". Force `RCLONE_LINKS`, `RCLONE_LOCAL_LINKS`, `RCLONE_COPY_LINKS` and `RCLONE_LOCAL_COPY_LINKS` to `false` in every subprocess, because rclone 1.75 lets these variables override `--links=false`/`--copy-links=false` and source symlinks could be copied. Make timestamp-sensitive tests wait for the filesystem clock to advance (Linux 5.15/ext4 stamps mtime/ctime coarsely). The full suite requires rclone 1.60+ and 7-Zip 22.00+ (`7zz`); the distribution packages rclone 1.53 and p7zip 16.02 fail unrelated tests.

## 0.14.7 — 2026-10-16

Add per-job `verify_source_changes` to control consistency checks while reading/copying source files. When enabled, retry source-change races up to three times after the initial attempt, then fail the backup. Keep the default enabled in application configuration and examples; disable it in the local Sambinha configuration. Document the tradeoff and cover retries and opt-out behavior.

## 0.14.6 — 2026-10-16

For oversized individual batch files with recognized compressed-format extensions, upload the original file directly and restore/verify it without recompressing. Add planner and local round-trip regressions.

## 0.14.5 — 2026-10-16

Move `temp_dir` from individual jobs to the shared `[backup]` configuration, alongside the other configured directories. All jobs in one backup configuration now use the same existing writable temporary directory; relative paths resolve from the TOML file. Update validation, examples and documentation to reflect the shared setting.

## 0.14.4 — 2026-10-16

Build batch archives directly from source files, removing the temporary copy of originals and reducing staging to approximately one archive per worker. Validate the source before and after compression and stop upload if files differ from the inventory. Process batches exceeding `max_megabytes` one at a time. Add per-job `temp_dir` to select an existing temporary directory on another disk, with relative paths resolved from the TOML file, and update diagnostics, examples and documentation.

## 0.14.3 — 2026-10-15

Fix false source-change errors when native exFAT/FAT reassigns an unpinned inode number between discovery and opening a file or directory. Detect affected devices from the current mount namespace, including nested mounts; accept only an inode-only difference with matching device, type, size, mtime, ctime and mode. Unknown or ambiguous filesystem information keeps strict validation.

Use the opened descriptor as the directory baseline until its pathname is rechecked. Pin files through metadata/hash inspection and final descriptor/path checks; simple mode uses `O_PATH` and never reads contents. Keep strict identity checks while objects are open and conservative inode-based fingerprint-cache reuse. Error messages now include the comparison stage and complete before/after observations. Source symlinks remain ignored, and discovery stays sequential. Add inode-renumbering, replacement, diagnostics, descriptor-cleanup and device-detection regressions; validate a controlled historical-inode change on a real read-only exFAT mount.

## 0.14.2 — 2026-10-15

Combine native filtered discovery and batch inventory in one sequential mtime/name DFS walk, inspecting files as each directory is discovered. Use open directory descriptors and no-follow relative operations instead of repeating ancestor-link checks for every file. Source symlinks, including directory links, broken links and cycles, remain ignored; their targets are never inventoried or copied through the link. Preserve hash-cache signatures, race detection, native exclusions and complete-inventory publication guards.

Use one synchronous `operations/list` HTTP request per directory, removing asynchronous job polling and sleeps. Keep independent directory deadlines and stop/reap the private rclone server on failures or interruptions. Apply the combined walk to local metadata verification as well. Add inventory/race regressions and a reproducible read-only before/after benchmark.

## 0.14.1 — 2026-10-15

Show the current directory or file path last in terminal progress, after the percentage, stage and activity counters. Keep counts ahead of variable-length paths in both full and compact layouts. Preserve terminal-width fitting, Unicode handling, redaction and indeterminate progress. Add layout regressions and update bilingual documentation.

## 0.14.0 — 2026-10-15

Discover batch sources with exclusions through one private, authenticated local `rclone rcd` process. List one directory at a time using `operations/list`, with native filters anchored to the original source root, instead of launching rclone and building dummy filter trees for every directory. Apply the configured timeout independently to each directory; cancel outstanding work and stop/reap the server on failure, timeout or interruption. Never commit a partial inventory. Show directory/file discovery counters and keep historical-path matching unchanged.

Document the glob `Icon[\x0d␍]` for macOS Finder Icon files, including rclone's visible CR representation, without disabling directory-filter optimization through `{{...}}`. Add regressions for native patterns, pruning, one-process traversal, links, authentication, timeouts, invalid responses, interruption and committed-state preservation. Existing backups, configuration and archive formats remain compatible.

## 0.13.3 — 2026-10-15

Make retention, verification, logging, deletion alerts, fingerprint caching, transfer and batch concurrency, recovery-index history and dry-run settings independent per job. Add `control_storage_class` for S3 JSON/control objects; `storage_class` continues to apply to `.7z` batch archives. Control objects default to `STANDARD`, while batch archives use their configured storage class. Move settings into each job in both public TOML examples.

[Português do Brasil](CHANGELOG.pt-BR.md)

## 0.13.2 — 2026-10-15

Keep at most `batch_workers` batch tasks in flight and stop scheduling pending batches after any worker failure, including errors observed before an earlier worker finishes. Wait for in-flight cleanup and preserve transactional state/index publication.

Remove source snapshots after archive integrity and member validation, before uploads. Estimate temporary space from the actual largest simultaneous batches (originals plus an incompressible archive and overhead), including oversized groups and dedicated files, and recheck before copying each batch. Keep the existing 95% occupancy check and mandatory temporary checks. Update bilingual guidance and add blocked-S3, failure-cancellation, cleanup and capacity regressions.

## 0.13.1 — 2026-10-14

Replace `max_bytes` with `max_megabytes` and `--max-bytes` with `--max-megabytes`. Express the limit in MB (1 MB = 1,048,576 bytes), default 1024. Accept decimals; update examples and migration instructions while preserving batch sizes.

## 0.13.0 — 2026-10-14

Introduce stable batch membership with logical IDs, mtime/name DFS traversal and uniform compression (level 3, two threads). Remove count/category boundaries. Divert files reaching `individual_batch_percent` (100%) without closing the normal batch. Edits preserve members even beyond max_bytes; empty groups follow retention.

Add `batch_size_alert_percent` (200%; 0 disables) for nonindividual groups, aggregating final Cronitor failure while preserving successful operational exit status. Add source-independent manual `rebalance`, changing only oversized groups and preserving conforming/individual archives.

Restore no longer depends on the source path, mount or capacity. Backups bootstrap missing/corrupt local state before planning. Immutable recovery generations with SHA-256/size confirmations, a durable journal and validated fallback support interrupted-run recovery; `recovery_index_versions = 3` bounds metadata generations. Preserve legacy controls/indexes, history and unique active paths. Document migration and recovery limits.

Rename `integrity-test` to `verify-destination-integrity` and its Python entry point to `verify_destination_integrity_job`; the old CLI name is no longer accepted. Compare archive listings and recorded timestamps in UTC to avoid false mismatches caused by historical daylight saving offsets. Apply `TZ=UTC` only to listing subprocesses, preserving the configured environment. Add real 7z/ZIP regressions across timezones and update bilingual commands and documentation.

Rename `verify` to `verify-local-metadata` and `verify-metadata` to `verify-destination-metadata`, including their Python job entry points. Update commands, tests and bilingual documentation. The old CLI names are no longer accepted; update scheduled commands accordingly. Existing verification behavior, configuration and log event names are preserved.

## 0.12.0 — 2026-10-14

Restore batch data on the output filesystem, with per-batch temporary cleanup, protected publication and support for filesystems without POSIX attributes or hard links. Keep archive downloads and extraction off the machine's default temporary disk.

Separate daily `verify` (source paths/size/time against local JSON mirrors, no S3 reads), monthly `verify-metadata` (replica agreement and archive size metadata) and manual `integrity-test` (7-Zip listing/integrity without extracted files). Add configurable `metadata_dir`, an identity-bound local active catalog, migration from committed local state and atomic JSON publication. Advance active state only after updated archives and both local/remote controls succeed; retain STANDARD for S3 JSON controls and recovery indexes. Document accepted archive timestamp precision loss, lifecycle considerations and scheduling.

Report unknown TOML parameters with job name, parameter names, source lines and configuration path. Update bilingual guides and complete examples. Add regressions for cache-only daily checks, metadata divergence, publication failures/retries, storage classes, timestamp precision, destination-disk restore, corruption and overwrite/link protection.

## 0.11.0 — 2026-10-14

Refactor backup orchestration into dedicated execution context, job lifecycle, preflight, archive creation and simple/batch strategy modules. Give preflight inventories named, typed fields; split batch planning, upload selection and bounded workers into separate functions. Share index identity and active-file lookup with recovery, and keep the existing engine import entry points. Preserve CLI/configuration, ordering, retention, state schemas, storage operations, error policies and telemetry.

Separate recovery index loading, archive validation, staging/publication and verification from action orchestration. Isolate rclone command construction from storage mutation guards and native tree operations. Group configuration validation by global options, job options, paths and destination overlap, preserving validation order and error messages. Keep the existing configuration, Storage and recovery function entry points.

Move test doubles to the modules that own their responsibilities. Correct an existing invalid-filter test to allow the empty destination root already prepared by native sync, while still requiring no destination entries, no committed index and failure notification; application behavior is unchanged.

This release also includes the previously committed destination-link changes below.

Avoid the extra destination scan on native exFAT/FAT without link-capable submounts; retain root checks and conservative scanning for unknown filesystems.

Never create destination links. Ignore source links and leave nonconflicting destination links intact; replace conflicting local data links with real files/directories without touching their targets, respecting exclusions and dry runs. Rebuild batch archives/controls that became links and ignore links in expired batch history. Keep destination-root and metadata link protections. Update and enable the two formerly deferred destination-link tests, with additional regressions for broken links, exclusions, simulation, batch rebuilding and directory races.

## 0.10.2 — 2026-10-13

Add independent `error`, `summary` and `detailed` levels for JSONL file and terminal logs. Both default to errors and execution summaries. Simple sync now requests only rclone `ERROR` messages by default instead of per-file `DEBUG`; detailed output requests and records rclone diagnostics. Interactive progress receives periodic statistics without enabling other messages, and detailed streaming keeps a bounded error tail in memory.

Rclone's `--update` still protects newer destination files. With rclone 1.60, their skip notice exists only at `DEBUG`: the default low-volume mode cannot identify these conflicts and does not fail the job for them. Set either log output to `detailed` to retain conflict reporting and failure for manual review. Batch conflict handling is unchanged.

## 0.10.1 — 2026-10-13

Keep progress within the terminal width, compact counters and abbreviate paths, including wide/combining Unicode characters. Clear reflowed rows after resizing. During phases without a reliable total, show activity instead of a frozen percentage, with no additional inventory scan; resume the measured percentage when transfer totals become available.

Read rclone progress from structured JSON byte/file counters, including one-line statistics without a `Transferred:` prefix. Interactive copies and syncs show checked/copied/deleted/moved counters and a heartbeat with elapsed time. Discovery/deletion activity does not invent transfer progress; bytes moved into backup history do not falsely complete the transfer stage. Keep the overall percentage monotonic and reserve 100% for successful completion.

Detect destination POSIX attribute support for backups and restores. Avoid ownership and permission changes on exFAT, FAT and filesystems without known support, preserving contents and timestamps. Keep metadata on POSIX filesystems and S3. Detection is read-only, including simulation; batch restore also stops requiring `chmod` on these destinations.

## 0.10.0 — 2026-10-12

Keep simple jobs on native `rclone sync` and `--backup-dir`, and purge expired run histories as the final job step using the longer of `deleted_days` and `version_days` (rclone history does not distinguish deletions from replacements). Preserve destination files newer than the source, report their paths and fail the job for manual review. Automatically replace file/directory type conflicts while retaining the displaced data in history. Add `restore --path` for selected current files and directories in simple jobs. Reject overlapping destinations across jobs while allowing an earlier destination to feed a later source. Keep the per-job absolute `max_delete` limit and rclone native exclusions.

Update bilingual guidance and tests for local/S3 history expiry, newer destinations, replacements, overlapping destinations, selected restores and batch retention alerts.

## 0.9.1 — 2026-10-12

Show rclone's live transfer percentage during interactive bulk copies, so the overall backup progress follows the active transfer instead of a time-based estimate. Keep elapsed-time estimates for other opaque subprocess work. Update bilingual usage/configuration documentation and release metadata.

## 0.9.0 — 2026-10-12

Run every job's full lifecycle sequentially in TOML order: freeze exclusions, preflight, inventory, transfers/retention, state commit, scheduled verification and summary. Later jobs observe earlier writes, including newly created origins and exclusion files. Remove cross-job source/destination and destination/destination overlap restrictions for local and S3 backups; keep each job's own source/destination and state/log protections. A later preflight failure no longer blocks already completed jobs.

Remove shared inventories and make deletion-alert thresholds independent per job, matching separate invocations. Use equal per-job progress ranges with byte-weighted work inside each job. Add real local/S3 integration tests comparing combined and separate commands, fresh caches, overlapping destinations, scheduled/manual verification, restoration and failures. Document shared-destination removal/overwrite, stale indexes, last-writer remote batch controls, reserved source-root metadata and dry-run dependencies.

## 0.8.0 — 2026-10-12

Ignore source/destination symbolic links without following, copying or translating them. Protect existing backed-up paths/history while the corresponding source or destination path is a symlink. Explicit rclone flags enforce this behavior even when environment settings enable link handling. Shorten both READMEs and preserve detailed instructions in the complete configuration guide.

Add per-job source/destination disk-check switches, keeping temporary storage checks enabled. Attribute job failures in messages, logs and notifications, including global preflight. Add per-job `continue_on_job_error = true` to continue independent jobs, or false to stop on the first failure, with matching behavior for backup and manual verification. Document settings in bilingual READMEs, usage guides and configuration examples.

## 0.7.0 — 2026-10-11

Add configurable audit-log retention: `log_retention_days = 14` by default, with `0` disabling cleanup and a CLI override. Normal backup, restore and verification invocations prune inactive, recognized log files by last-write age. Current/open logs, unrelated files, symlinks and nested directories remain protected; dry runs never prune logs. Progress lines now put the overall percentage first, before the stage and working subdirectory. Bilingual configuration examples and READMEs document log retention and exclusion files. This release includes all changes committed after 0.6.2.

## 0.6.2 — 2026-10-11

Interactive terminals omit per-file audit events and show the actual working subdirectory. Progress estimates the entire backup with weighted phases/jobs and concurrent batches, never restarting or decreasing across directory/stage changes; only successful completion shows 100%. Complete per-file details remain in JSON logs. Bulk copy groups stay within one directory. Document exclusion-file paths, native rclone patterns, simulation and protection of already backed-up excluded files in both READMEs. Public configurations include the exclusion-file path and bilingual templates with no active exclusions.

## 0.6.1 — 2026-10-11

Default to metadata-only inventories and file comparisons with `verification_mode = "simple"` (size in bytes and nanosecond modification time). Select `"hash"` in TOML or `--verification-mode hash` to use SHA-256/MD5 and periodic fingerprint-cache refreshes. Simple mode avoids source hash reads and destination hash listings; switching to hash recomputes missing hashes. Archive/control checksums remain enabled. Interactive progress shows stage, directory and estimated completion on stderr without writing progress to JSON logs. Interrupted backups no longer log a successful end. Add mode-switching, metadata precision, recovery and terminal/log separation tests; update bilingual examples and documentation.

## 0.6.0

English becomes the default documentation language. README, changelog, security notes, usage/S3/architecture/specification/performance/test guides and public commented configuration examples have Brazilian Portuguese versions, with reciprocal links and checks for example equivalence. Release metadata is 0.6.0.

## 0.5.0

Implement P1–P6 in order: fingerprint cache with inode/device/size/nanosecond mtime/ctime/mode validation and periodic full scans; bounded bulk rclone transfers; shared inventories for equivalent source selections; limited concurrent batch compression/uploads; execution-scoped filter caching; per-job compression levels and threads for text, video, photo and other files. Defaults favor low effort for already compressed media. Existing intact archives remain reusable.

## 0.4.1

Harden index/control validation, relative paths and active/retired separation; make metadata/log/lock permissions private; reject metadata symlinks; expand secret redaction and protect safe recovery extraction and exclusive output creation. Tests reproduce unsafe input and disclosure cases.

## 0.4.0

Add `restore` for active-index files/folders/full backups, `verify`, scheduled integrity checks, a guard against abnormal source removals and per-job summaries. Source/local destination/staging disks fail above 95% occupancy. S3 integrity verification compares recorded object size and MD5 through metadata only, never downloading objects or missing indexes. Legacy batches without recorded upload checksums fail verification explicitly. Actual restoration downloads selected content.

## 0.3.1

New batches group files oldest-first within each directory, with path as the tie breaker. Existing intact compatible archives retain their original keys/member order, including earlier releases. Only new files and affected archive members are repacked.

## 0.3.0

Add nonempty mounted-volume checks through `source_mount`/`destination_mount`, advisory permanent-deletion warnings through `deletion_alert_threshold`, native rclone `exclude_files` with protected history/mixed archives, and protection for newer destination files. Errors are reported to Cronitor with conflict details.

Add optional private dotenv `secrets_file` input using python-dotenv, isolated subprocess environments and inherited-variable priority. CLI supports selecting or disabling it. Missing/invalid input fails without revealing values. Public examples cover every configuration option; CI checks complete Git history with Gitleaks.

## 0.2.0

Rename the project, package, executable, configuration and metadata namespace to Sambinha/`sambinha`/`sambinha.toml`/`.sambinha`. Rewrite the README and add separate recovery/S3/architecture guides. Old namespace states require separate destinations/state. CI checks local/emulated-S3 round trips and coverage, using Node.js 24 actions.
