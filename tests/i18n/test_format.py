import unittest
from unittest.mock import patch

from support import VECTORS, core


class FormatVectors(unittest.TestCase):
    def test_both_sides_format_the_same_vectors(self):
        rows = VECTORS['format']
        for side in ('pc', 'vm'):
            module = core(side)
            for row in rows['cases']:
                with self.subTest(side=side, row=row), patch.object(module, '_pack', side_effect=lambda tag: rows['packs'].get(tag, {})), patch.object(module, 'active', return_value=module.Locale(row['tag'], row['tag'], 'en')):
                    value = module.plural(row['key'], row['n'], **row['args']) if 'n' in row else module.t(row['key'], **row['args'])
                    self.assertEqual(value, row['expected'])
                    bundle = module.bundle()
                    self.assertEqual(bundle['messages']['popup.only_en'], 'Fallback {name}')

    def test_missing_keys_are_logged_once_and_labels_preserve_keys(self):
        for side in ('pc', 'vm'):
            module = core(side)
            module._missing.clear()
            packs = {'en': {'plugin.card.opt.yes': 'Yes'}, 'zh-Hant': {}}
            with patch.object(module, '_pack', side_effect=lambda tag: packs.get(tag, {})), patch.object(module, 'active', return_value=module.Locale('zh-TW', 'zh-Hant', 'zh')):
                with self.assertLogs(module.__name__, 'WARNING') as logs:
                    self.assertEqual(module.t('plugin.card.opt.yes'), 'Yes')
                    self.assertEqual(module.t('plugin.card.opt.yes'), 'Yes')
                self.assertEqual(len(logs.output), 1)
                labels = module.labels('plugin.card.opt.')
                self.assertEqual(module.answer_key('Yes', labels), 'plugin.card.opt.yes')
                self.assertIsNone(module.answer_key('pet_done', labels))

    def test_format_rejects_attribute_access_and_positional_fields(self):
        for side in ('pc', 'vm'):
            for bad in ('{0}', '{name.value}', '{name!r}', '{name:>4}'):
                with self.subTest(side=side, bad=bad), self.assertRaises(ValueError):
                    core(side).format_message(bad, {'name': 'Muse'})
