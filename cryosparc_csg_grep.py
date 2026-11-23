#!/usr/bin/env python3
"""Run the .csg YAML grep tool directly from the source checkout."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from cryosparc_io_tools.cryosparc_csg_grep import main

if __name__ == "__main__":
    raise SystemExit(main())
