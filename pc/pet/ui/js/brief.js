import { t as tr } from "../../../i18n/i18n.js";

const activities = new Set(["screen", "shell", "script", "steps", "action", "read", "working"]);
const segments = new Intl.Segmenter(undefined, { granularity: "grapheme" });

export function phaseUntil(stamp, bubbleMs, now = Date.now()) {
  const at = Date.parse(stamp);
  // Receipt time is a compatibility fallback for previews without a timestamp.
  // Delayed observations must not give an old phase a new bubble_ms window.
  return Math.min(now, Number.isFinite(at) ? at : now) + bubbleMs;
}

// The backend bounds and cleans the phase; keep previews/older hosts safe too.
// Eight graphemes includes the ellipsis; do not split emoji or combining marks.
export function clipBrief(text) {
  const line = Array.from(String(text ?? "")).slice(0, 500).join("")
    .replace(/\p{Cf}/gu, (c) => c === "\u200d" ? c : "")
    .replace(/[\u0000-\u001f\u007f-\u009f\s]+/gu, " ").trim();
  if (!line.replace(/\u200d/gu, "")) return "";
  const chars = Array.from(segments.segment(line), (part) => part.segment);
  return chars.length > 8 ? chars.slice(0, 7).join("").trimEnd() + "…" : line;
}

export function workingBrief(snapshot, phrase, until, now = Date.now()) {
  if (snapshot?.state !== "working") return null;
  const phase = now < until ? clipBrief(phrase) : "";
  const activity = activities.has(snapshot.activity) ? snapshot.activity : "working";
  return phase || (snapshot.activity ? clipBrief(tr(`pet.activity.${activity}`)) : null);
}
