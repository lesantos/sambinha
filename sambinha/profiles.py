"""One configurable compression level and thread count for every file type."""


def settings(job, files=()):
    return 'uniform', job.compression_level, job.compression_threads
