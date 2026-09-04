import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { loadPlugin, session, React, query, JSDOM, createRoot } from './harness.mjs';

async function mount(plugin, data, scope = session, area = 'session.statusStack') {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost/' });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const registered = [];
  const ctx = { register: row => registered.push(row), rest: async () => { throw new Error('No network fixture installed'); } };
  plugin.default.register(ctx);
  const client = new query.QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (data) client.setQueryData(plugin.realmQueryOptions(ctx, scope).queryKey, data);
  const Component = registered.find(row => row.area === area)?.data?.render;
  assert.equal(typeof Component, 'function');
  const root = createRoot(document.getElementById('root'));
  await React.act(async () => root.render(React.createElement(query.QueryClientProvider, { client }, React.createElement(Component, { session: scope }))));
  return { dom, client, ctx, registered, text: () => document.body.textContent, close: async () => { await React.act(async () => root.unmount()); client.clear(); dom.window.close(); } };
}

const liveRealm = { id: 'realm-a', runtime_session_id: session.runtimeSessionId, stored_session_id: session.storedSessionId, state: 'live', window_count: 2, controlled: false };

test('registered row and both badges render only their owning realm and show effective mode', async () => {
  const plugin = await loadPlugin();
  assert.equal(typeof plugin.default?.register, 'function');
  for (const area of ['session.statusStack', 'session.tileBadge', 'session.listBadge']) {
    const view = await mount(plugin, { mode: 'realm', realms: [liveRealm] }, session, area);
    assert.match(view.text(), /Realm/);
    if (area === 'session.statusStack') {
      assert.match(view.text(), /2 windows/);
      assert.match(view.text(), /live/);
      assert.match(view.text(), /Watch/);
      assert.match(view.text(), /Pop out/);
      assert.equal(view.dom.window.document.querySelectorAll('button:not(:disabled)').length, 2);
    } else {
      assert.equal(view.text(), 'Realm · 2');
      assert.equal(view.dom.window.document.querySelector('[aria-label]').getAttribute('aria-label'), 'Realm · 2 windows · live');
    }
    await view.close();
  }
  for (const mode of ['realm', 'host', 'ask']) {
    const view = await mount(plugin, { mode, realms: [] });
    assert.match(view.text().toLowerCase(), new RegExp(mode));
    assert.equal(view.dom.window.document.querySelectorAll('button').length, 0);
    await view.close();
  }
  const absent = await mount(plugin, null, null);
  assert.equal(absent.text(), '');
  await absent.close();
  const foreign = await mount(plugin, { mode: 'realm', realms: [{ ...liveRealm, stored_session_id: 'foreign' }] });
  assert.doesNotMatch(foreign.text(), /2 windows|Watch|Pop out/);
  await foreign.close();
});

test('viewer actions require current ownership and fresh tickets, and use scoped native surfaces', async () => {
  const calls = [], previews = [], viewers = [];
  const plugin = await loadPlugin({ openPreview: async options => { previews.push(options); return true; } });
  assert.equal(typeof plugin.openRealmViewer, 'function');
  // Explicit protocol test values at the native boundary; live router proof is separate.
  const urls = ['http://127.0.0.1:9100/realms/realm-a/view#ticket=first', 'http://127.0.0.1:9100/realms/realm-a/view#ticket=second'];
  const ctx = {
    rest: async (...args) => { calls.push(args); return { url: urls[calls.length - 1] }; },
    os: { openViewer: async value => { viewers.push(value); return true; } }
  };
  await plugin.openRealmViewer(ctx, session, liveRealm, 'watch');
  await plugin.openRealmViewer(ctx, session, liveRealm, 'popout');
  assert.equal(calls.length, 2);
  assert.equal(calls[0][0], '/realms/realm-a/watch');
  assert.deepEqual(calls[0][1], { method: 'POST', scope: session, body: { runtime_session_id: session.runtimeSessionId, stored_session_id: session.storedSessionId } });
  assert.equal(previews[0].url, urls[0]);
  assert.deepEqual(previews[0].session, session);
  assert.equal(viewers[0].url, urls[1]);
  assert.deepEqual(viewers[0].session, session);
  assert.match(viewers[0].id, /^realm-[a-f0-9]{64}$/);
  assert.equal(await plugin.viewerId(session, liveRealm.id), viewers[0].id);
  assert.notEqual(await plugin.viewerId({ ...session, profile: 'other' }, liveRealm.id), viewers[0].id);
  await assert.rejects(plugin.openRealmViewer(ctx, session, { ...liveRealm, stored_session_id: 'foreign' }, 'watch'), /owner/i);
  await assert.rejects(plugin.openRealmViewer(ctx, session, { ...liveRealm, state: 'stopped' }, 'watch'), /live/i);
  const unsafe = { ...ctx, rest: async () => ({ url: 'javascript:alert(1)' }) };
  await assert.rejects(plugin.openRealmViewer(unsafe, session, liveRealm, 'popout'), /URL/i);
  const stale = { ...ctx, rest: async () => ({ url: urls[0] }), os: { openViewer: async () => false } };
  await assert.rejects(plugin.openRealmViewer(stale, session, liveRealm, 'popout'), /unavailable|stale/i);
  assert.equal(calls.length, 2);
});

test('row buttons invoke viewers and expose safe failures; failed refresh removes stale live actions', async () => {
  const opened = [];
  const plugin = await loadPlugin({ openPreview: async value => { opened.push(value); return true; } });
  const view = await mount(plugin, { mode: 'realm', realms: [liveRealm] });
  view.ctx.rest = async () => ({ url: 'http://127.0.0.1:9100/realms/realm-a/view#ticket=test' });
  await React.act(async () => {
    view.dom.window.document.querySelector('button').click();
    await new Promise(resolve => setTimeout(resolve, 20));
  });
  assert.equal(opened.length, 1);
  assert.deepEqual(opened[0].session, session);
  view.ctx.rest = async () => { throw new Error('private https://example.test/#ticket=secret'); };
  await React.act(async () => {
    view.dom.window.document.querySelector('button').click();
    await new Promise(resolve => setTimeout(resolve, 20));
  });
  assert.match(view.text(), /Unable to open|Could not open/);
  assert.doesNotMatch(view.text(), /secret|example.test/);
  await React.act(async () => {
    const current = view.client.getQueryCache().find({ queryKey: plugin.realmQueryOptions(view.ctx, session).queryKey });
    current.setState({ status: 'error', error: new Error('backend offline') });
    await new Promise(resolve => setTimeout(resolve, 20));
  });
  assert.match(view.text(), /unavailable/i);
  assert.match(view.text(), /Retry/);
  assert.doesNotMatch(view.text(), /live|Watch|Pop out/);
  await view.close();
  const failed = await mount(plugin, { mode: 'realm', realms: [{ ...liveRealm, state: 'error', error: 'Compositor exited' }] });
  assert.match(failed.text(), /Compositor exited/);
  assert.equal(failed.dom.window.document.querySelectorAll('button:not(:disabled)').length, 0);
  await failed.close();
});

test('badges do not advertise stale live state and malformed owners cannot partially fall back', async () => {
  const plugin = await loadPlugin();
  for (const scope of [{ ...session, runtimeSessionId: 'x'.repeat(257) }, { ...session, storedSessionId: 12 }]) {
    assert.equal(plugin.realmQueryOptions({}, scope).enabled, false);
  }
  const view = await mount(plugin, { mode: 'realm', realms: [liveRealm] }, session, 'session.tileBadge');
  await React.act(async () => {
    view.client.getQueryCache().find({ queryKey: plugin.realmQueryOptions(view.ctx, session).queryKey }).setState({ status: 'error', error: new Error('offline') });
    await new Promise(resolve => setTimeout(resolve, 20));
  });
  assert.doesNotMatch(view.text(), /live/);
  assert.match(view.text(), /unavailable/i);
  await view.close();
});

test('native five-second polling reaches a real HTTP failure with no socket subscription', async t => {
  const requests = [];
  const server = createServer((request, response) => {
    requests.push(request.url);
    response.writeHead(503, { 'Content-Type': 'text/plain' });
    response.end('Unavailable');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const plugin = await loadPlugin();
  const view = await mount(plugin, { mode: 'realm', realms: [liveRealm] });
  t.after(() => view.close());
  view.ctx.rest = async (path, options) => {
    assert.deepEqual(options.scope, session);
    const response = await fetch(`http://127.0.0.1:${server.address().port}${path}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  };
  await React.act(async () => {
    const until = Date.now() + 10000;
    while (requests.length < 2 && Date.now() < until) await new Promise(resolve => setTimeout(resolve, 100));
    await new Promise(resolve => setTimeout(resolve, 100));
  });
  assert.ok(requests.length >= 2, 'poll plus bounded retry reached actual HTTP server');
  assert.equal(requests[0], '/realms?runtime_session_id=runtime-a&stored_session_id=stored-a');
  assert.match(view.text(), /unavailable/i);
  assert.doesNotMatch(view.text(), /live|Watch/);
});

test('remote Watch and Pop out fail before requesting any ticket or opening a surface', async () => {
  const calls = [];
  const plugin = await loadPlugin({ openPreview: async value => { calls.push(['preview', value]); return true; } });
  const ctx = {
    rest: async (...args) => { calls.push(['rest', args]); throw new Error('Ticket endpoint must not be reached'); },
    os: { openViewer: async value => { calls.push(['viewer', value]); return true; } }
  };
  for (const connectionId of ['ssh-server', 'oauth-server', 'LOCAL', 'local ']) {
    const remote = { ...session, connectionId };
    for (const target of ['watch', 'popout']) {
      await assert.rejects(plugin.openRealmViewer(ctx, remote, liveRealm, target), /remote.*unsupported.*tunnel.*local/i);
    }
  }
  assert.deepEqual(calls, []);
});

test('remote status keeps counts but disables viewers with actionable tunnel guidance', async t => {
  const plugin = await loadPlugin();
  const remote = { ...session, connectionId: 'ssh-server' };
  const view = await mount(plugin, { mode: 'realm', realms: [liveRealm] }, remote);
  t.after(() => view.close());
  assert.match(view.text(), /2 windows/);
  assert.match(view.text(), /remote.*unsupported.*tunnel.*local/i);
  assert.match(view.text(), /SSH is not enough/);
  assert.equal(view.dom.window.document.querySelectorAll('button').length, 2);
  assert.equal(view.dom.window.document.querySelectorAll('button:not(:disabled)').length, 0);
});

test('session queries carry explicit owner scope, never focused identity, and poll natively', async () => {
  const plugin = await loadPlugin({ state: new Proxy({}, { get() { throw new Error('Focused identity is forbidden'); } }) });
  assert.equal(typeof plugin.realmQueryOptions, 'function');
  const calls = [];
  const ctx = { rest: async (...args) => { calls.push(args); return { mode: 'realm', realms: [] }; } };
  const options = plugin.realmQueryOptions(ctx, session);
  assert.equal(options.enabled, true);
  assert.equal(options.refetchInterval, 5000);
  await options.queryFn();
  assert.equal(calls[0][0], '/realms?runtime_session_id=runtime-a&stored_session_id=stored-a');
  assert.deepEqual(calls[0][1].scope, session);
  assert.notDeepEqual(options.queryKey, plugin.realmQueryOptions(ctx, { ...session, profile: 'other' }).queryKey);
  assert.notDeepEqual(options.queryKey, plugin.realmQueryOptions(ctx, { ...session, connectionId: 'remote' }).queryKey);
  assert.notDeepEqual(options.queryKey, plugin.realmQueryOptions(ctx, { ...session, runtimeSessionId: 'runtime-b' }).queryKey);
  const list = plugin.realmQueryOptions(ctx, { ...session, runtimeSessionId: null });
  await list.queryFn();
  assert.equal(calls[1][0], '/realms?stored_session_id=stored-a');
  for (const invalid of [null, {}, { ...session, profile: '' }, { ...session, connectionId: '' }, { ...session, runtimeSessionId: null, storedSessionId: null }]) {
    assert.equal(plugin.realmQueryOptions(ctx, invalid).enabled, false);
    await assert.rejects(plugin.realmQueryOptions(ctx, invalid).queryFn, /owner/i);
  }
  assert.equal(calls.length, 2);
});
