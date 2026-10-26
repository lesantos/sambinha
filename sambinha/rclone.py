"""rclone argument construction and execution, independent of storage policy."""
from .filtering import SKIP_LINK_FLAGS


def command(config, job, runner, operation, *args, retry=False, standard=False, input=None,
            progress_directory=None, on_output=None):
    flags = ["--retries", "1", "--low-level-retries", "1", *SKIP_LINK_FLAGS]
    if config.rclone_config:
        flags += ["--config", config.rclone_config]
    if job.type == "s3":
        # GLACIER_IR is cold storage with immediate access, retrieval fees,
        # minimum billed size 128 KiB and minimum duration 90 days. GLACIER
        # and DEEP_ARCHIVE need restore and bill at least 90/180 days.
        # STANDARD costs more per GB, without archival restore constraints.
        storage_class = (job.control_storage_class if standard == "control" else
                         "STANDARD" if standard else job.storage_class)
        flags += ["--s3-storage-class", storage_class]
        # Optional tuning is S3-only: local destinations never receive these flags.
        if job.fast_list and job.logic == "simple" and operation in {"sync", "check", "lsjson"}:
            flags.append("--fast-list")
        if operation in {"copyto", "copy", "sync"}:
            if job.s3_chunk_size:
                flags += ["--s3-chunk-size", job.s3_chunk_size]
            if job.s3_upload_concurrency:
                flags += ["--s3-upload-concurrency", str(job.s3_upload_concurrency)]
    progress = {"progress_directory": progress_directory} if progress_directory is not None else {}
    if on_output is not None:
        progress["on_output"] = on_output
    return runner.run([config.rclone, operation, *map(str, args), *flags],
                           retry=retry, input=input, **progress)
