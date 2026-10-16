# Stable batches, recovery generations and manual rebalance (0.13.0)

[Português do Brasil](batch-lifecycle.pt-BR.md) · [Configuration](configuration.md)

## Configuration and migration

```toml
[[jobs]]
name = "documents-s3"
source = "/mnt/source/documents"
destination = "aws:bucket/documents"
type = "s3"
logic = "batch"
recovery_index_versions = 3
max_megabytes = 1024
compression_level = 3
compression_threads = 2
individual_batch_percent = 100
batch_size_alert_percent = 200
```

`max_megabytes` limits the sum of original member sizes in MB (1 MB = 1,048,576 bytes), not the compressed .7z size. The default is 1024 MB (1 GiB). Positive finite integers and decimals equivalent to at least 1 byte are accepted; fractional bytes are truncated during internal conversion. There is no member-count limit. Compression uses a single level (0–9, default 3) and thread count (1–64, default 2), regardless of extension. New settings apply to newly created/rebuilt archives; intact archives are reused.

Replace `max_bytes` with `max_megabytes` in TOMLs and `--max-bytes` with `--max-megabytes` in commands, dividing the old value by 1,048,576. For example, `max_bytes = 1073741824` becomes `max_megabytes = 1024`. The old name is rejected; existing archives remain intact.

Remove `max_files` and all `compression_text_*`, `compression_video_*`, `compression_photo_*`, `compression_other_*` options from old TOML files and scheduled CLI invocations. These obsolete configuration options are rejected with parameter/line diagnostics. Replace category options with `compression_level`/`compression_threads`. Existing archive controls remain readable and their membership is preserved; migration does not automatically consolidate legacy small archives.

Confirmed state migrates to schema 2 on the first 0.13.0 publication. Legacy schema 1 remains readable; older executables reject new state rather than overwrite membership/generations they do not understand. Unsupported future schemas never silently fall back to an older writable state.

## Initial formation and new files

Inside each source directory, sort files and subdirectories together by nanosecond modification time (oldest first), breaking ties by name. On encountering a directory, process its entire subtree before continuing with its next sibling. Add regular files in that depth-first order. Source links remain ignored; exclusions are applied before inventory. The directory dates used are the actual source dates, not dates reconstructed by restore.

A file with `size * 100 >= int(max_megabytes * 1048576) * individual_batch_percent` gets a dedicated archive. The percentage must be finite, greater than 0 and at most 100; equality qualifies. Diverting an individual file leaves the normal batch open. For example, with a 100-byte limit, files of 40, 200 and 60 bytes produce one normal 100-byte batch and one individual 200-byte batch. A file is never split. Normal new batches close only when adding the next normal file would exceed the byte limit. File type and member count do not create boundaries.

If that individual file already has a recognized compressed format extension (such as ZIP, 7z, RAR, TAR.GZ, GZIP, BZIP2, XZ or Zstandard), Sambinha uploads the original file without compressing it again. Restore and integrity checks validate that file directly.

Confirmed batches are not reopened just to add new files: new files form new batches using the same algorithm. This avoids reuploading unchanged data. The initial packing minimizes sequential boundaries under this traversal, but does not solve arbitrary bin packing by reordering members.

## Stable membership and history

Each new group records an immutable logical `batch_id`, plus an `individual` role in its control. Revision filenames are content/metadata addressed. A modified member stays in its original group and its original member position even if its mtime or size changes. A group may grow beyond `max_megabytes`; lowering the limit does not automatically rebuild it. Removing a member does not move the others. An empty group leaves the active index, and its prior physical revision follows deletion/version retention. A dedicated group remains dedicated after shrinking. Legacy singleton controls are treated as individual groups.

Exactly one active control owns each selected path. Old revisions can contain the same path as active data because they are retained history; ordinary restore uses only the active selection. Exclusions continue protecting excluded history, including legacy mixed excluded batches. Changes to archive format rebuild affected archive representations while retaining group members.

Updating one member rebuilds/uploads the entire archive. Large groups therefore require proportionally more transfer, staging space and time for partial restore, and concentrate more data behind one archive's integrity. Use the actual group size when planning temporary space; `max_megabytes` is not a runtime size ceiling for existing groups.

## Deferred growth alerts

`batch_size_alert_percent` is a finite nonnegative percentage of `max_megabytes`, default 200. Zero disables it. Alert when a nonindividual active batch reaches or exceeds the threshold in original bytes. Single-file archives are excluded, regardless of size. Log one `batch_size_alert` record per matching batch and aggregate the messages across jobs.

Growth alerts never interrupt jobs or change a successful operational exit code (0). After all allowed jobs finish, send one Cronitor `fail`, including the affected jobs/batches; do not send `complete` afterward. Real operational failures still return nonzero and include growth alerts in the final failure message. Notification delivery errors are operational failures. Dry runs may log projected alerts but send no telemetry. Existing imminent-deletion alerts keep their separate before-purge behavior.

## Manual rebalance

```bash
sambinha rebalance --config sambinha.toml --job documents-s3 --dry-run
sambinha rebalance --config sambinha.toml --job documents-s3
```

Only batch jobs are accepted. Rebalance reads the confirmed backup, not current source contents, and works without the source/mount. Every nonindividual group with more than one member above `max_megabytes` must be replaced; every already conforming group and every individual archive is preserved byte-for-byte. This is the minimum possible number of existing active archives that must change to enforce the bound. It does not merge healthy small groups or rebalance automatically.

Pool only members of affected groups in their recorded group/member order, divert individual files using the configured percentage, and pack remaining members by bytes. An archive containing an individually oversized member can therefore be split into a dedicated archive and normal groups. Archive overhead is outside the original-byte limit. New generations/retention are published through the ordinary safe publication workflow; old archives remain until retention permits deletion.

Only affected archives are downloaded/extracted, with content validation. GLACIER/DEEP_ARCHIVE archives must first be made readable through AWS restore; rebalance never initiates it. Stage their members, then rebuild the new archives. Disk/temporary failures abort publication. Dry runs list planned changes without extracting archives, changing backup/state or sending telemetry. `rebalance_summary` records affected/preserved counts. Rebalance is not a full repack command for consolidating legacy healthy archives.

## Restore and machine loss

Restore checks the backup destination and its mounted volume, output directory and temporary capacity, without requiring the original source path, mount or disk. Preserve/recreate the job configuration and credentials on another machine; the recorded job identity must match. Output remains new/empty, separate from protected paths, with link/overwrite protection. Current simple-tree restores use rclone and need no batch index.

The active state is kept in `state_dir/JOB/state.json`. JSON controls are mirrored in `metadata_dir/JOB` (or legacy state controls) and beside each archive at the destination. Complete recovery generations contain the validated state and referenced active/excluded/retired controls.

Publishing first uploads all changed archives and their controls. A durable local `publication.json` journal is written, then `.sambinha/indexes/GENERATION.tar.gz`, then a `.commit.json` containing generation, size and SHA-256. Only confirmed generations are recovery candidates. Finally update the compatibility `.sambinha/control.tar.gz`, atomically save local state, and remove the journal. A failed compatibility-index update may leave a newer confirmed generation recoverable even though the invocation reports failure. A later backup with a journal reconciles from the destination before planning.

Before backup with missing/invalid local state, inspect the destination and recover the newest valid confirmed generation (with a valid matching compatibility copy as fallback), then older valid generations or the legacy index. Validate identity, bounded tar/JSON structure, unique member paths, marker checksum and active archive/control presence and archive sizes before adoption. Never arbitrarily extract recovery tar paths. Metadata downloads do not initiate Glacier restore; generation metadata uses STANDARD.

A truly empty destination can initialize a backup; inaccessible/missing S3 buckets are errors, not empty inventories. A durable local initialization marker makes interrupted first uploads retryable. Unowned nonempty destinations without a recoverable index fail before mutation. Corrupt metadata symlinks and identity mismatches are never silently adopted. An unavailable source is never treated as an empty source during backup.

`recovery_index_versions` retains the latest positive number of confirmed metadata generations (default 3); confirmation is removed before pruning an older generation archive. Data history still follows `deleted_days`/`version_days`: metadata generations are not independent permanent data snapshots. Generations referring to expired/missing active archives are skipped. Falling back to an older valid generation can recover an older backup point. Logs are not needed for restoration. Keep independent data/configuration copies for loss of both source and destination; metadata alone cannot reconstruct missing content.

## Verification and operation

Use `verify-local-metadata` for source size/time against local controls, `verify-destination-metadata` for metadata replicas and archive sizes, and `verify-destination-integrity` for archive listing/testing. None automatically rebalances growth. Local verification does not mark a group dirty just because its size limit changed. Monitor final alerts, test restores and run rebalance explicitly when desired.

The producer enforces the same limits as the reader before confirming a generation: 64 MiB per state.json/control and 256 MiB total referenced controls. Exceeding a limit fails before index publication, preserving the previous confirmed point. These are recovery-format metadata bounds, not a packing member-count cap. Very large jobs may need separate source jobs or disabled persistent fingerprint caching. Rebalance freezes exclusions and continues protecting excluded history from expiry, even without the source.
