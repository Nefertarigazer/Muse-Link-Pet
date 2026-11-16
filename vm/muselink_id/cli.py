"""cli - the `muselink-id` command (wrapper: vm/muselink-id.sh).

    muselink-id route <file> <req_id>             postman: one JSON line
    muselink-id sign-reply <in.json> <out.json>   reply script: sign a v3 reply
    muselink-id check --req ID --action A [--grant G] [--fact k=v]...
                                                  dry run of decide(), JSON line
    muselink-id authorize --req ID --action A [--grant G] [--fact k=v]...
                                                  full authorize() incl. cards
    muselink-id enroll-pending                    bridge-service: next pending enrollment
    muselink-id agents list|show ID|set-tier ID TIER|revoke ID|rekey ID|provision ID
    muselink-id grants propose --agent ID --action A --where FIELD:OP:VALUE ...
                           --days N | --until ISO  --uses N  [--mode allow|delay]
                           [--auto-match] [--note TEXT] [--requested-by WHO]
    muselink-id grants list [--agent ID] | show G | revoke G [--reason R]
                           | revoke-agent ID
    muselink-id mirror                            rewrite the PC directory files
    muselink-id doctor                            report problems, change nothing

Requirements
  1. Exit codes from muselink_id: EXIT_OK 0, EXIT_USAGE 2, EXIT_ASK 10,
     EXIT_DELAY 11, EXIT_DENY 20, EXIT_INTERNAL 30. `check` maps the
     decision (allow/allow_with_grant 0, ask_user 10, delay 11, reject 20);
     `authorize` returns 0 only when Outcome.proceed is True, else 20.
     Any uncaught exception -> print a JSON error line, exit 30.
  2. stdout carries machine output only (JSON lines); human text goes to
     stderr. Never print keys, seals or request text.
  3. `check`/`authorize` load the request from $ML_BRIDGE_HOME/envelopes/
     <req_id>.json and RE-VERIFY it (envelope.verify_request with
     check_freshness=False); the verdict file is never trusted for
     authentication. Legacy requests: auth "legacy_unsigned". --fact
     values are strings unless they parse as a JSON number/bool (k=3 ->
     3); the caller (Muse) gives facts it measured, the CLI records that
     they were caller-supplied in the audit ("facts_source": "cli").
  4. `sign-reply`: input = the inner reply JSON produced by
     muse-bridge-reply.sh; looks up verdicts/<req_id>.json, refuses unless
     auth == "verified"; signs with the agent's ACTIVE key; writes the v3
     envelope (atomic). Exit 20 when the request is not verified (the
     caller then sends a v2 unsigned reply).
  5. `grants propose` validates, proposes, and immediately runs
     flows.grant_ceremony(); prints the grant id and the final status.
     There is no command that activates a grant without a card.
  6. `agents set-tier` and `agents rekey` also go through a card (flows).
     `revoke` commands need no card (they only remove power) but print what
     was revoked and audit it.
  7. `doctor`: permissions, seals of every entry, policy validity, active
     grants close to expiry, reservations still open, seen-table size,
     alerts not yet acknowledged; and a warning when the clock is earlier
     than the newest audit line (a clock jump back revives expired grants).
"""
from muselink_id.i18n import t, labels, answer_key, active

import json
import os
import sys
import tempfile
import time

from . import (
    EXIT_ASK, EXIT_DELAY, EXIT_DENY, EXIT_INTERNAL, EXIT_OK, EXIT_USAGE,
    REPLY_INNER_KEYS, REQ_ID_RX, AuthError, GrantError, IdentityError, PolicyError,
    valid_id,
)
from . import envelope, flows, inbound
from .grants import Grants, render
from .policy import Policy, decide
from .registry import Registry
from .store import Store
from .ui import PcAdapter, PopupUI


class UsageError(Exception):
    pass


def _vm_dir():
    return os.environ.get("ML_VM_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _policy_path():
    return os.environ.get("MUSELINK_POLICY") or os.path.expanduser("~/.config/muselink/policy.json")


def _open():
    store = Store().open()
    policy = Policy.from_file(_policy_path())
    registry = Registry(store)
    grants = Grants(store, policy, registry)
    return store, policy, registry, grants


def _out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def _err(text):
    sys.stderr.write(str(text) + "\n")


def _parse(args, value_flags=(), bool_flags=(), multi_flags=()):
    opts = {k: [] for k in multi_flags}
    pos = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in value_flags:
            if i + 1 >= len(args):
                raise UsageError("missing value for " + a)
            opts[a] = args[i + 1]
            i += 2
        elif a in multi_flags:
            if i + 1 >= len(args):
                raise UsageError("missing value for " + a)
            opts[a].append(args[i + 1])
            i += 2
        elif a in bool_flags:
            opts[a] = True
            i += 1
        elif a.startswith("--"):
            raise UsageError("unknown option " + a)
        else:
            pos.append(a)
            i += 1
    return opts, pos


def _fact_value(text):
    try:
        v = json.loads(text)
    except Exception:
        return text
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    return text


def _facts(items):
    facts = {}
    for item in items:
        if "=" not in item:
            raise UsageError("--fact needs k=v")
        k, v = item.split("=", 1)
        if not k:
            raise UsageError("empty fact name")
        facts[k] = _fact_value(v)
    return facts


def _req_id(value):
    # every req_id becomes part of a file path under $ML_BRIDGE_HOME
    if not valid_id(REQ_ID_RX, value):
        raise UsageError("bad req_id")
    return value


def _mirror_best_effort(registry, grants):
    try:
        pc = PcAdapter(_vm_dir())
        now = int(time.time())
        pc.write_directory("agents.json", registry.public_directory(now))
        pc.write_directory("grants.json", grants.public_list(now))
    except Exception as e:
        _err("mirror not updated: " + str(e)[:120])


def _write_atomic(path, data):
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _cmd_route(args):
    if len(args) != 2:
        raise UsageError("route <file> <req_id>")
    _req_id(args[1])
    store, policy, registry, grants = _open()
    res = inbound.route_request(args[0], args[1], store=store, registry=registry,
                                policy=policy, grants=grants, now=time.time())
    _out(res._asdict())
    return EXIT_INTERNAL if res.code == "internal" else EXIT_OK


def _cmd_sign_reply(args):
    if len(args) != 2:
        raise UsageError("sign-reply <in.json> <out.json>")
    store, policy, registry, grants = _open()
    with open(args[0], "rb") as f:
        inner = envelope.strict_loads(f.read().decode("utf-8-sig"))
    if not isinstance(inner, dict):
        raise UsageError("reply is not a JSON object")
    req_id = _req_id(inner.get("req_id"))
    vp = store.bridge_path("verdicts", req_id + ".json")
    try:
        with open(vp, "rb") as f:
            verdict = json.loads(f.read().decode("utf-8"))
    except Exception:
        verdict = {}
    if verdict.get("auth") != "verified" or not verdict.get("agent_id"):
        _out({"ok": False, "reason": "not_verified"})
        return EXIT_DENY
    # The recipient is the VERIFIED agent from the verdict, whatever the
    # reply script put in the draft; a mismatch would only be quarantined
    # by the client, so fix it here and refuse malformed drafts early.
    agent_id = verdict["agent_id"]
    inner["agent_id"] = agent_id
    inner["reply_protocol"] = 3
    if set(inner) != REPLY_INNER_KEYS or type(inner.get("seq")) is not int:
        raise UsageError("reply draft does not have exactly the v3 reply keys")
    key_id, key = registry.active_key(agent_id)
    env = envelope.new_reply_envelope(agent_id, key_id, key, inner)
    _write_atomic(args[1], json.dumps(env, ensure_ascii=False).encode("utf-8"))
    _out({"ok": True, "req_id": req_id, "seq": inner.get("seq")})
    return EXIT_OK


def _cmd_check(args, do_authorize):
    opts, pos = _parse(args, value_flags=("--req", "--action", "--grant"),
                       bool_flags=(), multi_flags=("--fact",))
    req_id = opts.get("--req")
    action = opts.get("--action")
    if not req_id or not action:
        raise UsageError("--req and --action are required")
    _req_id(req_id)
    store, policy, registry, grants = _open()
    auth, agent, agent_id, inner, legacy_from = inbound.reverify(store, registry, policy, req_id, time.time())
    grant_id = opts.get("--grant") or (inner.get("grant_id") if inner else None)
    facts = _facts(opts.get("--fact", []))
    if not do_authorize:
        d = decide(policy, auth=auth, agent=agent, action=action, grant_id=grant_id,
                   facts=facts, grants=grants, now=time.time())
        _out({"decision": d.decision, "reason": d.reason, "grant_id": d.grant_id,
              "window_s": d.window_s, "policy_hash": d.policy_hash})
        return {"allow": EXIT_OK, "allow_with_grant": EXIT_OK, "ask_user": EXIT_ASK,
                "delay": EXIT_DELAY, "reject": EXIT_DENY}.get(d.decision, EXIT_INTERNAL)
    ctx = {"auth": auth, "agent": agent, "agent_id": agent_id or "",
           "req_id": req_id, "action": action, "facts": facts,
           "grant_id": grant_id, "facts_source": "cli", "legacy_from": legacy_from}
    out = flows.authorize(ctx, policy=policy, grants=grants, ui=PopupUI(_vm_dir()),
                          store=store, now_fn=time.time)
    _out({"proceed": out.proceed, "how": out.how, "decision": out.decision,
          "reason": out.reason, "grant_id": out.grant_id})
    return EXIT_OK if out.proceed else EXIT_DENY


def _cmd_enroll_pending(args):
    if args:
        raise UsageError("enroll-pending takes no arguments")
    store, policy, registry, grants = _open()
    d = store.ensure_bridge_dir("pending")
    names = sorted((n for n in os.listdir(d) if n.endswith(".json")),
                   key=lambda n: os.path.getmtime(os.path.join(d, n)))
    # a request backing off after a transient failure waits its turn
    names = [n for n in names if flows.enroll_due(os.path.join(d, n), time.time())]
    if not names:
        _out({"ok": True, "pending": False})
        return EXIT_OK
    name = names[0]
    req_id = name[:-5]
    agent_id = ""
    try:
        with open(os.path.join(d, name), "rb") as f:
            agent_id = str((json.loads(f.read().decode("utf-8")).get("inner") or {}).get("agent_id") or "")
    except Exception:
        pass
    out = flows.enroll(os.path.join(d, name), registry=registry, grants=grants,
                       ui=PopupUI(_vm_dir()), pc=PcAdapter(_vm_dir()), store=store,
                       policy=policy, now_fn=time.time)
    _out({"ok": out.proceed, "pending": True, "req_id": req_id, "agent_id": agent_id,
          "proceed": out.proceed, "how": out.how, "reason": out.reason, "final": out.final})
    return EXIT_OK if out.proceed else EXIT_DENY


def _cmd_agents(args):
    if not args:
        raise UsageError("agents list|show|set-tier|revoke|rekey|provision")
    sub = args[0]
    rest = args[1:]
    store, policy, registry, grants = _open()
    if sub == "list":
        _out(registry.public_directory(time.time()))
        return EXIT_OK
    if sub == "show":
        if len(rest) != 1:
            raise UsageError("agents show <id>")
        e = registry.get(rest[0])
        if e is None:
            _out({"ok": False, "reason": "unknown_agent"})
            return EXIT_DENY
        safe = {k: v for k, v in e.items() if k not in ("seal", "keys")}
        safe["key_ids"] = [k.get("key_id") for k in e.get("keys", [])]
        _out(safe)
        return EXIT_OK
    if sub == "set-tier":
        if len(rest) != 2:
            raise UsageError("agents set-tier <id> <tier>")
        agent_id, tier = rest
        if tier not in ("whitelist", "ask-first") or registry.get(agent_id) is None:
            raise UsageError("unknown agent or tier (whitelist | ask-first)")   # before any card
        ui = PopupUI(_vm_dir())
        rendered = labels("id.ask.opt.")
        ans = ui.choice(t('id.tier.body', agent=agent_id, tier=tier),
                        [rendered[k] for k in [flows.ASK_DENY, flows.ASK_ALLOW]], policy.ask_card_timeout_s)
        if getattr(ans, "kind", "") == "answered" and answer_key(getattr(ans, "answer", ""), rendered) == flows.ASK_ALLOW:
            ev = {"popup_req": ans.popup_req, "answer": ans.answer, "at": int(time.time())}
            registry.set_tier(agent_id, tier, ev, int(time.time()))
            _out({"ok": True, "agent_id": agent_id, "tier": tier})
            return EXIT_OK
        _out({"ok": False, "reason": "declined"})
        return EXIT_DENY
    if sub == "rekey":
        if len(rest) != 1:
            raise UsageError("agents rekey <id>")
        agent_id = rest[0]
        ui = PopupUI(_vm_dir())
        rendered = labels("id.rekey.opt.")
        ans = ui.choice(t('id.rekey.manual', agent=agent_id),
                        [rendered[k] for k in [flows.REKEY_NO, flows.REKEY_OK]], policy.ask_card_timeout_s)
        if getattr(ans, "kind", "") == "answered" and answer_key(getattr(ans, "answer", ""), rendered) == flows.REKEY_OK:
            ev = {"popup_req": ans.popup_req, "answer": ans.answer, "at": int(time.time())}
            key_id, key = registry.rekey(agent_id, ev, int(time.time()))
            rec = {"v": 1, "agent_id": agent_id, "key_id": key_id,
                   "key": __import__("base64").b64encode(key).decode("ascii"),
                   "enrolled_at": int(time.time())}
            try:
                PcAdapter(_vm_dir()).write_identity(agent_id, rec)
            except Exception:
                _out({"ok": True, "agent_id": agent_id, "key_id": key_id, "provision_failed": True})
                return EXIT_OK
            _out({"ok": True, "agent_id": agent_id, "key_id": key_id})
            return EXIT_OK
        _out({"ok": False, "reason": "declined"})
        return EXIT_DENY
    if sub == "revoke":
        if len(rest) != 1:
            raise UsageError("agents revoke <id>")
        agent_id = rest[0]
        now = int(time.time())
        registry.revoke(agent_id, "cli", now)
        n = grants.revoke_agent(agent_id, "agent revoked", now)
        try:
            PcAdapter(_vm_dir()).remove_identity(agent_id)
        except Exception as e:
            _err("PC identity file not removed: " + str(e)[:120])
        _mirror_best_effort(registry, grants)
        _out({"ok": True, "agent_id": agent_id, "grants_revoked": n})
        return EXIT_OK
    if sub == "provision":
        if len(rest) != 1:
            raise UsageError("agents provision <id>")
        agent_id = rest[0]
        key_id, key = registry.active_key(agent_id)
        rec = {"v": 1, "agent_id": agent_id, "key_id": key_id,
               "key": __import__("base64").b64encode(key).decode("ascii"),
               "enrolled_at": int(time.time())}
        PcAdapter(_vm_dir()).write_identity(agent_id, rec)
        _out({"ok": True, "agent_id": agent_id, "key_id": key_id})
        return EXIT_OK
    raise UsageError("unknown agents subcommand " + sub)


def _cmd_grants(args):
    if not args:
        raise UsageError("grants propose|list|show|revoke|revoke-agent")
    sub = args[0]
    rest = args[1:]
    store, policy, registry, grants = _open()
    if sub == "propose":
        opts, pos = _parse(rest, value_flags=("--agent", "--action", "--days", "--until",
                                              "--uses", "--mode", "--note", "--requested-by"),
                           bool_flags=("--auto-match",), multi_flags=("--where",))
        if not opts.get("--agent") or not opts.get("--action"):
            raise UsageError("--agent and --action are required")
        now = int(time.time())
        conditions = []
        for w in opts.get("--where", []):
            parts = w.split(":", 2)
            if len(parts) != 3:
                raise UsageError("--where FIELD:OP:VALUE")
            conditions.append({"field": parts[0], "op": parts[1], "value": _fact_value(parts[2])})
        if opts.get("--until"):
            import datetime
            try:
                expires = int(datetime.datetime.fromisoformat(opts["--until"]).timestamp())
            except Exception:
                raise UsageError("bad --until")
        else:
            days = int(opts.get("--days", "7"))
            expires = now + days * 86400
        spec = {
            "agent_id": opts["--agent"],
            "action": opts["--action"],
            "conditions": conditions,
            "not_before": now,
            "expires_at": expires,
            "max_uses": int(opts.get("--uses", "1")),
            "mode": opts.get("--mode", "allow"),
            "auto_match": bool(opts.get("--auto-match")),
            "note": opts.get("--note", ""),
        }
        g = grants.propose(spec, opts.get("--requested-by", "user-chat"), now)
        ok = flows.grant_ceremony(g["grant_id"], grants=grants, ui=PopupUI(_vm_dir()),
                                  pc=PcAdapter(_vm_dir()), store=store, now_fn=time.time)
        final = grants.get(g["grant_id"]) or {}
        _out({"ok": bool(ok), "grant_id": g["grant_id"], "status": final.get("status")})
        return EXIT_OK if ok else EXIT_DENY
    if sub == "list":
        opts, pos = _parse(rest, value_flags=("--agent",))
        items = grants.public_list(int(time.time()))
        if opts.get("--agent"):
            items = [g for g in items if g.get("agent_id") == opts["--agent"]]
        _out({"grants": items})
        return EXIT_OK
    if sub == "show":
        if len(rest) != 1:
            raise UsageError("grants show <id>")
        g = grants.get(rest[0])
        if g is None:
            _out({"ok": False, "reason": "unknown_grant"})
            return EXIT_DENY
        safe = {k: v for k, v in g.items() if k not in ("seal", "approval")}
        _out(safe)
        return EXIT_OK
    if sub == "revoke":
        opts, pos = _parse(rest, value_flags=("--reason",))
        if len(pos) != 1:
            raise UsageError("grants revoke <id> [--reason R]")
        grants.revoke(pos[0], opts.get("--reason", "cli"), int(time.time()))
        _mirror_best_effort(registry, grants)
        _out({"ok": True, "grant_id": pos[0]})
        return EXIT_OK
    if sub == "revoke-agent":
        if len(rest) != 1:
            raise UsageError("grants revoke-agent <id>")
        n = grants.revoke_agent(rest[0], "cli", int(time.time()))
        _mirror_best_effort(registry, grants)
        _out({"ok": True, "agent_id": rest[0], "grants_revoked": n})
        return EXIT_OK
    raise UsageError("unknown grants subcommand " + sub)


def _cmd_mirror(args):
    if args:
        raise UsageError("mirror takes no arguments")
    store, policy, registry, grants = _open()
    pc = PcAdapter(_vm_dir())
    now = int(time.time())
    pc.write_directory("agents.json", registry.public_directory(now))
    pc.write_directory("grants.json", grants.public_list(now))
    _out({"ok": True})
    return EXIT_OK


def _cmd_doctor(args):
    if args:
        raise UsageError("doctor takes no arguments")
    problems = []
    try:
        store = Store().open()
    except Exception as e:
        _out({"ok": False, "problem": "store", "detail": str(e)[:200]})
        return EXIT_INTERNAL
    try:
        policy = Policy.from_file(_policy_path())
    except Exception as e:
        problems.append({"problem": "policy", "detail": str(e)[:200]})
        policy = None
    registry = Registry(store)
    grants = Grants(store, policy, registry) if policy else None
    try:
        d = registry._load()
        for agent_id in d.get("agents", {}):
            try:
                registry.get(agent_id)
            except Exception as e:
                problems.append({"problem": "registry_seal", "agent_id": agent_id, "detail": str(e)[:120]})
    except Exception as e:
        problems.append({"problem": "registry", "detail": str(e)[:200]})
    if grants:
        try:
            for g in grants.public_list(int(time.time())):
                if g.get("status") == "active" and g.get("expires_at", 0) - time.time() < 3 * 86400:
                    problems.append({"problem": "grant_expiring", "grant_id": g["grant_id"]})
        except Exception as e:
            problems.append({"problem": "grants", "detail": str(e)[:200]})
    try:
        conn = store.db()
        try:
            n = conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
            r = conn.execute("SELECT COUNT(*) FROM grant_uses WHERE state='reserved'").fetchone()[0]
        finally:
            conn.close()
        if r:
            problems.append({"problem": "open_reservations", "count": int(r)})
        # requirement 7: a clock that went backwards revives expired grants
        try:
            ap = store.path("audit.jsonl")
            if os.path.exists(ap):
                with open(ap, "rb") as f:
                    f.seek(max(0, os.path.getsize(ap) - 4096))
                    tail = [l for l in f.read().decode("utf-8", "replace").splitlines() if l.strip()]
                last = json.loads(tail[-1]).get("ts", 0) if tail else 0
                if last and time.time() + 60 < float(last):
                    problems.append({"problem": "clock_behind_audit", "newest_audit_ts": last})
        except Exception as e:
            problems.append({"problem": "audit_unreadable", "detail": str(e)[:120]})
        try:
            ad = store.bridge_path("alerts")
            na = len([x for x in os.listdir(ad) if x.endswith(".txt")]) if os.path.isdir(ad) else 0
            if na:
                problems.append({"problem": "alerts_present", "count": na})
        except OSError:
            pass
        _out({"ok": not problems, "seen_rows": int(n), "problems": problems})
    except Exception as e:
        _out({"ok": False, "problems": problems + [{"problem": "state", "detail": str(e)[:200]}]})
        return EXIT_INTERNAL
    return EXIT_OK if not problems else EXIT_DENY


def _main(argv):
    if not argv:
        _err("usage: muselink-id <command> ...")
        return EXIT_USAGE
    cmd = argv[0]
    rest = argv[1:]
    if cmd == "route":
        return _cmd_route(rest)
    if cmd == "sign-reply":
        return _cmd_sign_reply(rest)
    if cmd == "check":
        return _cmd_check(rest, False)
    if cmd == "authorize":
        return _cmd_check(rest, True)
    if cmd == "enroll-pending":
        return _cmd_enroll_pending(rest)
    if cmd == "agents":
        return _cmd_agents(rest)
    if cmd == "grants":
        return _cmd_grants(rest)
    if cmd == "mirror":
        return _cmd_mirror(rest)
    if cmd == "doctor":
        return _cmd_doctor(rest)
    _err("unknown command: " + cmd)
    return EXIT_USAGE


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        return _main(argv)
    except UsageError as e:
        _err("usage: " + str(e))
        return EXIT_USAGE
    except (PolicyError, GrantError, AuthError, IdentityError) as e:
        _out({"ok": False, "error": e.code, "detail": str(e)[:200]})
        return EXIT_INTERNAL
    except Exception as e:
        _out({"ok": False, "error": type(e).__name__, "detail": str(e)[:200]})
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
