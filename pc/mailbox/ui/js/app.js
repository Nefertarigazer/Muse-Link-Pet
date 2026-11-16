import { I18N, t as tr } from "../../../i18n/i18n.js";
// app.js - mailbox UI: renders model.js output into the DOM.
// Safety rule: message text, names, paths and ids come from local agents and
// from Muse and are UNTRUSTED. They are only ever set through textContent /
// attributes via h(); innerHTML is used for the static icon constants only.

import { connect } from "./api.js";
import { ICONS } from "./icons.js";
import { renderMarkdown } from "./markdown.js";
import * as M from "./model.js";

const $ = (sel, root = document) => root.querySelector(sel);

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.setAttribute("style", v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "icon") el.innerHTML = ICONS[v] || "";
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const state = {
  client: null, aliases: M.DEFAULT_ALIASES, names: M.DEFAULT_NAMES,
  merged: [], selected: null, detail: null, detailSig: "", focus: null, visible: true, sending: false,
  userAvatar: null,
  drafts: new Map(),
  threadHints: new Map(),
  rawThreads: [],
  query: "", results: null, status: null, error: null, loading: true, syncing: false,
};

function detailSignature(d) {
  if (!d) return "";
  const msgs = (d.messages || []).map((m) => [
    m.message_id, m.state, m.trust, m.status, m.final, m.text, m.sort_ts, m.release_card?.status || "",
  ]);
  return JSON.stringify([d.id, d.title, d.status, !!d.orphan, msgs]);
}

function hintMap() {
  const byCanon = {};
  for (const entry of state.threadHints.values()) {
    if (!entry || !entry.canon || !entry.hint) continue;
    const h = entry.hint;
    const cur = byCanon[entry.canon];
    if (!cur) {
      byCanon[entry.canon] = {
        project: h.project,
        artifactProject: h.artifactProject,
        urlProject: h.urlProject,
        versions: [...h.versions],
        reqIds: [...h.reqIds],
        replyTos: [...h.replyTos],
        texts: [...h.texts],
      };
    } else {
      cur.artifactProject = cur.artifactProject || h.artifactProject;
      cur.urlProject = cur.urlProject || h.urlProject;
      cur.project = cur.artifactProject || cur.urlProject;
      cur.versions = [...new Set([...cur.versions, ...h.versions])];
      cur.reqIds = [...new Set([...cur.reqIds, ...h.reqIds])];
      cur.replyTos = [...new Set([...cur.replyTos, ...h.replyTos])];
      cur.texts = [...cur.texts, ...h.texts];
    }
  }
  return byCanon;
}

// ---- small views ---------------------------------------------------------
const AGENT_ASSET = Object.freeze({
  muse: "assets/muse-pet.png",
  pet: "assets/muse-pet.png",
  codex: "assets/codex.png",
  gemini: "assets/gemini.png",
  workbuddy: "assets/workbuddy.svg",
  opencode: "assets/opencode.png",
});

function avatar(agent, size = "", orphan = false) {
  const kind = M.agentKind(agent);
  const iconAgents = new Set(["muse", "codex", "claude", "gemini", "kiro", "workbuddy", "opencode", "user", "pet"]);
  const cls = orphan ? "av-orphan" : iconAgents.has(kind) ? `av-${kind}` : "av-other";
  if (!orphan && Object.prototype.hasOwnProperty.call(AGENT_ASSET, kind)) {
    return h("span", { class: `avatar ${size} ${cls}`, "aria-hidden": "true" },
      h("img", { src: AGENT_ASSET[kind], alt: "" }));
  }
  if (!orphan && kind === "user" && state.userAvatar) {
    return h("span", { class: `avatar ${size} ${cls}`, "aria-hidden": "true" },
      h("img", { src: state.userAvatar, alt: "" }));
  }
  if (!orphan && iconAgents.has(kind)) {
    return h("span", { class: `avatar ${size} ${cls}`, "aria-hidden": "true", icon: kind });
  }
  const letter = orphan ? "?" : (M.displayName(agent, state.names)[0] || "?").toUpperCase();
  return h("span", { class: `avatar ${size} ${cls}`, "aria-hidden": "true" }, letter);
}

function pill(info, extraClass = "") {
  return h("span", { class: `pill tone-${info.tone} ${extraClass}` }, info.label);
}

function threadName(t) {
  if (t.orphan) return tr("mailbox.orphans");
  if (t.id === "pet/chat") return tr("mailbox.pet_chat_name");
  return M.displayName(M.threadAgent(t, state.aliases), state.names);
}

function textWithMarks(text, q) {
  return M.highlightParts(text, q).map((p) => (p.hit ? h("mark", {}, p.text) : p.text));
}

// ---- sidebar -------------------------------------------------------------
function renderSidebar() {
  const list = $("#side-list");
  list.replaceChildren();
  if (state.loading) {
    list.append(h("div", { class: "skeleton" }, h("div"), h("div"), h("div"), h("div")));
    return;
  }
  if (state.results) {
    renderResults(list);
    return;
  }
  const sections = M.sidebarSections(state.merged);
  if (!sections.length) {
    list.append(h("div", { class: "placeholder" }, h("span", { icon: "tray" }),
      h("h3", {}, tr("mailbox.empty")), h("p", {}, tr("mailbox.empty_hint"))));
    return;
  }
  let i = 0;
  for (const sec of sections) {
    if (sec.key !== "pinned" && sec.key !== "all") {
      list.append(h("div", { class: `section-title ${sec.key}` }, sec.title,
        h("span", { class: "count" }, String(sec.items.length))));
    }
    for (const t of sec.items) list.append(threadRow(t, i++));
  }
}

const stripMdInline = (s) => String(s || "").replace(/\*\*([^*\n]+)\*\*|`([^`\n]+)`/g, "$1$2");

function threadRow(t, index) {
  const agent = t.orphan ? "" : M.threadAgent(t, state.aliases);
  const badge = M.threadTopicBadge(t, state.merged, state.aliases, hintMap());
  const sel = state.selected === t.id;
  const rawPreview = t.snippet || M.cleanTitle(t.title, t.id) || (t.id === "pet/chat" ? tr("mailbox.pet_chat_hint") : "");
  const preview = stripMdInline(rawPreview);
  return h("button", {
    class: `thread-row${sel ? " selected" : ""}${t.id === "pet/chat" ? " is-pinned" : ""}`, "data-id": t.id, "data-index": index,
    "aria-current": sel ? "true" : null,
    onclick: () => { closeCtxMenu(); openThread(t.id); },
    oncontextmenu: t.starter ? null : (e) => { e.preventDefault(); e.stopPropagation(); openCtxMenu(t, e.clientX, e.clientY); },
  },
  avatar(agent, "", t.orphan),
  h("div", { class: "row-main" },
    h("div", { class: "row-top" },
      h("div", { class: "name" }, h("span", {}, threadName(t)),
        badge ? h("span", { class: `chip chip-${badge.kind}` }, badge.kind === "pin" ? h("span", { class: "pin-ico", icon: "pin" }) : null, badge.label) : null),
      h("div", { class: "time" }, M.shortTime(t.lastTs))),
    h("div", { class: "row-sub" },
      h("div", { class: "snippet" }, preview),
      h("div", { class: "meta" },
        t.status === "waiting_user" ? h("span", { class: "pill tone-orange" }, tr("mailbox.waiting_short")) : null,
        t.status === "waiting_user" && t.unread ? h("span", { class: "badge", title: tr("mailbox.unread_replies") }, String(t.unread)) : null))));
}

function renderResults(list) {
  const r = state.results;
  list.append(h("div", { class: "section-title" }, tr("mailbox.search_results"), h("span", { class: "count" }, String(r.length))));
  if (!r.length) {
    list.append(h("div", { class: "placeholder" }, h("p", {}, tr("mailbox.search_none", { query: state.query }))));
    return;
  }
  for (const m of r) {
    const canon = M.canonicalThreadId(m.thread_id, state.aliases);
    const t = state.merged.find((x) => x.id === canon) || { id: canon, participants: [], orphan: false };
    const who = m.role === "muse" ? "muse" : M.canonicalAgent(m.agent || M.threadAgent(t, state.aliases), state.aliases);
    list.append(h("button", { class: "result", onclick: () => openThread(canon, m.message_id) },
      h("div", { class: "where" }, avatar(who, "sm"),
        h("span", {}, `${M.displayName(who, state.names)} · ${threadName(t)}`),
        h("span", { style: "margin-left:auto" }, M.shortTime(m.sort_ts))),
      h("div", { class: "text" }, textWithMarks(m.text || "", state.query))));
  }
}

// ---- thread view ---------------------------------------------------------
function renderContent(opts = {}) {
  const { animate = true, scrollMode = "bottom", keepScrollTop = 0 } = opts;
  const content = $("#content");
  const prevBox = $("#mb-compose");
  const composeFocused = prevBox && document.activeElement === prevBox;
  const selStart = composeFocused ? prevBox.selectionStart : 0;
  const selEnd = composeFocused ? prevBox.selectionEnd : 0;
  content.replaceChildren();
  if (state.error && !state.merged.length) {
    content.append(h("div", { class: "placeholder" }, h("span", { icon: "cloudOff" }),
      h("h3", {}, tr("mailbox.index_error")), h("p", {}, state.error),
      h("button", { class: "pill tone-blue", onclick: () => refreshNow() }, tr("mailbox.retry"))));
    return;
  }
  const d = state.detail;
  if (!d) {
    content.append(h("div", { class: "placeholder" }, h("span", { icon: "bubbles" }),
      h("h3", {}, state.loading ? tr("mailbox.loading") : tr("mailbox.select")),
      h("p", {}, tr("mailbox.select_hint"))));
    return;
  }
  content.append(threadHeader(d));
  const flow = h("div", { class: `flow${animate ? " enter" : " no-anim"}`, id: "flow", role: "log", "aria-label": tr("mailbox.letters") });
  if (d.orphan) {
    flow.append(h("div", { class: "notice" }, h("span", { icon: "warn" }), h("div", {},
      h("strong", {}, tr("mailbox.orphan_title")),
      tr("mailbox.orphan_explain")
      + tr("mailbox.orphan_hint"))));
  }
  let gi = 0;
  const days = M.groupByRequest(d.messages || []);
  const lastGroup = days.length && days[days.length - 1].groups.length
    ? days[days.length - 1].groups[days[days.length - 1].groups.length - 1] : null;
  if (!days.length && d.id === "pet/chat") {
    flow.append(h("div", { class: "placeholder" }, h("span", { icon: "bubbles" }),
      h("h3", {}, tr("mailbox.pet_chat_name")), h("p", {}, tr("mailbox.pet_chat_hint"))));
  }
  for (const day of days) {
    flow.append(h("div", { class: "day" }, h("span", {}, M.dayLabel(day.dayTs))));
    for (const g of day.groups) flow.append(groupView(g, d, gi++, g === lastGroup));
  }
  content.append(flow);
  if (!d.orphan) content.append(composerBar(d, composeFocused, selStart, selEnd));
  const target = state.focus && flow.querySelector(`[data-msg="${CSS.escape(state.focus)}"]`);
  if (target) {
    target.scrollIntoView({ block: "center" });
    target.classList.add("flash");
    state.focus = null;
  } else if (scrollMode === "preserve") {
    flow.scrollTop = keepScrollTop;
  } else {
    flow.scrollTop = flow.scrollHeight;
  }
}

function threadHeader(d) {
  const agent = d.orphan ? "" : M.threadAgent(d, state.aliases);
  const badge = M.threadTopicBadge(d, state.merged, state.aliases, hintMap());
  const name = threadName(d);
  const rawSub = M.cleanThreadSubtitle(d, hintMap());
  const sub = rawSub && rawSub.toLowerCase() !== String(name || "").trim().toLowerCase() ? rawSub : "";
  const showStatus = !d.orphan && d.messages?.length && d.status === "waiting_user";
  return h("header", { class: "thread-head" },
    avatar(agent, "lg", d.orphan),
    h("div", { class: "titles" },
      h("h2", {}, name, badge ? h("span", { class: `chip chip-${badge.kind}` }, badge.label) : null),
      sub ? h("div", { class: "sub", title: sub }, sub) : null),
    showStatus ? pill(M.threadStatusInfo(d.status)) : null);
}

function composerBar(d, restoreFocus = false, selStart = 0, selEnd = 0) {
  const saved = state.drafts.get(d.id) || "";
  const ta = h("textarea", {
    id: "mb-compose", rows: "1", maxlength: "2000", spellcheck: "false",
    placeholder: d.id === "pet/chat" ? tr("mailbox.compose_placeholder_muse") : tr("mailbox.compose_placeholder_thread"),
    oninput: (e) => {
      state.drafts.set(d.id, e.target.value);
      e.target.style.height = "auto";
      e.target.style.height = `${Math.min(e.target.scrollHeight, 108)}px`;
      btn.disabled = !e.target.value.trim() || state.sending;
    },
    onkeydown: (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submitComposer(); }
    },
  });
  if (saved) ta.value = saved;
  const btn = h("button", {
    type: "button", class: "composer-send", disabled: !saved.trim() || state.sending,
    "aria-label": tr("mailbox.compose_send"), icon: "send", onclick: submitComposer,
  });
  const errEl = h("div", { class: "composer-err", hidden: true });
  async function submitComposer() {
    const text = ta.value.trim();
    if (!text || state.sending) return;
    state.sending = true;
    btn.disabled = true;
    ta.disabled = true;
    errEl.hidden = true;
    try {
      await state.client.send_message(d.id, text);
      state.drafts.delete(d.id);
      ta.value = "";
      ta.style.height = "auto";
      await loadThreads();
      renderSidebar();
      await openThread(d.id);
      $("#mb-compose")?.focus();
    } catch (err) {
      errEl.textContent = (err && err.message) || tr("mailbox.compose_failed");
      errEl.hidden = false;
    } finally {
      state.sending = false;
      ta.disabled = false;
      btn.disabled = !ta.value.trim();
    }
  }
  if (saved || restoreFocus) {
    requestAnimationFrame(() => {
      if (saved) { ta.style.height = "auto"; ta.style.height = `${Math.min(ta.scrollHeight, 108)}px`; }
      if (restoreFocus) { ta.focus(); try { ta.setSelectionRange(selStart, selEnd); } catch {} }
    });
  }
  return h("div", { class: "composer" },
    h("div", { class: "composer-box" }, ta, errEl),
    btn);
}

function groupView(g, d, index, isLastGroup = false) {
  const wrap = h("section", { class: "group", style: `--i:${Math.min(index, 12)}` });
  const activeWaiting = isLastGroup && d.status === "waiting_user";
  if (g.preReplies && g.preReplies.length) {
    g.preReplies.forEach((r, i) => wrap.append(replyRow(r, i === 0, !g.request && !g.replies.length && activeWaiting && i === g.preReplies.length - 1)));
  }
  if (g.request) wrap.append(requestRow(g.request, d));
  // consecutive Muse replies read as one block: the avatar and name appear once
  g.replies.forEach((r, i) => wrap.append(replyRow(r, i === 0, activeWaiting && i === g.replies.length - 1)));
  return wrap;
}

function bodyText(m) {
  if (m.parse_error) return h("span", { class: "empty" }, tr("mailbox.parse_error", { error: m.parse_error }));
  return m.text ? renderMarkdown(m.text) : h("span", { class: "empty" }, tr("mailbox.no_body"));
}

function requestStatusNote(m) {
  if (m.state === "rejected") {
    return h("div", { class: "foot" }, pill({ label: tr("mailbox.not_delivered"), tone: "red" }));
  }
  if (m.state === "delivered" || m.state === "claimed") {
    return h("div", { class: "foot" }, h("span", { class: "wait-note" }, tr("mailbox.waiting_muse")));
  }
  return null;
}

function requestRow(m, d) {
  const agent = M.canonicalAgent(m.agent || M.threadAgent(d, state.aliases), state.aliases);
  const isUser = m.role === "user" || agent === "pet";
  const name = isUser ? tr("mailbox.you") : M.displayName(agent, state.names);
  const showType = !isUser && (m.type === "publish-release" || m.type === "publish-voicebridge");
  const whoRow = h("div", { class: "who" }, h("span", {}, name), showType ? h("span", { class: "chip" }, M.typeLabel(m.type)) : null,
    h("span", {}, M.clock(m.sort_ts)));
  const bubbleCls = m.text && !m.parse_error ? "bubble has-md" : "bubble";
  const stack = h("div", { class: "stack" },
    whoRow,
    h("div", { class: bubbleCls }, bodyText(m)),
    m.release_card ? releaseCard(m.release_card, m) : null,
    requestStatusNote(m));
  if (isUser) {
    const av = avatar("user", "sm");
    av.classList.add("is-interactive");
    av.title = tr("pet.set_avatar");
    av.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      e.stopPropagation();
      openAvatarCtxMenu(e.clientX, e.clientY);
    });
    return h("div", { class: "row user", "data-msg": m.message_id }, stack, av);
  }
  return h("div", { class: "row agent", "data-msg": m.message_id }, stack, avatar(agent, "sm"));
}

function replyRow(m, showWho = true, showNeedsUser = false) {
  const info = M.replyInfo(m);
  const quarantined = m.trust === "rejected_by_client";
  const cls = ["bubble", m.text && !m.parse_error ? "has-md" : "", quarantined ? "quarantined" : m.orphan ? "orphan" : ""].filter(Boolean).join(" ");
  const st = (m.status || "").toLowerCase();
  const showPill = !quarantined && (
    st === "error"
    || (showNeedsUser && (st === "needs-user" || st === "needs_user"))
  );
  const hasFoot = !showWho || showPill || quarantined;
  return h("div", { class: `row muse${showWho ? "" : " cont"}`, "data-msg": m.message_id },
    showWho ? avatar("muse", "sm") : h("span", { class: "avatar-gap", "aria-hidden": "true" }),
    h("div", { class: "stack" },
      showWho ? h("div", { class: "who" }, h("span", {}, "Muse"), h("span", {}, M.clock(m.sort_ts))) : null,
      h("div", { class: cls }, bodyText(m)),
      hasFoot ? h("div", { class: "foot" },
        showWho ? null : h("span", {}, M.clock(m.sort_ts)),
        quarantined ? h("span", { class: "pill tone-red" }, tr("mailbox.quarantined")) : null,
        showPill ? pill(info) : null) : null));
}

function releaseCard(c, m) {
  const statusInfo = { ok: { label: tr("mailbox.published"), tone: "green" }, error: { label: tr("mailbox.failed"), tone: "red" },
    "needs-user": { label: tr("mailbox.confirm"), tone: "orange" } }[c.status]
    || (m.state === "replied" ? { label: tr("mailbox.done"), tone: "green" } : m.state === "rejected"
      ? { label: tr("mailbox.rejected"), tone: "red" } : { label: tr("mailbox.active"), tone: "blue" });
  const rows = [
    [tr("mailbox.package"), c.package_path, c.package_size, c.sha256],
    [tr("mailbox.setup"), c.setup_path, c.setup_size, c.setup_sha256],
  ].filter((r) => r[1]);
  return h("div", { class: "release" },
    h("div", { class: "top" }, h("span", { class: "icon", icon: "package" }),
      h("div", {}, h("div", { class: "ver" }, c.version ? `v${c.version}` : tr("mailbox.unknown_version"),
        c.test ? h("small", {}, tr("mailbox.test_release")) : null),
      h("div", { class: "hash" }, tr("mailbox.release_card"))),
      pill(statusInfo)),
    h("dl", {}, rows.map(([label, path, size, sha]) => [
      h("dt", {}, label),
      h("dd", {}, h("span", { class: "file", title: path }, M.baseName(path)), h("span", { class: "size" }, M.formatSize(size))),
      h("dt", {}, "SHA-256"),
      h("dd", {}, h("span", { class: "hash", title: sha }, M.shortHash(sha)), sha ? copyButton(sha) : null),
    ])));
}

function copyButton(text) {
  const btn = h("button", { class: "copy", title: tr("mailbox.copy_hash") }, tr("mailbox.copy"));
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    try { await navigator.clipboard.writeText(text); } catch {
      const ta = h("textarea", { style: "position:fixed;opacity:0" }, text);
      document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove();
    }
    btn.textContent = tr("mailbox.copied"); btn.classList.add("done");
    setTimeout(() => { btn.textContent = tr("mailbox.copy"); btn.classList.remove("done"); }, 1400);
  });
  return btn;
}

// ---- thread context menu (right-click delete) ---------------------------
let ctxMenu = null;
function closeCtxMenu() {
  if (ctxMenu) { ctxMenu.remove(); ctxMenu = null; }
}

function positionCtxMenu(menu, clientX, clientY) {
  document.body.append(menu);
  const rect = menu.getBoundingClientRect();
  const x = Math.max(8, Math.min(clientX, window.innerWidth - rect.width - 8));
  const y = Math.max(8, Math.min(clientY, window.innerHeight - rect.height - 8));
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
  ctxMenu = menu;
}

function openAvatarCtxMenu(clientX, clientY) {
  closeCtxMenu();
  const menu = h("div", { class: "ctx-menu", role: "menu", "aria-label": tr("pet.set_avatar") });
  const btn = h("button", {
    type: "button", class: "ctx-item", role: "menuitem",
    onclick: async (e) => {
      e.stopPropagation();
      closeCtxMenu();
      try {
        const res = await state.client.set_avatar();
        if (res && res.updated) {
          state.userAvatar = await state.client.avatar().catch(() => null);
          const flow = $("#flow");
          renderContent({ animate: false, scrollMode: flow ? "preserve" : "bottom", keepScrollTop: flow ? flow.scrollTop : 0 });
        }
      } catch (err) {
        state.status = { ...(state.status || {}), last_error: String(err && err.message || err) };
        renderSync();
      }
    },
  }, h("span", { class: "ctx-icon", icon: "user" }), h("span", {}, tr("pet.set_avatar")));
  menu.append(btn);
  positionCtxMenu(menu, clientX, clientY);
}

function openCtxMenu(thread, clientX, clientY) {
  closeCtxMenu();
  let armed = false;
  const menu = h("div", { class: "ctx-menu", role: "menu", "aria-label": tr("mailbox.menu") });
  const btn = h("button", {
    type: "button", class: "ctx-item danger", role: "menuitem",
    onclick: async (e) => {
      e.stopPropagation();
      if (!armed) {
        armed = true;
        btn.classList.add("armed");
        btn.querySelector("span:last-child").textContent = tr("mailbox.delete_confirm");
        return;
      }
      closeCtxMenu();
      await deleteThread(thread);
    },
  }, h("span", { class: "ctx-icon", icon: "trash" }), h("span", {}, tr("mailbox.delete_thread")));
  menu.append(btn);
  positionCtxMenu(menu, clientX, clientY);
}

async function deleteThread(thread) {
  try {
    await state.client.delete_thread(thread.sourceIds || [thread.id]);
    state.merged = state.merged.filter((x) => x.id !== thread.id);
    if (state.selected === thread.id) {
      const next = M.sidebarSections(state.merged).flatMap((s) => s.items)[0];
      state.selected = null;
      state.detail = null;
      renderSidebar();
      if (next) await openThread(next.id);
      else renderContent();
    } else {
      renderSidebar();
    }
  } catch (err) {
    state.status = { ...(state.status || {}), last_error: String(err && err.message || err) };
    renderSync();
  }
}

function refreshLanguage() {
  document.title = tr("mailbox.title");
  I18N.scan(document);
  renderSync();
  renderSidebar();
  renderContent();
}

// ---- data flow -----------------------------------------------------------
function applyMergedThreads(raw) {
  state.merged = M.mergeThreadList(raw, state.aliases, hintMap());
  if (!state.merged.some((t) => t.id === "pet/chat")) {
    state.merged.unshift({
      id: "pet/chat", title: "Muse", participants: ["pet", "muse"], status: "done",
      last_activity: "", last_ts: 0, unread: 0, snippet: "", message_count: 0,
      sourceIds: ["pet/chat"], orphan: false, pinned: true, starter: true,
    });
  }
  if (state.selected && !state.merged.some((t) => t.id === state.selected)) {
    const owner = state.merged.find((t) => t.sourceIds && t.sourceIds.includes(state.selected));
    if (owner) state.selected = owner.id;
  }
}

async function hydrateThreadHints(raw) {
  const pending = (raw || []).filter((t) => {
    if (!t || t.id === "pet/chat" || M.isOrphanThread(t)) return false;
    const sig = `${t.last_ts}:${t.message_count}`;
    const prev = state.threadHints.get(t.id);
    return !prev || prev.sig !== sig;
  }).slice(0, 16);
  if (!pending.length) return;
  let updated = false;
  await Promise.all(pending.map(async (t) => {
    const canon = M.canonicalThreadId(t.id, state.aliases);
    const sig = `${t.last_ts}:${t.message_count}`;
    try {
      const detail = await state.client.thread(t.id);
      const hint = M.extractThreadHint(detail);
      if (hint) {
        state.threadHints.set(t.id, { canon, sig, hint });
        updated = true;
      }
    } catch {}
  }));
  if (updated && state.rawThreads === raw) {
    const prevCount = state.detail && state.detail.sourceIds ? state.detail.sourceIds.length : 0;
    applyMergedThreads(raw);
    renderSidebar();
    const cur = state.selected ? state.merged.find((x) => x.id === state.selected) : null;
    if (state.detail && cur && cur.sourceIds && cur.sourceIds.length !== prevCount) {
      openThread(cur.id, null, true);
    } else if (state.detail) {
      const head = $(".thread-head");
      if (head) head.replaceWith(threadHeader(state.detail));
    }
  }
}

async function loadThreads() {
  const raw = await state.client.threads();
  state.rawThreads = raw;
  applyMergedThreads(raw);
  await hydrateThreadHints(raw);
  state.error = null;
}

function markCurrentSeen() {
  if (!state.visible || !state.selected || !state.detail || !state.client) return;
  const t = state.merged.find((x) => x.id === state.selected);
  if (!t || !t.unread) return;
  t.unread = 0;
  renderSidebar();
  state.client.mark_seen(t.sourceIds || [t.id]).catch(() => {});
}

async function openThread(id, focusMessageId = null, fromPoll = false) {
  const t = state.merged.find((x) => x.id === id);
  if (!t) return;
  const prevFlow = $("#flow");
  const sameThread = state.detail && state.detail.id === id;
  const wasAtBottom = !prevFlow || prevFlow.scrollHeight - prevFlow.scrollTop - prevFlow.clientHeight < 40;
  const prevTop = prevFlow ? prevFlow.scrollTop : 0;
  state.selected = id;
  state.focus = focusMessageId;
  renderSidebar();
  let nextDetail = null;
  try {
    const details = await Promise.all(t.sourceIds.map((src) => state.client.thread(src)));
    if (state.selected !== id) return;           // the user moved on meanwhile
    nextDetail = M.mergeThreadDetails(details, state.aliases);
    if (nextDetail) {
      nextDetail.orphan = t.orphan;
      const hint = M.extractThreadHint(nextDetail);
      if (hint && t.sourceIds.length === 1 && t.sourceIds[0] === t.id) {
        state.threadHints.set(t.id, { canon: t.id, sig: `${t.last_ts}:${t.message_count}`, hint });
      }
    }
    else if (t.id === "pet/chat") nextDetail = { ...t, messages: [] };
  } catch (err) {
    state.detail = null;
    state.detailSig = "";
    state.error = String(err && err.message || err);
    renderContent();
    return;
  }
  const nextSig = detailSignature(nextDetail);
  if (fromPoll && sameThread && !focusMessageId && nextSig === state.detailSig && $("#flow")) {
    state.detail = nextDetail;
    const head = $(".thread-head");
    if (head) head.replaceWith(threadHeader(nextDetail));
  } else {
    state.detail = nextDetail;
    state.detailSig = nextSig;
    const preserve = fromPoll && sameThread && !focusMessageId && !wasAtBottom;
    renderContent({
      animate: !(fromPoll && sameThread),
      scrollMode: preserve ? "preserve" : "bottom",
      keepScrollTop: prevTop,
    });
  }
  if (state.detail && (!fromPoll || (state.visible && document.hasFocus()))) markCurrentSeen();
}

let searchTimer = null;
function onSearchInput(e) {
  const q = e.target.value.trim();
  $(".search").classList.toggle("has-text", !!e.target.value);
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    state.query = q;
    state.results = q ? await state.client.search(q, 50).catch(() => []) : null;
    renderSidebar();
  }, 180);
}

function clearSearch() {
  const input = $("#search-input");
  input.value = "";
  $(".search").classList.remove("has-text");
  state.query = ""; state.results = null;
  renderSidebar();
}

function renderSync() {
  const s = state.status || {};
  const el = $("#sync");
  el.classList.toggle("busy", state.syncing || !!s.syncing);
  el.classList.toggle("error", !!s.last_error);
  const label = state.syncing || s.syncing ? tr("mailbox.syncing") : s.last_error ? tr("mailbox.sync_error") : state.client && state.client.kind === "demo" ? tr("mailbox.demo") : tr("mailbox.synced");
  $("#sync-label").textContent = label;
  el.title = s.last_error ? String(s.last_error) : s.built_at ? tr("mailbox.index_updated", { date: I18N.date(new Date(s.built_at), {dateStyle: "short", timeStyle: "short"}) }) : "";
}

async function poll(force = false) {
  try {
    const s = await state.client.status();
    const changed = !state.status || s.built_at !== state.status.built_at || s.messages !== state.status.messages;
    state.status = s;
    renderSync();
    if (changed || force) {
      await loadThreads();
      renderSidebar();
      if (state.selected && state.merged.some((t) => t.id === state.selected)) {
        await openThread(state.selected, null, true);
      }
    }
  } catch (err) {
    state.status = { ...(state.status || {}), last_error: String(err && err.message || err) };
    renderSync();
  }
}

async function refreshNow() {
  state.syncing = true; renderSync();
  try { state.status = await state.client.refresh(); } catch (err) { state.error = String(err.message || err); }
  state.syncing = false;
  await poll(true);
}

function onKey(e) {
  const inField = /^(INPUT|TEXTAREA)$/.test(document.activeElement?.tagName || "");
  if ((e.key === "f" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !inField)) {
    e.preventDefault(); $("#search-input").focus(); return;
  }
  if (e.key === "Escape" && state.results) { clearSearch(); return; }
  if ((e.key === "ArrowDown" || e.key === "ArrowUp") && !inField && !state.results) {
    const order = M.sidebarSections(state.merged).flatMap((s) => s.items).map((t) => t.id);
    if (!order.length) return;
    const i = order.indexOf(state.selected);
    const next = order[Math.max(0, Math.min(order.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))];
    e.preventDefault();
    openThread(next).then(() => {
      const row = document.querySelector(`.thread-row[data-id="${CSS.escape(next)}"]`);
      if (row) row.scrollIntoView({ block: "nearest" });
    });
  }
}

async function main() {
  try {
    state.client = await connect();
    I18N.init(await state.client.i18n());
  $("#search-icon").innerHTML = ICONS.search;
  $("#search-input").addEventListener("input", onSearchInput);
  $("#search-clear").addEventListener("click", clearSearch);
  $("#sync").addEventListener("click", refreshNow);
  document.addEventListener("keydown", onKey);
  renderSidebar();
  renderContent();
    const cfg = (await state.client.config()) || {};
    state.aliases = { ...M.DEFAULT_ALIASES, ...(cfg.agent_aliases || {}) };
    state.names = { ...M.DEFAULT_NAMES, ...(cfg.display_names || {}) };
    state.userAvatar = await state.client.avatar().catch(() => null);
    if (state.client.kind === "demo") $("#demo-banner").hidden = false;
    if (state.client.kind === "pywebview") {
      $("#winctl").hidden = false;
      $("#win-min").addEventListener("click", (e) => { e.currentTarget.blur(); state.client.minimize(); });
      $("#win-close").addEventListener("click", (e) => { e.currentTarget.blur(); state.visible = false; state.client.hide(); });
    }
    state.status = await state.client.status();
    await loadThreads();
    state.loading = false;
    renderSync();
    renderSidebar();
    // deep link: #thread=<id> (the pet / a notification opens one directly)
    const wanted = new URLSearchParams(location.hash.slice(1)).get("thread");
    const target = wanted && state.merged.find((t) => t.id === M.canonicalThreadId(wanted, state.aliases));
    const first = target || M.sidebarSections(state.merged).flatMap((s) => s.items)[0];
    if (first) await openThread(first.id); else renderContent();
    let lastKeyAt = 0;
    document.addEventListener("keydown", () => { lastKeyAt = Date.now(); }, true);
    const clearWinCtlFocus = () => {
      const ae = document.activeElement;
      if ((ae === $("#win-min") || ae === $("#win-close")) && Date.now() - lastKeyAt > 200) ae.blur();
    };
    window.MuseMailbox = {
      onShow() { state.visible = true; clearWinCtlFocus(); markCurrentSeen(); },
      onHide() { state.visible = false; clearWinCtlFocus(); },
      async refreshAvatar() {
        state.userAvatar = await state.client.avatar().catch(() => null);
        const flow = $("#flow");
        renderContent({ animate: false, scrollMode: flow ? "preserve" : "bottom", keepScrollTop: flow ? flow.scrollTop : 0 });
      },
    };
    window.addEventListener("focus", () => { clearWinCtlFocus(); if (state.visible) markCurrentSeen(); });
    window.addEventListener("hashchange", () => {
      const id = new URLSearchParams(location.hash.slice(1)).get("thread");
      if (id) openThread(M.canonicalThreadId(id, state.aliases));
    });
    document.addEventListener("pointerdown", (e) => { if (ctxMenu && !ctxMenu.contains(e.target)) closeCtxMenu(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeCtxMenu(); });
    window.addEventListener("blur", closeCtxMenu);
    I18N.follow(state.client, refreshLanguage);
    setInterval(() => poll(false), Math.max(2000, Number(cfg.poll_ms) || 5000));
  } catch (err) {
    state.loading = false;
    state.error = String(err && err.message || err);
    renderSidebar();
    renderContent();
  }
}

main();
