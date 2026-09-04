# Phase 1 manager + CLI verification

## Final execution

```sh
cd <user-home>/projects/hermes-realms
<user-home>/projects/hermes-agent-personal/.venv/bin/python -m pytest tests/test_manager*.py tests/test_cli.py -q -s -W error
```

**30 passed in 21.05s.** Full output: [phase1-test-receipt.txt](phase1-test-receipt.txt). Parsed renderer/cleanup evidence: [phase1-receipts.json](phase1-receipts.json).

All realm homes were pytest temporary profiles (or explicit temporary HERMES_HOME); no live Hermes profile or host compositor config was modified. Tests use actual systemd user services/scopes, labwc, Xwayland, curated private D-Bus/AT-SPI, wayvnc, wlr-randr and grim—not mocked outputs.

## Proven

- Lifecycle start/list/env/stop across fresh processes; concurrent starts produce independent private displays/session/a11y buses and reuse the same session generation safely.
- Default gles2 capture at 1920x1080. Log: `GL vendor: AMD`; `GL renderer: AMD Ryzen 5 7600 6-Core Processor (radeonsi, raphael_mendocino, ACO, DRM 3.64, 7.2.3-arch1-2)`. No llvmpipe. Pixman also captures 1920x1080. Each empty-desktop PNG was 6121 bytes.
- Runtime resize800x600 and CLI resize640x480 without compositor restart, verified from PNG dimensions/live output.
- Actual logind `sleep`/`block` inhibitor belongs to scope; no idle/lock inhibition.
- AF_UNIX VNC mode0600 under runtime0700: real RFB handshake, peer PID/UID ownership, no VNC TCP listening FD, distinct concurrent endpoints, socket deletion. Second-UID impersonation was not performed; UID access protection is based on verified kernel permission modes.
- Worker RPC exec keeps explicit cwd/project access; double-fork/setsid descendant remains in scope and is gone after stop.
- Actual Popen command-prefix transport: SCM_RIGHTS pipes, stdin/out/err fidelity, exit17, caller env override and sanitizer preservation; forwarded SIGTERM reaches child whose exit23 is returned; controlling PTY (both fresh and already-owned caller tty), 120x40 geometry.
- Idle cleanup without a living manager object; SIGKILL of bus, compositor, atspi, registry, vnc, worker, Xwayland and guardian triggers complete cleanup without subsequent manager calls. Guardian's scope dependency and ExecStopPost survive sentinel death.
- Teardown removes scope cgroup, processes, private runtime/Wayland/VNC sockets, Xwayland filesystem socket and registry. Receipt checks read exact recorded targets after execution; no broad process kill was used.
- Registry/profile/UID/generation/scope invocation/PID-start/socket binding validation; foreign runtime deletion, VNC/guardian rebinding and root symlink refused. Atomic JSON writes and locked allocation; failed executable/scope startup rolled back; stale prior durable record reconciled.
- Fresh uv venv installs package dependencies and viewer static assets, imports bridge, runs installed `hermes-realm doctor`, starts/stops a real realm. No root installs. Installed entrypoint is `realms.cli:main`, avoiding the existing Hermes root `cli` module collision.

## Observed intermittent failure (not hidden)

An earlier full run under substantial simultaneous CPU load returned an empty RPC response during the first gles2 screenshot: JSONDecodeError in Manager._rpc; **29 passed, 1 failed in 70.03s**. The scoped compositor log showed normal hardware initialization and graceful shutdown at0.86s, and journal showed a normal scope stop; the old sentinel log did not identify a dead component. Cause is not established. Added persistent component-exit diagnostics. Five successive renderer tests passed (1.42–1.44s each), followed by the final complete30-test green run above. No speculative retry/sleep workaround was added or claimed as a fix. Parent should retain this caveat for loaded integration testing.

## Integration contract / limits

Current API: `<user-home>/projects/_cua_research/realms-manager-api.md`.

Use `Manager.command_prefix(id)` with original Popen argv and caller-sanitized realm env to get process containment; environment injection alone does not move a process into the scope. Put the parent's driver sandbox command after that prefix. Product VNC uses `vnc_socket`, with `vnc_port=None`; bridge must use Unix transport. Ordinary realm exec preserves local workspace access and is not a hostile-code sandbox. Parent owns CUA containment, viewer capabilities, plugin/core/desktop integration. Cursor/overlay config is exposed, not implemented as a CUA daemon by the manager.

No commit was made. Non-manager integration files were not edited by this implementation.
