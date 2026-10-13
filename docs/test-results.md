# Sambinha 0.10.0 test results

[Português do Brasil](test-results.pt-BR.md)

On 2026-10-12/13, Linux, Python 3.12.3: `python -m unittest discover -v` ran **306 tests with no failures**; 26 were skipped because HTTP S3 was opt-in and three symlink edge cases were deferred. The opt-in `tests.test_s3_http` suite then passed **23/23**, the updated source-link retention test passed, and the five documentation/example checks passed. Across these runs, **304 tests passed and two symlink race/conflict cases remain skipped**. Tests used real local rclone/7-Zip and Moto locally, without AWS or Cronitor credentials.

Coverage was not recomputed for this release.

## Historical results: 0.9.0

Run on 2026-10-12, Linux, Python 3.12.3: **301 tests passed, no failures or skips**, in **349.296 seconds**. Branch-inclusive coverage is **94%**, above the required 85%.

## Verified behavior

Independent jobs complete exclusions, preflight, inventory, transfers/retention, committed state, scheduled verification and summary before the next inventory begins. Real local tests compare combined and separate commands for simple/batch chains in simple/hash modes after adding, changing and deleting files. They verify fresh origins and newly produced exclusions, persistent fingerprint-cache invalidation for same-size/same-mtime edits, byte-exact restores and manual/scheduled verification.

Overlapping exact/nested local and S3 destinations are accepted. Tests verify disjoint copies protected by exclusions, later overwrites and removals invalidating earlier indexes, and batch jobs sharing a destination whose remote recovery control belongs to the last writer. Reserved source-root metadata and unmaterialized dry-run dependencies fail consistently with separate commands. A later preflight failure preserves previous completed work; continuation policies and interruption handling remain covered. Deletion-alert thresholds are independent per job and global progress remains monotonic.

All prior backup, retention, compression, filtering, concurrency, disk/mount, symbolic-link, writer-lock, recovery, verification, secret-redaction and progress tests were rerun. Tests use temporary data, real rclone/7-Zip and Moto in-process and over localhost HTTP, without real AWS or Cronitor services. Public bilingual examples and reciprocal guide links also pass.

## Distribution

| Suite | Passed |
| --- | ---: |
| `tests.test_batches` | 17 |
| `tests.test_config` | 17 |
| `tests.test_disks` | 10 |
| `tests.test_examples` | 5 |
| `tests.test_filtering` | 11 |
| `tests.test_hardening` | 10 |
| `tests.test_job_errors` | 10 |
| `tests.test_job_independence` | 11 |
| `tests.test_local_integration` | 28 |
| `tests.test_log_retention` | 9 |
| `tests.test_mounts` | 11 |
| `tests.test_performance` | 8 |
| `tests.test_progress` | 13 |
| `tests.test_protection` | 7 |
| `tests.test_recovery` | 15 |
| `tests.test_retention_alerts` | 10 |
| `tests.test_s3_http` | 22 |
| `tests.test_s3_integration` | 20 |
| `tests.test_secrets` | 25 |
| `tests.test_security_examples` | 2 |
| `tests.test_services` | 20 |
| `tests.test_symlinks` | 7 |
| `tests.test_verification_modes` | 13 |
| **TOTAL** | **301** |

## Reproduce

```bash
.venv/bin/python -m pip install -e . -r requirements-test.txt
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m coverage run --branch -m unittest discover -v
.venv/bin/python -m coverage report --include='sambinha/*' --show-missing --fail-under=85
```

```text
Name                     Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------
sambinha/__init__.py         1      0      0      0   100%
sambinha/batches.py         56      0     26      0   100%
sambinha/cli.py             47      6      8      1    84%   52-54, 58-60
sambinha/config.py         204      4    110      4    97%   143, 146, 184, 207
sambinha/disks.py           26      0     14      0   100%
sambinha/engine.py         538     11    194     19    96%   107, 135->125, 156->158, 168, 177->179, 183->185, 198->174, 204->206, 266->268, 286->288, 311->309, 314, 317, 482-483, 487, 489->506, 499, 505, 510, 577
sambinha/errors.py          18      0      4      0   100%
sambinha/filtering.py       93      8     34      3    90%   24-25, 41-45, 74->76, 84
sambinha/inventory.py      140      6     72      5    95%   39, 46, 54, 65, 101, 120
sambinha/logging.py         79      1     26      2    97%   63->68, 71
sambinha/mounts.py          19      1     12      1    94%   17
sambinha/notify.py          22      1     10      2    91%   17->exit, 21
sambinha/process.py         71      2     18      1    97%   52-53, 69->exit
sambinha/profiles.py        11      0      2      0   100%
sambinha/progress.py        88      0     14      0   100%
sambinha/protection.py      21      0      8      0   100%
sambinha/recovery.py       246     15    116     13    92%   43, 76, 82, 89, 91, 94, 102, 124, 150->148, 168, 207, 224, 250, 275-277
sambinha/retention.py       41      2     12      0    96%   28-29
sambinha/secrets.py         22      0      6      0   100%
sambinha/state.py          119     16     70     17    83%   25, 46, 52, 58, 63, 82, 90, 94, 106, 108, 109->114, 111, 117, 120, 123, 125, 131
sambinha/storage.py        103     12     38      3    88%   75-76, 81-82, 83->85, 107, 109-110, 128-129, 132-134
--------------------------------------------------------------------
TOTAL                     1965     85    794     71    94%
```
