import { Button, ConfirmDialog } from '@hermes/plugin-sdk';
import { useEffect, useRef, useState } from 'react';
import { jsx, jsxs } from 'react/jsx-runtime';
import { useRealmText } from './setup-controls.js';

const ownerBody = session => Object.fromEntries([
  ['runtime_session_id', session.runtimeSessionId], ['stored_session_id', session.storedSessionId]
].filter(([, value]) => typeof value === 'string' && value.length));

export function RealmPermissionControls({ ctx, session, data, refresh }) {
  const t = useRealmText();
  const [review, setReview] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);
  const pending = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  async function prepare() {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError(null);
    const scope = Object.freeze({ ...session });
    const rest = ctx.rest.bind(ctx);
    try {
      const value = await rest('/realms/permissions/prepare', { method: 'POST', body: ownerBody(scope), scope });
      if (typeof value?.digest !== 'string' || typeof value.text !== 'string' || !Array.isArray(value.resources)) throw new Error();
      if (mounted.current) setReview({ value, scope, rest });
    } catch {
      if (mounted.current) setError(t('permissionError'));
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  async function accept() {
    if (!mounted.current || pending.current || !review) throw new Error(t('permissionError'));
    pending.current = true;
    try {
      const { value, scope, rest } = review;
      const result = await rest('/realms/permissions/accept', {
        method: 'POST', body: { ...ownerBody(scope), digest: value.digest }, scope
      });
      if (result?.state !== 'optional') throw new Error();
      if (mounted.current) await refresh();
    } catch {
      // A changed backend nonce/digest is never silently re-reviewed or retried.
      throw new Error(t('permissionError'));
    } finally { pending.current = false; }
  }

  return jsxs('div', { className: 'flex flex-col gap-2 text-xs', 'data-realms-permission': '', children: [
    jsxs('div', { className: 'flex flex-wrap items-center gap-2', children: [
      jsx('span', { role: 'status', children: t(data.permission.state === 'legacy-host' ? 'legacyHost' : 'permissionHeld') }),
      jsx(Button, { size: 'micro', variant: 'ghost', disabled: busy || !!review, onClick: () => void prepare(), children: t('permissionReview') })
    ] }),
    error && jsx('span', { role: 'alert', children: error }),
    review && jsx(ConfirmDialog, {
      open: true, title: t('permissionTitle'), confirmLabel: t('permissionAccept'),
      onClose: () => setReview(null), onConfirm: accept,
      description: jsxs('span', { className: 'flex flex-col gap-3', children: [
        jsx('span', { children: `${review.scope.connectionId} · ${review.scope.profile} · ${review.scope.storedSessionId || review.scope.runtimeSessionId}` }),
        jsx('span', { children: review.value.text }),
        ...review.value.resources.map(row => jsx('span', { children: `${row.id}: ${row.warning}` }, row.id))
      ] })
    })
  ] });
}
