"""Attribution, input consent, Muse priority and body-free audit."""
from datetime import datetime, timezone
import json
import time

from i18n import t, labels, answer_key
from . import ask, trace
from ._shared import agent_id, atomic, mutex, read_json, tool_dir

ALLOW_ONCE = "plugin.consent.opt.allow_once"
ALLOW_30 = "plugin.consent.opt.allow_30"
DENY = "plugin.consent.opt.deny"
DEFAULT_POLICY = {"consent_seconds": 1800, "muse_priority_seconds": 15, "tools": {}}


def _clean(value, limit):
    text = "".join(c if c.isprintable() else " " for c in str(value))
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def decide(tier, registered, expires_at=0, now=0, muse_at=None, priority_seconds=15):
    """Pure decision: caller supplies all facts, including PC wall-clock time."""
    if tier == "read":
        return {"decision": "allow"}
    if tier == "local" and registered:
        return {"decision": "allow"}
    if tier != "input" or not registered:
        return {"decision": "deny", "status": "denied", "error": "UNREGISTERED_OR_DISABLED"}
    if muse_at is not None and now - muse_at < priority_seconds:
        return {"decision": "busy", "status": "busy", "message": "Muse 正在操作桌面",
                "retry_after_s": max(1, int(priority_seconds - (now - muse_at)) + 1)}
    return {"decision": "allow" if expires_at > now else "ask"}


def registered(base, agent):
    try:
        item = read_json(tool_dir(base) / "identities" / (agent_id(agent) + ".json"), {})
        return item.get("agent_id") == agent and item.get("v") == 1 and not item.get("revoked", False)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def muse_activity(base):
    latest = None
    # Bounded tail; inspect rotated predecessor too at rotation boundaries.
    for name in ("executor-events.jsonl.1", "executor-events.jsonl"):
        try:
            with (tool_dir(base) / name).open("rb") as f:
                f.seek(0, 2)
                start = max(0, f.tell() - 256 * 1024)
                f.seek(start)
                lines = f.read().splitlines()
                if start:
                    lines = lines[1:]
            for line in lines:
                try:
                    row = json.loads(line)
                    job = row.get("job", "")
                    if not job or job.startswith("plugin:"):
                        continue
                    stamp = row["ts"]
                    at = float(stamp) if isinstance(stamp, (int, float)) else datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                    latest = at if latest is None else max(at, latest)
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
        except FileNotFoundError:
            pass
    return latest


def _policy(base):
    policy = dict(DEFAULT_POLICY)
    policy.update(read_json(base / "plugin" / "policy.json", {}) or {})
    # Policy may tighten/disable tools, never expose a new host command.
    if not 0 < policy["consent_seconds"] <= 1800 or not 15 <= policy["muse_priority_seconds"] <= 3600:
        raise ValueError("invalid policy intervals")
    return policy


def enforce(base, agent, tool, tier, apps, target, *, ask_fn=None, intent=None):
    base = tool_dir(base)
    policy = _policy(base)
    configured = policy.get("tools", {}).get(tool, tier)
    if configured == "disabled" or (tier == "input" and configured != "input"):
        return {"decision": "deny", "status": "denied", "error": "POLICY_DISABLED"}
    tier = "input" if configured == "input" and tier != "local" else tier
    for app in apps or [""]:
        def current():
            active_policy = _policy(base)
            active_tier = active_policy.get("tools", {}).get(tool, tier)
            if active_tier == "disabled" or (tier == "input" and active_tier != "input"):
                return {"decision": "deny", "status": "denied", "error": "POLICY_DISABLED"}
            with mutex(base, "plugin-state"):
                state = read_json(base / "plugin" / "state.json", {}) or {}
                expiry = state.get(agent, {}).get(app, 0)
            return decide(tier, registered(base, agent), expiry, time.time(), muse_activity(base),
                          active_policy["muse_priority_seconds"])
        verdict = current()
        if verdict["decision"] == "ask":
            # app/target come from the agent asking for consent: one short line
            # each, so they cannot paint extra "system" lines on the card.
            text = t("plugin.consent.body", agent=ask.display_name(base, agent), app=_clean(app, 60), intent=intent or tool, target=_clean(target, 60))
            rendered = labels("plugin.consent.opt.")
            response = (ask_fn or ask.ask_user)(agent, text, "choice", [rendered[k] for k in (ALLOW_ONCE, ALLOW_30, DENY)], base=base)
            if response.get("status") != "ok":
                return {"decision": "ask", **response}
            if response.get("kind") not in ("choice", "typed", "pet_typed") or answer_key(response.get("result"), rendered) not in (ALLOW_ONCE, ALLOW_30):
                return {"decision": "deny", "status": "denied", "error": "USER_DENIED"}
            # The user's "30 minutes" answer is recorded first: a busy desktop
            # right now stops this call only, not the consent. The grant only
            # skips the card; registration, policy and Muse priority are
            # checked on every call.
            if answer_key(response["result"], rendered) == ALLOW_30:
                with mutex(base, "plugin-state"):
                    path = base / "plugin" / "state.json"
                    state = read_json(path, {}) or {}
                    now = time.time()
                    state = {a: {p: t for p, t in v.items() if t > now} for a, v in state.items()}
                    state.setdefault(agent, {})[app] = now + policy["consent_seconds"]
                    atomic(path, state)
            # A Muse job or identity/policy change while the card was open
            # must take effect before any desktop input.
            again = current()
            if again["decision"] not in ("ask", "allow"):
                return again
        elif verdict["decision"] != "allow":
            return verdict
    if tier == "input":
        # Multi-app batches can spend time asking for each app. Recheck Muse
        # after the last answer, including a batch with cached grants.
        verdict = decide(tier, registered(base, agent), time.time() + 1, time.time(), muse_activity(base),
                         policy["muse_priority_seconds"])
        if verdict["decision"] != "allow":
            return verdict
    return {"decision": "allow"}


def audit(base, agent, tool, apps, target, decision, result, elapsed_ms, query=None):
    base = tool_dir(base)
    row = dict(at=datetime.now(timezone.utc).isoformat(), agent=agent, tool=tool, app=apps,
               target=target, decision=decision, status=result["status"], ms=elapsed_ms)
    with mutex(base, "plugin-audit"):
        path = base / "plugin" / "audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    trace.record(base, agent, tool, apps, query or {}, result["status"])
