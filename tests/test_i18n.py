"""Include i18n tests in the existing root discovery without shadowing pc/i18n."""
from pathlib import Path
import unittest


def load_tests(loader, tests, pattern):
    start = str(Path(__file__).parent / 'i18n')
    return unittest.TestLoader().discover(start, top_level_dir=start)
