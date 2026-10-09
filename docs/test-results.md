# Sambinha 0.6.0 test results

[Português do Brasil](test-results.pt-BR.md)

Run on 2026-10-09, Linux, Python 3.12.3: **222 tests passed, no failures or skips**, in **287.499 seconds**. Branch-inclusive coverage is **92%**, above the required 85%.

## Verified behavior

* Oldest-first new batches and preservation of intact/legacy archives; edits/removals only replace affected batches.
* Active-index file/folder restoration, missing batch index recovery, binary data, ordinary permissions, checksum validation before publication and overwrite/traversal/link rejection.
* Local content verification and scheduled success/failure timestamps. S3 verification only uses metadata. Real rclone completed verification against an HTTP server that allowed HEAD and rejected GET, with no object-body requests.
* Mass-removal blocking before destination/state changes, exact-threshold handling and one-invocation overrides. Disk occupancy above 95% fails before backup and after a final transfer that crosses the limit.
* Private metadata/log/lock permissions despite permissive umask, corrupt-index rejection, active/history conflict checks and multiline/password/URL secret redaction.
* Fingerprint reuse without content reads, ctime detection of same-size/same-mtime changes, periodic full reads, cache opt-out and shared source inventory.
* Bounded bulk copies, concurrently running limited batch workers, category compression settings and execution-scoped filter caching.
* Existing local/S3 simple/batch round trips, newer-destination protection, filters/mounts/retention/notifications, and equivalent bilingual public examples with reciprocal guide links.

Tests used temporary data, real rclone and 7-Zip, and Moto both in-process and over localhost HTTP. No real AWS or Cronitor services were used. Speed tests validate reduced read/process counts and concurrency; these are not production timing guarantees.

## Distribution

| Suite | Passed |
| --- | ---: |
| `tests.test_batches` | 17 |
| `tests.test_config` | 12 |
| `tests.test_disks` | 5 |
| `tests.test_examples` | 5 |
| `tests.test_filtering` | 11 |
| `tests.test_hardening` | 10 |
| `tests.test_local_integration` | 28 |
| `tests.test_mounts` | 11 |
| `tests.test_performance` | 8 |
| `tests.test_protection` | 7 |
| `tests.test_recovery` | 13 |
| `tests.test_retention_alerts` | 10 |
| `tests.test_s3_http` | 20 |
| `tests.test_s3_integration` | 18 |
| `tests.test_secrets` | 25 |
| `tests.test_security_examples` | 2 |
| `tests.test_services` | 20 |

## Reproduce

```bash
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m coverage run --branch -m unittest discover -v
.venv/bin/python -m coverage report --include='sambinha/*' --show-missing --fail-under=85
```

```text
Name                     Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------
sambinha/__init__.py         1      0      0      0   100%
sambinha/batches.py         56      0     26      0   100%
sambinha/cli.py             46      6      8      1    83%   50-52, 56-58
sambinha/config.py         200      4    110      4    97%   133, 136, 181, 204
sambinha/disks.py           22      1     12      1    94%   15
sambinha/engine.py         432     14    150     12    96%   93, 121->111, 147, 173-174, 238->236, 241, 244, 389, 397-398, 402, 416, 422, 503, 510
sambinha/errors.py           2      0      0      0   100%
sambinha/filtering.py       88      8     34      3    89%   22-23, 38-42, 70->72, 80
sambinha/inventory.py       82      6     42      5    91%   37, 44, 50, 65, 82, 91
sambinha/logging.py         31      0      6      0   100%
sambinha/mounts.py          19      1     12      1    94%   17
sambinha/notify.py          22      1     10      2    91%   17->exit, 21
sambinha/process.py         60      2     16      1    96%   49-50, 66->exit
sambinha/profiles.py        11      0      2      0   100%
sambinha/protection.py      21      0      8      0   100%
sambinha/recovery.py       208     21     86     15    87%   34, 41, 70, 76, 83, 85, 88, 96, 132-135, 145, 183, 199, 222, 230, 237-240
sambinha/retention.py       41      2     12      0    96%   28-29
sambinha/secrets.py         22      0      6      0   100%
sambinha/state.py          117     16     68     17    82%   25, 44, 50, 56, 61, 80, 88, 92, 104, 106, 107->112, 109, 115, 118, 121, 123, 129
sambinha/storage.py         89     12     32      3    86%   65-66, 71-72, 73->75, 92, 94-95, 110-111, 114-116
--------------------------------------------------------------------
TOTAL                     1570     94    640     65    92%
```
