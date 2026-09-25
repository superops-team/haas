---
name: ui-automation
description: HaaS GUI browser and desktop runtime verification. Use when running production Vite preview, Playwright E2E, screenshots, request/performance observation, artifact viewer checks, packaged OpenHarness smoke tests, or browser/Tauri parity evidence. Not for ordinary component implementation.
---

# HaaS UI Automation

## Authority

- Follow root `AGENTS.md`; keep temporary observation specs and reports out of git.
- Use repository-native Playwright fixtures before adding new automation tools.
- Rebuild `manager/surfaces/gui/dist` with `npm run build` before trusting production preview or performance measurements.

## Preferred Test Surfaces

1. **Hermetic GUI E2E**
   - Directory: `manager/surfaces/gui/e2e`
   - Fixture: `e2e/fixtures.ts`
   - Command: `npm run e2e -- <spec files>` or `npx playwright test <spec files>`
   - Use for UI behavior that does not require the real Python sidecar.

2. **Production preview browser evidence**
   - Preferred stable command: from the repository root, run `make gui-preview-smoke`.
   - Equivalent GUI-local command: `cd manager/surfaces/gui && npm run e2e:preview`.
   - The command rebuilds the production bundle before starting `vite preview`.
   - Use temporary Playwright specs only for extra measurements not covered by the checked-in preview smoke, then delete them before delivery.
   - Capture request counts, chunk names, console errors, long tasks, and user-visible assertions.

3. **Packaged desktop smoke**
   - Build with `manager/packaging/build_dmg.sh`.
   - Verify with `manager/packaging/smoke_packaged_app.sh <OpenHarness.app>`.
   - Before packaged smoke, close installed `/Applications/OpenHarness.app` to avoid Tauri single-instance interception:
     `osascript -e 'tell application id "com.openharness.desktop" to quit'` and confirm with `pgrep -af '/OpenHarness.app/Contents/MacOS/openharness-desktop'`.

4. **Installed app smoke**
   - Launch `/Applications/OpenHarness.app`.
   - Verify `openharness-desktop`, `openworker-server`, and `haas-sidecar` processes.
   - Check `GET /v1/health` on the actual sidecar port and `GET /v1/haas/health` on the HaaS port.

## Evidence Rules

- Record counts, timings, route/chunk/component labels, and command results.
- Do not record prompt content beyond synthetic test strings, credentials, cookies, auth tokens, raw tool args, signed URLs, or full transcripts.
- Treat expected sandbox CSP errors as expected only when the test deliberately verifies blocked exfiltration.
- If an observation script reveals a bug, turn it into a focused durable test before or during the fix.
- Delete temporary `playwright.*.config.ts`, `performance-observation.spec.ts`, screenshots, HAR files, and debug reports unless the user explicitly asks to keep them.

## Common Checks

### Bundle Budget

```bash
cd manager/surfaces/gui
npm run build
node - <<'NODE'
const fs=require('fs'), zlib=require('zlib'), path=require('path');
const dist='dist';
const manifest=JSON.parse(fs.readFileSync(path.join(dist,'.vite/manifest.json'),'utf8'));
let bytes=0, gzip=0; const seen=new Set();
function visit(k){ const e=manifest[k]; if(!e) throw new Error('missing '+k);
  if(e.file?.endsWith('.js') && !seen.has(e.file)){
    seen.add(e.file); const b=fs.readFileSync(path.join(dist,e.file));
    bytes+=b.length; gzip+=zlib.gzipSync(b).length;
  }
  for(const i of e.imports||[]) visit(i);
}
visit('index.html');
console.log({bytes,gzip,files:[...seen]});
NODE
```

### Production Preview E2E

Use the checked-in preview smoke first:

```bash
make gui-preview-smoke
```

Create temporary config only when the stable command does not cover the needed measurement:

```ts
import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://localhost:5201" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run preview -- --host 127.0.0.1 --port 5201 --strictPort",
    url: "http://127.0.0.1:5201",
    reuseExistingServer: false,
  },
});
```

Then run focused specs such as:

```bash
npx playwright test -c playwright.preview.config.ts \
  e2e/rail-default.spec.ts e2e/artifacts.spec.ts e2e/inbox.spec.ts
```

## Output

Summarize:

```text
Runtime: <dev server / production preview / packaged / installed app>
Commands: <commands and pass/fail>
Observed metrics: <request counts, timings, chunks, long tasks>
Findings: <fixed / no issue / residual risk>
Cleanup: <temporary files removed, processes left running or stopped>
```
