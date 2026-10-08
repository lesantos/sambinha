#!/bin/sh
# Run from any directory on a machine permitting localhost network sockets.
set -eu
cd "$(dirname "$0")/.."
SMABAFIBA_HTTP_S3_TESTS=1 /tmp/smabafiba-test-env/bin/python -m unittest tests.test_s3_http -v
