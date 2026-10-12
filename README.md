# Sambinha

[Português do Brasil](README.pt-BR.md)

Sambinha **0.8.0** backs up files to a local folder, mounted disk or Amazon S3. It keeps previous versions and records each execution. Requires Linux, Python 3.12+, rclone and 7-Zip.

## Install

```bash
sudo apt install git python3 python3-venv rclone p7zip-full
git clone https://github.com/lesantos/sambinha.git
cd sambinha
python3 -m venv .venv
.venv/bin/python -m pip install .
```

The repository is private. Installable packages are also available in [releases](https://github.com/lesantos/sambinha/releases).

## Configure and run

Create `sambinha.toml`. Paths are relative to this file; `~` and environment variables are supported. Keep destination, state and logs outside the source, and preserve the state directory.

```toml
[backup]
state_dir = "./state"
log_dir = "./logs"
log_retention_days = 14
deleted_days = 10
version_days = 2
verification_mode = "simple"

[[jobs]]
name = "documents"
source = "~/Documents"
destination = "/mnt/backup/documents"
type = "local"
logic = "simple"
check_source_disk = true
check_destination_disk = true
continue_on_job_error = true
# Optional UTF-8 rclone exclusion-pattern file:
# exclude_files = "./excludes.txt"
```

```bash
.venv/bin/sambinha --config sambinha.toml --dry-run
.venv/bin/sambinha --config sambinha.toml
.venv/bin/sambinha verify --config sambinha.toml --job documents
.venv/bin/sambinha restore --config sambinha.toml --job documents --output /mnt/recovered
```

Simulation reads inventories and writes a log without changing backups/state or sending notifications. Restore uses a separate new or empty directory. The [complete example](sambinha.example.toml) includes all options.

## Essential behavior

- **Storage and verification:** `logic = "simple"` preserves the tree; `"batch"` creates compressed archives. Default `verification_mode = "simple"` compares size in bytes and nanosecond modification time; `"hash"` checks contents.
- **Symbolic links are always ignored:** links are not copied, followed or translated, and linked directories are not traversed. Existing copies/history for linked paths remain protected while the link exists. [Decision and limits](docs/usage.md#symbolic-links).
- **Retention:** deleted files stay for 10 days and previous versions for 2 days by default. Logs retain 14 days; `log_retention_days = 0` disables log cleanup.
- **Jobs:** run sequentially. Each job's `continue_on_job_error` defaults to `true`; set it to `false` to stop when that job fails. Any job failure returns a nonzero exit code, and errors identify the job.
- **Disks:** source/local destination checks default to `true` and fail above 95% occupancy. Use `false` on the corresponding per-job option to skip the check, such as `check_source_disk = false` for Seafile/FUSE. Temporary space stays checked.
- **Protection and progress:** abnormal removals are blocked by default. Concurrent normal backups/verifications sharing `state_dir` are refused. Terminal progress begins with the overall estimated percentage and shows stage/subdirectory; file details remain in JSON logs.

## More information

- [Complete configuration, failure policy, exclusions and log retention](docs/configuration.md)
- [Recovery, verification, retention, disk/mount checks and limits](docs/usage.md)
- [Performance and compression/thread profiles](docs/performance.md)
- [S3 setup and storage classes](docs/s3.md) · [Credentials and security](SECURITY.md)
- [Changelog](CHANGELOG.md) · [Tests](docs/test-results.md) · [Architecture](docs/architecture.md)
