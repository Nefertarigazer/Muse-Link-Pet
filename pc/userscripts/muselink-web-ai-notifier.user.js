// ==UserScript==
// @name         MuseLink web-AI completion notifier
// @namespace    muselink
// @version      2.0.0
// @description  When a ChatGPT / Gemini / Claude reply finishes, tell the local MuseLink web-bridge (start-wait.sh --kind web).
// @match        https://chatgpt.com/*
// @match        https://gemini.google.com/*
// @match        https://claude.ai/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// ==/UserScript==
//
// Reference implementation. Site DOMs change often; the "is generating"
// probe below is the part to adjust. The bridge only listens while a
// `start-wait.sh --kind web` wait is armed, so every request outside that
// window simply fails and is ignored.
//
// Protocol (pc/web-bridge.ps1): GET /nonce, then POST /done/<nonce> with
// {"text", "site", "url", "title"}. The nonce cannot be read by ordinary web
// pages (no CORS header); GM_xmlhttpRequest can.
(function () {
  'use strict';
  const PORT = 18923;
  const SETTLE_MS = 4000;          // reply must stay unchanged this long
  const POLL_MS = 1000;

  function isGenerating() {
    return !!document.querySelector(
      '[data-testid="stop-button"], button[aria-label*="Stop"], button[aria-label*="停止"], ' +
      'button[aria-label*="Stop response"], .stop-icon'
    );
  }

  function lastReplyText() {
    const sel = [
      '[data-message-author-role="assistant"]',      // chatgpt
      'model-response',                              // gemini
      '[data-is-streaming]',                         // claude
    ];
    for (const s of sel) {
      const nodes = document.querySelectorAll(s);
      if (nodes.length) return (nodes[nodes.length - 1].innerText || '').trim();
    }
    return '';
  }

  function post(text) {
    GM_xmlhttpRequest({
      method: 'GET', url: `http://127.0.0.1:${PORT}/nonce`, timeout: 3000,
      onload: (r) => {
        let nonce = '';
        try { nonce = JSON.parse(r.responseText).nonce || ''; } catch (e) { return; }
        if (!nonce) return;
        GM_xmlhttpRequest({
          method: 'POST', url: `http://127.0.0.1:${PORT}/done/${nonce}`, timeout: 5000,
          headers: { 'Content-Type': 'application/json' },
          data: JSON.stringify({ text: text.slice(0, 200000), site: location.host, url: location.href, title: document.title }),
        });
      },
    });
  }

  let sawGenerating = false, lastText = '', stableSince = 0;
  setInterval(() => {
    if (isGenerating()) { sawGenerating = true; stableSince = 0; return; }
    if (!sawGenerating) return;
    const t = lastReplyText();
    if (t !== lastText) { lastText = t; stableSince = Date.now(); return; }
    if (stableSince && Date.now() - stableSince >= SETTLE_MS && t) {
      sawGenerating = false; stableSince = 0;
      post(t);
    }
  }, POLL_MS);
})();
