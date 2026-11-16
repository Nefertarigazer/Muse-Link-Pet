import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
VECTORS = json.loads((ROOT / 'spec/test-vectors/i18n.json').read_text(encoding='utf-8'))


def core(side):
    name = '_test_muselink_' + side + '_i18n'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / side / 'i18n/__init__.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]
