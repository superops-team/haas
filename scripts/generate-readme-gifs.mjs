#!/usr/bin/env node

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const root = path.resolve(import.meta.dirname, '..');
const assets = path.join(root, 'docs', 'architecture');
const archify = process.env.ARCHIFY_ROOT || path.join(os.homedir(), '.trae', 'skills', 'archify');
const cli = path.join(archify, 'bin', 'archify.mjs');
const browserRuntime = path.join(archify, 'bin', 'visual-check.mjs');
const magick = process.env.MAGICK || 'magick';
const WIDTH = 1440, HEIGHT = 810, FPS = 12, MAX_BYTES = 5 * 1024 * 1024;

const stories = [
  ['architecture', 'haas-system.architecture.json', 'haas-system.html', 'haas-concept.gif', true],
  ['architecture', 'haas-system.zh-CN.architecture.json', 'haas-system.zh-CN.html', 'haas-concept.zh-CN.gif', true],
  ['sequence', 'run-sse.sequence.json', 'run-sse.html', 'run-sse-flow.gif'],
  ['sequence', 'run-sse.zh-CN.sequence.json', 'run-sse.zh-CN.html', 'run-sse-flow.zh-CN.gif'],
  ['dataflow', 'request-processing.dataflow.json', 'request-processing.html', 'request-processing.gif'],
  ['dataflow', 'request-processing.zh-CN.dataflow.json', 'request-processing.zh-CN.html', 'request-processing.zh-CN.gif'],
];

function requireFile(file) {
  if (!fs.existsSync(file)) throw new Error(`Required file not found: ${file}`);
}

function run(command, args, stdio = 'inherit') {
  return execFileSync(command, args, { cwd: root, stdio, encoding: stdio === 'pipe' ? 'utf8' : undefined });
}

function requireCommand(command) {
  try {
    run(command, ['-version'], 'pipe');
  } catch {
    throw new Error(`Required command is unavailable: ${command}`);
  }
}

function readViews(sourceFile) {
  const source = JSON.parse(fs.readFileSync(sourceFile, 'utf8'));
  const views = source.meta?.views;
  if (source.meta?.animation !== 'trace') throw new Error(`${sourceFile} must use trace animation`);
  if (!Array.isArray(views) || views.length < 2 || views.some((view) => !view.id)) {
    throw new Error(`${sourceFile} needs at least two identified guided views`);
  }
  return views.map((view) => view.id);
}

async function evaluate(browser, sessionId, expression, awaitPromise = false) {
  const response = await browser.cdp.send('Runtime.evaluate', { expression, awaitPromise, returnByValue: true }, sessionId);
  if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text);
  return response.result?.value;
}

async function capture(Browser, chrome, html, viewIds, workDir) {
  const browser = new Browser(chrome);
  const frames = [];
  try {
    const sessionId = await browser.sessionPromise;
    await browser.cdp.send('Emulation.setDeviceMetricsOverride', { width: WIDTH, height: HEIGHT, deviceScaleFactor: 1, mobile: false }, sessionId);
    const url = new URL(pathToFileURL(path.join(assets, html)).href);
    url.search = '?embed=1&play=1&theme=dark';
    url.hash = `view=${viewIds[0]}`;
    const loaded = browser.cdp.waitFor('Page.loadEventFired', sessionId);
    await browser.cdp.send('Page.navigate', { url: url.href }, sessionId);
    await loaded;
    const state = await evaluate(browser, sessionId, `(async()=>{await document.fonts.ready;document.documentElement.setAttribute('data-motion','live');document.documentElement.setAttribute('data-share-playback','true');Archify.guidedViews.pause();if(Archify.readerLayout?.whenStable)await Archify.readerLayout.whenStable();if(Archify.viewerChromeLayout?.whenStable)await Archify.viewerChromeLayout.whenStable();return{count:Archify.guidedViews.count,width:innerWidth,height:innerHeight}})()`, true);
    if (state.count !== viewIds.length || state.width !== WIDTH || state.height !== HEIGHT) throw new Error(`Unexpected browser state: ${JSON.stringify(state)}`);

    for (let index = 0; index < viewIds.length; index += 1) {
      await evaluate(browser, sessionId, `document.documentElement.setAttribute('data-share-playback','true');Archify.guidedViews.pause();Archify.guidedViews.activate(${JSON.stringify(viewIds[index])})`);
      for (let transition = 0; transition < 5; transition += 1) {
        await new Promise((resolve) => setTimeout(resolve, 80));
        const file = path.join(workDir, `${String(frames.length).padStart(3, '0')}.png`);
        const shot = await browser.cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false }, sessionId, 20000);
        fs.writeFileSync(file, Buffer.from(shot.data, 'base64'));
        frames.push([file, 1 / FPS]);
      }
      await new Promise((resolve) => setTimeout(resolve, 180));
      const file = path.join(workDir, `${String(frames.length).padStart(3, '0')}.png`);
      const shot = await browser.cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false }, sessionId, 20000);
      fs.writeFileSync(file, Buffer.from(shot.data, 'base64'));
      const stageDwell = viewIds.length <= 3 ? 1.8 : 1.6;
      const finalDwell = viewIds.length <= 3 ? 2.4 : 2.2;
      frames.push([file, stageDwell + (index === viewIds.length - 1 ? finalDwell : 0)]);
    }
  } finally {
    await browser.close();
  }
  return frames;
}

function assemble(frames, output, workDir) {
  void workDir;
  const args = [];
  for (const [file, duration] of frames) args.push('-delay', String(Math.max(1, Math.round(duration * 100))), file);
  run(magick, [...args, '-layers', 'OptimizeFrame', '-colors', '192', '-loop', '0', output]);
}

function verify(output) {
  const dimensions = run('/usr/bin/sips', ['-g', 'pixelWidth', '-g', 'pixelHeight', output], 'pipe');
  if (!dimensions.includes(`pixelWidth: ${WIDTH}`) || !dimensions.includes(`pixelHeight: ${HEIGHT}`)) throw new Error(`Wrong dimensions: ${output}`);
  const bytes = fs.statSync(output).size;
  if (bytes > MAX_BYTES) throw new Error(`${output} exceeds 5 MiB: ${(bytes / 1048576).toFixed(2)} MiB`);
  const delays = run(magick, ['identify', '-format', '%T\n', output], 'pipe')
    .trim().split(/\s+/).map(Number);
  const iterations = run(magick, ['identify', '-verbose', `${output}[0]`], 'pipe')
    .match(/^  Iterations: (\d+)$/m)?.[1];
  const duration = delays.reduce((sum, delay) => sum + delay, 0) / 100;
  const finalDwell = delays.at(-1) / 100;
  if (iterations !== '0') throw new Error(`${output} must loop forever`);
  if (finalDwell < 2) throw new Error(`${output} final dwell is below 2 seconds`);
  if (duration < 9 || duration > 13) throw new Error(`${output} duration is outside 9-13 seconds: ${duration}`);
  return { bytes, duration };
}

async function main() {
  [cli, browserRuntime].forEach(requireFile);
  requireCommand(magick);
  const { ChromeVisualBrowser, findChrome } = await import(pathToFileURL(browserRuntime).href);
  const chrome = findChrome();
  if (!chrome) throw new Error('Chrome or Chromium is required');
  const bilingualCounts = new Map();
  for (const [type, source, html, output, needsRepoRoot] of stories) {
    const views = readViews(path.join(assets, source));
    const family = output.replace('.zh-CN', '');
    if (bilingualCounts.has(family) && bilingualCounts.get(family) !== views.length) throw new Error(`Bilingual stage mismatch: ${family}`);
    bilingualCounts.set(family, views.length);
    const deliverArgs = [cli, 'deliver', type, path.join('docs', 'architecture', source), path.join('docs', 'architecture', html), '--quality', 'showcase', '--json'];
    if (needsRepoRoot) deliverArgs.push('--repo-root', '.');
    run(process.execPath, deliverArgs);
    const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'haas-readme-gif-'));
    try {
      const frames = await capture(ChromeVisualBrowser, chrome, html, views, workDir);
      const destination = path.join(assets, output);
      assemble(frames, destination, workDir);
      const { bytes, duration } = verify(destination);
      process.stdout.write(`${output}: ${views.length} stages, ${duration.toFixed(1)}s, ${(bytes / 1048576).toFixed(2)} MiB\n`);
    } finally {
      fs.rmSync(workDir, { recursive: true, force: true });
    }
  }
}

main().catch((error) => { process.stderr.write(`${error.stack || error.message}\n`); process.exitCode = 1; });
