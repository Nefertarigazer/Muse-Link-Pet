"""Shell paths of identity v3 (reviewer's tests, 2026-11-12).

The postman and the reply script run under Git Bash with shims: python3 ->
python, flock/systemctl stubs, a fake lib/muselink-common.sh and a fake
muselink-id.sh. Nothing touches a PC or a VM.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bash():
    for p in (shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"):
        if p and os.path.exists(p):
            return p
    return None


def _w(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


FAKE_COMMON = r'''#!/usr/bin/env bash
ML_VM_DIR="__VM__"
ML_BRIDGE_HOME="__TMP__/bridge"
PC_BRIDGE_DIR="__TMP__/pc"
ML_PC_BRIDGE_FWD="__TMP__/pc"
ML_STATE_DIR="__TMP__/state"
ml_fix_owner() { :; }
ml_valid_id() { [[ "${1:-}" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{2,95}$ ]]; }
ml_psq() { printf "'%s'" "$1"; }
ml_ps() {
  printf '%s\n' "$1" >> "__TMP__/ps.log"
  case "$1" in
    *Get-ChildItem*) ls "__TMP__/pc/inbox" 2>/dev/null; echo LIST_OK ;;
    *) : ;;
  esac
}
ml_scp_from() { cp "__TMP__/pc/inbox/$(basename "$1")" "$2"; }
ml_scp_to() { mkdir -p "$(dirname "$2")"; cp "$1" "$2"; }
'''


@unittest.skipUnless(_bash(), "bash is not available")
class ShellPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ml-idsh-")
        t = self.tmp.replace("\\", "/")
        self.vm = os.path.join(self.tmp, "vm")
        vm = self.vm.replace("\\", "/")
        for d in ("pc/inbox", "pc/outbox", "bridge/alerts", "state", "bin", "canned"):
            os.makedirs(os.path.join(self.tmp, d), exist_ok=True)
        _w(os.path.join(self.tmp, "bin", "python3"), '#!/usr/bin/env bash\nexec python "$@"\n', 0o755)
        _w(os.path.join(self.tmp, "bin", "flock"), "#!/usr/bin/env bash\nexit 0\n", 0o755)
        _w(os.path.join(self.tmp, "bin", "systemctl"), "#!/usr/bin/env bash\nexit 0\n", 0o755)   # service up
        _w(os.path.join(self.vm, "lib", "muselink-common.sh"),
           FAKE_COMMON.replace("__VM__", vm).replace("__TMP__", t))
        for rel in ("hooks/bridge-postman.sh", "hooks/hook-lib.sh", "hooks/selfheal-lib.sh",
                    "hooks/bridge-alert-watch.sh", "bridge/muse-bridge-reply.sh", "alert-ack.sh"):
            dst = os.path.join(self.vm, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(os.path.join(ROOT, "vm", *rel.split("/")), dst)
        self.env = dict(os.environ, MUSELINK_VM_DIR=self.vm, MUSELINK_HOOK_DRY_RUN="0",
                        PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
                        MUSELINK_HOOK_ADAPTER=os.path.join(self.tmp, "no-adapter.sh"),
                        PATH=os.path.join(self.tmp, "bin") + os.pathsep + os.environ.get("PATH", ""))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_sh(self, rel, *args):
        p = subprocess.run([_bash(), os.path.join(self.vm, *rel.split("/"))] + list(args), env=self.env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        return p.returncode, p.stdout, p.stderr

    def test_postman_one_request_per_wake_production_payload(self):
        # contract v2 (docs/protocol.en.md section 7): files that need no worker are
        # handled in the same tick; the first one that does wakes
        # codex_message with the production fields plus the identity fields.
        t = self.tmp.replace("\\", "/")
        # fake route: canned JSON per request id (+ the worker file for inbox
        # routes, as the real route writes it); fake reply script records calls
        _w(os.path.join(self.vm, "muselink-id.sh"),
           '#!/usr/bin/env bash\n[ "$1" = route ] || exit 2\ncat "%s/canned/$3.json"\n'
           '[ -f "%s/canned/$3.worker" ] && cp "%s/canned/$3.worker" "%s/bridge/inbox/$3.json"\nexit 0\n'
           % (t, t, t, t), 0o755)
        _w(os.path.join(self.vm, "bridge", "muse-bridge-reply.sh"),
           '#!/usr/bin/env bash\necho "$@" >> "%s/replies.log"\n' % t, 0o755)
        canned = {
            "pub_20261112_000001": {"route": "inbox", "code": "", "type": "publish-release", "decision": "ask_user",
                                    "reply_hint": "none", "agent_id": "codex"},
            "old_20261112_000002": {"route": "rejected", "code": "stale", "type": "", "decision": None,
                                    "reply_hint": "signed_error", "agent_id": "codex"},
            "enr_20261112_000003": {"route": "pending", "code": "", "type": "enroll", "decision": None,
                                    "reply_hint": "none", "agent_id": "newbot"},
            "msg_20261112_000004": {"route": "inbox", "code": "", "type": "message", "decision": "ask_user",
                                    "reply_hint": "none", "agent_id": "kiro"},
        }
        for rid, res in canned.items():
            _w(os.path.join(self.tmp, "canned", rid + ".json"), json.dumps(res) + "\n")
            _w(os.path.join(self.tmp, "pc", "inbox", rid + ".json"), "{}")
            if res["route"] == "inbox":
                _w(os.path.join(self.tmp, "canned", rid + ".worker"),
                   json.dumps({"req_id": rid, "type": res["type"], "created_at": "c", "text": "\u4f60\u597d",
                               "payload": {"k": 1}}, ensure_ascii=False))
        os.makedirs(os.path.join(self.tmp, "bridge", "inbox"), exist_ok=True)
        _w(os.path.join(self.tmp, "bridge", "alerts", "x.txt"), "possible impersonation\n")

        # the fake lists alphabetically: enr (pending), msg (wake) - old and pub wait
        rc, out, err = self.run_sh("hooks/bridge-postman.sh")
        self.assertTrue(out.startswith("WAKE: codex_message "), out + err)
        payload = json.loads(out.split(" ", 2)[2])
        self.assertEqual(payload["req_id"], "msg_20261112_000004")
        for k, v in (("type", "message"), ("created_at", "c"), ("text", "\u4f60\u597d"), ("payload", {"k": 1}),
                     ("agent_id", "kiro"), ("decision", "ask_user")):
            self.assertEqual(payload[k], v, k)
        self.assertTrue(payload["local_path"].endswith("/bridge/inbox/msg_20261112_000004.json"))
        self.assertTrue(payload["verdict_path"].endswith("/verdicts/msg_20261112_000004.json"))
        self.assertNotIn("alert", out)                            # alerts are bridge-alert-watch's job
        with open(os.path.join(self.tmp, "ps.log"), encoding="utf-8") as f:
            ps = f.read()
        self.assertRegex(ps, r"'pending'.*enr_20261112_000003")
        self.assertRegex(ps, r"'claimed'.*msg_20261112_000004")
        self.assertNotIn("old_20261112_000002", ps)               # untouched until the next tick

        for n in ("enr_20261112_000003.json", "msg_20261112_000004.json"):   # the fake does not move files
            os.remove(os.path.join(self.tmp, "pc", "inbox", n))
        rc, out, err = self.run_sh("hooks/bridge-postman.sh")
        self.assertTrue(out.startswith("SILENT: pipeline_owned "), out + err)   # old rejected, pub owned
        with open(os.path.join(self.tmp, "replies.log"), encoding="utf-8") as f:
            self.assertIn("old_20261112_000002 error", f.read())  # the signed error reply went out

    def test_postman_dry_run_changes_nothing(self):
        _w(os.path.join(self.tmp, "pc", "inbox", "codex_20261112_000009.json"), "{}")
        self.env["HATCH_HOOK_DRY_RUN"] = "1"
        self.env.pop("MUSELINK_HOOK_DRY_RUN", None)
        rc, out, err = self.run_sh("hooks/bridge-postman.sh")
        self.assertTrue(out.startswith("SILENT: dry_run_found "), out + err)
        ps_log = os.path.join(self.tmp, "ps.log")
        if os.path.exists(ps_log):
            with open(ps_log, encoding="utf-8") as f:
                self.assertNotIn("Move-Item", f.read())            # nothing claimed on the PC
        self.assertEqual(os.listdir(os.path.join(self.tmp, "state")), [])     # no heartbeat, no marker

    def alert_env(self, dry=False):
        e = dict(self.env)
        e["MUSELINK_HOOK_DRY_RUN"] = "1" if dry else "0"
        return e

    def run_alerts(self, dry=False, **extra):
        p = subprocess.run([_bash(), os.path.join(self.vm, "hooks", "bridge-alert-watch.sh")],
                           env=dict(self.alert_env(dry), **extra),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        return p.stdout + p.stderr

    def ack(self, *ids):
        p = subprocess.run([_bash(), os.path.join(self.vm, "alert-ack.sh"), *ids], env=self.env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        return p.returncode, (json.loads(p.stdout) if p.stdout.strip() else p.stderr)

    def test_alert_watch_history_is_baseline_and_one_wake_per_alert(self):
        a = os.path.join(self.tmp, "bridge", "alerts")
        _w(os.path.join(a, "old1.txt"), "old alert\n")
        seen = os.path.join(self.tmp, "state", "bridge-alert-watch.seen")
        self.assertIn("SILENT: baseline", self.run_alerts(dry=True))
        self.assertFalse(os.path.exists(seen))                       # dry run wrote nothing
        self.assertIn("SILENT: baseline", self.run_alerts())          # history never wakes
        self.assertIn("SILENT: no_new_alerts", self.run_alerts())
        _w(os.path.join(a, "codex_20261112_1.txt"), "possible impersonation of codex: bad_signature\n")
        os.utime(os.path.join(a, "codex_20261112_1.txt"), (1, 1))     # oldest of the new ones
        _w(os.path.join(a, "grant-g-1.txt"), "possible grant tampering\n")
        self.assertIn("WAKE: bridge_alert", self.run_alerts(dry=True))
        out = self.run_alerts()
        self.assertTrue(out.startswith("WAKE: bridge_alert "), out)
        p = json.loads(out.split(" ", 2)[2])
        self.assertEqual((p["alert_id"], p["more"]), ("codex_20261112_1", 1))
        self.assertIn("bad_signature", p["text"])
        self.assertEqual((p["attempt"], p["max_attempts"]), (1, 3))
        self.assertTrue(p["ack"].endswith("/alert-ack.sh codex_20261112_1"), p["ack"])
        self.assertIn('"alert_id": "grant-g-1"', self.run_alerts())
        self.assertIn("SILENT: awaiting_ack", self.run_alerts())       # both woken, neither confirmed
        self.assertEqual(self.ack("codex_20261112_1", "grant-g-1")[0], 0)
        self.assertIn("SILENT: no_new_alerts", self.run_alerts())
        self.assertEqual(sorted(os.listdir(a)), ["codex_20261112_1.txt", "grant-g-1.txt", "old1.txt"])  # never deleted

    def test_alert_watch_resends_until_confirmed_then_gives_up(self):
        a = os.path.join(self.tmp, "bridge", "alerts")
        fast = dict(MUSELINK_ALERT_RETRY_S="0", MUSELINK_ALERT_MAX_WAKES="2")
        self.assertIn("SILENT: baseline", self.run_alerts(**fast))
        _w(os.path.join(a, "lost.txt"), "a wake that never arrived\n")
        for attempt in (1, 2):
            out = self.run_alerts(**fast)
            self.assertTrue(out.startswith("WAKE: bridge_alert "), out)
            p = json.loads(out.split(" ", 2)[2])
            self.assertEqual((p["alert_id"], p["attempt"]), ("lost", attempt))
        self.assertEqual(self.ack("--list")[1]["unconfirmed"][0]["wakes"], 2)
        self.assertIn("SILENT: no_new_alerts", self.run_alerts(**fast))   # gave up after the last wake
        with open(os.path.join(self.tmp, "state", "bridge-alert-watch.log"), encoding="utf-8") as f:
            self.assertIn("gave_up (no ack after the last wake): lost.txt", f.read())
        # a confirmed alert is never woken again; the default retry waits
        _w(os.path.join(a, "seen-once.txt"), "reported\n")
        self.assertIn('"attempt": 1', self.run_alerts())
        self.assertIn("SILENT: awaiting_ack", self.run_alerts())          # 600 s have not passed
        rc, out = self.ack("seen-once", "seen-once", "nope", "../x")
        self.assertEqual(rc, 2)
        self.assertEqual((out["acked"], out["already"], out["unknown"]), (["seen-once"], ["seen-once"], ["nope", "../x"]))
        self.assertIn("SILENT: no_new_alerts", self.run_alerts(**fast))
        self.assertEqual(sorted(os.listdir(a)), ["lost.txt", "seen-once.txt"])   # files untouched

    def test_alert_ack_before_the_first_hook_run_keeps_history_quiet(self):
        a = os.path.join(self.tmp, "bridge", "alerts")
        _w(os.path.join(a, "old.txt"), "history\n")
        self.assertEqual(self.ack("--list")[1]["unconfirmed"], [])     # the ack script took the baseline
        self.assertIn("SILENT: no_new_alerts", self.run_alerts())

    def test_alert_watch_without_alert_dir_starts_from_an_empty_baseline(self):
        shutil.rmtree(os.path.join(self.tmp, "bridge", "alerts"))
        self.assertIn("SILENT: no_new_alerts", self.run_alerts())
        _w(os.path.join(self.tmp, "bridge", "alerts", "x.txt"), "first alert ever\n")
        self.assertIn("WAKE: bridge_alert", self.run_alerts())

    def test_reply_script_signs_replies_to_enrollments_and_rejected_v3(self):
        t = self.tmp.replace("\\", "/")
        _w(os.path.join(self.vm, "muselink-id.sh"),
           '#!/usr/bin/env bash\n[ "$1" = sign-reply ] || exit 2\n'
           'printf \'{"v":3,"signed":"sealed"}\' > "$3"\necho "$2" >> "%s/signed.log"\nexit 0\n' % t, 0o755)
        b = os.path.join(self.tmp, "bridge")
        _w(os.path.join(b, "processed", "enr_20261112_000005.json"),
           json.dumps({"inner": {"req_id": "enr_20261112_000005", "reply_protocol": 3}, "envelope": {}}))
        _w(os.path.join(b, "rejected", "rej_20261112_000006.json"),
           json.dumps({"v": 3, "req_id": "rej_20261112_000006", "agent_id": "codex", "signed": "{}", "auth": {}}))
        _w(os.path.join(b, "inbox", "leg_20261112_000007.json"),
           json.dumps({"req_id": "leg_20261112_000007", "reply_protocol": 2}))
        txt = os.path.join(self.tmp, "reply.txt")
        _w(txt, "hello")
        for rid in ("enr_20261112_000005", "rej_20261112_000006", "leg_20261112_000007"):
            rc, out, err = self.run_sh("bridge/muse-bridge-reply.sh", rid, "ok", txt)
            self.assertEqual(rc, 0, err)
        outbox = sorted(os.listdir(os.path.join(self.tmp, "pc", "outbox")))
        self.assertEqual(outbox, ["enr_20261112_000005.001.reply.json.tmp",
                                  "leg_20261112_000007.001.reply.json.tmp",
                                  "rej_20261112_000006.001.reply.json.tmp"])
        for rid in ("enr_20261112_000005", "rej_20261112_000006"):
            with open(os.path.join(self.tmp, "pc", "outbox", rid + ".001.reply.json.tmp"), encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"v": 3, "signed": "sealed"}, rid)
        with open(os.path.join(self.tmp, "pc", "outbox", "leg_20261112_000007.001.reply.json.tmp"), encoding="utf-8") as f:
            legacy = json.load(f)
        self.assertEqual((legacy["reply_protocol"], "agent_id" in legacy), (2, False))   # v2 stays unsigned


if __name__ == "__main__":
    unittest.main()
