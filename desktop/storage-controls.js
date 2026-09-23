import { Button, ConfirmDialog, useQuery, useQueryClient } from '@hermes/plugin-sdk';
import { useEffect, useId, useRef, useState } from 'react';
import { jsx, jsxs } from 'react/jsx-runtime';
import { useRealmText } from './setup-controls.js';

function storedSize(bytes, unknown) {
  if (!Number.isSafeInteger(bytes) || bytes < 0) return unknown;
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let unit = 0;
  while (bytes >= 1024 && unit < units.length - 1) { bytes /= 1024; unit += 1; }
  return `${Number.isInteger(bytes) ? bytes : bytes.toFixed(1)} ${units[unit]}`;
}

const validRelease = value => typeof value === 'string' && /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(value);
const profileUpdate = value => value?.operation === 'vm-base-update' && value.scope === 'profile' && value.kind === 'omarchy-vm';
const active = job => ['running', 'cancelling'].includes(job?.state);
const terminal = job => ['succeeded', 'failed', 'cancelled'].includes(job?.state);
const validJob = job => profileUpdate(job) && typeof job.id === 'string' && /^[a-f0-9]{32}$/.test(job.id)
  && (active(job) || terminal(job)) && typeof job.message === 'string' && typeof job.cancellable === 'boolean'
  && (job.error === undefined || typeof job.error === 'string')
  && (job.base_commit_started === undefined || typeof job.base_commit_started === 'boolean');
const strings = value => Array.isArray(value) && value.every(item => typeof item === 'string');
const validReview = (value, release, scope) => profileUpdate(value) && value.release === release
  && value.review_binding?.connectionId === scope.connectionId && value.review_binding?.profile === scope.profile
  && typeof value.consent === 'string' && value.consent.trim().length > 0
  && typeof value.summary === 'string' && strings(value.details) && strings(value.blockers) && !value.blockers.length;

export function RealmStorageSettings({ ctx, scope }) {
  if (!scope || !['connectionId', 'profile'].every(key => typeof scope[key] === 'string' && scope[key].length)) return null;
  const binding = Object.freeze({ connectionId: scope.connectionId, profile: scope.profile });
  return jsx(StorageSection, { ctx, scope: binding }, JSON.stringify(binding));
}

function StorageSection({ ctx, scope }) {
  const t = useRealmText();
  const mounted = useRef(true);
  const pending = useRef(false);
  const [request] = useState(() => ctx.rest.bind(ctx));
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const [cancelPending, setCancelPending] = useState(false);
  const [started, setStarted] = useState(null);
  const [uncertain, setUncertain] = useState(false);
  const id = useId();
  const [review, setReview] = useState(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const detailsId = useId();
  const [checking, setChecking] = useState(false);
  const [notice, setNotice] = useState(null);
  async function checkUpdates() {
    if (!mounted.current || pending.current || review || uncertain || active(job)) return;
    pending.current = true;
    setChecking(true);
    setNotice(null);
    try {
      const latest = await request('/realms/vm/settings?check_updates=true', { scope });
      if (!mounted.current) return;
      if (latest.update?.available !== true) {
        setNotice(t(latest.update?.available === false ? 'updateCurrent' : 'updateUnknown'));
        return;
      }
      if (!validRelease(latest.update.latest)) throw new Error();
      const value = await request('/realms/vm/update/prepare', {
        method: 'POST', body: { release: latest.update.latest, review_binding: scope }, scope
      });
      if (!mounted.current) return;
      if (!validReview(value, latest.update.latest, scope)) throw new Error();
      setDetailsOpen(false);
      setReview({ value });
    } catch { if (mounted.current) setNotice(t('updateReviewError')); }
    finally { pending.current = false; if (mounted.current) setChecking(false); }
  }
  const result = useQuery({
    queryKey: ['hermes-realms-storage', scope.connectionId, scope.profile],
    queryFn: () => request('/realms/vm/settings', { scope }),
    enabled: open || active(started), staleTime: 5000, retry: 1
  });
  const latestJob = validJob(result.data?.base_update_job) ? result.data.base_update_job : null;
  // A start receipt bridges only to its first matching inventory acknowledgement.
  useEffect(() => {
    if (started && latestJob?.id === started.id && (!terminal(started) || terminal(latestJob))) setStarted(null);
  }, [started, latestJob]);
  const seed = started && !(latestJob?.id === started.id && terminal(latestJob)) ? started : latestJob;
  const jobKey = ['hermes-realms-base-update', scope.connectionId, scope.profile, seed?.id];
  const jobResult = useQuery({
    queryKey: jobKey,
    enabled: Boolean(seed) && active(seed),
    queryFn: async () => {
      const value = await request(`/realms/vm/update/jobs/${seed.id}`, { scope });
      if (!validJob(value) || value.id !== seed.id) throw new Error();
      return value;
    },
    refetchInterval: query => query.state.status !== 'error' && active(query.state.data || seed) ? 1000 : false,
    retry: false
  });
  const job = terminal(seed) ? seed : jobResult.data || seed;
  const refreshed = useRef(null);
  useEffect(() => {
    if (terminal(job) && active(seed) && refreshed.current !== job.id) {
      refreshed.current = job.id;
      void result.refetch();
    }
  }, [job, seed, result.refetch]);
  const readError = (jobResult.isError && !terminal(seed)) || (result.isError && Boolean(job));
  async function refreshUpdate() {
    const reads = [result.refetch()];
    if (seed) reads.push(jobResult.refetch());
    const [latest] = await Promise.all(reads);
    if (mounted.current && !latest.isError) { setUncertain(false); setNotice(null); }
  }
  async function cancelJob() {
    if (!mounted.current || pending.current || job?.state !== 'running' || !job.cancellable || job.base_commit_started) return;
    pending.current = true;
    setCancelPending(true);
    try {
      const value = await request(`/realms/vm/update/jobs/${job.id}/cancel`, { method: 'POST', body: {}, scope });
      if (!validJob(value) || value.id !== job.id) throw new Error();
      if (!mounted.current) return;
      await queryClient.cancelQueries({ queryKey: jobKey, exact: true });
      queryClient.setQueryData(jobKey, value);
    } catch { if (mounted.current) { setNotice(t('updateReadError')); setUncertain(true); } }
    finally { pending.current = false; if (mounted.current) setCancelPending(false); }
  }
  const base = result.data?.base;
  const version = base?.present === false ? t('storageNotPrepared')
    : base?.present === true ? (typeof base.version === 'string' && base.version || t('storagePrepared'))
      : t('storageUnknown');
  return jsxs('div', { className: 'flex flex-col items-start gap-2 text-xs', children: [
    jsx(Button, { size: 'micro', variant: 'ghost', 'aria-expanded': open, 'aria-controls': id,
      onClick: () => setOpen(value => !value), children: t('storageTitle') }),
    open && jsxs('div', { id, role: 'region', 'aria-label': t('storageLabel'), className: 'flex flex-col gap-2', children: [
      jsx('span', { className: 'text-(--ui-text-tertiary)', children: `${scope.connectionId} · ${scope.profile}` }),
      jsx(Button, { size: 'micro', variant: 'ghost', disabled: checking || Boolean(review) || active(job) || uncertain,
        onClick: () => void checkUpdates(), children: t('updateCheck') }),
      (notice || job || uncertain) && jsxs('div', { role: 'status', className: 'flex flex-wrap items-center gap-2', children: [
        jsx('span', { children: job?.message || notice }),
        job && notice && !readError && jsx('span', { children: notice }),
        readError && jsx('span', { children: t('updateReadError') }),
        (readError || uncertain) && jsx(Button, { size: 'micro', variant: 'ghost', disabled: result.isFetching || jobResult.isFetching,
          onClick: () => void refreshUpdate(), children: t('retry') }),
        !uncertain && !readError && job?.state === 'running' && job.cancellable && !job.base_commit_started && jsx(Button, {
          size: 'micro', variant: 'ghost', disabled: cancelPending, onClick: () => void cancelJob(), children: t('updateCancel')
        })
      ] }),
      result.isError && !job && !uncertain ? jsxs('div', { role: 'status', children: [
        t('storageError'), ' ', jsx(Button, { size: 'micro', variant: 'ghost', disabled: result.isFetching,
          onClick: () => void result.refetch(), children: t('retry') })
      ] }) : result.isPending ? jsx('span', { role: 'status', children: t('storageLoading') }) : jsxs('div', {
        className: 'flex flex-col gap-2', children: [
          jsxs('span', { children: [t('storageBase'), ': ', jsx('span', { children: version })] }),
          jsx('dl', { className: 'grid grid-cols-[1fr_auto] gap-x-4 gap-y-1', children:
            [['iso_bytes', 'storageDownloads'], ['base_bytes', 'storageBaseFiles'], ['session_bytes', 'storageWorkspaces']]
              .flatMap(([field, label]) => [
                jsx('dt', { children: t(label) }, `${field}-label`),
                jsx('dd', { children: storedSize(result.data?.storage?.[field], t('storageUnknown')) }, field)
              ])
          }),
          jsx('span', { className: 'text-(--ui-text-tertiary)', children: t('storageRetained') })
        ]
      })
    ] }),
    review && jsx(ConfirmDialog, {
      open: true, title: t('updateTitle'), confirmLabel: t('updateConfirm'), cancelLabel: t('updateLater'),
      onClose: () => setReview(current => current === review ? null : current),
      onConfirm: async () => {
        if (!mounted.current || pending.current || review.used) return;
        review.used = true;
        pending.current = true;
        const { value } = review;
        try {
          const receipt = await request('/realms/vm/update/start', {
            method: 'POST', body: { release: value.release, review_binding: scope, consent: value.consent }, scope
          });
          if (!validJob(receipt)) throw new Error();
          if (!mounted.current) return;
          setStarted(receipt);
          setNotice(null);
          void result.refetch();
        } catch {
          if (!mounted.current) return;
          setReview(current => current === review ? null : current);
          setUncertain(true);
          setNotice(t('updateUncertain'));
          void result.refetch();
        } finally { pending.current = false; }
      },
      description: jsxs('span', { className: 'flex flex-col gap-3', children: [
        jsx('span', { children: review.value.summary }),
        jsx('span', { children: `${t('updateScope')} ${scope.connectionId} · ${scope.profile}` }),
        jsxs('span', { children: [
          jsx(Button, { type: 'button', size: 'inline', variant: 'text', 'aria-expanded': detailsOpen, 'aria-controls': detailsId,
            onClick: () => setDetailsOpen(value => !value), children: t('details') }),
          jsx('span', { id: detailsId, hidden: !detailsOpen, children:
            review.value.details.map((detail, index) => jsx('span', { className: 'block break-words', children: detail }, index)) })
        ] })
      ] })
    })
  ] });
}
