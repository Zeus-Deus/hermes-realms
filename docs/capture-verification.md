# Historical capture and cursor verification

This is a reviewed historical summary, not raw stdout or a new test run. Original captures and receipts were retained privately before publication cleanup. See [current verification scope](verification.md).

The component run used fresh temporary profile-owned labwc realms, pinned cua-driver 0.23.2 and the product's bubblewrap containment. No host display/input or installed driver mutation was part of that lane.

- Own-session screenshot fallback tests used real plugin discovery and actual independently sized realms. They checked ownership, PNG dimensions, unique mode-0700 capture directories and fail-closed handling; no fabricated screenshot/RPC result was used.
- A raw-only private Unix VNC client observed the native pointer moving between two positions: 103 changed pixels in each cursor crop and zero changes outside the crops. Synthetic-only logical cursor movement did not establish native pointer movement.
- Fresh overlay-enabled daemons visibly rendered themed cursor pixels; overlay-disabled controls did not show those synthetic overlays. Native and themed pointers could coexist. This establishes those fresh daemons' behavior, not overlay activation in every existing model session.
- The initial focused run recorded **5 passed in 25.51s**. A later focused run including integration recorded **14 passed in 34.99s**.
- An intermediate wider run recorded **63 passed, 1 skipped, 1 failed in 74.97s**. The failure was an unclosed resource warning in CUA integration. It was not a pass; later historical final gates are distinguished in the main verification summary.

To reproduce the focused lane in an authorized isolated Linux environment, use an explicit compatible host Python environment and write evidence outside this checkout:

```sh
cd "$PRODUCT"
REALMS_TEST_CAPTURE_EVIDENCE=/absolute/path/to/private-capture-evidence \
PYTHONPATH="$HOST:$PRODUCT" \
  "$HOST/.venv/bin/python" -W error -m pytest \
  -o asyncio_default_fixture_loop_scope=function \
  tests/test_capture_fallback.py tests/test_cursor_framebuffer.py tests/test_cursor_overlay.py -q -s
```

The tests/probe stop owned managers in cleanup. Environment availability, current driver lifecycle and real framebuffer readback must be checked anew; configuration alone is not evidence.
