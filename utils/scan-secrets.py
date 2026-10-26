#!/usr/bin/env python3
import runpy
from pathlib import Path
runpy.run_path(str(Path(__file__).resolve().parents[1] / "utilities/security/scan-secrets.py"), run_name="__main__")
