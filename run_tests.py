#!/usr/bin/env python3
"""Run the tests for every tool from the source checkout."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from cryosparc_io_tools.run_tests import main

if __name__ == "__main__":
    raise SystemExit(main())
