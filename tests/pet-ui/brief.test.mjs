import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { I18N } from "../../pc/i18n/i18n.js";
import { clipBrief, phaseUntil, workingBrief } from "../../pc/pet/ui/js/brief.js";

const init = (tag) => I18N.init({ tag, messages: JSON.parse(readFileSync(new URL(`../../pc/i18n/${tag}.json`, import.meta.url), "utf8")) });

test("delayed phases expire by event time instead of gaining a fresh window on receipt", () => {
  init("en");
  const now = Date.parse("2026-01-01T12:00:10Z");
  const snap = {state: "working", activity: "shell"};
  const until = phaseUntil("2026-01-01T12:00:00Z", 8000, now);
  assert.equal(workingBrief(snap, "Old phase", until, now), "Running");
  assert.equal(phaseUntil("2026-01-01T12:00:09Z", 8000, now), now + 7000);
  assert.equal(phaseUntil(undefined, 8000, now), now + 8000);
  assert.equal(phaseUntil("invalid", 8000, now), now + 8000);
  assert.equal(phaseUntil("2026-01-01T12:01:00Z", 8000, now), now + 8000);
});

test("fresh phase takes precedence; expiry reveals the latest job label", () => {
  init("zh-Hans");
  const snap = { state: "working", activity: "screen", brief: "正在检查新的界面状态以及更多内容" };
  const full = snap.brief;
  assert.equal(workingBrief(snap, full, 8000, 7999), "正在检查新的界…");
  assert.equal(snap.brief, full);
  assert.equal(workingBrief(snap, full, 8000, 8000), "查看屏幕");
  snap.activity = "shell";
  assert.equal(workingBrief(snap, full, 8000, 8001), "执行命令");
  assert.equal(workingBrief(snap, null, 0, 9000), "执行命令");
  assert.equal(workingBrief(snap, "\n\0\t", 10000, 9000), "执行命令");
  for (const state of ["idle", "waiting_user", "received"]) {
    assert.equal(workingBrief({ ...snap, state }, full, 10000, 9000), null);
  }
});

test("working without a phase or activity leaves the pill on its dot fallback", () => {
  init("en");
  for (const activity of [undefined, null, ""]) {
    const snap = { state: "working", activity };
    assert.equal(workingBrief(snap, null, 0, 9000), null);
    assert.equal(workingBrief(snap, "\n\0\t", 10000, 9000), null);
    assert.equal(workingBrief(snap, "Checking", 10000, 9000), "Checking");
    assert.equal(workingBrief(snap, "Checking", 10000, 10000), null);
  }
});

test("display phrases are one line, at most eight graphemes, with intact Unicode", () => {
  assert.equal(clipBrief("  查看\n\t屏幕\0  "), "查看 屏幕");
  assert.equal(clipBrief("一二三四五六七八"), "一二三四五六七八");
  assert.equal(clipBrief("一二三四五六七八九"), "一二三四五六七…");
  assert.equal(clipBrief("a".repeat(2000)), "aaaaaaa…");
  const emoji = "👩‍💻";
  assert.equal(clipBrief(emoji.repeat(9)), emoji.repeat(7) + "…");
  assert.equal(clipBrief("e\u0301".repeat(9)), "e\u0301".repeat(7) + "…");
  assert.equal(clipBrief(null), "");
  assert.equal(clipBrief("\u200b\u202e\ufeff\u200d"), "");
  assert.equal(clipBrief("a\u200bb\u202ec"), "abc");
});

test("all activity labels stay short in each supported language; unknown kinds use a generic label", () => {
  for (const tag of ["en", "zh-Hans", "zh-Hant"]) {
    init(tag);
    for (const activity of ["screen", "shell", "script", "steps", "action", "read", "working"]) {
      const label = I18N.t(`pet.activity.${activity}`);
      assert.ok(label && !/\s|…/u.test(label));
      assert.ok(Array.from(label).length <= 8);
      assert.equal(workingBrief({ state: "working", activity }, null, 0, 1), label);
    }
    assert.equal(workingBrief({ state: "working", activity: "<raw-command>" }, null, 0, 1), I18N.t("pet.activity.working"));
  }
});
