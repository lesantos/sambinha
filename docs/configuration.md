# Complete configuration and operation guide

[Português do Brasil](configuration.pt-BR.md) · [README](../README.md)

Sambinha backs up your files to a local folder, a mounted disk or Amazon S3, and records each run. Simple jobs delegate comparison, exclusions and synchronization to `rclone sync`; displaced and deleted destination files go to `--backup-dir`. Batch jobs keep the indexed archive workflow. The current version is **0.14.9**.

## How it protects your files

**Simple backups** keep the original names and directory tree. **Batch backups** use 7-Zip to package files into immutable archives, reducing the number of objects stored in S3. Each archive has a JSON control file listing its original files and metadata. Both strategies support local and S3 destinations, and one source can have multiple independent backup jobs.

New batches use depth-first traversal with files/subdirectories sorted together by mtime (oldest first, ties by name). Divert large files without closing the normal batch. There are no category or member-count boundaries. Confirmed membership stays stable through edits, growth and removals; only manual rebalance redistributes oversized groups. See [stable batches and recovery](batch-lifecycle.md).

Batch jobs report an error before replacing files newer at the destination. Simple jobs pass `--update` to rclone, which preserves destination files it considers newer. Independent jobs continue after a job failure.

For S3 batch jobs, `storage_class` applies to the `.7z` archives and `control_storage_class` applies to JSON files and recovery indexes. Controls default to `STANDARD` so the metadata needed to list and restore backups stays immediately accessible; the archive class is independently configurable per job.

Run logs contain events from the whole invocation, so cleanup uses the longest `log_retention_days` among the configured jobs. This preserves the retention window requested by every job.

Batch jobs retain deleted files for **10 days** and replaced versions for **2 days** by default. Simple jobs purge expired rclone `--backup-dir` run directories after the larger of `deleted_days` and `version_days`. Rclone does not label each saved path as a deletion or replacement, so simple history uses the longer period for every item. Cleanup lists only immediate history directories and then purges expired run directories.

Each simple job has an absolute `max_delete` limit (default 100), passed to rclone. When more destination files would be deleted, rclone stops the sync with an error. Batch jobs retain the percentage guard based on their prior index. `--allow-mass-deletion` bypasses the applicable guard for one invocation.

By default, source and local destination disks are checked before the backup and before transfers/deletions. Occupancy **above 95%** produces an error; exactly 95% is accepted. Batch temporary storage is also checked. S3 buckets do not expose a local disk occupancy percentage.

Batch creation checks estimated temporary space before starting workers and again before creating each archive. The compressor reads directly from the source and validates configured metadata/content before and after the read. Batches exceeding `max_megabytes` run one at a time. Set `[backup].temp_dir` next to `state_dir`, `log_dir` and `metadata_dir` to use another disk; one existing writable directory is shared by every job in that configuration. Without it, Python's default temporary directory is used. See [temporary space and concurrency](performance.md).

Metadata and logs use private permissions. Relative paths and indexes are validated before retention or recovery. Known secrets are redacted from errors, logs and telemetry. Retries are bounded: one attempt and up to three retries, two seconds apart.

## Install

Use Linux or macOS and **Python 3.12 or newer**, with rclone and 7-Zip. Backups run under your normal account.

**rclone 1.60 or newer** and **7-Zip 22.00 or newer** (`7zz`) are required; Sambinha refuses to run with older versions. Ubuntu 22.04 and Linux Mint 21 ship rclone 1.53 and p7zip 16.02, which are too old: install the official binaries from <https://rclone.org/downloads/> and <https://www.7-zip.org/download.html> (Linux x64 `7zz`) into `/usr/local/bin`, or on Ubuntu 24.04/Debian 13 use `sudo apt install rclone 7zip`. Then set `compressor = "7zz"` in batch jobs and check with `rclone version` and `7zz`.

```bash
sudo apt install git python3 python3-venv
git clone https://github.com/lesantos/sambinha.git
cd sambinha
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/sambinha --version
```

The repository is private, so your GitHub account needs access. Installable artifacts are available from the [releases page](https://github.com/lesantos/sambinha/releases). From a source checkout, `.venv/bin/python -m sambinha` also works.

### macOS

Install the tools with [Homebrew](https://brew.sh): `brew install git python@3.12 rclone sevenzip`. The compressor is `7zz`, so set `compressor = "7zz"` in batch jobs. Then follow the same `git clone`, `python3 -m venv .venv` and `pip install .` steps above.

Behavior that differs from Linux:

- `source_mount` and `destination_mount` are verified by comparing the device of the mount point with its parent directory, because macOS has no `mountpoint` command. External disks appear under `/Volumes/NAME`, so use `destination_mount = "/Volumes/NAME"`.
- Filesystem detection reads the `mount` table instead of `/proc/self/mountinfo`. APFS and HFS+ volumes receive full POSIX metadata (`--metadata`); FAT (`msdos`) and exFAT volumes keep the same conservative handling as on Linux. SMB, AFP and other network mounts never request extended metadata.
- APFS is case-insensitive by default. Two source files that differ only by letter case cannot coexist on such a volume; backups made on Linux that contain both will collide when restored to a default APFS volume. HFS+ also normalizes accented names to NFD; APFS preserves the original form. Keep state and destination on the same kind of volume as the source whenever possible.
- Reading `~/Documents`, `~/Desktop`, `~/Downloads`, Mail and similar folders requires **Full Disk Access** (System Settings, Privacy and Security). When backups run from `cron` or `launchd`, grant that permission to the Python interpreter of the virtual environment (or to `/usr/sbin/cron`), otherwise the inventory fails with permission errors.
- Batch jobs reject names with control characters, which includes the `Icon\r` files Finder creates in folders with custom icons. Exclude them with the `Icon[\x0d␍]` rule described below; simple jobs copy them through rclone.
- Schedule with `cron` or a `launchd` agent in `~/Library/LaunchAgents`; the command is the same `.venv/bin/sambinha backup`.

Tests marked `RealMacOSTests` in `tests/integration/test_macos.py` run only on a Mac and check these behaviors against the live system; the remaining macOS tests simulate the platform and run on Linux CI as well.

## First backup

Create `sambinha.toml`, with paths relative to the configuration file. `~` and environment variables are supported.

```toml
[backup]
state_dir = "./state"
log_dir = "./logs"

[[jobs]]
name = "documents"
source = "~/Documents"
destination = "/mnt/backup/documents"
type = "local"
logic = "simple"
log_retention_days = 14
deleted_days = 10
version_days = 2
verify_interval_days = 7
verification_mode = "simple" # Default: size in bytes and modification time.
```

Keep the destination outside the source. Destinations of different jobs cannot overlap, but an earlier job's destination may be a later job's source; review the interactions below and preserve `state_dir`. State and logs must not overlap the source, destination or each other. Connect and mount external disks before starting; optional `source_mount` and `destination_mount` checks can pin the expected nonempty volumes.

```bash
.venv/bin/sambinha --config sambinha.toml --dry-run
.venv/bin/sambinha --config sambinha.toml
```

Simulation runs `rclone sync --dry-run` for simple jobs and does not build a Sambinha source index for them. Batch jobs still read an inventory. Each job ends with a summary; simple-job file counts are reported by rclone's log. Empty source directories are preserved by simple syncs.

## Configure batch size

For each job with `logic = "batch"`, set `max_megabytes` to limit original data per batch, before compression:

```toml
max_megabytes = 1024 # 1024 MB = 1 GiB; 1 MB = 1,048,576 bytes
```

The default is 1024; decimals such as `max_megabytes = 0.5` are accepted. Override it for one run with `--max-megabytes 100`. Migrate old configurations by replacing `max_bytes` with `max_megabytes` and dividing the value by 1,048,576; `max_bytes = 1073741824` becomes `max_megabytes = 1024`. Confirmed batches retain their members even when they grow beyond the limit; see [batches and rebalance](batch-lifecycle.md).

## Jobs and parallel execution

Jobs execute sequentially in TOML order. Each job reads its exclusions and checks disks/mounts; simple jobs then let rclone traverse and synchronize the trees, while batch jobs build an inventory and commit indexed state. Concurrency settings apply inside each job. Normal backup and verification invocations acquire a nonblocking exclusive lock in `state_dir`: a second invocation using the same state directory exits with an error rather than waiting or modifying backup data. This also applies to different `--job` selections. Dry runs and restores do not acquire that writer lock. Independent state directories can run concurrently and should manage separate destinations.


## Disk checks per job

Both `check_source_disk` and `check_destination_disk` default to `true` inside each `[[jobs]]`. Set either to `false` to skip its disk-capacity checks throughout backup and verification. For a virtual source such as Seafile/FUSE that reports zero capacity, disable only the source check:

```toml
[[jobs]]
name = "seafile-data"
source = "/opt/seafile/seafile-fuse"
destination = "/mnt/backup/seafile"
type = "local"
logic = "simple"
check_source_disk = false
check_destination_disk = true
```

Set `check_destination_disk = false` to also skip local destination capacity checks. That option covers the restore output directory as well. S3 destinations have no local capacity check. Temporary space used by batch creation, restoration and archive downloads is checked; daily batch verification does not need archive staging. These options do not disable mount checks, source readability, path validation or protection against mass deletion. An actual write failure, including insufficient space, still fails the job. With checks enabled, unavailable capacity is an error, and disk errors identify the path and reason.

## Continue or stop after a job failure

`continue_on_job_error = true` is a per-job default inside each `[[jobs]]`: if that job fails, execution continues with the next independent jobs, including failures while reading exclusion rules or running preflight checks/inventories. Set it to `false` in a job that must stop the invocation when it fails. The failed job's setting controls the decision; jobs can use different policies:

```toml
[[jobs]]
name = "critical"
source = "~/Documents"
destination = "/mnt/backup/critical"
continue_on_job_error = false

[[jobs]]
name = "optional"
source = "~/Photos"
destination = "/mnt/backup/optional"
continue_on_job_error = true
```

This applies to backup and manual verification. Preflight belongs to each job. A failure in a job with `false`, including its preflight, stops subsequent jobs without undoing completed work. Jobs with `true` can fail and be skipped while the remaining valid jobs run. Either policy returns a nonzero exit code if any job fails. Shared failures, such as an unavailable rclone executable or state lock, stop the invocation regardless of this setting. Errors include `Job NAME:` in their message and a `job` field in JSON logs, including preflight failures; job names also appear in failure notifications.

## Log retention

each job’s `log_file_level` and `log_terminal_level` independently select the JSON events written to the file and printed to stdout. Both accept `"error"` (errors only), `"summary"` (errors, alerts, per-job summaries, and the final result), and `"detailed"` (all events, including per-file events). Both default to `"summary"`. Simple sync asks rclone for only `ERROR` output by default; if either output is `detailed`, it asks for `DEBUG` and forwards those records to the detailed output. Detailed logs can be large. Interactive stderr progress still receives only the periodic statistics it needs. With rclone 1.60, notices about newer destination files protected by `--update` appear only at `DEBUG`; the default therefore preserves those files without failing the job, while `detailed` identifies them and fails the job for review. Override the levels for one run with `--log-file-level` and `--log-terminal-level`.

```toml
[[jobs]]
name = "documents"
source = "~/Documents"
destination = "/mnt/backup/documents"
log_file_level = "summary"
log_terminal_level = "summary"
```

`log_retention_days = 14` inside each job keeps Sambinha audit logs written within the last 14 days. Set a different number of days, or `0` to keep logs indefinitely. `--log-retention-days 30` overrides the setting for one invocation. Non-simulated backup, restore and verification runs remove only inactive regular log files matching Sambinha's timestamp/UUID `.jsonl` naming format in `log_dir`. The age is measured from the last write, not the filename date. The current log, logs held open by other invocations, symlinks, subdirectories and unrelated files are preserved. Dry runs do not remove logs. No separate cleanup timer is installed.

```toml
[[jobs]]
name = "documents"
source = "~/Documents"
destination = "/mnt/backup/documents"
log_retention_days = 14
```

## Choose simple or hash verification

**`verification_mode = "simple"` is the default**, including when the option is omitted from an existing configuration. Batch inventories compare size and modification time without reading contents. Simple jobs use rclone size/time comparison flags; `verification_mode = "hash"` makes their sync use `--checksum`.

To compare contents using SHA-256 and MD5, configure:

```toml
[[jobs]]
name = "documents"
source = "~/Documents"
destination = "/mnt/backup/documents"
verification_mode = "hash"
```

Or select a mode for one invocation:

```bash
.venv/bin/sambinha --config sambinha.toml --verification-mode simple
.venv/bin/sambinha --config sambinha.toml --verification-mode hash
```

For batch inventories, simple mode cannot detect a content edit that preserves size and modification time. Hash mode reads contents on the first inventory and periodically thereafter. Switching batch modes can rebuild archives because their indexed metadata changes. `logic = "simple"`/`"batch"` selects the storage strategy independently of `verification_mode`. Archives and recovery controls retain checksums in both modes.

Batch jobs check that source files remain stable while they are inspected and compressed. Set `verify_source_changes = false` inside a job to skip these consistency checks and let later backups pick up subsequent changes. With the default `true`, a changing file is retried up to three times after its first read; persistent changes fail the backup. With `false` and `verification_mode = "simple"`, the inventory takes each file's size, date and mode from the directory listing without opening the file. This setting does not change how simple jobs use `rclone sync`.

On a slow network share, `inventory_from_listing = true` (job option, off by default) skips the inventory's own pass over the tree: the inventory is built from the size and modification date that the `exclude_files` discovery (rclone) already lists, with no `stat` per file. It requires `logic = "batch"`, `verification_mode = "simple"`, `verify_source_changes = false` and `exclude_files`, and the configuration is rejected otherwise. Order, sizes and dates are identical to the normal walk, so batches and the saved state stay the same. Links and special files (FIFOs, sockets, devices) are not listed by rclone and are never backed up; a file that changes after it was listed is picked up by the next backup. The listing carries no permissions, so every file is recorded with mode `0o755` (what such shares usually report anyway). To choose the permissions of restored files, set `file_mode` (for example `file_mode = 0o644`); without it, restore applies the recorded mode.

During interactive execution, stderr displays one percentage for the entire backup at the start of the line, followed by the current stage and working subdirectory. The estimate assigns equal ranges to the jobs and reserves 5% for finalization. Each job uses 20% of its range for preflight/inventory, 70% for transfers/retention and 10% for verification/completion. File/byte counters and directory queues advance within those ranges. Bulk rclone copies use the transfer percentage reported by rclone; other opaque subprocess operations use bounded elapsed-time estimates. Concurrent batches contribute by their byte weights. The percentage never decreases or restarts when changing directory, stage or job, and reaches 100% only after successful completion. Bulk copies are grouped by directory so progress names the directory being copied. Per-file events appear in JSONL or stdout only when their respective log level is `detailed`; run summaries and errors remain visible by default. Progress never enters JSON logs or stdout and is disabled when stderr is not a terminal.

## Restore a file, folder or complete backup

```bash
.venv/bin/sambinha restore --job documents --path projects --output /mnt/recovery/documents
.venv/bin/sambinha restore --job documents --path notes.md --output /mnt/recovery/notes
.venv/bin/sambinha restore --job documents --output /mnt/recovery/all
```

`--path` can be repeated for simple or batch jobs. Simple restore uses rclone to select current destination files/directories; it does not restore historical versions. The output must be new or empty and separate from sources, backups, state and logs. Batch files are staged and checked before publication. Restore does not overwrite existing files. Source files need not still exist.

Batch restoration downloads and extracts into a temporary directory inside `--output`, using the restoration disk instead of the machine's default temporary disk. Each archive and its unselected extracted members are removed before processing the next batch. Selected files stay on the output disk until all requested content has passed validation. Publication uses hard links where supported; otherwise it copies and removes each staged file in turn. Allow space for the restored selection plus one compressed/extracted batch, and one additional file during publication on filesystems without hard links. Temporary-space checks also measure the restoration disk. A dry run does not create the output directory.

Restoration from S3 **downloads the selected objects** and can incur retrieval charges. Selecting one file in a batch downloads its entire archive. Glacier/Deep Archive objects must first be made available through AWS restore. Batch restoration can recover a missing local index from `.sambinha/control.tar.gz`. Simple jobs do not maintain a Sambinha index; use rclone to copy a historical version from `.sambinha/history/<run-id>`.

## Independent batch checks

```bash
sambinha verify-local-metadata --job documents-s3         # Daily source size/time against local JSONs
sambinha verify-destination-metadata --job documents-s3   # On demand: remote JSON controls vs local (not routine)
sambinha verify-destination-integrity --job documents-s3                # Manual 7-Zip integrity test
```

For batch jobs, `verify-local-metadata` uses the current source and local JSON mirrors, without reading archives, calculating hashes or contacting S3. It reports `needs_update` and new/changed/deleted files and affected batches; run `backup` to apply updates. A successful check returns 0 even when updates are needed. `metadata_dir = "./metadata"` keeps mirrors beside state/logs; an omitted/empty setting preserves `state/JOB/controls`. Unchanged JSONs are not re-uploaded.

`verify-destination-metadata` compares the complete known local/destination JSONs and archive size metadata. Remote controls and the recovery index are uploaded in STANDARD regardless of archive class. Only these metadata files are downloaded, never archive bodies. A missing local state/catalog can be rebuilt from the recovery index. Bucket lifecycle rules must keep metadata immediately accessible.

`verify-destination-integrity` separately compares archive listings and runs `7z t`, without MD5/SHA comparisons or extracted files on disk. Local archives are read in place; S3 archives are downloaded/deleted one at a time and Glacier/Deep Archive need prior AWS restore. 7z/ZIP-NTFS dates retain 100 ns; legacy ZIP dates can retain one/two seconds. This accepted precision loss is accounted for in archive comparisons; JSONs preserve the original `mtime_ns`.

`verify_interval_days` optionally schedules the lightweight local check after successful batch backups; 0 disables it. Integrity testing is manual. Simple `verify-local-metadata` continues using `rclone check`. See [batch activities and publication guarantees](batch-checks.md) for cache migration, scheduling, failure recovery and disk requirements.

## Performance settings and separate file profiles

Performance settings provide fingerprint caching for batch inventories, native simple-job syncs with bounded rclone transfers, limited concurrent batch compression/uploads and per-tier compression levels (see below) and a thread count. Simple jobs do not build a separate recursive inventory.

```toml
[[jobs]]
name = "media"
source = "~/Files"
destination = "/mnt/backup/media"
logic = "batch"
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

### Three-tier compression (0.18.0)

Each batch archive is compressed in up to three tiers, chosen by the file's **last extension** (case-insensitive; `a.tar.gz` -> `gz`; `.bashrc`, `Makefile` and `name.` have none; dots in directory names are ignored):

| Tier | Files | Level option | Default |
|---|---|---|---|
| text | extensions in `compression_text_extensions` | `compression_text_level` | 1 |
| other | every extension in neither list | `compression_other_level` | 1 |
| already compressed | extensions in `compression_compressed_extensions` | `compression_compressed_level` | 0 (store) |

Levels are integers 0-9 (booleans rejected); `compression_threads` (1-64) applies to every command. Both extension lists are TOML arrays of bare extensions (no dot, spaces or separators; case and one leading dot are ignored). Duplicates inside a list and an extension in both lists are rejected. The keyword `"<none>"` stands for files without an extension; it may be in at most one list (default: text); in neither, such files use the "other" tier. The defaults (about 230 text and 110 compressed extensions, defined in `sambinha/extensions.py`) are listed in full in `sambinha.example.toml`; setting a list replaces the default, it does not extend it. The levels can also be overridden on the command line (`--compression-text-level`, `--compression-other-level`, `--compression-compressed-level`); the lists are TOML-only. These options are job-level only, like the other batch settings.

`compression_level` was **removed in 0.18.0** (no alias): a TOML that still sets it is rejected with the parameter, its line and the replacement. Use `compression_other_level` (same meaning for unlisted extensions; the default changed from 3 to 1). Existing archives are unaffected; new settings apply to newly created or rebuilt archives.

Fingerprint settings apply to batch inventories. `transfer_workers` controls concurrent rclone transfers in simple jobs. `max_delete` is an absolute per-job cap; `--allow-mass-deletion` bypasses it for one run. Level 0 stores content without compression; levels 1–9 spend progressively more effort. Threads are limited to 1–64 per archive. Existing intact archives keep their settings; the new settings apply when archives are rebuilt. [Performance guide](performance.md).

### Optional rclone tuning for S3 (0.17.0)

Three per-job settings, all optional and ignored for local destinations (never passed to rclone there):

- `fast_list = true` (default `false`): passes `--fast-list` to rclone `sync`, `check` and `lsjson` of **simple** jobs on S3. Fewer listing requests on large buckets, at the cost of memory proportional to the number of objects. No effect on batch jobs.
- `s3_chunk_size = "64M"`: passes `--s3-chunk-size` on uploads (batch archives/controls and simple sync). Format: digits plus `K`, `M` or `G` (optionally `Ki`, `Mi`, `Gi`); S3 requires at least 5 MiB per chunk. Larger chunks help multi-GB archives and use more memory per concurrent transfer (chunk size x upload concurrency x transfers).
- `s3_upload_concurrency = 8`: passes `--s3-upload-concurrency` (integer 1-64) on the same uploads.

`verification_mode = "hash"` makes simple jobs pass `--checksum` to rclone, which compares MD5 sums: rclone reads and hashes the entire local source again on every sync (S3 multipart ETags are not MD5, so rclone also has to rely on stored checksums). Use `"simple"` unless that cost is acceptable.

## S3, credentials, exclusions and notifications

Use [sambinha.example.toml](../sambinha.example.toml) for the complete configuration and [rclone.example.conf](../rclone.example.conf) for the `aws` S3 remote. Create the bucket first and match its region. Prefer separate prefixes to avoid overlapping data or recovery controls. The default `GLACIER_IR` class provides immediate retrieval but charges for retrieval and has minimum billing terms. JSON controls and the recovery index use STANDARD. [S3 setup and storage classes](s3.md).

Store credentials outside the repository in an AWS profile/IAM role or a private dotenv file:

```toml
[backup]
secrets_file = "~/.config/sambinha/secrets.env"
cronitor_url = "${CRONITOR_URL}"
```

Protect the file with `chmod 600`. [secrets.example.env](../secrets.example.env) contains placeholders only. The file is parsed as data, never executed; inherited environment values take precedence. `--secrets-file ""` disables loading for one run. Missing or malformed files fail without exposing their values. Notifications are optional; with no Cronitor URL, they are disabled. Never commit a credential-bearing URL or real AWS keys.

## Exclude files and directories

Create a UTF-8 text file beside your `sambinha.toml`, for example `excludes.txt`, and set `exclude_files` inside each job that should use it:

```toml
[[jobs]]
name = "documents"
source = "~/Documents"
destination = "/mnt/backup/documents"
exclude_files = "./excludes.txt"
```

Put one rclone exclusion pattern per line in `excludes.txt`:

```text
# Temporary files and logs at any depth
*.tmp
*.log

# Directories at the source root, including all their contents
/cache/**
/node_modules/**
```

The **file location** is relative to `sambinha.toml`, not the terminal's working directory. The **patterns** are relative to the job's source. For a source `/mnt/dados`, `/cache/**` matches `/mnt/dados/cache/`, not the system directory `/cache/`; it does not match a nested `projects/cache/`. `*.tmp` and `*.log` match files in any subdirectory. Blank lines and lines beginning with `#` are ignored. Rules use rclone syntax, rather than Git's ignore-file syntax.

The shipped TOML examples already set `exclude_files = "./excludes.example.txt"`. That template has no active exclusions; uncomment the patterns you want or copy it to `excludes.txt` and update the job's path. A [Brazilian Portuguese template](../excludes.example.pt-BR.txt) is also provided.

For Finder/macOS Icon files, use `Icon[\x0d␍]`: it matches the trailing CR or its visible rclone representation without excluding names such as `Icon.png` or `Icon1`. Prefer glob patterns when possible; embedded `{{...}}` regex rules prevent rclone from pruning excluded directories. Batch discovery uses one authenticated local rclone process, with a separate `timeout` deadline for each directory. Rules are frozen per job, so edits apply on the next execution. See [discovery and performance](performance.md#bounded-source-discovery).

Run a simulation to check the selected file count and planned work before the backup:

```bash
.venv/bin/sambinha --config sambinha.toml --dry-run
```

Excluded files are not uploaded or considered removed. Files already in the backup are **not deleted merely because they become excluded**; their history also stays protected while the exclusion applies. Mixed legacy archives containing excluded paths are retained separately in `state.excluded`. Each job can use a different rule file. Missing, unreadable or invalid UTF-8 rule files stop the backup. Changes made after a job freezes its rules apply when that job next starts; later jobs read their own rules at their start. [Filtering and retention details](usage.md).

## Tests and documentation

```bash
.venv/bin/python -m pip install -e '.[test,dev]'
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m pytest -n auto --cov=sambinha --cov-branch --cov-report=term-missing --cov-fail-under=85
```

The `dev` extra installs ruff (linter, pinned to the 0.17 series) and mypy; for the lint check alone, `.venv/bin/python -m pip install -e '.[dev]'` is enough, then run `make lint` or `.venv/bin/python -m ruff check .`. `make test`, `make coverage`, `make lint` (ruff) and `make typecheck` (mypy) wrap the same commands; `make test-fast` runs only `tests/unit` (no rclone, 7-Zip or network) and `make test-integration` only `tests/integration`. The tests remain `unittest` cases, so `python -m unittest discover` still works, only serially. `ruff format` is configured but not enforced: CI runs `ruff check` and mypy in a lint job before the test matrix (Python 3.12 and 3.13).

Tests live in two directories, marked automatically (`pytest -m unit`, `pytest -m integration`). `tests/unit` is pure logic with mocks, fakes (`FakeRunner`) and temporary directories; it needs neither rclone, 7z nor network and runs in seconds. `tests/integration` uses real rclone/7-Zip subprocesses, the real CLI process or Moto/HTTP S3 and skips itself when a tool is missing. Shared builders (`Sandbox`, `FakeRunner`) live in `tests/helpers.py`. Integration tests use temporary files, real rclone/7-Zip and Moto's local AWS emulator. They do not require real AWS or Cronitor credentials. HTTP integration requires loopback networking. Results are recorded in [test results](test-results.md); GitHub runs [continuous tests](https://github.com/lesantos/sambinha/actions).

English is the default documentation language. Every maintained Markdown guide and public commented configuration example has a `.pt-BR` counterpart. Code, configuration keys, dependency lists, backup data fixtures and license identifiers retain their stable names. See the [usage guide](usage.md), [architecture](architecture.md), [security notes](../SECURITY.md), [changelog](../CHANGELOG.md), and [configuration example](../sambinha.example.toml).

## Same backup from several machines

The job **name** is the backup's identity, together with its type and logic (and the S3 destination for S3 jobs). Source and local destination paths are recorded per machine as informational origins, so the same job can run from Linux (`/mnt/disk/Docs`) and from macOS (`/Volumes/disk/Docs`) against one destination, alternating, without losing history. Keep the same `[[jobs]]` name on every machine and give each machine its own `state_dir`; the first run on a new machine adopts the confirmed index from the destination, rehashes the source once (fingerprint caches are per device) and continues from there. Restores work from any machine with the configuration and credentials.

Per-machine mount points can be expressed with `source_mount`/`destination_mount` set to that machine's volume, or with `${VARS}` in `source`/`destination` resolved from the environment of each machine. Renaming a job, or pointing a job at a destination that belongs to another job name, is refused (`Destino pertence ao job X`); run `sambinha --config ... backup --adopt-identity` once to take over the existing backup, after which the new name is written to the destination marker and no flag is needed. A pre-0.16 state on the same machine migrates by itself; one whose paths differ also needs `--adopt-identity`. Type, logic and S3 destination differences are never adopted.

## Resuming interrupted backups

A batch job resumes an interrupted run by itself (`resume = "auto"`, the default): archives already uploaded are recorded in `state/<job>/uploads.json` and are neither recompressed nor uploaded again if the destination still has them and the source files are unchanged. Set `resume = "fresh"` in a `[[jobs]]` table (or pass `--fresh-start` for one run) to discard the fingerprint cache, the last full scan and every journal before planning; confirmed backup state is kept. `resume` accepts only `auto` or `fresh` and is ignored by simple jobs. `--prune-orphans` (backup only) additionally deletes destination archives and recovery generations that no confirmed state references and that are over one day old; without it they are only reported. Details: [interruption and resume, orphan report, clean restart and temporary sweep](batch-lifecycle.md#interruption-and-resume).

## Sequential jobs and overlapping paths

From 0.9.0, jobs run to completion in TOML order. Each job freezes its own exclusion file, checks mounts/disks, builds a fresh inventory using only its own persistent fingerprint cache, transfers and applies retention, commits its index, performs any due verification and writes its summary before the next job starts. A later preflight failure cannot prevent an earlier job from completing. `continue_on_job_error` still belongs to the failing job; there is no automatic dependency ordering or rollback.

Destinations may not overlap each other, including nested local paths or S3 prefixes. A job's destination may be the source of a later job: the later job sees the completed files from the earlier one, as when running separate commands in order. Each job still rejects overlap between its own source and destination, and all sources/destinations must remain separate from state/logs.

Review these interactions when sharing paths:

- Simple backups manage their entire selected destination tree. Files absent from that job's source can be moved to history and removed, even if another job created them. Exclude the other job's paths when they must be preserved; separate subdirectories are easier to manage.
- A later job can overwrite earlier data or remove archives during retention. Verification records success at the time it runs; it cannot guarantee that later jobs leave the data intact. A subsequent verification of an earlier index can fail. Newer-destination and source-removal guards still apply.
- Batch jobs with the same destination share `.sambinha/control.tar.gz`. The last writer replaces the remote recovery index. Preserve every local job index; remote-only recovery of earlier jobs may fail. Shared archives can also be retired by a job unaware that another job uses them.
- `.sambinha` remains reserved at the source root. Copying an entire backup destination directly as another source can fail after history or batch controls appear there. Use a parent directory so the backup's metadata is below the source root, as in the `LLLD` example. Nested backup history is then part of the selected data unless excluded.
- Dry runs do not create intermediate files or origins. A dependent job whose source would be created by an earlier real transfer can therefore fail in a dry run; separate dry-run commands behave the same way.

Deletion-alert counts are independent per job, so combining commands does not change the threshold. Jobs with identical sources now perform independent scans, which can cost more IO; their persistent fingerprint caches still work. Progress assigns equal ranges to jobs because later source sizes are unknown until those jobs start. Internal file/batch progress remains weighted by bytes.

One invocation retains a shared configuration, audit log, overall progress, Cronitor lifecycle and writer lock. It returns failure if any executed job fails. Separate commands have separate logs/notifications and release the lock between commands; a combined command keeps the lock for its full duration. Parsing/configuration errors and unavailable shared runtime dependencies still prevent execution. These invocation details do not change successful copy or verification results for the same jobs and order, without external intervening writes.


## Batches and recovery in 0.13.0

See [stable batches, uniform compression, final Cronitor alerts, manual rebalance and generation recovery](batch-lifecycle.md) for current behavior and migration from 0.12.0.
