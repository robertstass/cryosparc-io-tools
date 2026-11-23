"""Helpers shared by the command-line tools."""
from __future__ import annotations

import csv
import glob
from pathlib import Path
from typing import Iterable, Sequence


def expand_paths(patterns: Iterable[str]) -> tuple[list[str], list[str]]:
    """Expand shell-style wildcards ourselves (Windows shells do not).

    Returns ``(paths, unmatched)``: paths in argument order (sorted within one
    wildcard, duplicates dropped) and the patterns that matched nothing.
    """
    paths: list[str] = []
    unmatched: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        if glob.has_magic(pattern):
            found = sorted(glob.glob(pattern, recursive=True))
            found = [p for p in found if Path(p).is_file()]
        else:
            found = [pattern] if Path(pattern).is_file() else []
        if not found:
            unmatched.append(pattern)
        for path in found:
            if path not in seen:
                seen.add(path)
                paths.append(path)
    return paths, unmatched


def write_csv(path: str, header: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    """Write a CSV with a header row, replacing any existing file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
