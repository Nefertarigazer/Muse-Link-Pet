"""The read-only JSON bridge methods. SQL belongs exclusively to mlindex.query."""
from i18n import active, t
import json
from pathlib import Path
import re

from mlindex import query
from mlindex.__main__ import _load_config, _resolve_paths, _section

_CONTRACT = json.loads((Path(__file__).parent / "ui/fixtures/contract.json").read_text(encoding="utf-8"))
_THREAD_RX = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
_LEGACY_ASK_HEAD_RX = re.compile(r"^(?:代理|Agent)\s+(.+?)\s+(?:请求执行：|請求執行：|requests:\s*)(.+)$")
_KV_LINE_RX = re.compile(r"^([A-Za-z0-9_]+)=(.*)$")
_HEX64_RX = re.compile(r"^[0-9a-fA-F]{64}$")


def _human_size(num):
    if num >= 1024 * 1024:
        return f"{num / (1024 * 1024):.1f} MB"
    if num >= 1024:
        return f"{num / 1024:.1f} KB"
    return f"{num} B"


def _format_history_ask(text):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if len(lines) < 3:
        return text
    m = _LEGACY_ASK_HEAD_RX.match(lines[0])
    if not m:
        return text
    raw_agent, raw_action = m.group(1).strip(), m.group(2).strip()
    raw_agent = re.sub(r"（未签名，身份未验证）|（未簽名，身份未驗證）", "", raw_agent)
    unsigned = False
    if raw_agent != m.group(1).strip():
        unsigned = True
    elif re.search(r"\(unsigned,\s*identity unverified\)", raw_agent, re.I):
        raw_agent = re.sub(r"\s*\(unsigned,\s*identity unverified\)", "", raw_agent, flags=re.I)
        unsigned = True
    agent_label = t("mailbox.auth_unsigned", agent=raw_agent.strip()) if unsigned else raw_agent.strip()
    action_label = t("mailbox.auth_publish_release") if raw_action in ("publish-release", "publish-voicebridge") else raw_action

    facts = {}
    tail = []
    for line in lines[1:]:
        kv = _KV_LINE_RX.match(line)
        if kv:
            facts[kv.group(1)] = kv.group(2).strip()
        else:
            tail.append(line)
    if not facts:
        return text

    out = [t("mailbox.auth_request", agent=agent_label, action=action_label)]
    consumed = set()
    version = facts.get("version", "")
    if version:
        out.append(t("mailbox.auth_version", value=version))
        consumed.add("version")
    pkg = facts.get("package_path", "")
    setup_present = facts.get("setup_present", "").lower() == "true"
    if pkg:
        parts = [p for p in re.split(r"[\\/]+", pkg) if p]
        name = parts[-1] if parts else pkg
        folder = ("\\" if "\\" in pkg else "/").join(parts[:-1])
        extras = []
        raw_size = facts.get("package_size", "")
        if raw_size.isdigit() and int(raw_size) > 0:
            extras.append(_human_size(int(raw_size)))
        if setup_present:
            extras.append(t("mailbox.auth_with_setup"))
        zh = getattr(active(), "lang", "") == "zh"
        extra_str = f"（{'，'.join(extras)}）" if extras and zh else (f" ({', '.join(extras)})" if extras else "")
        out.append(t("mailbox.auth_package", name=name, extra=extra_str))
        if folder:
            out.append(t("mailbox.auth_folder", value=folder))
        consumed.update(("package_path", "package_size", "setup_present"))
    elif setup_present:
        out.append(t("mailbox.auth_with_setup"))
        consumed.add("setup_present")
    else:
        consumed.add("setup_present")
    sha = facts.get("package_sha256", "")
    if sha:
        short = f"{sha[:8]}…{sha[-6:]}" if _HEX64_RX.match(sha) else sha
        out.append(t("mailbox.auth_hash", value=short))
        consumed.add("package_sha256")
    if facts.get("test", "").lower() == "true":
        out.append(t("mailbox.auth_test"))
    consumed.add("test")
    for k in sorted(facts):
        if k in consumed:
            continue
        v = facts[k]
        if (k.endswith("_size") or k.endswith("_bytes")) and v.isdigit() and int(v) > 0:
            v = _human_size(int(v))
        elif _HEX64_RX.match(v):
            v = f"{v[:8]}…{v[-6:]}"
        out.append(f"{k}: {v}")
    if any(t_line in ("允许吗？", "Allow?", "允許嗎？") for t_line in tail):
        out.append(t("mailbox.auth_allow_question"))
    elif tail:
        out.extend(tail)
    return "\n".join(out)


class Api:
    def i18n(self):
        from i18n import bundle
        return self._wrap(bundle)

    def __init__(self, syncer, config_path=None, root=None, index_path=None, actions=None, history_path=None, on_minimize=None, avatar_path=None, on_set_avatar=None):
        self._syncer = syncer
        self._config_path = config_path
        self._root, self._index = _resolve_paths(root, index_path, config_path)
        self._actions = actions
        self._history_path = Path(history_path) if history_path else (Path(__file__).resolve().parent / "pet-history.jsonl")
        self._avatar_path = Path(avatar_path) if avatar_path else (Path(__file__).resolve().parent / "user-avatar.png")
        self._on_minimize = on_minimize
        self._on_set_avatar = on_set_avatar
        self._window = None

    def _attach(self, window):
        self._window = window

    def _wrap(self, fn):
        try:
            return {"ok": True, "data": fn()}
        except Exception as exc:
            # No SQL text, paths, search input or letter content crosses this boundary.
            if isinstance(exc, (ValueError, TypeError)):
                code = "invalid_input"
                msg = t('mailbox.invalid_input')
            elif hasattr(exc, "code"):
                code = str(exc.code)
                msg = str(exc) or t('mailbox.compose_failed')
            else:
                code = "index_unavailable"
                msg = t('mailbox.index_unavailable')
            return {"ok": False, "error": msg, "code": code}

    def config(self):
        def read():
            cfg = _section(_load_config(self._config_path), "mailbox")
            aliases = cfg.get("agent_aliases", {"codex-legion": "codex"})
            names = cfg.get("display_names", {"codex": "Codex", "kiro": "Kiro", "workbuddy": "WorkBuddy", "muse": "Muse"})
            poll = cfg.get("poll_ms", 5000)
            if not isinstance(aliases, dict) or not isinstance(names, dict):
                raise ValueError()
            if any(not isinstance(k, str) or not isinstance(v, str) for d in (aliases, names) for k, v in d.items()):
                raise ValueError()
            return {"agent_aliases": aliases, "display_names": names, "poll_ms": max(100, int(poll))}
        return self._wrap(read)

    def avatar(self):
        def read():
            try:
                raw = self._avatar_path.read_bytes()
            except OSError:
                return None
            if not raw or len(raw) > 2 * 1024 * 1024:
                return None
            import base64
            if raw[:3] == b"\xff\xd8\xff":
                mime = "image/jpeg"
            elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
                mime = "image/webp"
            else:
                mime = "image/png"
            return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
        return self._wrap(read)

    def threads(self):
        def read():
            rows = query.list_threads(self._index)
            for row in rows:
                row["unread"] = self._syncer.thread_attention(row["id"])
            return rows
        return self._wrap(read)

    def mark_seen(self, thread_ids):
        def write():
            if not isinstance(thread_ids, list) or len(thread_ids) > 200 or any(not isinstance(x, str) for x in thread_ids):
                raise ValueError()
            return {"unread": self._syncer.mark_seen(thread_ids)}
        return self._wrap(write)

    def delete_thread(self, thread_ids):
        def write():
            if not isinstance(thread_ids, list) or not thread_ids or len(thread_ids) > 50 or any(not isinstance(x, str) or not x for x in thread_ids):
                raise ValueError()
            return self._syncer.delete_thread(thread_ids)
        return self._wrap(write)

    def send_message(self, thread_id, text):
        def write():
            if not isinstance(thread_id, str) or not _THREAD_RX.match(thread_id):
                raise ValueError()
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000 or "\x00" in text:
                raise ValueError()
            actions = self._actions
            if actions is None:
                from pet.actions import Actions
                actions = Actions(Path(__file__).resolve().parents[1])
            res = actions.say(text.strip(), None, thread=thread_id)
            try:
                self._syncer.refresh()
            except Exception:
                pass
            return res
        return self._wrap(write)

    def _messages(self, messages):
        meta = query.request_meta(self._index, (m["req_id"] for m in messages))
        result = []
        for original in messages:
            m = dict(original)
            request = meta.get(m["req_id"], {})
            m["agent"] = request.get("agent", "") if m["role"] == "agent" else ""
            m["final_read"] = request.get("final_read", False)
            # Preserve exactly the contract, never expose index provenance.
            result.append({key: m[key] for key in _CONTRACT["message"]})
        return result

    def _history_messages(self):
        try:
            lines = self._history_path.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            return []
        out = []
        for idx, raw in enumerate(lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except ValueError:
                continue
            ts = float(item.get("ts") or 0)
            req_id = str(item.get("req_id") or f"popup_{idx}")
            ask = str(item.get("ask") or "").strip()
            answer = str(item.get("answer") or "").strip()
            kind = str(item.get("kind") or "")
            if ask:
                out.append({
                    "message_id": f"{req_id}:ask:{idx}",
                    "req_id": req_id,
                    "seq": 0,
                    "thread_id": "pet/chat",
                    "role": "muse",
                    "agent": "",
                    "type": "question",
                    "status": "needs-user",
                    "final": True,
                    "text": _format_history_ask(ask),
                    "payload": {},
                    "created_at": "",
                    "replied_at": "",
                    "sort_ts": ts - 0.001,
                    "trust": "read",
                    "state": "replied",
                    "final_read": True,
                    "dir": "archive",
                    "parse_error": None,
                    "release_card": None,
                    "attachments": [],
                })
            reply_text = answer if answer else (t("mailbox.popup_confirmed") if kind == "pet_done" or kind == "info" else "")
            if reply_text:
                out.append({
                    "message_id": f"{req_id}:ans:{idx}",
                    "req_id": req_id,
                    "seq": 1,
                    "thread_id": "pet/chat",
                    "role": "user",
                    "agent": "pet",
                    "type": "message",
                    "status": "",
                    "final": False,
                    "text": reply_text,
                    "payload": {},
                    "created_at": "",
                    "replied_at": "",
                    "sort_ts": ts,
                    "trust": None,
                    "state": "replied",
                    "final_read": True,
                    "dir": "sent",
                    "parse_error": None,
                    "release_card": None,
                    "attachments": [],
                })
        return out

    def thread(self, id):
        def read():
            if not isinstance(id, str):
                raise ValueError()
            result = query.thread(self._index, id)
            if result is not None:
                result["messages"] = self._messages(result["messages"])
            if id == "pet/chat":
                extra = self._history_messages()
                if extra:
                    if result is None:
                        result = {
                            "id": "pet/chat",
                            "title": "Muse",
                            "participants": ["pet", "muse"],
                            "status": "done",
                            "last_activity": "",
                            "last_ts": max(m["sort_ts"] for m in extra),
                            "unread": 0,
                            "snippet": extra[-1]["text"][:120],
                            "message_count": len(extra),
                            "messages": extra,
                        }
                    else:
                        result["messages"] = sorted(result["messages"] + extra, key=lambda m: (m["sort_ts"], m["message_id"]))
                        result["message_count"] = len(result["messages"])
            return result
        return self._wrap(read)

    def search(self, q, limit=50):
        def read():
            if not isinstance(q, str):
                raise ValueError()
            rows = query.search(self._index, q[:200], max(1, min(200, int(limit))))
            messages = self._messages(rows)
            for m, row in zip(messages, rows):
                m["thread_title"] = row["thread_title"]
            return messages
        return self._wrap(read)

    def status(self):
        return self._wrap(self._syncer.status)

    def refresh(self):
        return self._wrap(self._syncer.refresh)

    # The card window is frameless: its own buttons need these two. They
    # touch only the window, never the mailbox or the index.
    def _window_call(self, name):
        def run():
            if self._window is None:
                raise RuntimeError("no window")
            getattr(self._window, name)()
            return None
        return self._wrap(run)

    def set_avatar(self):
        def write():
            if self._on_set_avatar is None:
                return {"updated": False}
            return {"updated": bool(self._on_set_avatar())}
        return self._wrap(write)

    def minimize(self):
        if self._on_minimize is not None:
            return self._wrap(lambda: self._on_minimize() and None)
        return self._window_call("minimize")

    def hide(self):
        return self._window_call("hide")
