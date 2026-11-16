"""inbound - route ONE request file fetched from the PC inbox.

Called by vm/hooks/bridge-postman.sh through `muselink-id route <file>
<req_id>` for every new PC inbox file. The postman only moves the PC file
(claimed\\ or rejected\\) and builds the wake message from the JSON line this
prints; every security decision is made here.

Requirements
  1. Order (docs/protocol.en.md section 3): read_envelope_file -> legacy / enroll /
     v3 branch -> verify -> replay check -> decide() with request-level
     facts (facts = {} here; enforcement points re-decide with measured
     facts) -> write files -> audit. Any unexpected exception -> route
     "rejected" with code "internal" and an alert (fail closed).
  2. Routes and files ($ML_BRIDGE_HOME = store.bridge_home):
       inbox      inbox/<id>.json = envelope.worker_request(...) (or the
                  legacy request unchanged, plus "auth": {"verified": false,
                  "mode": "legacy_unsigned"}); envelopes/<id>.json = the
                  original bytes; verdicts/<id>.json = RouteResult as JSON
       pending    enroll requests: pending/<id>.json (inner + original
                  envelope); bridge-service runs flows.enroll() later
       rejected   rejected/<id>.json = original bytes; verdict as well
       duplicate  nothing written except an audit line (the earlier copy
                  is already routed; the PC file is still moved to claimed)
  3. Replay store (store.db() table seen): insert (req_id, agent_id,
     digest, now, route) in one transaction. Same req_id + same digest ->
     "duplicate"; same req_id + different digest -> reject "conflict" with
     an alert. Legacy and enroll requests are recorded too (digest = SHA-256
     of the file bytes). prune_seen() with policy.seen_retention_s runs at
     most once per hour.
  4. Legacy (no "v"): legacy_unsigned == "reject" -> reject
     "legacy_disabled"; the v1 "from" equals a registered agent_id ->
     reject "downgrade" + alert; otherwise route inbox with auth
     "legacy_unsigned".
  5. Enroll: envelope.parse_enroll(); rate limit policy.enroll_limits.
     per_hour (count enroll_log rows in the last hour; over the limit ->
     reject "enroll_rate_limited", no card); otherwise pending. An enroll
     for an existing agent_id is still pending (flows shows a re-key card
     with a warning).
  6. Alerts: an AuthError whose code is in IMPERSONATION_CODES while the
     outer agent_id is registered -> store.alert(req_id, "possible
     impersonation of <agent_id>: <code>"). Alert text never includes
     request text.
  7. reply_hint in the result tells the postman/service whether to send a
     reply: "none" for auth failures (steps 4-6), "signed_error" for
     post-auth rejects (bad_inner, stale, future, conflict), "unsigned_error"
     for legacy / enroll rejects.
  8. Output (stdout of `muselink-id route`): exactly one JSON line =
     RouteResult._asdict(); no request text in it.
"""

import hashlib
import json
import os
import re
import time
from collections import namedtuple

from . import (
    AuthError, IMPERSONATION_CODES, PolicyError, current_type,
)
from .policy import decide
from . import envelope

RouteResult = namedtuple(
    "RouteResult",
    "route req_id agent_id auth code type decision reason grant_id alert reply_hint",
)

# Failures after the signature was verified (docs/protocol.en.md section 3, steps
# 7-8): the sender is genuine, so it gets a signed error reply. Inside
# verify_request, id_mismatch can only come from step 7 (the file-name
# check, step 2, happens in read_envelope_file).
POST_AUTH_CODES = frozenset({"bad_inner", "stale", "future", "id_mismatch"})
_SAFE_TYPE = re.compile(r"[^a-z0-9_-]")


def _legacy_type(value):
    # v1 "type" is free text from an unauthenticated file and ends up in the
    # wake message; keep only the inert charset the old postman allowed.
    # A retired type name becomes the current one here, once for everything
    # downstream (postman, resident service, pipeline, wake message).
    return current_type(_SAFE_TYPE.sub("", str(value or "").lower())[:40])


def _legacy_label(value):
    # same for the self-declared v1 "from" (RouteResult.agent_id -> wake message)
    return re.sub(r"[^A-Za-z0-9_.-]", "", str(value or ""))[:40]


def _raw(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return b""


def _write(store, name, filename, data):
    d = store.ensure_bridge_dir(name)
    store.write_bytes(os.path.join(d, filename), data)


def _write_json(store, name, filename, obj):
    _write(store, name, filename, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))


def _registered(registry, agent_id):
    if not agent_id:
        return False
    try:
        return registry.get(agent_id) is not None
    except AuthError as e:
        return e.code == "registry_tampered"
    except Exception:
        return False


def _prune(store, policy, now):
    marker = store.path(".prune")
    try:
        if os.path.exists(marker) and now - os.path.getmtime(marker) < 3600:
            return
    except OSError:
        pass
    conn = store.db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM seen WHERE first_seen < ?", (now - int(policy.seen_retention_s),))
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
    finally:
        conn.close()
    try:
        with open(marker, "a"):
            pass
        os.utime(marker, (now, now))
    except OSError:
        pass


def _replay(store, policy, req_id, agent_id, digest, now, route):
    conn = store.db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT digest FROM seen WHERE req_id=?", (req_id,)).fetchone()
        if row is not None:
            conn.execute("COMMIT")
            return "duplicate" if row[0] == digest else "conflict"
        conn.execute("INSERT INTO seen(req_id,agent_id,digest,first_seen,route) VALUES(?,?,?,?,?)",
                     (req_id, agent_id, digest, now, route))
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()
    _prune(store, policy, now)
    return "new"


def _forget(store, req_id, now):
    try:
        conn = store.db()
        try:
            conn.execute("DELETE FROM seen WHERE req_id=? AND first_seen=?", (req_id, now))
            conn.execute("DELETE FROM enroll_log WHERE req_id=? AND at=?", (req_id, now))
        finally:
            conn.close()
    except Exception:
        pass


def _result(route, req_id, agent_id="", auth="failed", code="", type_="", decision=None,
            reason=None, grant_id=None, alert=False, reply_hint="none"):
    return RouteResult(route, req_id, agent_id, auth, code, type_, decision, reason,
                       grant_id, alert, reply_hint)


def _audit(store, now, res, digest=""):
    try:
        store.audit("route", now, req_id=res.req_id, agent_id=res.agent_id, route=res.route,
                    code=res.code, decision=res.decision, reason=res.reason, digest=digest)
    except Exception:
        pass


def route_request(path, file_req_id, *, store, registry, policy, grants, now):
    raw = _raw(path)
    try:
        kind, outer = envelope.read_envelope_file(path, file_req_id, policy.max_envelope_bytes)
    except AuthError as e:
        res = _result("rejected", file_req_id, "", "failed", e.code)
        _write(store, "rejected", file_req_id + ".json", raw)
        _write_json(store, "verdicts", file_req_id + ".json", res._asdict())
        _audit(store, now, res)
        return res
    except Exception:
        res = _result("rejected", file_req_id, "", "failed", "internal", alert=True)
        _write(store, "rejected", file_req_id + ".json", raw)
        _write_json(store, "verdicts", file_req_id + ".json", res._asdict())
        try:
            store.alert(file_req_id, "internal error while reading a request")
        except Exception:
            pass
        _audit(store, now, res)
        return res

    agent_id = outer.get("agent_id") if isinstance(outer.get("agent_id"), str) else ""

    try:
        if kind == "legacy":
            return _route_legacy(path, file_req_id, outer, raw, store, registry, policy, grants, now)
        if kind == "enroll":
            return _route_enroll(path, file_req_id, outer, raw, store, registry, policy, grants, now)
        return _route_v3(path, file_req_id, outer, raw, store, registry, policy, grants, now)
    except Exception:
        # The CLI exits 30 and the postman leaves the PC file for the next
        # round. Forget the replay row this call may have written, or the
        # retry would be filed as a "duplicate" and the request silently lost.
        _forget(store, file_req_id, now)
        res = _result("rejected", file_req_id, agent_id, "failed", "internal", alert=True)
        _write(store, "rejected", file_req_id + ".json", raw)
        _write_json(store, "verdicts", file_req_id + ".json", res._asdict())
        try:
            store.alert(file_req_id, "internal error while routing a request")
        except Exception:
            pass
        _audit(store, now, res)
        return res


def _route_legacy(path, req_id, outer, raw, store, registry, policy, grants, now):
    from_ = outer.get("from")
    label = _legacy_label(from_)
    type_ = _legacy_type(outer.get("type", ""))
    if policy.legacy_unsigned == "reject":
        res = _result("rejected", req_id, label, "legacy_unsigned",
                      "legacy_disabled", type_, reply_hint="unsigned_error")
        _write(store, "rejected", req_id + ".json", raw)
        _write_json(store, "verdicts", req_id + ".json", res._asdict())
        _audit(store, now, res)
        return res
    # "Codex " or "CODEX" is still the enrolled "codex" to a human reading it
    if isinstance(from_, str) and _registered(registry, from_.strip().lower()):
        res = _result("rejected", req_id, label, "legacy_unsigned", "downgrade", type_,
                      alert=True, reply_hint="unsigned_error")
        _write(store, "rejected", req_id + ".json", raw)
        _write_json(store, "verdicts", req_id + ".json", res._asdict())
        try:
            store.alert(req_id, "possible downgrade: registered agent " + label + " sent an unsigned request")
        except Exception:
            pass
        _audit(store, now, res)
        return res
    digest = hashlib.sha256(raw).hexdigest()
    rep = _replay(store, policy, req_id, label, digest, now, "inbox")
    if rep == "duplicate":
        res = _result("duplicate", req_id, label, "legacy_unsigned", "", type_)
        _audit(store, now, res, digest)
        return res
    if rep == "conflict":
        res = _result("rejected", req_id, label, "legacy_unsigned", "conflict", type_,
                      alert=True, reply_hint="unsigned_error")
        _write(store, "rejected", req_id + ".json", raw)
        _write_json(store, "verdicts", req_id + ".json", res._asdict())
        try:
            store.alert(req_id, "replay conflict for " + req_id)
        except Exception:
            pass
        _audit(store, now, res, digest)
        return res
    d = decide(policy, auth="legacy_unsigned", agent=None, action=type_, grant_id=None,
                      facts={}, grants=grants, now=now)
    worker = dict(outer)
    if "type" in worker:
        worker["type"] = type_     # the stored envelope keeps the original bytes
    worker["auth"] = {"verified": False, "mode": "legacy_unsigned"}
    res = _result("inbox", req_id, label, "legacy_unsigned", "", type_,
                  d.decision, d.reason, d.grant_id)
    # inbox/ last: a worker that picks the request up must already find the
    # envelope (the publish pipeline re-verifies it) and the verdict
    _write(store, "envelopes", req_id + ".json", raw)
    _write_json(store, "verdicts", req_id + ".json", res._asdict())
    _write_json(store, "inbox", req_id + ".json", worker)
    _audit(store, now, res, digest)
    return res


def _route_enroll(path, req_id, outer, raw, store, registry, policy, grants, now):
    try:
        inner = envelope.parse_enroll(outer, now, policy.freshness["max_age_s"],
                                      policy.freshness["max_future_s"])
    except AuthError as e:
        res = _result("rejected", req_id, _legacy_label(outer.get("agent_id")), "enroll", e.code,
                      "enroll", reply_hint="unsigned_error")
        _write(store, "rejected", req_id + ".json", raw)
        _write_json(store, "verdicts", req_id + ".json", res._asdict())
        _audit(store, now, res)
        return res
    agent_id = inner["agent_id"]
    digest = hashlib.sha256(raw).hexdigest()
    rep = _replay(store, policy, req_id, agent_id, digest, now, "pending")
    if rep == "duplicate":
        res = _result("duplicate", req_id, agent_id, "enroll", "", "enroll")
        _audit(store, now, res, digest)
        return res
    if rep == "conflict":
        res = _result("rejected", req_id, agent_id, "enroll", "conflict", "enroll",
                      alert=True, reply_hint="unsigned_error")
        _write(store, "rejected", req_id + ".json", raw)
        _write_json(store, "verdicts", req_id + ".json", res._asdict())
        try:
            store.alert(req_id, "replay conflict for " + req_id)
        except Exception:
            pass
        _audit(store, now, res, digest)
        return res
    conn = store.db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT COUNT(*) FROM enroll_log WHERE at > ?", (now - 3600,)).fetchone()
        count = int(row[0]) if row else 0
        if count >= int(policy.enroll_limits["per_hour"]):
            conn.execute("COMMIT")
            res = _result("rejected", req_id, agent_id, "enroll", "enroll_rate_limited", "enroll",
                          reply_hint="unsigned_error")
            _write(store, "rejected", req_id + ".json", raw)
            _write_json(store, "verdicts", req_id + ".json", res._asdict())
            _audit(store, now, res, digest)
            return res
        conn.execute("INSERT OR REPLACE INTO enroll_log(req_id,agent_id,at) VALUES(?,?,?)",
                     (req_id, agent_id, now))
        conn.execute("COMMIT")
    finally:
        conn.close()
    _write_json(store, "pending", req_id + ".json", {"inner": inner, "envelope": outer})
    _write(store, "envelopes", req_id + ".json", raw)
    res = _result("pending", req_id, agent_id, "enroll", "", "enroll")
    _write_json(store, "verdicts", req_id + ".json", res._asdict())
    _audit(store, now, res, digest)
    return res


def _route_v3(path, req_id, outer, raw, store, registry, policy, grants, now):
    # unauthenticated until verify_request succeeds: inert charset only
    agent_id = _legacy_label(outer.get("agent_id"))
    try:
        vr = envelope.verify_request(outer, registry, now, policy.freshness["max_age_s"],
                                     policy.freshness["max_future_s"])
    except AuthError as e:
        post = e.code in POST_AUTH_CODES
        alert = e.code in IMPERSONATION_CODES and _registered(registry, agent_id)
        res = _result("rejected", req_id, agent_id, "verified" if post else "failed", e.code,
                      "", alert=alert, reply_hint="signed_error" if post else "none")
        _write(store, "rejected", req_id + ".json", raw)
        _write_json(store, "verdicts", req_id + ".json", res._asdict())
        if alert:
            try:
                store.alert(req_id, "possible impersonation of " + agent_id + ": " + e.code)
            except Exception:
                pass
        _audit(store, now, res)
        return res
    except Exception:
        res = _result("rejected", req_id, agent_id, "failed", "internal", alert=True)
        _write(store, "rejected", req_id + ".json", raw)
        _write_json(store, "verdicts", req_id + ".json", res._asdict())
        try:
            store.alert(req_id, "internal error while verifying a request")
        except Exception:
            pass
        _audit(store, now, res)
        return res

    rep = _replay(store, policy, req_id, vr.agent_id, vr.digest, now, "inbox")
    if rep == "duplicate":
        res = _result("duplicate", req_id, vr.agent_id, "verified", "", vr.inner["type"])
        _audit(store, now, res, vr.digest)
        return res
    if rep == "conflict":
        res = _result("rejected", req_id, vr.agent_id, "verified", "conflict", vr.inner["type"],
                      alert=True, reply_hint="signed_error")
        _write(store, "rejected", req_id + ".json", raw)
        _write_json(store, "verdicts", req_id + ".json", res._asdict())
        try:
            store.alert(req_id, "replay conflict for " + req_id)
        except Exception:
            pass
        _audit(store, now, res, vr.digest)
        return res

    try:
        agent = registry.get(vr.agent_id)
    except AuthError as e:
        res = _result("rejected", req_id, vr.agent_id, "failed", e.code, vr.inner["type"],
                      alert=True, reply_hint="none")
        _write(store, "rejected", req_id + ".json", raw)
        _write_json(store, "verdicts", req_id + ".json", res._asdict())
        _audit(store, now, res, vr.digest)
        return res
    # retired type names become the current one in the worker copy and the
    # verdict; the signed envelope stays byte for byte (reverify needs it)
    type_ = current_type(vr.inner["type"])
    d = decide(policy, auth="verified", agent=agent, action=type_,
                      grant_id=vr.inner.get("grant_id"), facts={}, grants=grants, now=now)
    tier = agent.get("tier") if agent else None
    worker = envelope.worker_request(vr, tier)
    worker["type"] = type_
    res = _result("inbox", req_id, vr.agent_id, "verified", "", type_,
                  d.decision, d.reason, d.grant_id)
    _write(store, "envelopes", req_id + ".json", raw)
    _write_json(store, "verdicts", req_id + ".json", res._asdict())
    _write_json(store, "inbox", req_id + ".json", worker)     # last, see _route_legacy
    _audit(store, now, res, vr.digest)
    return res


def reverify(store, registry, policy, req_id, now, worker=None):
    """Authentication for an enforcement point that acts on a request later
    (publish pipeline, `muselink-id check/authorize`). Re-verifies the stored
    envelope; never trusts a verdict file.
    -> (auth, agent_entry, agent_id, inner, legacy_from)
    auth: "verified" | "legacy_unsigned" | "failed:<code>".

    A missing envelope is legacy only when the worker request (if known)
    does not claim to be verified: requests routed before the identity
    module existed have no envelope; a verified one always has. An unknown
    request id with no worker file is "failed:unknown_request" (review
    2026-11-12: it used to fall back to the more permissive legacy tier)."""
    from . import REQ_ID_RX, valid_id
    if not valid_id(REQ_ID_RX, req_id):
        return "failed:bad_req_id", None, "", None, ""
    if worker is None:
        for d in ("inbox", "working", "processed"):
            p = store.bridge_path(d, req_id + ".json")
            if os.path.exists(p):
                try:
                    with open(p, "rb") as f:
                        worker = envelope.strict_loads(f.read().decode("utf-8-sig"))
                except Exception:
                    return "failed:unparsable", None, "", None, ""
                break
    claims_verified = isinstance(worker, dict) and isinstance(worker.get("auth"), dict) \
        and worker["auth"].get("verified") is True
    path = store.bridge_path("envelopes", req_id + ".json")
    if not os.path.exists(path):
        if worker is None:
            return "failed:unknown_request", None, "", None, ""
        if claims_verified:
            return "failed:envelope_missing", None, "", None, ""
        return "legacy_unsigned", None, "", None, str(worker.get("from") or "")
    try:
        kind, outer = envelope.read_envelope_file(path, req_id, policy.max_envelope_bytes)
    except AuthError as e:
        return "failed:" + e.code, None, "", None, ""
    if kind == "legacy":
        if claims_verified:
            return "failed:envelope_mismatch", None, "", None, ""
        return "legacy_unsigned", None, "", None, str(outer.get("from") or "")
    if kind == "enroll":
        return "failed:unsigned", None, "", None, ""
    try:
        vr = envelope.verify_request(outer, registry, now, policy.freshness["max_age_s"],
                                     policy.freshness["max_future_s"], check_freshness=False)
    except AuthError as e:
        return "failed:" + e.code, None, "", None, ""
    try:
        agent = registry.get(vr.agent_id)
    except AuthError as e:
        return "failed:" + e.code, None, "", None, ""
    return "verified", agent, vr.agent_id, vr.inner, ""
