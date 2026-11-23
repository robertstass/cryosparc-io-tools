#!/usr/bin/env python3
"""Extract values from CryoSPARC .csg files (or any YAML) by key path.

Every value in a file has a path such as ``results.blob.num_items`` (list
items use their index, e.g. ``items.0.name``). Key patterns select paths.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Callable, Iterator

import yaml

from ._common import expand_paths, write_csv

_TIMESTAMP = "tag:yaml.org,2002:timestamp"


class _Loader(yaml.SafeLoader):
    """SafeLoader that keeps timestamps as the text written in the file."""


_Loader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != _TIMESTAMP]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def glob_to_regex(pattern: str, sep: str = ".") -> str:
    """Shell-style wildcards over paths: ``*``/``?`` stay inside one level, ``**`` crosses levels."""
    not_sep = f"(?:(?!{re.escape(sep)}).)"
    out: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern.startswith("**", i):
                while i < len(pattern) and pattern[i] == "*":
                    i += 1
                out.append(".*")
                continue
            out.append(not_sep + "*")
        elif char == "?":
            out.append(not_sep)
        elif char == "[":
            j = i + 1
            if j < len(pattern) and pattern[j] in "!^":
                j += 1
            if j < len(pattern) and pattern[j] == "]":
                j += 1
            while j < len(pattern) and pattern[j] != "]":
                j += 1
            if j >= len(pattern):
                out.append(re.escape(char))
            else:
                body = pattern[i + 1:j].replace("\\", "\\\\")
                if body[:1] in ("!", "^"):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                i = j
        else:
            out.append(re.escape(char))
        i += 1
    return "".join(out)


def make_matcher(pattern: str, mode: str, sep: str, ignore_case: bool) -> Callable[[str], bool]:
    """Return a predicate on a path string. Modes: glob (default), regex (search), fixed (exact)."""
    flags = re.IGNORECASE if ignore_case else 0
    if mode == "fixed":
        wanted = pattern.lower() if ignore_case else pattern
        return lambda path: (path.lower() if ignore_case else path) == wanted
    if mode == "regex":
        compiled = re.compile(pattern, flags)
        return lambda path: compiled.search(path) is not None
    compiled = re.compile(glob_to_regex(pattern, sep), flags)
    return lambda path: compiled.fullmatch(path) is not None


def walk(value: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    """Yield (path, value) for every node below the root, containers included."""
    if isinstance(value, dict):
        children = ((str(key), child) for key, child in value.items())
    elif isinstance(value, list):
        children = ((str(index), child) for index, child in enumerate(value))
    else:
        return
    for key, child in children:
        yield path + (key,), child
        yield from walk(child, path + (key,))


def format_value(value: Any) -> str:
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str, separators=(",", ":"))
    return str(value)


def load_yaml(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return yaml.load(handle, Loader=_Loader)


def grep_document(document: Any, patterns: list[str], mode: str = "glob", sep: str = ".",
                  ignore_case: bool = False) -> tuple[list[tuple[str, Any]], list[str]]:
    """Return ``([(path, value), ...], unmatched_patterns)`` for one loaded document.

    With no patterns every value is selected. A dict or list is returned (as
    its value) only when none of its contents matched too, so a pattern that
    matches both a section and things inside it reports the inner values.
    """
    matchers = [(pattern, make_matcher(pattern, mode, sep, ignore_case)) for pattern in patterns]
    hits: list[tuple[int, tuple[str, ...], Any]] = []
    used: set[str] = set()
    for path, value in walk(document):
        text = sep.join(path)
        if not matchers:
            hits.append((0, path, value))
            continue
        matched = [(i, pattern) for i, (pattern, test) in enumerate(matchers) if test(text)]
        if matched:
            used.update(pattern for _, pattern in matched)
            hits.append((matched[0][0], path, value))
    covered = {path[:i] for _, path, _ in hits for i in range(1, len(path))}
    # Results follow the order the patterns were given, then the file's own order.
    kept = sorted((hit for hit in hits if hit[1] not in covered), key=lambda hit: hit[0])
    return ([(sep.join(path), value) for _, path, value in kept],
            [pattern for pattern in patterns if pattern not in used])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cryosparc_csg_grep.py",
        description="Print values from CryoSPARC .csg (or any YAML) files by key path, "
                    "e.g. -k results.blob.num_items. Without -k every value is printed.")
    parser.add_argument("files", nargs="+", metavar="FILE",
                        help="One or more YAML files; shell-style wildcards (*.csg) are accepted")
    parser.add_argument("-k", "-e", "--key", action="append", default=[], metavar="PATTERN",
                        help="Key path to extract; repeat for several. Default matching is "
                             "shell-style: * and ? stay within one level, ** crosses levels, "
                             "[abc] is a character set (e.g. 'results.*.num_items')")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--regex", action="store_true",
                      help="Treat patterns as regular expressions searched in the full path")
    mode.add_argument("--fixed", action="store_true",
                      help="Treat patterns as exact paths (no wildcards)")
    parser.add_argument("-i", "--ignore-case", action="store_true", help="Case-insensitive key matching")
    parser.add_argument("--sep", default=".", metavar="STR",
                        help="Separator between levels in key paths (default '.')")
    parser.add_argument("--values-only", action="store_true",
                        help="Print just the values, one per line")
    parser.add_argument("--output-csv", metavar="CSV",
                        help="Also write a CSV (an existing file is overwritten): one row per file with "
                             "a column per matched path, or with --long one row per match")
    parser.add_argument("--long", action="store_true",
                        help="With --output-csv write columns file,key,value instead of one column per key")
    args = parser.parse_args(argv)
    if not args.sep:
        parser.error("--sep must not be empty")
    mode_name = "regex" if args.regex else "fixed" if args.fixed else "glob"
    if mode_name == "regex":
        for pattern in args.key:
            try:
                re.compile(pattern)
            except re.error as exc:
                parser.error(f"invalid regular expression {pattern!r}: {exc}")

    paths, unmatched = expand_paths(args.files)
    failed = False
    for pattern in unmatched:
        print(f"error: no file matches {pattern!r}", file=sys.stderr)
        failed = True

    results: list[tuple[str, list[tuple[str, str]]]] = []
    for path in paths:
        try:
            document = load_yaml(path)
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            print(f"error: {path}: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}",
                  file=sys.stderr)
            failed = True
            continue
        hits, missing = grep_document(document, args.key, mode_name, args.sep, args.ignore_case)
        for pattern in missing:
            print(f"warning: {path}: no match for {pattern!r}", file=sys.stderr)
            failed = True
        if not args.key and not hits:
            print(f"warning: {path}: no values found", file=sys.stderr)
            failed = True
        results.append((path, [(key, format_value(value)) for key, value in hits]))

    show_file = len(paths) > 1
    for path, hits in results:
        for key, value in hits:
            value = value.replace("\r", "\\r").replace("\n", "\\n")
            if args.values_only:
                print(value)
            else:
                print(("\t".join((path, key, value)) if show_file else f"{key}\t{value}"))

    if args.output_csv:
        if args.long:
            write_csv(args.output_csv, ["file", "key", "value"],
                      ([path, key, value] for path, hits in results for key, value in hits))
            print(f"Written csv file to: {args.output_csv}")
        else:
            columns: list[str] = []
            for _, hits in results:
                for key, _ in hits:
                    if key not in columns:
                        columns.append(key)
            write_csv(args.output_csv, ["file", *columns],
                      ([path, *(dict(hits).get(key, "") for key in columns)] for path, hits in results))
            print(f"Written csv file to: {args.output_csv}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
