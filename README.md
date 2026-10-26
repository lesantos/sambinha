# Sambinha

[Português do Brasil](README.pt-BR.md)

Sambinha **0.15.0** backs up files to a local folder, mounted disk or Amazon S3. It keeps previous versions and records each execution. Requires Linux or macOS, Python 3.12+, rclone 1.60+ and 7-Zip 22.00+.

Jobs run to completion in TOML order. Destinations may not overlap each other; one job's destination may be a later job's source. Review the [interaction rules](docs/configuration.md#sequential-jobs-and-overlapping-paths).

## Install

**rclone 1.60 or newer** and **7-Zip 22.00 or newer** (`7zz`) are required; Sambinha refuses to run with older versions. Ubuntu 22.04 and Linux Mint 21 ship rclone 1.53 and p7zip 16.02, which are too old: install the official binaries from <https://rclone.org/downloads/> and <https://www.7-zip.org/download.html> (Linux x64 `7zz`) into `/usr/local/bin`, or on Ubuntu 24.04/Debian 13 use `sudo apt install rclone 7zip`. Then set `compressor = "7zz"` in batch jobs and check with `rclone version` and `7zz`.

```bash
sudo apt install git python3 python3-venv
git clone https://github.com/lesantos/sambinha.git
cd sambinha
python3 -m venv .venv
.venv/bin/python -m pip install .
```

On macOS, install the tools with Homebrew instead of apt (`brew install git python@3.12 rclone sevenzip`; the compressor is also `7zz`), then run the same `git clone`, `venv` and `pip install` steps. See [macOS notes](docs/configuration.md#macos) before scheduling backups.

The repository is private. Installable packages are also available in [releases](https://github.com/lesantos/sambinha/releases).

## Configure and run

Create `sambinha.toml`. Paths are relative to this file; `~` and environment variables are supported. Keep destination, state and logs outside the source, and preserve the state directory.

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
verification_mode = "simple"
verify_source_changes = true # Retry source races up to 3 times; then fail.
max_delete = 100 # Absolute per-job limit passed to rclone sync.
check_source_disk = true
check_destination_disk = true
continue_on_job_error = true
# Optional UTF-8 rclone exclusion-pattern file:
# exclude_files = "./excludes.txt"
```

```bash
.venv/bin/sambinha --config sambinha.toml --dry-run
.venv/bin/sambinha --config sambinha.toml
.venv/bin/sambinha verify-local-metadata --config sambinha.toml --job documents
.venv/bin/sambinha restore --config sambinha.toml --job documents --output /mnt/recovered
```

Simulation runs `rclone sync --dry-run` and writes a log without changing backup data. Simple jobs no longer build a Sambinha file index; `max_delete` is an absolute per-job rclone limit. `--backup-dir` history expires at the larger of `deleted_days` and `version_days`; since rclone does not label saved items as deletions or replacements, both use this conservative retention. Restore accepts `--path` for files and directories. The [complete example](sambinha.example.toml) includes all options.

## Essential behavior

- **Storage and verification:** `logic = "simple"` delegates tree sync and exclusions to rclone; `"batch"` creates indexed compressed archives. `sambinha verify-local-metadata` uses `rclone check` for simple jobs; batch `verify-local-metadata` compares source size/time with local JSONs. Separate `verify-destination-metadata` and `verify-destination-integrity` actions check replicas and archive integrity. See [batch checks](docs/batch-checks.md).
- **Symbolic links:** source links are ignored and never recreated in the destination. Conflicting destination links are replaced by real files/directories without touching their targets; links without conflicts are left intact. [Decision and limits](docs/usage.md#symbolic-links).
- **Retention:** simple-job backups in `.sambinha/history/<run-id>` expire after the larger of `deleted_days` and `version_days`; batch jobs keep separate deletion/version deadlines. Logs retain 14 days; `log_retention_days = 0` disables log cleanup.
- **Jobs:** run sequentially. Each job's `continue_on_job_error` defaults to `true`; set it to `false` to stop when that job fails. Any job failure returns a nonzero exit code, and errors identify the job.
- **Disks:** source/local destination checks default to `true` and fail above 95% occupancy. Use `false` on the corresponding per-job option to skip the check, such as `check_source_disk = false` for Seafile/FUSE. Temporary space stays checked.
- **Protection and progress:** each simple job has an absolute `max_delete` limit; `--allow-mass-deletion` bypasses it for one run. Rclone's native filters protect excluded files. Concurrent normal backups/verifications sharing `state_dir` are refused. Terminal progress begins with the overall estimated percentage and follows rclone's sync stats.
- **Log detail:** file and terminal logs default to errors and summaries. `log_file_level` and `log_terminal_level` can be `error`, `summary` or `detailed`. Simple sync requests only rclone errors by default. On rclone 1.60, `--update` still preserves newer destination files, but detecting and failing on those conflicts requires `detailed` in at least one output.

## More information

- [Complete configuration, failure policy, exclusions and log retention](docs/configuration.md)
- [Recovery, verification, retention, disk/mount checks and limits](docs/usage.md)
- [Performance and compression/thread profiles](docs/performance.md)
- [S3 setup and storage classes](docs/s3.md) · [Credentials and security](SECURITY.md)
- [Changelog](CHANGELOG.md) · [Tests](docs/test-results.md) · [Architecture](docs/architecture.md)


## Batches and recovery in 0.13.0

See [stable batches, uniform compression, final Cronitor alerts, manual rebalance and generation recovery](docs/batch-lifecycle.md) for current behavior and migration from 0.12.0.
