# Project skill sources

These skills are vendored for repository-local agent workflows. Keep their upstream
copyright and attribution headers intact when updating them. Project instructions in
the repository `AGENTS.md` remain authoritative when a vendored skill differs.
The React guidance preserves the upstream individual rule content. Repository packaging
moves those rules under `references/`, removes duplicate compiled/authoring documents,
and normalizes runtime metadata without changing the rule instructions.

| Skill | Installed from | Revision | License |
|---|---|---|---|
| `agent-browser` | `zai-org/ZCode/.agents/skills/agent-browser` | `872ad960de7ec172591f7e1952f7849229f94521` | Apache-2.0; derived from `vercel-labs/agent-browser` and retains its copyright header |
| `dogfood` | `zai-org/ZCode/.agents/skills/dogfood` | `872ad960de7ec172591f7e1952f7849229f94521` | Apache-2.0; derived from `vercel-labs/agent-browser` and retains its copyright header |
| `react-best-practices` | `zai-org/ZCode/.agents/skills/react-best-practices` | `872ad960de7ec172591f7e1952f7849229f94521` | MIT as declared by the vendored skill; originally credited to Vercel Engineering and `@shuding` |
| `fallow` | `fallow-rs/fallow/npm/fallow/skills/fallow` | `bd8fca5af5df4ccfd94c7a835d17bd93c31a7cef` | MIT, Copyright (c) 2026 Bart Waardenburg |
| `spec-coding` | adapted from local `/Users/bytedance/workspace/bytedance/volcclaw-monorepo/.agents/skills/spec-coding` | local snapshot, HaaS-specific rewrite | Internal workflow text rewritten for this repository; no external code or runtime assets retained |
| `requirement-spec` | adapted from local `/Users/bytedance/workspace/bytedance/volcclaw-monorepo/.agents/skills/requirement-spec` | local snapshot, HaaS-specific rewrite | Internal workflow text rewritten for this repository; no external code or runtime assets retained |
| `react-typescript-kit` | adapted from local `/Users/bytedance/workspace/bytedance/volcclaw-monorepo/.agents/skills/react-typescript-kit` | local snapshot, HaaS-specific rewrite | Internal workflow text rewritten for this repository; no external code or runtime assets retained |
| `ui-automation` | adapted from local `/Users/bytedance/workspace/bytedance/volcclaw-monorepo/.agents/skills/ui-automation` | local snapshot, HaaS-specific rewrite | Internal workflow text rewritten for this repository; no external code or runtime assets retained |
| `code-automation` | adapted from local `/Users/bytedance/workspace/bytedance/volcclaw-monorepo/.agents/skills/code-automation` | local snapshot, HaaS-specific rewrite | Internal workflow text rewritten for this repository; no external code or runtime assets retained |
| `context-engineering-review` | adapted from local `/Users/bytedance/workspace/bytedance/volcclaw-monorepo/.agents/skills/context-engineering-review` | local snapshot, HaaS-specific rewrite | Internal workflow text rewritten for this repository; no external code or runtime assets retained |
| `haas-debug-workflow` | adapted from `logseq/logseq/.agents/skills/logseq-debug-workflow` | `43a540f35049689088ecb4038a2bec893f40ea94`; HaaS-specific rewrite for three runtimes (webview / rust-shell / python-sidecar) | Technology-agnostic pattern; no external code retained |
| `tauri-react-render-perf` | adapted from `gitbutlerapp/gitbutler/.agents/skills/lite-render-perf` | `bb81a1c0680ae08e19e700236e0dd29a989206c5`; HaaS-specific rewrite; Redux/react-query examples replaced with local state patterns; React Compiler not introduced | No external code retained |
| `parallel-code-review` | adapted from `logseq/logseq/.agents/skills/logseq-review-workflow` + `usebruno/bruno/.claude/skills/code-review` | `43a540f35049689088ecb4038a2bec893f40ea94` (logseq) + `4eb7e585f0fb318b2ef8d88b34635da633f3acf4` (bruno); HaaS-specific lenses (secretless / protocol-compat / adapter-isolation / recovery / cross-platform / tests); aligned with existing review gates | No external code retained |
| `code-automation` enhancement | adapted from `EcoPasteHub/EcoPaste/.agents/skills/trellis-check` | `5139d30b0f4c1309356a9b308c05092f1038bc9b`; additive cross-layer data flow + spec sync + code reuse checks; existing content preserved | No external code retained |
| `fastapi-backend` | adapted from `fastapi/fastapi/.agents/skills/fastapi` | `50113da16fec53b66b80d75e80a89296de4fa5a5`; HaaS-specific rewrite: Annotated/Depends aliases, yield-dependency lifecycle, async-vs-def rules, `response_model` as secretless filter, router layout; SSE details moved to `references/streaming.md`; HaaS commands and scope boundaries added | MIT (upstream `fastapi/fastapi`); instructional content rewritten, no external code retained |
| `pydantic-modeling` | adapted from `pydantic/pydantic/.agents/skills/pydantic` | `0384c970e37a59b344e75161eb106ea9996378ba`; HaaS-specific rewrite: field-vs-type metadata, union-metadata-position trap, discriminated-uniform event hierarchy, UTC-aware datetimes, parse/dump redaction; scoped to the model layer only | MIT (upstream `pydantic/pydantic`); instructional content rewritten, no external code retained |
| `adapter-extension` | adapted from `pydantic/pydantic-ai/.agents/skills` (adding-a-provider-api-feature + complete-partial-pr) | `a48606989919b0ce3f655fa7ffff8e23853d3989`; HaaS-specific rewrite: "find the existing abstraction first" decision framework (reuse / promote-after-3 / typed Literal, no `extra_body`/`**kwargs`, default-on vs opt-in), adapter-change parity checklist, harness-adapter + codex-app-server-adapter spec references | MIT (upstream `pydantic/pydantic-ai`); methodology rewritten, no external code retained |
| `brooks-review` | adapted from local `~/.trae/skills/brooks-review` | local snapshot; repository-local reference paths normalized | Local workflow text; no external executable code retained |
| `brooks-test` | adapted from local `~/.trae/skills/brooks-test` and its shared references | local snapshot; repository-local references made self-contained and history-ledger writes removed | Local workflow text; no external executable code retained |
| `code-review` | adapted from local `~/.trae/skills/code-review` | local snapshot; frontmatter normalized to the repository skill contract | Local workflow text; no external executable code retained |
| `dev-loop` | adapted from local `~/.agents/skills/dev-loop` | local snapshot; rewritten for HaaS spec, review, and verification gates; source-machine manifest removed | Local workflow text; no external executable code retained |
| `old-coder` | adapted from local `~/.trae/skills/old-coder` | local snapshot; bundled verifier references retained | Local workflow text; no external executable code retained |
| `review-spec` | adapted from local `~/.trae/skills/review-spec` | local snapshot; frontmatter normalized to the repository skill contract | Local workflow text; no external executable code retained |

The repository root `LICENSE` contains the Apache-2.0 license text used by the first
two skills. The Fallow MIT permission notice is reproduced below because it differs
from the repository license.

## Fallow MIT license notice

Copyright (c) 2026 Bart Waardenburg

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
