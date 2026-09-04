# Companion Hermes extensions

Agent Realms requires generic host extension APIs for trusted session identity, per-session execution, typed input policy denials, status/badge contributions and isolated viewer windows. They remain separate from the plugin; no installed Hermes files are automatically patched.

- Tested base: `c3af12a201261d583ab7873711228f319a464a40`
- Tested companion commit: `d56a7aae3479ebf0a25b80df2de3621a70918cc1`
- Local branch: `feat/session-desktop-context`
- [Complete source patch](hermes-session-extensions.patch)

The companion commit is local; this document does not imply it is available on upstream GitHub. The patch is included so the dependency is not hidden in a machine-local checkout.

Apply only in a clean, separate Hermes checkout based on the tested base:

```sh
git apply --check /absolute/path/to/hermes-realms/integration/hermes-session-extensions.patch
git apply /absolute/path/to/hermes-realms/integration/hermes-session-extensions.patch
```

Do not apply over unrelated dirty work or assume compatibility with a different upstream revision. Follow Hermes's normal dependency setup. From `apps/desktop`, the development build sequence exercised here was:

```sh
npm run build
node scripts/bundle-electron-main.mjs --dev
```

Use a separate test `HERMES_HOME`, OS/XDG home and Electron user-data. Set the existing `HERMES_DESKTOP_HERMES_ROOT` override to this companion checkout so the tested renderer does not silently use an older backend. The external plugin directory and its dependencies must be available to that backend. See the [main README](../README.md) and [verification record](../docs/verification.md).

Reverse-application of this patch against the committed companion checkout was verified with `git apply --reverse --check`. The changed Python suite passed 101 cases warning-strict; the desktop lane passed 42 cases plus typechecks. Independent review details are archived under `docs/e2e/`.
