"""Default extension lists for the three compression tiers and their normalization.

Tiers are chosen by file extension only: text (small, very compressible), already compressed (stored,
recompressing wastes CPU) and everything else. ``NONE`` is the keyword that stands for files without an
extension (``Makefile``, ``.bashrc``, ``README``); it may appear in at most one of the two lists.
"""
from __future__ import annotations

from functools import lru_cache

from .errors import ConfigError

NONE = "<none>"

DEFAULT_TEXT_EXTENSIONS: tuple[str, ...] = (
    "<none>", "txt", "text", "md", "markdown", "rst", "adoc", "asciidoc",
    "org", "tex", "latex", "bib", "log", "out", "err", "csv",
    "tsv", "psv", "json", "jsonl", "ndjson", "geojson", "xml", "xsd",
    "xsl", "xslt", "dtd", "plist", "yaml", "yml", "toml", "ini",
    "cfg", "conf", "config", "properties", "env", "lock", "gradle", "cmake",
    "mk", "html", "htm", "xhtml", "css", "scss", "sass", "less",
    "js", "mjs", "cjs", "jsx", "ts", "tsx", "vue", "svelte",
    "php", "py", "pyi", "pyw", "rb", "erb", "pl", "pm",
    "tcl", "lua", "r", "jl", "sh", "bash", "zsh", "fish",
    "ps1", "psm1", "psd1", "bat", "cmd", "awk", "sed", "c",
    "h", "cc", "cpp", "cxx", "hpp", "hh", "hxx", "cs",
    "java", "kt", "kts", "scala", "groovy", "swift", "m", "mm",
    "go", "rs", "zig", "nim", "dart", "ex", "exs", "erl",
    "hrl", "hs", "ml", "mli", "fs", "fsx", "clj", "cljs",
    "lisp", "el", "scm", "sql", "ddl", "graphql", "gql", "proto",
    "thrift", "tf", "tfvars", "hcl", "nix", "vcf", "ics", "srt",
    "vtt", "ass", "svg", "rtf", "diff", "patch", "eml", "mbox",
    "ldif", "po", "pot", "qss", "ui", "qml", "asm", "s",
    "v", "vhd", "vhdl", "sv", "ipynb", "sln", "csproj", "vbproj",
    "xaml", "resx", "tmpl", "tpl", "j2", "jinja", "hbs", "mustache",
    "ejs", "pug", "haml", "reg", "inf", "service", "timer", "desktop",
    "rules", "cnf", "gitignore", "gitattributes", "editorconfig", "dockerignore", "mod", "sum",
    "cabal", "gemspec", "podspec", "rake", "bzl", "bazel", "ninja", "sbt",
    "pro", "pri", "wsdl", "rss", "atom", "opml", "kml", "gpx",
    "tei", "bas", "vb", "vbs", "pas", "pp", "dpr", "f",
    "f90", "for", "cob", "cbl", "ada", "adb", "ads", "d",
    "di", "ps", "eps", "sty", "cls", "bst", "md5", "sha1",
    "sha256", "sha512", "cue", "nfo", "lrc", "m3u", "m3u8", "pls",
    "xpm", "pbm", "pgm", "ppm",
)

DEFAULT_COMPRESSED_EXTENSIONS: tuple[str, ...] = (
    "jpg", "jpeg", "jpe", "jfif", "png", "apng", "gif", "webp",
    "avif", "heic", "heif", "jxl", "jp2", "j2k", "mp4", "m4v",
    "mov", "mkv", "webm", "avi", "wmv", "flv", "f4v", "mpg",
    "mpeg", "m2ts", "mts", "3gp", "3g2", "ogv", "vob", "mp3",
    "aac", "m4a", "m4b", "ogg", "oga", "opus", "wma", "flac",
    "amr", "mka", "ape", "wv", "zip", "zipx", "7z", "rar",
    "gz", "tgz", "bz2", "tbz2", "tbz", "xz", "txz", "zst",
    "tzst", "lz", "lz4", "lzma", "z", "cab", "arj", "lzh",
    "lha", "ace", "br", "jar", "war", "ear", "apk", "aab",
    "ipa", "xpi", "crx", "whl", "egg", "nupkg", "vsix", "deb",
    "rpm", "snap", "appimage", "dmg", "docx", "xlsx", "pptx", "docm",
    "xlsm", "pptm", "odt", "ods", "odp", "odg", "odf", "epub",
    "pages", "numbers", "key", "kmz", "vsdx", "pdf", "woff", "woff2",
    "gpg", "pgp", "age", "kdbx", "kdb", "aes", "enc",
)


def extension_of(path: str) -> str:
    """Lower-case last suffix of the file name without dot, or ``NONE``.

    Same rule as ``pathlib``: ``a.tar.gz`` -> ``gz``; ``.bashrc``, ``Makefile`` and ``name.`` have no
    extension; dots in directory names are ignored.
    """
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        return NONE
    return name[dot + 1:].lower()


def normalize_extension(entry, option: str) -> str:
    """Strip, lower-case and drop one leading dot; reject what cannot be a bare extension."""
    if not isinstance(entry, str):
        raise ConfigError(f"{option}: cada extensão deve ser uma string: {entry!r}")
    value = entry.strip().lower()
    if value == NONE:
        return NONE
    value = value.removeprefix(".")
    if not value or "." in value or any(c.isspace() or c in "/\\" for c in value):
        raise ConfigError(f"{option}: extensão inválida {entry!r}; use apenas o sufixo, sem espaços, "
                          f"barras nem pontos internos (ex.: \"txt\"), ou \"{NONE}\"")
    return value


def normalize_extensions(values, option: str) -> tuple[str, ...]:
    """Normalized tuple from a TOML array; non-list, bad entries and duplicates raise ``ConfigError``."""
    if not isinstance(values, (list, tuple)):
        raise ConfigError(f"{option} deve ser uma lista de strings")
    result = tuple(normalize_extension(entry, option) for entry in values)
    repeated = sorted({entry for entry in result if result.count(entry) > 1})
    if repeated:
        raise ConfigError(f"{option}: extensões repetidas: {', '.join(repeated)}")
    return result


def check_extension_lists(text, compressed) -> None:
    """Reject an extension (or the ``NONE`` keyword) present in both lists."""
    both = sorted(set(text) & set(compressed))
    if both:
        raise ConfigError("Extensão em compression_text_extensions e compression_compressed_extensions "
                          f"ao mesmo tempo: {', '.join(both)}")


@lru_cache(maxsize=16)
def lookup(text: tuple[str, ...], compressed: tuple[str, ...]) -> tuple[frozenset[str], frozenset[str]]:
    return frozenset(text), frozenset(compressed)
