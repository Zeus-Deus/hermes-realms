import { Button, ConfirmDialog, SegmentedControl, usePluginI18n, useQuery, useQueryClient } from '@hermes/plugin-sdk';
import { useEffect, useRef, useState } from 'react';
import { jsx, jsxs } from 'react/jsx-runtime';

export const setupLocales = {
  en: {
    updateUncertain: 'Update outcome unknown. Refresh status before checking again.',
    updateCancel: 'Cancel update',
    updateReadError: 'Connection interrupted. Refresh update status.',

    updateCheck: 'Check for updates',
    updateTitle: 'Review base update',
    updateConfirm: 'Update',
    updateLater: 'Later',
    updateScope: 'Base image for this profile; approved packages affect the gateway host. Retained workspaces are kept.',
    updateCurrent: 'Base image is up to date.',
    updateUnknown: 'Latest base version unavailable.',
    updateReviewError: 'Update review unavailable or changed. Check again.',

    storageTitle: 'Storage and base image', storageLabel: 'Omarchy storage', storageBase: 'Omarchy base',
    storageLoading: 'Reading storage…', storageError: 'Storage unavailable. Refresh to try again.',
    storageUnknown: 'Unknown', storageNotPrepared: 'Not prepared', storagePrepared: 'Prepared',
    storageDownloads: 'Downloaded files', storageBaseFiles: 'Base image files', storageWorkspaces: 'VM workspace files',
    storageRetained: 'Stop retains workspace data. Export needed work before using Delete in the conversation’s management menu.',
    permissionHeld: 'Execution paused for permission review', legacyHost: 'Legacy host access retained · private testing disabled',
    permissionReview: 'Use optional targets…', permissionTitle: 'Review conversation permissions', permissionAccept: 'Accept permission change',
    permissionError: 'Permission review unavailable or changed. Close and review again. No guest was changed.',
    manage: 'Manage private testing', popout: 'Pop out', stopTesting: 'Stop private testing', disableTesting: 'Disable use for this session',
    actionError: 'Action failed. Refresh status before trying again.',
    deleteAction: 'Delete workspace…', retryDelete: 'Retry deletion…', deleteTitle: 'Delete retained workspace?', deleteConfirm: 'Delete workspace',
    deleteWarning: 'This permanently removes the selected private workspace. Export any work you need before deleting. The original host project and shared base image are kept.',
    deleteError: 'Deletion unavailable or workspace changed. Close and review again. Running work must be stopped first.',
    deleteRefreshError: 'Workspace deleted. Refresh status to update this view.',
    testingEnabled: 'Private testing enabled', testingDisabled: 'Private testing disabled', testingUnselected: 'Private testing not selected',
    deletingState: 'deleting', stoppedState: 'stopped', recoveryState: 'needs recovery', cleanupState: 'cleanup incomplete',
    realm: 'Realm', vm: 'Omarchy VM', repair: 'Repair', install: 'Set up', start: 'Use this desktop',
    repairAction: 'Repair…', installAction: 'Set up…', vmAction: 'Set up Omarchy VM…',
    switchAction: 'Use this desktop…', reviewTitle: 'Review desktop setup', details: 'Details',
    ready: 'Ready', needsRepair: 'Needs setup or repair', needsVm: 'A disposable Omarchy desktop with its own kernel and disk.',
    checking: 'Checking setup…', started: 'Setup started', running: 'Preparing desktop…', failed: 'Setup failed',
    cancelSetup: 'Cancel setup', cancelling: 'Cancelling…', cancelled: 'Setup cancelled', cancelPending: 'Requesting cancellation…',
    retry: 'Retry…', connectionLost: 'Connection interrupted. Setup may still be running.',
    reviewChanged: 'Setup requirements changed. Review the updated details before confirming.',
    error: 'Unable to prepare this desktop. Try again.', scoped: 'Desktop choice: this chat. Driver and base image: this profile. Approved system packages affect the gateway host. Nothing is installed before you confirm.'
  },
  ru: {
    updateUncertain: 'Результат обновления неизвестен. Обновите статус перед новой проверкой.',
    updateCancel: 'Отменить обновление',
    updateReadError: 'Соединение прервано. Обновите статус обновления.',

    updateCheck: 'Проверить обновления',
    updateTitle: 'Проверка обновления базового образа',
    updateConfirm: 'Обновить',
    updateLater: 'Позже',
    updateScope: 'Базовый образ для этого профиля; одобренные пакеты устанавливаются на сервер шлюза. Рабочие области сохраняются.',
    updateCurrent: 'Базовый образ актуален.',
    updateUnknown: 'Последняя версия базового образа недоступна.',
    updateReviewError: 'Проверка обновления недоступна или изменилась. Проверьте снова.',

    storageTitle: 'Хранилище и базовый образ', storageLabel: 'Хранилище Omarchy', storageBase: 'Базовый образ Omarchy',
    storageLoading: 'Чтение хранилища…', storageError: 'Хранилище недоступно. Обновите для повторной попытки.',
    storageUnknown: 'Неизвестно', storageNotPrepared: 'Не подготовлен', storagePrepared: 'Подготовлен',
    storageDownloads: 'Загруженные файлы', storageBaseFiles: 'Файлы базового образа', storageWorkspaces: 'Файлы рабочих областей ВМ',
    storageRetained: 'Остановка сохраняет рабочие данные. Экспортируйте нужные файлы перед удалением через меню управления беседой.',
    permissionHeld: 'Выполнение приостановлено для проверки разрешений', legacyHost: 'Прежний доступ к хосту сохранён · приватное тестирование отключено',
    permissionReview: 'Использовать отдельные цели…', permissionTitle: 'Проверка разрешений беседы', permissionAccept: 'Принять изменение разрешений',
    permissionError: 'Проверка разрешений недоступна или изменилась. Закройте и проверьте снова. Гостевая система не изменена.',
    manage: 'Управление изолированным тестированием', popout: 'Отдельное окно', stopTesting: 'Остановить изолированное тестирование', disableTesting: 'Отключить для этого сеанса',
    actionError: 'Не удалось выполнить действие. Обновите статус перед повторной попыткой.',
    deleteAction: 'Удалить рабочую область…', retryDelete: 'Повторить удаление…', deleteTitle: 'Удалить сохранённую рабочую область?', deleteConfirm: 'Удалить рабочую область',
    deleteWarning: 'Выбранная приватная рабочая область будет удалена навсегда. Сначала экспортируйте нужные файлы. Исходный проект на хосте и общий базовый образ сохранятся.',
    deleteError: 'Удаление недоступно или рабочая область изменилась. Закройте и проверьте снова. Сначала остановите запущенную работу.',
    deleteRefreshError: 'Рабочая область удалена. Обновите статус.',
    testingEnabled: 'Изолированное тестирование включено', testingDisabled: 'Изолированное тестирование отключено', testingUnselected: 'Изолированное тестирование не выбрано',
    deletingState: 'удаляется', stoppedState: 'остановлено', recoveryState: 'требуется восстановление', cleanupState: 'очистка не завершена',
    realm: 'Realm', vm: 'Omarchy VM', repair: 'Исправить', install: 'Настроить', start: 'Использовать этот рабочий стол',
    repairAction: 'Исправить…', installAction: 'Настроить…', vmAction: 'Настроить Omarchy VM…',
    switchAction: 'Использовать этот рабочий стол…', reviewTitle: 'Проверка настройки рабочего стола', details: 'Подробности',
    ready: 'Готово', needsRepair: 'Требуется настройка или исправление', needsVm: 'Одноразовый рабочий стол Omarchy с отдельным ядром и диском.',
    checking: 'Проверка настройки…', started: 'Настройка запущена', running: 'Подготовка рабочего стола…', failed: 'Не удалось настроить',
    cancelSetup: 'Отменить настройку', cancelling: 'Отмена…', cancelled: 'Настройка отменена', cancelPending: 'Запрос отмены…',
    retry: 'Повторить…', connectionLost: 'Соединение прервано. Настройка может продолжаться.',
    reviewChanged: 'Требования изменились. Проверьте обновлённые сведения перед подтверждением.',
    error: 'Не удалось подготовить рабочий стол. Повторите попытку.', scoped: 'Рабочий стол — для этого чата; драйвер и базовый образ — для этого профиля. Одобренные системные пакеты устанавливаются на сервер шлюза. Установка начнётся после подтверждения.'
  },
  zh: {
    updateUncertain: '更新结果未知。请刷新状态后再检查。',
    updateCancel: '取消更新',
    updateReadError: '连接中断。请刷新更新状态。',

    updateCheck: '检查更新',
    updateTitle: '审核基础镜像更新',
    updateConfirm: '更新',
    updateLater: '稍后',
    updateScope: '此配置的基础镜像；批准的软件包会安装到网关主机。保留的工作区不会删除。',
    updateCurrent: '基础镜像已是最新版本。',
    updateUnknown: '无法获取最新基础镜像版本。',
    updateReviewError: '更新审核不可用或已更改。请重新检查。',

    storageTitle: '存储和基础镜像', storageLabel: 'Omarchy 存储', storageBase: 'Omarchy 基础镜像',
    storageLoading: '正在读取存储…', storageError: '存储不可用。请刷新重试。',
    storageUnknown: '未知', storageNotPrepared: '尚未准备', storagePrepared: '已准备',
    storageDownloads: '已下载的文件', storageBaseFiles: '基础镜像文件', storageWorkspaces: '虚拟机工作区文件',
    storageRetained: '停止会保留工作区数据。在会话管理菜单中使用删除前，请先导出需要保留的文件。',
    permissionHeld: '执行已暂停，等待权限审核', legacyHost: '保留原有主机访问权限 · 私有测试已禁用',
    permissionReview: '使用可选目标…', permissionTitle: '审核会话权限', permissionAccept: '接受权限变更',
    permissionError: '权限审核不可用或已更改。请关闭并重新审核。未更改任何访客环境。',
    manage: '管理私有桌面测试', popout: '独立窗口', stopTesting: '停止私有桌面测试', disableTesting: '在此会话中禁用',
    actionError: '操作失败。请先刷新状态再重试。',
    deleteAction: '删除工作区…', retryDelete: '重试删除…', deleteTitle: '删除保留的工作区？', deleteConfirm: '删除工作区',
    deleteWarning: '这会永久删除选定的私有工作区。删除前请导出需要保留的文件。主机上的原始项目和共享基础镜像将保留。',
    deleteError: '无法删除或工作区已更改。请关闭并重新审核。必须先停止运行中的任务。',
    deleteRefreshError: '工作区已删除。请刷新状态以更新此视图。',
    testingEnabled: '已启用私有桌面测试', testingDisabled: '已禁用私有桌面测试', testingUnselected: '尚未选择私有桌面测试',
    deletingState: '正在删除', stoppedState: '已停止', recoveryState: '需要恢复', cleanupState: '清理未完成',
    realm: 'Realm', vm: 'Omarchy VM', repair: '修复', install: '设置', start: '使用此桌面',
    repairAction: '修复…', installAction: '设置…', vmAction: '设置 Omarchy VM…',
    switchAction: '使用此桌面…', reviewTitle: '确认桌面设置', details: '详细信息',
    ready: '就绪', needsRepair: '需要设置或修复', needsVm: '拥有独立内核和磁盘的一次性 Omarchy 桌面。',
    checking: '正在检查…', started: '设置已开始', running: '正在准备桌面…', failed: '设置失败',
    cancelSetup: '取消设置', cancelling: '正在取消…', cancelled: '设置已取消', cancelPending: '正在请求取消…',
    retry: '重试…', connectionLost: '连接中断。设置可能仍在进行。',
    reviewChanged: '设置要求已更改。请查看更新后的详情再确认。',
    error: '无法准备此桌面。请重试。', scoped: '桌面选择仅适用于此聊天，驱动和基础镜像仅用于此配置。批准的系统软件包会安装到网关主机。在您确认之前不会安装任何内容。'
  }
};

const ownerBody = session => Object.fromEntries([
  ['runtime_session_id', session.runtimeSessionId], ['stored_session_id', session.storedSessionId]
].filter(([, value]) => typeof value === 'string' && value.length));
const running = job => ['running', 'cancelling'].includes(job?.state);
const finished = job => ['succeeded', 'failed', 'cancelled'].includes(job?.state);
const validReview = value => value && ['realm', 'omarchy-vm'].includes(value.kind)
  && ['repair', 'install', 'start'].includes(value.action) && typeof value.summary === 'string'
  && Array.isArray(value.details) && value.details.every(detail => typeof detail === 'string')
  && typeof value.consent === 'string' && value.consent.length > 0;

export function useRealmText() {
  const translate = usePluginI18n('hermes-realms');
  return key => { const value = translate(key); return value === key ? setupLocales.en[key] : value; };
}

export function RealmSetupControls({ ctx, session, data, refresh }) {
  const t = useRealmText();
  const queryClient = useQueryClient();
  const [cancelPending, setCancelPending] = useState(false);
  const currentKind = data.kind || 'realm';
  const [choice, setChoice] = useState(null);
  const kind = choice || data.requested_kind || currentKind;
  const [review, setReview] = useState(null);
  const [preparing, setPreparing] = useState(false);
  const [error, setError] = useState(null);
  const [started, setStarted] = useState(null);
  const mounted = useRef(true);
  const pending = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  // The local receipt bridges start -> first owner poll, not future jobs.
  useEffect(() => {
    if (started && data.setup_job?.id === started.id
        && (!finished(started) || finished(data.setup_job))) setStarted(null);
  }, [started, data.setup_job]);
  const jobSeed = started && !(data.setup_job?.id === started.id && finished(data.setup_job))
    ? started : data.setup_job;
  const jobKey = ['hermes-realms-setup', session.connectionId, session.profile, session.storedSessionId, session.runtimeSessionId, jobSeed?.id];
  const jobResult = useQuery({
    queryKey: jobKey,
    enabled: Boolean(jobSeed?.id) && running(jobSeed),
    queryFn: () => ctx.rest(`/realms/setup/jobs/${encodeURIComponent(jobSeed.id)}?${new URLSearchParams(ownerBody(session))}`, { scope: session }),
    refetchInterval: query => running(query.state.data || jobSeed) ? 1000 : false,
    retry: 1,
    gcTime: 60000
  });
  // A terminal owner observation must beat a stale (now disabled) job query.
  const job = finished(jobSeed) ? jobSeed : jobResult.data || jobSeed;
  const jobError = jobResult.isError && !finished(job);
  const busy = preparing || review !== null || running(job);
  const refreshed = useRef(null);
  useEffect(() => {
    if (job?.state === 'succeeded' && refreshed.current !== job.id) {
      refreshed.current = job.id;
      void refresh();
    }
  }, [job?.id, job?.state, refresh]);
  const setup = kind === 'omarchy-vm' ? data.vm_setup : data.setup;
  const ready = setup?.ready === true;
  const needsAction = !ready || kind !== currentKind || data.mode !== 'realm';

  async function prepare() {
    if (pending.current || running(job)) return;
    pending.current = true;
    setPreparing(true);
    setError(null);
    const scope = Object.freeze({ ...session });
    try {
      const value = await ctx.rest('/realms/setup/prepare', { method: 'POST', body: { ...ownerBody(scope), kind }, scope });
      if (!validReview(value) || value.kind !== kind) throw new Error(t('error'));
      if (mounted.current) setReview({ value, scope });
    } catch (failure) {
      if (mounted.current) setError(failure instanceof Error ? failure.message : t('error'));
    } finally {
      pending.current = false;
      if (mounted.current) setPreparing(false);
    }
  }

  async function cancelJob() {
    if (!mounted.current || pending.current || job?.state !== 'running' || job.cancellable === false) return;
    pending.current = true;
    setCancelPending(true);
    const scope = Object.freeze({ ...session });
    const id = job.id;
    try {
      const receipt = await ctx.rest(`/realms/setup/jobs/${encodeURIComponent(id)}/cancel`, {
        method: 'POST', body: ownerBody(scope), scope
      });
      if (receipt?.id !== id || !['running', 'cancelling', 'cancelled', 'succeeded', 'failed'].includes(receipt.state)) throw new Error(t('error'));
      // A pre-Cancel polling reply must not overwrite the accepted receipt.
      await queryClient.cancelQueries({ queryKey: jobKey, exact: true });
      queryClient.setQueryData(jobKey, receipt);
      if (mounted.current) setError(null);
    } catch (failure) {
      if (mounted.current) setError(failure instanceof Error ? failure.message : t('error'));
    } finally {
      pending.current = false;
      if (mounted.current) setCancelPending(false);
    }
  }

  async function confirm() {
    if (!mounted.current || pending.current || !review) return;
    pending.current = true;
    const { value, scope } = review;
    try {
      const receipt = await ctx.rest('/realms/setup/start', {
        method: 'POST', body: { ...ownerBody(scope), kind: value.kind, consent: value.consent }, scope
      });
      if (!receipt?.id || !['running', 'succeeded', 'failed'].includes(receipt.state)) throw new Error(t('error'));
      if (receipt.state === 'failed') throw new Error(receipt.message || t('failed'));
      if (mounted.current) {
        setStarted(receipt);
        setError(null);
        if (receipt.state === 'succeeded') await refresh();
      }
    } catch (failure) {
      // Electron's IPC error envelope does not preserve HTTP status fields.
      // Re-read the proposal rather than guessing from an exception string.
      let changed = false;
      if (mounted.current) {
        try {
          const next = await ctx.rest('/realms/setup/prepare', { method: 'POST', body: { ...ownerBody(scope), kind: value.kind }, scope });
          if (mounted.current && validReview(next) && next.kind === value.kind
              && JSON.stringify(next.consent) !== JSON.stringify(value.consent)) {
            setReview({ value: next, scope });
            changed = true;
          }
        } catch { /* Preserve the original failure if the connection is down. */ }
      }
      if (changed) throw new Error(t('reviewChanged'));
      throw failure;
    } finally { pending.current = false; }
  }

  if (data.requested !== undefined && !needsAction && !busy && !error
      && !['failed', 'cancelled'].includes(job?.state) && !jobError) return null;

  const actionLabel = error || ['failed', 'cancelled'].includes(job?.state) ? t('retry')
    : kind === 'omarchy-vm' && !ready ? t('vmAction')
    : !ready ? t('repairAction') : t('switchAction');
  const status = preparing ? t('checking') : running(job) ? (job.message || t('running'))
    : job?.state === 'cancelled' ? t('cancelled')
    : kind === currentKind && data.mode === 'host' ? t('testingDisabled')
    : kind === currentKind && data.mode === 'ask' ? t('testingUnselected')
    : ready && kind === currentKind && data.mode === 'realm' ? t('ready')
    : kind === 'omarchy-vm' ? t('needsVm') : t('needsRepair');

  return jsxs('div', { className: 'flex flex-col gap-2 text-xs', 'data-realms-setup': '', children: [
    jsxs('div', { className: 'flex flex-wrap items-center gap-2', children: [
      data.requested === undefined && jsx(SegmentedControl, { options: [{ id: 'realm', label: t('realm') }, { id: 'omarchy-vm', label: t('vm') }], value: kind, disabled: busy,
        onChange: next => { setChoice(next); setError(null); } }),
      jsx('span', { role: 'status', className: 'text-muted-foreground', children: status }),
      running(job) && jsx(Button, { size: 'micro', variant: 'ghost', disabled: cancelPending || job.state === 'cancelling' || job.cancellable === false,
        onClick: () => void cancelJob(), children: cancelPending ? t('cancelPending') : job.state === 'cancelling' ? t('cancelling') : t('cancelSetup') }),
      (needsAction || job?.state === 'cancelled') && !running(job) && jsx(Button, { size: 'micro', variant: 'ghost', disabled: busy,
        onClick: () => void prepare(), children: actionLabel })
    ] }),
    (error || job?.state === 'failed' || jobError) && jsx('div', { role: 'alert', children:
      error || (jobError ? t('connectionLost') : job.message || t('failed')) }),
    review && jsx(ConfirmDialog, {
      open: true, title: t('reviewTitle'), confirmLabel: t(review.value.action), busyLabel: t('checking'),
      doneLabel: t('started'), onClose: () => setReview(null), onConfirm: confirm,
      description: jsxs('span', { className: 'flex flex-col gap-3', children: [
        jsx('span', { children: review.value.summary }),
        jsx('span', { children: t('scoped') }),
        review.value.details.length > 0 && jsxs('details', { children: [
          jsx('summary', { children: t('details') }),
          ...review.value.details.map((detail, index) => jsx('span', { className: 'block break-words', children: detail }, index))
        ] })
      ] })
    })
  ] });
}
