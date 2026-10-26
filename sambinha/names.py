"""Domain constants and literal aliases shared by every module; imports nothing from the package."""
from typing import Literal

NAMESPACE = ".sambinha"
HISTORY = NAMESPACE + "/history"
INDEXES = NAMESPACE + "/indexes"
CONTROL = NAMESPACE + "/control.tar.gz"
# Destination marker naming the job that owns the prefix (see state.identity).
JOB_MARKER = NAMESPACE + "/job.json"
STATE_FILE = "state.json"
# rclone filter that keeps the reserved namespace out of sync/check/restore/listing commands.
NAMESPACE_EXCLUDE = ("--exclude", "/" + NAMESPACE + "/**")

type Logic = Literal["simple", "batch"]
type StorageType = Literal["local", "s3"]
type VerificationMode = Literal["simple", "hash"]
type Action = Literal["backup", "restore", "rebalance", "verify-local-metadata",
                      "verify-destination-metadata", "verify-destination-integrity"]
