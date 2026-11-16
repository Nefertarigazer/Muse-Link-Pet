// markdown.js - safe, pure-DOM Markdown renderer for mailbox bubbles.
// Untrusted agent/Muse text is ONLY ever inserted via createTextNode / safe
// https?:// href attributes; innerHTML is never used.

function safeUrl(raw) {
  try {
    const u = new URL(String(raw || "").trim());
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}

// Parse inline Markdown into a tree of plain descriptors (testable under Node).
export function parseInline(text) {
  const s = String(text || "");
  const out = [];
  let i = 0;
  const pushText = (t) => {
    if (!t) return;
    const prev = out[out.length - 1];
    if (prev && prev.type === "text") prev.text += t;
    else out.push({ type: "text", text: t });
  };
  while (i < s.length) {
    // 1. Inline code `...`
    if (s[i] === "`") {
      const end = s.indexOf("`", i + 1);
      if (end > i + 1) {
        out.push({ type: "code", text: s.slice(i + 1, end) });
        i = end + 1;
        continue;
      }
    }
    // 2. Link [label](url)
    if (s[i] === "[") {
      const closeBracket = s.indexOf("]", i + 1);
      if (closeBracket > i + 1 && s[closeBracket + 1] === "(") {
        const closeParen = s.indexOf(")", closeBracket + 2);
        if (closeParen > closeBracket + 2) {
          const href = safeUrl(s.slice(closeBracket + 2, closeParen));
          if (href) {
            out.push({ type: "link", href, children: parseInline(s.slice(i + 1, closeBracket)) });
            i = closeParen + 1;
            continue;
          }
        }
      }
    }
    // 3. Bold **...** or __...__
    if ((s[i] === "*" && s[i + 1] === "*") || (s[i] === "_" && s[i + 1] === "_")) {
      const mark = s.slice(i, i + 2);
      const end = s.indexOf(mark, i + 2);
      if (end > i + 2) {
        const inner = s.slice(i + 2, end);
        if (!/^\s|\s$/.test(inner)) {
          out.push({ type: "bold", children: parseInline(inner) });
          i = end + 2;
          continue;
        }
      }
    }
    // 4. Strikethrough ~~...~~
    if (s[i] === "~" && s[i + 1] === "~") {
      const end = s.indexOf("~~", i + 2);
      if (end > i + 2) {
        const inner = s.slice(i + 2, end);
        if (!/^\s|\s$/.test(inner)) {
          out.push({ type: "del", children: parseInline(inner) });
          i = end + 2;
          continue;
        }
      }
    }
    // 5. Italic *...* (single asterisk, non-whitespace boundaries)
    if (s[i] === "*" && s[i + 1] !== "*") {
      const end = s.indexOf("*", i + 1);
      if (end > i + 1) {
        const inner = s.slice(i + 1, end);
        if (!/^\s|\s$/.test(inner) && !inner.includes("\n")) {
          out.push({ type: "italic", children: parseInline(inner) });
          i = end + 1;
          continue;
        }
      }
    }
    pushText(s[i]);
    i += 1;
  }
  return out;
}

// Parse block-level Markdown into block descriptors (testable under Node).
export function parseBlocks(text) {
  const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let i = 0;
  const isFence = (l) => /^\s*```/.test(l);
  const isHr = (l) => /^\s*(?:---+|___+|\*\*\*+)\s*$/.test(l);
  const headingMatch = (l) => l.match(/^\s{0,3}(#{1,4})\s+(.+?)\s*#*$/);
  const quoteMatch = (l) => l.match(/^\s*>\s?(.*)$/);
  const ulMatch = (l) => l.match(/^\s*[-*•]\s+(.+)$/);
  const olMatch = (l) => l.match(/^\s*(\d+)\.\s+(.+)$/);

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }

    if (isFence(line)) {
      const lang = line.replace(/^\s*```/, "").trim();
      const codeLines = [];
      i += 1;
      while (i < lines.length && !isFence(lines[i])) {
        codeLines.push(lines[i]);
        i += 1;
      }
      if (i < lines.length && isFence(lines[i])) i += 1;
      blocks.push({ type: "code", lang, text: codeLines.join("\n") });
      continue;
    }

    if (isHr(line)) {
      blocks.push({ type: "hr" });
      i += 1;
      continue;
    }

    const hm = headingMatch(line);
    if (hm) {
      blocks.push({ type: "heading", level: hm[1].length, children: parseInline(hm[2]) });
      i += 1;
      continue;
    }

    if (quoteMatch(line)) {
      const qLines = [];
      while (i < lines.length) {
        const qm = quoteMatch(lines[i]);
        if (!qm) break;
        qLines.push(qm[1]);
        i += 1;
      }
      blocks.push({ type: "quote", lines: qLines.map(parseInline) });
      continue;
    }

    if (ulMatch(line)) {
      const items = [];
      while (i < lines.length) {
        const um = ulMatch(lines[i]);
        if (!um) break;
        items.push(parseInline(um[1]));
        i += 1;
      }
      blocks.push({ type: "ul", items });
      continue;
    }

    if (olMatch(line)) {
      const items = [];
      const start = Number(olMatch(line)[1]) || 1;
      while (i < lines.length) {
        const om = olMatch(lines[i]);
        if (!om) break;
        items.push(parseInline(om[2]));
        i += 1;
      }
      blocks.push({ type: "ol", start, items });
      continue;
    }

    const pLines = [];
    while (
      i < lines.length && lines[i].trim() && !isFence(lines[i]) && !isHr(lines[i])
      && !headingMatch(lines[i]) && !quoteMatch(lines[i]) && !ulMatch(lines[i]) && !olMatch(lines[i])
    ) {
      pLines.push(lines[i]);
      i += 1;
    }
    blocks.push({ type: "paragraph", lines: pLines.map(parseInline) });
  }
  return blocks;
}

function renderNodes(nodes, doc) {
  const frag = doc.createDocumentFragment();
  for (const n of nodes) {
    if (n.type === "text") {
      frag.append(doc.createTextNode(n.text));
    } else if (n.type === "code") {
      const el = doc.createElement("code");
      el.className = "md-code";
      el.textContent = n.text;
      frag.append(el);
    } else if (n.type === "bold") {
      const el = doc.createElement("strong");
      el.append(renderNodes(n.children, doc));
      frag.append(el);
    } else if (n.type === "italic") {
      const el = doc.createElement("em");
      el.append(renderNodes(n.children, doc));
      frag.append(el);
    } else if (n.type === "del") {
      const el = doc.createElement("del");
      el.append(renderNodes(n.children, doc));
      frag.append(el);
    } else if (n.type === "link") {
      const el = doc.createElement("a");
      el.className = "md-link";
      el.href = n.href;
      el.target = "_blank";
      el.rel = "noopener noreferrer";
      el.append(renderNodes(n.children, doc));
      frag.append(el);
    }
  }
  return frag;
}

function appendLines(el, lineNodes, doc) {
  lineNodes.forEach((nodes, idx) => {
    if (idx > 0) el.append(doc.createElement("br"));
    el.append(renderNodes(nodes, doc));
  });
}

export function renderMarkdown(text, doc = document) {
  const wrap = doc.createElement("div");
  wrap.className = "md-body";
  const blocks = parseBlocks(text);
  for (const b of blocks) {
    if (b.type === "code") {
      const pre = doc.createElement("pre");
      pre.className = "md-pre";
      const code = doc.createElement("code");
      code.textContent = b.text;
      pre.append(code);
      wrap.append(pre);
    } else if (b.type === "hr") {
      const hr = doc.createElement("hr");
      hr.className = "md-hr";
      wrap.append(hr);
    } else if (b.type === "heading") {
      const h = doc.createElement("div");
      h.className = `md-h md-h${b.level}`;
      const strong = doc.createElement("strong");
      strong.append(renderNodes(b.children, doc));
      h.append(strong);
      wrap.append(h);
    } else if (b.type === "quote") {
      const q = doc.createElement("blockquote");
      q.className = "md-quote";
      appendLines(q, b.lines, doc);
      wrap.append(q);
    } else if (b.type === "ul" || b.type === "ol") {
      const list = doc.createElement(b.type);
      list.className = "md-list";
      if (b.type === "ol" && b.start !== 1) list.start = b.start;
      for (const item of b.items) {
        const li = doc.createElement("li");
        li.append(renderNodes(item, doc));
        list.append(li);
      }
      wrap.append(list);
    } else {
      const p = doc.createElement("p");
      p.className = "md-p";
      appendLines(p, b.lines, doc);
      wrap.append(p);
    }
  }
  return wrap;
}
