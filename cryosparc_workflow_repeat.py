#!/usr/bin/env python3
"""Run the repeat tool directly from the source checkout, without installing."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from cryosparc_io_tools.cryosparc_workflow_repeat import main

if __name__ == "__main__":
    raise SystemExit(main())
