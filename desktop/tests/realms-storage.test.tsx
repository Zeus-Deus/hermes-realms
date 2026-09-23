import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

// @ts-expect-error Runtime plugin modules are plain JavaScript SDK consumers.
import { RealmStorageSettings } from '../../desktop/storage-controls.js'

const scope = { connectionId: 'local', profile: 'test-profile' }
const clients: QueryClient[] = []
afterEach(() => {
  cleanup()
  clients.splice(0).forEach(client => client.clear())
})

function mount(rest: ReturnType<typeof vi.fn>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  const ctx = { rest }

  const tree = (selected = scope) => (
    <QueryClientProvider client={client}>
      <RealmStorageSettings ctx={ctx} scope={selected} />
    </QueryClientProvider>
  )

  return { ...render(tree()), tree, client }
}

it('reads storage only when opened, with explicit scope and no update download', async () => {
  const rest = vi.fn(async () => ({
    base: { present: true, version: '4.0.3' },
    storage: { iso_bytes: 1024, base_bytes: 2048, session_bytes: 4096 }
  }))

  const view = mount(rest)
  expect(rest).not.toHaveBeenCalled()
  fireEvent.click(view.getByRole('button', { name: 'Storage and base image' }))
  await view.findByText('4.0.3')
  expect(view.getByText('1 KiB')).toBeTruthy()
  expect(view.getByText('2 KiB')).toBeTruthy()
  expect(view.getByText('4 KiB')).toBeTruthy()
  expect(rest).toHaveBeenCalledWith('/realms/vm/settings', { scope })
  expect(rest.mock.calls).toHaveLength(1)
})

it('keeps another source isolated from a late storage result and labels unknown sizes honestly', async () => {
  let finish!: (value: unknown) => void

  const pending = new Promise(resolve => {
    finish = resolve
  })

  const other = { connectionId: 'remote-test', profile: 'other-profile' }

  const rest = vi.fn((_path: string, options: { scope: typeof scope }) =>
    options.scope.connectionId === 'local'
      ? pending
      : Promise.resolve({ base: { present: false }, storage: { iso_bytes: 0, base_bytes: null, session_bytes: -1 } })
  )

  const view = mount(rest)
  fireEvent.click(view.getByRole('button', { name: 'Storage and base image' }))
  view.rerender(view.tree(other))
  fireEvent.click(view.getByRole('button', { name: 'Storage and base image' }))
  await view.findByText('Not prepared')
  await act(async () => {
    finish({ base: { present: true, version: 'previous-source' }, storage: {} })
    await pending
  })
  expect(view.queryByText('previous-source')).toBeNull()
  expect(view.getByText('0 B')).toBeTruthy()
  expect(view.getAllByText('Unknown')).toHaveLength(2)
  expect(rest).toHaveBeenCalledWith('/realms/vm/settings', { scope: other })
})

const settings = {
  base: { present: true, version: '4.0.3' },
  storage: {},
  update: { installed: '4.0.3', latest: '4.0.4', available: true }
}

const proposal = (binding = scope) => ({
  operation: 'vm-base-update',
  scope: 'profile',
  kind: 'omarchy-vm',
  release: '4.0.4',
  review_binding: binding,
  consent: 'reviewed-digest',
  summary: 'Prepare base 4.0.4',
  details: ['Retained workspaces keep their base.'],
  blockers: []
})

async function openStorage(view: ReturnType<typeof mount>) {
  fireEvent.click(view.getByRole('button', { name: 'Storage and base image' }))
  await view.findByText('4.0.3')
}

async function check(view: ReturnType<typeof mount>) {
  fireEvent.click(view.getByRole('button', { name: 'Check for updates' }))

  return view.findByRole('dialog')
}

it('renders Update review with valid inline disclosure and keyboard-only non-consent', async () => {
  const diagnostics = vi.spyOn(console, 'error')

  const reviewed = {
    ...proposal(),
    details: ['Download the reviewed ISO.', 'Install and verify the new base.', 'Retained workspaces keep their base.']
  }

  const rest = vi.fn(async (path: string) => (path.endsWith('/prepare') ? reviewed : settings))

  // jsdom does not synthesize a button's native click from keyboard events.
  const activate = (button: HTMLElement, key: string) => {
    button.focus()
    expect(button.ownerDocument.activeElement).toBe(button)
    expect(fireEvent.keyDown(button, { key })).toBe(true)
    expect(rest.mock.calls.some(([path]) => path.endsWith('/start'))).toBe(false)

    if (key === 'Enter') {
      fireEvent.click(button, { detail: 0 })
    }

    fireEvent.keyUp(button, { key })

    if (key === ' ') {
      fireEvent.click(button, { detail: 0 })
    }
  }

  try {
    const view = mount(rest)
    await openStorage(view)
    const dialog = await check(view)
    expect(diagnostics.mock.calls).toEqual([])
    const description = dialog.ownerDocument.getElementById(dialog.getAttribute('aria-describedby')!)!
    expect(description.tagName).toBe('P')
    expect(description.textContent).toContain(reviewed.summary)
    expect(description.textContent).toContain('local · test-profile')
    const details = within(dialog).getByRole('button', { name: 'Details' })
    const content = dialog.ownerDocument.getElementById(details.getAttribute('aria-controls')!)!
    expect(content).not.toBeNull()
    expect(description.contains(details)).toBe(true)
    expect(description.contains(content)).toBe(true)
    expect(details.getAttribute('aria-expanded')).toBe('false')
    expect(content.hidden).toBe(true)
    const requestsBeforeDisclosure = [...rest.mock.calls]

    expect(details.tagName).toBe('BUTTON')
    expect(details.getAttribute('type')).toBe('button')
    expect(dialog.ownerDocument.activeElement).toBe(within(dialog).getByRole('button', { name: 'Update' }))

    for (const key of ['Enter', ' ']) {
      activate(details, key)
      expect(details.getAttribute('aria-expanded')).toBe('true')
      expect(content.hidden).toBe(false)

      for (const text of reviewed.details) {
        expect(content.contains(within(content).getByText(text))).toBe(true)
      }

      expect(rest.mock.calls).toEqual(requestsBeforeDisclosure)
      activate(details, key)
      expect(details.getAttribute('aria-expanded')).toBe('false')
      expect(content.hidden).toBe(true)

      for (const text of reviewed.details) {
        expect(within(content).getByText(text).closest('[hidden]')).toBe(content)
      }

      expect(rest.mock.calls).toEqual(requestsBeforeDisclosure)
    }

    expect(diagnostics.mock.calls).toEqual([])
    activate(details, 'Enter')
    expect(content.hidden).toBe(false)
    activate(within(dialog).getByRole('button', { name: 'Later' }), 'Enter')
    await waitFor(() => expect(view.queryByRole('dialog')).toBeNull())
    const reopened = await check(view)
    expect(within(reopened).getByRole('button', { name: 'Details' }).getAttribute('aria-expanded')).toBe('false')
    activate(within(reopened).getByRole('button', { name: 'Later' }), ' ')
    await waitFor(() => expect(view.queryByRole('dialog')).toBeNull())
    expect(rest.mock.calls.some(([path]) => path.endsWith('/start'))).toBe(false)
  } finally {
    diagnostics.mockRestore()
  }
})

it('checks explicitly and Later dismisses the native review without starting an update', async () => {
  const rest = vi.fn(async (path: string) => (path.endsWith('/prepare') ? proposal() : settings))
  const view = mount(rest)
  await openStorage(view)
  expect(rest.mock.calls.map(([path]) => path)).toEqual(['/realms/vm/settings'])
  const dialog = await check(view)
  expect(rest).toHaveBeenCalledWith('/realms/vm/settings?check_updates=true', { scope })
  expect(rest).toHaveBeenCalledWith('/realms/vm/update/prepare', {
    method: 'POST',
    body: { release: '4.0.4', review_binding: scope },
    scope
  })
  expect(within(dialog).getByText('Details')).toBeTruthy()
  expect(dialog.textContent).toContain('local · test-profile')
  const later = within(dialog).getByRole('button', { name: 'Later' })

  for (const key of ['Enter', ' ']) {
    fireEvent.keyDown(later, { key })
  }

  expect(rest.mock.calls.some(([path]) => path.endsWith('/start'))).toBe(false)
  fireEvent.click(later)
  await waitFor(() => expect(view.queryByRole('dialog')).toBeNull())
  await check(view)
  fireEvent.keyDown(view.getByRole('dialog'), { key: 'Escape' })
  await waitFor(() => expect(view.queryByRole('dialog')).toBeNull())
  expect(
    rest.mock.calls.every(([path]) =>
      ['/realms/vm/settings', '/realms/vm/settings?check_updates=true', '/realms/vm/update/prepare'].includes(path)
    )
  ).toBe(true)
})

it('starts only the exact reviewed release and binding after explicit Update', async () => {
  const rest = vi.fn(async (path: string) =>
    path.endsWith('/prepare')
      ? proposal()
      : path.endsWith('/start')
        ? {
            id: 'a'.repeat(32),
            operation: 'vm-base-update',
            scope: 'profile',
            kind: 'omarchy-vm',
            state: 'running',
            message: 'Installing reviewed base',
            cancellable: true
          }
        : settings
  )

  const view = mount(rest)
  await openStorage(view)
  const dialog = await check(view)
  expect(rest.mock.calls.some(([path]) => path.endsWith('/start'))).toBe(false)
  fireEvent.click(within(dialog).getByRole('button', { name: 'Update' }))
  await waitFor(() =>
    expect(rest).toHaveBeenCalledWith('/realms/vm/update/start', {
      method: 'POST',
      body: { release: '4.0.4', review_binding: scope, consent: 'reviewed-digest' },
      scope
    })
  )
})

it.each([
  { consent: '' },
  { release: '4.0.5' },
  { review_binding: { ...scope, profile: 'foreign' } },
  { operation: 'session-setup' },
  { scope: 'session' },
  { kind: 'realm' },
  { details: [12] },
  { blockers: ['Stop another setup first'] },
  { blockers: null },
  { summary: null }
])('refuses missing, blocked or inconsistent proposals %j', async invalid => {
  const rest = vi.fn(async (path: string) => (path.endsWith('/prepare') ? { ...proposal(), ...invalid } : settings))
  const view = mount(rest)
  await openStorage(view)
  fireEvent.click(view.getByRole('button', { name: 'Check for updates' }))
  await view.findByText('Update review unavailable or changed. Check again.')
  expect(view.queryByRole('dialog')).toBeNull()
  expect(rest.mock.calls.some(([path]) => path.endsWith('/start'))).toBe(false)
})
it.each(['4.0.4/evil', ' 4.0.4', 'v4.0.4', null])('does not normalize malformed latest release %j', async latest => {
  const rest = vi.fn(async () => ({ ...settings, update: { ...settings.update, latest } }))
  const view = mount(rest)
  await openStorage(view)
  fireEvent.click(view.getByRole('button', { name: 'Check for updates' }))
  await view.findByText('Update review unavailable or changed. Check again.')
  expect(rest.mock.calls).toHaveLength(2)
})

function deferred() {
  let resolve!: (value: unknown) => void
  let reject!: (reason: Error) => void

  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })

  return { promise, resolve, reject }
}

it.each(['check', 'prepare'])('abandons stale %s promises before further review requests', async stage => {
  const pending = deferred()

  const rest = vi.fn(async (path: string, options: { scope: typeof scope }) => {
    if (
      options.scope.profile === scope.profile &&
      (stage === 'check' ? path.includes('?') : path.endsWith('/prepare'))
    ) {
      return pending.promise
    }

    return path.endsWith('/prepare') ? proposal(options.scope) : settings
  })

  const view = mount(rest)
  await openStorage(view)
  fireEvent.click(view.getByRole('button', { name: 'Check for updates' }))
  await waitFor(() =>
    expect(rest.mock.calls.some(([path]) => (stage === 'check' ? path.includes('?') : path.endsWith('/prepare')))).toBe(
      true
    )
  )
  const other = { ...scope, profile: 'replacement' }
  view.rerender(view.tree(other))
  await openStorage(view)
  const replacement = await check(view)
  await act(async () => {
    pending.resolve(stage === 'check' ? settings : proposal())
    await pending.promise
  })
  expect(view.getByRole('dialog')).toBe(replacement)
  expect(
    rest.mock.calls.filter(([path, options]) => path.endsWith('/prepare') && options.scope.profile === scope.profile)
  ).toHaveLength(stage === 'check' ? 0 : 1)
})

const job = (state = 'running', id = 'a'.repeat(32)) => ({
  id,
  operation: 'vm-base-update',
  scope: 'profile',
  kind: 'omarchy-vm',
  state,
  message: `Base update ${state}`,
  cancellable: state === 'running'
})

it('recovers profile progress on remount and cancels only its current update', async () => {
  const rest = vi.fn(async (path: string) =>
    path.endsWith('/cancel')
      ? job('cancelling')
      : path.includes('/jobs/')
        ? job()
        : { ...settings, base_update_job: job() }
  )

  const view = mount(rest)
  await openStorage(view)
  await view.findByText('Base update running')
  fireEvent.click(view.getByRole('button', { name: 'Cancel update' }))
  await view.findByText('Base update cancelling')
  expect(rest).toHaveBeenCalledWith(`/realms/vm/update/jobs/${'a'.repeat(32)}/cancel`, {
    method: 'POST',
    body: {},
    scope
  })
  expect(view.queryByRole('button', { name: 'Cancel update' })).toBeNull()
  expect(rest.mock.calls.every(([path]) => path.startsWith('/realms/vm/'))).toBe(true)
})

it('retains an accepted start through failed reads and Retry never resubmits it', async () => {
  let started = false
  let readable = false

  const rest = vi.fn(async (path: string) => {
    if (path.endsWith('/prepare')) {
      return proposal()
    }

    if (path.endsWith('/start')) {
      started = true

      return job()
    }

    if (started && !readable) {
      throw new Error('offline')
    }

    return path.includes('/jobs/')
      ? job('succeeded')
      : { ...settings, base_update_job: started ? job('succeeded') : null }
  })

  const view = mount(rest)
  await openStorage(view)
  fireEvent.click(within(await check(view)).getByRole('button', { name: 'Update' }))
  await waitFor(() => expect(view.queryByRole('dialog')).toBeNull())
  await view.findByText('Connection interrupted. Refresh update status.')
  expect(view.getByText('Base update running')).toBeTruthy()
  readable = true
  fireEvent.click(view.getByRole('button', { name: 'Retry…' }))
  await view.findByText('Base update succeeded')
  expect(rest.mock.calls.filter(([path]) => path.endsWith('/start'))).toHaveLength(1)
})

it.each(['rejected', 'malformed'])(
  'invalidates %s start consent and recovers latest instead of retrying mutation',
  async outcome => {
    let attempted = false

    const rest = vi.fn(async (path: string) => {
      if (path.endsWith('/prepare')) {
        return proposal()
      }

      if (path.endsWith('/start')) {
        attempted = true

        if (outcome === 'rejected') {
          throw new Error('uncertain transport')
        }

        return {}
      }

      return { ...settings, base_update_job: attempted ? job() : null }
    })

    const view = mount(rest)
    await openStorage(view)
    const dialog = await check(view)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Update' }))
    await waitFor(() => expect(rest.mock.calls.filter(([path]) => path === '/realms/vm/settings')).toHaveLength(2))
    await view.findByText('Update outcome unknown. Refresh status before checking again.')

    if (view.queryByRole('dialog')) {
      fireEvent.click(within(dialog).getByRole('button', { name: 'Update' }))
    }

    expect(rest.mock.calls.filter(([path]) => path.endsWith('/start'))).toHaveLength(1)
    await view.findByText('Base update running')
  }
)

it('prefers terminal settings over stale running poll cache and acknowledges starts before a later job', async () => {
  const rest = vi.fn(async (path: string) =>
    path.includes('/jobs/') ? job() : { ...settings, base_update_job: job() }
  )

  const view = mount(rest)
  await openStorage(view)
  await view.findByText('Base update running')
  const key = ['hermes-realms-storage', scope.connectionId, scope.profile]
  await act(async () => {
    view.client.setQueryData(key, { ...settings, base_update_job: job('succeeded') })
  })
  await view.findByText('Base update succeeded', {}, { timeout: 1000 })
  expect(view.queryByRole('button', { name: 'Cancel update' })).toBeNull()
  await act(async () => {
    view.client.setQueryData(key, { ...settings, base_update_job: job('failed', 'b'.repeat(32)) })
  })
  await view.findByText('Base update failed')
})
it('retires the accepted start bridge after owner acknowledgement so a newer update is visible', async () => {
  let accepted = false

  const rest = vi.fn(async (path: string) => {
    if (path.endsWith('/prepare')) {
      return proposal()
    }

    if (path.endsWith('/start')) {
      accepted = true

      return job()
    }

    if (path.includes('/jobs/')) {
      return job()
    }

    return { ...settings, base_update_job: accepted ? job() : job('failed', 'c'.repeat(32)) }
  })

  const view = mount(rest)
  await openStorage(view)
  fireEvent.click(within(await check(view)).getByRole('button', { name: 'Update' }))
  await waitFor(() => expect(view.queryByRole('dialog')).toBeNull())
  await view.findByText('Base update running')
  await act(async () => {
    view.client.setQueryData(['hermes-realms-storage', scope.connectionId, scope.profile], {
      ...settings,
      base_update_job: job('succeeded', 'b'.repeat(32))
    })
  })
  await view.findByText('Base update succeeded', {}, { timeout: 1000 })
})

it('keeps a replacement review after an old start and delayed native dialog close', async () => {
  const pending = deferred()

  const rest = vi.fn(async (path: string, options: { scope: typeof scope }) =>
    path.endsWith('/start') ? pending.promise : path.endsWith('/prepare') ? proposal(options.scope) : settings
  )

  const view = mount(rest)
  await openStorage(view)
  fireEvent.click(within(await check(view)).getByRole('button', { name: 'Update' }))
  view.rerender(view.tree({ ...scope, connectionId: 'remote' }))
  await openStorage(view)
  const replacement = await check(view)
  vi.useFakeTimers()
  await act(async () => {
    pending.resolve(job())
    await pending.promise
    await vi.advanceTimersByTimeAsync(1000)
  })
  vi.useRealTimers()
  expect(view.getByRole('dialog')).toBe(replacement)
  expect(within(replacement).getByRole('button', { name: 'Update' })).toBeTruthy()
  expect(rest.mock.calls.filter(([path]) => path.endsWith('/start'))).toHaveLength(1)
})
it.each([{ operation: 'session-setup' }, { scope: 'session' }, { id: '../foreign' }, { id: 'b'.repeat(32) }])(
  'rejects mismatched polled job %j without session fallback',
  async invalid => {
    const rest = vi.fn(async (path: string) =>
      path.includes('/jobs/') ? { ...job(), ...invalid } : { ...settings, base_update_job: job() }
    )

    const view = mount(rest)
    await openStorage(view)
    await view.findByText('Connection interrupted. Refresh update status.')
    expect(rest.mock.calls.every(([path]) => path.startsWith('/realms/vm/'))).toBe(true)
  }
)
it('does not inspect or cancel session jobs returned in profile settings', async () => {
  const rest = vi.fn(async () => ({
    ...settings,
    base_update_job: { ...job(), operation: 'session-setup', scope: 'session' }
  }))

  const view = mount(rest)
  await openStorage(view)
  expect(view.queryByRole('button', { name: 'Cancel update' })).toBeNull()
  expect(rest.mock.calls).toHaveLength(1)
})
it('refreshes storage after a terminal job without rechecking latest releases', async () => {
  let complete = false

  const rest = vi.fn(async (path: string) => {
    if (path.includes('/jobs/')) {
      complete = true

      return job('succeeded')
    }

    return {
      ...settings,
      base: { present: true, version: complete ? '4.0.4' : '4.0.3' },
      base_update_job: job(complete ? 'succeeded' : 'running')
    }
  })

  const view = mount(rest)
  fireEvent.click(view.getByRole('button', { name: 'Storage and base image' }))
  await view.findByText('Base update succeeded')
  await view.findByText('4.0.4', {}, { timeout: 1000 })
  expect(rest.mock.calls.some(([path]) => path.includes('check_updates'))).toBe(false)
})

it.each([false, null])('reports explicit check availability %j without preparing or starting', async available => {
  const rest = vi.fn(async () => ({ ...settings, update: { ...settings.update, available } }))
  const view = mount(rest)
  await openStorage(view)
  fireEvent.click(view.getByRole('button', { name: 'Check for updates' }))
  await view.findByText(available === false ? 'Base image is up to date.' : 'Latest base version unavailable.')
  expect(rest.mock.calls).toHaveLength(2)
})
it('stops automatic polling after a read error until explicit Retry', async () => {
  const rest = vi.fn(async (path: string) => {
    if (path.includes('/jobs/')) {
      throw new Error('offline')
    }

    return { ...settings, base_update_job: job() }
  })

  const view = mount(rest)
  await openStorage(view)
  await view.findByText('Connection interrupted. Refresh update status.')
  const count = rest.mock.calls.length
  await act(async () => {
    await new Promise(resolve => setTimeout(resolve, 1500))
  })
  expect(rest.mock.calls).toHaveLength(count)
})

it('offers read-only Retry after an uncertain cancellation', async () => {
  const rest = vi.fn(async (path: string) => {
    if (path.endsWith('/cancel')) {
      throw new Error('lost response')
    }

    return path.includes('/jobs/') ? job() : { ...settings, base_update_job: job() }
  })

  const view = mount(rest)
  await openStorage(view)
  fireEvent.click(await view.findByRole('button', { name: 'Cancel update' }))
  await view.findByText('Connection interrupted. Refresh update status.')
  fireEvent.click(view.getByRole('button', { name: 'Retry…' }))
  await waitFor(() => expect(view.queryByText('Connection interrupted. Refresh update status.')).toBeNull())
  expect(rest.mock.calls.filter(([path]) => path.endsWith('/cancel'))).toHaveLength(1)
})

it('recovers a running profile job in a fresh component after unmount', async () => {
  const rest = vi.fn(async (path: string) =>
    path.includes('/jobs/') ? job() : { ...settings, base_update_job: job() }
  )

  const first = mount(rest)
  await openStorage(first)
  await first.findByText('Base update running')
  first.unmount()
  const second = mount(rest)
  await openStorage(second)
  await second.findByText('Base update running')
  expect(rest.mock.calls.filter(([path]) => path === '/realms/vm/settings')).toHaveLength(2)
  expect(rest.mock.calls.some(([path]) => path.endsWith('/start'))).toBe(false)
})
