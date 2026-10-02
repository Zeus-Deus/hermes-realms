import { webcrypto } from 'node:crypto'

import { afterEach, expect, it, vi } from 'vitest'

import { host } from '@/sdk'

// @ts-expect-error The bundled plugin is a plain JavaScript SDK consumer.
import { openRealmViewer } from '../../desktop/plugin.js'

interface PreviewInput {
  url: string
  onKeepAlive: () => Promise<void>
  onReopen?: () => Promise<void>
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const session = { connectionId: 'local', profile: 'original', storedSessionId: 'history', runtimeSessionId: 'runtime' }
const realm = { id: 'r-fixture', state: 'live', stored_session_id: 'history', runtime_session_id: 'runtime' }
const owner = { stored_session_id: 'history', runtime_session_id: 'runtime' }

function bridge() {
  let minted = 0

  return vi.fn(async (path: string, _init?: unknown) => {
    if (path.endsWith('/watch')) {
      minted += 1

      return { url: `http://127.0.0.1:1234/realms/r-fixture/view#ticket=${String(minted).repeat(43)}` }
    }

    return { renewed: true }
  })
}

it('re-opens Watch on a fresh ticket and renews each document with its own ticket', async () => {
  vi.stubGlobal('crypto', webcrypto)
  const rest = bridge()
  const preview = vi.spyOn(host, 'openPreview').mockResolvedValue(true)

  await openRealmViewer({ rest }, { ...session }, realm, 'watch')
  const first = preview.mock.calls[0][0] as unknown as PreviewInput
  expect(first.onReopen).toBeTypeOf('function')

  await first.onReopen!()
  const second = preview.mock.calls[1][0] as unknown as PreviewInput
  expect(second.url).not.toBe(first.url)
  expect(second.url).toContain('#ticket=' + '2'.repeat(43))
  expect(rest).toHaveBeenCalledWith('/realms/r-fixture/watch', { method: 'POST', scope: session, body: owner })

  await second.onKeepAlive()
  expect(rest).toHaveBeenLastCalledWith('/realms/r-fixture/renew', {
    method: 'POST',
    scope: session,
    body: { ...owner, viewer_token: '2'.repeat(43) }
  })

  // The next rebuild can re-open again.
  await second.onReopen!()
  expect(preview).toHaveBeenCalledTimes(3)
})

it('refuses to re-open once the plugin is disabled or the bridge refuses', async () => {
  vi.stubGlobal('crypto', webcrypto)
  const rest = bridge()
  const preview = vi.spyOn(host, 'openPreview').mockResolvedValue(true)
  let enabled = true

  await openRealmViewer({ rest }, { ...session }, realm, 'watch', () => true, () => enabled)
  const input = preview.mock.calls[0][0] as unknown as PreviewInput

  enabled = false
  await expect(input.onReopen!()).rejects.toThrow()
  expect(rest).toHaveBeenCalledTimes(1)

  enabled = true
  rest.mockRejectedValueOnce(new Error('realm stopped'))
  await expect(input.onReopen!()).rejects.toThrow()
  expect(preview).toHaveBeenCalledTimes(1)
})

it('does not offer a re-open to the pop-out window', async () => {
  vi.stubGlobal('crypto', webcrypto)
  const popup = vi.fn(async (_input: Record<string, unknown>) => true)

  await openRealmViewer({ rest: bridge(), os: { openViewer: popup } }, { ...session }, realm, 'popout')
  expect(popup.mock.calls[0][0]).not.toHaveProperty('onReopen')
})
