// sprite.js - renders a pet pack into the pet element.
// Two pack kinds (docs/architecture.en.md section 8):
//   * Codex Pets atlas: pet.json + spritesheet (8x9 cells of 192x208), frames
//     stepped here; "animations" in pet.json override the default rows.
//   * static: one image; the state is carried by CSS motion (placeholder
//     route of the task book: static picture + fades until the user picks
//     the animation route).
import * as M from "./model.js";

const PACK_ID = /^[a-z0-9][a-z0-9_-]{0,40}$/i;
const reduced = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

async function readPack(id) {
  if (!PACK_ID.test(id)) throw new Error("bad pack id");
  const base = new URL(`../packs/${id}/`, import.meta.url);
  const res = await fetch(new URL("pet.json", base));
  if (!res.ok) throw new Error(`pack ${id} missing`);
  const pack = await res.json();
  const file = M.safeAsset(pack.kind === "static" ? pack.image : pack.spritesheetPath);
  if (!file) throw new Error(`pack ${id}: bad image name`);
  return { pack, url: new URL(file, base).href };
}

export class Sprite {
  constructor(el) {
    this.el = el;
    this.frames = el.querySelector(".frames");
    this.pack = null;
    this.tracks = {};
    this.timer = 0;
    this.current = null;
  }

  async load(id) {
    let got;
    try { got = await readPack(id); } catch (e) { got = await readPack("jolly"); }
    const { pack, url } = got;
    this.pack = pack;
    this.tracks = M.packTracks(pack);
    const f = this.frame = pack.kind === "static"
      ? { width: (pack.size && pack.size.width) || 192, height: (pack.size && pack.size.height) || 208, columns: 1 }
      : { width: 192, height: 208, columns: 8, rows: 9, ...(pack.frame || {}) };
    const scale = M.spriteFit(f);
    Object.assign(this.frames.style, {
      width: `${f.width}px`, height: `${f.height}px`,
      transform: `translate(-50%, 0) scale(${scale})`,
      backgroundImage: `url("${url}")`,
      backgroundSize: pack.kind === "static" ? "contain" : `${f.width * f.columns}px ${f.height * (f.rows || 9)}px`,
    });
    this.el.classList.toggle("static-pack", pack.kind === "static");
    this.el.dataset.pack = pack.id || id;
    return pack;
  }

  has(name) { return !!this.tracks[name]; }

  // state: idle | working | waiting_user | received
  // A state in M.HOLD_STATES plays its track once and keeps the last frame
  // until the state changes. While a one-shot (enter / exit) runs, play()
  // only records the state: the poll calls play() every second, and cutting
  // the one-shot short would leave once() unresolved (leave() then never
  // hid the window, and every later right click was ignored).
  play(state) {
    for (const s of M.STATES) this.el.classList.toggle(`motion-${s}`, s === state);
    this.state = state;
    if (!this.pack || this.pack.kind === "static" || this.oneShot) return;
    const name = M.pickTrack(this.tracks, state);
    if (!name || name === this.current) return;
    if (M.HOLD_STATES.has(state)) this.run(name, null, true, true);
    else this.run(name, () => this.play("idle"));
  }

  // One-shot slot (enter / exit). Resolves when done; false if the pack has
  // no such track (the caller then falls back to a fade).
  once(slot) {
    const name = M.pickTrack(this.tracks, slot);
    if (!name || !this.pack || this.pack.kind === "static" || reduced() || this.oneShot) return Promise.resolve(false);
    this.oneShot = true;
    return new Promise((resolve) => this.run(name, () => { this.oneShot = false; resolve(true); }, true));
  }

  run(name, onEnd, forceOnce = false, hold = false) {
    clearTimeout(this.timer);
    this.current = name;
    const track = this.tracks[name];
    let i = 0;
    const step = () => {
      const fr = track.frames[i];
      const p = M.framePosition(fr.index, this.frame);
      this.frames.style.backgroundPosition = `${p.x}px ${p.y}px`;
      if (reduced()) return;
      i += 1;
      if (i >= track.frames.length) {
        if (track.loop && !forceOnce) i = 0;
        else if (hold) return;   // keep the last frame; current stays name
        else { this.timer = setTimeout(() => { this.current = null; onEnd && onEnd(); }, fr.ms); return; }
      }
      this.timer = setTimeout(step, fr.ms);
    };
    step();
  }
}
