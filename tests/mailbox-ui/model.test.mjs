// Mailbox UI presentation rules + the API contract sample.
//   node --test tests/mailbox-ui/
import test from "node:test";
import { I18N } from "../../pc/i18n/i18n.js";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as M from "../../pc/mailbox/ui/js/model.js";
import { parseBlocks, parseInline } from "../../pc/mailbox/ui/js/markdown.js";

I18N.init({tag: "zh-Hans", messages: JSON.parse(readFileSync(new URL("../../pc/i18n/zh-Hans.json", import.meta.url), "utf8"))});

const demo = JSON.parse(readFileSync(new URL("../../pc/mailbox/ui/fixtures/demo.json", import.meta.url), "utf8"));

const T = (id, extra = {}) => ({ id, title: id, participants: [], status: "done", last_activity: "",
  last_ts: 0, unread: 0, snippet: "", message_count: 1, ...extra });

test("codex-legion is shown as codex and its default threads merge", () => {
  assert.equal(M.canonicalAgent("codex-legion"), "codex");
  assert.equal(M.canonicalAgent("Codex-Legion "), "codex");
  assert.equal(M.canonicalThreadId("codex-legion/release"), "codex/release");
  assert.equal(M.canonicalThreadId("codex-legion/chat"), "codex/chat");
  assert.equal(M.canonicalThreadId("my-own-thread"), "my-own-thread");        // explicit ids untouched
  assert.equal(M.canonicalThreadId("team/codex-legion/chat"), "team/codex-legion/chat");
  const merged = M.mergeThreadList([
    T("codex-legion/release", { participants: ["codex-legion", "muse"], last_ts: 100, status: "done", unread: 1, message_count: 5, snippet: "old" }),
    T("codex/release", { participants: ["codex", "muse"], last_ts: 200, status: "active", unread: 2, message_count: 2, snippet: "new" }),
  ]);
  assert.equal(merged.length, 1);
  const t = merged[0];
  assert.deepEqual([t.id, t.status, t.unread, t.message_count, t.snippet], ["codex/release", "active", 3, 7, "new"]);
  assert.deepEqual(t.sourceIds.sort(), ["codex-legion/release", "codex/release"]);
  assert.deepEqual(t.participants.sort(), ["codex", "muse"]);
  assert.equal(M.displayName(M.threadAgent(t)), "Codex");
});

test("no surface ever names codex-legion (demo data end to end)", () => {
  const merged = M.mergeThreadList(demo.threads, demo.config.agent_aliases);
  const names = merged.map((t) => [t.id, ...t.participants, M.threadAgent(t, demo.config.agent_aliases)]).flat();
  assert.ok(!names.some((n) => String(n).includes("codex-legion")), names.join(","));
  const release = merged.find((t) => t.id === "codex/release");
  const detail = M.mergeThreadDetails(release.sourceIds.map((id) => demo.details[id]), demo.config.agent_aliases);
  assert.ok(detail.messages.filter((m) => m.role === "agent").every((m) => m.agent === "codex"));
  assert.equal(detail.messages.length, release.message_count);
  assert.equal(detail.title, "发布 MyApp 1.8.0");                     // the oldest request names it
});

test("orphan replies get their own section and are flagged", () => {
  const merged = M.mergeThreadList([
    T("unknown/chat", { participants: ["muse"], last_ts: 999 }),
    T("kiro/chat", { participants: ["kiro", "muse"], last_ts: 10, status: "waiting_user" }),
    T("workbuddy/chat", { participants: ["workbuddy", "muse"], last_ts: 20 }),
  ]);
  const sections = M.sidebarSections(merged);
  assert.deepEqual(sections.map((s) => s.key), ["waiting", "all", "orphans"]);
  assert.deepEqual(sections[2].items.map((t) => t.id), ["unknown/chat"]);
  assert.equal(M.canonicalThreadId("unknown/chat"), "unknown/chat");
  const msgs = M.markOrphans([
    { message_id: "codex_20990101_000000_test99#0", req_id: "codex_20990101_000000_test99", role: "muse", sort_ts: 1 },
    { message_id: "r1", req_id: "r1", role: "agent", sort_ts: 2 },
    { message_id: "r1#1", req_id: "r1", role: "muse", sort_ts: 3 },
  ]);
  assert.deepEqual(msgs.map((m) => m.orphan), [true, false, false]);
  assert.equal(msgs[0].agentHint, "codex");
});

test("requests and their replies stay together, split by day", () => {
  const day = 86400;
  const days = M.groupByRequest([
    { message_id: "a#1", req_id: "a", role: "muse", seq: 1, sort_ts: day * 10 + 50 },
    { message_id: "b", req_id: "b", role: "agent", sort_ts: day * 11 },
    { message_id: "a", req_id: "a", role: "agent", sort_ts: day * 10 },
    { message_id: "a#2", req_id: "a", role: "muse", seq: 2, sort_ts: day * 11 + 10 },   // late final, stays with a
  ]);
  assert.equal(days.length, 2);
  assert.deepEqual(days[0].groups.map((g) => [g.reqId, g.replies.map((r) => r.seq)]), [["a", [1, 2]]]);
  assert.deepEqual(days[1].groups.map((g) => g.reqId), ["b"]);
});

test("states, finals and trust labels", () => {
  assert.equal(M.replyInfo({ final: false, status: "ok" }).final, false);     // --interim ok is not final
  assert.equal(M.replyInfo({ final: true, status: "needs-user" }).tone, "orange");
  assert.equal(M.replyInfo({ final: true, status: "error" }).label, "出错");
  assert.deepEqual(["delivered", "claimed", "acked", "replied", "needs_user", "rejected"].map(M.stepIndex), [0, 1, 2, 3, 3, -1]);
  assert.equal(M.threadStatusInfo("waiting_user").label, "待你处理");
  assert.equal(M.trustInfo("unread_unverified").label, "未读");
  assert.equal(M.trustInfo("rejected_by_client").tone, "red");
  assert.equal(M.trustInfo("read"), null);
  assert.equal(M.mergeStatus("done", "waiting_user"), "waiting_user");
  assert.equal(M.mergeStatus("active", "done"), "active");
});

test("formatting helpers", () => {
  assert.equal(M.formatSize(91234567), "87.0 MB");
  assert.equal(M.formatSize(0), "");
  assert.equal(M.shortHash("a".repeat(64)), "aaaaaaaa…aaaaaa");
  assert.equal(M.baseName("D:\\out\\x.zip"), "x.zip");
  assert.deepEqual(M.highlightParts("Tailscale 设置 tailscale", "TAILSCALE").map((p) => p.hit), [true, false, true]);
  const now = Date.UTC(2026, 10, 12, 12) / 1000;
  assert.equal(M.dayLabel(now - 3600, now), "今天");
  assert.equal(M.dayLabel(now - 86400, now), "昨天");
  assert.equal(M.isRawId("codex_20261113_112256_62e947"), true);
  assert.equal(M.isRawId("Release 1.8"), false);
  assert.equal(M.cleanTitle("codex_20261113_112256_62e947", "pet/chat"), "");
  assert.equal(M.cleanTitle("pet/chat", "pet/chat"), "");
  assert.equal(M.cleanTitle("Check status", "pet/chat"), "Check status");
  assert.equal(M.agentKind("codex-legion"), "codex");
  assert.equal(M.agentKind("claude-code"), "claude");
  assert.equal(M.agentKind("gemini-cli"), "gemini");
  const list = [
    T("pet/chat", { title: "Muse" }),
    T("codex/release", { title: "发布 1.0" }),
    T("codex/chat", { title: "帮我查一下状态" }),
    T("workbuddy/chat", { title: "日常" }),
  ];
  assert.deepEqual(M.sidebarSections(list).map((s) => s.key), ["pinned", "all"]);
  assert.equal(M.threadTopicBadge(list[0], list), null);
  assert.deepEqual(M.threadTopicBadge(list[1], list), { kind: "release", label: "发版" });
  assert.deepEqual(M.threadTopicBadge(T("codex/release", { title: "发布 微信语音桥 v1.2.0" }), list), { kind: "release", label: "微信语音桥" });
  assert.deepEqual(M.threadTopicBadge(list[2], list), { kind: "chat", label: "状态" });
  assert.equal(M.threadTopicBadge(list[3], list), null);
});

test("markdown parser handles bold, code, lists, quotes, safe links and snake_case", () => {
  const inline = parseInline("r15 部署验收：**弹窗加粗** 与 `pet_chat` 和 [文档](https://example.com) 及 [bad](javascript:alert(1))");
  assert.equal(inline.find((n) => n.type === "bold").children[0].text, "弹窗加粗");
  assert.equal(inline.find((n) => n.type === "code").text, "pet_chat");
  assert.equal(inline.find((n) => n.type === "link").href, "https://example.com/");
  assert.ok(!inline.some((n) => n.type === "link" && n.href.startsWith("javascript:")));
  const blocks = parseBlocks("### 标题\n> 引用\n- 第一项\n- 第二项\n```js\nconst x = 1;\n```");
  assert.deepEqual(blocks.map((b) => b.type), ["heading", "quote", "ul", "code"]);
  assert.equal(blocks[2].items.length, 2);
});

test("groupByRequest keeps Muse ask before user answer and falls back on zero sort_ts", () => {
  const days = M.groupByRequest([
    { message_id: "pop1:ask:0", req_id: "pop1", role: "muse", seq: 0, sort_ts: 1794836514.999 },
    { message_id: "pop1:ans:0", req_id: "pop1", role: "user", seq: 1, sort_ts: 1794836515.0 },
    { message_id: "req0", req_id: "req0", role: "agent", sort_ts: 0, created_at: "2026-11-16T14:41:03.5554890+01:00" },
    { message_id: "req0#1", req_id: "req0", role: "muse", seq: 1, sort_ts: 1794836478 },
  ]);
  assert.equal(days.length, 1);
  assert.ok(days[0].dayTs > 1700000000);
  const popGroup = days[0].groups.find((g) => g.reqId === "pop1");
  assert.equal(popGroup.preReplies.length, 1);
  assert.equal(popGroup.preReplies[0].role, "muse");
  assert.equal(popGroup.request.role, "user");
  assert.equal(popGroup.replies.length, 0);
});

test("project extraction & release follow-up merge stay generic across projects", () => {
  assert.equal(M.projectFromArtifact("D:\\Releases\\Sample-Bridge-V1.8.5-win-x64.zip"), "Sample Bridge");
  assert.equal(M.projectFromArtifact("D:\\out\\MuseLink-0.3.0-Setup.exe"), "MuseLink");
  assert.equal(M.projectFromUrl("https://example.com/sample-bridge/latest.json"), "Sample Bridge");

  const rel = T("opencode/release", {
    title: "用户已确认发版，请缪斯协助校验并发布 1.8.5（仅使用冻结的 6 字段 latest.json 契约）。",
    last_ts: 1794836463,
    snippet: "release 1.8.5 published",
  });
  const chatFollowup = T("opencode/chat", {
    title: "【紧急】1.8.5 独立公网复核发现问题：当前线上 https://example.com/sample-bridge/latest.json 多了一个字段",
    last_ts: 1794837573,
    snippet: "收到，1.8.5 发布状态已复核通过",
  });
  const unrelatedChat = T("codex/chat", {
    title: "帮我确认一下 Tailscale 的出口设置",
    last_ts: 1794837500,
    snippet: "需要你确认出口设置",
  });
  const codexRel = T("codex/release", {
    title: "发布 MyApp 1.8.1",
    last_ts: 1794836400,
  });

  const hints = {
    "opencode/release": M.extractThreadHint({
      ...rel,
      messages: [{ req_id: "r1", text: rel.title, release_card: { version: "1.8.5", package_path: "D:\\out\\Sample-Bridge-V1.8.5-win-x64.zip" } }],
    }),
  };
  const merged = M.mergeThreadList([rel, chatFollowup, codexRel, unrelatedChat], M.DEFAULT_ALIASES, hints);
  assert.equal(merged.length, 3);
  const openMerged = merged.find((t) => t.id === "opencode/release");
  assert.deepEqual(openMerged.sourceIds, ["opencode/release", "opencode/chat"]);
  assert.deepEqual(M.threadTopicBadge(openMerged, merged, M.DEFAULT_ALIASES, hints), { kind: "release", label: "Sample Bridge" });
  assert.equal(M.cleanThreadSubtitle({ ...openMerged, messages: [{ release_card: { version: "1.8.5" } }] }, hints), "v1.8.5");
  assert.ok(merged.some((t) => t.id === "codex/chat"));
});

// ---- the backend contract (fixtures/contract.json), checked on the demo sample
const C = JSON.parse(readFileSync(new URL("../../pc/mailbox/ui/fixtures/contract.json", import.meta.url), "utf8"));

test("demo.json follows the backend contract (docs/architecture.en.md section 7)", () => {
  for (const k of C.config) assert.ok(k in demo.config, k);
  for (const k of C.status) assert.ok(k in demo.status, k);
  for (const t of demo.threads) {
    for (const k of C.thread_summary) assert.ok(k in t, `${t.id}.${k}`);
    assert.ok(C.enums.thread_status.includes(t.status), t.status);
  }
  for (const [id, d] of Object.entries(demo.details)) {
    assert.equal(d.id, id);
    for (const m of d.messages) {
      for (const k of C.message) assert.ok(k in m, `${m.message_id}.${k}`);
      assert.ok(C.enums.role.includes(m.role), m.role);
      assert.ok(C.enums.state.includes(m.state), m.state);
      assert.ok(C.enums.trust.includes(m.trust), String(m.trust));
      if (m.release_card) for (const k of C.release_card) assert.ok(k in m.release_card, `${m.message_id}.card.${k}`);
    }
  }
});
