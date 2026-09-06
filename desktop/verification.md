# Desktop / Omarchy verification scope

Reviewed summary, not raw stdout. Original development receipts were preserved privately before publication cleanup. [Main verification](../docs/verification.md) separates historical component checks, latest core-workflow E2E and outstanding acceptance.

The initial desktop component lane recorded **6 tests, 6 pass, 0 fail** with real React/React DOM/jsdom and React Query. Model data and native OS actions were explicit unit-test fixtures, not successful full backend E2E. A later remote-guard lane recorded **8 passed, 0 failed**. The polling case used a real loopback HTTP failure service and the real polling/retry interval. Node's experimental VM-module notice is expected.

Historical Lua unit output:

```text
PASS: silent native-title routing, latest/cycle/index, closed cleanup, malformed identity rejection
PASS: reload restores viewer indexes, no focus or move on reload
```

The historical installed Hyprland parser check reported `config ok`; parser success and recorded dispatch construction do not prove live window placement or focus behavior. No configuration should be installed/reloaded merely to run this publication audit. Optional scratchpad mapping and latest-host locking remain outside the latest core-workflow E2E proof.

Reproduce standalone desktop tests using [the documented explicit dependency installation](README.md). This test harness does not need a hardcoded sibling host SDK checkout. Integrated tests still require [the compatible host extension APIs](../integration/README.md), and remote viewer forwarding remains unsupported.
