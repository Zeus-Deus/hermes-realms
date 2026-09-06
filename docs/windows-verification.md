# Historical private window count and remote viewer guards

Reviewed summary of privately retained component receipts, not a new execution record. See [verification scope](verification.md).

`realms/windows.py` counts private labwc foreign-toplevel handles rather than app/PID guesses. Endpoint/process identity validation, bounded protocol reads and a short-lived bounded cache protect the read-only status path; failures report unknown rather than zero. Remote Watch/Pop out rejects non-local connections before requesting a ticket, while remote status polling remains available.

Historical real GTK tests exercised independent realm window-count changes, cache behavior, private compositor stop/resume, tampered binding rejection and timeout handling. Desktop tests checked disabled remote actions and existing local behavior. All realm instances used temporary profiles with owned teardown.

The archived canonical window/integration/API lane recorded **18 passed, 0 failed**; the desktop remote lane recorded **8 passed, 0 failed**. A separate warning-strict CUA integration run **failed** with unclosed resources. An earlier combined normal-warning-policy run passed 18 tests, but is not proof that warning-strict cleanup was fixed. Later historical final gates are documented separately in the main verification summary.

These window counts do not establish CUA AX/app-window targeting. No native Windows operating-system support is implied by this document's filename.
