// Desktop pet UI rules + the API contract on the demo backend.
//   node --test tests/pet-ui/model.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import * as M from "../../pc/pet/ui/js/model.js";
import { METHODS, DemoTransport } from "../../pc/pet/ui/js/api.js";
import { CHIME } from "../../pc/pet/ui/js/sound.js";
import { Sprite } from "../../pc/pet/ui/js/sprite.js";
import { I18N } from "../../pc/i18n/i18n.js";

I18N.init({tag: "zh-Hans", messages: JSON.parse(readFileSync(new URL("../../pc/i18n/zh-Hans.json", import.meta.url), "utf8"))});

const C = JSON.parse(readFileSync(new URL("../../pc/pet/ui/fixtures/contract.json", import.meta.url), "utf8"));
const keys = (o) => Object.keys(o).sort();

test("UI stays at design size while only the character shrinks about its frame anchor", () => {
  assert.equal(M.UI_SCALE, 1);
  assert.equal(M.SPRITE_SCALE, 0.7);
  assert.deepEqual(M.WINDOW, { w: 1140, h: 680 });
  assert.deepEqual(M.fromRig({ x: -30, y: 20, w: 300, h: 44 }, { x: 198, y: 292 }),
    { x: 168, y: 312, w: 300, h: 44 });
  assert.equal(M.ORB, 44);
  const p = M.SPRITE_BOX;
  assert.ok(Math.abs(p.w - 86.8) < 1e-10);
  assert.ok(Math.abs(p.h - 93.8) < 1e-10);
  assert.equal(p.x + p.w / 2, M.SPRITE_ANCHOR.x);
  assert.ok(Math.abs(p.y + p.h - M.SPRITE_BOTTOM * M.SPRITE_SCALE - M.SPRITE_ANCHOR.y) < 1e-10);
  const grown = M.scaleAbout(p, M.SPRITE_ANCHOR.x, M.SPRITE_ANCHOR.y, M.SPRITE_HOVER_SCALE_MAX);
  assert.ok(grown.w > p.w && grown.w < M.PET.w);
  assert.equal(grown.x + grown.w / 2, M.SPRITE_ANCHOR.x);
  assert.equal(M.DRAG_START_PX, 4);
});

test("sprite loader fits atlas and static packs at 70 percent without changing tracks", async () => {
  const originalFetch = globalThis.fetch;
  try {
    for (const pack of [
      { id: "test", spritesheetPath: "sprite.png", frame: { width: 192, height: 208, columns: 8, rows: 9 } },
      { id: "test", kind: "static", image: "sprite.png", size: { width: 300, height: 100 } },
    ]) {
      globalThis.fetch = async () => ({ ok: true, json: async () => pack });
      const frames = { style: {} };
      const el = { querySelector: () => frames, classList: { toggle() {} }, dataset: {} };
      const sprite = new Sprite(el);
      await sprite.load("test");
      const f = sprite.frame;
      const expected = Math.min(M.PET.w / f.width, M.PET.h / f.height) * 0.7;
      assert.equal(frames.style.transform, `translate(-50%, 0) scale(${expected})`);
      assert.ok(f.width * expected <= M.SPRITE_BOX.w + 1e-10);
      assert.ok(f.height * expected <= M.SPRITE_BOX.h + 1e-10);
      assert.deepEqual(sprite.tracks, M.packTracks(pack));
      assert.equal(frames.style.backgroundSize, pack.kind === "static" ? "contain" : "1536px 1872px");
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("four visible states; received is a 3 s local moment", () => {
  assert.equal(M.displayState({ state: "working" }, 0, 10), "working");
  assert.equal(M.displayState({ state: "waiting_user" }, 0, 10), "waiting_user");
  assert.equal(M.displayState({ state: "bogus" }, 0, 10), "idle");
  assert.equal(M.displayState(null, 0, 10), "idle");
  assert.equal(M.displayState({ state: "working" }, 5000, 4999), "received");
  assert.equal(M.displayState({ state: "working" }, 5000, 5000), "working");
  assert.equal(M.RECEIVED_MS, 3000);
});

test("green check only when the waiting ask was really answered", () => {
  const ask = { req_id: "r1", mode: "input", summary: "x" };
  const prev = { state: "waiting_user", ask };
  assert.deepEqual(M.detectAnswer(prev, { state: "working", ask: null, last_answer: { req_id: "r1", by: "popup" } }), { req_id: "r1", by: "popup" });
  assert.equal(M.detectAnswer(prev, { state: "waiting_user", ask, last_answer: null }), null);         // still waiting
  assert.equal(M.detectAnswer(prev, { state: "idle", ask: null, last_answer: null }), null);          // expired: no fake success
  assert.equal(M.detectAnswer(prev, { state: "idle", ask: null, last_answer: { req_id: "r0" } }), null); // an older answer
  assert.equal(M.detectAnswer({ state: "idle" }, { state: "working", last_answer: { req_id: "r1" } }), null);
});

test("buttons: hover-only, except check + talk while Muse waits; a panel hides all", () => {
  const ids = (s) => [...s].sort();
  assert.deepEqual(ids(M.visibleControls({ state: "idle", hover: false })), []);
  assert.deepEqual(ids(M.visibleControls({ state: "working", hover: false })), []);
  assert.deepEqual(ids(M.visibleControls({ state: "idle", hover: true })), ["channel", "inbox", "session", "talk"]);
  assert.deepEqual(ids(M.visibleControls({ state: "waiting_user", hover: false })), ["done", "talk"]);
  assert.deepEqual(ids(M.visibleControls({ state: "waiting_user", hover: false, ask: { mode: "input" } })), ["talk"]);
  assert.deepEqual(ids(M.visibleControls({ state: "waiting_user", hover: false, ask: { mode: "choice" } })), ["done", "talk"]);
  assert.deepEqual(ids(M.visibleControls({ state: "waiting_user", hover: false, ask: { mode: "info" } })), ["done", "talk"]);
  assert.deepEqual(ids(M.visibleControls({ state: "waiting_user", hover: true })), ["channel", "done", "inbox", "session", "talk"]);
  assert.deepEqual(ids(M.visibleControls({ state: "received", hover: false })), []);
  assert.deepEqual(ids(M.visibleControls({ state: "waiting_user", hover: true, panel: true })), []);
  assert.deepEqual(M.ACTIONS.map((a) => a.label), ["状态", "Agent 信箱", "会话", "聊天"]);
});

test("orb labels stay short in all three languages", () => {
  const expected = {
    "zh-Hans": ["状态", "Agent 信箱", "会话", "聊天"],
    "zh-Hant": ["狀態", "Agent 信箱", "會話", "聊天"],
    en: ["Status", "Agent Inbox", "Session", "Chat"],
  };
  try {
    for (const [tag, labels] of Object.entries(expected)) {
      I18N.init({tag, messages: JSON.parse(readFileSync(new URL(`../../pc/i18n/${tag}.json`, import.meta.url), "utf8"))});
      assert.deepEqual(M.ACTIONS.map((a) => a.label), labels, tag);
    }
  } finally {
    I18N.init({tag: "zh-Hans", messages: JSON.parse(readFileSync(new URL("../../pc/i18n/zh-Hans.json", import.meta.url), "utf8"))});
  }
});

test("every button fits inside the backend window with the rig pinned bottom-right", () => {
  const W = C.window;
  assert.deepEqual([M.WINDOW.w, M.WINDOW.h], [W.width, W.height]);
  const rigX = W.width - (M.SPILL.right + M.PET.right + M.PET.w) * M.UI_SCALE;
  const rigY = W.height - (M.SPILL.bottom + M.PET.bottom + M.PET.h) * M.UI_SCALE;
  for (const a of [...M.ACTIONS, M.DONE]) {
    const r = M.fromRig(M.slotRect(a.angle), { x: rigX, y: rigY });
    assert.ok(r.x >= 70 * M.UI_SCALE, `${a.id} leaves room for its label`);
    assert.ok(r.y >= 0 && r.y + r.h <= W.height - 4 * M.UI_SCALE, `${a.id} inside vertically`);
  }
  // no two orbs overlap
  const rects = [...M.ACTIONS, M.DONE].map((a) => M.slotRect(a.angle));
  for (let i = 1; i < rects.length; i++) {
    const a = rects[i - 1], b = rects[i];
    assert.ok(Math.hypot(a.x - b.x, a.y - b.y) >= M.ORB + 4, "orbs touch");
  }
});

test("hover growth and pop reserve convert once, including labels and padding", () => {
  const orb = M.slotRect(M.ACTIONS[3].angle);
  const label = { x: orb.x - 128, y: orb.y, w: 120, h: 24 };
  const grown = M.scaleAbout(label, orb.x + orb.w / 2, orb.y + orb.h / 2, M.HOVER_SCALE_MAX);
  const reach = M.inflate(M.unionRect([grown, { ...grown, x: grown.x - 10, y: grown.y - 4 }]), 8);
  const logical = M.fromRig(reach, { x: 100, y: 150 });
  assert.equal(logical.x, 100 + reach.x);
  assert.equal(logical.y, 150 + reach.y);
  assert.equal(logical.w, reach.w);
  assert.equal(logical.h, reach.h);
});

test("fit flips sides and clamps both axes including oversize fallback", () => {
  const v={x:10,y:20,w:300,h:200},anchor={x:260,y:100,w:30,h:40};
  assert.deepEqual(M.fit({x:300,y:190,w:100,h:80,anchor,gap:10},"right",v),
    {x:150,y:140,w:100,h:80,side:"left"});
  assert.deepEqual(M.fit({x:-100,y:-100,w:400,h:300},"left",v),
    {x:10,y:20,w:400,h:300,side:"left"});
});

test("four arc orientations and Done reserve keep distinct controls inside avail", () => {
  const origin={x:598,y:292},c=M.CHAR_RECT;
  const cases=[{x:0,y:0,w:1140,h:680},
    ...[false,true].flatMap(left=>[false,true].map(top=>({x:left?c.x:0,y:top?c.y:0,
      w:(left?1140:c.x+c.w)-(left?c.x:0),h:(top?680:c.y+c.h)-(top?c.y:0)})))];
  const orientations=new Set();
  for (const v of cases) {
    const arc=M.orientArc(origin,v);orientations.add(`${arc.sx},${arc.sy}`);
    assert.equal(arc.relocated, false);
    assert.equal(arc.rects.done.y, arc.rects.talk.y);
    assert.equal(Math.abs(arc.rects.done.x - arc.rects.talk.x), M.ORB + 12);
    for(const [id,r] of Object.entries(arc.rects)) assert.ok(M.contains(v,M.inflate(r,id==="done"?22:12)),id+JSON.stringify({v,arc}));
    for(const r of Object.values(arc.rects)) assert.ok(M.contains(v,M.hoverExtent(r,r,origin)),JSON.stringify({v,r}));
    const rs=Object.values(arc.rects);
    for(let i=0;i<rs.length;i++)for(let j=i+1;j<rs.length;j++)
      assert.ok(Math.hypot(rs[i].x-rs[j].x,rs[i].y-rs[j].y)>=44,JSON.stringify(arc));
  }
  assert.equal(orientations.size,4);
});

test("busy capsule hugs the character from below, centered, across sizes", () => {
  const origin={x:598,y:292};
  const overlap=(a,b)=>a.x<b.x+b.w && a.x+a.w>b.x && a.y<b.y+b.h && a.y+a.h>b.y;
  for(const w of [32,62.015625,118,218])for(const h of [14,28.84375,66]) {
    const v={x:0,y:0,w:1140,h:680};
    const arc=M.orientArc(origin,v),r=M.busyRect(w,h,origin,v,arc),pet=M.fromRig(M.SPRITE_BOX,origin);
    assert.ok(Math.abs(r.y-(pet.y+pet.h+4))<1e-8,JSON.stringify({r,pet}));
    assert.ok(Math.abs((r.x+r.w/2)-(pet.x+pet.w/2))<1e-8,JSON.stringify({r,pet}));
    assert.ok(M.contains(v,r));
    assert.ok(!overlap(r,pet),JSON.stringify({r,pet}));
    for(const q of Object.values(arc.rects))
      assert.ok(!overlap(r,q),'pill body overlaps orb '+JSON.stringify({r,q}));
  }
});

test("busy capsule stays inside the window and clears pet and orbs at edges and corners", () => {
  const origin={x:598,y:292}, c=M.CHAR_RECT, pet=M.fromRig(M.SPRITE_BOX,origin);
  const overlap=(a,b)=>a.x<b.x+b.w && a.x+a.w>b.x && a.y<b.y+b.h && a.y+a.h>b.y;
  for(const w of [32,62.015625,118,218])for(const h of [14,28.84375,66]) {
    for(const x of [0,c.x])for(const right of [1140,c.x+c.w])
      for(const y of [0,c.y])for(const bottom of [680,c.y+c.h]) {
        const v={x,y,w:right-x,h:bottom-y};
        if (x && right<1140) continue; // no room on either side: fit's clamped fallback
        if (y && bottom<680) continue;
        const arc=M.orientArc(origin,v),r=M.busyRect(w,h,origin,v,arc);
        assert.ok(M.contains(v,r),JSON.stringify({r,v}));
        assert.ok(!overlap(r,pet),JSON.stringify({r,pet,v}));
        for(const q of Object.values(arc.rects)) {
          assert.ok(!overlap(r,q),'pill body overlaps orb: '+JSON.stringify({r,q,v}));
        }
        if (arc.sy === 1 && bottom === 680) {
          assert.equal(r.side, 'bottom');
          assert.ok(Math.abs(r.y - (pet.y + pet.h + 4)) < 1e-8);
        }
      }
  }
});

test("character rect uses measured atlas values at 70 percent", () => {
  const l=JSON.parse(readFileSync(new URL("../../pc/pet/ui/layout.json",import.meta.url)));
  assert.deepEqual(M.CHAR_RECT,l.char_rect);
});

test("ask bubble clears pet, alert mark and visible orbs across all work-area edges", () => {
  const origin={x:598,y:292}, c=M.CHAR_RECT;
  const cases=[{x:0,y:0,w:1140,h:680},
    ...[false,true].flatMap(left=>[false,true].map(top=>({x:left?c.x:0,y:top?c.y:0,
      w:(left?1140:c.x+c.w)-(left?c.x:0),h:(top?680:c.y+c.h)-(top?c.y:0)})))];
  const pet=M.fromRig(M.SPRITE_BOX,origin);
  const overlap=(a,b)=>a.x<b.x+b.w && a.x+a.w>b.x && a.y<b.y+b.h && a.y+a.h>b.y;
  for (const v of cases) {
    const arc=M.orientArc(origin,v);
    for (const visible of [["done","talk"],["channel","inbox","session","talk","done"]]) {
      const b=M.bubbleRect(252,118,origin,v,arc,visible,true);
      assert.ok(M.contains(v,b), JSON.stringify({v,visible,b}));
      assert.ok(!overlap(b,pet), JSON.stringify({v,visible,b,pet}));
      for (const id of visible) assert.ok(!overlap(b,arc.rects[id]), JSON.stringify({v,visible,id,b,orb:arc.rects[id]}));
    }
  }
});

test("window region: integers, clipped, stable key", () => {
  const win = { w: 340, h: 440 };
  const r = M.regionRects([{ x: -3.2, y: 10.6, w: 50, h: 20 }, null, { x: 300, y: 430, w: 80, h: 30 }, { x: 0, y: 0, w: 0, h: 5 }], win);
  assert.deepEqual(r, [{ x: 0, y: 10, w: 47, h: 21 }, { x: 300, y: 430, w: 40, h: 10 }]);
  assert.equal(M.regionKey(r), M.regionKey(M.regionRects([{ x: 300, y: 430, w: 80, h: 30 }, { x: -3.2, y: 10.6, w: 50, h: 20 }], win)));
  assert.deepEqual(M.unionRect([{ x: 1, y: 2, w: 3, h: 4 }, { x: 0, y: 5, w: 1, h: 1 }]), { x: 0, y: 2, w: 4, h: 4 });
});

test("talk: validation, reply target, result text", () => {
  assert.equal(M.checkSay("   ").ok, false);
  assert.equal(M.checkSay("x".repeat(M.SAY_MAX + 1)).ok, false);
  assert.deepEqual(M.checkSay("  好了 "), { ok: true, text: "好了" });
  const snap = { ask: { req_id: "r1" } };
  assert.equal(M.sayTarget({ replyTo: "r1" }, snap), "r1");
  assert.equal(M.sayTarget({ replyTo: "r1" }, { ask: { req_id: "r2" } }), null);   // ask changed: new message
  assert.equal(M.sayTarget({ replyTo: null }, snap), null);
  assert.equal(M.sayResultText({ via: "wait" }), "已交给 Muse");
  assert.match(M.sayResultText({ via: "bridge" }), /一分钟/);
  assert.equal(M.clip("a  b\n c", 10), "a b c");
  assert.equal(M.clip("abcdefghij", 5), "abcd…");
});

test("pet packs: Codex atlas rows, overrides, state tracks, safe names", () => {
  const atlas = M.packTracks({ spritesheetPath: "s.webp", frame: { width: 192, height: 208, columns: 8, rows: 9 },
    animations: { "waving": { frames: [24, 25], fps: 4, loop: false }, enter: { frames: [{ spriteIndex: 40, durationMs: 90 }] } } });
  assert.equal(atlas.idle.frames.length, 6);
  assert.equal(atlas.running.frames[0].index, 7 * 8);
  assert.deepEqual(atlas.waving, { loop: false, frames: [{ index: 24, ms: 250 }, { index: 25, ms: 250 }] });
  assert.equal(atlas.enter.frames[0].ms, 90);
  assert.equal(M.pickTrack(atlas, "working"), "running");
  assert.equal(M.pickTrack(atlas, "received"), "review");
  assert.equal(atlas.review.loop, true);
  assert.ok(!M.HOLD_STATES.has("received") && !M.HOLD_STATES.has("working"));
  assert.equal(M.pickTrack({ waving: {}, idle: {} }, "received"), "waving");
  assert.equal(M.pickTrack({ jumping: {}, idle: {} }, "received"), "idle");
  assert.equal(M.pickTrack(atlas, "enter"), "enter");
  assert.equal(M.pickTrack({ idle: {} }, "waiting_user"), "idle");
  assert.equal(M.pickTrack({}, "exit"), null);                                    // -> CSS fade
  assert.deepEqual(M.framePosition(10, { width: 192, height: 208, columns: 8 }), { x: -384, y: -208 });
  assert.deepEqual(M.packTracks({ kind: "static" }), {});
  assert.equal(M.safeAsset("jolly-placeholder.svg"), "jolly-placeholder.svg");
  for (const bad of ["../x.png", "a/b.png", "x.exe", "", "c:\\x.png"]) assert.equal(M.safeAsset(bad), null);
  // the shipped pack: static placeholder or a Codex atlas, either way loadable
  const jolly = JSON.parse(readFileSync(new URL("../../pc/pet/ui/packs/jolly/pet.json", import.meta.url), "utf8"));
  const file = jolly.kind === "static" ? jolly.image : jolly.spritesheetPath;
  assert.equal(M.safeAsset(file), file);
  assert.ok(existsSync(new URL(`../../pc/pet/ui/packs/jolly/${file}`, import.meta.url)), `${file} is in the package`);
  if (jolly.kind !== "static") assert.ok(M.pickTrack(M.packTracks(jolly), "working"));
});

test("demo backend follows the contract (docs/architecture.en.md section 8)", async () => {
  assert.deepEqual([...METHODS].sort(), [...C.methods].sort());
  const d = new DemoTransport();
  assert.deepEqual(keys(await d.call("config")), [...C.config].sort());
  let s = await d.call("state");
  assert.deepEqual(keys(s), [...C.state].sort());
  assert.ok(C.enums.state.includes(s.state));
  d.ask("info");
  s = await d.call("state");
  assert.deepEqual(keys(s.ask), [...C.ask].sort());
  assert.ok(C.enums.ask_mode.includes(s.ask.mode));
  const done = await d.call("done", s.ask.req_id);
  assert.deepEqual(keys(done), [...C.done_result].sort());
  s = await d.call("state");
  assert.deepEqual(keys(s.last_answer), [...C.last_answer].sort());
  assert.ok(C.enums.answer_by.includes(s.last_answer.by));
  const said = await d.call("say", "你好", null);
  assert.deepEqual(keys(said), [...C.say_result].sort());
  assert.ok(C.enums.say_via.includes(said.via));
  const ch = await d.call("channels");
  assert.deepEqual(keys(ch), [...C.channels].sort());
  for (const c of ch.items) {
    assert.deepEqual(keys(c), [...C.channel_item].sort());
    assert.ok(C.enums.level.includes(c.level) && C.enums.channel_id.includes(c.id));
  }
  assert.deepEqual(CHIME, Object.fromEntries(Object.entries(C.confirm_chime).filter(([k]) => k !== "_comment")));
});
