"""Test the legacy release approval gate without importing a live pipeline."""
import ast
import os
import re
import unittest
from unittest.mock import patch

from support import ROOT, core


class Refused(Exception):
    pass


class ReleaseCardTests(unittest.TestCase):
    def gate(self, tag, *, approve='', decline='', pet_done=False):
        vm = core('vm')
        source = ast.parse((ROOT / 'vm/bridge/pipelines/publish-release.py').read_text(encoding='utf-8'))
        function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'approval_gate')
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            # Return precisely the label shown, after a simulated locale change.
            current.return_value = vm.Locale('en', 'en', 'en')
            return 0, 'KIND:' + ('pet_done' if pet_done else 'pet_typed') + '\n---RESULT---\n' + argv[2], ''
        namespace = dict(APPROVAL='popup', APPROVAL_TIMEOUT=1, APPROVE_LABEL=approve,
                         DECLINE_LABEL=decline, labels=vm.labels, t=vm.t, answer_key=vm.answer_key,
                         os=os, re=re, run=run, POPUP_ASK='fixture', Refuse=Refused)
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<approval_gate>', 'exec'), namespace)
        with patch.object(vm, 'active', return_value=vm.Locale(tag, tag, 'en')) as current:
            try:
                result = namespace['approval_gate']({'from': 'Codex'}, '1.2.3', [('test.zip', 1024, 'a'*64)], 'D:\\out\\test.zip')
            except Refused:
                result = 'denied'
        return result, calls

    def test_labels_come_from_each_pack_and_the_shown_card_is_authoritative(self):
        for tag in ('en', 'zh-Hans', 'zh-Hant'):
            result, calls = self.gate(tag)
            self.assertEqual(result, 'popup')
            self.assertEqual(calls[0][2], core('vm').labels('release.approval.opt.', tag=tag)['release.approval.opt.approve'])

    def test_config_overrides_empty_defaults_duplicate_labels_and_generic_check(self):
        result, calls = self.gate('zh-Hant', approve='Ship it')
        self.assertEqual((result, calls[0][2]), ('popup', 'Ship it'))
        self.assertEqual(self.gate('en', approve='Same', decline='Same')[0], 'denied')
        self.assertEqual(self.gate('en', pet_done=True)[0], 'denied')
