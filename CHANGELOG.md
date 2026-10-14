# Sambinha changes

[Português do Brasil](CHANGELOG.pt-BR.md)

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
