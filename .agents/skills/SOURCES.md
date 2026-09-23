# Project skill sources

These skills are vendored for repository-local agent workflows. Keep their upstream
copyright and attribution headers intact when updating them. Project instructions in
the repository `AGENTS.md` remain authoritative when a vendored skill differs.
The React guidance copy only normalizes Markdown trailing whitespace to satisfy the
repository commit gate; its instructional content is otherwise unchanged.

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
