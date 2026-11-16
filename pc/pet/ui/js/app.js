import { I18N, t as tr } from "../../../i18n/i18n.js";
// app.js - desktop pet UI: renders model.js decisions into the DOM.
// Safety rule: ask summaries, briefs and channel details come from Muse and
// local files and are UNTRUSTED: only ever set as text (directly or via h());
// innerHTML is used for the static icon constants only.

import { connect } from "./api.js";
import { ICONS } from "./icons.js";
import * as M from "./model.js";
import { playConfirm } from "./sound.js";
import { Sprite } from "./sprite.js";
import { phaseUntil, workingBrief } from "./brief.js";

const $ = (sel, root = document) => root.querySelector(sel);

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
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

const params = new URLSearchParams(location.search);
const ui = {
  client: null, cfg: { bubble_ms: 8000, poll_ms: 1000, sound: true },
  snap: { state: "idle" }, receivedUntil: 0, celebrated: new Set(),
  typing: false,        // the chat box has focus: the pet listens (waiting row)
  hover: params.get("hover") === "1", forceHover: params.get("hover") === "1",
  panel: null,          // null | {kind:"talk", replyTo, status, error, busy} | {kind:"channel", data}
  brief: null, briefUntil: 0, lastBriefId: null,
  leaving: false, hideTimer: 0, channelTimer: 0,
  region: { key: "", timer: 0 },
  drag: null,           // {x, y, started} while the primary button is down on the pet
  menu: null, languages: [],
};
const win = () => ({ w: window.innerWidth, h: window.innerHeight });
const sprite = new Sprite($("#body"));

// ---- orbs ---------------------------------------------------------------------
function buildOrbs() {
  const box = $("#orbs");
  box.replaceChildren();
  for (const a of [...M.ACTIONS, M.DONE]) {
    box.append(h("button", {
      class: `orb orb-${a.id}${a.primary ? " primary" : ""}${M.tipAbove(a.angle) ? " tip-above" : ""}`,
      type: "button", "data-id": a.id,
      "aria-label": a.label,
      onpointerenter: () => syncRegion(),
      onpointerleave: () => syncRegion(),
      onfocus: () => syncRegion(),
      onblur: () => syncRegion(),
      onclick: (e) => { e.stopPropagation(); act(a.id); },
    }, h("span", { class: "glyph", icon: a.id }), h("span", { class: "tip" }, a.label), a.id === "inbox" ? h("span", {class:"unread-badge",hidden:true,"aria-hidden":"true"}) : null));
  }
}

// Rig-relative placement (the rig is pinned bottom-right by CSS), so the
// layout never depends on the window size the page happened to load with.
function layout() {
  const s = M.UI_SCALE;
  const rig = $("#rig");
  rig.style.setProperty("--ui-scale", s);
  rig.style.setProperty("--sprite-scale", M.SPRITE_SCALE);
  rig.style.setProperty("--sprite-bottom", `${M.SPRITE_BOTTOM * M.SPRITE_SCALE}px`);
  // whole pixels: offsetLeft/offsetTop (orbExtent) are integers
  Object.assign(rig.style, { width: `${M.PET.w}px`, height: `${M.PET.h}px`,
    right: `${Math.round((M.SPILL.right + M.PET.right + M.PET.w) * s - M.PET.w)}px`,
    bottom: `${Math.round((M.SPILL.bottom + M.PET.bottom + M.PET.h) * s - M.PET.h)}px` });
  rig.style.setProperty("--sprite-top", `${Math.round(M.SPRITE_BOX.y)}px`);
  rig.style.setProperty("--sprite-right", `${M.SPRITE_BOX.x + M.SPRITE_BOX.w}px`);
  rig.style.setProperty("--mouth-y", `${Math.round(M.SPRITE_BOX.y + M.SPRITE_BOX.h * 0.4)}px`);
  const p = M.SPRITE_BOX;
  Object.assign($("#pet").style, { left: `${p.x}px`, top: `${p.y}px`, width: `${p.w}px`, height: `${p.h}px` });
  placeArc();
}

const avail = () => {
  const a = ui.screen || {x:0,y:0,w:innerWidth,h:innerHeight};
  const x=Math.max(0,a.x), y=Math.max(0,a.y);
  return {x,y,w:Math.max(0,Math.min(innerWidth,a.x+a.w)-x),h:Math.max(0,Math.min(innerHeight,a.y+a.h)-y)};
};
const size = el => { const s=getComputedStyle(el); return {w:parseFloat(s.width),h:parseFloat(s.height)}; };
const rigOrigin = () => ({x:$("#rig").offsetLeft,y:$("#rig").offsetTop});
function placeArc() {
  const rig=$("#rig"), origin=rigOrigin(), p=M.SPRITE_BOX;
  ui.arc=M.orientArc(origin,avail());
  rig.dataset.arc = `${ui.arc.sx},${ui.arc.sy}`;
  for (const a of [...M.ACTIONS, M.DONE]) {
    const placed = ui.arc.rects[a.id];
    const r = {...placed,x:placed.x-origin.x,y:placed.y-origin.y};
    const el = $(`.orb[data-id="${a.id}"]`);
    Object.assign(el.style, { left: `${r.x}px`, top: `${r.y}px` });
    el.style.setProperty("--dx", `${Math.round(p.x + p.w / 2 - (r.x + r.w / 2))}px`);
    el.style.setProperty("--dy", `${Math.round(p.y + p.h / 2 - (r.y + r.h / 2))}px`);
  }
  const hoverOrbs = M.ACTIONS.map((a) => ui.arc.rects[a.id]);
  if (current() === "waiting_user" && ui.arc.rects.done) hoverOrbs.push(ui.arc.rects.done);
  const zone = M.fit(M.inflate(M.unionRect([M.fromRig(M.SPRITE_BOX,origin), ...hoverOrbs]),18),"left",avail());
  zone.x-=origin.x; zone.y-=origin.y;
  placeTips();
  Object.assign($("#zone").style, { left: `${zone.x}px`, top: `${zone.y}px`, width: `${zone.w}px`, height: `${zone.h}px` });
}

// ---- render -------------------------------------------------------------------
function current() { return M.displayState(ui.snap, ui.receivedUntil, Date.now()); }

function render() {
  const st = current();
  const stage = $("#stage");
  stage.dataset.state = st;
  stage.classList.toggle("hover", ui.hover && !ui.panel && !ui.menu);
  // While the user types to Muse the pet listens (headphones row); the
  // "received" moment still wins.
  sprite.play(ui.typing && ui.panel && ui.panel.kind === "talk" && st !== "received" ? "waiting_user" : st);
  const shown = M.visibleControls({ state: st, hover: ui.hover, panel: !!ui.panel || !!ui.menu, ask: ui.snap && ui.snap.ask });
  for (const el of document.querySelectorAll(".orb")) el.classList.toggle("on", shown.has(el.dataset.id));
  $(".orb-talk").classList.toggle("reply", st === "waiting_user");
  placeArc();
  const badge = $(".orb-inbox .unread-badge");
  const unread = Math.max(0, Number(ui.snap.unread) || 0);
  badge.textContent = unread > 99 ? "99+" : String(unread);
  badge.hidden = !unread;
  $(".orb-inbox").setAttribute("aria-label", unread ? `${tr("pet.inbox")}, ${tr("mailbox.unread_replies")}: ${unread}` : tr("pet.inbox"));
  renderBusy(st);
  renderBubble(st);
  renderPanel();
  renderMenu();
  syncRegion();
}

function renderBusy(st) {
  const el = $(".busy");
  const text = st === "working" ? workingBrief(ui.snap, ui.brief, ui.briefUntil) : null;
  const label = $(".busy-text", el);
  label.textContent = text || "";
  label.hidden = !text;
  el.classList.toggle("has-text", !!text);
  el.setAttribute("aria-hidden", String(st !== "working"));
  el.setAttribute("aria-label", text || tr("pet.activity.working"));
  placeBusy();
}

const plainAsk = (s) => String(s || "").replace(/\*\*([^*\n]+)\*\*|`([^`\n]+)`/g, "$1$2");

function renderBubble(st) {
  const el = $("#bubble");
  let body = null;
  if (!ui.panel && !ui.menu && st === "waiting_user" && ui.snap.ask) {
    const ask = ui.snap.ask;
    body = h("div", { class: "ask", onclick: () => openTalk(true) },
      h("div", { class: "title" }, h("span", { class: "dot" }), M.askTitle(ask)),
      ask.summary ? h("div", { class: "text" }, M.clip(plainAsk(ask.summary), 96)) : null,
      h("div", { class: "hint" }, tr("pet.hint_done"), h("b", { class: "k-done", icon: "done" }), tr("pet.hint_talk"),
        h("b", { class: "k-talk", icon: "talk" }), tr("pet.hint_reply")));
  }
  if (body) {
    el.replaceChildren(body);
    el.classList.toggle("tone-ask", st === "waiting_user");
    setShown(el, true);
  } else {
    setShown(el, false);
  }
}

function setShown(el, on) {
  if (on) {
    clearTimeout(el._t);
    cancelAnimationFrame(el._raf);
    el.hidden = false;
    el._raf = requestAnimationFrame(() => { el._raf = 0; el.classList.add("in"); });
  } else {
    cancelAnimationFrame(el._raf);
    el._raf = 0;
    if (!el.hidden) {
      el.classList.remove("in");
      clearTimeout(el._t);
      el._t = setTimeout(() => {
        el.hidden = true;
        if (el.id === "bubble") { el.classList.remove("tone-ask"); el.replaceChildren(); }
        syncRegion();
      }, 260);
    }
  }
}

// ---- panels -------------------------------------------------------------------
function renderPanel() {
  const el = $("#panel");
  const p = ui.panel;
  if (!p) { setShown(el, false); return; }
  if (el.dataset.kind !== p.kind) { el.replaceChildren(p.kind === "talk" ? talkView(p) : channelView()); el.dataset.kind = p.kind; }
  if (p.kind === "talk") updateTalk(p);
  if (p.kind === "channel") updateChannel(p);
  setShown(el, true);
  placeTalk();
}

// Set positions from stable layout measurements; entry transforms never feed
// back into the next placement. fit() handles every work-area edge.
function placeTalk() {
  const el=$("#panel");
  if (el.hidden) return;
  const origin=rigOrigin(), v=avail();
  el.style.maxWidth=`${Math.min(300,v.w)}px`;
  el.style.maxHeight=`${v.h}px`; el.style.overflowY="auto";
  const {w,h}=size(el);
  const base={x:origin.x+M.PET.w+2-w,y:origin.y+Math.round(M.SPRITE_BOX.y)+(el.dataset.kind==="talk"?1:6)-h,w,h};
  // Fitted coordinates never overwrite the desired CSS percentage.
  const ratio=el.dataset.kind === "talk" ? parseFloat(getComputedStyle(el).getPropertyValue("--talk-shift"))/100 : 0;
  const arcBand=ui.arc?.sx === -1 ? M.ORB+18 : 0;
  const r=M.fit({...base,y:base.y+h*(Number.isFinite(ratio)?ratio:0),
    x:base.x-arcBand,anchor:M.fromRig(M.SPRITE_BOX,origin),gap:8+arcBand},"left",v);
  el.dataset.side=r.side;
  el.style.left=`${r.x-origin.x}px`; el.style.right="auto";
  el.style.top=`${r.y-origin.y}px`; el.style.bottom="auto";
}

function placeBusy() {
  const el=$(".busy"), origin=rigOrigin(), v=avail();
  el.style.maxWidth=`${Math.min(218,v.w)}px`;
  const {w,h}=size(el);
  const shown = M.visibleControls({ state: current(), hover: ui.hover, panel: !!ui.panel || !!ui.menu, ask: ui.snap && ui.snap.ask });
  const visArc = ui.arc ? { ...ui.arc, rects: Object.fromEntries(Object.entries(ui.arc.rects || {}).filter(([id]) => shown.has(id))) } : null;
  const r=M.busyRect(w,h,origin,v,visArc);
  el.dataset.side=r.side;
  el.style.left=`${r.x-origin.x}px`; el.style.top=`${r.y-origin.y+h/2}px`;
}

function placeTips() {
  const origin=rigOrigin(),v=avail(),sx=ui.arc?.sx||1;
  const doneEl=$(".orb-done"), doneOn=doneEl&&doneEl.classList.contains("on");
  const doneRect=doneOn?{x:origin.x+doneEl.offsetLeft,y:origin.y+doneEl.offsetTop,w:M.ORB,h:M.ORB}:null;
  const side=sx===1?"left":"right";
  for (const el of document.querySelectorAll(".orb")) {
    const tip=$(".tip",el), {w,h}=size(tip);
    const r={x:origin.x+el.offsetLeft,y:origin.y+el.offsetTop,w:M.ORB,h:M.ORB};
    const above=el.classList.contains("tip-above");
    let q;
    if (el.dataset.id==="talk" && doneRect) {
      const below={x:r.x+r.w/2-w/2,y:r.y+r.h+8,w,h};
      const top={x:r.x+r.w/2-w/2,y:r.y-8-h,w,h};
      const besideDone={x:sx===1?doneRect.x-8-w:doneRect.x+doneRect.w+8,y:doneRect.y-8-h,w,h};
      const pref=!ui.hover?top:M.contains(v,M.hoverExtent(below,r,origin,8))?below:besideDone;
      q=M.fit({...pref,anchor:r,gap:8},side,v);
    } else {
      const x=above?r.x+r.w/2-w/2:sx===1?r.x-8-w:r.x+r.w+8;
      const y=above?r.y-8-h:r.y+r.h/2-h/2;
      q=M.fit({x,y,w,h,anchor:r,gap:8},side,v);
    }
    for(let i=0;i<6;i++) {
      const reach=M.hoverExtent(q,r,origin,8);
      if(M.contains(v,reach)) break;
      const fixed=M.fit(reach,side,v);
      q.x+=fixed.x-reach.x;q.y+=fixed.y-reach.y;
    }
    Object.assign(tip.style,{left:`${q.x-r.x}px`,top:`${q.y-r.y}px`,right:"auto",bottom:"auto",transform:"none"});
  }
}

function placeBubble() {
  const el=$("#bubble"); if(el.hidden) return;
  const o=rigOrigin(),v=avail();
  el.style.maxWidth=`${Math.min(264,v.w)}px`;
  const {w,h}=size(el);
  const st=current();
  const shown=M.visibleControls({state:st,hover:ui.hover,panel:!!ui.panel||!!ui.menu,ask:ui.snap&&ui.snap.ask});
  const r=M.bubbleRect(w,h,o,v,ui.arc,shown,st==="waiting_user");
  Object.assign(el.style,{left:`${r.x-o.x}px`,top:`${r.y-o.y}px`,right:"auto",bottom:"auto"});
}

function header(title, extra) {
  return h("header", { class: "p-head" },
    h("span", { class: "p-mark" }), h("h2", {}, title), extra || null,
    h("button", { class: "p-close", type: "button", "aria-label": tr("pet.close"), icon: "close", onclick: closePanel }));
}

function talkView(p) {
  const ask = p.replyTo && ui.snap.ask && ui.snap.ask.req_id === p.replyTo ? ui.snap.ask : null;
  const input = h("textarea", {
    id: "say", rows: "2", maxlength: String(M.SAY_MAX), spellcheck: "false",
    placeholder: ask ? tr("pet.answer_placeholder") : tr("pet.talk_placeholder"),
    oninput: (e) => { autosize(e.target); updateTalk(ui.panel); },
    onfocus: () => { ui.typing = true; render(); },
    onblur: () => { ui.typing = false; render(); },
    onkeydown: (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
    },
  });
  return h("div", { class: "talk" },
    header(ask ? tr("pet.reply_muse") : tr("pet.talk_muse")),
    ask ? h("blockquote", { class: "quote" }, M.clip(plainAsk(ask.summary) || M.askTitle(ask), 120)) : null,
    h("div", { class: "field" }, input,
      h("button", { class: "send", type: "button", "aria-label": tr("pet.send"), icon: "send", onclick: send })),
    h("footer", { class: "p-foot" }, h("span", { class: "status" }), h("span", { class: "keys" }, tr("pet.keys"))));
}

function autosize(t) { t.style.height = "auto"; t.style.height = `${Math.min(t.scrollHeight, 132)}px`; syncRegion(); }

function updateTalk(p) {
  const t = $("#say");
  if (!t) return;
  const ok = M.checkSay(t.value).ok;
  $(".send").disabled = !ok || p.busy || !!p.sent;
  t.disabled = !!p.busy || !!p.sent;
  const s = $(".talk .status");
  s.className = `status${p.error ? " error" : p.sent ? " sent" : ""}`;
  s.textContent = p.error || p.status || "";
  $(".talk .keys").hidden = !!(p.error || p.status);
}

async function send() {
  const p = ui.panel;
  if (!p || p.kind !== "talk" || p.busy || p.sent) return;
  const check = M.checkSay($("#say").value);
  if (!check.ok) { p.error = check.error; updateTalk(p); return; }
  const target = M.sayTarget(p, ui.snap);
  p.busy = true; p.error = null; p.status = tr("pet.sending"); updateTalk(p);
  try {
    const res = await ui.client.say(check.text, target);
    p.busy = false; p.sent = true; p.status = M.sayResultText(res); updateTalk(p);
    if (target) ui.celebrated.add(target);
    celebrate();
    setTimeout(() => { if (ui.panel === p) closePanel(); }, res && res.via === "wait" ? 1100 : 1700);
  } catch (err) {
    p.busy = false; p.status = null; p.error = tr("pet.send_failed", { error: err.message || err }); updateTalk(p);
  }
}

function channelView() {
  return h("div", { class: "channel" },
    header(tr("pet.channels"), h("button", { class: "p-refresh", type: "button", "aria-label": tr("pet.refresh"), icon: "refresh", onclick: loadChannels })),
    h("ul", { class: "links" }, h("li", { class: "loading" }, tr("pet.checking"))),
    h("footer", { class: "p-foot" }, h("span", { class: "status" })));
}

function updateChannel(p) {
  const d = p.data;
  if (!d) return;
  const list = $(".channel .links");
  list.replaceChildren(...(d.items || []).map((c) => h("li", { class: `lv-${M.LEVELS[c.level] ? c.level : "unknown"}` },
    h("span", { class: "lamp" }), h("span", { class: "name" }, M.clip(c.label, 12)),
    h("span", { class: "detail", title: c.detail || "" }, M.clip(c.detail || M.LEVELS[c.level] || "", 34)))));
  const at = d.checked_at ? new Date(d.checked_at) : null;
  $(".channel .status").textContent = d.error ? d.error : at && !isNaN(at) ? tr("pet.checked_at", { time: I18N.date(at, {timeStyle: "short"}) }) : "";
  $(".channel").dataset.summary = M.channelSummary(d.items);
  syncRegion();
}

async function loadChannels() {
  const p = ui.panel;
  if (!p || p.kind !== "channel") return;
  try { p.data = await ui.client.channels(); } catch (err) { p.data = { items: (p.data && p.data.items) || [], error: tr("pet.channels_unavailable") }; }
  if (ui.panel === p) updateChannel(p);
}

function openTalk(reply) {
  closeMenu();
  const ask = ui.snap.ask;
  ui.panel = { kind: "talk", replyTo: reply && ask ? ask.req_id : null };
  $("#panel").dataset.kind = "";
  ui.hover = false;
  render();
  ui.client.screen().then((area) => { ui.screen = area; if (ui.panel?.kind === "talk") render(); }).catch(() => {});
  ui.client.activate(true).catch(() => {});
  requestAnimationFrame(() => { const t = $("#say"); if (t) t.focus(); });
}

function openChannel() {
  closeMenu();
  ui.panel = { kind: "channel", data: null };
  $("#panel").dataset.kind = "";
  ui.hover = false;
  render();
  loadChannels();
  clearInterval(ui.channelTimer);
  ui.channelTimer = setInterval(loadChannels, 5000);
}

function closePanel() {
  if (!ui.panel) return;
  const wasTalk = ui.panel.kind === "talk";
  ui.panel = null;
  ui.typing = false;     // a removed textarea fires no blur
  clearInterval(ui.channelTimer);
  render();
  if (wasTalk) ui.client.activate(false).catch(() => {});
}

// ---- context card: an inline language submenu, never a second window -----------
let collapseTimer = 0;
function cancelCollapse() { clearTimeout(collapseTimer); }
// The language flyout closes when the pointer has left both its trigger and the list.
function scheduleCollapse() {
  cancelCollapse();
  collapseTimer = setTimeout(() => {
    if (ui.menu && ui.menu.expanded) { ui.menu.expanded = false; render(); }
  }, 180);
}
function renderMenu() {
  const el = $("#context-card"), menu = ui.menu;
  if (!menu) { setShown(el, false); return; }
  // Polling must not replace the focused/hovered item beneath the pointer.
  const key = JSON.stringify([I18N.tag, menu.expanded, menu.busy, menu.error]);
  if (el.dataset.view !== key) {
    el.dataset.view = key;
    const row = (label, attrs = {}, ...children) => h("button", {
      type: "button", class: "context-row", role: "menuitem", ...attrs,
    }, h("span", {class: "row-label"}, label), ...children);
    const expand = () => {
      if (!menu.expanded) { menu.expanded = true; render(); }
    };
    el.replaceChildren(...[
      h("div", {role: "menu", "aria-label": tr("pet.menu")},
        row(tr("pet.hide"), {onclick: () => { closeMenu(); leave(); }}),
        row(tr("pet.exit"), {class: "context-row exit", onclick: async () => {
          try { await ui.client.exit(); closeMenu(); }
          catch { menu.error = tr("pet.error.backend"); render(); }
        }}),
        row(tr("pet.set_avatar"), {disabled: menu.busy, onclick: selectAvatar}),
        row(tr("pet.set_language"), {id: "language-trigger", "aria-expanded": String(menu.expanded),
          "aria-controls": "language-list", onpointerenter: expand, onpointerleave: scheduleCollapse, onclick: expand,
          onkeydown: (e) => { if (e.key === "ArrowRight") { expand(); $("#language-list button")?.focus(); } },
        }, h("span", {class: "chevron", "aria-hidden": "true"}, "›")),
        h("div", {id: "language-list", class: "language-list", role: "group", onpointerenter: cancelCollapse, onpointerleave: scheduleCollapse,
          "aria-label": tr("pet.set_language"), hidden: !menu.expanded},
        ui.languages.map(language => row(language.name, {
          role: "menuitemradio", "aria-checked": String(language.tag === I18N.tag),
          lang: language.tag, disabled: menu.busy, "data-language": language.tag,
          onclick: () => selectLanguage(language.tag),
        }, language.tag === I18N.tag ? h("span", {"aria-hidden": "true"}, "✓") : null))),
      ),
      menu.error ? h("p", {class: "context-error", role: "alert"}, menu.error) : null,
    ].filter(Boolean));   // replaceChildren(null) would print "null"
  }
  setShown(el, true);
  placeMenu();
}
// The menu opens from the character's centre towards the lower right; it
// flips left / up where the screen's work area would cut it. The language
// flyout opens to the right of the menu, same rules.
function placeMenu() {
  const card = $("#context-card"), list = $("#language-list");
  if (!card || card.hidden || !card.offsetParent) return;
  const OFF=14,GAP=6,V=avail();
  card.style.maxWidth=`${Math.min(244,V.w)}px`; card.style.maxHeight=`${V.h}px`;
  const pet=$("#pet").getBoundingClientRect();
  const anchor={x:pet.x+pet.width/2,y:pet.y+pet.height/2,w:0,h:0};
  const r=M.fit({x:anchor.x+OFF,y:anchor.y+OFF,...size(card),anchor,gap:OFF,flipY:true},"right",V);
  const parent=card.offsetParent.getBoundingClientRect();
  card.style.left=`${r.x-parent.left}px`;card.style.top=`${r.y-parent.top}px`;
  card.dataset.side=r.side;
  const row=$("#language-trigger");
  if(!list||list.hidden||!row)return;
  list.style.maxHeight=`${Math.min(220,V.h)}px`;
  const q=M.fit({x:r.x+r.w+GAP,y:r.y+row.offsetTop-6,...size(list),anchor:r,gap:GAP,flipY:true},"right",V);
  list.style.left=`${q.x-r.x}px`;list.style.top=`${q.y-r.y}px`;list.dataset.side=q.side;

}

function openMenu() {
  closePanel();
  ui.menu = {expanded: false, busy: false};
  ui.client.screen().then((area) => { ui.screen = area; if (ui.menu) render(); }).catch(() => {});
  $("#context-card").dataset.view = "";
  ui.hover = false; ui.drag = null;
  render();
  ui.client.activate(true).catch(() => {});
  $("#context-card button")?.focus({preventScroll: true});
}

function closeMenu() {
  if (!ui.menu) return;
  ui.menu = null;
  render();
  ui.client.activate(false).catch(() => {});
}

async function selectAvatar() {
  const menu = ui.menu;
  if (!menu || menu.busy) return;
  menu.busy = true; menu.error = null; render();
  try {
    await ui.client.set_avatar();
    if (ui.menu === menu) closeMenu();
  } catch {
    if (ui.menu === menu) menu.error = tr("pet.error.backend");
  } finally {
    if (ui.menu === menu) { menu.busy = false; render(); }
  }
}

async function selectLanguage(tag) {
  const menu = ui.menu;
  if (!menu || menu.busy) return;
  menu.busy = true; menu.error = null; render();
  try {
    I18N.init(await ui.client.set_language(tag), {name: petName()});
    refreshLanguage();
    if (ui.menu === menu) closeMenu();
  } catch {
    menu.error = tr("pet.error.backend");
  } finally { menu.busy = false; render(); }
}

function refreshLanguage() {
  buildOrbs(); layout();
  // Static panel headings and placeholders are rebuilt in the new language.
  const draft = $("#say")?.value;
  $("#panel").dataset.kind = "";
  if (ui.panel) { ui.panel.error = null; ui.panel.status = null; }
  render();
  if (draft !== undefined && $("#say")) { $("#say").value = draft; autosize($("#say")); updateTalk(ui.panel); }
  if (ui.panel?.kind === "channel") { ui.panel.data = null; loadChannels(); }
  if (ui.client.kind === "demo") demoBar();
}

// ---- actions ------------------------------------------------------------------
async function act(id) {
  if (id === "talk") return openTalk(current() === "waiting_user");
  if (id === "channel") return openChannel();
  ui.hover = false;
  render();
  if (id === "inbox") return ui.client.open_mailbox(null).catch(() => {});
  if (id === "session") return ui.client.open_session().catch(() => {});
  if (id === "done") {
    const ask = ui.snap.ask;
    if (!ask) return;
    ui.celebrated.add(ask.req_id);
    try {
      const res = await ui.client.done(ask.req_id);
      if (res && res.delivered === false) ui.celebrated.delete(ask.req_id);
      else celebrate();
    } catch (e) { ui.celebrated.delete(ask.req_id); }
    poll();
  }
}

// The single "received" moment: green check ~3 s + the confirmation chime.
// While the pet is visible it is the only end that chimes (backend rule).
function celebrate() {
  ui.receivedUntil = Date.now() + M.RECEIVED_MS;
  if (ui.cfg.sound !== false) playConfirm();
  render();
  setTimeout(render, M.RECEIVED_MS + 30);
}

// ---- hover ----------------------------------------------------------------------
function setHover(on, immediate = false) {
  clearTimeout(ui.hideTimer);
  ui.hideTimer = 0;
  if (on) { if (!ui.hover) { ui.hover = true; render(); } return; }
  if (ui.forceHover) return;
  if (immediate) { if (ui.hover) { ui.hover = false; render(); } return; }
  ui.hideTimer = setTimeout(() => { ui.hideTimer = 0; if (ui.hover) { ui.hover = false; render(); } }, 70);
}

// ---- window region (click-through outside) ---------------------------------------
// A button and its label where they end up, taken from layout instead of the
// current transform: right while the button is still popping in, and with
// room for the hover growth (M.HOVER_SCALE_MAX) and the pop overshoot
// (M.POP_OVERSHOOT).
function orbExtent(el) {
  let x = 0, y = 0;
  const rig = $("#rig");
  for (let n = el; n && n !== rig; n = n.offsetParent) { x += n.offsetLeft; y += n.offsetTop; }
  const o = { x, y, w: el.offsetWidth, h: el.offsetHeight };
  const cx = o.x + o.w / 2, cy = o.y + o.h / 2;
  const sx = -M.POP_OVERSHOOT * (parseFloat(el.style.getPropertyValue("--dx")) || 0);
  const sy = -M.POP_OVERSHOOT * (parseFloat(el.style.getPropertyValue("--dy")) || 0);
  const reach = (r) => {
    const g = M.scaleAbout(r, cx, cy, M.HOVER_SCALE_MAX);
    return M.unionRect([g, { ...g, x: g.x + sx, y: g.y + sy }]);
  };
  let ox = 0, oy = 0;
  for (let n = rig; n; n = n.offsetParent) { ox += n.offsetLeft; oy += n.offsetTop; }
  const list = [M.inflate(reach(o), 12)];
  // Include the tooltip box in the window region while hovered, focused, or still fading out,
  // so fast pointer sweeps across orbs never slice a fading tooltip mid-animation.
  const tip = el.querySelector(".tip");
  if (tip && (el.matches(":hover, :focus-visible") || parseFloat(getComputedStyle(tip).opacity) > 0.01)) {
    const t = { x: o.x + tip.offsetLeft, y: o.y + tip.offsetTop, w: tip.offsetWidth, h: tip.offsetHeight };
    list.push(M.inflate(reach(t), 10));
  }
  return list.map((r) => M.fromRig(r, { x: ox, y: oy }));
}

function visibleRects() {
  const rects = [];
  const add = (el, pad) => {
    if (!el || el.hidden) return;
    const r = el.getBoundingClientRect();
    if (r.width && r.height) rects.push(M.inflate({ x: r.left, y: r.top, w: r.width, h: r.height }, pad * M.UI_SCALE));
  };
  // The character hit target and painted frame use the smaller real size.
  // Reserve hover spring growth about the unchanged bottom/center anchor;
  // measured frames also cover static-pack motion outside the hit target.
  const pet = $("#pet").getBoundingClientRect();
  rects.push(M.inflate(M.scaleAbout({ x: pet.left, y: pet.top, w: pet.width, h: pet.height },
    pet.left + pet.width / 2, pet.bottom - M.SPRITE_BOTTOM * M.SPRITE_SCALE * M.UI_SCALE,
    M.SPRITE_HOVER_SCALE_MAX), 6 * M.SPRITE_SCALE * M.UI_SCALE));
  add($("#body .frames"), 6 * M.SPRITE_SCALE);
  if ($("#stage").dataset.state === "working") {
    // Reserve the final pill width immediately, including the spring's
    // overshoot. Measuring its initial scale(.76) clips it during entry.
    const el = $(".busy"), rig = $("#rig");
    add(el, 20);  // also cover the stage's initial enter transform
    let x = 0, y = 0;
    for (let n = el; n && n !== rig; n = n.offsetParent) { x += n.offsetLeft; y += n.offsetTop; }
    const body = { x, y: y - el.offsetHeight / 2, w: el.offsetWidth, h: el.offsetHeight };
    rects.push(M.fromRig(M.inflate(M.scaleAbout(body, body.x + body.w / 2, body.y + body.h / 2, 1.06), 20),
      { x: rig.offsetLeft, y: rig.offsetTop }));
  }
  if ($("#stage").dataset.state === "waiting_user") add($(".alert"), 8);
  // the green check sits outside the pet box: without this it is clipped
  if ($("#stage").dataset.state === "received") { add($(".check"), 12); add($(".check-label"), 8); }
  add($("#bubble"), 22);
  add($("#panel"), 26);
  add($("#context-card"), 26);
  add($("#language-list"), 26);
  if ($("#stage").classList.contains("hover")) add($("#zone"), 0);
  for (const el of document.querySelectorAll(".orb.on")) rects.push(...orbExtent(el));
  return M.regionRects(rects, win());
}

// Grow at once, shrink only after fades finish (the region also clips paint).
function syncRegion() {
  if (!ui.client) return;
  placeTalk(); placeBusy(); placeBubble();
  const send = (rects) => {
    const key = M.regionKey(rects);
    if (key === ui.region.key) return;
    ui.region.key = key;
    ui.client.region(rects).catch(() => {});
  };
  const t = Date.now();
  const now = visibleRects();
  const recent = (ui.region.recent || []).filter((entry) => entry.until > t);
  if (now.length) recent.push({ until: t + 320, rects: now });
  ui.region.recent = recent;
  const dedup = new Map();
  for (const r of [...recent.flatMap((entry) => entry.rects), ...now]) {
    dedup.set(`${r.x},${r.y},${r.w},${r.h}`, r);
  }
  let merged = M.regionRects([...dedup.values()], win());
  if (merged.length > 28) {
    const fading = recent.flatMap((entry) => entry.rects);
    merged = M.regionRects([...now, ...(fading.length ? [M.unionRect(fading)] : [])], win());
  }
  send(merged);
  clearTimeout(ui.region.timer);
  ui.region.timer = setTimeout(() => {
    const cur = visibleRects();
    ui.region.recent = cur.length ? [{ until: Date.now() + 320, rects: cur }] : [];
    send(cur);
  }, 320);
}

// ---- polling ---------------------------------------------------------------------
async function poll() {
  let next;
  try { next = await ui.client.state(); } catch (e) { return; }
  try { ui.screen = await ui.client.screen(); } catch { /* retain last known work area */ }
  const ans = M.detectAnswer(ui.snap, next);
  if (!next.brief) { ui.brief = null; ui.briefUntil = 0; }
  if (next.brief && next.brief_id && next.brief_id !== ui.lastBriefId) {
    ui.lastBriefId = next.brief_id;
    ui.brief = next.brief;
    ui.briefUntil = phaseUntil(next.brief_at, Number(ui.cfg.bubble_ms) || 8000);
    setTimeout(render, Math.max(0, ui.briefUntil - Date.now()) + 30);
  }
  ui.snap = next || { state: "idle" };
  if (ans && !ui.celebrated.has(ans.req_id)) { ui.celebrated.add(ans.req_id); celebrate(); return; }
  // the ask this composer answers is gone (answered on the card): close it
  if (ui.panel && ui.panel.kind === "talk" && ui.panel.replyTo && !ui.panel.sent && !(next.ask && next.ask.req_id === ui.panel.replyTo)) closePanel();
  render();
}

// ---- enter / leave (placeholder fades until the enhancement pack exists) -----------
async function enter() {
  const stage = $("#stage");
  stage.classList.remove("leaving");
  stage.classList.add("entering");
  void stage.offsetWidth; // commit the start frame, then animate in
  setTimeout(() => stage.classList.remove("entering"), 20);
  await sprite.once("enter");
  render();
}

async function leave() {
  if (ui.leaving) return;
  ui.leaving = true;
  closeMenu();
  closePanel();
  ui.hover = false;
  render();
  const played = await sprite.once("exit");
  $("#stage").classList.add("leaving");
  await new Promise((r) => setTimeout(r, played ? 120 : 260));
  try { await ui.client.hide(); } catch (e) { /* stays visible */ }
  ui.leaving = false;
  if (ui.client.kind === "demo") setTimeout(enter, 900);
}

// ---- demo controls ----------------------------------------------------------------
function demoBar() {
  const d = ui.client.demo;
  const bar = $("#demo-bar");
  const chip = (label, fn) => h("button", { type: "button", onclick: () => { fn(); poll(); } }, label);
  bar.replaceChildren(
    h("span", { class: "tag" }, tr("pet.demo")),
    chip(tr("pet.demo_idle"), () => d.idle()),
    chip(tr("pet.demo_work"), () => d.work([tr("pet.demo_work_text"), tr("pet.demo_step2"), tr("pet.demo_step3")][Math.floor(Math.random() * 3)])),
    chip(tr("pet.demo_input"), () => d.ask("input")),
    chip(tr("pet.demo_choice"), () => d.ask("info")),
    chip(tr("pet.demo_answer"), () => d.answer("popup")),
    chip(tr("pet.demo_hover"), () => { ui.forceHover = !ui.forceHover; ui.hover = ui.forceHover; render(); }),
  );
  bar.hidden = params.has("clean");
  document.body.classList.add("demo");
}

// ---- boot -------------------------------------------------------------------------
async function main() {
  ui.client = await connect();
  try { ui.cfg = { ...ui.cfg, ...((await ui.client.config()) || {}) }; } catch (e) { /* defaults */ }
  globalThis.musePetName = ui.cfg.display_name || "Muse Pet";
  I18N.init(await ui.client.i18n(), {name: globalThis.musePetName});
  ui.languages = await ui.client.languages();
  document.title = globalThis.musePetName;
  buildOrbs();
  layout();
  // Size changes also come from status text, language changes and reply quotes.
  const resizeObserver=new ResizeObserver(() => syncRegion());
  resizeObserver.observe($("#panel"));
  resizeObserver.observe($(".busy"));
  resizeObserver.observe($("#bubble"));
  window.addEventListener("resize", () => { layout(); render(); });
  const pet = $("#pet");
  const inHoverArea = (node) => !!(node && node.closest && (node.closest("#pet") || node.closest("#zone") || node.closest(".orb.on")));
  pet.addEventListener("pointerenter", () => setHover(true));
  pet.addEventListener("pointerleave", (e) => { if (!inHoverArea(e.relatedTarget)) setHover(false); });
  $("#zone").addEventListener("pointerenter", () => { if (ui.hover) setHover(true); });
  $("#zone").addEventListener("pointerleave", (e) => { if (!inHoverArea(e.relatedTarget)) setHover(false); });
  $("#orbs").addEventListener("pointerover", (e) => { if (e.target.closest(".orb.on")) setHover(true); });
  $("#orbs").addEventListener("pointerout", (e) => {
    if (e.target.closest(".orb.on") && !inHoverArea(e.relatedTarget)) setHover(false);
  });
  document.addEventListener("pointerleave", () => setHover(false, true));
  // Drag starts on the smaller character hit target. The 4 px screen-space
  // threshold stays independent of both sprite and UI scale.
  // Drag to move: the backend moves the window natively once the pointer has
  // travelled a few pixels with the button down; a drag never counts as a click.
  pet.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    ui.drag = { x: e.screenX, y: e.screenY, started: false };
  });
  pet.addEventListener("pointermove", (e) => {
    const d = ui.drag;
    if (!d || d.started || !(e.buttons & 1)) return;
    if (Math.hypot(e.screenX - d.x, e.screenY - d.y) < M.DRAG_START_PX) return;
    d.started = true;
    ui.client.drag().catch(() => {});
  });
  pet.addEventListener("click", (e) => {
    const dragged = ui.drag && ui.drag.started;
    ui.drag = null;
    if (dragged) { e.preventDefault(); return; }
    if (ui.menu) { closeMenu(); return; }
    ui.panel ? closePanel() : act("session");
  });
  document.addEventListener("contextmenu", (e) => { e.preventDefault(); if (e.target.closest("#pet")) openMenu(); });
  document.addEventListener("pointerdown", e => { if (!e.target.closest("#context-card, #pet")) closeMenu(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeMenu(); closePanel(); }
    if (ui.menu && ["ArrowDown", "ArrowUp"].includes(e.key)) {
      const items = [...$("#context-card").querySelectorAll("button:not(:disabled)")].filter(el => !el.closest("[hidden]"));
      const i = items.indexOf(document.activeElement);
      e.preventDefault(); items[(i + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
    }
  });
  window.addEventListener("blur", closeMenu);
  // backend -> UI hooks (evaluate_js): shown after tray restore, poke on change
  window.MusePet = {
    avail: (area) => { ui.screen=area; render(); },
    shown: () => enter(), poke: () => poll(), talk: () => openTalk(current() === "waiting_user"),
    // backend cursor tracking: pointerleave never fires when the cursor exits
    // the window region, so the backend reports the transition itself
    pointer: (inside) => { if (!inside) setHover(false, true); },
  };

  I18N.follow(ui.client, refreshLanguage, {name: petName()});
  if (ui.cfg.display_name) pet.setAttribute("aria-label", tr("pet.accessible", {name: ui.cfg.display_name}));
  await sprite.load(ui.cfg.pack || "jolly");
  if (ui.client.kind === "demo") demoBar();
  await poll();
  const panel = params.get("panel");
  if (panel === "talk") openTalk(current() === "waiting_user");
  if (panel === "channel") openChannel();
  if (ui.client.kind === "demo" && params.has("received")) { ui.receivedUntil = Date.now() + 60000; }
  enter();
  setInterval(poll, Math.max(500, Number(ui.cfg.poll_ms) || 1000));
}

main();

function petName() { return globalThis.musePetName || "Muse Pet"; }
