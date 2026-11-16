import assert from 'node:assert/strict';
import fs from 'node:fs';
import { connect, METHODS } from '../../pc/pet/ui/js/api.js';

const contract = JSON.parse(fs.readFileSync(new URL('../../pc/pet/ui/fixtures/contract.json', import.meta.url), 'utf8'));
assert.deepEqual([...METHODS].sort(), [...contract.methods].sort());
const languages = [{tag: 'en', name: 'English'}];
const bundle = {tag: 'en', dir: 'ltr', font: 'Segoe UI', messages: {}};
const calls = [];
globalThis.location = {search: ''};
globalThis.window = {pywebview: {api: {
  languages: async (...args) => { calls.push(['languages', ...args]); return {ok: true, data: languages}; },
  set_language: async (...args) => { calls.push(['set_language', ...args]); return {ok: true, data: bundle}; },
  exit: async (...args) => { calls.push(['exit', ...args]); return {ok: true, data: null}; },
}}};
try {
  const client = await connect();
  assert.deepEqual(await client.languages(), languages);
  assert.deepEqual(await client.set_language('en'), bundle);
  assert.equal(await client.exit(), null);
  assert.deepEqual(calls, [['languages'], ['set_language', 'en'], ['exit']]);
  window.pywebview.api.set_language = async () => ({ok: false, error: 'Invalid language', code: 'invalid_input'});
  await assert.rejects(client.set_language('../en'), {message: 'Invalid language', code: 'invalid_input'});
} finally {
  delete globalThis.location;
  delete globalThis.window;
}
console.log('OK JS: language and exit bridge wrappers');
