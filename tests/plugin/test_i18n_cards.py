from unittest.mock import patch

from plugin_support import Sandbox
from plugin import gate
import i18n


class LocalizedConsentTests(Sandbox):
    def test_choice_and_typed_answers_use_the_actual_displayed_labels(self):
        for tag in ('en', 'zh-Hans', 'zh-Hant'):
            for kind in ('choice', 'typed', 'pet_typed', 'pet_done'):
                loc = i18n.Locale(tag, tag, 'zh' if tag.startswith('zh') else 'en')
                with self.subTest(tag=tag, kind=kind), patch.object(i18n, 'active', return_value=loc) as current:
                    def answer(agent, text, mode, options, **kwargs):
                        displayed = options[0]
                        # Simulate a language change after the card was shown.
                        current.return_value = i18n.Locale('en', 'en', 'en')
                        return dict(status='ok', kind=kind, result=displayed)
                    decision = gate.enforce(self.base, 'codex', 'act', 'input', ['App'], 'Target', ask_fn=answer)
                    self.assertEqual(decision['decision'], 'deny' if kind == 'pet_done' else 'allow')

    def test_a_key_name_or_a_different_languages_label_is_not_consent(self):
        with patch.object(i18n, 'active', return_value=i18n.Locale('en', 'en', 'en')):
            wrong = i18n.labels('plugin.consent.opt.', tag='zh-Hans')[gate.ALLOW_ONCE]
            for value in (gate.ALLOW_ONCE, wrong):
                verdict = gate.enforce(self.base, 'codex', 'act', 'input', ['App'], 'Target',
                                       ask_fn=lambda *a, value=value, **k: dict(status='ok', kind='typed', result=value))
                self.assertEqual(verdict['decision'], 'deny')
