import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { type ComponentType } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

// @ts-expect-error Runtime plugins are JavaScript SDK consumers.
import plugin, { realmQueryOptions } from '../../desktop/plugin.js'

import { SESSION_AREAS, type SessionContribution, type SessionContributionProps } from '@/contrib/session'

const owner = { connectionId: 'local', profile: 'default', storedSessionId: 'old', runtimeSessionId: 'runtime' }
const clients: QueryClient[] = []

afterEach(() => {
  cleanup()
  clients.splice(0).forEach(client => client.clear())
})

it('reviews in the existing status slot, cancels, rejects scope drift and explicitly accepts', async () => {
  const held = { requested: true, realms: [], mode: null, permission: { state: 'legacy-pending' } }

  const rest = vi.fn(async (path: string) => {
    if (path.endsWith('/prepare')) {
      return { digest: 'reviewed', text: 'Original configured backend. No guest is stopped.', resources: [] }
    }

    if (path.endsWith('/accept')) {
      return { state: 'optional' }
    }

    return held
  })

  const renders = new Map<string, ComponentType<SessionContributionProps>>()

  const ctx = {
    rest,
    register: ({ area, data }: { area: string; data: SessionContribution }) => renders.set(area, data.render)
  }

  plugin.register(ctx)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  client.setQueryData(realmQueryOptions(ctx, owner).queryKey, held)
  const Row = renders.get(SESSION_AREAS.statusStack)!

  const tree = (session = owner) => (
    <QueryClientProvider client={client}>
      <Row session={session} />
    </QueryClientProvider>
  )

  const view = render(tree())

  expect(view.getByText('Execution paused for permission review')).toBeTruthy()
  fireEvent.click(view.getByRole('button', { name: 'Use optional targets…' }))
  await view.findByRole('dialog')
  fireEvent.click(view.getByRole('button', { name: 'Cancel' }))
  await waitFor(() => expect(view.queryByRole('dialog')).toBeNull())
  expect(rest.mock.calls.some(([path]) => path.endsWith('/accept'))).toBe(false)

  fireEvent.click(view.getByRole('button', { name: 'Use optional targets…' }))
  await view.findByRole('dialog')
  const other = { ...owner, connectionId: 'other' }
  client.setQueryData(realmQueryOptions(ctx, other).queryKey, held)
  view.rerender(tree(other))
  await waitFor(() => expect(view.queryByRole('dialog')).toBeNull())
  expect(rest.mock.calls.some(([path]) => path.endsWith('/accept'))).toBe(false)

  view.rerender(tree())
  fireEvent.click(view.getByRole('button', { name: 'Use optional targets…' }))
  await view.findByRole('dialog')
  fireEvent.click(view.getByRole('button', { name: 'Accept permission change' }))
  await waitFor(() =>
    expect(rest).toHaveBeenCalledWith('/realms/permissions/accept', {
      method: 'POST',
      body: { runtime_session_id: 'runtime', stored_session_id: 'old', digest: 'reviewed' },
      scope: owner
    })
  )
  expect(rest.mock.calls.some(([path]) => path.includes('/setup/') || path.includes('/action'))).toBe(false)
})
