"""Enrollment defect 2026-11-13 (RULES.en.md lesson table, 2026-11-13):
a click on "白名单" never completed, a request died with its waiter, a
restart did not resume. Kiro's regression tests for the fix.

    python -m unittest discover -s tests/identity -t tests/identity
"""
import os
import unittest

from _support import NOW, Env, FakePc, M, flows
from muselink_id import ui as ui_mod
from muselink_id.ui import UiAnswer


class Killed(BaseException):
    """Stands in for SIGTERM/SIGKILL: no except/finally clause of ours runs it down."""


class ResumableUI:
    """Like PopupUI with resume support. script: list of callables(call) -> UiAnswer."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def choice(self, text, options, timeout_s, expire=False, resume=None, on_staged=None):
        call = {"text": text, "options": options, "resume": resume, "on_staged": on_staged, "expire": expire}
        self.calls.append(call)
        return self.script.pop(0)(call)


def staged_then(popup_req, result):
    def step(call):
        call["on_staged"](popup_req)
        if isinstance(result, BaseException):
            raise result
        return result
    return step


class ParseRealWaitOutput(unittest.TestCase):
    """The root cause: popup-send.sh appends a status line after the answer."""

    def parse(self, out, rc=0):
        return ui_mod.PopupUI("/vm")._parse(ui_mod.subprocess.CompletedProcess([], rc, out, ""), True)

    def test_trailer_after_the_answer_is_not_part_of_it(self):
        out = ("STAGED req=req_1\nWAIT_STARTED:10:25:05\nWAIT_ENDED:10:25:47\nSTATUS:DONE\nREQ_ID:req_1\n"
               "DONE_AT:2026-11-13T10:25:47+08:00\nKIND:choice\n---RESULT---\n白名单\n"
               "DELIVERED req=req_1 (inflight cleared)\n")
        self.assertEqual(self.parse(out), UiAnswer("answered", "白名单", "req_1"))

    def test_clixml_progress_on_stderr_is_cut(self):
        out = "STATUS:DONE\nREQ_ID:req_2\n---RESULT---\n先问我\n#< CLIXML\n<Objs Version=\"1.1.0.1\">...\n"
        self.assertEqual(self.parse(out).answer, "先问我")

    def test_gbk_line_is_read_as_chinese(self):
        raw = "---RESULT---\n".encode() + "白名单\n".encode("gbk")
        self.assertIn("白名单", ui_mod._decode(raw))


class EnrollSurvivesItsWaiter(unittest.TestCase):
    def setUp(self):
        def mutate(d):
            d["enroll_limits"]["per_hour"] = 100
        self.env = Env(mutate)
        self.env.add_agent("codex", "whitelist")
        path, rid = self.env.enroll_request("pet", display_name="小助手")
        self.assertEqual(self.env.route(path, rid).route, "pending")
        self.ppath, self.rid = self.env.bridge_file("pending", rid + ".json"), rid
        self.clock = [NOW]

    def tearDown(self):
        self.env.close()

    def run_enroll(self, ui, pc):
        return flows.enroll(self.ppath, registry=self.env.registry, grants=self.env.grants, ui=ui, pc=pc,
                            store=self.env.store, policy=self.env.policy, now_fn=lambda: self.clock[0])

    def test_click_made_while_no_waiter_was_alive_still_enrolls(self):
        # waiter killed while the card is up (service restart)
        with self.assertRaises(Killed):
            self.run_enroll(ResumableUI(staged_then("req_card_A", Killed())), FakePc())
        self.assertTrue(os.path.exists(self.ppath), "request lost with its waiter")
        self.assertIsNone(self.env.registry.get("pet"))
        # restarted service: waits on the SAME card, the user's click is there
        ui, pc = ResumableUI(lambda call: UiAnswer("answered", flows.t(flows.ENROLL_WHITELIST), "req_card_A")), FakePc()
        out = self.run_enroll(ui, pc)
        self.assertEqual(ui.calls[0]["resume"]["popup_req"], "req_card_A")
        self.assertEqual((out.proceed, out.how, out.final), (True, "enrolled:whitelist", True))
        self.assertEqual(self.env.registry.tier("pet"), "whitelist")
        self.assertIn("pet", pc.identities)
        self.assertFalse(os.path.exists(self.ppath))
        self.assertFalse(os.path.exists(flows.card_state_path(self.ppath)))

    def test_crash_after_the_click_finishes_without_a_second_card(self):
        class DyingPc(FakePc):
            def write_identity(self, agent_id, record):
                raise Killed()
        ans = UiAnswer("answered", flows.t(flows.ENROLL_ASK_FIRST), "req_card_B")
        with self.assertRaises(Killed):
            self.run_enroll(ResumableUI(staged_then("req_card_B", ans)), DyingPc())
        ui, pc = ResumableUI(), FakePc()           # no card may be shown again
        out = self.run_enroll(ui, pc)
        self.assertEqual(ui.calls, [])
        self.assertEqual((out.proceed, out.how), (True, "enrolled:ask-first"))
        self.assertEqual(pc.identities["pet"]["key_id"], "pet#1")    # the same key, not a rekey

    def test_busy_card_slot_backs_off_and_retries_instead_of_dropping(self):
        out = self.run_enroll(ResumableUI(lambda call: UiAnswer("refused", "", "")), FakePc())
        self.assertEqual((out.proceed, out.final), (False, False))
        self.assertTrue(os.path.exists(self.ppath))
        self.assertFalse(flows.enroll_due(self.ppath, NOW))
        self.assertTrue(flows.enroll_due(self.ppath, NOW + flows.ENROLL_RETRY_S))

    def test_a_long_busy_slot_spends_no_attempts(self):
        refused = lambda call: UiAnswer("refused", "", "")
        for i in range(flows.ENROLL_MAX_ATTEMPTS + 3):            # run 4: stale/test cards held the slot
            out = self.run_enroll(ResumableUI(refused), FakePc())
            self.clock[0] += flows.ENROLL_RETRY_S
            self.assertEqual(out.final, False)
        ui = ResumableUI(lambda call: UiAnswer("answered", flows.t(flows.ENROLL_WHITELIST), "req_card_D"))
        self.assertEqual(self.run_enroll(ui, FakePc()).how, "enrolled:whitelist")
        # and it does not wait forever
        path, rid = self.env.enroll_request("pet2")
        self.env.route(path, rid)
        self.ppath = self.env.bridge_file("pending", rid + ".json")
        self.run_enroll(ResumableUI(refused), FakePc())
        self.clock[0] += flows.ENROLL_SLOT_WAIT_S
        out = self.run_enroll(ResumableUI(refused), FakePc())
        self.assertEqual((out.final, out.reason), (True, "slot_busy:gave_up"))

    def test_transient_failures_give_up_after_the_limit(self):
        for i in range(flows.ENROLL_MAX_ATTEMPTS):
            out = self.run_enroll(ResumableUI(lambda call: UiAnswer("error", "", "")), FakePc())
        self.assertEqual((out.proceed, out.final), (False, True))
        self.assertFalse(os.path.exists(self.ppath))

    def test_card_is_plain_language_and_expires_by_itself(self):
        ui = ResumableUI(lambda call: UiAnswer("answered", flows.t(flows.ENROLL_REJECT), "req_card_C"))
        out = self.run_enroll(ui, FakePc())
        text = ui.calls[0]["text"]
        self.assertTrue(ui.calls[0]["expire"])
        self.assertIn("登记", text)
        self.assertIn("代理自称：小助手", text)
        self.assertNotIn("报到", text)
        self.assertEqual((out.how, out.final), ("declined", True))


if __name__ == "__main__":
    unittest.main()
