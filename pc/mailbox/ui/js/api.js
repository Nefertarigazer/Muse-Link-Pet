// api.js - the ONLY place the UI talks to the backend.
// Contract: docs/architecture.en.md section 7. Every call
// is async and returns plain JSON. Two transports:
//   * pywebview  - window.pywebview.api.<method>(...) (production window)
//   * demo       - ?demo in the URL, data from fixtures/demo.json; also used
//                  when no backend appears (plain browser preview)
// The UI never writes anything: there is no write method in this contract.

const METHODS = ["i18n", "config", "avatar", "set_avatar", "threads", "thread", "search", "status", "refresh", "mark_seen", "delete_thread", "send_message", "minimize", "hide"];

function waitForPywebview(timeoutMs) {
  if (window.pywebview && window.pywebview.api) return Promise.resolve(window.pywebview.api);
  return new Promise((resolve) => {
    const done = () => resolve(window.pywebview && window.pywebview.api ? window.pywebview.api : null);
    window.addEventListener("pywebviewready", done, { once: true });
    setTimeout(done, timeoutMs);
  });
}

function unwrap(value) {
  // backend convention: {"ok": false, "error": "..."} for failures
  if (value && typeof value === "object" && value.ok === false) {
    const err = new Error(value.error || "backend error");
    err.code = value.code || "backend";
    throw err;
  }
  return value && typeof value === "object" && "data" in value ? value.data : value;
}

class PywebviewTransport {
  constructor(api) { this.api = api; this.kind = "pywebview"; }
  async call(method, ...args) {
    if (typeof this.api[method] !== "function") throw new Error(`backend has no ${method}()`);
    return unwrap(await this.api[method](...args));
  }
}

class DemoTransport {
  constructor(data) {
    this.kind = "demo";
    this.data = data;
    this.builtAt = data.status.built_at;
    this.seen = new Set();
    this.deleted = new Set();
  }
  async call(method, ...args) {
    const d = this.data;
    await new Promise((r) => setTimeout(r, 60));
    switch (method) {
      case "i18n": return globalThis.museLinkDemoBundle || {tag: "en", dir: "ltr", font: "Segoe UI", messages: (await (await fetch("/i18n/en.json")).json())};
      case "config": return d.config;
      case "avatar": return null;
      case "set_avatar": return { updated: false };
      case "threads": return d.threads.filter((t) => !this.deleted.has(t.id)).map((t) => ({ ...t, unread: t.status === "waiting_user" && !this.seen.has(t.id) ? (t.unread || 1) : 0 }));
      case "thread": return this.deleted.has(args[0]) ? null : d.details[args[0]] || null;
      case "status": return { ...d.status, built_at: this.builtAt };
      case "refresh": return { ...d.status, built_at: this.builtAt };
      case "mark_seen": {
        for (const id of args[0] || []) this.seen.add(id);
        const left = d.threads.filter((t) => !this.deleted.has(t.id) && t.status === "waiting_user" && !this.seen.has(t.id)).length;
        return { unread: left };
      }
      case "delete_thread": {
        for (const id of args[0] || []) this.deleted.add(id);
        const left = d.threads.filter((t) => !this.deleted.has(t.id) && t.status === "waiting_user" && !this.seen.has(t.id)).length;
        return { deleted: (args[0] || []).length, unread: left };
      }
      case "send_message": {
        const [tid, text] = args;
        const ts = Date.now() / 1000;
        const reqId = `pet_${Date.now()}`;
        const msg = {
          message_id: reqId, req_id: reqId, seq: null, thread_id: tid,
          role: "user", agent: "pet", type: "message", status: "", final: false,
          text, payload: {}, created_at: new Date().toISOString(), replied_at: "",
          sort_ts: ts, trust: null, state: "delivered", final_read: false, dir: "sent",
          parse_error: null, release_card: null, attachments: [],
        };
        if (!d.details[tid]) {
          d.details[tid] = { id: tid, title: "Muse", participants: ["pet", "muse"], status: "active", last_activity: msg.created_at, last_ts: ts, unread: 0, snippet: text, message_count: 0, messages: [] };
          d.threads.unshift({ ...d.details[tid] });
        }
        d.details[tid].messages.push(msg);
        d.details[tid].message_count = d.details[tid].messages.length;
        const t = d.threads.find((x) => x.id === tid);
        if (t) { t.snippet = text; t.last_ts = ts; t.message_count = d.details[tid].message_count; }
        this.builtAt = new Date().toISOString();
        return { via: "bridge", req_id: reqId };
      }
      case "minimize": case "hide": return null; // no window in a browser preview
      case "search": {
        const q = String(args[0] || "").toLowerCase();
        if (!q) return [];
        const out = [];
        for (const [id, t] of Object.entries(d.details)) {
          for (const m of t.messages) {
            if ((m.text || "").toLowerCase().includes(q)) out.push({ ...m, thread_id: id, thread_title: t.title });
          }
        }
        return out.sort((a, b) => b.sort_ts - a.sort_ts).slice(0, Number(args[1]) || 50);
      }
      default: throw new Error(`unknown method ${method}`);
    }
  }
}

export async function connect() {
  const demo = new URLSearchParams(location.search).has("demo");
  const api = demo ? null : await waitForPywebview(1500);
  let transport;
  if (api) {
    transport = new PywebviewTransport(api);
  } else {
    const res = await fetch(new URL("../fixtures/demo.json", import.meta.url));
    transport = new DemoTransport(await res.json());
  }
  const client = { kind: transport.kind };
  for (const m of METHODS) client[m] = (...args) => transport.call(m, ...args);
  return client;
}
