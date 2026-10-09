#!/bin/sh
# Scan all reachable Git history; output is always redacted.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$project_dir"
gitleaks_bin=${GITLEAKS_BIN:-gitleaks}
"$gitleaks_bin" git "$project_dir" --log-opts="--all" --redact --no-banner --ignore-gitleaks-allow
