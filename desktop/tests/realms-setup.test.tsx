import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { type ComponentType } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import { createPluginI18n } from '@/i18n/plugin-i18n'

// @ts-expect-error Runtime plugins are plain JavaScript SDK consumers.
import realmsPlugin, { realmQueryOptions } from '../../desktop/plugin.js'

import { SESSION_AREAS, type SessionContribution, type SessionContributionProps } from '@/contrib/session'

const owner = { connectionId: 'local', profile: 'default', storedSessionId: 'history', runtimeSessionId: 'runtime' }
const clients: QueryClient[] = []
const disposers: (() => void)[] = []

function mount(rest: ReturnType<typeof vi.fn>, data: Record<string, unknown> = {}) {
  const renders = new Map<string, ComponentType<SessionContributionProps>>()

  const ctx = {
    rest,
    i18n: createPluginI18n('hermes-realms', dispose => {
      disposers.push(dispose)

      return dispose
    }),
    register: ({ area, data }: { area: string; data: SessionContribution }) => renders.set(area, data.render)
  }

  realmsPlugin.register(ctx)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  client.setQueryData(realmQueryOptions(ctx, owner).queryKey, {
    kind: 'realm',
    mode: 'realm',
    realms: [],
    setup: { ready: false, message: 'Run hermes realms install-driver in this profile.' },
    ...data
  })
  const StatusRow = renders.get(SESSION_AREAS.statusStack)!

  const tree = (session = owner) => (
    <QueryClientProvider client={client}>
      <StatusRow session={session} />
    </QueryClientProvider>
  )

  return { ...render(tree()), tree, client }
}

afterEach(() => {
  cleanup()
  clients.splice(0).forEach(client => client.clear())
  disposers.splice(0).forEach(dispose => dispose())
})

it.each(['succeeded', 'failed', 'cancelled'])('uses an observed %s job over cached running progress', async state => {
  const runningJob = { id: 'finished-job', state: 'running', message: 'Preparing the profile-local Realm driver…' }

  const snapshot = {
    kind: 'realm',
    mode: 'realm',
    requested: true,
    realms: [],
    setup: { ready: false },
    setup_job: runningJob
  }

  const rest = vi.fn(async (path: string) => (path.startsWith('/realms/setup/jobs/') ? runningJob : snapshot))
  const view = mount(rest, snapshot)
  await view.findByText(runningJob.message)
  await waitFor(() => expect(rest.mock.calls.some(([path]) => path.startsWith('/realms/setup/jobs/'))).toBe(true))
  await act(async () => {
    const cached = view.client.getQueryCache().find({
      queryKey: [
        'hermes-realms-setup',
        owner.connectionId,
        owner.profile,
        owner.storedSessionId,
        owner.runtimeSessionId,
        runningJob.id
      ]
    })

    expect(cached).toBeTruthy()
    cached!.setState({ status: 'error', error: new Error('stale poll failure') })
    view.client.setQueryData(
      ['hermes-realms', owner.connectionId, owner.profile, owner.storedSessionId, owner.runtimeSessionId],
      {
        ...snapshot,
        setup: { ready: state === 'succeeded' },
        setup_job: { id: runningJob.id, state, message: 'Finished setup observation', cancellable: false }
      }
    )
  })
  await waitFor(() => expect(view.queryByRole('button', { name: 'Cancel setup' })).toBeNull())
  expect(view.queryByText(runningJob.message)).toBeNull()

  if (state === 'cancelled') {
    expect(view.getByText('Setup cancelled')).toBeTruthy()
  }

  if (state === 'failed') {
    expect(view.getByRole('alert').textContent).toContain('Finished setup observation')
  }
})

it('retires the start receipt after acknowledgement without hiding a later job', async () => {
  let job = { id: 'first-job', state: 'running', message: 'First job running' }
  let started = false

  const snapshot = () => ({
    kind: 'realm',
    mode: 'realm',
    requested: true,
    realms: [],
    setup: { ready: job.state === 'succeeded' },
    setup_job: started ? job : null
  })

  const rest = vi.fn(async (path: string) => {
    if (path === '/realms/setup/prepare') {
      return { kind: 'realm', action: 'repair', summary: 'Reviewed setup', details: [], consent: 'consent' }
    }

    if (path === '/realms/setup/start') {
      started = true

      return job
    }

    if (path.startsWith('/realms/setup/jobs/first-job?') && job.id !== 'first-job') {
      return { id: 'first-job', state: 'succeeded', message: 'First job completed' }
    }

    if (path.startsWith('/realms/setup/jobs/')) {
      return job
    }

    return snapshot()
  })

  const view = mount(rest, { requested: true })
  fireEvent.click(view.getByRole('button', { name: 'Repair…' }))
  fireEvent.click(await view.findByRole('button', { name: 'Repair' }))
  await view.findByText('First job running')
  await waitFor(() => expect(view.queryByRole('dialog')).toBeNull(), { timeout: 3000 })
  await act(async () => {
    job = { ...job, state: 'succeeded', message: 'First job completed' }
    view.client.setQueryData(
      ['hermes-realms', owner.connectionId, owner.profile, owner.storedSessionId, owner.runtimeSessionId],
      snapshot()
    )
  })
  await waitFor(() => expect(view.queryByRole('button', { name: 'Cancel setup' })).toBeNull())
  await act(async () => {
    job = { id: 'second-job', state: 'running', message: 'Second job running' }
    view.client.setQueryData(
      ['hermes-realms', owner.connectionId, owner.profile, owner.storedSessionId, owner.runtimeSessionId],
      snapshot()
    )
  })
  await view.findByText('Second job running')
  expect(view.getByRole('button', { name: 'Cancel setup' })).toHaveProperty('disabled', false)
})

it('renders polled worker stages without inventing completion or percentages', async () => {
  let job = { id: 'progress-job', state: 'running', message: 'Downloading pinned driver…' }

  const rest = vi.fn(async (path: string) =>
    path.startsWith('/realms/setup/jobs/')
      ? job
      : { kind: 'realm', mode: 'realm', requested: true, realms: [], setup: { ready: false }, setup_job: job }
  )

  const view = mount(rest, { requested: true, setup_job: job })
  await view.findByText(job.message)

  for (const message of ['Verifying downloaded driver…', 'Preparing verified driver…']) {
    const previous = job.message
    job = { ...job, message }
    await act(async () => {
      await view.client.refetchQueries({ type: 'active' })
    })
    await view.findByText(message)
    expect(view.queryByText(previous)).toBeNull()
    expect(view.queryByText('Ready')).toBeNull()
    expect(view.queryByText(/\d+%/)).toBeNull()
    expect(view.getByRole('button', { name: 'Cancel setup' })).toHaveProperty('disabled', false)
  }

  expect(rest).toHaveBeenCalledWith(
    '/realms/setup/jobs/progress-job?runtime_session_id=runtime&stored_session_id=history',
    { scope: owner }
  )
})

it.each([
  ['host', 'Private testing disabled'],
  ['ask', 'Private testing not selected']
])('does not describe a ready %s target as needing repair', (mode, label) => {
  const view = mount(vi.fn(), { requested: true, mode, setup: { ready: true } })
  expect(view.getByText(label)).toBeTruthy()
  expect(view.queryByText('Needs setup or repair')).toBeNull()
  expect(view.getByRole('button', { name: 'Use this desktop…' })).toBeTruthy()
})

it('keeps a late Cancel receipt scoped to the original connection and profile', async () => {
  let complete!: (value: object) => void

  const rest = vi.fn((path: string) => {
    if (path.endsWith('/cancel')) {
      return new Promise(resolve => {
        complete = resolve
      })
    }

    if (path.startsWith('/realms/setup/jobs/')) {
      return Promise.resolve({ id: 'original', state: 'running' })
    }

    return Promise.resolve({ kind: 'realm', mode: 'realm', requested: false, realms: [], setup: { ready: true } })
  })

  const view = mount(rest, { setup_job: { id: 'original', state: 'running' } })
  fireEvent.click(await view.findByRole('button', { name: 'Cancel setup' }))
  view.rerender(view.tree({ ...owner, connectionId: 'other-source', profile: 'other', storedSessionId: 'other-chat' }))
  await act(async () => {
    complete({ id: 'original', state: 'cancelling', message: 'Old job draining' })
  })
  expect(view.queryByText('Old job draining')).toBeNull()
  expect(rest).toHaveBeenCalledWith('/realms/setup/jobs/original/cancel', {
    method: 'POST',
    body: { runtime_session_id: 'runtime', stored_session_id: 'history' },
    scope: owner
  })
  expect(rest.mock.calls.filter(([path]) => path.endsWith('/cancel'))).toHaveLength(1)
})

it('does not claim cancellation when the Cancel request loses connection', async () => {
  const rest = vi.fn(async (path: string) => {
    if (path.endsWith('/cancel')) {
      throw new Error('Connection interrupted')
    }

    return { id: 'running', state: 'running', message: 'Still observing setup' }
  })

  const view = mount(rest, { setup_job: { id: 'running', state: 'running' } })
  fireEvent.click(await view.findByRole('button', { name: 'Cancel setup' }))
  await view.findByText('Connection interrupted')
  expect(view.queryByText('Setup cancelled')).toBeNull()
  expect(view.getByRole('button', { name: 'Cancel setup' })).toHaveProperty('disabled', false)
})

it('does not offer cancellation after activation admission', () => {
  const rest = vi.fn()

  const view = mount(rest, {
    setup_job: { id: 'activating', state: 'running', cancellable: false, message: 'Starting target…' }
  })

  expect(view.getByText('Starting target…')).toBeTruthy()
  const button = view.getByRole('button', { name: 'Cancel setup' })

  expect(button).toHaveProperty('disabled', true)
  fireEvent.click(button)
  expect(rest.mock.calls.some(([path]) => path.endsWith('/cancel'))).toBe(false)
})

it('cancels the running owned job, keeps draining visible, then allows retry', async () => {
  let state = 'running'

  const job = () => ({
    id: 'owned-job',
    kind: 'realm',
    state,
    message: state === 'cancelling' ? 'Waiting for package transaction…' : ''
  })

  const rest = vi.fn(async (path: string) => {
    if (path.endsWith('/cancel')) {
      state = 'cancelling'

      return job()
    }

    if (path.startsWith('/realms/setup/jobs/')) {
      return job()
    }

    return { kind: 'realm', mode: 'ask', realms: [], setup: { ready: false }, setup_job: job() }
  })

  const view = mount(rest, { setup_job: job() })
  const cancel = await view.findByRole('button', { name: 'Cancel setup' })
  fireEvent.click(cancel)
  fireEvent.click(cancel)
  await view.findByText('Waiting for package transaction…')
  expect(view.getByRole('button', { name: 'Cancelling…' })).toHaveProperty('disabled', true)
  expect(rest.mock.calls.filter(([path]) => path.endsWith('/cancel'))).toHaveLength(1)
  expect(rest).toHaveBeenCalledWith('/realms/setup/jobs/owned-job/cancel', {
    method: 'POST',
    body: { runtime_session_id: 'runtime', stored_session_id: 'history' },
    scope: owner
  })
  state = 'cancelled'
  await view.findByText('Setup cancelled', {}, { timeout: 3000 })
  expect(view.queryByRole('button', { name: 'Cancelling…' })).toBeNull()
  expect(view.getByRole('button', { name: 'Retry…' })).toHaveProperty('disabled', false)
})

it('offers only the requested VM setup after a failed selection, without a persistent kind picker', async () => {
  const rest = vi.fn(async (_path: string) => ({
    kind: 'omarchy-vm',
    action: 'install',
    summary: 'Prepare the private VM.',
    details: [],
    consent: 'reviewed-vm-plan'
  }))

  const view = mount(rest, {
    requested: true,
    requested_kind: 'omarchy-vm',
    kind: 'realm',
    vm_setup: { ready: false }
  })

  expect(view.queryByRole('button', { name: 'Realm' })).toBeNull()
  expect(view.queryByRole('button', { name: 'Omarchy VM' })).toBeNull()
  fireEvent.click(view.getByRole('button', { name: 'Set up Omarchy VM…' }))
  await view.findByRole('dialog')
  expect(rest).toHaveBeenCalledWith(
    '/realms/setup/prepare',
    expect.objectContaining({
      body: expect.objectContaining({ kind: 'omarchy-vm' }),
      scope: owner
    })
  )
  expect(rest.mock.calls.some(([path]) => path === '/realms/setup/start')).toBe(false)
})

it('repairs through explicit native confirmation, preserves failures for retry, and reads back readiness', async () => {
  let ready = false
  let fail = true
  const consent = 'pinned-owner-and-revision'

  const rest = vi.fn(async (path: string) => {
    if (path === '/realms/setup/prepare') {
      return {
        kind: 'realm',
        ready: false,
        action: 'repair',
        summary: 'Verify the existing driver.',
        details: [],
        consent
      }
    }

    if (path === '/realms/setup/start') {
      if (fail) {
        throw new Error('Permission denied. Retry setup.')
      }

      return { id: 'job', state: 'running', message: 'Verifying driver…' }
    }

    if (path.startsWith('/realms/setup/jobs/')) {
      ready = true

      return { id: 'job', state: 'succeeded', message: 'Ready' }
    }

    return { kind: 'realm', mode: 'realm', realms: [], setup: { ready } }
  })

  const view = mount(rest)

  expect(view.queryByRole('dialog')).toBeNull()
  fireEvent.click(view.getByRole('button', { name: 'Repair…' }))
  await view.findByRole('dialog')
  expect(rest.mock.calls.some(([path]) => path === '/realms/setup/start')).toBe(false)
  fireEvent.click(view.getByRole('button', { name: 'Cancel' }))
  expect(view.queryByRole('dialog')).toBeNull()
  expect(rest.mock.calls.some(([path]) => path === '/realms/setup/start')).toBe(false)
  fireEvent.click(view.getByRole('button', { name: 'Repair…' }))
  await view.findByRole('dialog')
  fireEvent.click(view.getByRole('button', { name: 'Repair' }))
  await view.findByText('Permission denied. Retry setup.')
  expect(view.getByRole('dialog')).toBeTruthy()
  fail = false
  fireEvent.click(view.getByRole('button', { name: 'Repair' }))
  await waitFor(() => expect(view.queryByRole('dialog')).toBeNull())
  await view.findByText('Ready')

  const call = rest.mock.calls.find(([path]) => path === '/realms/setup/start') as unknown as [
    string,
    { scope: typeof owner; body: object }
  ]

  expect(call[1].scope).toEqual(owner)
  expect(call[1].body).toEqual({ runtime_session_id: 'runtime', stored_session_id: 'history', kind: 'realm', consent })
  expect(view.container.textContent).not.toContain('install-driver')
})

it('makes the VM choice discoverable without switching before consent or opening a stale owner dialog', async () => {
  let resolveReview!: (value: object) => void

  const rest = vi.fn((path: string) =>
    path === '/realms/setup/prepare'
      ? new Promise(resolve => {
          resolveReview = resolve
        })
      : Promise.resolve({ kind: 'realm', mode: 'realm', realms: [], setup: { ready: false } })
  )

  const view = mount(rest)

  fireEvent.click(view.getByRole('button', { name: 'Omarchy VM' }))
  fireEvent.click(view.getByRole('button', { name: 'Set up Omarchy VM…' }))
  expect(rest.mock.calls.some(([path]) => path === '/realms/setup/start')).toBe(false)
  view.rerender(view.tree({ ...owner, profile: 'other', storedSessionId: 'other-history' }))
  await act(async () => {
    resolveReview({
      kind: 'omarchy-vm',
      ready: false,
      action: 'install',
      summary: 'Download and build the VM.',
      details: [],
      consent: 'old'
    })
  })
  expect(view.queryByRole('dialog')).toBeNull()
  expect(rest.mock.calls.some(([path]) => path === '/realms/setup/start')).toBe(false)
})
