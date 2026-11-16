// icons.js - static inline SVG (trusted constants; never mixed with data)
const svg = (body, w = 1.9) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;

export const ICONS = {
  // 通道: the link between Muse and this PC
  channel: svg('<circle cx="12" cy="12" r="1.8"/><path d="M8.2 15.8a5.4 5.4 0 0 1 0-7.6M15.8 8.2a5.4 5.4 0 0 1 0 7.6"/><path d="M5.4 18.6a9.3 9.3 0 0 1 0-13.2M18.6 5.4a9.3 9.3 0 0 1 0 13.2"/>'),
  // 收件箱: the mailbox window
  inbox: svg('<path d="M4 13l2.4-7h11.2L20 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/><path d="M4 13h4.5l1 2h5l1-2H20"/>'),
  // 会话: the Muse chat itself
  session: svg('<rect x="3.5" y="4.5" width="17" height="13" rx="3"/><path d="M8 21h8M12 17.5V21"/><path d="M8 9.5h8M8 12.5h5"/>'),
  // 跟它说: talk to the pet, wakes Muse
  talk: svg('<path d="M5 6.8A2.8 2.8 0 0 1 7.8 4h8.4A2.8 2.8 0 0 1 19 6.8v6.4a2.8 2.8 0 0 1-2.8 2.8H11l-4.2 3.5V16A2.8 2.8 0 0 1 5 13.2z"/><path d="M9 10h.01M12 10h.01M15 10h.01" stroke-width="2.6"/>'),
  done: svg('<path d="M5.5 12.5l4.2 4.2L18.5 7.8"/>', 2.6),
  close: svg('<path d="M7 7l10 10M17 7L7 17"/>', 2),
  send: svg('<path d="M12 18.5V6M6.8 11.2L12 6l5.2 5.2"/>', 2.4),
  refresh: svg('<path d="M19 12a7 7 0 1 1-2.05-4.95"/><path d="M19 4.5V8h-3.5"/>'),
};
