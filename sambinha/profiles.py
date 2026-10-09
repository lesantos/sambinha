"""Compression settings chosen by file category, with separate defaults."""
from pathlib import PurePosixPath

EXTENSIONS = {
    "text": {".md", ".txt", ".rst", ".log", ".csv", ".json", ".xml", ".yaml", ".yml",
             ".toml", ".ini", ".conf", ".html", ".css", ".js", ".ts", ".py", ".sh", ".sql", ".tex"},
    "video": {".mp4", ".mkv", ".mov", ".avi", ".webm", ".mpeg", ".mpg", ".m4v", ".wmv"},
    "photo": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".tif", ".tiff", ".raw", ".dng"},
}


def category(path):
    path = PurePosixPath(path)
    if path.name.lower().split(".")[0] in {"readme", "license", "changelog", "copying", "authors"}:
        return "text"
    return next((kind for kind, extensions in EXTENSIONS.items() if path.suffix.lower() in extensions), "other")


def settings(job, files):
    kinds = {category(f.path) for f in files}
    kind = next(iter(kinds)) if len(kinds) == 1 else "other"
    return kind, getattr(job, f"compression_{kind}_level"), getattr(job, f"compression_{kind}_threads")
