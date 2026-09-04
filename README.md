# Agent Realms

Private, per-session Linux desktops for Hermes: labwc + Xwayland + private D-Bus, a contained Cua driver, and an authenticated noVNC viewer.

**Compatibility:** Linux with a working systemd user session and wlroots. The integrated Hermes UI/tool routing requires the companion generic session-extension changes in the `feat/session-desktop-context` Hermes worktree. This is **not** a drop-in plugin for unmodified upstream Hermes. The standalone CLI works independently. The tested companion source patch and exact base/commit are included in [`integration/`](integration/README.md).

## What it does

- Starts a realm lazily for a session's terminal/computer-use tools. GUI processes share the realm's private Wayland/Xwayland displays, not your physical desktop.
- Keeps sessions separate, including their compositor seats, session/accessibility buses, Xauthority and clipboards.
- Owns subprocesses in a systemd scope; validates boot ID, PID start time, scope identity and runtime ownership before reusing or stopping them.
- Shows real foreign-toplevel window counts, a compact session badge, Watch, and Pop out.
- Opens Watch view-only. Take over acquires an exclusive cross-process lease and pauses agent input. Returning control, disconnecting, expiry or revocation releases it.
- Defaults to hardware GLES rendering; does not silently choose a slow renderer or fall back to the host desktop.

## Install from this checkout

First build/use the companion Hermes checkout. Keep its production profile separate when evaluating these changes.

System prerequisites on Arch/Omarchy:

```sh
omarchy pkg add labwc wayvnc wlr-randr grim xorg-xwayland bubblewrap
```

A working user D-Bus, AT-SPI bus launcher, systemd user manager and render node are also required; `hermes-realm doctor` checks runtime prerequisites. GTK4 and Python GObject are needed for the GUI test fixtures, not normal usage.

Create a standalone CLI environment and install the pinned driver:

```sh
uv venv .venv
uv pip install --python .venv/bin/python -e .
.venv/bin/python -m realms.install_driver
.venv/bin/hermes-realm doctor
```

The driver installer verifies both the release archive and executable SHA-256. It installs cua-driver **0.23.2** in this checkout's `vendor/`; it does not replace the global driver. Bundled noVNC is **1.7.0**; licenses and integrity details are in [`THIRD_PARTY.md`](realms/web/THIRD_PARTY.md).

For local plugin development, link this checkout into an **explicit** target Hermes profile:

```sh
export HERMES_HOME=/absolute/path/to/test-hermes-home
mkdir -p "$HERMES_HOME/plugins"
ln -s /absolute/path/to/hermes-realms "$HERMES_HOME/plugins/hermes-realms"
hermes config set plugins.realms.default_mode realm
```

Do not overwrite an existing plugin directory. Ensure the target Hermes Python environment has the dependencies listed in `pyproject.toml`. Restart only that test backend to load Python hooks. In the corresponding development app, use **Settings → Plugins → Agent Realms** to enable the desktop UI. Merely installing the Python plugin does not enable the user-controlled desktop toggle.

For Omarchy's special-workspace mapping, see [`omarchy/README.md`](omarchy/README.md). The Lua module is opt-in; installing the plugin does not silently edit or reload your live Hyprland configuration.

## Use

The agent skill explains natural-language selection such as “use your realm,” “on my desktop,” and “show me what you're doing.” Explicit session commands include:

```text
/realm on
/realm off
/realm status
/realm size 1280x720
/realm watch
/realm stop
```

`on` selects realm mode; a lazy session starts its desktop on the first eligible tool, not on the mode command itself. `off` stops the owned realm and returns future tools to the existing host route. The configured `default_mode: ask` requires an explicit choice before GUI-capable tools proceed; there is no `/realm ask` command. `stop` stops the current realm without implicitly opting into host mode. The next allowed realm-mode tool can create a fresh realm.

Standalone CLI (use `--help` on each subcommand for exact arguments):

```sh
.venv/bin/hermes-realm start demo
.venv/bin/hermes-realm list
.venv/bin/hermes-realm env REALM_ID
.venv/bin/hermes-realm exec REALM_ID -- your-gui-command
.venv/bin/hermes-realm shot REALM_ID screenshot.png
.venv/bin/hermes-realm resize REALM_ID 1280x720
.venv/bin/hermes-realm stop REALM_ID
```

Settings are profile-scoped and changed through Hermes:

```sh
hermes config set plugins.realms.default_mode realm
hermes config set plugins.realms.size 1920x1080
hermes config set plugins.realms.idle_ttl 1800
hermes config set plugins.realms.renderer gles2
hermes config set plugins.realms.overlay true
hermes config set plugins.realms.cursor_theme cua.default
```

Supported defaults are `realm`, `host`, and `ask`. Idle TTL is positive seconds. `pixman` is an explicit software-rendering option, not an automatic fallback.

## Boundaries and limitations

- **GUI isolation is not a hostile-code sandbox.** Ordinary realm terminals deliberately retain development filesystem/network access. Do not treat them as isolation from malicious same-user shell commands. The Cua driver separately runs inside mount/PID/network/IPC namespaces, with only its private runtime, required system files and render nodes exposed.
- Raw WayVNC is a **0600 Unix socket beneath a 0700 directory**, not an unauthenticated localhost TCP port. Browser capabilities are short-lived, realm-generation-bound, origin-checked and revocable. Tickets are removed from URL fragments before connection and are not persistent app tabs.
- Standard/bounded/unrestricted computer-use permissions remain Hermes's responsibility; realms do not grant additional authority. A deny-input approval policy is honored before input; this host has no built-in `approvals.mode: read-only` setting. User takeover is an additional input prohibition, not a reason to escalate to another input mechanism.
- Remote-session Watch/Pop out is disabled before issuing a capability: a local-only listener cannot be assumed reachable through a remote connection.
- Stock labwc/Cua window metadata can have missing PID/bounds. Realm automation uses an explicit desktop target rather than pretending unreliable app metadata is trustworthy. Window counts use the actual private foreign-toplevel protocol, not process counts.
- Cursor overlay support is compositor/driver-dependent; capture and cursor verification receipts distinguish the supported native-cursor fallback from themed overlays.
- Display locking and machine suspension are different. The manager uses a suspend inhibitor while active; see the recorded lock test rather than assuming a compositor screenshot proves lock behavior.

## Verification

[`docs/verification.md`](docs/verification.md) records actual commands/results and distinguishes unit tests, real-process tests, browser tests, and live-model development-app tests. It records the final passing gates, live acceptance evidence, skipped/overlapping lanes and explicit platform/security limitations.

For the integration suite, use the companion Hermes checkout's canonical runner (it provides the expected test environment):

```sh
cd /absolute/path/to/hermes-realms-host-sdk
scripts/run_tests.sh /absolute/path/to/hermes-realms/tests --file-retries 0
```

For the real browser viewer lane, provide the absolute installed Playwright module path:

```sh
REALMS_PLAYWRIGHT_MODULE=/absolute/path/to/node_modules/playwright \
  .venv/bin/python scripts/test_viewer.py
```

The script launches real labwc, GTK, WayVNC, and Chromium, asserts view-only/takeover/return behavior, and stops its owned realm. Hardware/systemd tests require the appropriate Linux session; their skips must not be reported as passed acceptance tests.

## Layout

- `realms/`: manager, lifecycle, containment, session integration, window reader, viewer authority and RFB bridge.
- `plugin.py`, `plugin.yaml`, `dashboard/`: native Hermes registration and authenticated, owner-scoped API.
- `desktop/`: external desktop plugin using generic session contributions.
- `omarchy/`: opt-in Lua scratchpad integration and usage guide.
- `skills/realms/`: agent-facing workflow and safety instructions.
- `tests/`, `scripts/`: regression tests and real browser harness.
