"""Destination objects no confirmed state references: report them, remove them only on request.

Interrupted or superseded runs leave immutable archives/controls (and recovery
generations without their commit marker) behind. After a committed backup they
are counted from the destination listing the run already holds; ``--prune-orphans``
removes only those older than a day, under the same mass-deletion guard as source
removals. Anything under ``.sambinha/history`` and every key that does not look
like a Sambinha batch artifact is never considered.
"""
import json
import re
import time

from .errors import BackupError
from .index_generations import GENERATION, PREFIX
from .names import NAMESPACE
from .protection import timestamp_ns
from .retention import DAY

MIN_AGE = DAY
EXAMPLES = 20
# Keys the planner can produce: batch-<digest>.<ext> or .versions/<digest>/<name>.
ARTIFACT = re.compile(r"(?:\A|/)(?:batch-[0-9a-f]{24}\.[A-Za-z0-9]+|\.versions/[0-9a-f]{24}/[^/]+)\Z")
INDEX = re.compile(re.escape(PREFIX) + r"(" + GENERATION.pattern.removesuffix(r"\Z") + r")\.tar\.gz\Z")


def referenced(ctx):
    """Keys the confirmed state, retention history or a live journal still need."""
    keys = set()
    for key in (*ctx.state["active"], *ctx.state.get("excluded", {}), *ctx.journal,
                *(entry["key"] for entry in ctx.state["retired"])):
        keys.update((key, key + ".json"))
    keys.update(entry["control"] for entry in ctx.state["retired"] if entry.get("control"))
    return keys


def find(ctx, entries):
    """Unreferenced batch artifacts and uncommitted recovery generations, as {key: size}."""
    keep = referenced(ctx)
    found = {}
    for key, size in entries.items():
        if key.startswith(NAMESPACE + "/"):
            match = INDEX.fullmatch(key)
            if match and PREFIX + match[1] + ".commit.json" not in entries:
                found[key] = size
        elif key not in keep and ARTIFACT.search(key[:-5] if key.endswith(".json") else key):
            found[key] = size
    return found


def ages(ctx, keys, now):
    """Seconds since each object changed, from ONE listing restricted to those keys."""
    raw = ctx.storage.command("lsjson", ctx.job.destination, "--recursive", "--files-only", "--no-mimetype",
                              "--files-from-raw", "-", input="".join(key + "\n" for key in keys))
    try:
        return {item["Path"]: max(0.0, now - timestamp_ns(item["ModTime"]) / 1e9) for item in json.loads(raw)}
    except (ValueError, KeyError, TypeError, BackupError):
        return {}


def report(ctx, *, now=None):
    """Log orphans after a commit and prune them when asked; never in dry runs or rebalances."""
    if ctx.job.dry_run or ctx.rebalancing:
        return
    if ctx.listing is None:
        if not ctx.job.prune_orphans:
            return
        from .index_generations import listing
        ctx.listing = listing(ctx.storage)
    found = find(ctx, ctx.listing)
    if not found:
        return
    now = time.time() if now is None else now
    old = ages(ctx, sorted(found), now)
    ctx.event("orphans", count=len(found), bytes=sum(found.values()),
              oldest_age_days=round(max(old.values()) / DAY, 2) if old else None,
              examples=sorted(found)[:EXAMPLES])
    if not ctx.job.prune_orphans:
        return
    doomed = sorted(key for key in found if old.get(key, 0) > MIN_AGE)
    total = len(set(ctx.listing) & referenced(ctx)) + len(found)
    if (doomed and not ctx.allow_mass_deletion and total >= ctx.job.deletion_min_files
            and len(doomed) > int(total * ctx.job.max_delete_percent / 100)):
        ctx.event("mass_deletion_blocked", removed_files=len(doomed), previous_files=total,
                  threshold_percent=ctx.job.max_delete_percent, paths=doomed[:EXAMPLES])
        raise BackupError(f"Remoção de órfãos bloqueada: {len(doomed)} de {total} objetos; "
                          "use --allow-mass-deletion apenas para remoções intencionais")
    for key in doomed:
        ctx.storage.delete(key)
        ctx.event("orphan_removed", path=key, bytes=found[key])
        ctx.listing.pop(key, None)
