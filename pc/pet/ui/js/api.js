import { I18N, t as tr } from "../../../i18n/i18n.js";
// api.js - the ONLY place the pet UI talks to the backend.
// Contract: docs/architecture.en.md section 8 and
// fixtures/contract.json. Two transports:
//   * pywebview - window.pywebview.api.<method>() (the real pet window)
//   * demo      - ?demo in the URL: a scripted local backend for previews
// Every method returns {"ok": true, "data": ...} or {"ok": false, "error", "code"}.

export const METHODS = ["i18n", "languages", "set_language", "config", "state", "channels", "done", "say",
  "open_mailbox", "open_session", "hide", "exit", "set_avatar", "region", "activate", "drag", "screen"];

function waitForPywebview(timeoutMs) {
  if (window.pywebview && window.pywebview.api) return Promise.resolve(window.pywebview.api);
  return new Promise((resolve) => {
    const done = () => resolve(window.pywebview && window.pywebview.api ? window.pywebview.api : null);
    window.addEventListener("pywebviewready", done, { once: true });
    setTimeout(done, timeoutMs);
  });
}

function unwrap(value) {
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

// ---- demo backend -----------------------------------------------------------
const iso = () => new Date().toISOString();

export class DemoTransport {
  constructor(params = new URLSearchParams()) {
    this.kind = "demo";
    this.snap = { state: "idle", brief: null, brief_id: null, brief_at: null, activity: null, ask: null, last_answer: null, unread: 0, since: iso() };
    const s = params.get("state");
    if (s === "working") this.work(tr("pet.demo_work_text"));
    if (s === "waiting_user" || s === "waiting") this.ask(params.get("mode") || "input");
  }
  work(brief) {
    this.snap = { ...this.snap, state: "working", activity: "working", brief, brief_id: String(Date.now()), brief_at: iso(), since: iso() };
  }
  ask(mode = "input") {
    const text = {
      info: tr("pet.demo_info"),
      choice: tr("pet.demo_question"),
      input: tr("pet.demo_confirm"),
    }[mode];
    this.snap = { ...this.snap, state: "waiting_user", ask: { req_id: "req_demo_" + Date.now(), mode, summary: text }, since: iso() };
  }
  answer(by) {
    const id = this.snap.ask && this.snap.ask.req_id;
    if (!id) return false;
    this.snap = { ...this.snap, state: "working", ask: null, last_answer: { req_id: id, by, at: iso() }, since: iso() };
    return true;
  }
  idle() { this.snap = { ...this.snap, state: "idle", brief: null, brief_id: null, brief_at: null, activity: null, ask: null, since: iso() }; }
  async call(method, ...args) {
    await new Promise((r) => setTimeout(r, 40));
    switch (method) {
      case "i18n": return globalThis.museLinkDemoBundle || {tag: "en", dir: "ltr", font: "Segoe UI", messages: (await (await fetch("/i18n/en.json")).json())};
      case "languages": return Promise.all(["en","zh-Hans","zh-Hant"].map(async tag =>
        ({tag, name:(await(await fetch(`/i18n/${tag}.json`)).json())._meta.name})));
      case "set_language": return {tag:args[0],dir:"ltr",messages:await(await fetch(`/i18n/${args[0]}.json`)).json()};
      case "config": return { display_name: "Muse Pet", pack: "jolly", bubble_ms: 8000, poll_ms: 1000, sound: true };
      case "state": return { ...this.snap };
      case "channels": return {
        checked_at: iso(),
        items: [
          { id: "tunnel", label: tr("pet.tunnel"), level: "ok", detail: tr("pet.demo_tunnel") },
          { id: "host", label: tr("pet.executor"), level: "ok", detail: tr("pet.demo_host") },
          { id: "bus", label: tr("pet.bus"), level: this.snap.ask ? "warn" : "ok", detail: this.snap.ask ? tr("pet.demo_waiting") : tr("pet.idle") },
          { id: "bridge", label: tr("pet.mailbox"), level: "ok", detail: tr("pet.demo_reply") },
        ],
      };
      case "done": return { delivered: this.answer("pet") };
      case "say": {
        const [text, replyTo] = args;
        if (replyTo && this.snap.ask && this.snap.ask.req_id === replyTo) { this.answer("pet"); return { via: "wait", req_id: replyTo }; }
        return { via: "bridge", req_id: "pet_demo_" + Date.now() + "_" + String(text).length };
      }
      case "set_avatar": return { updated: false };
      case "open_mailbox": case "open_session": case "hide": case "region": case "activate": case "drag": case "screen": return null;
      default: throw new Error(`unknown method ${method}`);
    }
  }
}

export async function connect() {
  const params = new URLSearchParams(location.search);
  const demo = params.has("demo");
  const api = demo ? null : await waitForPywebview(1500);
  const transport = api ? new PywebviewTransport(api) : new DemoTransport(params);
  const client = { kind: transport.kind, demo: transport.kind === "demo" ? transport : null };
  for (const m of METHODS) client[m] = (...args) => transport.call(m, ...args);
  return client;
}

function petName() { return globalThis.musePetName || "Muse Pet"; }
