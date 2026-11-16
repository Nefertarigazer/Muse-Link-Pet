import os
import importlib.util
import json
from pathlib import Path
from subprocess import CompletedProcess
import sys
import tempfile
import unittest
from unittest.mock import patch

from support import ROOT, VECTORS, core


class ResolveVectors(unittest.TestCase):
    def test_executor_info_advertises_the_cached_pc_locale(self):
        spec = importlib.util.spec_from_file_location('_i18n_executor_test', ROOT / 'pc/executor-host.py')
        host = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(host)
        pc = core('pc')
        with patch.dict(sys.modules, {'i18n': pc}), patch.object(pc, 'active', return_value=pc.Locale('zh-TW', 'zh-Hant', 'zh')):
            info = json.loads(host.handle({'kind': 'info'})['out'])
        self.assertEqual(info['locale'], dict(system='zh-TW', ui='zh-Hant', docs='zh'))

    def test_vm_reads_pc_info_once_and_caches_a_transport_failure_as_english(self):
        vm = core('vm')
        try:
            for ok, expected in ((True, 'zh-Hant'), (False, 'en')):
                vm.pc_locale.cache_clear()
                vm.active.cache_clear()
                reply = dict(ok=ok, out=json.dumps(dict(locale=dict(system='zh-TW', ui='zh-Hant', docs='zh'))))
                with patch('subprocess.run', return_value=CompletedProcess([], 0 if ok else 1, json.dumps(reply), '')) as run:
                    self.assertEqual(vm.active().ui, expected)
                    self.assertEqual(vm.active().ui, expected)
                    self.assertEqual(run.call_count, 1)
        finally:
            vm.pc_locale.cache_clear()
            vm.active.cache_clear()

    def test_pc_resolves_and_vm_consumes_every_vector(self):
        pc, vm = core('pc'), core('vm')
        for row in VECTORS['resolve']:
            with self.subTest(system=row['system'], available=row['available']):
                loc = pc.resolve(row['system'], row['available'])
                self.assertEqual((loc.system, loc.ui, loc.docs), (row['system'], row['ui'], row['docs']))
                # VM does not resolve/detect a language. Test the same contract
                # by supplying the actual PC executor info result.
                with patch.object(vm, 'pc_locale', return_value=dict(system=loc.system, ui=loc.ui, docs=loc.docs)), patch.object(vm, 'available', return_value=row['available']), patch.object(vm, '_pack', return_value={}):
                    vm.active.cache_clear()
                    mirrored = vm.active()
                    self.assertEqual((mirrored.system, mirrored.ui, mirrored.docs), (loc.system, loc.ui, loc.docs))
        vm.active.cache_clear()

    def test_override_is_cached_and_empty_override_is_not_ignored(self):
        pc = core('pc')
        preference = patch.object(pc, '_preference', return_value=None)
        preference.start()
        self.addCleanup(preference.stop)
        try:
            with patch.dict(os.environ, {'MUSELINK_LOCALE': 'zh-TW'}):
                pc.active.cache_clear()
                self.assertEqual(pc.active().ui, 'zh-Hant')
                os.environ['MUSELINK_LOCALE'] = 'en-US'
                self.assertEqual(pc.active().ui, 'zh-Hant')
            with patch.dict(os.environ, {'MUSELINK_LOCALE': ''}):
                pc.active.cache_clear()
                self.assertEqual(pc.active().ui, 'en')
        finally:
            pc.active.cache_clear()

    def test_documents_use_the_other_edition_only_when_needed(self):
        for side in ('pc', 'vm'):
            module = core(side)
            with tempfile.TemporaryDirectory() as directory:
                base = Path(directory) / 'GUIDE'
                zh = base.with_name('GUIDE.zh.md')
                en = base.with_name('GUIDE.en.md')
                zh.write_text('Chinese', encoding='utf-8')
                with patch.object(module, 'active', return_value=module.Locale('en-US', 'en', 'en')):
                    self.assertEqual(module.doc_path(base), zh)
                    en.write_text('English', encoding='utf-8')
                    self.assertEqual(module.doc_path(base), en)
                    readme = Path(directory) / 'README'
                    self.assertEqual(module.doc_path(readme).name, 'README.md')
