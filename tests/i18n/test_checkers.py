import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from support import ROOT


def checker(name):
    spec = importlib.util.spec_from_file_location('_check_' + name, ROOT / 'tools' / ('check-' + name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CheckerTests(unittest.TestCase):
    def test_invalid_placeholders_options_and_plural_pairs_are_detected(self):
        module = checker('i18n')
        en = {'_meta': {'tag': 'en', 'name': 'English'}, 'plugin.card.opt.yes': 'Yes {agent}',
              'plugin.card.opt.no': 'No', 'mailbox.count_one': '{n} item'}
        other = {'_meta': {'tag': 'fr', 'name': 'Français'}, 'plugin.card.opt.yes': 'Same',
                 'plugin.card.opt.no': 'Same', 'mailbox.count_one': '{n} item'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for tag, pack in [('en', en), ('fr', other)]:
                (root / (tag + '.json')).write_text(json.dumps(pack), encoding='utf-8')
            findings = module.check(root)
            for expected in ('placeholder set', 'duplicate option', 'missing _other'):
                self.assertTrue(any(expected in row for row in findings), findings)
            (root / 'broken.json').write_bytes(b'\xff')
            self.assertTrue(any('broken.json' in row for row in module.check(root)))

    def test_pseudo_preserves_placeholders_and_expands_text(self):
        module = checker('i18n')
        pack = {'popup.text': 'A fairly long message for {agent} with {{literal}} braces'}
        generated = module.pseudo(pack)
        self.assertEqual(module.placeholders(pack['popup.text']), module.placeholders(generated['popup.text']))
        self.assertGreater(len(generated['popup.text']), len(pack['popup.text']) * 1.2)
        self.assertEqual(generated['_meta']['tag'], 'qps-ploc')

    def test_docs_check_pairs_duplicate_anchors_and_missing_targets(self):
        module = checker('docs')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            a = root / 'guide.zh.md'
            a.write_text('## Same\n## Same\n`[ignored](...)`\n[ok](#same-1)\n[bad](missing.txt)\n', encoding='utf-8')
            (root / 'guide.en.md').write_text('## One\n', encoding='utf-8')
            kinds = {kind for kind, _ in module.check(root)}
            self.assertEqual(kinds, {'heading-count', 'missing-target'})
            (root / 'guide.en.md').unlink()
            kinds = {kind for kind, _ in module.check(root)}
            self.assertEqual(kinds, {'missing-english', 'missing-target'})
