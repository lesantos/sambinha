# Sambinha requirements

[Português do Brasil](specification.pt-BR.md)

This document consolidates the original backup requirements and all product changes requested in the conversation through version **0.7.0**. English is the default version; the Brazilian Portuguese companion must contain equivalent requirements. The sections below define behavior and acceptance criteria; release milestones at the end provide traceability. The 0.6.1 section updates the earlier hash-only requirements.

## 1. Platform, architecture and configuration

- Run on Linux, including Linux Mint, with Python 3.12 or newer. Runtime backups must not require root privileges.
- Keep readable code organized into separate domains, with comments explaining relevant behavior. Use existing Linux tools such as rclone and a 7-Zip-compatible compressor where they simplify the implementation.
- Maintain the project in the private `lesantos/sambinha` GitHub repository, with a README, usage/recovery guides, public configuration examples, tests and release notes.
- Validate dependencies, configured sources, mounts and local disk occupancy before transfers. Independent jobs must continue after a per-job failure; a global preflight failure can stop the invocation before transfers.
- Use TOML configuration with global `[backup]` settings and independent `[[jobs]]`. Support an alternative configuration file and CLI overrides; omitted CLI arguments inherit the file's values.
- Allow one source to have multiple independent jobs, destinations and backup strategies. Reject overlapping source/destination/state/log paths and overlapping destinations managed by different jobs.
- Resolve relative configuration paths from the TOML directory, and support home-directory and environment-variable references.
- Provide bounded timeouts and one initial transfer attempt plus at most three retries, two seconds apart.

## 2. Destinations, strategies and retained history

- Support local folders, including mounted volumes, and Amazon S3. Both destinations must support the `simple` and `batch` strategies.
- Copy only files that are missing or differ from the destination. Use checksums rather than falling back to size-only comparisons when a required checksum is unavailable.
- Simple backups must preserve original paths and file attributes/permissions where supported. Local simple backups must preserve empty directories. S3 does not offer native POSIX permissions; original metadata remains available in the index.
- If the destination has a newer file, preserve it, report the conflict and fail that job. Batch comparisons must use original timestamps from the confirmed index, not archive upload times.
- Keep deleted source files for 10 days and previous edited versions for 2 days by default, with configurable retention. Expire history during subsequent executions, including at the exact deadline; zero-day retention allows immediate cleanup.
- Save simple historical bytes and their retirement record before replacing or deleting the current destination file. Retired batch archives must remain at their immutable keys until expiry. A lot containing both edits and deletions uses the longest applicable retention.
- Reactivated active archives must not be deleted by earlier retirement records. Publish retry information when retention cleanup succeeds locally but updating the remote recovery index fails.
- Default S3 storage to `GLACIER_IR`, with configurable storage classes. Document access and billing characteristics in code/examples/guides without hardcoded prices. Simple S3 backups require a class readable without asynchronous restore.
- Store batch JSON controls and `.sambinha/control.tar.gz` in `STANDARD`, even when data archives use colder classes.

## 3. Batch planning, ordering and reuse — 0.3.1

- Group new files deterministically by directory. Within the same directory, consider files by original modification time in nanoseconds, **oldest to newest**, with path as the tie breaker.
- Give existing batches priority over rebuilding the directory from scratch. Preserve every intact compatible batch, its key, member order and stored compression, including batches made by earlier releases.
- Minimize affected batches: only new files and members of edited, removed or incompatible batches must be repacked. An intact batch must not be changed to fill a new batch or accommodate a file inserted earlier in the sort order.
- Respect `max_bytes`, default 1 GiB of original uncompressed bytes, and `max_files`, default 1000. A directory may span batches; small directory groups may share a batch. Archive overhead may make compressed size exceed the original-byte limit.
- New batches must separate compression categories while preserving the oldest-first processing order within each directory. Category boundaries may produce singleton archives.
- Prefer at least two members when limits and category permit. A preceding new batch with more than two members may lend a file to a new trailing singleton if both remain valid and use the same category. Oversized/unpairable files use individual archives.
- Store normal archives at the nearest common ancestor as `batch-<hash>.<format>`. Individual revisions use `<directory>/.versions/<hash>/<basename-with-archive-extension>`, avoiding collisions and retaining the requested basename.
- Preserve the source-relative directory tree inside archives. Support configurable 7-Zip-compatible compressors, with `7z` as default and `7z`/`zip` archive formats.
- Write a JSON control next to every archive, listing original file paths, sizes, SHA-256, MD5, timestamps and permissions. Newly uploaded archives must additionally record compressed size and MD5, enabling S3 checks without downloading the archive.
- Maintain a configurable local control mirror without retaining compressed data archives. Send a compressed recovery index containing state and controls to the destination.
- Use temporary staging and remove it after success or failure. Validate source snapshots, archive integrity and exact member paths/sizes before upload. Commit the new index only after all required archives, controls and the recovery index are uploaded successfully.

Acceptance: inserting an older new file must leave intact prior batch keys unchanged; editing or removing one member must not rebuild unaffected batches. Limit/format changes may replace only incompatible batches. Every selected file must occur exactly once in the active index.

## 4. Restore command — selected functionality 1 / 0.4.0

- Provide `sambinha restore` for a selected job, restoring a file, a folder's indexed contents or all active files.
- Accept repeatable literal source-relative `--path` selections and a required `--output`. Preserve the original tree; reject paths absent from the active index.
- Require a new or empty output directory separate from sources, backups, state and logs. Do not overwrite existing files or follow output symlinks.
- Stage the selected content and verify original size, SHA-256 and MD5 before publishing requested files. Restore ordinary permission bits and modification timestamps, without privileged mode bits.
- Work even when the original source is unavailable. Simple recovery requires its local index. Batch recovery may recover a missing index from `.sambinha/control.tar.gz`, reading only one regular `state.json` member limited to 64 MiB without arbitrary tar extraction.
- S3 **restoration** may download selected objects; selecting one file from an archive downloads that archive. Document retrieval costs and the need for prior AWS restore for `GLACIER`/`DEEP_ARCHIVE`.
- Restore the active revision through this command. Earlier historical versions remain recoverable manually through controls and history paths.
- Simulation must report selected file count/bytes without downloading selected content or creating the restore output. If a missing batch index must be recovered for selection, that recovery-index download belongs to restoration, not integrity verification.
- A later publication/write failure must return an error; already published verified files may remain.

Acceptance: restore binary and Unicode files byte-exactly, including selections and lost-source recovery; corruption must fail before requested output files are published, and existing output data must remain intact.

## 5. Integrity verification — selected functionality 2 / 0.4.0

- Provide `sambinha verify` for immediate checking, plus configurable periodic checks through `verify_interval_days`.
- Local simple verification must read and hash destination files. Local batch verification must validate recorded archive checksums when available, test the archive and verify extracted members against original metadata.
- S3 verification must use **only object metadata**, comparing size and whole-object MD5 with the local index. For batches, check both data archives and JSON controls.
- **Never download S3 object bodies, archives, JSON controls or recovery indexes as part of verification**, including error paths. Never request a Glacier restore to complete a check. Metadata API requests may still incur charges.
- Do not treat a multipart ETag as a whole-object MD5. Use the checksum supplied by rclone's S3 metadata interface; an unavailable required checksum must produce an explicit error.
- Require the local index for S3 verification. Legacy archives without recorded upload size/checksum must fail explicitly, without download fallback or automatic rebuilding of intact archives just to establish a baseline.
- State the limitation: metadata verification does not decompress archived members or prove their extraction against an adversarially modified index.
- Keep automatic checks disabled by default (`verify_interval_days = 0`) for compatibility. With a positive interval, check after successful backups when due; the first enabled execution must check immediately. This schedule depends on backup executions and does not install a separate timer.
- Update `last_verified` only after a successful periodic check. Failure must return error, retain the previous successful timestamp and notify Cronitor when configured. Manual verification does not update the periodic timestamp. Simulation must report planned verification without checking content or downloading objects.

Acceptance: run real rclone against an HTTP server allowing HEAD and rejecting GET; S3 verification must succeed using metadata without requesting object bodies. Missing local state, legacy checksums, corruption or unavailable hashes must fail without downloads.

## 6. Protection against mass removals — selected functionality 3 / 0.4.0

- Compare the current source selection with the previously confirmed selected inventory, excluding paths filtered out by current exclusion rules.
- Block the job before history changes, uploads, replacements or retention cleanup when the previous selected count reaches `deletion_min_files` and removed files exceed `max_delete_percent`.
- Default to at least 20 previous selected files and a strictly greater than 50% removal ratio. Exactly the threshold is allowed; the first backup has no baseline and is allowed.
- Allow configurable thresholds and a one-invocation `--allow-mass-deletion` bypass for intentional removals. Do not persist the bypass in job configuration or state.
- Record removed paths, counts and threshold, return failure and notify Cronitor when configured. Other independent jobs may continue.
- Keep this blocking source-removal guard distinct from the advisory warning about permanent history deletion.

Acceptance: blocked simple and batch jobs must leave destination contents and the confirmed state unchanged. Exact-threshold cases and an explicit one-run bypass must behave as configured.

## 7. Disk occupancy and mount monitoring — 0.4.0

- Monitor the source filesystem and the local destination filesystem. For batch jobs, also monitor local temporary staging; restoration must monitor its output filesystem.
- Generate an error when occupancy is **above 95%**. Exactly 95% is allowed. Include space unavailable to the current account in the occupancy calculation.
- Measure a not-yet-created destination using its nearest existing ancestor, without creating the backup directory just to measure space.
- Check before backup, before destination changes and after the strategy, so a final transfer crossing the threshold is reported as failure. Record occupancy/free bytes in `disk_usage` events; report failures to Cronitor when configured.
- Do not invent a destination-disk percentage for S3 buckets. For S3 jobs, measure the applicable local source/staging filesystems.
- Optional `source_mount` and local-only `destination_mount` must identify mounted, nonempty expected volumes. Missing, empty, unreadable, misplaced or lost mounts must stop affected operations before writing to an unintended volume.

Acceptance: test below/exactly/above 95%, reserved space, nonexistent destinations, full source/destination, a final transfer crossing the threshold and local staging for S3 jobs.

## 8. Logs, simulation, summaries and notifications — selected functionality 5 / 0.4.0

- Write a log for each invocation, recording start/end, jobs/directories, copied files, identical/skipped files, guards and errors. Use optional configurable Cronitor lifecycle/error notifications through external `CRONITOR_URL` credentials.
- Provide dry-run execution that reports planned work and writes its report, without creating backup state/archives, transferring/deleting backup objects or notifying Cronitor.
- Emit a per-job `summary` for execution and simulation with success, dry-run status, total/new/modified/unchanged/removed files, reused/rebuilt batches, planned original bytes, actually transferred payload bytes and pending historical deletions.
- Exclude control/recovery-index overhead from transferred payload bytes. Distinguish original planned bytes from compressed transmitted bytes. Partial transfers on failure must not be reported as a completed transaction.
- Preserve `deletion_alert_threshold`, default 1000: strictly exceeding it triggers an advisory warning about impending permanent deletions. Aggregate across jobs and count original files/versions within archives, excluding protected history.
- Use a warning window of two days, one day for one-day retention and immediate warning for zero-day retention. This warning must not stop backup, change deadlines or turn an otherwise successful backup into a failed exit code.

Acceptance: summaries must account correctly for added/edited/deleted/unchanged files, archive reuse/rebuild and simulation's zero transferred bytes; existing retention notifications must retain their advisory behavior.

## 9. Security review and critical corrections — 0.4.1

- Review the code against established security guidance and implement the most critical applicable corrections, documenting their scope and regression validation.
- Validate state/control schemas, safe relative paths, original metadata/checksums, unique active members and conflicts between active and retired objects before retention or recovery. Reject traversal, root paths, NUL/newline filenames and unsupported source symlinks/special files; reserve `.sambinha` at the source root.
- Write state atomically and protect single-writer operations with a lock. Use private metadata/log directories (0700) and new state/control/log/lock files (0600), independent of permissive umask. Reject metadata-directory symlink redirection and symlink locks.
- Keep AWS/Cronitor secrets outside versioned files. Parse optional private dotenv input as data, never shell commands; inherited environment values take priority. Missing/malformed input must fail without exposing values.
- Redact known credential values from environment and rclone configuration, explicit Cronitor URLs, URL userinfo and password-style variables from backend errors/logs/telemetry, including multiline values before JSON serialization. Leave encrypted rclone configuration decryption to rclone.
- Invoke subprocesses without shell interpolation; use literal file lists and safe archive arguments to avoid interpreting filenames as options or wildcards.
- Reject unsafe/link-bearing archive members before restoration extraction, verify staged bytes, create output files exclusively and strip privileged permission bits. Read recovery tar metadata without arbitrary extraction.
- Preserve existing native rclone exclusion rules and freeze them per execution. Excluded files must not be uploaded or treated as deleted; their history and mixed legacy archives remain protected.
- Use automated credential scanning in CI, with redacted output and verified scanner downloads. Public examples contain only placeholders or environment references.
- Document the trust boundary: private permissions/schema checks do not authenticate a valid index modified by the same account/root; metadata-only S3 checks do not provide full archive extraction testing.

Acceptance: test malicious paths, corrupt indexes, active/history conflicts, archive/output links, permissive umask, secret-bearing backend errors and encrypted rclone configuration compatibility.

## 10. Performance improvements P1–P6 — 0.5.0

All six improvements were selected for implementation, in priority order P1 through P6. Faster execution must preserve source/destination protection, integrity validation, exclusion rules and index commit guarantees.

| Priority | Required improvement | Configuration and acceptance |
| --- | --- | --- |
| P1 | Cache fingerprints of unchanged files. | `fingerprint_cache = true`; reuse requires equal device, inode, size, nanosecond mtime/ctime and permissions. `fingerprint_full_scan_days = 7` forces periodic full content reads; `--no-fingerprint-cache` disables reuse. A same-size edit with restored mtime must invalidate the cache through ctime. |
| P2 | Group simple transfers into bounded rclone operations. | `transfer_group_size = 100`, `transfer_workers = 4`; use literal lists, save history first and preserve checksum/newer-destination/source-consistency checks. Confirm fewer process calls and exact recovered bytes. |
| P3 | Share equivalent source inventories across independent jobs. | The same source, frozen selection and compatible full-scan requirement must share one scan in an invocation, including across different destinations. Different selections/scan requirements remain independent. |
| P4 | Compress and upload batches with limited concurrency. | `batch_workers = 2`; each worker owns staging and subprocess reports. Commit only after all required uploads succeed. Test actual concurrent execution and the configured upper bound. |
| P5 | Reuse filter results within an execution. | Cache immutable results for identical historical-path selections; continue using rclone's matcher and frozen rules. Do not carry stale filter results into later runs. |
| P6 | Configure compression effort and threads by file type. | Separate per-job text/video/photo/other levels and thread counts, as defined below. Preserve intact existing archives; new settings apply when creating/rebuilding archives. |

The fingerprint cache assumes trustworthy filesystem metadata; periodic full scans remain necessary. Worker/thread choices affect CPU, memory and staging space. Validate reductions in reads/process calls and bounded concurrency; do not promise a fixed production speedup without workload measurements.

## 11. Separate text, video, photo and other compression profiles

| Category | Typical files | Level setting/default | Thread setting/default |
| --- | --- | --- | --- |
| Text | `.md`, `.txt`, structured text/source code and extensionless README/LICENSE | `compression_text_level = 3` | `compression_text_threads = 2` |
| Video | `.mp4`, `.mkv`, `.mov`, `.avi`, `.webm` | `compression_video_level = 0` | `compression_video_threads = 1` |
| Photo | `.jpg`, `.jpeg`, `.png`, `.webp`, `.heic`, `.tiff`, `.raw`, `.dng` | `compression_photo_level = 1` | `compression_photo_threads = 1` |
| Other | Names/extensions outside the built-in categories | `compression_other_level = 3` | `compression_other_threads = 2` |

- Classify case-insensitive extensions and known extensionless documentation names; do not assume content inspection.
- Allow levels 0–9 and threads 1–64 per archive. Level 0 packages without compression; higher levels spend more effort. Actual thread use depends on the compressor's format/algorithm.
- Keep the within-directory oldest-first processing order. A new category boundary starts a new batch and may create singletons; compatible intact legacy archives remain intact even if they mix categories.
- Document the balance between compression time, uploaded bytes and storage cost, especially for already compressed media. Allow each category's settings to be adjusted independently.

## 12. Bilingual documentation — 0.6.0

- Keep maintained Markdown files, README, requirements, release/security/usage/performance/test guides and other human-readable documentation in English by default, with equivalent Brazilian Portuguese versions.
- Provide reciprocal language links and keep public commented configuration examples in both languages with equivalent effective settings.
- Keep code/configuration identifiers, dependency lists and backup data fixtures stable; do not translate them into different machine interfaces or data copies.
- Update both languages together whenever requirements or documented behavior changes, including this document and its legacy `backuper` mirrors.

Acceptance: every maintained guide must have a matching language companion with reciprocal links; both versions of each public example must parse to the same settings.

## 13. Testing, delivery and scope

- Maintain unit tests for principal domains and integration tests for all local/S3 and simple/batch combinations, including restored paths, size, SHA-256 and exact bytes.
- Keep small versioned fixtures and use established emulators such as Moto instead of real AWS/Cronitor services. Exercise real rclone/7-Zip and localhost HTTP where supported.
- Cover oldest-first/reuse behavior, restore and S3 no-download guarantees, removal/disk thresholds, security regressions, P1–P6 behavior and language/example equivalence. CI must run credential scanning and code coverage checks, with at least 85% branch-inclusive total coverage.
- Deliver versioned source and installable wheel artifacts with SHA-256 checksums. Keep the README, examples, release metadata and test report consistent with the delivered version.
- The selected new functionalities are 1 (restore), 2 (verification), 3 (removal protection) and 5 (summary). Proposed functionality 4, client-side encryption before upload, was not selected and is outside this change set. This does not disable existing backend/configuration encryption support.
- Updating these requirements consolidates the delivered scope; it does not itself change application code/version or rewrite published release artifacts.

## 14. Metadata verification and terminal progress — 0.6.1

- Add `[backup].verification_mode = "simple"|"hash"` and `--verification-mode`. Default to `"simple"`, including omitted settings in existing configurations.
- Simple mode compares size in bytes and nanosecond modification time. Creating its inventory must not read file contents or request destination hashes. Changes preserving both attributes may intentionally go undetected.
- Hash mode retains SHA-256/MD5, validated cache reuse, periodic full scans and errors for missing required destination hashes. Switching from simple must calculate missing hashes; it must not reuse stale/absent hashes. Switching modes may rebuild batches whose indexed metadata changes.
- Keep archive/control checksums and the newer-destination, retention, removal, source-race and path protections. Recovery checks sizes and recorded hashes when available; simple copy verification follows the selected size/time or hash mode. S3 verification never downloads bodies.
- Interactive execution shows stage, directory and estimated percentage only on terminal stderr. Inventory counters use files/bytes; discovery and opaque subprocesses may estimate. Do not add progress to JSON logs or stdout, and disable it when stderr is not a terminal.
- Update both READMEs and examples, test mode precision/switching, metadata-only reads and terminal/log separation, rerun all tests, and publish version 0.6.1 to GitHub only after successful checks.

## 15. Overall progress, quiet terminal and exclusions — 0.6.2

- Estimate completion for the entire backup, combining preflight, byte-weighted jobs and concurrent batches. Do not restart or decrease percentages when stages/directories/jobs change. Only successful completion may show 100%.
- Show the actual working subdirectory during scanning and copies. Keep bulk copy groups within a directory. Omit per-file events from the interactive terminal while retaining the complete JSON audit.
- Document exclusion-file setup and native patterns in both READMEs. Include a path in both public TOML examples and ship matching UTF-8 templates without active exclusions. Configure the user's `~/sambinha/sambinha.toml` to use `./excludes.txt`, creating the initially inactive file.
- Test monotonic global progress, out-of-order concurrent completions, multiple jobs, subdirectory display and terminal/audit separation. Rerun all tests and publish the 0.6.2 release with source/wheel artifacts.

## 16. Audit-log retention and release consolidation — 0.7.0

- Add `log_retention_days = 14` in `[backup]`, both public TOML examples and the live configuration; `0` disables cleanup and `--log-retention-days` overrides one invocation.
- Normal backup, restore and verification invocations remove only inactive, recognized regular Sambinha `.jsonl` logs older than the configured last-write age. Preserve the exact boundary, current/open logs, symlinks, subdirectories and unrelated files. Dry runs never remove logs.
- Put the overall estimated backup percentage at the beginning of each progress line, followed by stage and working subdirectory.
- Release version 0.7.0 with all post-0.6.2 commits, refreshed bilingual metadata/test reports and source/wheel/checksum artifacts; confirm main and the release tag are pushed to GitHub.

## 17. Requested release milestones

| Milestone | Requested scope |
| --- | --- |
| 0.3.1 | Oldest-first grouping within directories and minimal changes to existing batches; code, README and tests. |
| 0.4.0 | Selected functionality 1/2/3/5: restore, periodic verification, removal protection and summaries; S3 verification without downloads; occupancy errors above 95%. |
| 0.4.1 | Critical security improvements. The earlier proposed target 0.3.2 was superseded by this later version instruction. |
| 0.5.0 | All performance improvements P1–P6, with separate text/video/photo/other compression settings. |
| 0.6.0 | English-default documentation with Brazilian Portuguese companions. The published 0.6.0 release contains the accumulated changes above. |
| 0.6.1 | Default size/mtime verification with optional hash mode; terminal-only progress, bilingual documentation and full test rerun. |
| 0.6.2 | Overall backup percentage, quiet terminal with working subdirectories, documented/configured exclusion files and release artifacts. |
| 0.7.0 | Configurable 14-day log retention and percentage-first progress; consolidate and publish all changes since 0.6.2. |

These milestones record the requested scope and version decisions; they do not imply that every intermediate version was separately published. Operational detail is in the [usage guide](usage.md), [performance guide](performance.md), [S3 guide](s3.md), [security notes](../SECURITY.md) and [README](../README.md).
