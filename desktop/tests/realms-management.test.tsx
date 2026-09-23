import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { type ComponentType } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

// @ts-expect-error Runtime plugins are plain JavaScript SDK consumers.
import realmsPlugin, { realmQueryOptions } from '../../desktop/plugin.js'

import { SESSION_AREAS, type SessionContribution, type SessionContributionProps } from '@/contrib/session'

const session = { connectionId: 'local', profile: 'default', storedSessionId: 'history', runtimeSessionId: null }
const clients: QueryClient[] = []

const state = (stored = 'history') => ({
  requested: true,
  kind: 'realm',
  mode: 'realm',
  setup: { ready: true },
  realms: [{ id: 'owned', kind: 'realm', stored_session_id: stored, state: 'live', window_count: 1 }]
})

afterEach(() => {
  cleanup()
  clients.splice(0).forEach(client => client.clear())
})

function harness(rest: ReturnType<typeof vi.fn>) {
  const renders = new Map<string, ComponentType<SessionContributionProps>>()

  const ctx = {
    rest,
    register: ({ area, data }: { area: string; data: SessionContribution }) => renders.set(area, data.render)
  }

  realmsPlugin.register(ctx)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  client.setQueryData(realmQueryOptions(ctx, session).queryKey, state())
  const StatusRow = renders.get(SESSION_AREAS.statusStack)!

  return { ctx, client, StatusRow }
}

it.each([
  ['stop', 'Stop private testing'],
  ['off', 'Disable use for this session']
])('dispatches %s from the scoped management menu and refreshes status', async (action, label) => {
  let posted = false
  const after = state()

  if (action === 'off') {
    after.mode = 'host'
  } else {
    after.realms[0].state = 'stopped'
  }

  const rest = vi.fn(async (path: string, _options?: unknown) => {
    if (path === '/realms/session/action') {
      posted = true
    }

    return posted ? after : state()
  })

  const { client, StatusRow } = harness(rest)

  const view = render(
    <QueryClientProvider client={client}>
      <StatusRow session={session} />
    </QueryClientProvider>
  )

  expect(view.container.querySelector('[data-realms-setup]')).toBeNull()
  fireEvent.keyDown(view.getByRole('button', { name: 'Manage private testing' }), { key: 'Enter' })
  fireEvent.click(await view.findByRole('menuitem', { name: label }))
  await waitFor(() =>
    expect(rest).toHaveBeenCalledWith('/realms/session/action', {
      method: 'POST',
      body: { stored_session_id: 'history', action },
      scope: session
    })
  )
  await waitFor(() =>
    expect(view.container.textContent).toContain(action === 'off' ? 'Private testing disabled' : 'Realm · stopped')
  )
})

it('does not repaint another session from a late management failure', async () => {
  let reject!: (error: Error) => void

  const pending = new Promise((_, fail) => {
    reject = fail
  })

  const rest = vi.fn((path: string) => (path === '/realms/session/action' ? pending : Promise.resolve(state())))
  const { ctx, client, StatusRow } = harness(rest)
  const other = { ...session, storedSessionId: 'other' }
  client.setQueryData(realmQueryOptions(ctx, other).queryKey, state('other'))

  const view = render(
    <QueryClientProvider client={client}>
      <StatusRow session={session} />
    </QueryClientProvider>
  )

  fireEvent.keyDown(view.getByRole('button', { name: 'Manage private testing' }), { key: 'Enter' })
  fireEvent.click(await view.findByRole('menuitem', { name: 'Disable use for this session' }))
  await waitFor(() => expect(rest).toHaveBeenCalledTimes(1))
  view.rerender(
    <QueryClientProvider client={client}>
      <StatusRow session={other} />
    </QueryClientProvider>
  )
  await act(async () => {
    reject(new Error('private transport detail'))
    await pending.catch(() => {})
  })
  expect(view.queryByRole('alert')).toBeNull()
  expect(rest).toHaveBeenCalledTimes(1)
})
