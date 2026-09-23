// Runs desktop/tests against the Hermes Desktop app's own Vite config, SDK and
// dependencies. See docs/testing.md for the command.
import path from 'node:path'

import { defineConfig } from 'vitest/config'

const hermes = process.env.HERMES_AGENT_DIR
if (!hermes) throw new Error('Set HERMES_AGENT_DIR to a Hermes source checkout with installed node_modules')
const app = path.resolve(hermes, 'apps/desktop')

export default defineConfig({
  test: {
    projects: [{
      extends: path.join(app, 'vite.config.ts'),
      test: {
        name: 'realms-desktop',
        root: path.resolve(import.meta.dirname, '..'),
        environment: 'jsdom',
        setupFiles: [path.join(app, 'vitest.setup.ts')],
        include: ['desktop/tests/**/*.test.{ts,tsx}'],
        globals: true,
        testTimeout: 15_000
      }
    }]
  }
})
