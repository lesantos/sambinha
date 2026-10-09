# Sambinha specification

[Português do Brasil](specification.pt-BR.md)

## Platform and architecture

Use Python 3.12+ available on Linux Mint; runtime backups must not need root. Keep readable modules with separate domains, explanatory comments, standard configuration and existing Linux tools such as rclone and 7-Zip. Store the project in the private `lesantos/sambinha` GitHub repository. Document usage, configuration and recovery. Preflight validates dependencies before transfers.

Unit and integration tests cover all local/S3 and simple/batch combinations, including restoration completeness and byte integrity. Keep small fixtures versioned and use existing service emulators such as Moto. AWS and Cronitor credentials remain outside versioned files.

## Backup behavior

TOML holds job/global parameters. CLI overrides only supplied values and supports alternative configuration files. One source may have multiple independent destinations/strategies; overlapping source, destination, state and log paths are rejected. Failed transfers get up to three retries with two-second delays. Each invocation records start/end, directories, copied/identical files and errors. Optional Cronitor lifecycle telemetry reads `${CRONITOR_URL}` from external configuration.

Deleted source files default to ten days of history, edited previous versions to two days. Simulation reports planned work without creating archives/state, transferring/deleting backups or notifying Cronitor. Simple backups keep original paths, ordinary attributes and permissions where possible. Only changed contents are copied, using Linux tools and checksums. Newer destination files are preserved with an error.

S3 defaults to GLACIER_IR, with configurable storage classes and documented cost/access differences. Local storage includes mounted directories. Batch backups package files using a configurable 7-Zip-compatible compressor and delete temporary archives after upload. Default limits are 1 GiB of uncompressed data and 1000 files. Small directory groups may share a batch; a directory may span batches. Paths inside archives preserve the source tree, while keys use the common ancestor.

Normal batches contain at least two members where limits and compression category permit. Oversized or unpairable files use immutable singleton revisions preserving their basename with the archive extension. Sidecar JSON controls contain original metadata and upload size/checksum. A local control mirror enables reuse without remote downloads. A compressed recovery index includes state and controls and is always stored in STANDARD on S3.

## Requested releases

* 0.3.1: oldest-first files within directories; preserve compatible intact previous batches and repack only affected/new files.
* 0.4.0: restore commands, periodic integrity checks, blocking abnormal source removals, execution/simulation summaries and 95% local-disk checks. S3 verification never downloads objects.
* 0.4.1: private metadata/log permissions, complete index/path validation, credential redaction and safe restore extraction/output.
* 0.5.0: P1 fingerprint cache/full scans, P2 bulk simple transfers, P3 shared inventories, P4 bounded batch concurrency, P5 execution-scoped filter cache and P6 separate text/video/photo/other compression settings.
* 0.6.0: English is the default for maintained human-readable documentation and public commented examples, with equivalent Brazilian Portuguese companions and reciprocal links. Code/configuration identifiers and data fixtures remain stable.

The implemented behavior and limits are detailed in the [usage guide](usage.md), [performance guide](performance.md), [S3 guide](s3.md) and [security notes](../SECURITY.md).
