"""grants - pre-authorization credentials.

File: <id_home>/grants.json = {"v": 1, "grants": {<grant_id>: grant}}.
Grant fields: docs/protocol.en.md section 4. "status" is "proposed" | "active" |
"declined" | "revoked". Use counts live in state.sqlite (grant_uses), not in
the JSON.

Requirements
  1. validate_spec(spec, policy, now) -> normalized spec or GrantError:
     - agent_id matches AGENT_ID_RX; action canonicalized via
       policy.canonical() and must be in policy class "privileged";
     - conditions: list (may be empty) of {field, op, value}; field is
       [a-z0-9_.]{1,64}; op in OPS; value type per op: equals any JSON
       scalar, iequals/prefix/path_under non-empty str, in non-empty list of
       scalars, min/max int or float (NOT bool). Unknown op -> error;
     - not_before <= now + 1 day; expires_at > now and
       expires_at - max(now, not_before) <= grant_caps.max_days * 86400;
     - max_uses int 1..grant_caps.max_uses (bool is not an int here);
     - mode "allow" | "delay"; auto_match bool (default False);
     - note: str, newlines/control chars removed, at most 200 chars.
  2. evaluate(conditions, facts) -> (ok, failures). All conditions must
     hold. A missing fact (field absent or None) fails its condition.
     Dotted fields read nested dicts. Type-strict: "1" != 1, True != 1.
       equals      fact == value and type(fact) is type(value)
       iequals     both str; ASCII-only case fold (str.lower on ASCII
                   letters only)
       prefix      both str; fact.startswith(value)
       in          fact equals (type-strict) one element
       min / max   both numbers (int/float, not bool); fact >= / <= value
       path_under  see path_under()
  3. path_under(path, base) -> bool, Windows semantics, fail closed:
     - both must be absolute drive paths "X:\\..." after turning "/" into
       "\\"; reject UNC ("\\\\server"), device/namespace prefixes ("\\\\?\\",
       "\\\\.\\"), any ".." or "." segment, any ":" after the drive letter
       (alternate data streams), any segment ending in "." or " " (Windows
       strips them, so "out." would alias "out"), empty segments ("\\\\")
       except a single trailing separator on base;
     - compare with ASCII-only case folding; non-ASCII characters must
       match exactly (review 2026-11-12: even single-character upper() maps
       dotless i and long s onto ASCII letters, which NTFS does not);
     - true only if path == base or path starts with base + "\\" (so
       "D:\\out" never matches "D:\\outside\\x.zip").
  4. Seal: store.seal() over the whole grant except "seal". get() verifies;
     mismatch -> GrantError("tampered") + store.alert once; the grant is
     unusable. Every status change re-seals under lock("grants").
  5. propose(spec, requested_by, now) -> grant with status "proposed", a
     fresh grant_id (secrets.token_hex(6) with the "g-" prefix) and
     "proposal_digest" = SHA-256 of canonical_json(spec). activate(grant_id,
     evidence, now) requires status "proposed", evidence shape as in
     registry requirement 3, and that the proposal still has the same
     digest; then status "active" + approval + re-seal. decline() sets
     "declined". Only flows.grant_ceremony() calls activate.
  6. check(grant_id, agent_id, action, facts, now) -> (ok, reason) is
     read-only. reason codes: unknown_grant, tampered, not_active, revoked,
     wrong_agent, wrong_action, not_yet_valid, expired, exhausted,
     conditions_failed, agent_not_active. Revoked = status "revoked" OR a
     row in revocations (either one is enough).
  7. consume(...) / reserve(...): inside ONE "BEGIN IMMEDIATE" transaction
     re-run every check from requirement 6, count rows with state
     'consumed' plus 'reserved' rows whose expires_at > now, and insert only
     if the count < max_uses. Idempotent per (grant_id, req_id): a second
     consume for the same req_id returns ok with already=True and does not
     count twice. reserve stores expires_at = now + ttl_s.
     confirm(grant_id, req_id, now): reserved & not expired -> consumed;
     otherwise run consume() again (may fail if the grant ran out).
     release(): reserved -> released (no-op otherwise).
  8. revoke(grant_id, reason, now) and revoke_agent(agent_id, reason, now):
     immediate; write both the JSON status and the revocations row.
  9. find_auto(agent_id, action, facts, now) -> grant_id or None: only
     grants with auto_match True; if several match, the one expiring first.
 10. render(grant) -> the card text (Chinese), built ONLY from structured
     fields, one condition per line in plain words, e.g.
       "package_path 在 D:\\MyApp\\out 之下" / "version 以 1.9. 开头",
     plus agent, action, validity (local time), uses, mode. Values are
     sanitized (no newlines/control chars, 120 chars max each).
 11. public_list(now) -> mirror entries {grant_id, agent_id, action,
     conditions, not_before, expires_at, max_uses, uses_left, mode,
     auto_match, status, note}; no seal, no approval details.
"""
from muselink_id.i18n import t, labels, answer_key, active

import hashlib
import json
import re
import secrets
import time

import math

from . import (
    AGENT_ID_RX, GRANT_ID_RX, GrantError, valid_id,
)
from .store import canonical_json

OPS = frozenset({"equals", "iequals", "prefix", "in", "min", "max", "path_under"})
FIELD_RX = r"^[a-z0-9_.]{1,64}$"
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _clean(text, limit):
    if not isinstance(text, str):
        text = str(text)
    text = _CTRL.sub(" ", text).replace("\r", " ").replace("\n", " ")
    return text[:limit]


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _scalar(v):
    return v is None or isinstance(v, (str, int, float, bool)) and not isinstance(v, complex)


def _ascii_lower(s):
    return "".join(c.lower() if "A" <= c <= "Z" else c for c in s)


def _fold(s):
    # ASCII-only case folding (review 2026-11-12). Python's single-character
    # upper() still maps letters NTFS keeps apart: "\u0131".upper() == "I",
    # "\u017f".upper() == "S". Folding those would let a sibling folder such as
    # "b\u0131n" pass a grant for "bin". Non-ASCII letters must match exactly,
    # which can only produce a false "no" (the user gets asked), never a false
    # "yes".
    return "".join(chr(ord(c) - 32) if "a" <= c <= "z" else c for c in s)


def _norm_path(p):
    if not isinstance(p, str) or not p:
        return None
    s = p.replace("/", "\\")
    if len(s) < 3 or s[1] != ":" or not ("A" <= s[0] <= "Z" or "a" <= s[0] <= "z") or s[2] != "\\":
        return None
    drive = s[0].upper()
    rest = s[3:]
    if ":" in rest:
        return None
    if rest.endswith("\\"):
        rest = rest[:-1]
    if rest == "":
        return drive, ()
    parts = rest.split("\\")
    for part in parts:
        if part == "" or part in (".", ".."):
            return None
        if part.endswith(".") or part.endswith(" "):
            return None
    return drive, tuple(parts)


def validate_spec(spec, policy, now):
    if not isinstance(spec, dict):
        raise GrantError("bad_spec")
    allowed = {"agent_id", "action", "conditions", "not_before", "expires_at",
               "max_uses", "mode", "auto_match", "note"}
    if not set(spec) <= allowed:
        raise GrantError("bad_spec")
    agent_id = spec.get("agent_id")
    if not valid_id(AGENT_ID_RX, agent_id):
        raise GrantError("bad_agent_id")
    action = spec.get("action")
    if not isinstance(action, str):
        raise GrantError("bad_action")
    action = policy.canonical(action)
    if policy.classify(action) != "privileged":
        raise GrantError("action_not_privileged")
    conds = spec.get("conditions", [])
    if not isinstance(conds, list):
        raise GrantError("bad_conditions")
    out_conds = []
    for c in conds:
        if not isinstance(c, dict) or set(c) != {"field", "op", "value"}:
            raise GrantError("bad_condition")
        field, op, value = c.get("field"), c.get("op"), c.get("value")
        if not valid_id(FIELD_RX, field):
            raise GrantError("bad_field")
        if op not in OPS:
            raise GrantError("bad_op")
        if op in ("iequals", "prefix", "path_under"):
            if not isinstance(value, str) or not value:
                raise GrantError("bad_value")
            if op == "path_under" and _norm_path(value) is None:
                # a base path_under() can never accept would make a grant
                # the user approved on the card silently useless
                raise GrantError("bad_value")
        elif op == "in":
            if not isinstance(value, list) or not value or not all(_scalar(x) for x in value):
                raise GrantError("bad_value")
            if any(isinstance(x, float) and not math.isfinite(x) for x in value):
                raise GrantError("bad_value")
        elif op in ("min", "max"):
            if not _is_num(value):
                raise GrantError("bad_value")
        elif op == "equals":
            if not _scalar(value) or (isinstance(value, float) and not math.isfinite(value)):
                raise GrantError("bad_value")
        out_conds.append({"field": field, "op": op, "value": value})
    not_before = spec.get("not_before")
    expires_at = spec.get("expires_at")
    if type(not_before) is not int or type(expires_at) is not int:
        raise GrantError("bad_validity")
    if not_before > now + 86400:
        raise GrantError("not_before_too_far")
    if expires_at <= now:
        raise GrantError("expired")
    caps = policy.grant_caps
    if expires_at - max(now, not_before) > int(caps["max_days"]) * 86400:
        raise GrantError("too_long")
    max_uses = spec.get("max_uses")
    if type(max_uses) is not int or max_uses < 1 or max_uses > int(caps["max_uses"]):
        raise GrantError("bad_uses")
    mode = spec.get("mode", "allow")
    if mode not in ("allow", "delay"):
        raise GrantError("bad_mode")
    auto_match = spec.get("auto_match", False)
    if not isinstance(auto_match, bool):
        raise GrantError("bad_auto_match")
    note = spec.get("note", "")
    if not isinstance(note, str):
        raise GrantError("bad_note")
    return {
        "agent_id": agent_id,
        "action": action,
        "conditions": out_conds,
        "not_before": not_before,
        "expires_at": expires_at,
        "max_uses": max_uses,
        "mode": mode,
        "auto_match": auto_match,
        "note": _clean(note, 200),
    }


def _fact(facts, field):
    cur = facts
    for part in field.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    if cur is None:
        return None, False
    return cur, True


def evaluate(conditions, facts):
    """-> (ok: bool, failures: list of str)."""
    failures = []
    for c in conditions:
        fact, present = _fact(facts, c["field"])
        if not present:
            failures.append(c["field"])
            continue
        op, value = c["op"], c["value"]
        ok = False
        if op == "equals":
            ok = type(fact) is type(value) and fact == value
        elif op == "iequals":
            ok = isinstance(fact, str) and _ascii_lower(fact) == _ascii_lower(value)
        elif op == "prefix":
            ok = isinstance(fact, str) and fact.startswith(value)
        elif op == "in":
            ok = any(type(fact) is type(v) and fact == v for v in value)
        elif op == "min":
            ok = _is_num(fact) and fact >= value
        elif op == "max":
            ok = _is_num(fact) and fact <= value
        elif op == "path_under":
            ok = path_under(fact, value) if isinstance(fact, str) else False
        if not ok:
            failures.append(c["field"])
    return (not failures, failures)


def path_under(path, base):
    a = _norm_path(path)
    b = _norm_path(base)
    if a is None or b is None:
        return False
    if a[0] != b[0] or len(a[1]) < len(b[1]):
        return False
    for i, part in enumerate(b[1]):
        if _fold(a[1][i]) != _fold(part):
            return False
    return True


def render(grant):
    lines = []
    lines.append(t('id.grant.id') + str(grant.get("grant_id", "")))
    lines.append(t('id.grant.agent') + _clean(grant.get("agent_id", ""), 40))
    lines.append(t('id.grant.action') + _clean(grant.get("action", ""), 60))
    for c in grant.get("conditions", []):
        field = _clean(c.get("field", ""), 60)
        value = _clean(c.get("value", ""), 120)
        op = c.get("op")
        if op == "path_under":
            lines.append(t('id.grant.under', field=field, value=value))
        elif op == "prefix":
            lines.append(t('id.grant.prefix', field=field, value=value))
        elif op == "equals":
            lines.append(t('id.grant.equal', field=field, value=value))
        elif op == "iequals":
            lines.append(t('id.grant.equal_ci', field=field, value=value))
        elif op == "in":
            lines.append(t('id.grant.member', field=field, value=value))
        elif op == "min":
            lines.append(t('id.grant.min', field=field, value=value))
        elif op == "max":
            lines.append(t('id.grant.max', field=field, value=value))
    try:
        nb = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(grant.get("not_before", 0))))
        ex = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(grant.get("expires_at", 0))))
        lines.append(t('id.grant.valid', start=nb, end=ex))
    except Exception:
        pass
    lines.append(t('id.grant.uses', n=grant.get("max_uses", "")))
    mode = grant.get("mode")
    lines.append(t('id.grant.mode') + (t('id.grant.delay') if mode == "delay" else t('id.grant.direct')))
    if grant.get("auto_match"):
        lines.append(t('id.grant.auto'))
    note = _clean(grant.get("note", ""), 200)
    if note:
        lines.append(t('id.grant.note') + note)
    return "\n".join(lines)


class UseResult:
    """ok: bool; reason: str ('' when ok); uses_left: int; already: bool."""

    def __init__(self, ok, reason="", uses_left=0, already=False):
        self.ok, self.reason, self.uses_left, self.already = ok, reason, uses_left, already

    def __repr__(self):
        return f"UseResult(ok={self.ok}, reason={self.reason!r}, uses_left={self.uses_left}, already={self.already})"


class Grants:
    def __init__(self, store, policy, registry=None):
        """registry (optional) lets check() refuse grants of revoked agents."""
        self.store, self.policy, self.registry = store, policy, registry

    def _load(self):
        d = self.store.read_json("grants.json", {"v": 1, "grants": {}})
        if not isinstance(d, dict) or not isinstance(d.get("grants"), dict):
            return {"v": 1, "grants": {}}
        return d

    def _save(self, d):
        self.store.write_json("grants.json", d)

    def _sealable(self, grant):
        g = dict(grant)
        g.pop("seal", None)
        return g

    def _load_grant(self, d, grant_id):
        g = d["grants"].get(grant_id)
        if g is None:
            return None
        # The seal covers the record, not the key it is filed under. Without
        # the grant_id check a sealed record copied under a new key got a
        # fresh, empty use counter (review 2026-11-12).
        if (not isinstance(g, dict) or g.get("grant_id") != grant_id
                or not self.store.check_seal(self._sealable(g), g.get("seal"))):
            self.store.alert("grant-" + str(grant_id), "possible grant tampering: " + str(grant_id))
            raise GrantError("tampered")
        return g

    def _audit(self, event, now, **fields):
        try:
            self.store.audit(event, now, **fields)
        except Exception:
            pass

    def _facts_digest(self, action, facts):
        try:
            blob = canonical_json({"action": self.policy.canonical(action), "facts": facts})
        except (TypeError, ValueError):
            blob = repr((action, sorted((facts or {}).items())))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def propose(self, spec, requested_by, now):
        norm = validate_spec(spec, self.policy, now)
        with self.store.lock("grants"):
            d = self._load()
            while True:
                gid = "g-" + secrets.token_hex(6)
                if gid not in d["grants"]:
                    break
            grant = dict(norm)
            grant.update({
                "grant_id": gid,
                "requested_by": _clean(requested_by, 40),
                "created_at": now,
                "status": "proposed",
                "proposal_digest": hashlib.sha256(canonical_json(norm).encode("utf-8")).hexdigest(),
            })
            grant["seal"] = self.store.seal(self._sealable(grant))
            d["grants"][gid] = grant
            self._save(d)
        self._audit("grant_proposed", now, grant_id=gid, agent_id=grant["agent_id"],
                    action=grant["action"], max_uses=grant["max_uses"], mode=grant["mode"],
                    expires_at=grant["expires_at"], requested_by=grant["requested_by"])
        return json.loads(json.dumps(grant))

    def activate(self, grant_id, evidence, now):
        from .registry import _require_evidence
        ev = _require_evidence(evidence)
        with self.store.lock("grants"):
            d = self._load()
            g = self._load_grant(d, grant_id)
            if g is None:
                raise GrantError("unknown_grant")
            if g.get("status") != "proposed":
                raise GrantError("not_proposed")
            spec = {k: g.get(k) for k in ("agent_id", "action", "conditions", "not_before",
                                          "expires_at", "max_uses", "mode", "auto_match", "note")}
            digest = hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()
            if digest != g.get("proposal_digest"):
                raise GrantError("proposal_changed")
            g["status"] = "active"
            g["approval"] = ev
            g["seal"] = self.store.seal(self._sealable(g))
            self._save(d)
            out = json.loads(json.dumps(g))
        self._audit("grant_activated", now, grant_id=grant_id, popup_req=ev["popup_req"])
        return out

    def decline(self, grant_id, now):
        with self.store.lock("grants"):
            d = self._load()
            g = self._load_grant(d, grant_id)
            if g is None:
                raise GrantError("unknown_grant")
            if g.get("status") == "proposed":
                g["status"] = "declined"
                g["seal"] = self.store.seal(self._sealable(g))
                self._save(d)
                self._audit("grant_declined", now, grant_id=grant_id)

    def get(self, grant_id):
        d = self._load()
        return self._load_grant(d, grant_id)

    def _revoked_row(self, conn, grant_id):
        row = conn.execute("SELECT 1 FROM revocations WHERE grant_id=?", (grant_id,)).fetchone()
        return row is not None

    def _uses(self, conn, grant_id, now):
        row = conn.execute(
            "SELECT COUNT(*) FROM grant_uses WHERE grant_id=? AND "
            "(state='consumed' OR (state='reserved' AND expires_at > ?))",
            (grant_id, now)).fetchone()
        return int(row[0]) if row else 0

    def _static_reason(self, conn, grant, agent_id, action, facts, now):
        gid = grant["grant_id"]
        if grant.get("status") == "revoked":
            return "revoked"
        if self._revoked_row(conn, gid):
            return "revoked"
        if grant.get("status") != "active":
            return "not_active"
        if grant.get("agent_id") != agent_id:
            return "wrong_agent"
        if grant.get("action") != self.policy.canonical(action):
            return "wrong_action"
        if now < int(grant.get("not_before", 0)):
            return "not_yet_valid"
        if now >= int(grant.get("expires_at", 0)):
            return "expired"
        if self.registry is not None:
            try:
                entry = self.registry.get(agent_id)
            except Exception:
                return "agent_not_active"
            if entry is None or entry.get("status") != "active":
                return "agent_not_active"
        if facts is None:      # confirm(): conditions were checked at reserve time
            return ""
        ok, _fail = evaluate(grant.get("conditions", []), facts)
        if not ok:
            return "conditions_failed"
        return ""

    def _check_conn(self, conn, grant, agent_id, action, facts, now):
        reason = self._static_reason(conn, grant, agent_id, action, facts, now)
        if reason:
            return reason, 0
        used = self._uses(conn, grant["grant_id"], now)
        if used >= int(grant.get("max_uses", 0)):
            return "exhausted", used
        return "", used

    def check(self, grant_id, agent_id, action, facts, now):
        try:
            grant = self.get(grant_id)
        except GrantError as e:
            return False, "tampered" if e.code == "tampered" else "unknown_grant"
        if grant is None:
            return False, "unknown_grant"
        conn = self.store.db()
        try:
            reason, _used = self._check_conn(conn, grant, agent_id, action, facts, now)
            return (not reason), reason
        except Exception:
            return False, "tampered"
        finally:
            conn.close()

    def _use(self, grant_id, agent_id, req_id, action, facts, now, ttl_s=None):
        try:
            with self.store.lock("grants"):
                d = self._load()
                grant = self._load_grant(d, grant_id)
                if grant is None:
                    return UseResult(False, "unknown_grant")
                conn = self.store.db()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    reason = self._static_reason(conn, grant, agent_id, action, facts, now)
                    if reason:
                        conn.execute("COMMIT")
                        return UseResult(False, reason)
                    digest = self._facts_digest(action, facts)
                    row = conn.execute(
                        "SELECT state, expires_at, facts_digest FROM grant_uses WHERE grant_id=? AND req_id=?",
                        (grant_id, req_id)).fetchone()
                    if row:
                        state, expires_at, old_digest = row
                        if state == "consumed" or (state == "reserved" and expires_at > now):
                            if old_digest and old_digest != digest:
                                # Same request, different action or facts: that is a
                                # second use, not a retry. One grant use per request;
                                # anything more goes back to asking the user.
                                conn.execute("COMMIT")
                                return UseResult(False, "request_reused")
                            left = int(grant["max_uses"]) - self._uses(conn, grant_id, now)
                            conn.execute("COMMIT")
                            return UseResult(True, "", max(0, left), True)
                    used = self._uses(conn, grant_id, now)
                    if used >= int(grant.get("max_uses", 0)):
                        conn.execute("COMMIT")
                        return UseResult(False, "exhausted", 0)
                    if ttl_s is None:
                        conn.execute(
                            "INSERT OR REPLACE INTO grant_uses(grant_id,req_id,state,at,expires_at,facts_digest) "
                            "VALUES(?,?, 'consumed', ?, 0, ?)", (grant_id, req_id, now, digest))
                    else:
                        conn.execute(
                            "INSERT OR REPLACE INTO grant_uses(grant_id,req_id,state,at,expires_at,facts_digest) "
                            "VALUES(?,?, 'reserved', ?, ?, ?)", (grant_id, req_id, now, now + int(ttl_s), digest))
                    conn.execute("COMMIT")
                    self._audit("grant_consumed" if ttl_s is None else "grant_reserved", now,
                                grant_id=grant_id, agent_id=agent_id, req_id=req_id,
                                uses_left=max(0, int(grant["max_uses"]) - used - 1))
                    return UseResult(True, "", max(0, int(grant["max_uses"]) - used - 1))
                except Exception:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                    raise
                finally:
                    conn.close()
        except GrantError as e:
            return UseResult(False, "tampered" if e.code == "tampered" else "unknown_grant")
        except Exception:
            return UseResult(False, "internal")

    def consume(self, grant_id, agent_id, req_id, action, facts, now):
        return self._use(grant_id, agent_id, req_id, action, facts, now, None)

    def reserve(self, grant_id, agent_id, req_id, action, facts, now, ttl_s):
        return self._use(grant_id, agent_id, req_id, action, facts, now, ttl_s)

    def confirm(self, grant_id, req_id, now):
        with self.store.lock("grants"):
            d = self._load()
            grant = self._load_grant(d, grant_id)
            if grant is None:
                return UseResult(False, "unknown_grant")
            conn = self.store.db()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT state, expires_at FROM grant_uses WHERE grant_id=? AND req_id=?",
                    (grant_id, req_id)).fetchone()
                if row and row[0] == "consumed":
                    left = int(grant["max_uses"]) - self._uses(conn, grant_id, now)
                    conn.execute("COMMIT")
                    return UseResult(True, "", max(0, left), True)
                if not (row and row[0] == "reserved" and row[1] > now):
                    # No live reservation: the delay window is over (or never
                    # existed). Re-running the checks without the measured facts
                    # is meaningless, so this never turns into a use. Ruling on
                    # delivery note section 3: the reason is reservation_expired.
                    conn.execute("COMMIT")
                    return UseResult(False, "reservation_expired")
                # Revocation, expiry and agent status must still hold at the
                # moment the action proceeds; a revoke issued during the
                # 120 s window takes effect (conditions were checked with the
                # measured facts when the reservation was made).
                reason = self._static_reason(conn, grant, grant["agent_id"], grant["action"],
                                             None, now)
                if reason:
                    conn.execute(
                        "UPDATE grant_uses SET state='released', at=? WHERE grant_id=? AND req_id=?",
                        (now, grant_id, req_id))
                    conn.execute("COMMIT")
                    return UseResult(False, reason)
                conn.execute(
                    "UPDATE grant_uses SET state='consumed', at=?, expires_at=0 WHERE grant_id=? AND req_id=?",
                    (now, grant_id, req_id))
                used = self._uses(conn, grant_id, now)
                conn.execute("COMMIT")
                self._audit("grant_confirmed", now, grant_id=grant_id, req_id=req_id,
                            uses_left=max(0, int(grant["max_uses"]) - used))
                return UseResult(True, "", max(0, int(grant["max_uses"]) - used))
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            finally:
                conn.close()

    def release(self, grant_id, req_id, now):
        conn = self.store.db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE grant_uses SET state='released', at=? WHERE grant_id=? AND req_id=? AND state='reserved'",
                (now, grant_id, req_id))
            conn.execute("COMMIT")
        finally:
            conn.close()
        self._audit("grant_released", now, grant_id=grant_id, req_id=req_id)

    def _revocation_row(self, grant_id, reason, now):
        conn = self.store.db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR REPLACE INTO revocations(grant_id,at,reason) VALUES(?,?,?)",
                         (grant_id, now, _clean(reason, 200)))
            conn.execute("COMMIT")
        finally:
            conn.close()

    def revoke(self, grant_id, reason, now):
        # Revoking only removes power, so it must always work: the
        # revocations row is written first, even when the JSON record is
        # damaged or unknown (review 2026-11-12).
        if not valid_id(GRANT_ID_RX, grant_id):
            raise GrantError("unknown_grant")
        with self.store.lock("grants"):
            self._revocation_row(grant_id, reason, now)
            self._audit("grant_revoked", now, grant_id=grant_id, reason=_clean(reason, 120))
            d = self._load()
            g = self._load_grant(d, grant_id)   # may raise "tampered": the row above still holds
            if g is None:
                raise GrantError("unknown_grant")
            g["status"] = "revoked"
            g["seal"] = self.store.seal(self._sealable(g))
            self._save(d)

    def revoke_agent(self, agent_id, reason, now):
        """-> number of grants revoked."""
        n = 0
        with self.store.lock("grants"):
            d = self._load()
            dirty = False
            for gid, g in d["grants"].items():
                if not isinstance(g, dict) or g.get("agent_id") != agent_id:
                    continue
                self._revocation_row(gid, reason, now)
                try:
                    self._load_grant(d, gid)
                except GrantError:
                    continue            # damaged record: the revocations row is enough
                if g.get("status") != "revoked":
                    g["status"] = "revoked"
                    g["seal"] = self.store.seal(self._sealable(g))
                    dirty = True
                    n += 1
            if dirty:
                self._save(d)
        self._audit("grants_revoked_for_agent", now, agent_id=agent_id, count=n,
                    reason=_clean(reason, 120))
        return n

    def find_auto(self, agent_id, action, facts, now):
        d = self._load()
        hits = []
        for gid, g in d["grants"].items():
            if not g.get("auto_match"):
                continue
            ok, _reason = self.check(gid, agent_id, action, facts, now)
            if ok:
                hits.append((g.get("expires_at", 0), gid))
        if not hits:
            return None
        hits.sort()
        return hits[0][1]

    def public_list(self, now):
        d = self._load()
        conn = self.store.db()
        out = []
        try:
            for gid, g in d["grants"].items():
                try:
                    self._load_grant(d, gid)
                except GrantError:
                    continue
                used = self._uses(conn, gid, now)
                out.append({
                    "grant_id": gid,
                    "agent_id": g.get("agent_id"),
                    "action": g.get("action"),
                    "conditions": g.get("conditions", []),
                    "not_before": g.get("not_before"),
                    "expires_at": g.get("expires_at"),
                    "max_uses": g.get("max_uses"),
                    "uses_left": max(0, int(g.get("max_uses", 0)) - used),
                    "mode": g.get("mode"),
                    "auto_match": g.get("auto_match", False),
                    "status": g.get("status"),
                    "note": g.get("note", ""),
                })
        finally:
            conn.close()
        out.sort(key=lambda g: g["grant_id"])
        return out
