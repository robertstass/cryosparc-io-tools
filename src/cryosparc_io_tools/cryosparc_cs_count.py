#!/usr/bin/env python3
"""Print the number of rows (particles) in CryoSPARC .cs files.

A .cs file is a NumPy structured array saved with ``numpy.save``, so only the
header is read: the count is instant and the data (including any pickled
object columns) is never loaded.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from ._common import expand_paths, write_csv


def count_particles(path: str | Path) -> int:
    """Return the number of rows in a NumPy-format .cs file."""
    fmt = np.lib.format
    with open(path, "rb") as handle:
        try:
            version = fmt.read_magic(handle)
        except ValueError:
            raise ValueError("not a NumPy-format .cs file (unrecognized header; "
                             "this tool reads NumPy-format .cs files only)") from None
        reader = fmt.read_array_header_1_0 if version == (1, 0) else fmt.read_array_header_2_0
        shape = reader(handle)[0]
    if len(shape) < 1:
        raise ValueError("file holds a single value, not a table of particles")
    return int(shape[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cryosparc_cs_count.py",
        description="Print the number of particles (rows) in CryoSPARC .cs files.")
    parser.add_argument("files", nargs="+", metavar="FILE",
                        help="One or more .cs files; shell-style wildcards (*.cs) are accepted")
    parser.add_argument("--output-csv", metavar="CSV",
                        help="Also write a CSV with columns file,particles (an existing file is overwritten)")
    args = parser.parse_args(argv)

    paths, unmatched = expand_paths(args.files)
    failed = False
    for pattern in unmatched:
        print(f"error: no file matches {pattern!r}", file=sys.stderr)
        failed = True
    rows: list[tuple[str, int]] = []
    for path in paths:
        try:
            rows.append((path, count_particles(path)))
        except (OSError, ValueError) as exc:
            print(f"error: {path}: {exc}", file=sys.stderr)
            failed = True
    for path, count in rows:
        print(f"{count}\t{path}")
    if args.output_csv:
        write_csv(args.output_csv, ["file", "particles"], rows)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
