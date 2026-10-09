# Security and credentials

[Português do Brasil](SECURITY.pt-BR.md)

Keep AWS and Cronitor credentials outside the repository. Prefer IAM roles or an external AWS profile, or use a private `secrets_file` with mode 0600. A complete Cronitor endpoint contains credentials. Public examples contain placeholders only. Dotenv input is parsed as data; malformed bindings fail without exposing their contents.

## Code hardening in 0.4.1

* State/control schemas, file metadata, checksums, safe relative paths, duplicate members and active/retired conflicts are validated before use. Root paths, traversal, NUL and newline characters are rejected.
* Metadata/log directories use mode 0700, new JSON/log/lock files use 0600. Metadata directory symlinks and lock redirection are rejected. These protections are independent of a permissive umask.
* Environment credential values, explicit Cronitor URLs, rclone password-style names and URL userinfo are redacted from backend errors and audit/telemetry messages. Redaction happens before JSON escaping, including multiline secrets.
* Restore requires a new/empty separate output, checks archive members before extraction, rejects links/traversal, verifies staged content, creates output files exclusively and strips privileged permission bits. Recovery tars are not extracted indiscriminately.
* Source-removal guards and 95% disk checks run before destination modifications. Subprocess arguments are passed without a shell; literal file lists avoid wildcard/option interpretation.

Security tests exercise these failure cases. The local index must still be protected from an attacker with the same account or root access: permissions and schema checks do not cryptographically authenticate an adversarially modified valid index. Remote metadata verification detects size/checksum mismatches but cannot inspect archive extraction or replace a trusted index with authenticated snapshots. MD5 serves the S3/rclone integrity contract; original restored content is also checked with SHA-256.

## Previous credential audit and continuous checks

A previous review recorded zero known private values across tracked history, 141 local blobs and the published v0.2.0 wheel, with Gitleaks 8.30.1 checks of history, working snapshot and artifact. That historical audit predates this release; protected Git metadata in the current workspace is not a new complete-history audit.

CI retrieves full history and runs Gitleaks with redacted output and verified download checksums. `.gitleaks.toml` adds Cronitor URL detection to standard rules. Tests check placeholders and detection patterns without embedding real tokens.

For 0.6.0, the prepared release snapshot passed Gitleaks 8.30.1 with no findings. Its executable was checked against the official release checksum; the reachable history is checked again before publication.

```bash
./scripts/check-secrets.sh
```

If a real credential is published, revoke it at the provider before repairing history. Deleting a file does not revoke already copied secrets. Detection depends on known formats.

References: [OWASP logging guidance](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html), [OWASP argument injection guidance](https://cheatsheetseries.owasp.org/cheatsheets/OS_Command_Injection_Defense_Cheat_Sheet.html), [Python archive extraction guidance](https://docs.python.org/3.12/library/tarfile.html#extraction-filters).
