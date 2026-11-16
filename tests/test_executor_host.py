"""Unit tests for pc/executor-host.py logic that does not need a desktop.

    python -m unittest discover -s tests

Every agent-desktop / subprocess call is stubbed; nothing touches the real
event log (emit is replaced) or the real executor.
"""
import importlib.util
import json
import os
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "pc", "executor-host.py")


def load_host():
    spec = importlib.util.spec_from_file_location("executor_host_under_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.emit = lambda *a, **k: None
    return mod


class StateHomeTests(unittest.TestCase):
    def test_direct_python_entry_uses_site_home(self):
        config = json.dumps({"executor": {"agent_desktop_home": "X:/site-state"}})
        with mock.patch.dict(os.environ, {"AGENT_DESKTOP_HOME": "X:/old-state"}):
            with mock.patch("builtins.open", mock.mock_open(read_data=config)):
                load_host()
            self.assertEqual(os.environ["AGENT_DESKTOP_HOME"], "X:/site-state")

    def test_existing_environment_survives_without_site_home(self):
        with mock.patch.dict(os.environ, {"AGENT_DESKTOP_HOME": "X:/existing-state"}):
            with mock.patch("builtins.open", mock.mock_open(read_data="{}")):
                load_host()
            self.assertEqual(os.environ["AGENT_DESKTOP_HOME"], "X:/existing-state")


class PhaseTests(unittest.TestCase):
    def test_phase_is_one_cleaned_event_without_a_job(self):
        eh = load_host()
        events = []
        eh.emit = lambda *a, **k: events.append((a, k))
        self.assertTrue(eh.handle({"kind": "phase", "text": "  正在\n整理\x00周报  " + "长" * 80})["ok"])
        (args, kw), = events
        self.assertEqual(args, ("phase",))
        self.assertEqual(kw["text"], ("正在 整理 周报 " + "长" * 80)[:60])
        for bad in (None, 3, "", " \n\t "):
            self.assertFalse(eh.handle({"kind": "phase", "text": bad})["ok"])
        self.assertEqual(len(events), 1)

    def test_phase_line_is_what_the_pet_reads(self):
        import tempfile
        eh = load_host()
        d = tempfile.mkdtemp()
        eh.EVENTS = os.path.join(d, "executor-events.jsonl")
        spec = importlib.util.spec_from_file_location("host_emit", SRC)
        real = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(real)
        real.EVENTS = eh.EVENTS
        eh.emit = real.emit
        eh.handle({"kind": "phase", "text": "正在打包"})
        with open(eh.EVENTS, encoding="utf-8") as f:
            rec = json.loads(f.readline())
        self.assertEqual((rec["type"], rec["text"], rec["job"]), ("phase", "正在打包", ""))


class FindBudgetTests(unittest.TestCase):
    def test_default_override_and_deadline_cap(self):
        eh = load_host()
        self.assertEqual(eh.find_budget({}), 30000)
        self.assertEqual(eh.find_budget({"find_timeout_ms": 45000}), 45000)
        for bad in (999, 50001, "30000", True, 3.5):
            self.assertEqual(eh.find_budget({"find_timeout_ms": bad}), 30000)
        near = eh.find_budget({"deadline": time.time() + 12})
        self.assertTrue(6000 <= near <= 7000, near)              # 5 s kept for the reply
        self.assertEqual(eh.find_budget({"deadline": time.time() + 2}), 1000)

    def test_find_and_count_pass_the_budget_and_wait_for_it(self):
        eh = load_host()
        calls = []
        def fake_ad(argv, timeout=30):
            calls.append((argv, timeout))
            return True, {"ok": True, "data": {"matches": [], "count": 0}}
        eh.ad = fake_ad
        eh.find_matches("Kiro.exe", {"name": "Minimize"}, {"find_timeout_ms": 40000})
        eh.find_count("Kiro.exe", {"name": "Minimize"}, {})
        (find, t1), (count, t2) = calls
        self.assertEqual(find[find.index("--timeout-ms") + 1], "40000")
        self.assertEqual(count[count.index("--timeout-ms") + 1], "30000")
        self.assertIn("--count", count)
        self.assertEqual((t1, t2), (55, 45))                      # the subprocess outlives the budget


class EffectTests(unittest.TestCase):
    def act_with_delivery(self, delivery):
        eh = load_host()
        eh.find_matches = lambda app, q, req=None, limit=50: ([{"ref_id": "@s1:e1", "name": "x", "role": "button"}], "")
        eh.ad = lambda argv, timeout=30: (True, {"ok": True, "data": {"disposition": {"delivery": delivery, "retry": "unsafe"}}})
        return eh.sem_act({"app": "X", "query": {"name": "x"}, "action": "click"})

    def test_unverified_is_not_upgraded(self):
        r = self.act_with_delivery("delivered_unverified")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["effect"], "delivered_unverified")

    def test_verified_needs_exact_value(self):
        self.assertEqual(self.act_with_delivery("delivered_verified")["effect"], "verified")

    def test_uncertain_becomes_unknown(self):
        eh_r = self.act_with_delivery("delivery_uncertain")
        self.assertEqual(eh_r["status"], "unknown")
        self.assertEqual(eh_r["effect"], "uncertain")

    def test_press_is_scoped_to_app(self):
        eh = load_host()
        seen = {}
        eh.find_matches = lambda app, q, req=None, limit=50: ([{"ref_id": "@s1:e1"}], "")

        def fake_ad(argv, timeout=30):
            seen["argv"] = argv
            return True, {"ok": True, "data": {}}
        eh.ad = fake_ad
        eh.sem_act({"app": "Notepad.exe", "query": {"name": "x"}, "action": "press", "key": "return"})
        self.assertIn("--app", seen["argv"])
        self.assertIn("Notepad.exe", seen["argv"])


class SeqTests(unittest.TestCase):
    def test_prior_attempts_keeps_every_failure_before_success(self):
        eh = load_host()
        eh.wait_for_user_idle = lambda req=None: (0, False)
        results = iter([{"status": "fail", "error": "E0"}, {"status": "fail", "error": "E1"}, {"status": "ok"}])
        eh._run_sem = lambda req: dict(next(results))
        out = json.loads(eh.handle_seq({"steps": [{"verb": "act", "alternatives": [{"verb": "act"}, {"verb": "act"}]}]})["out"])
        errors = [p["error"] for p in out["steps"][0]["prior_attempts"]]
        self.assertEqual(errors, ["E0", "E1"])

    def test_every_input_step_yields_to_the_user(self):
        eh = load_host()
        calls = []
        eh.wait_for_user_idle = lambda req=None: (calls.append(1) or (0, False))
        eh._run_sem = lambda req: {"status": "ok"}
        eh._run_uia = lambda req: {"status": "ok"}
        eh.find_count = lambda app, q, req=None: (0, "")
        eh.handle_seq({"steps": [{"verb": "act_if", "app": "A", "unless": {"name": "done"}},
                                 {"kind": "uia", "op": "focus"},
                                 {"verb": "act"},
                                 {"verb": "verify"}]})
        self.assertEqual(len(calls), 3)

    def test_act_if_skip_does_not_wait(self):
        eh = load_host()
        calls = []
        eh.wait_for_user_idle = lambda req=None: (calls.append(1) or (0, False))
        eh.find_count = lambda app, q, req=None: (1, "")
        out = json.loads(eh.handle_seq({"steps": [{"verb": "act_if", "app": "A", "unless": {"name": "done"}}]})["out"])
        self.assertEqual(out["steps"][0].get("skipped"), "condition_present")
        self.assertEqual(calls, [])


class GateTests(unittest.TestCase):
    def test_token_check(self):
        eh = load_host()
        eh.TOKEN = "secret"
        eh.REQUIRE_TOKEN = True
        self.assertFalse(eh.authorized({"token": "nope"}))
        self.assertFalse(eh.authorized({}))
        self.assertTrue(eh.authorized({"token": "secret"}))

    def test_expired_request_is_dropped(self):
        eh = load_host()
        env = eh.handle_sem({"verb": "act", "app": "A", "query": {"name": "x"}, "deadline": time.time() - 1})
        self.assertEqual(json.loads(env["out"])["status"], "expired")

    def test_shell_disabled_by_default(self):
        eh = load_host()
        eh.ALLOW_SHELL = False
        self.assertEqual(eh.handle_shell({"argv": ["cmd", "/c", "echo"]})["status"], "denied")

    def test_script_allow_list(self):
        eh = load_host()
        self.assertEqual(eh.handle_script({"name": "..\\evil.ps1"})["status"], "denied")

    def test_ad_input_classification(self):
        eh = load_host()
        self.assertFalse(eh.ad_is_input(["list-windows"]))
        self.assertFalse(eh.ad_is_input(["snapshot", "--app", "X"]))
        self.assertTrue(eh.ad_is_input(["click", "@s1:e1"]))
        self.assertTrue(eh.ad_is_input(["restore", "--app", "X"]))
        self.assertTrue(eh.ad_is_input(["find", "--headed"]))
        self.assertTrue(eh.ad_is_input(["some-new-subcommand"]))


if __name__ == "__main__":
    unittest.main()
