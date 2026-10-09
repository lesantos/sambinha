#!/bin/sh
# Run from any directory using the project's test environment.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$project_dir"
python_bin=${SAMBINHA_TEST_PYTHON:-"$project_dir/.venv/bin/python"}
SAMBINHA_HTTP_S3_TESTS=1 "$python_bin" -m unittest tests.test_s3_http -v
