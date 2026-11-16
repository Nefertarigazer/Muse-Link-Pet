import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from support import core


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.pc = core('pc')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ui-language.txt'
        for name, value in [('preference_path', self.path), ('system_tag', 'en-US')]:
            override = patch.object(self.pc, name, return_value=value)
            override.start()
            self.addCleanup(override.stop)
        self.pc.active.cache_clear()
        self.addCleanup(self.pc.active.cache_clear)

    def test_languages_come_from_available_packs_and_native_names(self):
        languages = self.pc.languages()
        self.assertEqual([row['tag'] for row in languages], self.pc.available())
        for row in languages:
            self.assertEqual(set(row), {'tag', 'name'})
            self.assertEqual(row['name'], self.pc._pack(row['tag'])['_meta']['name'])

    def test_written_preference_changes_active_and_bundle_without_cache_clear(self):
        self.assertEqual(self.pc.active().ui, 'en')
        for tag in ('zh-Hant', 'zh-Hans', 'en'):
            with self.subTest(tag=tag):
                bundle = self.pc.set_language(tag)
                self.assertEqual(self.path.read_text(encoding='utf-8'), tag + '\n')
                self.assertEqual(self.pc.active().ui, tag)
                self.assertEqual(self.pc.active().system, 'en-US')
                self.assertEqual(bundle, self.pc.bundle())
                self.assertEqual(bundle['tag'], tag)
                self.assertEqual(bundle['font'], self.pc._pack(tag)['_meta']['font'])
                self.assertEqual(list(self.path.parent.glob('*.tmp')), [])
        self.pc.active.cache_clear()
        self.assertEqual(self.pc.active().ui, 'en')

    def test_missing_preference_falls_back_to_system_language(self):
        with patch.object(self.pc, 'system_tag', return_value='zh-TW'):
            self.assertEqual(self.pc.active().ui, 'zh-Hant')
            self.pc.set_language('en')
            self.path.unlink()
            self.assertEqual(self.pc.active().ui, 'zh-Hant')

    def test_external_atomic_replacement_is_observed(self):
        self.pc.set_language('en')
        external = self.path.with_suffix('.tmp')
        external.write_text('zh-Hant\n', encoding='utf-8')
        os.replace(external, self.path)
        self.assertEqual(self.pc.active().ui, 'zh-Hant')
        self.pc.active.cache_clear()
        self.assertEqual(self.pc.active().ui, 'zh-Hant')

    def test_invalid_or_unavailable_tags_do_not_change_preference(self):
        self.pc.set_language('zh-Hans')
        for tag in (None, True, 1, [], {}, '', '../en', 'en\n', 'EN', 'en-US', 'fr'):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                self.pc.set_language(tag)
            self.assertEqual(self.path.read_text(encoding='utf-8'), 'zh-Hans\n')
            self.assertEqual(self.pc.active().ui, 'zh-Hans')
            self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_invalid_unreadable_or_unsupported_preference_uses_system(self):
        for content in (b'../en', b'fr', b'\xff', b''):
            with self.subTest(content=content):
                self.path.write_bytes(content)
                self.assertEqual(self.pc.active().ui, 'en')
        with patch.object(Path, 'read_text', side_effect=PermissionError):
            self.assertEqual(self.pc.active().ui, 'en')

    def test_failed_replace_preserves_old_preference_and_removes_temporary_file(self):
        self.pc.set_language('en')
        replace = os.replace
        def fail(source, destination):
            self.assertEqual(Path(source).parent, self.path.parent)
            self.assertEqual(destination, self.path)
            self.assertEqual(self.path.read_text(encoding='utf-8'), 'en\n')
            self.assertEqual(Path(source).read_text(encoding='utf-8'), 'zh-Hans\n')
            raise PermissionError('locked preference')
        with patch.object(self.pc.os, 'replace', side_effect=fail), self.assertRaises(PermissionError):
            self.pc.set_language('zh-Hans')
        self.assertIs(os.replace, replace)
        self.assertEqual(self.pc.active().ui, 'en')
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])
