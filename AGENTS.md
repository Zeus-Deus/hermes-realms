# Hermes Realms

Standalone optional Hermes plugin providing per-session Linux desktops. Start with
[README.md](README.md), [host integration](integration/README.md), and
[verification scope](docs/verification.md). Do not depend on machine-local plans,
private sibling checkouts, or archived execution evidence.

## Development and validation

- Use TDD for behavioral changes: run a failing behavioral test before implementing
  each slice. Exercise real processes, sockets, discovery and temporary profiles;
  do not fabricate API results or use source-reading tests to claim runtime behavior.
- Keep realm behavior in this repository. Generic Hermes APIs belong in the
  separately reviewed host contribution linked in `integration/README.md`; never
  monkeypatch Hermes internals or modify installed/live Hermes.
- Use isolated worktrees and disposable `HOME`, `HERMES_HOME`, XDG directories and
  Electron user-data for integration tests. UI validation uses a newly built DEV
  app with verified renderer/backend source, never the production desktop.
- The full native suite requires explicit approval for its test environment. It
  can start compositors, systemd scopes and input-capable services; E2E markers
  alone do not identify every such test. Follow `docs/verification.md`.
- Every completion claim needs actual execution evidence. Store raw logs,
  screenshots, transcripts and inventories privately outside the repository.
  Put only reviewed, non-sensitive summaries and reproducible commands in docs;
  distinguish historical runs, fresh passes, failures, skips and untested claims.

## Runtime and security invariants

- Respect the selected `HERMES_HOME`. Settings belong in `config.yaml` under
  `plugins.realms.*`; do not add behavioral `HERMES_*` environment variables.
  Honor effective standard/bounded/unrestricted permissions unchanged.
- Fail closed: never fall back to the host display, accessibility bus, Cua daemon,
  pointer or input devices. Strip host display/control/portal hints; use private
  runtime/session/accessibility buses. GUI separation is not a hostile-code
  sandbox; ordinary realm commands retain development filesystem/network access.
- Validate device/socket exposure in the contained driver. Own entire process
  trees using systemd scopes and process start times, not bare PIDs. Handle
  teardown, crashes, idle expiry and concurrent startup. Never modify global
  systemd activation environment. Inhibit sleep only, never idle or locking.
- Raw VNC uses only a mode-0600 Unix socket under the mode-0700 realm runtime.
  The viewer binds loopback only. Verify HTTP/WebSocket capabilities independently;
  enforce view-only server-side and scoped, revocable takeover. Revalidate peers
  and input authority. Do not accept arbitrary target hosts or ports.
- Production profiles, live desktop configuration and unrelated dirty worktrees
  are out of scope. Do not install into or restart them during tests.

## Publication

- Never commit credentials, live viewer tickets, private conversations or personal
  runtime data. Review Git history and both wheel and source archives before
  publication; deleting a file does not remove earlier committed versions.
- Original project code is MIT-licensed; keep `LICENSE` and package metadata in
  sync. Preserve vendored third-party notices, attribution and exact release pins.
- No self/AI attribution in commits. Do not rewrite history, publish releases or
  change repository visibility without explicit owner authorization.
