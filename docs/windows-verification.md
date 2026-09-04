# Private window count and remote viewer guard

## Implemented

- `realms/windows.py` enumerates the private labwc `zwlr_foreign_toplevel_manager_v1` handles directly over its owned Wayland Unix socket. Registry discovery and initial toplevel events are delimited by display sync callbacks. No app/PID count, capture, input, seat binding, CUA daemon or host-display fallback is used.
- Existing lifecycle/environment validation plus SO_PEERCRED/compositor PID-start identity verification protect the endpoint. Protocol reads have a one-second timeout and a 4 MiB response limit; a per-service locked LRU caches at most 128 generation/profile-specific results for one second, including unknown results. Status queries do not refresh `last_activity`.
- `RealmIntegration.status` now returns the actual count, or `None` when enumeration/ownership/compositor availability fails.
- Desktop Watch and Pop out reject every non-`local` connection before REST ticket issuance, while remote status polling remains enabled. The exact host contract is `@hermes/shared` `LOCAL_CONNECTION_ID = 'local'`; the external plugin mirrors it because shared-module imports are not available through the external plugin loader. Both buttons are disabled remotely with explicit missing-viewer-tunnel guidance. SSH forwarding of REST alone is not advertised as viewer support.

## TDD and actual execution

- Initial real empty-realm status test failed with `None == 0`, then passed after the direct protocol reader was implemented.
- Extended real GTK test passed through realm A counts **0 → 1 → 2 → 1 → 0**, with realm B independently **0 → 1 → 0**. Both use actual `Manager.start` labwc scopes and `tests/fixtures/gtk_probe.py`. Closing fixture processes through their owned scope produced compositor close updates. No host desktop input was used.
- Cache test failed before `WindowCounter` existed, then passed with real compositor query observation: repeated reads coalesce, expiry refreshes, capacity eviction occurs, and status reads preserve the idle lease.
- Actual private compositor SIGSTOP produces unknown within the bound; SIGCONT plus cache expiry recovers to zero. Tampered private display binding and stopped realms return unknown. A systemd-timeout fault-injection test failed before subprocess exceptions were converted to unknown, then passed.
- Remote action tests first failed by reaching the forbidden REST endpoint; they now reject before any REST/native surface call. The remote row test first lacked the warning/disabled state, then passed. Existing local viewer actions still pass.

## Receipts

Exact commands, workdirs and unedited tool stdout are recorded in:

- [windows-and-integration-tests.txt](windows-and-integration-tests.txt): canonical host runner, `test_windows.py`, `test_integration.py`, `test_integration_identity.py`, `test_plugin_api.py`, `--file-retries 0 -W error -j 4` — **18 passed**, 0 failed, 12.1s. The discovery estimate excludes one parametrized case; the executed per-file counts and final total agree.
- [desktop-remote-tests.txt](desktop-remote-tests.txt): `node --experimental-vm-modules --test tests/desktop/plugin.test.mjs` — **8 passed**, 0 failed. Node emits its expected experimental VM module warning.
- [windows-cua-integration-tests.txt](windows-cua-integration-tests.txt): separate `test_integration_cua.py --file-retries 0 -W error` — **fails** on an unclosed `TextIOWrapper`/`FileIO` resource in CUA integration cleanup. This also reproduced in the combined warning-strict integration run. No host source was changed to hide it.

The combined canonical runner without `-W error` passed the CUA integration plus all then-existing window/integration/API tests: **18 passed**, 0 failed, 14.5s (before the fourth window timeout regression was added). This is not a claim that warning-strict CUA cleanup is fixed. Parent-owned development-app acceptance and broader suite validation remain separate.

No production Hermes configuration, installed host source, host desktop capture or host input was touched. All realm instances used temporary profile homes and were stopped in test cleanup.
