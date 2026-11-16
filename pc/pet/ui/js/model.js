import { I18N, t as tr } from "../../../i18n/i18n.js";
import layout from "../layout.json" with { type: "json" };
// model.js - pure rules of the desktop pet (no DOM, no I/O).
// Tested by tests/pet-ui/model.test.mjs. Contract and backend split:
// docs/architecture.en.md section 8.

// ---- states ---------------------------------------------------------------
// The backend reports idle | working | waiting_user. "received" is the 3 s
// green-check moment after an answer; the frontend owns that timer.
export const STATES = ["idle", "working", "waiting_user", "received"];
export const RECEIVED_MS = 3000;

export function displayState(snapshot, receivedUntil, now) {
  if (receivedUntil && now < receivedUntil) return "received";
  const s = snapshot && snapshot.state;
  return s === "working" || s === "waiting_user" ? s : "idle";
}

// An ask disappeared AND the backend says it was answered (anywhere): play the
// green check. An ask that vanishes without an answer (expired, replaced by a
// newer card) must not look like success.
export function detectAnswer(prev, next) {
  const was = prev && prev.ask;
  if (!was || !was.req_id) return null;
  const still = next && next.ask && next.ask.req_id === was.req_id;
  if (still) return null;
  const a = next && next.last_answer;
  return a && a.req_id === was.req_id ? { req_id: a.req_id, by: a.by || "other" } : null;
}

// UI design pixels map 1:1 to window logical pixels. Only the character
// shrinks; neither UI geometry nor the native window uses SPRITE_SCALE.
// Python's geometry.py reads the same data for the native window and bounds.
export const UI_SCALE = layout.ui_scale;
export const SPRITE_SCALE = layout.sprite_scale;
export const PET = { w: 124, h: 134, right: 18, bottom: 14 };
export const SPRITE_BOTTOM = 6;
export const ORB = 44;
export const ARC_RADIUS = 100;  // smallest radius at which orbs 28 degrees apart do not touch

// Hover actions sit on an arc around the pet's upper-left (the pet lives in
// the bottom-right corner, so everything opens towards the desktop). The
// lowest slot must stay above the window's bottom edge (test).
export const ACTIONS = [
  { id: "channel", get label() { return tr("pet.channels"); }, angle: 90 },
  { id: "inbox", get label() { return tr("pet.inbox"); }, angle: 118 },
  { id: "session", get label() { return tr("pet.session"); }, angle: 146 },
  { id: "talk", get label() { return tr("pet.talk", { name: petName() }); }, angle: 174, primary: true },
];
export const DONE = { id: "done", get label() { return tr("pet.done"); }, angle: 202 };
// The backend's window: transparent, pinned to the work area's bottom-right.
export const SPILL = layout.spill || { right: 0, bottom: 0 };
export const CHAR_RECT = layout.char_rect;
export const WINDOW = { w: Math.ceil((layout.window.width + (SPILL.left || 0) + SPILL.right) * UI_SCALE),
  h: Math.ceil((layout.window.height + (SPILL.top || 0) + SPILL.bottom) * UI_SCALE) };
// Pointer travel before a press becomes a drag: a property of the hand, not
// of the art, so it does not scale (Windows' own drag threshold is 4 px).
export const DRAG_START_PX = 4;
export function fromRig(r, origin) {
  return { x: origin.x + r.x * UI_SCALE, y: origin.y + r.y * UI_SCALE,
    w: r.w * UI_SCALE, h: r.h * UI_SCALE };
}
// A label sits left of its button; on buttons near the top of the arc it would
// land on the next button, so those labels sit above instead.
export const tipAbove = (angleDeg) => Math.abs(angleDeg - 90) < 15;

// Everything is placed relative to the pet box ("rig"), which CSS pins to the
// window's bottom-right corner; coordinates below are rig-relative.
export const PET_BOX = { x: 0, y: 0, w: PET.w, h: PET.h };
// Preserve the frames' CSS bottom/center anchor (the body ends 6 px above
// the rig bottom), including the shadow and the character's hit target.
export const SPRITE_ANCHOR = { x: PET.w / 2, y: PET.h - SPRITE_BOTTOM };
export const SPRITE_BOX = scaleAbout(PET_BOX, SPRITE_ANCHOR.x, SPRITE_ANCHOR.y, SPRITE_SCALE);
// The arc and the bubble follow the drawn character, not the full pet box.
// How far the arc centre drops below the sprite centre so the lowest hover orb
// (talk, last on the arc) ends level with the character's bottom.
const ARC_DROP = SPRITE_BOX.h / 2 - ORB / 2 + ARC_RADIUS * Math.sin((ACTIONS[3].angle * Math.PI) / 180);
export const PET_CENTER = { x: SPRITE_BOX.x + SPRITE_BOX.w / 2,
  y: SPRITE_BOX.y + SPRITE_BOX.h / 2 + ARC_DROP * 0.7 };  // 0.7 = 30% less drop (user, 2026-11-14)
export const SPRITE_HOVER_SCALE_MAX = 1.06; // 1.035 hover + --spring overshoot
export function spriteFit(frame) {
  return Math.min(PET.w / frame.width, PET.h / frame.height) * SPRITE_SCALE;
}

export function slotRect(angleDeg, size = ORB) {
  const c = PET_CENTER;
  const a = (angleDeg * Math.PI) / 180;
  return {
    x: Math.round(c.x + ARC_RADIUS * Math.cos(a) - size / 2),
    y: Math.round(c.y - ARC_RADIUS * Math.sin(a) - size / 2),
    w: size, h: size,
  };
}

// One pure placement rule for every card, pill, menu and flyout. Rects may
// supply an anchor and gap for side flipping. Oversize content is constrained
// by its caller before measuring; fit also reports a deterministic fallback.
export function fit(rect, preferredSide, avail) {
  const {w, h} = rect, a = rect.anchor;
  const other = preferredSide === "right" ? "left" : "right";
  const candidate = side => side === preferredSide || !a ? rect.x
    : side === "right" ? a.x + a.w + (rect.gap || 0) : a.x - (rect.gap || 0) - w;
  let side = preferredSide, x = candidate(side), y = rect.y;
  const fitsX = n => n >= avail.x && n + w <= avail.x + avail.w;
  if (!fitsX(x) && fitsX(candidate(other))) { side = other; x = candidate(other); }
  if (rect.flipY && a && y + h > avail.y + avail.h && a.y - (rect.gap || 0) - h >= avail.y)
    y = a.y - (rect.gap || 0) - h;
  return {x: Math.max(avail.x, Math.min(x, avail.x + avail.w - w)),
    y: Math.max(avail.y, Math.min(y, avail.y + avail.h - h)), w, h, side};
}

export function contains(avail, r) {
  const eps = .01;
  return r.x >= avail.x-eps && r.y >= avail.y-eps &&
    r.x+r.w <= avail.x+avail.w+eps && r.y+r.h <= avail.y+avail.h+eps;
}
export function mirrorSlot(angle, sx = 1, sy = 1) {
  const r = slotRect(angle), c = PET_CENTER;
  return {...r, x: sx === 1 ? r.x : 2*c.x-r.x-r.w,
    y: sy === 1 ? r.y : 2*c.y-r.y-r.h};
}
// Same hover/pop/shadow envelope used for the native region. A relocated
// upper Done travels farther from the sprite, so its pop may exceed 22px.
export function hoverExtent(rect, orb, origin, pad = 12) {
  const cx = orb.x + orb.w/2, cy = orb.y + orb.h/2;
  const sx = POP_OVERSHOOT * (cx-origin.x-SPRITE_BOX.x-SPRITE_BOX.w/2);
  const sy = POP_OVERSHOOT * (cy-origin.y-SPRITE_BOX.y-SPRITE_BOX.h/2);
  const grown = scaleAbout(rect,cx,cy,HOVER_SCALE_MAX);
  return inflate(unionRect([grown,{...grown,x:grown.x+sx,y:grown.y+sy}]),pad);
}
// Default upper-left -> upper-right -> lower-left -> lower-right.
// Keep the complete arc shape if a work-area edge requires a small correction.
export function orientArc(origin, avail) {
  const tries = [[1,1],[-1,1],[1,-1],[-1,-1]].map(([sx,sy]) => {
    const rects = Object.fromEntries(ACTIONS.map(a => [a.id, fromRig(mirrorSlot(a.angle,sx,sy),origin)]));
    const bounds = unionRect(Object.values(rects).map(r=>hoverExtent(r,r,origin)));
    const overflow = Math.max(0,avail.x-bounds.x)+Math.max(0,avail.y-bounds.y)+
      Math.max(0,bounds.x+bounds.w-avail.x-avail.w)+Math.max(0,bounds.y+bounds.h-avail.y-avail.h);
    return {sx,sy,rects,bounds,overflow};
  });
  const arc = tries.find(a => contains(avail,a.bounds)) || tries.reduce((a,b)=>a.overflow<=b.overflow?a:b);
  let dx=0, dy=0;
  // Reserve the waiting pulse/shadow bottom room on talk too so talk keeps
  // the exact same position in normal hover and waiting_user, and done sits
  // level beside it without being thrown to the top edge.
  const reserveFor = r=>unionRect([inflate(r,22),hoverExtent(r,r,origin)]);
  for(let i=0;i<6;i++) {
    arc.bounds=unionRect([...Object.values(arc.rects).map(r=>hoverExtent(r,r,origin)), reserveFor(arc.rects.talk)]);
    if(contains(avail,arc.bounds)) break;
    const fixed=fit(arc.bounds,arc.sx===1?"left":"right",avail);
    const mx=fixed.x-arc.bounds.x,my=fixed.y-arc.bounds.y;
    dx+=mx;dy+=my;
    for(const r of Object.values(arc.rects)){r.x+=mx;r.y+=my;}
  }
  const talk = arc.rects.talk, channel = arc.rects.channel;
  const candidates = [
    { x: talk.x - arc.sx * (ORB + 12), y: talk.y, w: ORB, h: ORB },
    { x: talk.x, y: talk.y + arc.sy * (ORB + 12), w: ORB, h: ORB },
    { x: channel.x, y: channel.y - arc.sy * (ORB + 12), w: ORB, h: ORB },
  ];
  let done = candidates.find((c) => contains(avail, reserveFor(c))) || candidates[0];
  const relocated = done !== candidates[0];
  for(let i=0;i<6;i++) {
    const reserve=reserveFor(done);
    if(contains(avail,reserve)) break;
    const fixed=fit(reserve,"left",avail);
    done.x+=fixed.x-reserve.x;done.y+=fixed.y-reserve.y;
  }
  arc.rects.done=done;
  return {...arc, relocated};
}

function overlapArea(a, b) {
  const ox = Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x));
  const oy = Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
  return ox * oy;
}

// Place the ask bubble cleanly around the pet, its alert mark and every
// currently visible orb without overlapping any of them.
export function bubbleRect(w, h, origin, avail, arc, visibleOrbIds = [], hasAlert = false) {
  const pet = fromRig(SPRITE_BOX, origin);
  const alert = hasAlert ? { x: pet.x + pet.w - 18, y: pet.y - 2, w: 22, h: 22 } : null;
  const petZone = inflate(unionRect([pet, alert].filter(Boolean)), 12);
  const orbZones = [...visibleOrbIds]
    .map((id) => arc && arc.rects && arc.rects[id])
    .filter(Boolean)
    .map((r) => inflate(r, 10));
  const obstacles = [petZone, ...orbZones];
  const allObs = unionRect(obstacles);
  const sx = (arc && arc.sx) || 1;
  const sideY = Math.max(avail.y, Math.min(pet.y + pet.h - h, pet.y));
  const raw = [
    { x: sx === 1 ? pet.x + pet.w - w : pet.x, y: allObs.y - h, w, h },
    { x: sx === 1 ? allObs.x - w : allObs.x + allObs.w, y: sideY, w, h },
    { x: sx === 1 ? petZone.x + petZone.w : petZone.x - w, y: sideY, w, h },
    { x: sx === 1 ? pet.x + pet.w - w : pet.x, y: allObs.y + allObs.h, w, h },
  ];
  let best = null, bestOverlap = Infinity;
  for (const c of raw) {
    const fitted = fit(c, sx === 1 ? "left" : "right", avail);
    const area = obstacles.reduce((sum, ob) => sum + overlapArea(fitted, ob), 0);
    if (area < bestOverlap - 0.01) {
      best = fitted;
      bestOverlap = area;
      if (area === 0) break;
    }
  }
  return best || fit(raw[0], "left", avail);
}

// The measured box is the capsule BODY only. The capsule hugs the character
// from below, centered under it (4 px gap); when the window leaves no room
// below or a downward arc blocks below, it chooses a clean spot beside or
// above without overlapping the character or any visible orb.
export function busyRect(w, h, origin, avail, arc) {
  const pet = fromRig(SPRITE_BOX, origin), gap = 4;
  const orbs = arc ? Object.values(arc.rects || {}).map(q => inflate(q, 10)) : [];
  const cx = pet.x + (pet.w - w) / 2;
  const sideY = origin.y + Math.round(SPRITE_BOX.y) + 20 - h / 2;
  const topObs = orbs.length ? Math.min(...orbs.map(q => q.y)) : pet.y;
  const botObs = orbs.length ? Math.max(...orbs.map(q => q.y + q.h)) : pet.y + pet.h;
  const leftObs = orbs.length ? Math.min(...orbs.map(q => q.x)) : pet.x;
  const rightObs = orbs.length ? Math.max(...orbs.map(q => q.x + q.w)) : pet.x + pet.w;
  const sideFirst = (!arc || arc.sx === 1)
    ? [{ x: pet.x + pet.w + 8, y: sideY, w, h, side: "right" }, { x: pet.x - 8 - w, y: sideY, w, h, side: "left" }]
    : [{ x: pet.x - 8 - w, y: sideY, w, h, side: "left" }, { x: pet.x + pet.w + 8, y: sideY, w, h, side: "right" }];
  const sideAlt = (!arc || arc.sx === 1)
    ? [
        ...orbs.flatMap(q => [{ x: pet.x - 8 - w, y: q.y - h, w, h, side: "left" }, { x: pet.x - 8 - w, y: q.y + q.h, w, h, side: "left" }]),
        { x: leftObs - w, y: sideY, w, h, side: "left" },
        { x: cx, y: Math.min(pet.y - gap - h, topObs - h), w, h, side: "top" },
        { x: cx, y: Math.max(pet.y + pet.h + gap, botObs), w, h, side: "bottom" },
        ...orbs.flatMap(q => [{ x: pet.x + pet.w + 8, y: q.y - h, w, h, side: "right" }, { x: pet.x + pet.w + 8, y: q.y + q.h, w, h, side: "right" }]),
      ]
    : [
        ...orbs.flatMap(q => [{ x: pet.x + pet.w + 8, y: q.y - h, w, h, side: "right" }, { x: pet.x + pet.w + 8, y: q.y + q.h, w, h, side: "right" }]),
        { x: rightObs, y: sideY, w, h, side: "right" },
        { x: cx, y: Math.min(pet.y - gap - h, topObs - h), w, h, side: "top" },
        { x: cx, y: Math.max(pet.y + pet.h + gap, botObs), w, h, side: "bottom" },
        ...orbs.flatMap(q => [{ x: pet.x - 8 - w, y: q.y - h, w, h, side: "left" }, { x: pet.x - 8 - w, y: q.y + q.h, w, h, side: "left" }]),
      ];
  const raw = [
    { x: cx, y: pet.y + pet.h + gap, w, h, side: "bottom" },
    ...sideFirst,
    { x: cx, y: pet.y - gap - h, w, h, side: "top" },
    ...sideAlt,
  ];
  let best = null, bestScore = Infinity;
  for (const c of raw) {
    const fitted = { ...fit(c, "left", avail), side: c.side };
    const petOv = overlapArea(fitted, pet);
    const orbOv = orbs.reduce((sum, ob) => sum + overlapArea(fitted, ob), 0);
    const dist = Math.hypot((fitted.x + w / 2) - (pet.x + pet.w / 2), (fitted.y + h / 2) - (pet.y + pet.h / 2));
    const score = petOv * 1e6 + orbOv * 1e3 + dist * 0.01;
    if (score < bestScore - 0.01) {
      best = fitted;
      bestScore = score;
      if (petOv === 0 && orbOv === 0 && c === raw[0]) break;
    }
  }
  return best || { ...fit(raw[0], "left", avail), side: "bottom" };
}

// Which buttons show. Hover reveals the four actions; nothing shows when the
// pointer is elsewhere. While Muse waits for the user, the check and the talk
// button stay up without hover (the two "sync icons" of the spec) — except
// that a pure input ask is answered by typing, so it gets no check. An open
// panel replaces all of them.
export function visibleControls({ state, hover, panel, ask }) {
  const out = new Set();
  if (panel) return out;
  if (state === "waiting_user") {
    if (!ask || ask.mode !== "input") out.add("done");
    out.add("talk");
  }
  if (hover && state !== "received") for (const a of ACTIONS) out.add(a.id);
  return out;
}

export function unionRect(rects) {
  const list = rects.filter(Boolean);
  if (!list.length) return null;
  const x = Math.min(...list.map((r) => r.x));
  const y = Math.min(...list.map((r) => r.y));
  const r = Math.max(...list.map((q) => q.x + q.w));
  const b = Math.max(...list.map((q) => q.y + q.h));
  return { x, y, w: r - x, h: b - y };
}

export function inflate(r, d) {
  return r && { x: r.x - d, y: r.y - d, w: r.w + 2 * d, h: r.h + 2 * d };
}
// A hovered button grows to scale(1.1) about its own center (pet.css
// .orb:hover), and the --pop curve briefly overshoots that. Its label is a
// child, so it grows and swings outward too: a long label's far edge moves
// several pixels. The window region is measured at rest, so it reserves
// this much growth; otherwise the label's rounded end is clipped flat until
// the next re-measure.
// Include the waiting pulse (1.08) multiplied by hover/spring growth.
export const HOVER_SCALE_MAX = 1.22;
// The same curve makes a button popping in travel past its slot, away from
// the pet, by up to ~10% of its travel (--dx/--dy); a label shown during the
// pop moves with it.
export const POP_OVERSHOOT = 0.1;
export function scaleAbout(r, cx, cy, s) {
  return r && { x: cx + (r.x - cx) * s, y: cy + (r.y - cy) * s, w: r.w * s, h: r.h * s };
}

// The window is click-through outside these rectangles (backend applies them
// as the window region). Integers, clipped to the window, stable order, so
// an unchanged layout yields the same key and no backend call.
export function regionRects(rects, win) {
  return rects.filter(Boolean).map((r) => {
    const x = Math.max(0, Math.floor(r.x));
    const y = Math.max(0, Math.floor(r.y));
    const w = Math.min(win.w, Math.ceil(r.x + r.w)) - x;
    const h = Math.min(win.h, Math.ceil(r.y + r.h)) - y;
    return { x, y, w, h };
  }).filter((r) => r.w > 0 && r.h > 0)
    .sort((a, b) => a.y - b.y || a.x - b.x || a.w - b.w || a.h - b.h);
}

export const regionKey = (rects) => rects.map((r) => `${r.x},${r.y},${r.w},${r.h}`).join(";");

// ---- text -----------------------------------------------------------------
export function clip(text, max) {
  const s = String(text == null ? "" : text).replace(/\s+/g, " ").trim();
  return s.length > max ? s.slice(0, max - 1).trimEnd() + "…" : s;
}

export function askTitle(ask) {
  const mode = ask && ask.mode;
  if (mode === "choice") return tr("pet.ask_choice");
  if (mode === "input") return tr("pet.ask_input");
  return tr("pet.ask_info");
}

export const SAY_MAX = 2000;

export function checkSay(text) {
  const textValue = String(text == null ? "" : text).trim();
  if (!textValue) return { ok: false, get error() { return tr("pet.empty_input"); } };
  if (textValue.length > SAY_MAX) return { ok: false, error: tr("pet.max_input", { n: SAY_MAX }) };
  return { ok: true, text: textValue };
}

// Reply to the waiting ask only when the composer was opened for it and the
// same ask is still up; otherwise the text goes to Muse as a new message.
export function sayTarget(composer, snapshot) {
  const ask = snapshot && snapshot.ask;
  return composer && composer.replyTo && ask && ask.req_id === composer.replyTo ? ask.req_id : null;
}

export function sayResultText(res) {
  if (res && res.via === "wait") return tr("pet.sent_wait");
  return tr("pet.sent");
}

// ---- channels ---------------------------------------------------------------
export const LEVELS = { get ok() { return tr("pet.level_ok"); }, get warn() { return tr("pet.level_warn"); }, get down() { return tr("pet.level_down"); }, get unknown() { return tr("pet.level_unknown"); } };

export function channelSummary(list) {
  const levels = (list || []).map((c) => c.level);
  if (levels.includes("down")) return "down";
  if (levels.includes("warn")) return "warn";
  if (levels.length && levels.every((l) => l === "ok")) return "ok";
  return "unknown";
}

// ---- pet packs ----------------------------------------------------------------
// Codex Pets atlas (8 columns x 9 rows, 192x208 cells). Row order and frame
// counts of the v1 atlas; a pack may override any track in pet.json
// "animations" (Codex style: frame numbers + fps, or {spriteIndex, durationMs}).
export const CODEX_ROWS = [
  ["idle", 6], ["running_right", 8], ["running_left", 8], ["waving", 4], ["jumping", 5],
  ["failed", 8], ["waiting", 6], ["running", 6], ["review", 6],
];
const ROW_FPS = { idle: 5, waiting: 6, waving: 6 };
// States that play once and hold the last frame until the state changes.
export const HOLD_STATES = new Set();

// Pet state -> animation names, first one the pack has wins. "enter"/"exit"
// are the enhancement-pack slots (door in / door out); without them the UI
// fades (task book: placeholder until the user picks the animation route).
export const TRACKS = {
  idle: ["idle"],
  working: ["running", "review", "idle"],
  waiting_user: ["waiting", "idle"],
  received: ["review", "waving", "idle"],
  enter: ["enter"],
  exit: ["exit", "waving"],
};

const norm = (name) => String(name).replace(/-/g, "_");

export function packTracks(pack) {
  const tracks = {};
  if (!pack || pack.kind === "static") return tracks;
  const cols = (pack.frame && pack.frame.columns) || 8;
  CODEX_ROWS.forEach(([name, count], row) => {
    const fps = ROW_FPS[name] || 8;
    tracks[name] = { loop: true, frames: Array.from({ length: count }, (_, i) => ({ index: row * cols + i, ms: Math.round(1000 / fps) })) };
  });
  for (const [rawName, t] of Object.entries(pack.animations || {})) {
    const name = norm(rawName);
    const fps = Number(t.fps) || 8;
    const frames = (t.frames || []).map((f) => typeof f === "number"
      ? { index: f, ms: Math.round(1000 / fps) }
      : { index: Number(f.spriteIndex), ms: Math.max(16, Number(f.durationMs) || Math.round(1000 / fps)) })
      .filter((f) => Number.isInteger(f.index) && f.index >= 0);
    if (frames.length) tracks[name] = { loop: t.loop !== false && t.loop_start !== null && t.loopStart !== null, frames };
  }
  return tracks;
}

export function pickTrack(tracks, state) {
  for (const name of TRACKS[state] || ["idle"]) if (tracks[name]) return name;
  return null;
}

export function framePosition(index, frame) {
  const cols = frame.columns || 8;
  return { x: -(index % cols) * frame.width, y: -Math.floor(index / cols) * frame.height };
}

// A pack is data from disk: only same-directory relative file names.
export function safeAsset(name) {
  const s = String(name || "");
  return /^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.(png|webp|svg|gif)$/i.test(s) ? s : null;
}

function petName() { return globalThis.musePetName || "Muse Pet"; }
