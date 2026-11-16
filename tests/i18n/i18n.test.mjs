import assert from 'node:assert/strict';
import fs from 'node:fs';
import { I18N, format } from '../../pc/i18n/i18n.js';

const vectors = JSON.parse(fs.readFileSync(new URL('../../spec/test-vectors/i18n.json', import.meta.url), 'utf8'));
for (const row of vectors.format.cases) {
  I18N.init({tag: row.tag, dir: 'ltr', messages: {...vectors.format.packs.en, ...vectors.format.packs[row.tag]}});
  const value = 'n' in row ? I18N.plural(row.key, row.n, row.args) : I18N.t(row.key, row.args);
  assert.equal(value, row.expected, JSON.stringify(row));
}
assert.equal(format('{{{name}}}', {name: '{Muse}'}), '{{Muse}}');
// Verify the scanner sets actual document language/direction and attributes.
const elements = Object.fromEntries(['data-i18n', 'data-i18n-title', 'data-i18n-placeholder'].map(attr => [attr,
  {getAttribute: () => 'popup.greeting', setAttribute(name, value) {this[name] = value;}}]));
globalThis.document = {documentElement: {}, querySelectorAll: selector => [elements[selector.slice(1,-1)]].filter(Boolean)};
I18N.init({tag: 'zh-Hant', dir: 'rtl', messages: {'popup.greeting': 'Hello'}});
assert.equal(document.documentElement.lang, 'zh-Hant');
assert.equal(document.documentElement.dir, 'rtl');
assert.equal(elements['data-i18n'].textContent, 'Hello');
assert.equal(elements['data-i18n-title'].title, 'Hello');
assert.equal(elements['data-i18n-placeholder'].placeholder, 'Hello');
delete globalThis.document;

// Drive follow() without wall-clock waits or a browser.
const saved = Object.fromEntries(['setTimeout', 'clearTimeout', 'addEventListener', 'removeEventListener']
  .map(name => [name, globalThis[name]]));
const timers = new Map(), listeners = new Map();
let timerId = 0;
globalThis.setTimeout = (fn, ms) => { assert.equal(ms, 500); timers.set(++timerId, fn); return timerId; };
globalThis.clearTimeout = id => timers.delete(id);
globalThis.addEventListener = (event, fn) => listeners.set(event, fn);
globalThis.removeEventListener = (event, fn) => { if (listeners.get(event) === fn) listeners.delete(event); };
const tick = () => {
  assert.equal(timers.size, 1);
  const [id, fn] = timers.entries().next().value;
  timers.delete(id);
  return fn();
};
try {
  I18N.init({tag: 'en', messages: {greeting: 'Hello'}});
  let bundle = {tag: 'en', messages: {greeting: 'Hello'}}, changes = 0, failure = false;
  const stop = I18N.follow({i18n: async () => {
    if (failure) throw new Error('bridge unavailable');
    return bundle;
  }}, async () => { changes++; });
  await tick();
  assert.equal(changes, 0);
  bundle = {tag: 'zh-Hant', messages: {greeting: '你好'}};
  await tick();
  assert.equal(I18N.tag, 'zh-Hant');
  assert.equal(I18N.t('greeting'), '你好');
  assert.equal(changes, 1);
  failure = true;
  await tick();
  assert.equal(I18N.tag, 'zh-Hant');
  assert.equal(changes, 1);
  failure = false;
  bundle = {tag: 'en', messages: {greeting: 'Hello again'}};
  await tick();
  assert.equal(I18N.t('greeting'), 'Hello again');
  assert.equal(changes, 2);
  stop();
  stop();
  assert.equal(timers.size, 0);
  assert.equal(listeners.size, 0);

  let finish;
  I18N.follow({i18n: () => new Promise(resolve => { finish = resolve; })}, () => { changes++; });
  const pending = tick();
  assert.equal(timers.size, 0, 'no overlapping requests while the bridge is pending');
  listeners.get('pagehide')();
  finish({tag: 'zh-Hans', messages: {greeting: 'late response'}});
  await pending;
  assert.equal(I18N.tag, 'en', 'a stopped follower ignores in-flight responses');
  assert.equal(changes, 2);
  assert.equal(timers.size, 0);
  assert.equal(listeners.size, 0);
} finally {
  Object.assign(globalThis, saved);
}
console.log(`OK JS: ${vectors.format.cases.length} format vectors, DOM scanner and language following`);
