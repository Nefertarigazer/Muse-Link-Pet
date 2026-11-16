"""model.py: thread key, states, trust, release card (pure functions)."""
import unittest

from _mailbox import Mailbox  # noqa: F401  (also puts <repo>/pc on sys.path)
from mlindex import model


def rec(kind="request", dir="inbox", req_id="r1", seq=None, status="",
        final=False, text="", payload=None, type_="message", agent="codex",
        thread_id=None, path=None):
    return {
        "kind": kind,
        "dir": dir,
        "req_id": req_id,
        "seq": seq,
        "status": status,
        "final": final,
        "text": text,
        "payload": {} if payload is None else payload,
        "type": type_,
        "agent": agent,
        "from": agent,
        "thread_id": thread_id,
        "parse_error": None,
        "path": path or "%s/%s/%s" % (dir, req_id, seq),
    }


class Model(unittest.TestCase):
    def test_thread_key(self):
        """explicit thread_id wins; bad charset ignored; default <agent>/release|chat. Pass: table of cases."""
        cases = [
            (dict(type_="message", agent="codex", thread_id="t1"), "t1"),
            (dict(type_="message", agent="codex", thread_id="a.b:c/d-e_f"),
             "a.b:c/d-e_f"),
            (dict(type_="publish-release", agent="kiro"), "kiro/release"),
            (dict(type_="publish-voicebridge", from_="codex"), "codex/release"),
            (dict(type_="message", agent="workbuddy"), "workbuddy/chat"),
            (dict(type_="message", agent="", from_="v1agent"), "v1agent/chat"),
            (dict(type_="message", agent="codex", thread_id="bad id"), "codex/chat"),
            (dict(type_="message", agent="codex", thread_id="-leading"), "codex/chat"),
            (dict(type_="message", agent="codex", thread_id="ok\n"), "codex/chat"),
            (dict(type_="message", agent=""), "unknown/chat"),
        ]
        for kwargs, expected in cases:
            item = dict(kwargs)
            from_ = item.pop("from_", None)
            request = {"type": item.get("type_"), "thread_id": item.get("thread_id")}
            if item.get("agent"):
                request["agent"] = item["agent"]
            if from_:
                request["from"] = from_
            self.assertEqual(model.thread_key(request), expected, kwargs)

    def test_request_states(self):
        """each state of docs/protocol.en.md section 5 from its file set, incl. final_read. Pass: table of cases."""
        cases = [
            ("inbox only", [rec(dir="inbox")], "delivered", False),
            ("sent + claimed", [rec(dir="sent"), rec(dir="claimed")], "claimed", False),
            ("pending", [rec(dir="pending")], "claimed", False),
            ("interim", [rec(dir="claimed"), rec("reply", "outbox", seq=1, status="info")],
             "acked", False),
            ("final outbox", [rec(dir="claimed"), rec("reply", "outbox", seq=2,
             status="ok", final=True)], "replied", False),
            ("final archive", [rec(dir="claimed"), rec("reply", "archive", seq=2,
             status="error", final=True)], "replied", True),
            ("needs-user", [rec(dir="claimed"), rec("reply", "outbox", seq=2,
             status="needs-user", final=True)], "needs_user", False),
            ("needs_user spelling", [rec(dir="claimed"), rec("reply", "outbox", seq=2,
             status="needs_user", final=True)], "needs_user", False),
            ("rejected", [rec(dir="rejected")], "rejected", False),
            ("done + final", [rec(dir="done"), rec("reply", "archive", seq=1,
             status="ok", final=True)], "replied", True),
            ("done without reply", [rec(dir="done")], "replied", True),
            ("quarantine does not close", [rec(dir="claimed"),
             rec("reply", "quarantine", seq=1, status="ok", final=True)],
             "claimed", False),
            ("quarantine is not an ack", [rec(dir="claimed"),
             rec("reply", "quarantine", seq=1, status="info")], "claimed", False),
            ("higher final wins", [rec(dir="claimed"),
             rec("reply", "archive", seq=1, status="ok", final=True),
             rec("reply", "outbox", seq=2, status="needs-user", final=True)],
             "needs_user", False),
            # reviewer: muse-bridge-reply.sh --interim sends status ok with
            # final false; muse-call keeps waiting, so the index must too
            ("explicit interim ok", [rec(dir="claimed"),
             rec("reply", "archive", seq=1, status="ok", final=False)], "acked", False),
            ("v1 reply without final key", [rec(dir="claimed"),
             dict(rec("reply", "archive", seq=0, status="ok"), final=None)], "replied", True),
        ]
        for name, records, state, final_read in cases:
            self.assertEqual(model.request_state("r1", records),
                             (state, final_read), name)

    def test_thread_state_precedence(self):
        """waiting_user > active > done. Pass: table of cases."""
        self.assertEqual(model.thread_state([]), "done")
        self.assertEqual(model.thread_state(["replied"]), "done")
        self.assertEqual(model.thread_state(["rejected"]), "done")
        self.assertEqual(model.thread_state(["replied", "claimed"]), "active")
        self.assertEqual(model.thread_state(["acked"]), "active")
        self.assertEqual(model.thread_state(["delivered"]), "active")
        self.assertEqual(model.thread_state(["active-wrong-name", "needs_user"]),
                         "waiting_user")
        self.assertEqual(model.thread_state(["claimed"], pending_enroll=True),
                         "waiting_user")

    def test_trust_and_release_card(self):
        """archive/outbox/quarantine -> trust; publish-* -> card fields. Pass: exact values."""
        self.assertEqual(model.trust(rec("reply", "archive")), "read")
        self.assertEqual(model.trust(rec("reply", "outbox")), "unread_unverified")
        self.assertEqual(model.trust(rec("reply", "quarantine")), "rejected_by_client")
        self.assertIsNone(model.trust(rec(dir="inbox")))
        self.assertIsNone(model.trust(rec("ack", "ack")))

        payload = {
            "version": "1.4.0",
            "package_path": "D:\\MyApp\\out\\VB-1.4.0.zip",
            "package_size": 123,
            "sha256": "a" * 64,
            "setup_path": "D:\\MyApp\\out\\setup.exe",
            "setup_size": 45,
            "setup_sha256": "b" * 64,
            "test": True,
            "notes": "ignored by the card",
        }
        request = rec(type_="publish-release", payload=payload)
        final = {"status": "ok", "final": True}
        self.assertEqual(model.release_card(request, final), {
            "version": "1.4.0",
            "package_path": "D:\\MyApp\\out\\VB-1.4.0.zip",
            "package_size": 123,
            "sha256": "a" * 64,
            "setup_path": "D:\\MyApp\\out\\setup.exe",
            "setup_size": 45,
            "setup_sha256": "b" * 64,
            "test": True,
            "status": "ok",
        })
        self.assertIsNone(model.release_card(rec(type_="message", payload=payload), final))
        card = model.release_card(rec(type_="publish-release", payload=payload), None)
        self.assertEqual(card["status"], "")


if __name__ == "__main__":
    unittest.main()
