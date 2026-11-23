#!/usr/bin/env python3
"""Run the test suite for every tool and print a per-tool summary."""
from __future__ import annotations

import argparse
import io
import sys
import unittest
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(
        prog="run_tests.py", description="Run the tests for all cryosparc-io-tools scripts.")
    parser.add_argument("tools", nargs="*", metavar="TOOL",
                        help="Only run tests whose name contains one of these (e.g. cs_count)")
    parser.add_argument("-v", "--verbose", action="store_true", help="List every test as it runs")
    args = parser.parse_args(argv)

    tests_dir = Path(__file__).resolve().parents[2] / "tests"
    if not tests_dir.is_dir():
        print(f"error: tests directory not found at {tests_dir} "
              "(run from a source checkout, or an editable install)", file=sys.stderr)
        return 2
    files = sorted(tests_dir.glob("test_*.py"))
    if args.tools:
        files = [f for f in files if any(t in f.stem for t in args.tools)]
    if not files:
        print("error: no test files match", file=sys.stderr)
        return 2

    summary: list[tuple[str, int, int, str]] = []
    for file in files:
        stream = sys.stderr if args.verbose else io.StringIO()
        suite = unittest.TestLoader().discover(str(tests_dir), pattern=file.name)
        result = unittest.TextTestRunner(stream=stream, verbosity=2 if args.verbose else 1).run(suite)
        bad = len(result.failures) + len(result.errors)
        if bad and not args.verbose:
            sys.stderr.write(stream.getvalue())
        summary.append((file.stem.removeprefix("test_"), result.testsRun, bad,
                        "FAIL" if bad or result.testsRun == 0 else "ok"))

    width = max(len(name) for name, *_ in summary)
    print(f"{'tool'.ljust(width)}  tests  failed  status")
    for name, run, bad, status in summary:
        print(f"{name.ljust(width)}  {run:5d}  {bad:6d}  {status}")
    return 0 if all(status == "ok" for *_, status in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
