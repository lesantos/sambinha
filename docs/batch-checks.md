# Batch verification, metadata mirrors and integrity tests

[Português do Brasil](batch-checks.pt-BR.md)

These activities apply to `logic = "batch"` and are independent of archive creation and restoration. Simple-job `verify-local-metadata` continues using `rclone check`.

| Activity | When to run | Reads |
| --- | --- | --- |
| `sambinha verify-local-metadata --job documents-s3` | After backups when due (`verify_interval_days`) | Source size/time and local JSON controls |
| `sambinha verify-destination-metadata --job documents-s3` | On demand, not routine (see below) | Local/destination JSON controls and archive size metadata |
| `sambinha verify-destination-integrity --job documents-s3` | Manually, when needed | Archive listing and compressed contents through 7-Zip |

## Daily verification

`verify-local-metadata` compares selected source paths, sizes and modification times with committed local controls. It reports `new_files`, `modified_files`, `removed_files`, `excluded_files`, `batches_to_update`, `new_batches` and `needs_update`. It does not read archive contents, calculate MD5/SHA or contact S3. Source mounts and exclusion rules remain respected; an unavailable source causes an error instead of an empty inventory.

A successful check returns 0 even when updates are needed; inspect `needs_update` in the `verification_complete` summary. Actual errors return a nonzero status. Run `backup` to perform the reported updates: verification itself never uploads or removes data. Equal size/time is deliberately considered unchanged, even if bytes changed. `verification_mode = "hash"` still controls backup inventories, not daily batch verification.

`verify_interval_days` optionally runs this local check after successful batch backups when due. `0` disables it; `1` allows a daily post-backup check. This is not a background scheduler. Use your scheduler to run the three commands independently at the desired times; integrity testing is never implicitly included in verification.

## Configurable local JSON mirrors

```toml
[backup]
state_dir = "./state"
log_dir = "./logs"
metadata_dir = "./metadata"
```

A control for `folder/batch-abc.7z` is mirrored at `metadata/JOB/folder/batch-abc.7z.json`. A local `.index.json` catalog records the active keys and job identity, allowing daily checks to use the mirrors when the backup state is unavailable. With `metadata_dir` omitted/empty, the existing `state/JOB/controls` location remains supported. Cache paths must be separate from sources, backups, restore outputs and logs.

Existing committed local state seeds a new mirror location without S3 reads. Daily checks do not rewrite unchanged controls or the unchanged catalog. Changing the mirror directory also migrates available retired controls from the previous default location. The backup remains responsible for updating controls when it changes batches and removing expired controls during retention.

## Publication and recovery from failures

Updating a batch uploads its archive and JSON control and atomically writes the identical JSON locally. Only after all batch uploads and both control copies succeed does backup publish its recovery index and commit the new local active state/catalog. Unchanged batches do not cause their remote JSONs to be rewritten.

Local filesystems and S3 cannot participate in one distributed transaction. A failed step returns an error and leaves the previous committed generation authoritative; incomplete new uploads can remain unreferenced. Retry backup to finish publication. Local JSON writes and catalog updates use atomic replacement. `verify-destination-metadata` reports missing/different replicas; it does not overwrite remote JSONs.

## What a backup already reads from S3

A routine backup trusts the local state and mirrors for everything about the source, and never downloads archives. For an S3 destination it still makes a few small requests: it reads the one small object that records the latest published generation (to notice a state older than the one published, for example after restoring the state folder from a backup), it lists the whole bucket once (about one request per thousand objects, a few seconds for several thousand batches) to confirm that every planned batch still exists there with the recorded size, and for each batch it uploads it asks the destination for the checksum that S3 already stores, instead of reading the file again. The listing is also what makes the next run notice an archive that disappeared from the bucket.

## S3 classes and destination metadata checks

Each remote `ARCHIVE.json` control and `.sambinha/control.tar.gz` recovery index is uploaded in **STANDARD**, independently of the archive's configured storage class, including GLACIER or DEEP_ARCHIVE. One bucket can hold objects in different classes; no extra bucket is required. The program does not manage bucket lifecycle rules: keep these metadata objects outside cold-storage transitions. The archives already receive their configured class at upload. See [AWS storage classes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/storage-class-intro.html) and [lifecycle filters](https://docs.aws.amazon.com/AmazonS3/latest/userguide/intro-lifecycle-filters.html).

`verify-destination-metadata` checks the known active, excluded and retained controls. It compares complete JSON values between local and destination copies, without calculating content hashes. It also checks the archive's stored compressed size through metadata. S3 archive bodies are never downloaded and no Glacier restore is requested. Only small JSON files are downloaded; if local state and catalog are both missing, the immediately accessible recovery index can bootstrap the mirrors. It makes about two requests per batch (a metadata request for the archive and a download of its JSON), thousands of requests for a large backup, many times more than the listing of a routine backup. They can incur normal S3 request charges.

Because every backup already confirms that each archive is present with the recorded size, the only thing this check adds is that the remote JSON controls match the local ones. That matters when the local state is lost and the recovery depends on the remote controls. It is not meant to run on a schedule: use it after losing or restoring the local state, when moving the backup to another machine, after an upgrade that changes the state format, or as an occasional safeguard. With a single machine and nobody else writing to the bucket it is rarely needed.

This establishes metadata agreement and archive presence/size. It does not prove that the stored compressed contents remain intact; that is the separate integrity activity. Legacy controls without an uploaded archive size cannot support the size check and cause an explicit error.

## Manual integrity tests and timestamp precision

`verify-destination-integrity` runs `7z l -slt` to compare member paths, sizes and dates, followed by `7z t` to test the compressed stream's integrity. It does not compare MD5/SHA or write extracted files. Local archives are read in place. S3 archives are downloaded and deleted **one at a time**; use `TMPDIR` on a suitably sized mounted disk when the local temporary disk is too small. GLACIER/DEEP_ARCHIVE archives must first be made readable using AWS restore; this command never initiates that restore automatically.

Testing can read and decompress the whole archive internally and use CPU/RAM, even though extracted files are not written to disk. Source files need not still exist. The test helps after moving backups, suspected storage corruption, or before relying on an archive for recovery.

Archive listings and JSON timestamps are compared in **UTC**, independently of the machine timezone and historical daylight saving rules. Only the listing subprocess receives `TZ=UTC`; the configured environment and other commands retain their timezone.

Local JSON controls preserve the source's integer `mtime_ns`. Archive timestamps can have less precision: 7z and ZIP/NTFS retain **100 ns**, so `123456789` fractional nanoseconds can become `123456700`. Legacy ZIP entries can retain only whole seconds or DOS two-second timestamps. The archive-listing comparison accepts truncation at that format's precision. This accepted loss does not reduce the precision of daily comparisons against the original JSON metadata.

## Restoration disk usage

Batch restoration downloads, extracts and stages inside the `--output` directory, including when that directory is itself a mounted volume. Each compressed archive and unselected extracted members are removed before the next batch. Selected files stay on that disk until the entire selection has passed restoration validation, then are published without overwriting. Restoration still validates recorded archive/file checksums; the lighter verification policy does not remove these recovery protections.

Reserve space on the output disk for restored files plus one compressed/extracted batch. Filesystems without hard links can temporarily need one additional file during publication. The machine's default temporary disk is not used for restoring batch data. Ordinary logs and backup state retain their configured locations.


## Batches and recovery in 0.13.0

See [stable batches, uniform compression, final Cronitor alerts, manual rebalance and generation recovery](batch-lifecycle.md) for current behavior and migration from 0.12.0.
