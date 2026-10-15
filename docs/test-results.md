# Sambinha 0.14.0 test results

[Português do Brasil](test-results.pt-BR.md)

## 0.14.0 validation

The 16 new discovery regressions passed with real rclone on Linux/Python 3.12. Coverage includes root-anchored native filters, excluded-directory pruning, one-process traversal, regex compatibility, Finder Icon control characters, source links, HTTP authentication, environment isolation, startup and per-directory timeouts, interruption, server crashes, invalid responses and preservation of previously committed backup data/state after discovery failure. Existing filtering and public-example checks also passed.

A synthetic 100-directory comparison selected identical files: discovery changed from 201 processes / 92.819 s to one process / 1.700 s. This measures local discovery only. Wheel and source distribution build successfully; release validation checks their installed contents separately. The full collection runs with `SAMBINHA_HTTP_S3_TESTS=1` and an 85% branch-inclusive coverage gate in [GitHub CI](https://github.com/lesantos/sambinha/actions/workflows/tests.yml). The 0.14.0 release notes record the completed full-suite totals and CI run.

## 0.13.0 validation

On 2026-10-14, the exact release commit `9ec723f2387f8d7fa06dfb07eebc1f293f6de4f6` passed **429/429 tests in 171.741 seconds, with no failures or skips**, and **94% branch-inclusive application coverage**, on Linux/Python 3.12 in [GitHub CI](https://github.com/lesantos/sambinha/actions/runs/38633741928). HTTP S3 was enabled for the full suite.

Local validation had previously passed 425 tests with 94% coverage; after final review, 58 focused tests passed, including four new regressions for excluded-history retention during source-independent rebalance and recovery-index producer bounds. These regressions were also included in the final complete CI run.

Coverage includes mixed-sibling mtime DFS, uniform compression, large-file diversion, no count cap, stable growth/removal membership, deferred non-blocking alerts, minimum-change source-independent rebalance, schema migration, lost/corrupt state/destination, legacy indexes, fallback, interrupted publication, metadata bounds, backward system clocks and generation retention. The implemented planner reproduced the 31,909-file/19,291,846,150-byte simulation in 14 batches without writing to real backups.

CI runs the entire collection with `SAMBINHA_HTTP_S3_TESTS=1` and requires >=85% coverage. The [0.13.0 release](https://github.com/lesantos/sambinha/releases/tag/v0.13.0) was published after the exact tagged commit passed. Its wheel and source distribution passed isolated package checks; downloaded release assets matched their SHA-256 checksums. Gitleaks found no credentials in Git history or staged changes.

```bash
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m coverage run --branch -m unittest discover -v
.venv/bin/python -m coverage report --include='sambinha/*' --fail-under=85
```

The records below are historical validation of earlier versions.

## 0.12.0 validation

On 2026-10-14, Linux, Python 3.12.3: the full branch-coverage run with `SAMBINHA_HTTP_S3_TESTS=1` passed **389/389 tests in 473.804 seconds, with no failures or skips**. All 25 HTTP S3 tests passed. Application coverage, including branches, is **94%**, above the required 85%.

New coverage exercises source-only daily verification without S3 requests or content hashing, configurable JSON mirrors and catalog recovery, monthly metadata synchronization, independent archive integrity tests, STANDARD metadata beside cold S3 archives, and publication failures followed by retries. Restoration tests cover destination-disk staging, one-batch cleanup, validation before publishing files, existing-file and symlink protections, and FAT/exFAT operation. Configuration tests cover unknown parameter names, job names and source lines; the public examples include all new options and actions.

The sections below retain earlier validation records.

## 0.11.0 validation

On 2026-10-14, Linux, Python 3.12.3: the full branch-coverage run with `SAMBINHA_HTTP_S3_TESTS=1` passed **349/349 tests in 847.735 seconds, with no failures or skips**. All 23 HTTP S3 tests passed. Application coverage, including branches, is **93%**, above the required 85%.

The run covers the original orchestration refactor and the additional recovery, storage transport and configuration-validator refactors. A focused run also passed 32 configuration/storage/verification tests. Syntax-tree comparisons confirm unchanged function logic for extracted recovery/native-sync functions and the same ordered validation checks. The wheel contains the exact current source files, imports all 39 modules independently and reports version 0.11.0. Gitleaks found no leaks in Git history or the working tree.

The pre-refactor baseline had one failure: the invalid-filter test required an absent destination root although native sync already creates an empty root before rclone parses the filter. The test now permits that existing behavior while requiring no destination entries, no committed index and failure notification. Application behavior was preserved.

Reproduce:

```bash
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m coverage run --branch -m unittest discover -v
.venv/bin/python -m coverage report --include='sambinha/*' --fail-under=85
```

The sections below retain earlier validation records.

## Unreleased destination-link changes

The final focused run passed **57 tests**, including filesystem detection, symlink policy, storage services, log levels and examples. Tests confirm that native FAT/exFAT avoids the destination traversal, while nested link-capable mounts, unknown filesystems and unsafe roots retain protection. The mounted `LLLD` and `LLLDBKP` destinations both select this scan-free path.

All **13 tests in `tests.test_symlinks` passed, with no skips**. The two destination-link tests previously deferred below have been updated and enabled. Coverage includes conflict replacement, intact external targets, nonconflicting/excluded links, dry runs, linked archive/control rebuilding, ignored expired-history links and root/directory race protections. The following results describe the already published 0.10.2 tag.

## Published 0.10.2 results

On 2026-10-13, Linux, Python 3.12.3: the full branch-coverage run with `SAMBINHA_HTTP_S3_TESTS=1` executed **339 tests in 761.289 seconds: 337 passed, no failures, 2 skipped**. The two pre-existing destination-symlink race/conflict cases remain deferred. Real local rclone/7-Zip, Moto in-process/localhost HTTP and a real pseudo-terminal were used, without real AWS or Cronitor credentials. Branch-inclusive application coverage is **92%**, above the required **85%**.

New regression tests cover independent log levels, default rclone `ERROR` output, detailed rclone streaming, bounded output retention, callback failure handling, and preservation of newer destination files in the default mode. Bilingual documentation/example checks and a working-tree Gitleaks scan also passed.

Reproduce:

```bash
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m coverage run --branch -m unittest discover -q
.venv/bin/python -m coverage report --include='sambinha/*' --fail-under=85
```

## Historical results: 0.10.1

On 2026-10-13, Linux, Python 3.12.3: the full branch-coverage run with `SAMBINHA_HTTP_S3_TESTS=1` executed **330 tests in 942.539 seconds: 328 passed, no failures, 2 skipped**. The two pre-existing destination-symlink race/conflict cases remain deferred. Real local rclone/7-Zip, Moto in-process/localhost HTTP and a real pseudo-terminal were used, without real AWS or Cronitor credentials.

Branch-inclusive application coverage is **92%**, above the required **85%**. New regression coverage includes unsupported POSIX destination metadata, backup/restore content and timestamps, structured rclone statistics, deletion-before-copy activity, empty files, interruption/timeout cleanup, terminal widths, Unicode cells and resizing.

Reproduce:

```bash
SAMBINHA_HTTP_S3_TESTS=1 .venv/bin/python -m coverage run --branch -m unittest discover -v
.venv/bin/python -m coverage report --include="sambinha/*" --show-missing --fail-under=85
```

```text
Name                      Stmts   Miss Branch BrPart  Cover   Missing
---------------------------------------------------------------------
sambinha/__init__.py          1      0      0      0   100%
sambinha/batches.py          56      0     26      0   100%
sambinha/cli.py              48      6      8      1    84%   54-56, 60-62
sambinha/config.py          227      5    128      5    97%   163, 166, 208, 231, 251
sambinha/disks.py            26      0     14      0   100%
sambinha/engine.py          434     11    142     14    96%   74, 108, 136->126, 138->126, 149->151, 174->172, 177, 180, 345-346, 350, 352->369, 362, 368, 440, 495
sambinha/errors.py           18      0      4      0   100%
sambinha/filesystems.py      28      1      6      2    91%   36, 43->31
sambinha/filtering.py       112      9     44      2    92%   24-25, 41-45, 85-86
sambinha/inventory.py       140      7     72      6    94%   39, 46, 54, 65, 94, 101, 120
sambinha/logging.py          79      1     26      2    97%   63->68, 71
sambinha/mounts.py           19      1     12      1    94%   17
sambinha/notify.py           22      1     10      2    91%   17->exit, 21
sambinha/process.py         145      4     42      3    96%   91-92, 108->exit, 147-148, 160->169
sambinha/profiles.py         11      0      2      0   100%
sambinha/progress.py        132      0     20      1    99%   129->131
sambinha/protection.py       21      0      8      0   100%
sambinha/recovery.py        260     40    124     23    80%   37, 44, 53, 64, 68, 77, 83, 90, 92, 95, 103, 124-133, 136->143, 148, 150, 153-156, 173, 201, 203-205, 210-212, 217, 239, 267, 292-294
sambinha/retention.py        41      2     12      0    96%   28-29
sambinha/secrets.py          22      0      6      0   100%
sambinha/state.py           119     17     70     19    81%   25, 46, 52-54, 58, 63, 82, 90, 94, 106, 108, 109->114, 111, 114->100, 117, 120, 123, 125, 131
sambinha/storage.py         165     26     72     13    82%   23, 45, 78-79, 81-87, 107, 109-110, 140->131, 153-155, 160, 165->157, 178, 190, 192, 198, 209-212
sambinha/terminal.py         67      1     30      3    96%   29->35, 54, 81->84
---------------------------------------------------------------------
TOTAL                      2193    132    878     97    92%
```

## Historical results: 0.10.0

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


## Batches and recovery in 0.13.0

See [stable batches, uniform compression, final Cronitor alerts, manual rebalance and generation recovery](batch-lifecycle.md) for current behavior and migration from 0.12.0.
