# Hermes Realms

Standalone Hermes plugin. The source plan is ../_cua_research/realms-plan/index.html; verified Phase 0 evidence is ../_cua_research/realms-spike/. Integration and driver audits are ../_cua_research/realms-integration-audit.md and realms-cua-audit.md.

## Rules
- TDD: execute a failing behavioral test before implementing each vertical slice. Real processes, sockets, discovery and temporary HERMES_HOME; no fabricated API results or source-reading tests.
- All realm behavior belongs here. Generic Hermes extensions are separately approved by the user in ../hermes-realms-host-sdk (branch feat/session-desktop-context); never monkeypatch Hermes internals or modify installed/live Hermes.
- Profile-aware HERMES_HOME. Settings in config.yaml under plugins.realms.* per agreed plan. No new behavioral HERMES_* env vars. Honor effective standard/bounded/unrestricted permissions unchanged.
- Fail closed: no fallback to host display, host a11y bus, host cua daemon, user pointer or input device. Strip host display/control/portal hints and use private runtime/session/a11y buses. GUI separation is not a hostile-code security sandbox: document honestly; deny device/socket escape paths in owned driver execution.
- Own and identify entire process trees (systemd scope and process start times), not PIDs alone. Handle teardown, crash, idle and concurrent starts. Do not modify global systemd activation environment. Inhibit sleep only, never idle/lock.
- Raw VNC uses only a private mode-0600 Unix socket under the mode-0700 realm runtime. The viewer listener binds loopback only; HTTP/WS capabilities are independently verified, view-only is server-enforced, takeover is scoped and revocable, and connected peers and input authority must be revalidated. No arbitrary target hosts/ports.
- No credentials or personal data in fixtures, repository, screenshots or commits. No self/AI attribution. Keep vendored licenses and exact release pins.
- User's default profile, production desktop, dirty checkout, /usr/share/omarchy are out of scope. UI validation is a newly built DEV app only.
- Every completion claim needs real execution receipts. Preserve evidence and exact commands in docs/verification.md.
