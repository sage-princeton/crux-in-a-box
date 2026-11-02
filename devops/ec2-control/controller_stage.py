"""Run the selected production controller configuration implementation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crux_devops.controller_box import main

if __name__ == '__main__':
    main()
