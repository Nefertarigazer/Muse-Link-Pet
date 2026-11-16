// One source served by the shared read-only UI server; locale comes from js_api.
export const I18N = {
  tag: "en", messages: {}, missing: new Set(),
  init(bundle, args = {}) {
    this.tag = bundle.tag; this.messages = bundle.messages || {};
    if (typeof document !== "undefined") {
      document.documentElement.lang = bundle.tag;
      document.documentElement.dir = bundle.dir || "ltr";
      if (document.documentElement.style && bundle.font) {
        document.documentElement.style.setProperty("--font", `${JSON.stringify(bundle.font)}, sans-serif`);
      }
      this.scan(document, args);
    }
    return this;
  },
  scan(root, args = {}) {
    for (const [attribute, target] of [["data-i18n", "textContent"], ["data-i18n-title", "title"],
      ["data-i18n-placeholder", "placeholder"], ["data-i18n-aria-label", "aria-label"]]) {
      root.querySelectorAll(`[${attribute}]`).forEach(el => {
        const value = this.t(el.getAttribute(attribute), args);
        if (target === "textContent") el.textContent = value; else el.setAttribute(target, value);
      });
    }
  },
  t(key, args = {}) {
    args = {brand: "MuseLink", muse: "Muse", codex: "Codex", kiro: "Kiro", workbuddy: "WorkBuddy", ...args};
    let message = this.messages[key];
    if (message === undefined) {
      if (!this.missing.has(key)) { this.missing.add(key); console.warn("Missing i18n key:", key); }
      message = key;
    }
    return format(message, args);
  },
  plural(key, n, args = {}) {
    return this.t(`${key}_${new Intl.PluralRules(this.tag).select(n)}`, { ...args, n });
  },
  date(value, options = {}) { return new Intl.DateTimeFormat(this.tag, options).format(value); },
  relative(value, unit) { return new Intl.RelativeTimeFormat(this.tag, {numeric: "auto"}).format(value, unit); },
  follow(client, changed, args = {}) {
    let stopped = false, timer;
    const refresh = async () => {
      try {
        const bundle = await client.i18n();
        if (!stopped && bundle.tag !== this.tag) {
          this.init(bundle, args);
          await changed();
        }
      } catch { /* Keep the current complete bundle during a bridge failure. */ }
      if (!stopped) timer = setTimeout(refresh, 500);
    };
    timer = setTimeout(refresh, 500);
    const stop = () => {
      stopped = true; clearTimeout(timer);
      globalThis.removeEventListener?.("pagehide", stop);
    };
    globalThis.addEventListener?.("pagehide", stop, {once: true});
    return stop;
  },
};
export function format(message, args = {}) {
  return message.replace(/\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}/g,
    (match, name) => match === "{{" ? "{" : match === "}}" ? "}" : name in args ? String(args[name]) : match);
}
export const t = (key, args) => I18N.t(key, args);
export const plural = (key, n, args) => I18N.plural(key, n, args);
