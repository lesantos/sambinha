#!/bin/sh
# Publish the prepared commit from a normal terminal with GitHub network access.
# Creates a private repository if absent; never force-pushes existing history.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
repository=lesantos/smabafiba
bundle="$project_dir/smabafiba.bundle"
if [ ! -f "$bundle" ]; then
  printf '%s\n' 'Missing smabafiba.bundle: use git push from an ordinary Git checkout.' >&2
  exit 1
fi
account=$(gh api user --jq .login)
if [ "$account" != lesantos ]; then
  printf '%s\n' 'Authenticate gh with account lesantos before publishing.' >&2
  exit 1
fi
publish_dir=$(mktemp -d /tmp/smabafiba-publish-XXXXXX)
trap 'rm -rf "$publish_dir"' EXIT HUP INT TERM
git clone --branch main "$bundle" "$publish_dir"
git -C "$publish_dir" remote set-url origin "https://github.com/$repository.git"
if ! gh repo view "$repository" --json name >/dev/null 2>&1; then
  gh repo create "$repository" --private --description 'Smart batch file backups for Linux, local storage and Amazon S3'
fi
git -C "$publish_dir" push origin main
printf '%s\n' "https://github.com/$repository/blob/main/README.md"
