# Running the tests

The tests exercise the plugin through real Hermes modules (plugin discovery, the
CLI, the dashboard, session execution), so they run against a Hermes source
checkout. They always test **this** repository's plugin: the fixtures install it
into a temporary profile as a user plugin, and the test run refuses a Hermes
checkout that still bundles its own `plugins/hermes-realms`.

## Python

Use a Hermes checkout (without `plugins/hermes-realms`) and its dev environment:

```sh
cd /path/to/hermes-agent && uv sync --extra dev
cd /path/to/hermes-realms
HERMES_AGENT_DIR=/path/to/hermes-agent /path/to/hermes-agent/.venv/bin/python -m pytest tests
```

`tests/conftest.py` puts `HERMES_AGENT_DIR` on the import path and loads Hermes'
own `tests/conftest.py`, so both suites share the same hermetic setup (temporary
`HOME`/`HERMES_HOME`, credential scrubbing, live-system guards, OS markers).

Keep the temporary directory short. Some tests bind Unix sockets under pytest's
`tmp_path`, and a long `TMPDIR` exceeds the socket path limit:

```sh
TMPDIR=$(mktemp -d /var/tmp/realms-XXXX) HERMES_AGENT_DIR=... python -m pytest tests
```

Native tests are marked `integration` and are deselected by default. They start
real private desktops, systemd scopes or guests, and some download the pinned
driver. Run them only in a disposable environment you have approved for that:

```sh
HOME="$(mktemp -d)" HERMES_AGENT_DIR=... python -m pytest tests -m integration \
  tests/test_realms_host_cli.py tests/test_realms_import_runtime.py tests/test_realms_setup.py
```

## Desktop

The desktop tests render the plugin's `desktop/plugin.js` with the Hermes Desktop
app's SDK, React and Vite config, so they need the Hermes checkout's installed
`node_modules`:

```sh
cd /path/to/hermes-agent && npm ci
cd /path/to/hermes-realms
ln -s /path/to/hermes-agent/node_modules node_modules   # vitest resolves from here; gitignored
HERMES_AGENT_DIR=/path/to/hermes-agent npx vitest run --config desktop/vitest.config.mts
```

`desktop/plugin.js` is generated from `desktop/plugin-source.js` and its sibling
modules. After editing them, rebuild and check with the same esbuild:

```sh
node desktop/build.mjs
node desktop/build.mjs --check
```
