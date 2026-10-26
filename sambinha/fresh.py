"""Clean restart (``--fresh-start`` / ``resume = "fresh"``): forget every resume hint, keep the backup.

Discarded: the fingerprint cache and last full scan (forces a complete rescan/rehash),
the upload journal and the publication journal. Kept: ``active``/``retired``/``excluded``,
so changed files are still detected against the confirmed controls.
"""
from .journal import JOURNAL
from .local_paths import reject_symlinks

JOURNALS = (JOURNAL, "publication.json")
CACHES = ("fingerprints", "last_full_scan")


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


def drop_caches(state):
    """Forget the scan caches in memory (persisted by the run's next save); returns what existed."""
    found = [name for name in CACHES if name in state]
    for name in found:
        del state[name]
    return found
