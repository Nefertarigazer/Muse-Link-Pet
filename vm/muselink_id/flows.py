"""flows - the deterministic flows that involve the user.

authorize() is what every enforcement point calls; enroll() and
grant_ceremony() are the only paths that create agents and grants.

Requirements
  1. authorize(ctx, ...) -> Outcome. ctx keys: auth ("verified" |
     "legacy_unsigned"), agent (registry entry or None), agent_id, req_id,
     action, facts, grant_id. Steps: policy.decide() -> act on the decision:
       allow             proceed, how "allow"
       allow_with_grant  grants.consume(); ok -> proceed, how "grant";
                         not ok (e.g. used up meanwhile) -> ask flow with
                         reason "grant_<reason>"
       delay             delay flow (requirement 2)
       ask_user          ask flow (requirement 3)
       reject            no card, proceed False, how "rejected"
     Any exception -> proceed False, how "internal_error" (fail closed),
     and the reservation (if any) is released. Exactly one audit line
     "authorize" per call: req_id, agent_id, action, decision, reason,
     grant_id, how, proceed, policy_hash, fact NAMES (not values).
  2. Delay flow: grants.reserve(ttl_s = window_s + 300); reserve fails ->
     ask flow. Card: ui.choice(delay_text, [DELAY_NOW, DELAY_CANCEL],
     window_s, expire=True). The text names the agent, the action, the key
     facts, the grant id with uses left, and the clock time at which it
     proceeds, and says that closing the card cancels.
       answered DELAY_NOW   -> confirm -> proceed, how "delay_now"
       timeout              -> confirm -> proceed, how "delay_passed"
       answered DELAY_CANCEL or closed -> release, how "cancelled"
       refused / error      -> release, how "card_unavailable" (the user
                               never saw it, so it must NOT proceed)
     confirm() failing -> proceed False, how "grant_<reason>".
  3. Ask flow: ui.choice(ask_text, [ASK_ALLOW, ASK_DENY],
     policy.ask_card_timeout_s). Only answered ASK_ALLOW proceeds (how
     "user_approved", evidence recorded in the Outcome); timeout, closed,
     ASK_DENY, refused, error -> proceed False with how "user_declined",
     "timeout", "closed", "card_unavailable". Optional last task: a third
     option ASK_ALLOW_AND_GRANT when policy.grant_templates has the action
     and auth is "verified"; it creates a grant from the template whose
     full render() text was on the card, activates it with this card's
     evidence, then consumes it for this request.
  4. Card texts are built from structured data only. Agent-supplied
     strings (client.display_name etc.) go through clean(): control chars
     and newlines removed, at most 40 chars, and labelled as self-declared
     ("代理自称"). Request `text` is never put on an authorization card.
  5. enroll(pending_path, ...): pending_path is the
     $ML_BRIDGE_HOME/pending/<req_id>.json file written by inbound; on any
     final outcome it moves to processed/. One enrollment at a time (store.lock
     ("enroll")). New agent_id -> card ENROLL_OPTIONS with
     policy.enroll_limits.card_timeout_s; answered ENROLL_ASK_FIRST /
     ENROLL_WHITELIST -> registry.add(tier) -> pc.write_identity() ->
     mirror both directory files -> rewrite verdicts/<req_id>.json with
     auth "verified" (so the reply script signs the final reply with the
     new key) -> Outcome proceed True, how "enrolled:<tier>". Existing agent_id -> REKEY_OPTIONS card with a
     warning that this replaces the key and may be an impersonation
     attempt; REKEY_OK -> registry.rekey() -> pc.write_identity(). Anything
     else -> proceed False ("declined" / "timeout" / "card_unavailable").
     If pc.write_identity() fails the registry change stays (the key only
     exists on the VM, which is harmless) and how is "provision_failed":
     `muselink-id agents provision <id>` re-sends the active key later.
     Since 2026-11-13 the card is an owned, expiring card that survives its
     waiter (pending/<req_id>.card, see card_state_path): a busy slot, a
     broken wait or a crash leaves the request in pending/ (Outcome
     final=False, back-off) and the next run resumes the SAME card or
     continues after the recorded click. Only a user decision, the card's
     expiry or ENROLL_MAX_ATTEMPTS transient failures end it.
  6. grant_ceremony(grant_id, ...): card = grants.render(grant) +
     GRANT_FOOTER, options [GRANT_NO, GRANT_OK]; only answered GRANT_OK ->
     grants.activate(evidence) and mirror; else grants.decline(). Returns
     True only when activated.
  7. Every card's evidence = {"popup_req": answer.popup_req, "answer":
     answer.answer, "at": now}. No evidence, no state change.
"""
from muselink_id.i18n import t, labels, answer_key, active

import base64
import json
import os
import re
import time
from collections import namedtuple

from . import AuthError, GrantError
from .policy import decide

# final=False (enroll only): nothing was decided yet - the request stays in
# pending/ and is picked up again (busy card slot, PC unreachable, a crash).
Outcome = namedtuple("Outcome", "proceed how decision reason grant_id evidence final",
                     defaults=(True,))

ASK_ALLOW = 'id.ask.opt.allow'
ASK_DENY = 'id.ask.opt.deny'
ASK_ALLOW_AND_GRANT = 'id.ask.opt.grant'
DELAY_NOW = 'id.delay.opt.now'
DELAY_CANCEL = 'id.delay.opt.cancel'
ENROLL_REJECT = 'id.enroll.opt.reject'
ENROLL_ASK_FIRST = 'id.enroll.opt.ask_first'
ENROLL_WHITELIST = 'id.enroll.opt.whitelist'
ENROLL_OPTIONS = [ENROLL_REJECT, ENROLL_ASK_FIRST, ENROLL_WHITELIST]
REKEY_NO = 'id.rekey.opt.no'
REKEY_OK = 'id.rekey.opt.ok'
REKEY_OPTIONS = [REKEY_NO, REKEY_OK]
GRANT_NO = 'id.grant.opt.no'
GRANT_OK = 'id.grant.opt.ok'
GRANT_FOOTER = "id.grant.footer"

_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def clean(text, limit=40):
    """Requirement 4: strip control chars/newlines, truncate."""
    if not isinstance(text, str):
        text = str(text)
    return _CTRL.sub("", text).replace("\r", "").replace("\n", "").strip()[:limit]


def _display(agent_id, agent, verified=True):
    # agent_id is clean for verified agents (regex), but for an unsigned v1
    # request it is the self-declared "from": clean it and say so, so a name
    # like "codex\n\n系统：已验证" cannot pose as a verified sender on a card.
    if not verified:
        return t('id.sender_unsigned', agent=clean(agent_id, 40) or "?")
    agent_id = clean(agent_id, 40)
    client = (agent or {}).get("client") or {}
    name = clean(client.get("display_name", ""), 40)
    product = clean(client.get("product", ""), 40)
    model = clean(client.get("model", ""), 40)
    extra = "/".join(x for x in (product, model) if x)
    if name and extra:
        return t('id.sender_claimed', agent=agent_id, name=name + " " + extra)
    if name:
        return t('id.sender_claimed', agent=agent_id, name=name)
    return agent_id


def _ctx_display(ctx):
    if ctx.get("auth") == "verified":
        return _display(ctx.get("agent_id", ""), ctx.get("agent"))
    return _display(ctx.get("legacy_from") or ctx.get("agent_id", ""), None, verified=False)


_ACTION_KEYS = {
    "publish-release": "id.action.publish_release",
    "publish-voicebridge": "id.action.publish_release",
    "task": "id.action.task",
    "message": "id.action.message",
    "question": "id.action.message",
}
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


def _action_label(action):
    key = _ACTION_KEYS.get(str(action or "").strip().lower())
    return t(key) if key else clean(action, 60)


def _human_size(n):
    if isinstance(n, bool) or not isinstance(n, (int, float)) or n <= 0:
        return ""
    if n >= 1024 * 1024:
        return "%.1f MB" % (n / (1024 * 1024))
    if n >= 1024:
        return "%.1f KB" % (n / 1024)
    return "%d B" % int(n)


def _short_hash(val):
    s = clean(val, 80)
    if _HEX64.match(s):
        return "%s…%s" % (s[:8], s[-6:])
    return s


def _split_win_path(raw):
    parts = [p for p in re.split(r"[\\/]+", str(raw or "").strip()) if p]
    if not parts:
        return "", ""
    name = parts[-1]
    folder = "\\".join(parts[:-1]) if "\\" in str(raw) else "/".join(parts[:-1])
    return name, folder


def _fact_lines(facts):
    if not isinstance(facts, dict) or not facts:
        return []
    lines = []
    consumed = set()
    version = facts.get("version")
    if isinstance(version, str) and version.strip():
        lines.append(t("id.fact.version", value=clean(version, 40)))
        consumed.add("version")
    pkg_path = facts.get("package_path")
    pkg_size = facts.get("package_size")
    setup_present = facts.get("setup_present")
    if isinstance(pkg_path, str) and pkg_path.strip():
        name, folder = _split_win_path(pkg_path)
        size_txt = _human_size(pkg_size)
        extras = []
        if size_txt:
            extras.append(size_txt)
        if setup_present is True:
            extras.append(t("id.fact.with_setup").lstrip("，, "))
        zh = getattr(active(), "lang", "") == "zh"
        extra_str = ("（%s）" % "，".join(extras)) if extras and zh else ((" (%s)" % ", ".join(extras)) if extras else "")
        lines.append(t("id.fact.package", name=clean(name or pkg_path, 60), extra=extra_str))
        if folder:
            lines.append(t("id.fact.folder", value=clean(folder, 64)))
        consumed.update(("package_path", "package_size", "setup_present"))
    elif setup_present is True:
        lines.append(t("id.fact.setup"))
        consumed.add("setup_present")
    elif setup_present is False:
        consumed.add("setup_present")
    sha = facts.get("package_sha256")
    if isinstance(sha, str) and sha.strip():
        lines.append(t("id.fact.hash", value=_short_hash(sha)))
        consumed.add("package_sha256")
    if facts.get("test") is True:
        lines.append(t("id.fact.test"))
        consumed.add("test")
    elif facts.get("test") is False:
        consumed.add("test")
    for k in sorted(facts):
        if k in consumed or len(lines) >= 8:
            continue
        v = facts[k]
        if not (isinstance(v, (str, int, float, bool)) or v is None):
            continue
        if isinstance(v, bool):
            val_txt = t("id.fact.yes") if v else t("id.fact.no")
        elif (k.endswith("_size") or k.endswith("_bytes")) and isinstance(v, (int, float)):
            val_txt = _human_size(v) or str(v)
        elif isinstance(v, str) and _HEX64.match(v.strip()):
            val_txt = _short_hash(v)
        else:
            val_txt = clean(str(v), 80)
        lines.append("%s: %s" % (clean(k, 40), val_txt))
    return lines


def _mirror(registry, grants, pc, now):
    try:
        pc.write_directory("agents.json", registry.public_directory(now))
    except Exception:
        pass
    try:
        pc.write_directory("grants.json", grants.public_list(now))
    except Exception:
        pass


def _rewrite_verdict(store, req_id, agent_id):
    p = store.bridge_path("verdicts", req_id + ".json")
    try:
        with open(p, "rb") as f:
            d = json.loads(f.read().decode("utf-8"))
    except Exception:
        d = {"route": "inbox", "req_id": req_id, "agent_id": agent_id}
    d["auth"] = "verified"
    d["agent_id"] = agent_id
    store.write_bytes(p, json.dumps(d, ensure_ascii=False, indent=2).encode("utf-8"))


def _evidence(answer, now):
    return {"popup_req": answer.popup_req, "answer": answer.answer, "at": now}


def _ask(ctx, policy, ui, now_fn, reason):
    action = ctx.get("action", "")
    facts = ctx.get("facts") or {}
    rendered = labels("id.ask.opt.")
    lines = [t('id.request', agent=_ctx_display(ctx), action=_action_label(action))]
    lines.extend(_fact_lines(facts))
    lines.append(t('id.allow_question'))
    try:
        answer = ui.choice("\n".join(lines), [rendered[k] for k in (ASK_ALLOW, ASK_DENY)], policy.ask_card_timeout_s)
    except Exception:
        return False, "internal_error", None, reason
    kind = getattr(answer, "kind", "error")
    label = answer_key(getattr(answer, "answer", ""), rendered)
    if kind == "answered" and label == ASK_ALLOW:
        return True, "user_approved", _evidence(answer, int(now_fn())), reason
    if kind == "answered":
        return False, "user_declined", None, reason
    if kind == "timeout":
        return False, "timeout", None, reason
    if kind == "closed":
        return False, "closed", None, reason
    return False, "card_unavailable", None, reason


def authorize(ctx, *, policy, grants, ui, store, now_fn):
    now = int(now_fn())
    req_id = ctx.get("req_id", "")
    agent_id = ctx.get("agent_id", "")
    action = ctx.get("action", "")
    facts = ctx.get("facts") or {}
    grant_id = ctx.get("grant_id")
    decision = None
    reason = ""
    used_grant = None
    evidence = None
    proceed = False
    how = "internal_error"
    reserved = None
    try:
        decision = decide(policy, auth=ctx.get("auth", "verified"), agent=ctx.get("agent"),
                                 action=action, grant_id=grant_id, facts=facts, grants=grants, now=now)
        reason = decision.reason
        if decision.decision == "allow":
            proceed, how = True, "allow"
        elif decision.decision == "allow_with_grant":
            used_grant = decision.grant_id
            r = grants.consume(decision.grant_id, agent_id, req_id, action, facts, now)
            if r.ok:
                proceed, how = True, "grant"
                reason = "grant_ok"
            else:
                used_grant = None
                reason = "grant_" + r.reason
                proceed, how, evidence, reason = _ask(ctx, policy, ui, now_fn, reason)
        elif decision.decision == "delay":
            used_grant = decision.grant_id
            window = int(decision.window_s or policy.delay_window_s)
            r = grants.reserve(decision.grant_id, agent_id, req_id, action, facts, now, window + 300)
            if not r.ok:
                used_grant = None
                reason = "grant_" + r.reason
                proceed, how, evidence, reason = _ask(ctx, policy, ui, now_fn, reason)
            else:
                reserved = decision.grant_id
                try:
                    rendered = labels("id.delay.opt.")
                    until = time.strftime("%H:%M:%S", time.localtime(now + window))
                    lines = [t('id.request', agent=_ctx_display(ctx), action=_action_label(action))]
                    lines.extend(_fact_lines(facts))
                    lines.append(t('id.grant_remaining', grant=decision.grant_id, n=r.uses_left))
                    lines.append(t('id.delay.body', time=until, cancel=rendered[DELAY_CANCEL]))
                    ans = ui.choice("\n".join(lines), [rendered[k] for k in (DELAY_NOW, DELAY_CANCEL)], window, expire=True)
                except Exception:
                    grants.release(decision.grant_id, req_id, int(now_fn()))
                    reserved = None
                    raise
                kind = getattr(ans, "kind", "error")
                label = answer_key(getattr(ans, "answer", ""), rendered)
                # The card took up to window_s; decide on the clock as it is
                # now (a grant that expired or was revoked meanwhile must stop).
                after = int(now_fn())
                if kind == "answered" and label == DELAY_NOW:
                    c = grants.confirm(decision.grant_id, req_id, after)
                    reserved = None
                    if c.ok:
                        proceed, how = True, "delay_now"
                        reason = "delay_now"
                    else:
                        proceed, how = False, "grant_" + c.reason
                        reason = how
                elif kind == "timeout":
                    c = grants.confirm(decision.grant_id, req_id, after)
                    reserved = None
                    if c.ok:
                        proceed, how = True, "delay_passed"
                        reason = "delay_passed"
                    else:
                        proceed, how = False, "grant_" + c.reason
                        reason = how
                else:
                    grants.release(decision.grant_id, req_id, after)
                    reserved = None
                    proceed = False
                    if kind == "answered" and label == DELAY_CANCEL:
                        how, reason = "cancelled", "cancelled"
                    elif kind == "closed":
                        how, reason = "cancelled", "closed"
                    else:
                        how, reason = "card_unavailable", "card_unavailable"
        elif decision.decision == "ask_user":
            used_grant = None
            proceed, how, evidence, reason = _ask(ctx, policy, ui, now_fn, decision.reason)
        else:
            proceed, how = False, "rejected"
            used_grant = None
    except Exception:
        proceed, how = False, "internal_error"
        if reserved:
            try:
                grants.release(reserved, req_id, now)
            except Exception:
                pass
        used_grant = None
    finally:
        try:
            store.audit("authorize", now, req_id=req_id, agent_id=agent_id, action=action,
                        decision=(decision.decision if decision else None),
                        reason=reason, grant_id=used_grant, how=how, proceed=bool(proceed),
                        policy_hash=(decision.policy_hash if decision else None),
                        fact_names=sorted(facts.keys()),
                        facts_source=ctx.get("facts_source"))
        except Exception:
            pass
    return Outcome(bool(proceed), how, (decision.decision if decision else None),
                   reason, used_grant, evidence)


# Enrollment survives its own waiter (defect 2026-11-13). Next to
# pending/<req_id>.json lives pending/<req_id>.card (JSON, not *.json so no
# scanner takes it for a request): {popup_req, staged_at} once the card is up,
# {answer, answer_popup_req} once the user clicked, {attempts, next_try_at}
# after a transient failure. A killed waiter, a service restart or a busy
# card slot therefore never loses the request: the next run waits on the SAME
# card (a click made meanwhile still counts) or continues after the click.
ENROLL_MAX_ATTEMPTS = 5
ENROLL_RETRY_S = 60
CARD_RETIRED = "card_retired"     # ui error detail: the waited-on card is gone
ENROLL_SLOT_WAIT_S = 6 * 3600     # how long a request waits for a busy card slot


def card_state_path(pending_path):
    return pending_path[:-5] + ".card" if pending_path.endswith(".json") else pending_path + ".card"


def _load_card_state(pending_path):
    try:
        with open(card_state_path(pending_path), "rb") as f:
            d = json.loads(f.read().decode("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_card_state(store, pending_path, state):
    store.write_bytes(card_state_path(pending_path), json.dumps(state, ensure_ascii=False).encode("utf-8"))


def enroll_due(pending_path, now):
    """False while a transient failure's back-off is running."""
    return int(_load_card_state(pending_path).get("next_try_at") or 0) <= int(now)


def _enroll_card_new(agent_id, client):
    product = "/".join(x for x in (clean(client.get("product", ""), 40), clean(client.get("model", ""), 40)) if x)
    where = "/".join(x for x in (clean(client.get("host", ""), 40), clean(client.get("session_id", ""), 40)) if x)
    return "\n".join([
        t('id.enroll.body'),
        t('id.enroll.name', agent=clean(agent_id, 40)),
        t('id.enroll.claimed', name=clean(client.get("display_name", ""), 40) or t("id.not_stated"), product=t('id.enroll.product', product=product) if product else ""),
        t('id.enroll.source', source=where or t("id.not_stated")),
        "",
        t('id.enroll.whitelist'),
        t('id.enroll.ask_first'),
        t('id.enroll.unknown', deny=t(ENROLL_REJECT)),
    ])


def _enroll_card_rekey(agent_id, client, revoked=False):
    lines = [t('id.rekey.body', agent=_display(agent_id, {"client": client}))]
    if revoked:
        # the user decides on the revocation knowingly, on this card
        lines.append(t('id.rekey.revoked', ok=t(REKEY_OK)))
    lines.append(t('id.rekey.warning', deny=t(REKEY_NO)))
    return "\n".join(lines)


def _accepts(fn, name):
    import inspect
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def enroll(pending_path, *, registry, grants, ui, pc, store, policy, now_fn):
    now = int(now_fn())
    try:
        with open(pending_path, "rb") as f:
            pending = json.loads(f.read().decode("utf-8"))
        inner = pending.get("inner") or {}
        req_id = inner["req_id"]
        agent_id = inner["agent_id"]
        client = inner.get("client") or {}
    except Exception:
        _enroll_finish(store, pending_path)
        _enroll_audit(store, now, "?", "", "internal_error", "bad_pending")
        return Outcome(False, "internal_error", None, "bad_pending", None, None)

    def transient(how, reason, keep_card=True):
        state = _load_card_state(pending_path)
        t = int(now_fn())
        if reason == "refused":
            # Another card holds the slot (Muse, another agent, a stale card).
            # That is not this request failing: wait for the slot without
            # spending attempts (run 4 on the real PC gave up this way), up to
            # ENROLL_SLOT_WAIT_S after the first refusal.
            first = int(state.setdefault("slot_wait_since", t))
            if t - first >= ENROLL_SLOT_WAIT_S:
                _enroll_finish(store, pending_path)
                _enroll_audit(store, now, req_id, agent_id, how, "slot_busy:gave_up")
                return Outcome(False, how, None, "slot_busy:gave_up", None, None, True)
            state.update(next_try_at=t + ENROLL_RETRY_S)
            state.pop("popup_req", None)
            state.pop("staged_at", None)
            state.pop("rendered", None)
            try:
                _save_card_state(store, pending_path, state)
            except OSError:
                pass
            _enroll_audit(store, now, req_id, agent_id, how, "slot_busy:retry")
            return Outcome(False, how, None, "slot_busy", None, None, False)
        attempts = int(state.get("attempts") or 0) + 1
        if attempts >= ENROLL_MAX_ATTEMPTS:
            _enroll_finish(store, pending_path)
            _enroll_audit(store, now, req_id, agent_id, how, reason + ":gave_up")
            return Outcome(False, how, None, reason + ":gave_up", None, None, True)
        state.update(attempts=attempts, next_try_at=t + ENROLL_RETRY_S * attempts)
        # Keep waiting on a card that is up (a broken wait, a crash); forget
        # one that never came up or was retired, so the next try stages anew.
        if not keep_card and not state.get("answer"):
            state.pop("popup_req", None)
            state.pop("staged_at", None)
            state.pop("rendered", None)
        try:
            _save_card_state(store, pending_path, state)
        except OSError:
            pass
        _enroll_audit(store, now, req_id, agent_id, how, reason + ":retry")
        return Outcome(False, how, None, reason, None, None, False)

    with store.lock("enroll"):
        state = _load_card_state(pending_path)
        timeout_s = policy.enroll_limits["card_timeout_s"]
        rendered = state.get("rendered", {}).get("labels")
        legacy = not rendered and (state.get("popup_req") or state.get("answer"))
        try:
            try:
                entry = registry.get(agent_id)
            except AuthError:
                entry, existing = None, True
            else:
                existing = entry is not None
            revoked = bool(entry and entry.get("status") == "revoked")
            if legacy:
                rendered = labels("id.rekey.opt." if existing else "id.enroll.opt.", tag="zh-Hans")
                state["rendered"] = {"tag": "zh-Hans", "labels": rendered}
            # resumed after the click: our own registry.add already happened
            ours = bool(entry and state.get("answer_popup_req")
                        and (entry.get("approval") or {}).get("popup_req") == state.get("answer_popup_req"))
            if state.get("answer"):
                ans = _Answer("answered", state["answer"], state.get("answer_popup_req", ""))
            else:
                text = _enroll_card_rekey(agent_id, client, revoked) if existing else _enroll_card_new(agent_id, client)
                keys = REKEY_OPTIONS if existing else ENROLL_OPTIONS
                if not rendered:
                    rendered = labels("id.rekey.opt." if existing else "id.enroll.opt.")
                    state["rendered"] = {"tag": active().ui, "labels": rendered}
                options = [rendered[k] for k in keys]
                extra = {}
                if _accepts(ui.choice, "on_staged"):
                    def staged(popup_req):
                        state.update(popup_req=popup_req, staged_at=int(now_fn()))
                        _save_card_state(store, pending_path, state)
                    extra = {"on_staged": staged,
                             "resume": ({"popup_req": state["popup_req"], "staged_at": state.get("staged_at")}
                                        if state.get("popup_req") else None)}
                # the card closes itself at the deadline, so a late click can
                # never land on a card nobody waits for any more
                ans = ui.choice(text, options, timeout_s, expire=True, **extra)
            kind = getattr(ans, "kind", "error")
            raw_label = getattr(ans, "answer", "")
            label = answer_key(raw_label, rendered)
            if kind == "answered":
                state.update(answer=raw_label, answer_popup_req=getattr(ans, "popup_req", ""))
                _save_card_state(store, pending_path, state)
            if kind in ("refused", "error"):
                return transient("card_unavailable", kind,
                                 keep_card=(kind == "error" and raw_label != CARD_RETIRED))
            evidence = None
            proceed, how = False, "declined"
            if existing and ours and kind == "answered" and label == REKEY_OK:
                # resumed after the click: our own rekey already happened
                now = int(now_fn())
                evidence = _evidence(ans, now)
                key_id, key = registry.active_key(agent_id)
                proceed, how = _provision(pc, registry, grants, store, req_id, agent_id, key_id, key, now, "rekeyed")
            elif existing and not ours:
                if kind == "answered" and label == REKEY_OK:
                    now = int(now_fn())          # the card may have waited 30 min
                    evidence = _evidence(ans, now)
                    key_id, key = registry.rekey(agent_id, evidence, now, reactivate=revoked)
                    proceed, how = _provision(pc, registry, grants, store, req_id, agent_id, key_id, key, now,
                                              "restored" if revoked else "rekeyed")
                elif kind == "timeout":
                    how = "timeout"
                elif kind == "closed":
                    how = "closed"
            else:
                tier = None
                if kind == "answered" and label == ENROLL_ASK_FIRST:
                    tier = "ask-first"
                elif kind == "answered" and label == ENROLL_WHITELIST:
                    tier = "whitelist"
                if tier:
                    now = int(now_fn())
                    evidence = _evidence(ans, now)
                    if ours:
                        key_id, key = registry.active_key(agent_id)
                    else:
                        key_id, key = registry.add(agent_id, tier, client, evidence, now)
                    proceed, how = _provision(pc, registry, grants, store, req_id, agent_id, key_id, key, now,
                                              "enrolled:" + tier)
                elif kind == "timeout":
                    how = "timeout"
                elif kind == "closed":
                    how = "closed"
                elif kind == "answered" and label != ENROLL_REJECT:
                    how = "unrecognized_answer"   # never silently: it is in the audit
        except Exception as e:
            import sys
            import traceback
            traceback.print_exc(file=sys.stderr)    # bridge-service logs the child's stderr
            return transient("internal_error", "internal:" + type(e).__name__)
        _enroll_finish(store, pending_path)
        _enroll_audit(store, now, req_id, agent_id, how, how, popup_req=getattr(ans, "popup_req", ""),
                      answer_kind=kind)
        return Outcome(proceed, how, None, how, None, evidence)


_Answer = namedtuple("_Answer", "kind answer popup_req")


def _provision(pc, registry, grants, store, req_id, agent_id, key_id, key, now, how):
    rec = {"v": 1, "agent_id": agent_id, "key_id": key_id,
           "key": base64.b64encode(key).decode("ascii"), "enrolled_at": now}
    try:
        pc.write_identity(agent_id, rec)
    except Exception:
        # the registry change stays; `agents provision <id>` re-sends the key
        return True, "provision_failed"
    _mirror(registry, grants, pc, now)
    _rewrite_verdict(store, req_id, agent_id)
    return True, how


def _enroll_finish(store, pending_path):
    try:
        dest = store.ensure_bridge_dir("processed")
        if os.path.exists(pending_path):
            os.replace(pending_path, os.path.join(dest, os.path.basename(pending_path)))
        side = card_state_path(pending_path)
        if os.path.exists(side):
            os.replace(side, os.path.join(dest, os.path.basename(side)))
    except OSError:
        pass


def _enroll_audit(store, now, req_id, agent_id, how, reason, **extra):
    try:
        store.audit("enroll", now, req_id=req_id, agent_id=agent_id, how=how, reason=reason, **extra)
    except Exception:
        pass


def grant_ceremony(grant_id, *, grants, ui, pc, store, now_fn):
    now = int(now_fn())
    try:
        g = grants.get(grant_id)
        if g is None:
            return False
        from .grants import render
        rendered = labels("id.grant.opt.")
        text = render(g) + t(GRANT_FOOTER, allow=rendered[GRANT_OK])
        ans = ui.choice(text, [rendered[k] for k in (GRANT_NO, GRANT_OK)], 1800)
        kind = getattr(ans, "kind", "error")
        label = answer_key(getattr(ans, "answer", ""), rendered)
        if kind == "answered" and label == GRANT_OK:
            now = int(now_fn())
            grants.activate(grant_id, _evidence(ans, now), now)
            try:
                pc.write_directory("grants.json", grants.public_list(now))
            except Exception:
                pass
            return True
        try:
            grants.decline(grant_id, now)
        except Exception:
            pass
        return False
    except Exception:
        try:
            grants.decline(grant_id, now)
        except Exception:
            pass
        return False
