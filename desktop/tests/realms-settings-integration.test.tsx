import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { type ProfileScope, setApiRequestConnection } from '@/api/client'
import { createPluginContext } from '@/contrib/plugin'
import { $pluginRecords } from '@/contrib/plugins-store'
import { $agentPlugins, $agentPluginsStatus } from '@/store/agent-plugins'

// @ts-expect-error Runtime plugin modules are plain JavaScript SDK consumers.
import realms from '../../desktop/plugin.js'

import { PluginsTab } from '@/app/capabilities/plugins/plugins-tab'

const requestGateway = vi.fn(async () => ({ plugins: [] }))
vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({
  useGatewayRequest: () => ({ requestGateway })
}))
const disposers: (() => void)[] = []
const clients: QueryClient[] = []
afterEach(() => {
  cleanup()
  disposers.splice(0).forEach(dispose => dispose())
  clients.splice(0).forEach(client => client.clear())
  vi.restoreAllMocks()
  setApiRequestConnection(null)
})

it('loads the Realms storage consumer in its existing Plugins row and binds each selected source', async () => {
  $pluginRecords.set({
    'hermes-realms': { id: 'hermes-realms', name: 'Realms', kind: 'bundled', status: 'loaded' },
    other: { id: 'other', name: 'Other plugin', kind: 'disk', status: 'loaded' }
  })
  $agentPlugins.set([])
  $agentPluginsStatus.set('ready')
  const ctx = createPluginContext('hermes-realms', dispose => disposers.push(dispose))

  const rest = vi
    .spyOn(ctx, 'rest')
    .mockResolvedValueOnce({ base: { present: true, version: 'selected-base' }, storage: {} })
    .mockResolvedValueOnce({ base: { present: false }, storage: {} })

  realms.register(ctx)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  const selected = { connectionId: 'local', profile: 'work' }
  const other = { connectionId: 'other-source', profile: 'work' }

  const tree = (profile: ProfileScope = selected) => (
    <QueryClientProvider client={client}>
      <PluginsTab profile={profile} />
    </QueryClientProvider>
  )

  setApiRequestConnection('local')
  const view = render(tree('work'))
  await act(async () => {})
  const row = view.getByRole('switch', { name: 'Desktop: Realms' }).closest('[role="row"]') as HTMLElement
  const otherRow = view.getByRole('switch', { name: 'Desktop: Other plugin' }).closest('[role="row"]') as HTMLElement
  expect(within(otherRow).queryByRole('button', { name: 'Storage and base image' })).toBeNull()
  expect(rest).not.toHaveBeenCalled()
  fireEvent.click(within(row).getByRole('button', { name: 'Storage and base image' }))
  await within(row).findByText('selected-base')
  expect(rest).toHaveBeenCalledWith('/realms/vm/settings', { scope: selected })
  view.rerender(tree(other))
  expect(view.queryByText('selected-base')).toBeNull()
  fireEvent.click(view.getByRole('button', { name: 'Storage and base image' }))
  await view.findByText('Not prepared')
  expect(rest).toHaveBeenCalledWith('/realms/vm/settings', { scope: other })
  act(() => {
    disposers.splice(0).forEach(dispose => dispose())
  })
  expect(view.queryByRole('button', { name: 'Storage and base image' })).toBeNull()
  expect(view.getByRole('switch', { name: 'Desktop: Realms' })).toBeTruthy()
})
