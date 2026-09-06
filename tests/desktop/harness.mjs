// Execute the uncompiled external plugin with real React + React Query.
// Only the native host/OS boundary is injected; no live renderer is modified.
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import vm from 'node:vm';
// Resolve local test dependencies, or an explicitly selected dependency installation.
// This test-only override does not select or modify a running Hermes backend.
export const requireHost = createRequire(process.env.REALMS_TEST_DESKTOP_PACKAGE || import.meta.url);
export const { JSDOM } = requireHost('jsdom');
const browser = new JSDOM('', { url: 'http://localhost/' });
globalThis.window = browser.window;
globalThis.document = browser.window.document;
export const React = requireHost('react');
export const query = requireHost('@tanstack/react-query');
export const { createRoot } = requireHost('react-dom/client');
export async function loadPlugin(host = {}) {
  let code;
  try { code = await readFile(new URL('../../desktop/plugin.js', import.meta.url), 'utf8'); }
  catch (error) { if (error.code === 'ENOENT') return {}; throw error; }
  const sdk = { ...query, host, SESSION_AREAS: { statusStack: 'session.statusStack', tileBadge: 'session.tileBadge', listBadge: 'session.listBadge' },
    Button: ({ size, variant, ...props }) => React.createElement('button', props),
    Badge: ({ size, variant, ...props }) => React.createElement('span', props) };
  const exports = { '@hermes/plugin-sdk': sdk, react: React, 'react/jsx-runtime': requireHost('react/jsx-runtime') };
  const module = new vm.SourceTextModule(code);
  await module.link(async (name) => {
    if (!exports[name]) throw new Error(`Unsupported plugin import: ${name}`);
    return new vm.SyntheticModule(Object.keys(exports[name]), function () {
      for (const [key, value] of Object.entries(exports[name])) this.setExport(key, value);
    });
  });
  await module.evaluate();
  return module.namespace;
}
export const session = Object.freeze({ runtimeSessionId: 'runtime-a', storedSessionId: 'stored-a', profile: 'coder', connectionId: 'local' });
