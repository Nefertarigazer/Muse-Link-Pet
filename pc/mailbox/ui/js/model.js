import { I18N, t as tr } from "../../../i18n/i18n.js";
// model.js - pure presentation rules for the mailbox UI (no DOM, no I/O).
// Everything the UI decides about the index data lives here, so it can be
// tested under Node (tests/mailbox-ui/model.test.mjs):
//   * agent aliases: "codex-legion" is shown as "codex" everywhere, and the
//     default threads "<alias>/<topic>" merge into "<canonical>/<topic>";
//   * orphan replies (no request in the index) are flagged; a thread made
//     only of orphans goes to its own sidebar section instead of posing as
//     a contact called "unknown";
//   * labels, grouping by request and by day, formatting.

export const DEFAULT_ALIASES = Object.freeze({ "codex-legion": "codex" });
export const DEFAULT_NAMES = Object.freeze({
  codex: "Codex", claude: "Claude", gemini: "Gemini", workbuddy: "WorkBuddy", kiro: "Kiro", opencode: "OpenCode", muse: "Muse", pet: "Pet", get user() { return tr("mailbox.you"); },
});
const TOPICS = new Set(["release", "chat"]);
const AGENT_RX = /^[a-z][a-z0-9-]{1,31}$/;
const RAW_ID_RX = /^(?:[a-z][a-z0-9-]*_\d{8}_\d{6}_[A-Za-z0-9]+|req_\d{8}_\d{6}_[A-Za-z0-9]+|local-[a-z0-9-]+-\d+-[a-f0-9]+)$/i;
const THREAD_RANK = { waiting_user: 3, active: 2, done: 1 };

export function isRawId(text) {
  return typeof text === "string" && RAW_ID_RX.test(text.trim());
}

export function cleanTitle(title, threadId = "") {
  const s = String(title || "").trim();
  if (!s || s === threadId || isRawId(s)) return "";
  return s;
}

export function canonicalAgent(id, aliases = DEFAULT_ALIASES) {
  if (typeof id !== "string" || !id) return "";
  const key = id.trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(aliases, key) ? aliases[key] : key;
}

export function displayName(id, names = DEFAULT_NAMES) {
  if (!id) return tr("mailbox.unknown");
  if (Object.prototype.hasOwnProperty.call(names, id)) return names[id];
  return id.split("-").map((p) => (p ? p[0].toUpperCase() + p.slice(1) : p)).join(" ");
}

// Only default keys ("<agent>/release" | "<agent>/chat") are rewritten;
// an explicit thread_id is the agents' own choice and stays as it is.
export function splitDefaultKey(threadId) {
  if (typeof threadId !== "string") return null;
  const i = threadId.lastIndexOf("/");
  if (i <= 0) return null;
  const agent = threadId.slice(0, i);
  const topic = threadId.slice(i + 1);
  if (!TOPICS.has(topic) || agent.includes("/")) return null;
  return { agent, topic };
}

export function canonicalThreadId(threadId, aliases = DEFAULT_ALIASES) {
  const parts = splitDefaultKey(threadId);
  if (!parts || parts.agent === "unknown") return threadId;
  return `${canonicalAgent(parts.agent, aliases)}/${parts.topic}`;
}

export function topicOf(threadId) {
  const parts = splitDefaultKey(threadId);
  return parts ? parts.topic : "thread";
}

export function mergeStatus(a, b) {
  return (THREAD_RANK[a] || 0) >= (THREAD_RANK[b] || 0) ? a : b;
}

// "codex_20261112_101500_ab12cd" -> "codex" (a hint for orphans only)
export function agentFromReqId(reqId) {
  if (typeof reqId !== "string") return "";
  const prefix = reqId.split("_")[0].toLowerCase();
  return AGENT_RX.test(prefix) ? prefix : "";
}

// A thread with no agent participant holds only replies whose request is
// gone (the index files them under "unknown/chat").
export function isOrphanThread(summary) {
  const people = (summary && summary.participants) || [];
  return people.length > 0 && people.every((p) => p === "muse");
}

function ts(value) {
  return typeof value === "number" && isFinite(value) ? value : 0;
}

function isoTs(value) {
  if (typeof value !== "string" || !value.trim()) return 0;
  const normalized = value.trim().replace(/(\.\d{3})\d+/, "$1");
  const ms = Date.parse(normalized);
  return isFinite(ms) ? ms / 1000 : 0;
}

function msgTs(m) {
  return ts(m && m.sort_ts) || isoTs(m && (m.created_at || m.replied_at)) || 0;
}

const GENERIC_ARTIFACT = new Set([
  "fake", "test", "app", "a", "p", "out", "dist", "build", "setup", "installer",
  "package", "archive", "release", "bundle", "artifacts", "win", "x64", "x86", "arm64",
]);
const STRIP_PKG_TOKENS = /(?:^|[-_. ])(?:win32|win64|win|windows|macos|mac|osx|darwin|linux|x64|x86_64|x86|amd64|arm64|ia32|setup|installer|portable|package|pkg|release|dist|bin|signed|unsigned)$/i;
const VERSION_TAIL = /(?:^|[-_. ])v?\d+(?:\.\d+){1,3}(?:[-+._][A-Za-z0-9]+)*$/i;
const GENERIC_URL_SEG = new Set([
  "api", "v1", "v2", "v3", "release", "releases", "download", "downloads",
  "assets", "raw", "blob", "tree", "latest.json", "index.html", "manifest.json",
]);
const VERSION_RX = /\b\d+\.\d+\.\d+\b/g;
const RELEASE_CTX_RX = /(?:发布|发版|回执|清单|校验|核验|复核|latest\.json|release|manifest|publish|sha-?256|setup|installer|\.zip|\.exe)/i;
const ISSUE_PHRASE_RX = /(?:发现问题|故障|报错|疑似|多了一个|少了|失败|无法|怎么|为什么|因为|所以|但是|然后|确认了|修复了|已经|已于|收到|字段|契约|源码已)/u;

export function projectFromArtifact(rawPath) {
  const file = String(rawPath || "").trim().split(/[\\/]+/).pop() || "";
  if (!file) return "";
  let stem = file.replace(/\.(?:tar\.gz|zip|exe|msi|7z|tgz|dmg|pkg|appimage)$/i, "");
  for (let k = 0; k < 4; k++) {
    const next = stem.replace(STRIP_PKG_TOKENS, "").replace(VERSION_TAIL, "").replace(/[-_. ]+$/, "");
    if (next === stem) break;
    stem = next;
  }
  const clean = stem.replace(/[-_]+/g, " ").replace(/\s+/g, " ").trim();
  if (!clean || clean.length < 2 || GENERIC_ARTIFACT.has(clean.toLowerCase())) return "";
  return clean;
}

export function projectFromUrl(rawText) {
  const matches = String(rawText || "").match(/https?:\/\/[^\s)>\]"'`]+/gi) || [];
  for (const raw of matches) {
    try {
      const u = new URL(raw.replace(/[.,;:!?。，；！？]+$/, ""));
      const segs = u.pathname.split("/").filter(Boolean);
      for (let i = segs.length - 1; i >= 0; i--) {
        const seg = segs[i].replace(/\.[a-z0-9]{1,5}$/i, "");
        const low = seg.toLowerCase();
        if (!seg || seg.length < 2 || GENERIC_URL_SEG.has(low) || GENERIC_URL_SEG.has(segs[i].toLowerCase())) continue;
        if (/^\d+(?:\.\d+)*$/.test(seg)) continue;
        const words = seg.replace(/[-_]+/g, " ").trim();
        if (!words) continue;
        return words.replace(/\b[a-z]/g, (ch) => ch.toUpperCase());
      }
    } catch {}
  }
  return "";
}

export function extractThreadHint(detail) {
  if (!detail || !Array.isArray(detail.messages)) return null;
  let artifactProject = "";
  let urlProject = "";
  const versions = new Set();
  const reqIds = new Set();
  const replyTos = new Set();
  const texts = [];
  for (const m of detail.messages) {
    if (m.req_id) reqIds.add(m.req_id);
    if (m.payload && typeof m.payload.reply_to === "string" && m.payload.reply_to) replyTos.add(m.payload.reply_to);
    const card = m.release_card || {};
    const payload = m.payload || {};
    for (const p of [card.package_path, payload.package_path, card.setup_path, payload.setup_path]) {
      if (!artifactProject && p) artifactProject = projectFromArtifact(p);
    }
    const ver = card.version || payload.version;
    if (typeof ver === "string" && ver.trim()) versions.add(ver.trim());
    if (typeof m.text === "string" && m.text.trim()) {
      texts.push(m.text);
      if (!urlProject) urlProject = projectFromUrl(m.text);
      for (const vm of m.text.match(VERSION_RX) || []) versions.add(vm);
    }
  }
  if (typeof detail.title === "string") {
    if (!urlProject) urlProject = projectFromUrl(detail.title);
    for (const vm of detail.title.match(VERSION_RX) || []) versions.add(vm);
  }
  return {
    project: artifactProject || urlProject,
    artifactProject,
    urlProject,
    versions: [...versions],
    reqIds: [...reqIds],
    replyTos: [...replyTos],
    texts,
  };
}

function getHint(hints, id) {
  if (!hints || !id) return null;
  return hints instanceof Map ? hints.get(id) : hints[id] || null;
}

function collectReleaseVersions(t, hint) {
  const out = new Set(hint && Array.isArray(hint.versions) ? hint.versions : []);
  for (const s of [t.title, t.snippet]) {
    for (const vm of String(s || "").match(VERSION_RX) || []) out.add(vm);
  }
  return [...out];
}

function shouldMergeChatIntoRelease(rel, chat, aliases, hints) {
  const relHint = getHint(hints, rel.id) || extractThreadHint(rel);
  const chatHint = getHint(hints, chat.id) || extractThreadHint(chat);
  const chatBlob = [chat.title, chat.snippet, ...(chatHint && chatHint.texts ? chatHint.texts : [])].filter(Boolean).join("\n");
  if (!chatBlob) return false;
  if (relHint && relHint.reqIds && relHint.reqIds.length) {
    if (chatHint && chatHint.replyTos && chatHint.replyTos.some((id) => relHint.reqIds.includes(id))) return true;
    if (relHint.reqIds.some((id) => chatBlob.includes(id))) return true;
  }
  const relTs = rel.lastTs || 0;
  const chatTs = chat.lastTs || 0;
  if (relTs > 0 && chatTs > 0 && Math.abs(chatTs - relTs) > 86400) return false;
  if (!RELEASE_CTX_RX.test(chatBlob)) return false;
  const relVersions = collectReleaseVersions(rel, relHint);
  const chatVersions = chatBlob.match(VERSION_RX) || [];
  if (relVersions.some((v) => chatVersions.includes(v))) return true;
  const relProj = (relHint && relHint.project) || extractTopicLabel(rel, relHint);
  if (relProj) {
    const norm = (x) => String(x || "").toLowerCase().replace(/[\s_-]+/g, "");
    const pNorm = norm(relProj);
    if (pNorm.length >= 3 && norm(chatBlob).includes(pNorm)) return true;
    const chatUrlProj = projectFromUrl(chatBlob);
    if (chatUrlProj && norm(chatUrlProj) === pNorm) return true;
  }
  return false;
}

// thread summaries from /api/threads -> merged, canonical, sorted
export function mergeThreadList(threads, aliases = DEFAULT_ALIASES, hints = null) {
  const byId = new Map();
  for (const t of threads || []) {
    const id = canonicalThreadId(t.id, aliases);
    const people = [...new Set((t.participants || []).map((p) => (p === "muse" ? p : canonicalAgent(p, aliases))))];
    const cur = byId.get(id);
    const lastTs = ts(t.last_ts) || isoTs(t.last_activity) || 0;
    if (!cur) {
      byId.set(id, { ...t, id, participants: people, sourceIds: [t.id], lastTs, orphan: isOrphanThread(t) });
      continue;
    }
    cur.sourceIds.push(t.id);
    cur.participants = [...new Set([...cur.participants, ...people])];
    cur.status = mergeStatus(cur.status, t.status);
    cur.unread = (cur.unread || 0) + (t.unread || 0);
    cur.message_count = (cur.message_count || 0) + (t.message_count || 0);
    cur.orphan = cur.orphan && isOrphanThread(t);
    if (lastTs > cur.lastTs) {
      cur.lastTs = lastTs;
      cur.last_activity = t.last_activity;
      cur.snippet = t.snippet;
    }
  }
  for (const [id, rel] of [...byId.entries()]) {
    const parts = splitDefaultKey(id);
    if (!parts || parts.topic !== "release" || parts.agent === "pet" || parts.agent === "unknown") continue;
    const chatId = `${parts.agent}/chat`;
    const chat = byId.get(chatId);
    if (!chat || chat.orphan) continue;
    if (!shouldMergeChatIntoRelease(rel, chat, aliases, hints)) continue;
    rel.sourceIds.push(...chat.sourceIds);
    rel.participants = [...new Set([...rel.participants, ...chat.participants])];
    rel.status = mergeStatus(rel.status, chat.status);
    rel.unread = (rel.unread || 0) + (chat.unread || 0);
    rel.message_count = (rel.message_count || 0) + (chat.message_count || 0);
    if (chat.lastTs > rel.lastTs) {
      rel.lastTs = chat.lastTs;
      rel.last_activity = chat.last_activity;
      rel.snippet = chat.snippet;
    }
    byId.delete(chatId);
  }
  return [...byId.values()].sort((a, b) => b.lastTs - a.lastTs || (a.id < b.id ? -1 : 1));
}

export function sidebarSections(merged) {
  const pinned = [], waiting = [], others = [], orphans = [];
  for (const t of merged) {
    if (t.orphan) orphans.push(t);
    else if (t.id === "pet/chat" || t.pinned) pinned.push(t);
    else if (t.status === "waiting_user") waiting.push(t);
    else others.push(t);
  }
  return [
    { key: "pinned", get title() { return tr("mailbox.pinned"); }, items: pinned },
    { key: "waiting", get title() { return tr("mailbox.needs_you"); }, items: waiting },
    { key: "all", get title() { return tr("mailbox.conversation"); }, items: others },
    { key: "orphans", get title() { return tr("mailbox.orphans"); }, items: orphans },
  ].filter((s) => s.items.length > 0);
}

// The agent a thread "belongs to" (sidebar avatar / header).
export function threadAgent(summary, aliases = DEFAULT_ALIASES) {
  if (summary && summary.id === "pet/chat") return "muse";
  const parts = splitDefaultKey(summary.id);
  if (parts && parts.agent !== "unknown") return canonicalAgent(parts.agent, aliases);
  const ai = (summary.participants || []).find((p) => p !== "muse" && p !== "pet" && p !== "user");
  if (ai) return canonicalAgent(ai, aliases);
  const first = (summary.participants || []).find((p) => p !== "muse");
  return first ? canonicalAgent(first, aliases) : "";
}

export function agentKind(id) {
  const s = String(id || "").toLowerCase();
  if (!s) return "";
  if (s === "muse" || s === "user" || s === "pet") return s;
  if (s.includes("codex") || s.includes("gpt") || s.includes("openai")) return "codex";
  if (s.includes("claude") || s.includes("anthropic")) return "claude";
  if (s.includes("gemini")) return "gemini";
  if (s.includes("kiro")) return "kiro";
  if (s.includes("workbuddy")) return "workbuddy";
  if (s.includes("opencode")) return "opencode";
  return s;
}

export function clip(text, max = 14) {
  const s = String(text || "").replace(/\s+/g, " ").trim();
  return s.length > max ? s.slice(0, max - 1).trimEnd() + "…" : s;
}

function displayCols(text) {
  let cols = 0;
  for (const ch of String(text || "")) cols += /[\u2e80-\uffff]/.test(ch) ? 2 : 1;
  return cols;
}

function clipCols(text, maxCols = 22) {
  const s = String(text || "").replace(/\s+/g, " ").trim();
  if (displayCols(s) <= maxCols) return s;
  let out = "";
  let cols = 0;
  for (const ch of s) {
    const w = /[\u2e80-\uffff]/.test(ch) ? 2 : 1;
    if (cols + w > maxCols - 1) return out.trimEnd() + "…";
    out += ch;
    cols += w;
  }
  return out;
}

function stripTaskChatter(raw) {
  let s = String(raw || "")
    .replace(/\*\*([^*\n]+)\*\*|`([^`\n]+)`/g, "$1$2")
    .split(/https?:\/\//i)[0]
    .replace(/[【\[](?:紧急|加急|重要|通知|提醒|复核|确认|urgent|notice|alert)[】\]]\s*/giu, "")
    .replace(/^(?:紧急|加急)[：:\s]+/u, "");
  s = s.split(/[\r\n。！？!?;；]/)[0] || s;
  s = s.replace(/[（(][^（）()]*[）)]/g, " ").replace(/\s+/g, " ").trim();
  const preambles = [
    /^(?:用户|我|这边|本机|客户端)?(?:已|已经)?(?:确认|同意|批准|完成|收到)(?:发版|发布|上线|回执|并(?:已)?完成)?[，,、:\s]+/u,
    /^(?:关于|针对|对于|有关)(?:发布单|任务单|工单)?\s*(?:[a-z][a-z0-9-]*_\d{8}_\d{6}_[a-z0-9]+)?[，,：:\s]*/iu,
    /^(?:请|麻烦|帮忙|帮我|请协助|请帮我|能否|可以|请求|申请)?(?:缪斯|muse)?(?:协助|帮忙|帮助)?(?:核验|校验|检查|查一下|查看|确认一下|确认|复核|发布|发版|部署|更新|修复|测试|处理|审查|审阅|看看|讨论|询问|咨询)(?:一下)?[并和及与，,：:\s]*/iu,
    /^(?:独立公网复核|二次独立公网复核|公网复核|发版核验|线上复核|发现问题|发布状态)?[，,：:\s]*(?:当前线上|当前|目前)?[，,：:\s]*/u,
    /^(?:please|kindly|can you|could you|help me|user confirmed|confirming|requesting|regarding|about|urgent)\b[,:\s-]*/i,
    /^(?:check|confirm|verify|publish|release|deploy|test|fix|update|review|discuss)\b(?:\s+(?:for|of|the))?\s+/i,
  ];
  for (let round = 0; round < 4; round++) {
    const prev = s;
    for (const rx of preambles) s = s.replace(rx, "");
    s = s.replace(/^[，,、：:\s]+/, "").trim();
    if (s === prev) break;
  }
  s = s
    .replace(/(?:^|\s+)v?\d+(?:\.\d+){1,3}(?:[-+.\w]*)?(?:\s*[（(].*)?$/i, "")
    .replace(/^\d+(?:\.\d+){1,3}\s+/i, "")
    .replace(/(?:它)?(?:已经)?(?:更新|发布|上线|完成|修复|准备|提交|推送|复核|校验)(?:好了|完毕|完成|通过|了)?.*$/u, "")
    .replace(/[？?！!。，,：:]+$/u, "")
    .replace(/\s+/g, " ")
    .trim();
  if (/^(?:了|并|的|发版|发布|发布回执|回执|没有|问题)$/u.test(s)) return "";
  return s;
}

function extractTopicLabel(t, hint = null) {
  const raw = cleanTitle(t.title, t.id) || cleanTitle(t.snippet, t.id);
  const effectiveHint = hint || extractThreadHint(t);
  let textLabel = "";
  if (raw) {
    let s = stripTaskChatter(raw);
    if (s && !isRawId(s) && !ISSUE_PHRASE_RX.test(s)) {
      if (s.length > 10) s = s.replace(/一下|的(?=\S)/gu, "");
      if (displayCols(s) <= 22) textLabel = s;
    }
  }
  if (textLabel) return clipCols(textLabel, 22);
  const hintProj = (effectiveHint && (effectiveHint.artifactProject || effectiveHint.urlProject))
    || projectFromUrl(raw)
    || projectFromUrl(t.snippet);
  if (hintProj) return clipCols(hintProj, 22);
  if (raw) {
    let s = stripTaskChatter(raw);
    if (s && !isRawId(s) && !ISSUE_PHRASE_RX.test(s)) {
      if (s.length > 10) s = s.replace(/一下|的(?=\S)/gu, "");
      return clipCols(s, 22);
    }
  }
  return "";
}

export function cleanThreadSubtitle(d, hints = null) {
  if (!d || d.id === "pet/chat") return "";
  if (d.orphan) return tr("mailbox.orphan_count", { n: d.messages.length });
  const raw = cleanTitle(d.title, d.id).replace(/\*\*([^*\n]+)\*\*|`([^`\n]+)`/g, "$1$2");
  if (!raw) return "";
  const hint = getHint(hints, d.id) || extractThreadHint(d);
  const ver = (hint && hint.versions && hint.versions[0]) || "";
  const isNoisyPrompt = raw.length > 32 || ISSUE_PHRASE_RX.test(raw) || /^(?:用户已确认|【紧急】|收到并已完成)/u.test(raw);
  if (isNoisyPrompt && (topicOf(d.id) === "release" || ver)) {
    return ver ? `v${ver}` : "";
  }
  return raw;
}

export function threadTopicBadge(t, allThreads = [], aliases = DEFAULT_ALIASES, hints = null) {
  if (!t || t.orphan || t.id === "pet/chat") return null;
  const topic = topicOf(t.id);
  const hint = getHint(hints, t.id);
  const extracted = extractTopicLabel(t, hint);
  if (topic === "release") {
    return { kind: "release", label: extracted || tr("mailbox.release") };
  }
  if (topic === "thread") {
    const seg = String(t.id || "").split("/").filter(Boolean).pop() || "";
    const label = !isRawId(seg) && seg ? clipCols(seg, 22) : extracted || tr("mailbox.topic");
    return { kind: "topic", label };
  }
  const agent = threadAgent(t, aliases);
  const sameAgentCount = (allThreads || []).filter((x) => !x.orphan && threadAgent(x, aliases) === agent).length;
  if (sameAgentCount > 1) {
    return { kind: "chat", label: extracted || tr("mailbox.topic") };
  }
  return null;
}

// details from /api/thread for every source id -> one canonical thread
export function mergeThreadDetails(details, aliases = DEFAULT_ALIASES) {
  const list = (details || []).filter(Boolean);
  if (!list.length) return null;
  const hints = {};
  for (const d of list) {
    const canon = canonicalThreadId(d.id, aliases);
    const h = extractThreadHint(d);
    if (h) hints[canon] = h;
  }
  const merged = mergeThreadList(list, aliases, hints)[0];
  const seen = new Set();
  const messages = [];
  for (const d of list) {
    for (const m of d.messages || []) {
      if (seen.has(m.message_id)) continue;
      seen.add(m.message_id);
      messages.push({ ...m, agent: canonicalAgent(m.agent || "", aliases) });
    }
  }
  messages.sort(compareMessages);
  // the merged title comes from the thread whose first request is oldest
  const oldest = [...list].sort((a, b) => firstTs(a) - firstTs(b))[0];
  return { ...merged, title: oldest.title, messages: markOrphans(messages) };
}

function firstTs(detail) {
  const req = (detail.messages || []).find((m) => m.role === "agent");
  return req ? (msgTs(req) || Infinity) : Infinity;
}

export function compareMessages(a, b) {
  return msgTs(a) - msgTs(b)
    || (a.role === "agent" ? 0 : 1) - (b.role === "agent" ? 0 : 1)
    || (a.req_id < b.req_id ? -1 : a.req_id > b.req_id ? 1 : 0)
    || (a.seq ?? -1) - (b.seq ?? -1);
}

export function markOrphans(messages) {
  const requests = new Set(messages.filter((m) => m.role === "agent").map((m) => m.req_id));
  return messages.map((m) => (m.role === "muse" && !requests.has(m.req_id)
    ? { ...m, orphan: true, agentHint: agentFromReqId(m.req_id) }
    : { ...m, orphan: false }));
}

// messages -> [{ dayKey, dayTs, groups: [{ reqId, request, preReplies, replies, ts }] }]
// A request and its replies stay together, in true chronological order.
export function groupByRequest(messages) {
  const groups = new Map();
  for (const m of messages) {
    const key = m.req_id || m.message_id;
    if (!groups.has(key)) groups.set(key, { reqId: key, request: null, preReplies: [], replies: [], ts: 0 });
    const g = groups.get(key);
    if (m.role === "agent" || m.role === "user") { g.request = m; g.ts = msgTs(m); }
    else { g.replies.push(m); if (!g.request && !g.ts) g.ts = msgTs(m); }
  }
  for (const g of groups.values()) {
    const allReplies = [...g.replies].sort(compareMessages);
    const reqT = g.request ? msgTs(g.request) : 0;
    if (g.request && reqT > 0) {
      g.preReplies = allReplies.filter((r) => msgTs(r) > 0 && msgTs(r) < reqT);
      g.replies = allReplies.filter((r) => !(msgTs(r) > 0 && msgTs(r) < reqT));
    } else {
      g.preReplies = [];
      g.replies = allReplies;
    }
    if (!g.ts) {
      const first = g.preReplies[0] || g.replies[0];
      if (first) g.ts = msgTs(first);
    }
  }
  const ordered = [...groups.values()].sort((a, b) => a.ts - b.ts || (a.reqId < b.reqId ? -1 : 1));
  const days = [];
  for (const g of ordered) {
    const key = dayKey(g.ts);
    if (!days.length || days[days.length - 1].dayKey !== key) days.push({ dayKey: key, dayTs: g.ts, groups: [] });
    days[days.length - 1].groups.push(g);
  }
  return days;
}

// ---- labels -----------------------------------------------------------
export const REQUEST_STEPS = ["delivered", "claimed", "acked", "replied"];
const STATE = {
  delivered: { get label() { return tr("mailbox.delivered"); }, tone: "neutral" },
  claimed: { get label() { return tr("mailbox.claimed"); }, tone: "blue" },
  acked: { get label() { return tr("mailbox.acked"); }, tone: "blue" },
  replied: { get label() { return tr("mailbox.replied"); }, tone: "green" },
  needs_user: { get label() { return tr("mailbox.waiting"); }, tone: "orange" },
  rejected: { get label() { return tr("mailbox.rejected"); }, tone: "red" },
};
export function stateInfo(state) {
  return STATE[state] || { label: state || tr("mailbox.unknown"), tone: "neutral" };
}
// index of the last reached step for the 4-dot tracker (needs_user = done)
export function stepIndex(state) {
  if (state === "needs_user") return 3;
  const i = REQUEST_STEPS.indexOf(state);
  return i < 0 ? -1 : i;
}

const THREAD = {
  waiting_user: { get label() { return tr("mailbox.waiting"); }, tone: "orange" },
  active: { get label() { return tr("mailbox.active"); }, tone: "blue" },
  done: { get label() { return tr("mailbox.done"); }, tone: "green" },
};
export function threadStatusInfo(status) {
  return THREAD[status] || { label: status || tr("mailbox.unknown"), tone: "neutral" };
}

export function replyInfo(msg) {
  const status = (msg.status || "").toLowerCase();
  if (!msg.final) return { label: status === "info" || !status ? tr("mailbox.intermediate") : tr("mailbox.progress"), tone: "neutral", final: false };
  if (status === "ok") return { get label() { return tr("mailbox.complete"); }, tone: "green", final: true };
  if (status === "error") return { get label() { return tr("mailbox.error"); }, tone: "red", final: true };
  if (status === "needs-user" || status === "needs_user") return { get label() { return tr("mailbox.needs_user"); }, tone: "orange", final: true };
  return { label: status || tr("mailbox.final"), tone: "neutral", final: true };
}

export function trustInfo(trust) {
  if (trust === "unread_unverified") return { get label() { return tr("mailbox.unread"); }, tone: "blue", get note() { return tr("mailbox.verify_on_read"); } };
  if (trust === "rejected_by_client") return { get label() { return tr("mailbox.quarantined"); }, tone: "red", get note() { return tr("mailbox.signature_failed"); } };
  return null;
}

const TYPES = {
  ping: "Ping", get message() { return tr("mailbox.message"); }, get question() { return tr("mailbox.question"); }, get task() { return tr("mailbox.task"); }, get enroll() { return tr("mailbox.enroll"); },
  get "publish-release"() { return tr("mailbox.release"); },
};
// Old letters keep the type they were sent with; a retired name is shown as
// its current one (same table as muse-call.ps1 and vm/muselink_id).
const RETIRED_TYPES = { "publish-voicebridge": "publish-release" };
export function typeLabel(type) {
  const t = RETIRED_TYPES[type] || type;
  return TYPES[t] || t || "";
}
export function topicLabel(topic) {
  return topic === "release" ? tr("mailbox.release") : topic === "chat" ? tr("mailbox.conversation") : tr("mailbox.topic");
}

// ---- formatting -------------------------------------------------------
const pad = (n) => String(n).padStart(2, "0");

export function dayKey(seconds) {
  if (!seconds) return "unknown";
  const d = new Date(seconds * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function dayLabel(seconds, now = Date.now() / 1000) {
  if (!seconds) return tr("mailbox.unknown_time");
  if (dayKey(seconds) === dayKey(now)) return I18N.relative(0, "day");
  if (dayKey(seconds) === dayKey(now - 86400)) return I18N.relative(-1, "day");
  return I18N.date(new Date(seconds * 1000), {year: "numeric", month: "long", day: "numeric", weekday: "short"});
}
export function clock(seconds) {
  return seconds ? I18N.date(new Date(seconds * 1000), {hour: "2-digit", minute: "2-digit"}) : "";
}
export function shortTime(seconds, now = Date.now() / 1000) {
  if (!seconds) return "";
  if (dayKey(seconds) === dayKey(now)) return clock(seconds);
  if (dayKey(seconds) === dayKey(now - 86400)) return I18N.relative(-1, "day");
  return I18N.date(new Date(seconds * 1000), now - seconds < 6 * 86400
    ? {weekday: "short"} : {month: "numeric", day: "2-digit"});
}

export function formatSize(bytes) {
  const n = Number(bytes) || 0;
  if (n <= 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let v = n, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

export function shortHash(hash) {
  return typeof hash === "string" && hash.length > 16 ? `${hash.slice(0, 8)}…${hash.slice(-6)}` : hash || "";
}

export function baseName(path) {
  if (typeof path !== "string") return "";
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

// split text around every case-insensitive occurrence of q (search marks)
export function highlightParts(text, q) {
  const s = String(text || "");
  if (!q) return [{ text: s, hit: false }];
  const out = [];
  const lower = s.toLowerCase(), needle = q.toLowerCase();
  let i = 0;
  for (;;) {
    const j = lower.indexOf(needle, i);
    if (j < 0) break;
    if (j > i) out.push({ text: s.slice(i, j), hit: false });
    out.push({ text: s.slice(j, j + needle.length), hit: true });
    i = j + needle.length;
  }
  if (i < s.length) out.push({ text: s.slice(i), hit: false });
  return out;
}
