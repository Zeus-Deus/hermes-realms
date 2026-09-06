# Historical manager and CLI verification

Reviewed summary of privately retained Phase 1 receipts; not a new execution record. See [verification scope](verification.md).

The historical manager/CLI lane recorded **30 passed in 21.05s**, warning-strict, using actual systemd scopes, labwc, Xwayland, private D-Bus/AT-SPI, WayVNC, wlr-randr and grim with temporary profiles. Coverage included independent displays/buses, real GLES and explicit pixman capture, resize, sleep-only inhibition, private Unix VNC ownership/modes, process-tree containment, crash/idle teardown, registry validation and installed-package startup/cleanup.

A prior loaded run recorded **29 passed, 1 failed in 70.03s** with an empty RPC response during capture. The cause was not established. Later repeated renderer tests and the final green run do not retroactively diagnose that failure or prove it impossible under load. Second-UID impersonation was not performed; UID isolation claims were based on checked kernel permission modes.

For reproduction, select the manager/CLI files through the compatible host's canonical test runner in an authorized disposable Linux environment. Do not run native lifecycle tests merely to validate documentation or packaging.

The implemented contract is in `realms/manager.py`: `Manager.command_prefix(id)` provides owned execution routing; environment injection alone does not put a child in its scope. Product VNC uses `vnc_socket`, not TCP. Ordinary realm execution preserves workspace access and is not a hostile-code sandbox.
