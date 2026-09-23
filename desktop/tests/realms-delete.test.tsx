import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react'
import { type ComponentType } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

// @ts-expect-error Runtime plugins are plain JavaScript SDK consumers.
import plugin, { realmQueryOptions } from '../../desktop/plugin.js'

import { SESSION_AREAS, type SessionContribution, type SessionContributionProps } from '@/contrib/session'

const owner = { connectionId: 'local', profile: 'default', storedSessionId: 'history', runtimeSessionId: null }
const clients: QueryClient[] = []

afterEach(() => {
  cleanup()
  clients.splice(0).forEach(client => client.clear())
  vi.useRealTimers()
})

function mount(rest: ReturnType<typeof vi.fn>, kind: string, state = 'stopped') {
  const renders = new Map<string, ComponentType<SessionContributionProps>>()

  const ctx = {
    rest,
    register: ({ area, data }: { area: string; data: SessionContribution }) => renders.set(area, data.render)
  }

  plugin.register(ctx)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)

  const snapshot = (stored = owner.storedSessionId) => ({
    requested: true,
    kind,
    mode: 'realm',
    setup: { ready: true },
    vm_setup: { ready: true },
    realms: [{ id: 'owned', kind, state, stored_session_id: stored }]
  })

  client.setQueryData(realmQueryOptions(ctx, owner).queryKey, snapshot())
  const Row = renders.get(SESSION_AREAS.statusStack)!

  const tree = (session = owner) => (
    <QueryClientProvider client={client}>
      <Row session={session} />
    </QueryClientProvider>
  )

  return { ...render(tree()), tree, client, ctx, snapshot }
}

it('keeps confirmed deletion distinct from a failed status refresh', async () => {
  let available = false

  const rest = vi.fn(async (path: string) => {
    if (path.endsWith('/delete/prepare')) {
      return {
        realm_id: 'owned',
        kind: 'realm',
        state: 'stopped',
        consent: 'reviewed',
        details: []
      }
    }

    if (path.endsWith('/delete/confirm')) {
      return { realm_id: 'owned', deleted: true }
    }

    if (!available) {
      throw new Error('status unavailable')
    }

    return { requested: false, kind: 'realm', mode: 'realm', realms: [] }
  })

  const view = mount(rest, 'realm')
  fireEvent.keyDown(view.getByRole('button', { name: 'Manage private testing' }), { key: 'Enter' })
  fireEvent.click(await view.findByRole('menuitem', { name: 'Delete workspace…' }))
  const dialog = await view.findByRole('dialog')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete workspace' }))
  await view.findByText('Workspace deleted. Refresh status to update this view.', {}, { timeout: 4000 })
  available = true
  fireEvent.click(view.getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(view.queryByRole('status')).toBeNull())
  expect(rest.mock.calls.filter(([path]) => path.endsWith('/delete/confirm'))).toHaveLength(1)
})

it('keeps a replacement review open after an old confirmed dialog finishes', async () => {
  let finish!: (value: unknown) => void

  const pending = new Promise(resolve => {
    finish = resolve
  })

  const rest = vi.fn((path: string) => {
    if (path.endsWith('/delete/prepare')) {
      return Promise.resolve({
        realm_id: 'owned',
        kind: 'realm',
        state: 'stopped',
        consent: 'reviewed',
        details: []
      })
    }

    if (path.endsWith('/delete/confirm')) {
      return pending
    }

    return Promise.resolve({ realms: [] })
  })

  const view = mount(rest, 'realm')
  const other = { ...owner, storedSessionId: 'other' }
  view.client.setQueryData(realmQueryOptions(view.ctx, other).queryKey, view.snapshot('other'))

  const openReview = async () => {
    fireEvent.keyDown(view.getByRole('button', { name: 'Manage private testing' }), { key: 'Enter' })
    fireEvent.click(await view.findByRole('menuitem', { name: 'Delete workspace…' }))

    return view.findByRole('dialog')
  }

  fireEvent.click(within(await openReview()).getByRole('button', { name: 'Delete workspace' }))
  await waitFor(() => expect(rest.mock.calls.some(([path]) => path.endsWith('/delete/confirm'))).toBe(true))
  view.rerender(view.tree(other))
  const replacement = await openReview()
  vi.useFakeTimers()
  await act(async () => {
    finish({ realm_id: 'owned', deleted: true })
    await pending
  })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000)
  })
  expect(view.getByRole('dialog')).toBe(replacement)
  expect(within(replacement).getByRole('button', { name: 'Delete workspace' })).toBeTruthy()
  expect(rest.mock.calls.filter(([path]) => path.endsWith('/delete/confirm'))).toHaveLength(1)
})

it.each(['connectionId', 'profile', 'storedSessionId'])(
  'drops an in-flight deletion review after %s changes, including switch-back',
  async field => {
    let complete!: (value: unknown) => void

    const pending = new Promise(resolve => {
      complete = resolve
    })

    const rest = vi.fn((path: string) => (path.endsWith('/delete/prepare') ? pending : Promise.resolve({ realms: [] })))
    const view = mount(rest, 'realm')
    const other = { ...owner, [field]: 'other' }
    view.client.setQueryData(realmQueryOptions(view.ctx, other).queryKey, view.snapshot(other.storedSessionId))
    fireEvent.keyDown(view.getByRole('button', { name: 'Manage private testing' }), { key: 'Enter' })
    fireEvent.click(await view.findByRole('menuitem', { name: 'Delete workspace…' }))
    await waitFor(() => expect(rest.mock.calls.some(([path]) => path.endsWith('/delete/prepare'))).toBe(true))
    view.rerender(view.tree(other))
    view.rerender(view.tree())
    await act(async () => {
      complete({ realm_id: 'owned', kind: 'realm', state: 'stopped', consent: 'stale', details: [] })
      await pending
    })
    expect(view.queryByRole('dialog')).toBeNull()
    expect(rest.mock.calls.some(([path]) => path.endsWith('/delete/confirm'))).toBe(false)
  }
)

it('never offers an implicit Stop-and-Delete for live compute', async () => {
  const rest = vi.fn(async () => ({ realms: [] }))
  const view = mount(rest, 'realm', 'live')
  fireEvent.keyDown(view.getByRole('button', { name: 'Manage private testing' }), { key: 'Enter' })
  const item = await view.findByRole('menuitem', { name: 'Delete workspace…' })
  expect(item.getAttribute('aria-disabled')).toBe('true')
  fireEvent.click(item)
  expect(rest).not.toHaveBeenCalled()
})

it.each(['realm', 'omarchy-vm'])('requires a fresh review to retry an interrupted %s deletion', async kind => {
  const rest = vi.fn(async (path: string) => {
    if (path.endsWith('/delete/prepare')) {
      return {
        realm_id: 'owned',
        kind,
        state: 'deleting',
        consent: 'interrupted-snapshot',
        details: ['Partial deletion is retained.']
      }
    }

    if (path.endsWith('/delete/confirm')) {
      throw new Error('inert private diagnostic')
    }

    return {
      requested: true,
      mode: 'realm',
      kind,
      realms: [{ id: 'owned', kind, state: 'deleting', stored_session_id: 'history' }]
    }
  })

  const view = mount(rest, kind, 'deleting')
  expect(view.container.textContent).toContain('deleting')
  fireEvent.keyDown(view.getByRole('button', { name: 'Manage private testing' }), { key: 'Enter' })
  fireEvent.click(await view.findByRole('menuitem', { name: 'Retry deletion…' }))
  const dialog = await view.findByRole('dialog')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete workspace' }))
  await within(dialog).findByText(/Close and review again/)
  expect(dialog.textContent).not.toContain('inert private diagnostic')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete workspace' }))
  await act(async () => {})
  expect(rest.mock.calls.filter(([path]) => path.endsWith('/delete/confirm'))).toHaveLength(1)
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  await waitFor(() => expect(view.queryByRole('dialog')).toBeNull())
  fireEvent.keyDown(view.getByRole('button', { name: 'Manage private testing' }), { key: 'Enter' })
  fireEvent.click(await view.findByRole('menuitem', { name: 'Retry deletion…' }))
  await view.findByRole('dialog')
  expect(rest.mock.calls.filter(([path]) => path.endsWith('/delete/prepare'))).toHaveLength(2)
})

it.each(['realm', 'omarchy-vm'])(
  'reviews and cancels before explicitly deleting only the selected %s workspace',
  async kind => {
    const rest = vi.fn(async (path: string) => {
      if (path.endsWith('/delete/prepare')) {
        return {
          realm_id: 'owned',
          kind,
          state: 'stopped',
          consent: 'reviewed-snapshot',
          details: ['Only this retained workspace is removed.']
        }
      }

      if (path.endsWith('/delete/confirm')) {
        return { realm_id: 'owned', deleted: true }
      }

      return { requested: false, mode: 'realm', kind, realms: [] }
    })

    const view = mount(rest, kind)

    const openReview = async () => {
      fireEvent.keyDown(view.getByRole('button', { name: 'Manage private testing' }), { key: 'Enter' })
      fireEvent.click(await view.findByRole('menuitem', { name: 'Delete workspace…' }))

      return view.findByRole('dialog')
    }

    const dialog = await openReview()
    expect(within(dialog).getByText(/Export any work you need/)).toBeTruthy()
    const cancel = within(dialog).getByRole('button', { name: 'Cancel' })
    cancel.focus()
    fireEvent.keyDown(cancel, { key: 'Enter' })
    expect(rest.mock.calls.some(([path]) => path.endsWith('/delete/confirm'))).toBe(false)
    fireEvent.click(cancel)
    await waitFor(() => expect(view.queryByRole('dialog')).toBeNull())
    const reviewed = await openReview()
    fireEvent.click(within(reviewed).getByRole('button', { name: 'Delete workspace' }))
    await waitFor(() =>
      expect(rest).toHaveBeenCalledWith('/realms/owned/delete/confirm', {
        method: 'POST',
        body: { stored_session_id: 'history', consent: 'reviewed-snapshot' },
        scope: owner
      })
    )
    await waitFor(() => expect(view.queryByRole('dialog')).toBeNull())
    expect(view.queryByRole('button', { name: 'Manage private testing' })).toBeNull()
    expect(rest.mock.calls.filter(([path]) => path.endsWith('/delete/confirm'))).toHaveLength(1)
  }
)
