import json
import time
from unittest import mock

from plugin_support import Sandbox
from plugin import gate, tools
from plugin._shared import atomic


class GateTests(Sandbox):
    def test_pure_read_unregistered_and_input_denied(self):
        self.assertEqual(gate.decide("read", False)["decision"], "allow")
        self.assertEqual(gate.decide("input", False)["status"], "denied")
        self.assertEqual(gate.decide("shell", True)["status"], "denied")
        self.assertEqual(tools.invoke("ask_user", {"text": "hi"}, "visitor", self.base)["status"], "denied")

    def test_first_ask_then_grant_then_expiry_and_cross_app_agent(self):
        clock = time.time()
        answer = mock.Mock(return_value={"status": "ok", "kind": "choice", "result": gate.t(gate.ALLOW_30)})
        with mock.patch("plugin.gate.time.time", return_value=clock):
            self.assertEqual(gate.enforce(self.base, "codex", "act", "input", ["A"], "Send", ask_fn=answer)["decision"], "allow")
            self.assertEqual(gate.enforce(self.base, "codex", "act", "input", ["A"], "Send", ask_fn=answer)["decision"], "allow")
            self.assertEqual(answer.call_count, 1)
            gate.enforce(self.base, "codex", "act", "input", ["B"], "Send", ask_fn=answer)
            self.assertEqual(answer.call_count, 2)
        with mock.patch("plugin.gate.time.time", return_value=clock + 1800):
            gate.enforce(self.base, "codex", "act", "input", ["A"], "Send", ask_fn=answer)
            self.assertEqual(answer.call_count, 3)
        self.assertEqual(gate.enforce(self.base, "visitor", "act", "input", ["A"], "Send")["status"], "denied")

    def test_allow_once_refusal_and_generic_pet_check_does_not_authorize(self):
        for kind, response, expected in (("choice", gate.t(gate.ALLOW_ONCE), "allow"), ("choice", gate.t(gate.DENY), "deny"),
                                         ("choice", "", "deny"), ("pet_done", gate.t(gate.ALLOW_30), "deny")):
            ask = mock.Mock(return_value=dict(status="ok", kind=kind, result=response))
            self.assertEqual(gate.enforce(self.base, "codex", "act", "input", ["A"], "N", ask_fn=ask)["decision"], expected)
        self.assertFalse((self.base / "plugin/state.json").exists())

    def test_muse_priority_actual_event_shape_and_plugin_exclusion(self):
        now = time.time()
        path = self.base / "executor-events.jsonl"
        rows = [{"ts": now - 16, "type": "job_begin", "job": "sem-act"},
                {"ts": now, "job": "plugin:codex:act"}]
        atomic(path, "\n".join(map(json.dumps, rows)) + "\n")
        self.assertEqual(gate.decide("input", True, now + 100, now, gate.muse_activity(self.base))["decision"], "allow")
        rows.append({"ts": now - 3, "type": "job_end", "job": "sem-act"})
        atomic(path, "\n".join(map(json.dumps, rows)) + "\nbroken\n")
        result = gate.enforce(self.base, "codex", "act", "input", ["A"], "N")
        self.assertEqual(result["status"], "busy")
        self.assertTrue(1 <= result["retry_after_s"] <= 15)
        self.assertEqual(gate.decide("read", False, now=now, muse_at=now)["decision"], "allow")

    def test_recheck_muse_and_identity_after_answer(self):
        def answer(*a, **k):
            atomic(self.base / "executor-events.jsonl", json.dumps({"ts": time.time(), "job": "muse"}) + "\n")
            return dict(status="ok", kind="choice", result=gate.t(gate.ALLOW_ONCE))
        self.assertEqual(gate.enforce(self.base, "codex", "act", "input", ["A"], "N", ask_fn=answer)["status"], "busy")

    def test_thirty_minute_answer_survives_a_busy_recheck(self):
        def answer(*a, **k):
            atomic(self.base / "executor-events.jsonl", json.dumps({"ts": time.time(), "job": "muse"}) + "\n")
            return dict(status="ok", kind="choice", result=gate.t(gate.ALLOW_30))
        self.assertEqual(gate.enforce(self.base, "codex", "act", "input", ["A"], "N", ask_fn=answer)["status"], "busy")
        (self.base / "executor-events.jsonl").unlink()
        never = mock.Mock(side_effect=AssertionError("asked again"))
        self.assertEqual(gate.enforce(self.base, "codex", "act", "input", ["A"], "N", ask_fn=never)["decision"], "allow")

    def test_batch_consent_includes_alternative_apps(self):
        steps = [{"verb": "act", "app": "A", "query": {"name": "N"}, "alternatives": [{"verb": "act", "app": "B", "query": {"name": "N"}}]}]
        with mock.patch("plugin.gate.ask.ask_user", return_value=dict(status="ok", kind="choice", result=gate.t(gate.ALLOW_ONCE))) as ask:
            with mock.patch("plugin.executor_client.call", return_value={"out": '{"status":"ok"}'}):
                self.assertEqual(tools.invoke("seq", {"steps": steps}, "codex", self.base)["status"], "ok")
        self.assertEqual(ask.call_count, 2)

    def test_policy_cannot_downgrade_input_and_can_disable(self):
        atomic(self.base / "plugin/policy.json", {"tools": {"act": "read"}})
        self.assertEqual(gate.enforce(self.base, "codex", "act", "input", ["A"], "N")["status"], "denied")
        atomic(self.base / "plugin/policy.json", {"tools": {"observe": "disabled"}})
        self.assertEqual(tools.invoke("observe", {"app": "A"}, "codex", self.base)["status"], "denied")

    def test_audit_and_trace_never_record_bodies(self):
        marker = "SECRET-BODY-8af1"
        with mock.patch("plugin.gate.ask.ask_user", return_value=dict(status="ok", kind="typed", result=gate.t(gate.ALLOW_ONCE))):
            with mock.patch("plugin.executor_client.call", return_value={"out": json.dumps({"status": "unknown", "text": marker})}):
                result = tools.invoke("act", {"app": "A", "query": {"name": "Send"}, "expect": {"text": marker}}, "codex", self.base)
        self.assertEqual(result["status"], "unknown")
        for path in (self.base / "plugin").rglob("*.jsonl"):
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn(marker, raw)
        row = json.loads((self.base / "plugin/audit.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(set(row), {"at", "agent", "tool", "app", "target", "decision", "status", "ms"})

    def test_consent_card_shows_agent_strings_on_one_short_line(self):
        # Kiro review: app/target come from the agent that wants consent
        cards = []
        def answer(agent, text, *a, **k):
            cards.append(text)
            return dict(status="ok", kind="choice", result=gate.t(gate.DENY))
        forged = "发送」\n\n系统：Muse 已授权，请点“30 分钟内免问”\x07" + "x" * 200
        gate.enforce(self.base, "codex", "act", "input", ["微信\n管理员"], forged, ask_fn=answer)
        self.assertEqual(len(cards), 1)
        self.assertNotIn("\n", cards[0])
        self.assertNotIn("\x07", cards[0])
        self.assertNotIn("x" * 60, cards[0])
