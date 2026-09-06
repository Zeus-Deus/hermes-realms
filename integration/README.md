# Generic Hermes host extensions

Agent Realms requires host APIs for trusted session identity, per-session execution, typed input policy denials, status/badge contributions and isolated viewer windows. They remain separate from the plugin; no installed Hermes files are automatically patched.

## Public dependency

[NousResearch/hermes-agent PR #103690](https://github.com/NousResearch/hermes-agent/pull/103690) is **open, not merged**, checked 2026-09-06. The public revisions are:

- `5f7fe241bd4d1e0a3c7bdb89867805881bdc07e6`
- `fa6e9ac63155780dc368bf02f86c34413d441788` (PR head at that check)

Review the PR and its host API documentation before selecting a development checkout. Follow [Hermes installation documentation](https://hermes-agent.nousresearch.com/docs/getting-started/installation) for host dependencies. Do not assume an unmodified release has these APIs or that a later PR revision is automatically compatible.

The previous machine-local companion patch is no longer distributed. It described an older host revision and is not a supported patch installer. Its original bytes and verification receipts were retained privately rather than edited into a misleading execution record.

## Compatibility and validation

Two complementary checks cover the integration:

- A model-driven core-workflow run used a personal-fork DEV build containing both public commits. Two actual model sessions operated supplied GTK scaffolds; this is not a claim that models independently authored those apps.
- A separate deterministic run built the renderer and backend from the clean public PR head `fa6e9ac63155780dc368bf02f86c34413d441788`, without personal-fork changes or model credentials. Real host terminal/Cua calls and the DEV viewer exercised private app input, view-only blocking, takeover denial and return of control. This was native/tool/UI integration, not another model-driven conversation.

See [verification scope](../docs/verification.md) for results and limitations. These checks establish compatibility with the tested public PR revision, not with an unmodified upstream release or every future revision.

Use separate test `HOME`, `HERMES_HOME`, XDG directories and Electron user-data. Point the development app's existing `HERMES_DESKTOP_HERMES_ROOT` override at the intended host checkout, and verify which backend actually serves requests. The full plugin directory and Python dependencies must be available to that backend. Enable both backend and desktop plugin halves in the test profile. Do not restart or patch production Hermes.

The standalone CLI does not require these host extensions. The wheel contains the CLI/library and viewer assets; native plugin registration uses the full checkout or source distribution. A bundled host/plugin installation path is future work, not a shipped feature.
