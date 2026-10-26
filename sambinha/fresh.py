"""Clean restart (``--fresh-start`` / ``resume = "fresh"``): forget every resume hint, keep the backup.

Discarded: the fingerprint cache and last full scan (forces a complete rescan/rehash),
the upload journal and the publication journal. Kept: ``active``/``retired``/``excluded``,
so changed files are still detected against the confirmed controls.
"""
from .journal import JOURNAL
from .local_paths import reject_symlinks
from .names import FINGERPRINTS_FILE
from .state import drop_fingerprints

JOURNALS = (JOURNAL, "publication.json")
CACHES = ("last_full_scan",)


def requested(job):
    return job.logic == "batch" and (job.fresh_start or job.resume == "fresh")


def discard_journals(config, job):
    """Remove the on-disk journals (names of those that existed); dry runs only report them."""
    directory = config.state_dir / job.name
    found = []
    for name in JOURNALS:
        path = directory / name
        reject_symlinks(path, label="Estado", include_parents=False)
        if path.exists():
            found.append(name)
            if not job.dry_run:
                path.unlink()
    return found


def drop_caches(state, config=None, job=None):
    """Forget the scan caches; returns what existed.

    ``last_full_scan`` (and a legacy embedded ``fingerprints``) go in memory and are persisted
    by the run's next save; ``fingerprints.json`` is deleted now unless this is a dry run.
    """
    found = [name for name in (*CACHES, "fingerprints") if name in state]
    for name in found:
        del state[name]
    if config is not None and job is not None:
        directory = config.state_dir / job.name
        exists = (directory / FINGERPRINTS_FILE).exists()
        if exists and not job.dry_run:
            drop_fingerprints(directory)
        if exists and "fingerprints" not in found:
            found.append("fingerprints")
    return sorted(found)
