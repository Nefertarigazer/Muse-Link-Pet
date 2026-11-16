"""share - consent, the share card, outbox and pull request.

State <tool dir>\\experience\\share.json:
  {"never": false, "last_asked": "<iso>", "last_expired": "<iso>",
   "entries": {"<id>": "shared" | "outboxed"}}

Requirements
  1. candidates(store): local, status valid, no state for the id,
     redact() leaves 0 hits, card_fits() passes. never == true -> [] always.
  2. check_once(): accumulates BATCH_SIZE (5) eligible local entries before
     raising one card; none when popup-pending.txt exists (Muse or another
     agent is waiting) or while throttled after an expired card. Card via
     plugin.ask.ask_user(agent="explib", mode="choice", options=[SHARE,
     NEVER], expires_in=600, silent=True, display="经验库").
     The card body shows every redacted entry in the batch (every content
     field, step args verbatim) plus CARD_NOTE; the upload payload is
     schema.render() of each same snapshot.
  3. Answers: SHARE -> write outbox/<id>.json with the exact bytes rendered
     from each redacted entry in the batch, mark "outboxed", then submit()
     each; NEVER -> never = true (never asks again); expired / preempted /
     busy / anything else -> no outbox change (expired records last_expired).
  4. submit(id): only if explib.public.repo is configured and `gh auth
     status` succeeds: fork (if needed), create a branch, PUT
     entries/<app>/<id>.json via `gh api` (no local clone), open a PR;
     mark "shared" with the PR URL. Otherwise leave it in outbox and return
     {"status": "outboxed", "how": "<manual steps>"}. Never submits bytes
     that differ from the outbox file.
  5. No path writes outbox/ or calls submit() without a SHARE answer
     obtained in this same check_once() call.
"""
from i18n import t, labels, answer_key

import base64
import hashlib
from datetime import datetime, timezone
import json
import re
import secrets
import subprocess
from urllib.parse import quote

from plugin import ask
from plugin._shared import atomic, mutex, read_json, tool_dir
from . import redact, schema
from .store import Store

BATCH_SIZE = 5
SHARE = "explib.share.opt.share"
NEVER = "explib.share.opt.never"
CARD_NOTE = "explib.share.note"
# The popup card shortens text that does not fit the screen (popup.ps1 cuts
# the last visible line with an ellipsis). A share card must show every
# content field, so an entry is only offered when its card text fits a
# conservative budget: about 56 display columns (CJK = 2) per wrapped line.
CARD_COLUMNS = 56
CARD_MAX_LINES = 16


def card_text(entry):
    """Plain-language view of every content field of the upload payload.

    Kiro review: the canonical JSON is the payload, but a 1200-character JSON
    line is unreadable on a card and was cut off on smaller screens, so the
    user could approve text they never saw. The card renders the SAME
    snapshot that goes to outbox, field by field, args included verbatim.
    """
    head = t('explib.share.app') + entry["app"] + (" " + entry["app_version"] if entry.get("app_version") else "")
    lines = [head]
    if entry.get("ui"):
        lines.append(t('explib.share.ui') + entry["ui"])
    lines.append(t('explib.share.goal') + entry["goal"])
    for i, step in enumerate(entry["method"], 1):
        if isinstance(step, str):
            text = step
        else:
            text = step["step"]
            extra = [step["tool"]] if step.get("tool") else []
            if step.get("args"):
                extra.append(json.dumps(step["args"], ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            if extra:
                text += t('explib.share.extra', extra=" ".join(extra))
        lines.append("%d. %s" % (i, text))
    lines.append(t('explib.share.verify') + entry["verify"])
    if entry.get("tags"):
        lines.append(t('explib.share.tags') + t('explib.share.tag_sep').join(entry["tags"]))
    return "\n".join(lines)


def batch_card_text(entries):
    blocks = []
    total = len(entries)
    for idx, entry in enumerate(entries, 1):
        header = "**" + t("explib.share.batch_item", index=idx, total=total, app=entry["app"]) + "**"
        blocks.append(header + "\n" + card_text(entry))
    return "\n\n".join(blocks)


def card_fits(text):
    import math
    def width(line):
        return sum(2 if ord(c) >= 0x2E80 else 1 for c in line)
    return sum(max(1, math.ceil(width(line) / CARD_COLUMNS)) for line in text.split("\n")) <= CARD_MAX_LINES


def candidates(store, base):
    state = _state(base)
    if state.get("never"):
        return []
    result = []
    for entry in store.entries.values():
        entry = schema.age_out(entry, datetime.now().date().isoformat())
        if (entry["source"] != "local" or entry["status"] != "valid" or
                entry["id"] in state.get("entries", {})):
            continue
        clean, hits = redact.redact(entry, redact.literals(base))
        try:
            schema.validate(clean)
            if not card_fits(card_text(clean)):
                hits.append("card-too-long")
        except ValueError:
            hits.append("schema/card-budget")
        if hits:
            # No private content or matched values in the diagnostic.
            atomic(tool_dir(base) / "experience" / "share-blocked.json",
                   {"id": entry["id"], "reasons": sorted(set(hits))})
        else:
            result.append(clean)
    return sorted(result, key=lambda e: (e["last_verified"], e["id"]), reverse=True)


def _state(base):
    state = read_json(tool_dir(base) / "experience" / "share.json", {})
    if not isinstance(state, dict) or not isinstance(state.get("entries", {}), dict):
        raise ValueError("share: invalid state")
    return state


def check_once(base, store=None, ask_fn=None, now=None):
    base = tool_dir(base)
    now = now or datetime.now(timezone.utc)
    if isinstance(now, str):
        now = datetime.fromisoformat(now.replace("Z", "+00:00"))
    if now.tzinfo is None:
        raise ValueError("now: timezone required")
    try:
        # One checker owns the full card lifecycle. No database/store lock is
        # held while the user is reading the card.
        with mutex(base, "explib-share", timeout_ms=0):
            state = _state(base)
            if state.get("never"):
                return {"status": "ok", "reason": "never"}
            if (base / "popup-pending.txt").exists():
                return {"status": "busy", "reason": "popup-pending"}
            options = candidates(store or Store(base), base)
            if not options:
                return {"status": "ok", "reason": "no-candidates"}
            if len(options) < BATCH_SIZE:
                return {"status": "ok", "reason": "not-enough-candidates", "count": len(options), "needed": BATCH_SIZE}
            if state.get("last_expired"):
                last = datetime.fromisoformat(state["last_expired"].replace("Z", "+00:00"))
                if (now - last).total_seconds() < 86400:
                    return {"status": "ok", "reason": "throttled"}
            batch = options[:BATCH_SIZE]
            rendered = labels("explib.share.opt.")
            bodies = {entry["id"]: schema.render(entry) for entry in batch}   # exact upload payloads
            response = (ask_fn or ask.ask_user)(agent="explib", text=batch_card_text(batch) + t(CARD_NOTE),
                                                mode="choice", options=[rendered[k] for k in (SHARE, NEVER)],
                                                expires_in=600, silent=True, display=t('explib.share.sender'), base=base)
            status, answer = response.get("status"), answer_key(response.get("result"), rendered)
            accepted = status == "ok" and response.get("kind") in ("choice", "typed", "pet_typed")
            if not accepted or answer not in (SHARE, NEVER):
                if status == "expired":
                    state["last_expired"] = now.isoformat()
                    atomic(base / "experience" / "share.json", state)
                return {"status": status if status in ("expired", "preempted", "busy") else "refused"}
            # Reload under our checker lock so an externally set never flag
            # cannot be undone by an older snapshot of the state.
            state = _state(base)
            if state.get("never"):
                return {"status": "ok", "reason": "never"}
            state["last_asked"] = now.isoformat()
            state.pop("last_expired", None)
            states = state.setdefault("entries", {})
            if answer == NEVER:
                state["never"] = True
                atomic(base / "experience" / "share.json", state)
                return {"status": "ok", "reason": "never"}
            hashes = state.setdefault("sha256", {})
            for entry in batch:
                # The immutable snapshots shown on this very card are the only
                # values allowed to cross into outbox. No rerender of live data.
                path = base / "experience" / "outbox" / (entry["id"] + ".json")
                if path.is_symlink() or path.parent.is_symlink() or path.resolve() != path.absolute():
                    raise ValueError("outbox: linked path")
                body = bodies[entry["id"]]
                atomic(path, body)
                states[entry["id"]] = "outboxed"
                hashes[entry["id"]] = hashlib.sha256(body.encode("utf-8")).hexdigest()
            atomic(base / "experience" / "share.json", state)
            results = [submit(base, entry["id"]) for entry in batch]
            overall = "shared" if all(r.get("status") == "shared" for r in results) else "outboxed"
            out = {"status": overall, "ids": [e["id"] for e in batch], "results": results}
            if overall == "outboxed":
                out["how"] = next((r["how"] for r in results if "how" in r), "")
            return out
    except TimeoutError:
        return {"status": "busy", "reason": "share-check-running"}


def submit(base, entry_id, run=None):
    with mutex(tool_dir(base), "explib-share"):
        return _submit(base, entry_id, run)


def _submit(base, entry_id, run=None):
    base = tool_dir(base)
    if not isinstance(entry_id, str) or not re.fullmatch(r"x-[0-9a-f]{12}", entry_id):
        raise ValueError("id: invalid")
    state = _state(base)
    if state.get("entries", {}).get(entry_id) != "outboxed":
        return {"status": "refused", "reason": "no-share-consent"}
    path = base / "experience" / "outbox" / (entry_id + ".json")
    if path.resolve() != path.absolute():
        raise ValueError("outbox: linked path")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != state.get("sha256", {}).get(entry_id):
        return {"status": "refused", "reason": "outbox-differs-from-consent"}
    entry = json.loads(data.decode("utf-8"))
    schema.validate(entry)
    if entry["id"] != entry_id or data != schema.render(entry).encode("utf-8"):
        raise ValueError("outbox: invalid canonical payload")
    manual = {"status": "outboxed", "id": entry_id,
              "how": "Copy experience/outbox/" + entry_id + ".json unchanged to entries/" +
                     entry["app"] + "/" + entry_id + ".json in the public repository, then open a PR."}
    repo = redact.config(base).get("public", {}).get("repo", "")
    if not repo:
        return manual
    if not isinstance(repo, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("public.repo: expected owner/repo")
    runner = run or subprocess.run
    def command(args, payload=None):
        proc = runner(["gh", *args], input=json.dumps(payload) if payload is not None else None,
                      capture_output=True, encoding="utf-8", timeout=30)
        if proc.returncode:
            raise OSError("gh failed")
        return json.loads(proc.stdout) if args[0] == "api" and proc.stdout.strip() else {}
    def api(endpoint, payload=None, method=None):
        args = ["api", endpoint]
        if method:
            args += ["--method", method]
        if payload is not None:
            args += ["--input", "-"]
        return command(args, payload)
    try:
        command(["auth", "status"])
        user = api("user")["login"]
        upstream = api("repos/" + repo)
        default = upstream["default_branch"]
        fork = repo if user.lower() == repo.split("/")[0].lower() else api("repos/" + repo + "/forks", {}, "POST")["full_name"]
        sha = api("repos/" + fork + "/git/ref/heads/" + quote(default, safe=""))["object"]["sha"]
        branch = "explib-" + entry_id + "-" + secrets.token_hex(3)
        api("repos/" + fork + "/git/refs", {"ref": "refs/heads/" + branch, "sha": sha}, "POST")
        identity = {"name": "MuseLink Community", "email": "community@users.noreply.github.com"}
        api("repos/" + fork + "/contents/entries/" + quote(entry["app"], safe="") + "/" + entry_id + ".json",
            {"message": "Add community experience " + entry_id, "branch": branch,
             "content": base64.b64encode(data).decode("ascii"), "author": identity, "committer": identity}, "PUT")
        pr = api("repos/" + repo + "/pulls", {"title": "Add community experience " + entry_id,
                 "head": fork.split("/")[0] + ":" + branch, "base": default,
                 "body": "User-approved, locally redacted experience entry. "
                         "Contributed under CC0 1.0 (see library/LICENSE)."}, "POST")["html_url"]
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return manual
    state = _state(base)
    state.setdefault("entries", {})[entry_id] = "shared"
    state.setdefault("prs", {})[entry_id] = pr
    atomic(base / "experience" / "share.json", state)
    return {"status": "shared", "id": entry_id, "url": pr}
