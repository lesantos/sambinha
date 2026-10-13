"""Fit transient progress to terminal cells without triggering line wrapping."""
import os
import re
import shutil
import unicodedata


def terminal_columns(stream):
    try:
        return max(1, os.get_terminal_size(stream.fileno()).columns)
    except (AttributeError, OSError, ValueError, TypeError):
        return max(1, shutil.get_terminal_size(fallback=(80, 24)).columns)


def cell_width(text):
    def width(char):
        if unicodedata.combining(char) or unicodedata.category(char) in {"Mn", "Me"}:
            return 0
        return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return sum(width(char) for char in text)


def shorten(text, columns, *, tail=False):
    if columns <= 0:
        return ""
    if cell_width(text) <= columns:
        return text
    chars, used = [], 0
    for char in reversed(text) if tail else text:
        width = cell_width(char)
        if used + width > columns - 1:
            break
        chars.append(char)
        used += width
    if tail:
        result = "".join(reversed(chars))
        while result and (unicodedata.combining(result[0]) or unicodedata.category(result[0]) in {"Mn", "Me"}):
            result = result[1:]
        return "…" + result
    return "".join(chars) + "…"


def compact_activity(text):
    for before, after in (("conferidos: ", "conf:"), ("copiados: ", "cóp:"),
                          ("remoções: ", "rem:"), ("movidos: ", "mov:"),
                          ("tempo: ", "t:"), ("aguardando estatísticas do rclone", "aguardando dados")):
        text = text.replace(before, after)

    def compact_count(match):
        number = int(match[0])
        for scale, suffix in ((10**9, "B"), (10**6, "M"), (10**3, "k")):
            if number >= scale:
                return f"{number / scale:.1f}".rstrip("0").rstrip(".") + suffix
        return match[0]

    return re.sub(r"\b\d{5,}\b", compact_count, text).replace(" | ", " ")


def progress_line(percent, stage, directory, activity, columns, *, indeterminate=False):
    # Leave a cell free: writing through the right margin can auto-wrap.
    budget = max(0, columns - 1)
    prefix = "Em andamento" if indeterminate else f"{percent:5.1f}% do backup (estimado)"
    parts = [prefix, stage, directory]
    if activity:
        parts.append(activity)
    text = " | ".join(parts)
    if cell_width(text) <= budget:
        return text
    prefix = "ativo" if indeterminate else f"{percent:5.1f}% est."
    aliases = {"Sincronizando arquivos": "Sincronizando", "Copiando arquivos": "Copiando",
               "Copiando arquivo": "Copiando", "Concluindo tarefa": "Concluindo",
               "Backup concluído": "Concluído"}
    suffix = compact_activity(activity) if activity else ""
    stage_budget = max(4, budget - cell_width(prefix) - cell_width(suffix) - 9 - 4)
    label = stage if cell_width(stage) <= stage_budget else aliases.get(stage, stage)
    parts = [prefix, shorten(label, stage_budget)]
    fixed = " | ".join(parts + ([suffix] if suffix else []))
    remaining = budget - cell_width(fixed) - 3
    if remaining >= 4:
        parts.append(shorten(directory, remaining, tail=True))
    elif cell_width(fixed) > budget:
        # On very narrow terminals prefer counters to the stage/path.
        parts = [prefix]
    if suffix:
        parts.append(suffix)
    return shorten(" | ".join(parts), budget)
