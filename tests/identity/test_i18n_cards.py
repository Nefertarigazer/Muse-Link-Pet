"""A card's displayed labels remain authoritative across language changes."""
import json
from contextlib import contextmanager
import unittest
from unittest.mock import patch

from _support import Env, NOW, FakePc, flows, locale_mod
from muselink_id.ui import PopupUI, UiAnswer
from test_enroll_resume import Killed, ResumableUI, staged_then


class CardLanguageTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.close)
        self.env.add_agent('codex', 'whitelist')
        path, req = self.env.enroll_request('pet')
        self.env.route(path, req)
        self.path = self.env.bridge_file('pending', req + '.json')

    def run_card(self, ui):
        return flows.enroll(self.path, registry=self.env.registry, grants=self.env.grants,
                            ui=ui, pc=FakePc(), store=self.env.store, policy=self.env.policy, now_fn=lambda: NOW)

    @contextmanager
    def locale(self, tag):
        loc = locale_mod.Locale(tag, tag, 'zh' if tag.startswith('zh') else 'en')
        with patch.object(locale_mod, 'active', return_value=loc), patch.object(flows, 'active', return_value=loc):
            yield

    def test_resume_compares_saved_english_text_after_switch_to_traditional(self):
        with self.locale('en'), self.assertRaises(Killed):
            self.run_card(ResumableUI(staged_then('card_en', Killed())))
        with open(flows.card_state_path(self.path), encoding='utf-8') as stream:
            state = json.load(stream)
        self.assertEqual(state['rendered']['tag'], 'en')
        saved = state['rendered']['labels'][flows.ENROLL_WHITELIST]
        def answer(call):
            self.assertIn(saved, call['options'])
            self.assertEqual(call['resume']['popup_req'], 'card_en')
            return UiAnswer('answered', saved, 'card_en')
        with self.locale('zh-Hant'):
            outcome = self.run_card(ResumableUI(answer))
        self.assertTrue(outcome.proceed)
        self.assertEqual(self.env.registry.tier('pet'), 'whitelist')

    def test_old_card_without_snapshot_uses_simplified_labels(self):
        state = {'popup_req': 'old_card', 'staged_at': NOW}
        self.env.store.write_bytes(flows.card_state_path(self.path), json.dumps(state).encode())
        old_labels = locale_mod.labels('id.enroll.opt.', tag='zh-Hans')
        with self.locale('en'):
            outcome = self.run_card(ResumableUI(lambda call: UiAnswer('answered', old_labels[flows.ENROLL_ASK_FIRST], 'old_card')))
        self.assertEqual((outcome.proceed, self.env.registry.tier('pet')), (True, 'ask-first'))

    def test_generic_pet_check_never_becomes_a_button_answer(self):
        from subprocess import CompletedProcess
        out = 'STATUS:DONE\nREQ_ID:card_one\nKIND:pet_done\n---RESULT---\nAllow\n'
        self.assertEqual(PopupUI('/unused')._parse(CompletedProcess([], 0, out, ''), False).kind, 'closed')
