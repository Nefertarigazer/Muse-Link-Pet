"""MuseLink resident executor host (v4.2.0).

Runs in the user's interactive desktop session, listens on 127.0.0.1 only,
and executes one JSON request per connection:

    {"kind": "ping|info|ad|sem|uia|seq|script|shell", "job": "...",
     "token": "<executor-token.txt>", "deadline": <epoch s>, ...}

Changes against v3.2.1 (see docs/worklog and the 2026-11-12 review):
  * every request must carry the token from executor-token.txt (the file is
    created on first start and readable only by the current user + SYSTEM);
  * "shell" is off unless muselink.json enables it; "script" runs only the
    tool-dir scripts listed in muselink.json;
  * every request that produces desktop input (ad action subcommands, sem
    act, uia focus/invoke, act/act_if/uia steps inside seq, script/shell
    flagged "input") takes the input lock and yields to the user;
  * the lock is acquired with a timeout and requests past their deadline are
    dropped, so a caller that already gave up never gets a ghost action;
  * act "effect" compares disposition.delivery exactly ("delivered_unverified"
    no longer reads as verified); delivery_uncertain becomes status unknown;
  * press and expect.text are scoped to the target app;
  * seq prior_attempts keeps every failed attempt; events carry a timezone
    and no command text, and the event log rotates.

The host is meant to run WITHOUT elevation (see docs/install.en.md section 8).
"""
import ctypes
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime

VERSION = "4.2.0"
BASE = os.path.dirname(os.path.abspath(__file__))
CREATE_NO_WINDOW = 0x08000000


# ------------------------------------------------------------------ config
def load_config():
    path = os.path.join(BASE, "muselink.json")
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:  # a broken config must not take the host down
        sys.stderr.write(f"muselink.json unreadable: {e}\n")
        return {}


CFG = load_config()
EX = CFG.get("executor", {}) if isinstance(CFG, dict) else {}
if EX.get("agent_desktop_home"):
    os.environ["AGENT_DESKTOP_HOME"] = str(EX["agent_desktop_home"])
HOST = "127.0.0.1"
PORT = int(EX.get("port", 18744))
AD_EXE = EX.get("agent_desktop_exe") or os.path.join(
    os.environ.get("APPDATA", ""), r"npm\node_modules\agent-desktop\bin\agent-desktop-win32-x64.exe")
EVENTS = os.path.join(BASE, "executor-events.jsonl")
EVENTS_MAX_BYTES = int(EX.get("events_max_bytes", 5 * 1024 * 1024))
TOKEN_FILE = os.path.join(BASE, "executor-token.txt")
REQUIRE_TOKEN = bool(EX.get("require_token", True))
ALLOW_SHELL = bool(EX.get("allow_shell", False))
ALLOWED_SCRIPTS = set(EX.get("scripts", ["muse-shot.ps1", "file-facts.ps1"]))
MAX_REQUEST_BYTES = 4 * 1024 * 1024
SEM_SESSION = "sem"
REF_ACTIONS = {"click", "focus", "expand", "collapse", "toggle", "check", "uncheck", "scroll-to"}
TOKEN = ""

# agent-desktop subcommands that only read. Everything else is treated as
# desktop input (default-deny: an unknown or new subcommand is serialized and
# yields to the user rather than slipping past the courtesy rules).
AD_READ_ONLY = {"list-windows", "list-apps", "list-surfaces", "snapshot", "find", "get", "is",
                "screenshot", "status", "wait", "skills", "permissions", "version", "help",
                "--help", "-h", "--version", "clipboard-get", "session"}

# ----------------------------------------------------- input serialization
# Desktop-input flows run one at a time. RLock so a seq batch can hold it
# while its steps re-enter per-verb paths.
INPUT_LOCK = threading.RLock()
USER_IDLE_FLOOR = float(EX.get("user_idle_floor_s", 2.5))
USER_WAIT_CAP = float(EX.get("user_wait_cap_s", 25.0))
DEFAULT_LOCK_WAIT = 60.0


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def user_idle_seconds():
    try:
        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return None
        now = ctypes.windll.kernel32.GetTickCount()
        return ((now - lii.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return None


def remaining(req):
    """Seconds left before the caller's deadline, or None when unbounded."""
    d = (req or {}).get("deadline")
    try:
        return None if d is None else float(d) - time.time()
    except (TypeError, ValueError):
        return None


def wait_for_user_idle(req=None):
    """Return (waited_ms, user_still_active). Never waits past the cap or the
    request deadline. Note: GetLastInputInfo also counts our own synthetic
    input, so a preceding automated key can cause a short harmless wait."""
    t0 = time.time()
    cap = USER_WAIT_CAP
    rem = remaining(req)
    if rem is not None:
        cap = max(0.0, min(cap, rem - 1.0))
    while True:
        idle = user_idle_seconds()
        if idle is None or idle >= USER_IDLE_FLOOR:
            return int((time.time() - t0) * 1000), False
        if time.time() - t0 >= cap:
            return int((time.time() - t0) * 1000), True
        time.sleep(0.5)


class LockBusy(Exception):
    pass


class Expired(Exception):
    pass


class input_lock:
    """Context manager: acquire INPUT_LOCK within the request deadline and
    refuse to run work whose deadline has already passed."""

    def __init__(self, req):
        self.req = req
        self.held = False

    def __enter__(self):
        rem = remaining(self.req)
        wait = DEFAULT_LOCK_WAIT if rem is None else max(0.0, rem)
        if not INPUT_LOCK.acquire(timeout=wait):
            raise LockBusy()
        self.held = True
        rem = remaining(self.req)
        if rem is not None and rem <= 0:
            INPUT_LOCK.release()
            self.held = False
            raise Expired()
        return self

    def __exit__(self, *exc):
        if self.held:
            INPUT_LOCK.release()
        return False


def lock_failure(kind, exc):
    if isinstance(exc, LockBusy):
        result = {"status": "busy", "error": "INPUT_LOCK_TIMEOUT"}
    else:
        result = {"status": "expired", "error": "DEADLINE_PASSED"}
    emit("job_drop", kind, result["error"])
    return {"ok": False, "ms": 0, "out": json.dumps(result, ensure_ascii=False)}


# ------------------------------------------------------------------ events
_EMIT_LOCK = threading.Lock()


def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def emit(type_, job="", detail="", **extra):
    """Append one event line. Details carry the kind of work, never command
    text, typed text or clipboard contents: the pet and the inbox UI read
    this file and may put it on screen."""
    rec = {"ts": _now_iso(), "type": type_, "job": job, "detail": str(detail)[:160]}
    rec.update(extra)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with _EMIT_LOCK:
        try:
            if os.path.exists(EVENTS) and os.path.getsize(EVENTS) > EVENTS_MAX_BYTES:
                os.replace(EVENTS, EVENTS + ".1")
            with open(EVENTS, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass


def _app_of(argv):
    try:
        i = argv.index("--app")
        return str(argv[i + 1])
    except (ValueError, IndexError):
        return ""


# ---------------------------------------------------------------- ad runner
def ad(argv, timeout=30):
    """Run agent-desktop once; return (ok, parsed_json_or_raw_text)."""
    p = subprocess.run([AD_EXE] + [str(a) for a in argv], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout,
                       creationflags=CREATE_NO_WINDOW)
    out = p.stdout or ""
    try:
        return p.returncode == 0, json.loads(out)
    except Exception:
        return p.returncode == 0, out


def ad_error(data):
    if isinstance(data, dict):
        err = data.get("error") or {}
        return err.get("code", ""), err.get("message", "")
    return "", str(data)[:200]


def ad_is_input(argv):
    if not argv:
        return False
    if any(str(a) == "--headed" for a in argv):
        return True
    return str(argv[0]) not in AD_READ_ONLY


def query_args(q):
    argv = []
    for key, flag in (("name", "--name"), ("role", "--role"), ("value", "--value"),
                      ("text", "--text"), ("description", "--description"),
                      ("native_id", "--native-id")):
        if q.get(key):
            argv += [flag, str(q[key])]
    if q.get("exact"):
        argv.append("--exact")
    return argv


def surface_args(req):
    s = (req or {}).get("surface")
    return ["--surface", str(s)] if s else []


DECORATIVE_ROLES = {"image", "statictext", "text", "separator", "spacer"}


def classify(m):
    """operable: the CLI can act on it (has a ref). decorative: a visible
    label/icon, not an action target. no_ref: a real element without a ref."""
    if not isinstance(m, dict):
        return "decorative"
    if m.get("ref_id") or m.get("ref"):
        return "operable"
    role = (m.get("role") or m.get("control_type") or "").lower()
    if m.get("interactive") is False and role in DECORATIVE_ROLES:
        return "decorative"
    return "no_ref"


def path_of(m):
    p = m.get("path")
    return " > ".join(str(x) for x in p) if isinstance(p, list) else ""


# agent-desktop's live find must observe the whole window before it calls a
# match unique; it gives up after --timeout-ms (its own default: 5 s). Deep
# Chromium windows need longer (Kiro, 2026-11-14: 5 s and 15 s TIMEOUT, 60 s
# budget answered in 20.4 s). A longer budget only costs time on windows that
# need it; small windows answer as fast as before. Uniqueness is unchanged.
FIND_TIMEOUT_MS = 30000
FIND_TIMEOUT_MAX_MS = 50000
def find_budget(req):
    """--timeout-ms for one find: the request's find_timeout_ms (1000..50000)
    or 30 s, and never past the caller's deadline (5 s kept for the reply)."""
    v = (req or {}).get("find_timeout_ms")
    ms = v if type(v) is int and 1000 <= v <= FIND_TIMEOUT_MAX_MS else FIND_TIMEOUT_MS
    rem = remaining(req)
    if rem is not None:
        ms = min(ms, int((rem - 5) * 1000))
    return max(1000, ms)
def find_matches(app, q, req=None, limit=50):
    """Return (matches, error_code); matches is None when find itself failed."""
    budget = find_budget(req)
    argv = (["find", "--app", app, "--session", SEM_SESSION] + surface_args(req)
            + query_args(q) + ["--limit", str(limit), "--timeout-ms", str(budget)])
    ok, data = ad(argv, timeout=budget / 1000 + 15)
    if not (isinstance(data, dict) and isinstance(data.get("data"), dict)):
        code, _ = ad_error(data)
        return None, code
    matches = data["data"].get("matches", [])
    pc, pe = q.get("path_contains"), q.get("path_excludes")
    if pc or pe:
        def keep(m):
            p = path_of(m)
            return (not pc or pc in p) and (not pe or pe not in p)
        matches = [m for m in matches if keep(m)]
    return matches, ""


def find_count(app, q, req=None):
    """Return (count, error_code); count is None when the find itself failed."""
    budget = find_budget(req)
    ok, data = ad(["find", "--app", app, "--session", SEM_SESSION] + surface_args(req)
                  + ["--count", "--timeout-ms", str(budget)] + query_args(q), timeout=budget / 1000 + 15)
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        c = data["data"].get("count")
        if c is not None:
            return c, ""
    code, _ = ad_error(data)
    return None, code


def match_digest(m):
    if not isinstance(m, dict):
        return {"raw": str(m)[:200], "kind": "decorative"}
    d = {}
    for k in ("ref_id", "ref", "name", "role", "control_type", "bounds",
              "available_actions", "interactive"):
        if m.get(k) is not None:
            d[k] = m[k]
    if isinstance(m.get("path"), list):
        d["path"] = m["path"][-4:]
    d["kind"] = classify(m)
    return d


def count_gate(verb, app, q, req=None):
    count, code = find_count(app, q, req)
    if count is None:
        status = "fail" if code else "unknown"
        return None, {"status": status, "verb": verb, "error": code or "COUNT_FAILED"}
    return count, None


def delivery_value(data):
    """disposition.delivery from an agent-desktop envelope, exactly."""
    if not isinstance(data, dict):
        return None
    for holder in (data.get("data"), data.get("error")):
        if isinstance(holder, dict):
            disp = holder.get("disposition")
            if isinstance(disp, dict):
                return disp.get("delivery")
            if isinstance(disp, str):
                return disp
    return None


# ---------------------------------------------------------------- sem verbs
def sem_observe(req):
    app = req["app"]
    mode = req.get("mode", "skeleton")
    argv = ["snapshot", "--app", app, "--session", SEM_SESSION] + surface_args(req)
    if mode == "skeleton":
        argv.append("--skeleton")
    ok, data = ad(argv, timeout=45)
    if not ok or not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        code, msg = ad_error(data)
        return {"status": "fail", "verb": "observe", "error": code or "SNAPSHOT_FAILED", "message": msg}
    d = data["data"]
    result = {"status": "ok", "verb": "observe", "app": app, "mode": mode,
              "surface": req.get("surface") or "window",
              "snapshot_id": d.get("snapshot_id"), "complete": d.get("complete"),
              "ref_count": d.get("ref_count"), "nodes_observed": d.get("nodes_observed")}
    if mode == "digest":
        flat = []
        omitted = [0]
        noise = {"ScrollTo", "SetFocus", "Scroll"}

        def walk(node, trail):
            if not isinstance(node, dict) or len(flat) >= 120:
                return
            name = node.get("name") or ""
            actions = [a for a in (node.get("available_actions") or []) if a not in noise]
            role = node.get("role") or node.get("control_type") or ""
            if not actions and role in ("statictext", "text") and len(name) > 48:
                omitted[0] += 1
                name = ""
            if name or actions:
                entry = {"name": name[:48]}
                for k in ("role", "control_type"):
                    if node.get(k) is not None:
                        entry[k] = node[k]
                if actions:
                    entry["actions"] = actions
                entry["_path"] = " > ".join([t for t in trail if t][-2:])
                flat.append(entry)
            for child in node.get("children") or []:
                walk(child, trail + [name] if name else trail)

        walk(d.get("tree") or {}, [])
        freq = {}
        for e in flat:
            freq[e["name"]] = freq.get(e["name"], 0) + 1
        for e in flat:
            p = e.pop("_path", "")
            if p and (freq.get(e["name"], 0) > 1 or not e["name"]):
                e["path"] = p
        result["elements"] = flat
        result["elements_shown"] = len(flat)
        result["text_nodes_omitted"] = omitted[0]
    else:
        result["tree"] = d.get("tree")
    return result


def sem_resolve(req):
    app, q = req["app"], req.get("query", {})
    matches, code = find_matches(app, q, req)
    if matches is None:
        return {"status": "fail" if code else "unknown", "verb": "resolve", "error": code or "FIND_FAILED"}
    digests = [match_digest(m) for m in matches]
    operable = [d for d in digests if d["kind"] == "operable"]
    base = {"verb": "resolve", "count": len(matches), "operable_count": len(operable),
            "surface": req.get("surface") or "window"}
    if not matches:
        return dict(base, status="not_found")
    if not operable:
        return dict(base, status="not_operable", matches=digests[:8])
    if len(operable) == 1:
        return dict(base, status="resolved", match=operable[0])
    return dict(base, status="ambiguous", candidates=operable[:8],
                other_matches=len(matches) - len(operable))


def sem_verify(req):
    app, q = req["app"], req.get("query", {})
    should = req.get("should", "present")
    count, fail = count_gate("verify", app, q, req)
    if fail:
        return fail
    passed = (count >= 1) if should == "present" else (count == 0)
    return {"status": "ok" if passed else "fail", "verb": "verify", "should": should, "count": count}


def _recheck(app, q, req):
    try:
        again, _ = find_matches(app, q, req)
        if again is None:
            return {"status": "find_failed"}
        ops = [d for d in (match_digest(x) for x in again) if d["kind"] == "operable"]
        return {"count": len(again), "operable_count": len(ops),
                "status": "still_present" if ops else "gone_or_inoperable"}
    except Exception as e:
        return {"status": "error", "message": str(e)[:120]}


def sem_act(req):
    app, q = req["app"], req.get("query", {})
    action = req.get("action", "click")
    expect = req.get("expect") or {}
    nth = req.get("nth")
    matches, code = find_matches(app, q, req)
    surface = req.get("surface") or "window"
    if matches is None:
        return {"status": "fail" if code else "unknown", "verb": "act", "error": code or "FIND_FAILED"}
    digests = [match_digest(m) for m in matches]
    operable = [d for d in digests if d["kind"] == "operable"]
    if not matches:
        return {"status": "fail", "verb": "act", "error": "NOT_FOUND", "surface": surface}
    if not operable:
        return {"status": "fail", "verb": "act", "error": "NOT_OPERABLE", "count": len(matches),
                "matches": digests[:8], "surface": surface}
    if len(operable) > 1 and nth is None:
        return {"status": "fail", "verb": "act", "error": "AMBIGUOUS", "count": len(operable),
                "candidates": operable[:8], "surface": surface}
    if nth is not None and len(operable) <= nth:
        return {"status": "fail", "verb": "act", "error": "NTH_OUT_OF_RANGE", "count": len(operable)}
    m = operable[nth or 0]
    ref = m.get("ref_id") or m.get("ref")
    if action == "press":
        # Scoped to the target app: an unscoped press lands in whatever
        # window happens to be in front.
        ok, data = ad(["press", str(req.get("key", "return")), "--app", app, "--session", SEM_SESSION])
    elif action in REF_ACTIONS:
        ok, data = ad([action, ref, "--session", SEM_SESSION], timeout=20)
    else:
        return {"status": "fail", "verb": "act", "error": f"UNSUPPORTED_ACTION {action}"}
    dv = delivery_value(data)
    if not ok:
        code, msg = ad_error(data)
        status = "unknown" if (code in ("STALE_REF",) or dv not in (None, "not_delivered")) else "fail"
        result = {"status": status, "verb": "act", "error": code or "ACTION_FAILED",
                  "message": msg, "target": m}
        if dv is not None:
            result["delivery_value"] = dv
        if status == "unknown":
            result["recheck"] = _recheck(app, q, req)
        return result
    result = {"status": "ok", "verb": "act", "action": action, "target": m}
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        dd = data["data"]
        if dd.get("disposition") is not None:
            result["delivery"] = dd["disposition"]
        if dd.get("post_state") is not None:
            result["post_state"] = dd["post_state"]
    if dv is not None:
        result["delivery_value"] = dv
    if dv == "delivery_uncertain":
        result["status"] = "unknown"
        result["error"] = "DELIVERY_UNCERTAIN"
        result["effect"] = "uncertain"
        result["recheck"] = _recheck(app, q, req)
        return result
    if expect.get("text"):
        ok2, _ = ad(["wait", "--text", str(expect["text"]), "--app", app,
                     "--session", SEM_SESSION, "--timeout", "8000"], timeout=15)
        result["expect"] = {"text": expect["text"], "met": bool(ok2)}
        if not ok2:
            result["status"] = "fail"
            result["error"] = "EXPECT_TEXT_NOT_SEEN"
    elif expect.get("query"):
        c2, _ = find_count(app, expect["query"], req)
        should = expect.get("should", "present")
        met = (c2 is not None and c2 >= 1) if should == "present" else (c2 == 0)
        result["expect"] = {"should": should, "count": c2, "met": met}
        if not met:
            result["status"] = "fail"
            result["error"] = "EXPECT_NOT_MET"
    # "verified" only when an explicit expectation was met or the CLI's own
    # delivery value says so, compared exactly. Never upgraded from text.
    if result["status"] == "ok":
        if result.get("expect", {}).get("met") or dv == "delivered_verified":
            result["effect"] = "verified"
        else:
            result["effect"] = "delivered_unverified"
    return result


SEM_VERBS = {"observe": sem_observe, "resolve": sem_resolve, "verify": sem_verify, "act": sem_act}


def _run_sem(req):
    """Execute one sem verb (no envelope). Caller holds the lock if needed."""
    verb = req.get("verb", "")
    fn = SEM_VERBS.get(verb)
    if fn is None:
        return {"status": "fail", "error": f"unknown verb {verb}"}
    try:
        return fn(req)
    except subprocess.TimeoutExpired:
        return {"status": "unknown", "verb": verb, "error": "HOST_TIMEOUT"}
    except Exception as e:
        return {"status": "unknown", "verb": verb, "error": f"HOST_ERROR {e}"}


def _mark_wait(result, waited, active):
    if isinstance(result, dict):
        if waited:
            result["user_wait_ms"] = waited
        if active:
            result["user_active"] = True
    return result


def handle_sem(req):
    t0 = time.time()
    verb = req.get("verb", "")
    job = req.get("job", "") or f"sem:{verb}"
    detail = f"{verb} {req.get('app', '')}"

    def run():
        emit("job_begin", job, detail)
        if verb == "act":
            waited, active = wait_for_user_idle(req)
            return _mark_wait(_run_sem(req), waited, active)
        return _run_sem(req)

    if verb == "act":
        try:
            with input_lock(req):
                result = run()
        except (LockBusy, Expired) as e:
            return lock_failure(job, e)
    else:
        result = run()
    ms = int((time.time() - t0) * 1000)
    emit("job_end", job, f"status={result.get('status')} ms={ms}")
    return {"ok": result.get("status") in ("ok", "resolved"), "ms": ms,
            "out": json.dumps(result, ensure_ascii=False)}


# ---------------------------------------------------------------- uia kind
UIA_OPS = {"locate", "focus", "invoke", "read_text", "focused"}
UIA_INPUT_OPS = {"focus", "invoke"}
UIA_HELPER = os.path.join(BASE, "uia-helper.ps1")


def _run_uia(req):
    """Run the native-UIA helper with a hard timeout. Identity is re-located
    from semantic properties on every call; runtime ids are an intra-call
    identity check only. Returns the result dict."""
    op = req.get("op", "")
    if op not in UIA_OPS:
        return {"status": "fail", "error": f"unknown uia op {op}"}
    timeout = min(float(req.get("timeout", 20)), 30.0)
    rem = remaining(req)
    if rem is not None:
        timeout = max(1.0, min(timeout, rem))
    tag = f"{os.getpid()}-{int(time.time() * 1000)}"
    req_f = os.path.join(BASE, f"uia-req-{tag}.json")
    out_f = os.path.join(BASE, f"uia-out-{tag}.json")
    payload = {"op": op, "app": req.get("app", ""), "query": req.get("query", {}),
               "timeoutSec": int(timeout)}
    try:
        with open(req_f, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", UIA_HELPER, "-ReqFile", req_f, "-OutFile", out_f],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout + 5, creationflags=CREATE_NO_WINDOW)
        if os.path.exists(out_f):
            with open(out_f, encoding="utf-8-sig") as f:
                return json.load(f)
        return {"status": "unknown", "error": "UIA_NO_OUTPUT", "stderr": (p.stderr or "")[:300]}
    except subprocess.TimeoutExpired:
        return {"status": "unknown", "error": "UIA_TIMEOUT"}
    except Exception as e:
        return {"status": "unknown", "error": f"UIA_HOST_ERROR {e}"}
    finally:
        for f in (req_f, out_f):
            try:
                os.remove(f)
            except Exception:
                pass


def handle_uia(req):
    t0 = time.time()
    op = req.get("op", "")
    job = req.get("job", "") or "uia"

    def run():
        emit("job_begin", job, f"uia {op} {req.get('app', '')}")
        if op in UIA_INPUT_OPS:
            waited, active = wait_for_user_idle(req)
            return _mark_wait(_run_uia(req), waited, active)
        return _run_uia(req)

    if op in UIA_INPUT_OPS:
        try:
            with input_lock(req):
                result = run()
        except (LockBusy, Expired) as e:
            return lock_failure(job, e)
    else:
        result = run()
    ms = int((time.time() - t0) * 1000)
    emit("job_end", job, f"status={result.get('status')} ms={ms}")
    return {"ok": result.get("status") in ("ok", "resolved"), "ms": ms,
            "out": json.dumps(result, ensure_ascii=False)}


# ---------------------------------------------------------------- seq kind
def _step_yields(step):
    if step.get("kind") == "uia":
        return step.get("op") in UIA_INPUT_OPS
    return step.get("verb") in ("act", "act_if")


def _seq_step(step, req):
    """Run one batch step under the batch's lock; return its result dict.
    Steps that put input on the desktop yield to the user first."""
    rem = remaining(req)
    if rem is not None and rem <= 0:
        return {"status": "expired", "error": "DEADLINE_PASSED"}
    sub = dict(step)
    if "deadline" not in sub and req.get("deadline") is not None:
        sub["deadline"] = req["deadline"]
    verb = sub.get("verb", "")
    if verb == "sleep":
        ms = int(sub.get("ms", 0))
        time.sleep(ms / 1000.0)
        return {"status": "ok", "verb": "sleep", "slept_ms": ms}
    if verb == "act_if":
        # Act only when the `unless` query is absent (e.g. claim a reward only
        # when it does not already read as claimed).
        unless = sub.get("unless") or {}
        c, code = find_count(sub.get("app", ""), unless, sub)
        if c is None:
            return {"status": "fail", "verb": "act_if", "error": code or "COUNT_FAILED"}
        if c >= 1:
            return {"status": "ok", "verb": "act_if", "skipped": "condition_present", "unless_count": c}
        sub["verb"] = "act"
        sub.pop("unless", None)
        waited, active = wait_for_user_idle(sub)
        return _mark_wait(_run_sem(sub), waited, active)
    if sub.get("kind") == "uia":
        if sub.get("op") in UIA_INPUT_OPS:
            waited, active = wait_for_user_idle(sub)
            return _mark_wait(_run_uia(sub), waited, active)
        return _run_uia(sub)
    if verb == "act":
        waited, active = wait_for_user_idle(sub)
        return _mark_wait(_run_sem(sub), waited, active)
    return _run_sem(sub)


def handle_seq(req):
    """Composite batch: sem/uia steps executed locally, in order, under one
    input lock. A step may carry `alternatives` tried in order while the
    result is a failure. Stops at the first step whose final result is not
    ok/resolved."""
    t0 = time.time()
    job = req.get("job", "") or "seq"
    steps = req.get("steps") or []
    results = []
    status = "ok"
    stopped_at = None
    try:
        with input_lock(req):
            emit("job_begin", job, f"seq {len(steps)} steps")
            for i, step in enumerate(steps):
                attempts = [step] + list(step.get("alternatives") or [])
                result = None
                prior = []
                for j, att in enumerate(attempts):
                    clean = {k: v for k, v in att.items() if k != "alternatives"}
                    result = _seq_step(clean, req)
                    result["attempt"] = j
                    if result.get("status") in ("ok", "resolved"):
                        break
                    prior.append({"attempt": j, "status": result.get("status"),
                                  "error": result.get("error")})
                if result is not None and prior:
                    if result.get("status") in ("ok", "resolved"):
                        result["prior_attempts"] = prior        # every failure before the success
                    elif len(prior) > 1:
                        result["prior_attempts"] = prior[:-1]   # the last failure IS the result
                results.append(result)
                if result.get("status") not in ("ok", "resolved"):
                    status = "fail" if result.get("status") != "expired" else "expired"
                    stopped_at = i
                    break
    except (LockBusy, Expired) as e:
        return lock_failure(job, e)
    ms = int((time.time() - t0) * 1000)
    emit("job_end", job, f"status={status} ms={ms} stopped_at={stopped_at}")
    out = {"status": status, "verb": "seq", "steps": results, "stopped_at": stopped_at}
    return {"ok": status == "ok", "ms": ms, "out": json.dumps(out, ensure_ascii=False)}


# ------------------------------------------------- ad / script / shell kinds
def _run_process(argv, req, job, detail, is_input):
    t0 = time.time()
    timeout = float(req.get("timeout", 60))
    rem = remaining(req)
    if rem is not None:
        timeout = max(1.0, min(timeout, rem))

    def run():
        emit("job_begin", job, detail)
        waited, active = (0, False)
        if is_input:
            waited, active = wait_for_user_idle(req)
        try:
            p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout, creationflags=CREATE_NO_WINDOW)
            out = (p.stdout or "") + (("\n[stderr] " + p.stderr) if p.stderr else "")
            ok = p.returncode == 0
            status = "ok" if ok else "fail"
        except subprocess.TimeoutExpired:
            out, ok, status = f"TIMEOUT after {timeout}s", False, "unknown"
        except Exception as e:
            out, ok, status = f"HOST ERROR: {e}", False, "fail"
        return ok, status, out, waited, active

    if is_input:
        try:
            with input_lock(req):
                ok, status, out, waited, active = run()
        except (LockBusy, Expired) as e:
            return lock_failure(job, e)
    else:
        ok, status, out, waited, active = run()
    ms = int((time.time() - t0) * 1000)
    emit("job_end", job, f"ok={ok} ms={ms}")
    env = {"ok": ok, "ms": ms, "status": status, "out": out}
    if waited:
        env["user_wait_ms"] = waited
    if active:
        env["user_active"] = True
    return env


def handle_ad(req):
    argv = [str(a) for a in req.get("argv", [])]
    sub = argv[0] if argv else ""
    detail = f"ad {sub} {_app_of(argv)}".strip()
    return _run_process([AD_EXE] + argv, req, req.get("job", "") or f"ad:{sub}", detail, ad_is_input(argv))


def handle_script(req):
    name = os.path.basename(str(req.get("name", "")))
    path = os.path.join(BASE, name)
    if name not in ALLOWED_SCRIPTS or not name.lower().endswith(".ps1") or not os.path.isfile(path):
        return {"ok": False, "ms": 0, "status": "denied", "out": f"SCRIPT_NOT_ALLOWED {name}"}
    argv = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path] + \
        [str(a) for a in req.get("args", [])]
    return _run_process(argv, req, req.get("job", "") or f"script:{name}", f"script {name}",
                        bool(req.get("input")))


def handle_shell(req):
    if not ALLOW_SHELL:
        return {"ok": False, "ms": 0, "status": "denied",
                "out": "SHELL_DISABLED (enable executor.allow_shell in muselink.json)"}
    argv = [str(a) for a in req.get("argv", [])]
    if not argv:
        return {"ok": False, "ms": 0, "status": "fail", "out": "EMPTY_ARGV"}
    exe = os.path.basename(argv[0])
    return _run_process(argv, req, req.get("job", "") or f"shell:{exe}", f"shell {exe}",
                        bool(req.get("input")))


PHASE_MAX = 60
def handle_phase(req):
    """What Muse is doing now, in a few words, for the pet's bubble. Written
    through emit(), so it shares the host's lock and rotation; no desktop
    effect and no job, so it neither shows as work nor delays local agents."""
    text = req.get("text")
    if not isinstance(text, str):
        return {"ok": False, "ms": 0, "status": "fail", "out": "PHASE_TEXT_REQUIRED"}
    text = " ".join("".join(c if c.isprintable() else " " for c in text).split())[:PHASE_MAX]
    if not text:
        return {"ok": False, "ms": 0, "status": "fail", "out": "PHASE_TEXT_REQUIRED"}
    emit("phase", text=text)
    return {"ok": True, "ms": 0, "out": "phase"}
# ------------------------------------------------------------------ dispatch
def handle(req):
    kind = req.get("kind", "ad")
    if kind == "ping":
        return {"ok": True, "ms": 0, "out": "pong", "version": VERSION}
    if kind == "info":
        from i18n import active
        locale = active()
        return {"ok": True, "ms": 0, "out": json.dumps({
            "locale": {k: getattr(locale, k) for k in ("system", "ui", "docs")},
            "version": VERSION, "pid": os.getpid(), "allow_shell": ALLOW_SHELL,
            "scripts": sorted(ALLOWED_SCRIPTS), "require_token": REQUIRE_TOKEN})}
    if kind == "phase":
        return handle_phase(req)
    if kind == "sem":
        return handle_sem(req)
    if kind == "uia":
        return handle_uia(req)
    if kind == "seq":
        return handle_seq(req)
    if kind == "ad":
        return handle_ad(req)
    if kind == "script":
        return handle_script(req)
    if kind == "shell":
        return handle_shell(req)
    return {"ok": False, "ms": 0, "out": f"unknown kind {kind}"}


def authorized(req):
    if not REQUIRE_TOKEN:
        return True
    got = req.get("token")
    return isinstance(got, str) and bool(TOKEN) and hmac.compare_digest(got, TOKEN)


def client(conn):
    try:
        conn.settimeout(30)
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
            if len(buf) > MAX_REQUEST_BYTES:
                raise ValueError("request too large")
        conn.settimeout(None)
        if buf:
            req = json.loads(buf.decode("utf-8"))
            if not isinstance(req, dict):
                raise ValueError("request must be a JSON object")
            if not authorized(req):
                emit("auth_fail", "", f"kind={req.get('kind', '')}")
                resp = {"ok": False, "ms": 0, "status": "denied", "out": "AUTH_FAILED"}
            else:
                req.pop("token", None)
                resp = handle(req)
            conn.sendall((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
    except Exception as e:
        try:
            conn.sendall((json.dumps({"ok": False, "ms": 0, "out": f"HOST ERROR: {e}"}) + "\n").encode("utf-8"))
        except Exception:
            pass
    finally:
        conn.close()


# --------------------------------------------------------------- bootstrap
def ensure_token():
    """Load the shared secret, creating it on first start. The file is
    restricted to the current user and SYSTEM, which keeps other accounts and
    sandbox identities (they run under different SIDs) out of the executor."""
    global TOKEN
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            TOKEN = f.read().strip()
    except FileNotFoundError:
        TOKEN = ""
    if TOKEN:
        return
    TOKEN = secrets.token_hex(32)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(TOKEN)
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    who = f"{domain}\\{user}" if domain else user
    try:
        subprocess.run(["icacls", TOKEN_FILE, "/inheritance:r", "/grant:r",
                        f"{who}:F", "*S-1-5-18:F"], capture_output=True,
                       creationflags=CREATE_NO_WINDOW, timeout=20)
    except Exception as e:
        emit("token_acl_warn", "", str(e)[:120])


_SINGLETON_MUTEX = None  # held for the process lifetime once acquired


def serve():
    global _SINGLETON_MUTEX
    # Single-instance guard: a named mutex is the primary gate; the bind is
    # exclusive as a backstop (SO_REUSEADDR once let two hosts share the port
    # and split requests nondeterministically).
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        # Same mutex name as v3.x, so an old and a new host can never coexist
        # during an upgrade.
        _SINGLETON_MUTEX = k32.CreateMutexW(None, False, f"executor-host-singleton-{PORT}")
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            emit("host_refuse_dup", "", f"pid={os.getpid()} another instance holds the mutex")
            return
    except Exception:
        pass
    ensure_token()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.setsockopt(socket.SOL_SOCKET, -5, 1)  # SO_EXCLUSIVEADDRUSE
    except Exception:
        pass
    bound = False
    for _attempt in range(20):  # ride out a predecessor's sockets, never a live holder
        try:
            srv.bind((HOST, PORT))
            bound = True
            break
        except OSError:
            time.sleep(1)
    if not bound:
        emit("host_bind_fail", "", f"pid={os.getpid()} port {PORT} unavailable after retries")
        return
    emit("host_start", "", f"pid={os.getpid()} v{VERSION}")
    srv.listen(16)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=client, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    serve()
