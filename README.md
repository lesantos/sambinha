# Sambinha

[Português do Brasil](README.pt-BR.md)

Sambinha backs up your files to a local folder, a mounted disk or Amazon S3. It copies changes, keeps previous versions for a configurable period, and records what happened. The current version is **0.6.0**.

## How it protects your files

**Simple backups** keep the original names and directory tree. **Batch backups** use 7-Zip to package files into immutable archives, reducing the number of objects stored in S3. Each archive has a JSON control file listing its original files and metadata. Both strategies support local and S3 destinations, and one source can have multiple independent backup jobs.

New batches visit directories deterministically and sort files within each directory by modification time, oldest first, then by path for ties. New batches separate text, video, photo and other compression profiles without changing that time order. Existing intact batches keep their keys and member order, including archives from older versions. Only new files and members of affected or incompatible batches are repacked. Preserve the local index so intact batches can be reused without remote listings or uploads.

Files newer at the destination are preserved: the job reports an error before replacing them, and Cronitor receives the conflict details. Batch comparisons use original timestamps from the confirmed index, rather than archive upload times. Independent jobs continue after a job failure.

Deleted source files remain in history for **10 days** by default; previous versions of edited files remain for **2 days**. Expired history is removed during later backup runs. The soft `deletion_alert_threshold` warning reports upcoming permanent deletions without delaying retention.

A separate safeguard blocks abnormal source removals **before any destination changes**: by default, more than 50% of a previously selected inventory of at least 20 files. Intentional removals can be authorized for one invocation with `--allow-mass-deletion`.

Source and local destination disks are checked before the backup and before transfers/deletions. Occupancy **above 95%** produces an error; exactly 95% is accepted. Batch temporary storage is also checked. S3 buckets do not expose a local disk occupancy percentage.

Metadata and logs use private permissions. Relative paths and indexes are validated before retention or recovery. Known secrets are redacted from errors, logs and telemetry. Retries are bounded: one attempt and up to three retries, two seconds apart.

## Install

Use Linux and **Python 3.12 or newer**, with rclone and 7-Zip. Backups run under your normal account.

```bash
sudo apt install git python3 python3-venv rclone p7zip-full
git clone https://github.com/lesantos/sambinha.git
cd sambinha
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/sambinha --version
```

The repository is private, so your GitHub account needs access. Installable artifacts are available from the [releases page](https://github.com/lesantos/sambinha/releases). From a source checkout, `.venv/bin/python -m sambinha` also works.

## First backup

Create `sambinha.toml`, with paths relative to the configuration file. `~` and environment variables are supported.

```toml
[backup]
state_dir = "./state"
log_dir = "./logs"
deleted_days = 10
version_days = 2
verify_interval_days = 7

[[jobs]]
name = "documents"
source = "~/Documents"
destination = "/mnt/backup/documents"
type = "local"
logic = "simple"
```

Keep the destination outside the source. Use a destination managed exclusively by this job, and preserve `state_dir`. State and logs must not overlap the source, destination or each other. Connect and mount external disks before starting; optional `source_mount` and `destination_mount` checks can pin the expected nonempty volumes.

```bash
.venv/bin/sambinha --config sambinha.toml --dry-run
.venv/bin/sambinha --config sambinha.toml
```

Simulation writes a report and reads inventories, but creates no backup state or archives, transfers or deletes no backup objects, and sends no Cronitor notifications. Each job ends with a `summary` record containing new, modified, unchanged and removed files, batches reused/rebuilt, planned original bytes, transferred payload bytes, pending history entries and success. Compression can make transferred bytes differ from planned bytes. Empty source directories are preserved by local simple backups.

## Restore a file, folder or complete backup

```bash
.venv/bin/sambinha restore --job documents --path projects --output /mnt/recovery/documents
.venv/bin/sambinha restore --job documents --path notes.md --output /mnt/recovery/notes
.venv/bin/sambinha restore --job documents --output /mnt/recovery/all
```

`--path` can be repeated. Paths refer to the active index and preserve their original directory tree. The output must be new or empty and separate from sources, backups, state and logs. Selected files are staged and their sizes, SHA-256 and MD5 verified before publication; restore does not overwrite existing files or restore privileged permission bits. Source files need not still exist.

Restoration from S3 **downloads the selected objects** and can incur retrieval charges. Selecting one file in a batch downloads its entire archive. Glacier/Deep Archive objects must first be made available through AWS restore. Batch restoration can recover a missing local index from `.sambinha/control.tar.gz`; simple restoration needs its local index. Historical revisions remain accessible through the controls and history paths described in the [usage guide](docs/usage.md).

## Verify integrity without S3 downloads

```bash
.venv/bin/sambinha verify --job documents
```

Local verification reads the backup contents and, for batches, tests/extracts archives and checks the indexed original files. S3 verification uses **only metadata requests**, comparing object size and MD5 against the local index. Batch archives and their JSON controls are checked. **No S3 object bodies or recovery indexes are downloaded**, including when verification fails. Metadata requests can still incur API charges. This verifies recorded object checksums, not extraction of archived members.

New batches record their compressed size and checksum at upload. Old batches lacking these fields, missing local indexes or unavailable remote hashes cause an explicit verification error, with no download fallback. Intact legacy archives are preserved rather than automatically rewritten. Keep rclone's S3 checksum metadata enabled for multipart uploads; see the [S3 guide](docs/s3.md).

Set `[backup].verify_interval_days = 7` to verify after a successful backup when seven days have elapsed. The first eligible run verifies immediately. `0`, the default for compatibility, disables scheduled checks. This schedule runs during backups; it does not install a system timer. A failed check returns an error and keeps the last successful verification timestamp.

## Performance settings and separate file profiles

All six performance improvements are implemented: fingerprint caching with periodic full scans; bounded bulk rclone copies; shared inventories for equivalent source/selection jobs; limited concurrent batch compression/uploads; per-run filter caching; and per-category compression levels and threads.

```toml
[backup]
fingerprint_cache = true
fingerprint_full_scan_days = 7
transfer_group_size = 100
transfer_workers = 4
batch_workers = 2

[[jobs]]
name = "media"
source = "~/Files"
destination = "/mnt/backup/media"
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

Cache reuse requires unchanged device, inode, size, nanosecond modification/change times and permissions. A full content scan runs every seven days by default; `--no-fingerprint-cache` forces hashing for each run. Transfer and archive validation still protect changed data. Level 0 stores content without compression; levels 1–9 spend progressively more effort. Threads are limited to 1–64 per archive. Existing intact archives keep their settings; the new settings apply when archives are rebuilt. [Performance guide](docs/performance.md).

## S3, credentials, exclusions and notifications

Use [sambinha.example.toml](sambinha.example.toml) for the complete configuration and [rclone.example.conf](rclone.example.conf) for the `aws` S3 remote. Create the bucket first and match its region. Each job needs a separate prefix. The default `GLACIER_IR` class provides immediate retrieval but charges for retrieval and has minimum billing terms. JSON controls and the recovery index use STANDARD. [S3 setup and storage classes](docs/s3.md).

Store credentials outside the repository in an AWS profile/IAM role or a private dotenv file:

```toml
[backup]
secrets_file = "~/.config/sambinha/secrets.env"
cronitor_url = "${CRONITOR_URL}"
```

Protect the file with `chmod 600`. [secrets.example.env](secrets.example.env) contains placeholders only. The file is parsed as data, never executed; inherited environment values take precedence. `--secrets-file ""` disables loading for one run. Missing or malformed files fail without exposing their values. Notifications are optional; with no Cronitor URL, they are disabled. Never commit a credential-bearing URL or real AWS keys.

Set `exclude_files` on a job to use rclone's native exclude-from patterns. Excluded files are not uploaded or considered removed. Existing history and mixed legacy archives containing excluded paths remain protected; mixed archives are tracked separately in `state.excluded`. Changing the filter file during a run affects the next run. [Filtering and retention details](docs/usage.md).

## Tests and documentation

```bash
.venv/bin/python -m pip install -e . -r requirements-test.txt
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m coverage run --branch -m unittest discover -v
.venv/bin/python -m coverage report --include='sambinha/*' --show-missing --fail-under=85
```

Tests use temporary files, real rclone/7-Zip and Moto's local AWS emulator. They do not require real AWS or Cronitor credentials. HTTP integration requires loopback networking. Results are recorded in [test results](docs/test-results.md); GitHub runs [continuous tests](https://github.com/lesantos/sambinha/actions).

English is the default documentation language. Every maintained Markdown guide and public commented configuration example has a `.pt-BR` counterpart. Code, configuration keys, dependency lists, backup data fixtures and license identifiers retain their stable names. See the [usage guide](docs/usage.md), [architecture](docs/architecture.md), [security notes](SECURITY.md), [changelog](CHANGELOG.md), and [configuration example](sambinha.example.toml).
