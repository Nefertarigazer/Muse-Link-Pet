"""Local cards on the existing popup bus; Muse can replace a local card."""
from datetime import datetime, timezone
from i18n import t
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

from ._shared import agent_id, atomic, mutex, read_json, tool_dir


def display_name(base, agent):
    identity = read_json(tool_dir(base) / "identities" / (agent_id(agent) + ".json"), {})
    return str(identity.get("display_name") or agent).splitlines()[0][:80]


def ps(base, script, *args):
    return ["powershell.exe", "-STA", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(tool_dir(base) / script), *map(str, args)]


def _read(base, name):
    try:
        return (base / name).read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        return ""


def _spawn(command, **kwargs):
    child = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kwargs)
    # Hold/reap Popen handles without making an agent wait for a notification.
    threading.Thread(target=child.wait, daemon=True).start()
    return child


def _wait(base, req_id, expires_in):
    deadline = time.monotonic() + expires_in
    while True:
        # Claim only after checking ownership, under the same mutex as Muse's
        # staging/completer. The legacy waiter itself does not take that lock.
        with mutex(base):
            if _read(base, "popup-reqid.txt") != req_id:
                return {"status": "preempted", "kind": "preempted", "result": "", "req_id": req_id}
            done = _read(base, "popup-done.txt")
            if "REQ:" + req_id not in done.splitlines() and time.monotonic() >= deadline:
                # Complete via the production script, after releasing its mutex.
                expire = True
            else:
                expire = False
            if "REQ:" + req_id in done.splitlines():
                proc = subprocess.run(ps(base, "wait-popup.ps1", "-ReqId", req_id,
                                         "-TimeoutSeconds", 1), capture_output=True, encoding="utf-8-sig")
                header, sep, answer = proc.stdout.replace("\r\n", "\n").partition("---RESULT---\n")
                if proc.returncode == 0 and "STATUS:DONE" in header and sep:
                    kind = next((s[5:] for s in header.splitlines() if s.startswith("KIND:")), "")
                    return {"status": "expired" if kind == "expired" else "ok", "kind": kind,
                            "result": answer.removesuffix("\n"), "req_id": req_id}
                return {"status": "unknown", "error": "ANSWER_ALREADY_CLAIMED", "req_id": req_id}
        if expire:
            completed = subprocess.run(ps(base, "Complete-MuseWait.ps1", "-ReqId", req_id,
                                         "-Kind", "expired"), capture_output=True, timeout=20)
            if completed.returncode not in (0, 3):
                return {"status": "unknown", "error": "EXPIRY_COMPLETION_FAILED", "req_id": req_id}
        time.sleep(0.1)


def ask_user(agent, text, mode="input", options=None, expires_in=600, *, base=None, wait=True,
             silent=False, display=None):
    base = tool_dir(base)
    agent_id(agent)
    options = options or []
    if mode not in ("input", "choice", "info") or not 10 <= expires_in <= 3600:
        raise ValueError("mode must be input/choice/info; expires_in must be 10..3600")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text is required")
    if mode == "choice" and not 1 <= len(options) <= 4:
        raise ValueError("choice requires 1..4 options")
    if any(not isinstance(s, str) or not s.strip() or "\n" in s or "\r" in s for s in options):
        raise ValueError("options must be nonempty single lines")
    req_id = f"local-{agent}-{int(time.time())}-{secrets.token_hex(3)}"
    try:
        with mutex(base):
            if (base / "popup-pending.txt").exists():
                return {"status": "busy", "error": "POPUP_BUSY", "retry_after_s": 1}
            for name in ("popup-done.txt", "popup-result.txt", "popup-kind.txt", "popup-draft.txt"):
                (base / name).unlink(missing_ok=True)
            sender = str(display).splitlines()[0][:80] if display else display_name(base, agent)
            body = t("plugin.ask.body", sender=sender, text=text)
            for name, value in {"text": body, "mode": mode, "options": "\n".join(options),
                                "expires-in": str(expires_in), "reqid": req_id}.items():
                atomic(base / ("popup-" + name + ".txt"), value)
            atomic(base / "popup-armed.json", dict(req_id=req_id, mode=mode, summary=body.splitlines()[0],
                   at=datetime.now(timezone.utc).isoformat(), lane="local", agent=agent))
            atomic(base / "popup-pending.txt", req_id)
            # A child wrapper only mutes notification chimes in its own process.
            # popup.ps1 and shared popup-sound.txt stay byte-for-byte unchanged.
            command = ps(Path(__file__).parent, "_popup.ps1", "-ToolDir", base,
                         "-ReqId", req_id, "-Silent", str(silent or not wait).lower(),
                         "-PopupScript", base / "popup.ps1")
            _spawn(command)
    except TimeoutError:
        return {"status": "busy", "error": "POPUP_BUSY", "retry_after_s": 1}
    if wait:
        return _wait(base, req_id, expires_in)
    # Bounded per-card reaper, not a service: releases pending even if the
    # notifying agent exits, by using the same waiter/claim as ask_user.
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    _spawn([sys.executable, "-m", "plugin.ask", str(base), req_id, str(expires_in)], env=env)
    return {"status": "ok", "kind": "info", "req_id": req_id}


def notify_user(agent, text, expires_in=600, *, base=None):
    return ask_user(agent, text, "info", expires_in=expires_in, base=base, wait=False)


if __name__ == "__main__":
    _wait(tool_dir(sys.argv[1]), sys.argv[2], int(sys.argv[3]))
