import { host, SESSION_AREAS, useQuery, Button, Badge, ConfirmDialog, DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from '@hermes/plugin-sdk';
import { useState, useRef, useEffect } from 'react';
import { jsx, jsxs } from 'react/jsx-runtime';
import { RealmSetupControls, setupLocales, useRealmText } from './setup-controls.js';
import { RealmPermissionControls } from './permission-controls.js';
import { RealmStorageSettings } from './storage-controls.js';
import * as sdk from '@hermes/plugin-sdk';

// Host @hermes/shared LOCAL_CONNECTION_ID contract. External plugins can only
// import the plugin SDK, not @hermes/shared; do not infer local from a URL.
const LOCAL_CONNECTION_ID = 'local';
const REMOTE_VIEWER_UNSUPPORTED = 'Remote viewers are unsupported: no viewer tunnel is available. Open Hermes on the realm machine using a local connection; forwarding only the REST endpoint over SSH is not enough.';
const validId = value => typeof value === 'string' && value.length > 0 && value.length <= 256;
const validSession = session => !!session && validId(session.connectionId) && validId(session.profile)
  && [session.runtimeSessionId, session.storedSessionId].every(id => id == null || validId(id))
  && (validId(session.runtimeSessionId) || validId(session.storedSessionId));
const ownerBody = session => Object.fromEntries([
  ['runtime_session_id', session.runtimeSessionId], ['stored_session_id', session.storedSessionId]
].filter(([, value]) => validId(value)));

export function realmQueryOptions(ctx, session) {
  const enabled = validSession(session);
  return {
    queryKey: ['hermes-realms', session?.connectionId, session?.profile, session?.storedSessionId, session?.runtimeSessionId],
    enabled,
    queryFn: async () => {
      if (!enabled) throw new Error('Realm session owner is unavailable');
      return ctx.rest(`/realms?${new URLSearchParams(ownerBody(session))}`, { scope: session });
    },
    refetchInterval: 5000,
    staleTime: 3000,
    retry: 1,
    gcTime: 60000
  };
}

function ownedRealms(data, session) {
  return (Array.isArray(data?.realms) ? data.realms : []).filter(realm =>
    /^[A-Za-z0-9_-]{1,128}$/.test(realm.id || '')
    && (!session.runtimeSessionId || realm.runtime_session_id === session.runtimeSessionId)
    && (!session.storedSessionId || realm.stored_session_id === session.storedSessionId));
}


// A VM realm is a different machine, not a different window group, so the user
// must be able to tell which one a session is on at a glance.
const KIND_NAMES = { realm: 'Realm', 'omarchy-vm': 'Omarchy VM' };
const kindName = kind => KIND_NAMES[kind] || 'Realm';
const gib = bytes => Number.isFinite(bytes) && bytes > 0 ? `${(bytes / 1073741824).toFixed(1)} GB` : null;
const stateLabel = (state, t) => ({ stopped: t('stoppedState'), 'recovery-required': t('recoveryState'), cleanup_failed: t('cleanupState'), deleting: t('deletingState') })[state]
  || (['starting', 'live', 'stopping', 'error'].includes(state) ? state : 'unknown');
export function realmLabel(realm, t = key => setupLocales.en[key]) {
  const name = kindName(realm.kind);
  const state = stateLabel(realm.state, t);
  const control = realm.controlled ? ' · user control' : '';
  if (realm.state !== 'live') return `${name} · ${state}${control}`;
  if (realm.kind === 'omarchy-vm') {
    // Cost is what the user actually wants on hover: a guest holds its whole
    // -m figure in host RAM for as long as it runs.
    const detail = [gib(realm.stats?.memory_bytes) && `${gib(realm.stats.memory_bytes)} RAM`,
      gib(realm.stats?.disk_bytes) && `${gib(realm.stats.disk_bytes)} disk`,
      realm.network === false && 'no network'].filter(Boolean).join(' · ');
    return `${name} · ${state}${detail ? ` · ${detail}` : ''}${control}`;
  }
  const count = Number.isSafeInteger(realm.window_count) && realm.window_count >= 0 ? realm.window_count : null;
  const windows = count === null ? 'windows unknown' : `${count} ${count === 1 ? 'window' : 'windows'}`;
  return `${name} · ${windows} · ${state}${control}`;
}

export async function viewerId(session, realmId) {
  const identity = new TextEncoder().encode(JSON.stringify([session.connectionId, session.profile, realmId]));
  const digest = await crypto.subtle.digest('SHA-256', identity);
  return `realm-${Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('')}`;
}

export async function openRealmViewer(ctx, session, realm, target, isCurrent = () => true, isEnabled = () => true) {
  if (!validSession(session) || !ownedRealms({ realms: [realm] }, session).length) throw new Error('Realm session owner is unavailable');
  if (realm.state !== 'live') throw new Error('Realm is not live');
  if (!['watch', 'popout'].includes(target)) throw new Error('Unsupported viewer surface');
  if (session.connectionId !== LOCAL_CONNECTION_ID) throw new Error(REMOTE_VIEWER_UNSUPPORTED);
  const scope = Object.freeze({ ...session });
  const path = `/realms/${encodeURIComponent(realm.id)}`;
  const reply = await ctx.rest(`${path}/watch`, { method: 'POST', body: ownerBody(scope), scope });
  let url;
  try { url = new URL(reply.url); } catch { throw new Error('Invalid viewer URL'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || /[\s\\]/.test(reply.url)) throw new Error('Invalid viewer URL');
  const token = new URLSearchParams(url.hash.slice(1)).get('ticket');
  if (!token || !/^[A-Za-z0-9_-]{40,128}$/.test(token)) throw new Error('Invalid viewer authorization');
  const onKeepAlive = async () => {
    if (!isEnabled()) throw new Error('Viewer plugin is disabled');
    const result = await ctx.rest(`${path}/renew`, {
      method: 'POST', body: { ...ownerBody(scope), viewer_token: token }, scope
    });
    if (result?.renewed !== true) throw new Error('Viewer authorization could not be renewed');
  };
  const id = await viewerId(scope, realm.id);
  if (!isCurrent()) throw new Error('Realm owner changed before viewer opened');
  const label = `${kindName(realm.kind)} · ${scope.profile}`;
  const opened = target === 'watch'
    ? await host.openPreview?.({ url: reply.url, label, session: scope, onKeepAlive })
    : await ctx.os?.openViewer?.({ id, url: reply.url, title: label.slice(0, 120), session: scope, onKeepAlive });
  if (!opened) throw new Error('Viewer unavailable or session owner is stale');
}

export default {
  id: 'hermes-realms',
  defaultEnabled: false,
  name: 'Realms',
  description: 'Desktop viewing controls only. Enable hermes-realms under Agent plugins for each profile that should use private desktops.',
  register(ctx) {
    let enabled = true;
    ctx.onDispose?.(() => { enabled = false; });
    ctx.i18n?.register(setupLocales);
    // Older Desktop hosts retain their existing session controls without this slot.
    if (sdk.PLUGIN_SETTINGS_AREA) ctx.register({ id: 'settings', area: sdk.PLUGIN_SETTINGS_AREA,
      data: { render: ({ scope }) => jsx(RealmStorageSettings, { ctx, scope }) } });
    function StatusRow({ session }) {
      const t = useRealmText();
      const result = useQuery(realmQueryOptions(ctx, session));
      const ownerKey = JSON.stringify(realmQueryOptions(ctx, session).queryKey);
      const currentOwner = useRef(ownerKey);
      const deleteEpoch = useRef(0);
      if (currentOwner.current !== ownerKey) deleteEpoch.current += 1;
      currentOwner.current = ownerKey;
      const [deleteReview, setDeleteReview] = useState(null);
      useEffect(() => { setDeleteReview(null); }, [ownerKey]);
      const mounted = useRef(true);
      const pending = useRef(new Set());
      const [action, setAction] = useState(null);
      useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
      async function runAction(operation, failureText) {
        if (pending.current.has(ownerKey)) return;
        pending.current.add(ownerKey);
        const current = () => mounted.current && currentOwner.current === ownerKey;
        setAction({ ownerKey, pending: true });
        try {
          await operation(current);
          if (current()) setAction(null);
        } catch {
          // Native/REST exceptions can include ticket URLs; never render/log them.
          if (current()) setAction({ ownerKey, error: failureText });
        } finally { pending.current.delete(ownerKey); }
      }
      function open(realm, target) {
        return runAction(current => openRealmViewer(ctx, session, realm, target, current, () => enabled),
          'Unable to open viewer. Refresh the realm and try again; check viewer access for remote connections.');
      }
      function manage(targetAction) {
        const scope = Object.freeze({ ...session });
        return runAction(async current => {
          await ctx.rest('/realms/session/action', { method: 'POST', body: { ...ownerBody(scope), action: targetAction }, scope });
          if (current()) await result.refetch();
        }, t('actionError'));
      }
      function prepareDelete(realm) {
        const scope = Object.freeze({ ...session });
        const rest = ctx.rest.bind(ctx);
        const epoch = deleteEpoch.current;
        return runAction(async current => {
          const value = await rest(`/realms/${realm.id}/delete/prepare`, {
            method: 'POST', body: ownerBody(scope), scope
          });
          if (value?.realm_id !== realm.id || value.kind !== (realm.kind || 'realm')
              || !['stopped', 'deleting'].includes(value.state)
              || typeof value.consent !== 'string' || !value.consent.length
              || !Array.isArray(value.details) || !value.details.every(detail => typeof detail === 'string')) throw new Error();
          if (current() && deleteEpoch.current === epoch) setDeleteReview({ value, scope, rest, ownerKey, epoch });
        }, t('deleteError'));
      }
      async function confirmDelete() {
        const review = deleteReview;
        if (!mounted.current || !review || review.invalid || currentOwner.current !== review.ownerKey
            || deleteEpoch.current !== review.epoch || pending.current.has(review.ownerKey)) throw new Error(t('deleteError'));
        pending.current.add(review.ownerKey);
        try {
          const { value, scope, rest } = review;
          const deleted = await rest(`/realms/${value.realm_id}/delete/confirm`, {
            method: 'POST', body: { ...ownerBody(scope), consent: value.consent }, scope
          });
          if (deleted?.deleted !== true || deleted.realm_id !== value.realm_id) throw new Error();
          if (mounted.current && currentOwner.current === review.ownerKey && deleteEpoch.current === review.epoch) {
            const refreshed = await result.refetch();
            if (refreshed.isError) setAction({ ownerKey: review.ownerKey, deleted: value.realm_id, error: t('deleteRefreshError') });
          }
        } catch {
          // Transport failure is uncertain, never permission for automatic replay.
          if (mounted.current) setDeleteReview(current => current === review ? { ...review, invalid: true } : current);
          throw new Error(t('deleteError'));
        } finally { pending.current.delete(review.ownerKey); }
      }
      if (!validSession(session)) return null;
      const realms = ownedRealms(result.data, session);
      // Older runtimes omit requested; retain their manual setup surface.
      if (!realms.length && result.data?.requested === false && !result.data?.setup_job) return null;
      if (result.isError && realms.length) return jsxs('div', { role: 'status', style: { fontSize: 12 }, children: [
        action?.ownerKey === ownerKey && action.deleted ? action.error : 'Realm status unavailable · ',
        jsx(Button, { size: 'micro', variant: 'ghost', disabled: result.isFetching, onClick: () => void result.refetch(), children: 'Retry' })
      ] });
      const mode = result.data?.mode;

      const needsPermission = result.data?.permission && result.data.permission.state !== 'optional';
      const controls = needsPermission ? jsx(RealmPermissionControls, {
        ctx, session, data: result.data, refresh: result.refetch
      }, ownerKey) : ['realm', 'host', 'ask'].includes(mode) ? jsx(RealmSetupControls, {
        ctx, session, data: result.data, refresh: result.refetch
      }, ownerKey) : null;
      if (!realms.length) return controls;
      return jsxs('div', { 'data-realms-status': '', style: { display: 'flex', flexDirection: 'column', gap: 4 }, children: [
        controls,
        deleteReview?.ownerKey === ownerKey && deleteReview.epoch === deleteEpoch.current && jsx(ConfirmDialog, {
          open: true, destructive: true, title: t('deleteTitle'), confirmLabel: t('deleteConfirm'),
          onClose: () => setDeleteReview(current => current === deleteReview ? null : current), onConfirm: confirmDelete,
          description: jsxs('span', { className: 'flex flex-col gap-3', children: [
            jsx('span', { children: t('deleteWarning') }),
            jsx('span', { children: `${deleteReview.scope.connectionId} · ${deleteReview.scope.profile} · ${kindName(deleteReview.value.kind)}` }),
            ...deleteReview.value.details.map((detail, index) => jsx('span', { children: detail }, index))
          ] })
        }),

        session.connectionId !== LOCAL_CONNECTION_ID ? jsx('span', { role: 'note', children: REMOTE_VIEWER_UNSUPPORTED }) : null,
        action?.ownerKey === ownerKey && action.error && !action.deleted ? jsx('span', { role: 'alert', children: action.error }) : null,
        ...realms.map(realm => jsxs('div', { style: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, fontSize: 12 }, children: [
          jsx('span', { children: realmLabel(realm, t) }),
          jsx(Button, { size: 'micro', variant: 'ghost', disabled: session.connectionId !== LOCAL_CONNECTION_ID || realm.state !== 'live' || (action?.ownerKey === ownerKey && action.pending), onClick: () => void open(realm, 'watch'), children: 'Watch' }),
          result.data?.requested === undefined ? jsx(Button, { size: 'micro', variant: 'ghost', disabled: session.connectionId !== LOCAL_CONNECTION_ID || realm.state !== 'live' || (action?.ownerKey === ownerKey && action.pending), onClick: () => void open(realm, 'popout'), children: 'Pop out' })
            : jsxs(DropdownMenu, { children: [
              jsx(DropdownMenuTrigger, { asChild: true, children: jsx(Button, { size: 'micro', variant: 'ghost', 'aria-label': t('manage'), disabled: action?.ownerKey === ownerKey && action.pending, children: '⋯' }) }),
              jsxs(DropdownMenuContent, { align: 'end', children: [
                jsx(DropdownMenuItem, { disabled: session.connectionId !== LOCAL_CONNECTION_ID || realm.state !== 'live', onSelect: () => void open(realm, 'popout'), children: t('popout') }),
                (realm.kind || 'realm') === (result.data?.kind || 'realm') && jsx(DropdownMenuItem, { disabled: realm.state !== 'live', onSelect: () => void manage('stop'), children: t('stopTesting') }),
                (realm.kind || 'realm') === (result.data?.kind || 'realm') && jsx(DropdownMenuItem, { disabled: mode !== 'realm', onSelect: () => void manage('off'), children: t('disableTesting') }),
                jsx(DropdownMenuItem, { disabled: !['stopped', 'deleting'].includes(realm.state), onSelect: () => void prepareDelete(realm), children: t(realm.state === 'deleting' ? 'retryDelete' : 'deleteAction') })
              ] })
            ] }),
          realm.error ? jsx('span', { role: 'alert', children: /https?:|ticket|token/i.test(String(realm.error)) ? 'Realm failed; check backend diagnostics.' : String(realm.error).slice(0, 240) }) : null
        ] }, realm.id))
      ] });
    }
    function RealmBadge({ session }) {
      const t = useRealmText();
      const result = useQuery(realmQueryOptions(ctx, session));
      if (!validSession(session)) return null;
      // A failed lookup is not evidence that this session has a realm. Keep
      // ordinary rows quiet and known badges stable while polling retries.
      const owned = ownedRealms(result.data, session);
      const selected = owned.filter(realm => (realm.kind || 'realm') === result.data?.kind);
      const realms = selected.length ? selected : owned;
      if (!realms.length) return null;
      const label = `${result.isError ? 'Realm status unavailable · Last known: ' : ''}${realms.map(realm => realmLabel(realm, t)).join('; ')}`;
      // The compact badge names the kind, because 'which machine am I on' is
      // the one thing a glance has to answer.
      const vm = realms.some(realm => realm.kind === 'omarchy-vm');
      const knownCount = !vm && realms.every(realm => Number.isSafeInteger(realm.window_count) && realm.window_count >= 0);
      const retained = realms.every(realm => ['stopped', 'recovery-required', 'cleanup_failed'].includes(realm.state));
      const attention = realms.find(realm => realm.state !== 'stopped') || realms[0];
      const compact = retained ? `${kindName(attention.kind)} · ${stateLabel(attention.state, t)}` : vm ? 'Omarchy VM'
        : knownCount ? `Realm · ${realms.reduce((sum, realm) => sum + realm.window_count, 0)}` : 'Realm';
      return jsx(Badge, { size: 'xs', title: label, 'aria-label': label, children: compact });
    }
    ctx.register({ id: 'realm-status', area: SESSION_AREAS.statusStack, data: { render: StatusRow } });
    ctx.register({ id: 'realm-tile', area: SESSION_AREAS.tileBadge, data: { render: RealmBadge } });
    ctx.register({ id: 'realm-list', area: SESSION_AREAS.listBadge, data: { render: RealmBadge } });
  }
};
