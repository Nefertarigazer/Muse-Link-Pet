"""Expose the VM pack module under an unambiguous package name.

PC and VM modules can coexist in a test runner without sharing i18n globals.
The release layout keeps the actual packs and implementation in vm/i18n/.
"""
import importlib.util
from pathlib import Path
import sys

_spec = importlib.util.spec_from_file_location(__name__, Path(__file__).resolve().parents[1] / "i18n/__init__.py")
_module = importlib.util.module_from_spec(_spec)
sys.modules[__name__] = _module
_spec.loader.exec_module(_module)
