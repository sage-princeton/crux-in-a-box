"""Reuse the installed SDK research loop and review gates without copying them."""

import importlib.util
import sys
from pathlib import Path

import crux_scaffold
from crux_scaffold.errors import InvalidDropInError

source = Path(crux_scaffold.__file__).resolve().parents[1] / "examples/tabpfn/tabpfn_extensions.py"
if not source.is_file():
    raise InvalidDropInError("The pilot requires the SDK source tree with its TabPFN research example")
spec = importlib.util.spec_from_file_location("crux_drop_in.tabpfn_extensions", source)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
