#!/usr/bin/env node
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';

const root = path.resolve(import.meta.dirname, '..');
const output = path.join(root, 'dist', 'explainer');
const full = '/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg';
const ffmpeg = process.env.FFMPEG || (fs.existsSync(full) ? full : 'ffmpeg');
const ffprobe = process.env.FFPROBE || 'ffprobe';
const fps = 30;

export function speechText(text) {
  return text.replace(/\b(HaaS|HTTP|AIO|AMP)\b/gi, word => ({
    haas: 'Hass', http: 'web', aio: 'all-in-one', amp: 'amp',
  })[word.toLowerCase()]);
}
export function wrapCaption(text, limit) {
  if (/[\u3400-\u9fff]/u.test(text)) {
    const lines = [];
    let rest = text;
    const segmenter = new Intl.Segmenter('zh-CN', { granularity: 'word' });
    while (rest.length > limit) {
      const target = rest.length / Math.ceil(rest.length / limit);
      const breaks = [...segmenter.segment(rest)].map(s => s.index).filter(i =>
        i > 0 && i <= limit && !/^[，。；：！？、）]/u.test(rest.slice(i)));
      const end = breaks.sort((a, b) => Math.abs(a - target) - Math.abs(b - target))[0] || limit;
      lines.push(rest.slice(0, end));
      rest = rest.slice(end);
    }
    lines.push(rest);
    return lines.join('\n');
  }
  const lines = [''];
  for (const word of text.split(' ')) {
    const i = lines.length - 1;
    if (lines[i] && lines[i].length + word.length + 1 > limit) lines.push(word);
    else lines[i] += (lines[i] ? ' ' : '') + word;
  }
  return lines.join('\n');
}
export function validateStory(story) {
  if (!/^https:\/\/github\.com\/[\w-]+\/[\w.-]+$/.test(story.repositoryUrl)) throw new Error('Expected public GitHub repository URL');
  if (!Array.isArray(story.scenes) || story.scenes.length !== 7) throw new Error('Expected seven scenes');
  const ids = new Set();
  for (const scene of story.scenes) {
    if (!scene.id || ids.has(scene.id) || !scene.cues?.length) throw new Error('Invalid scene');
    ids.add(scene.id);
    for (const cue of scene.cues) {
      if (cue.length !== 2 || cue.some(s => typeof s !== 'string' || !s.trim() || /[{}\\\n\r<>]/.test(s))) throw new Error('Invalid bilingual cue');
      if (wrapCaption(cue[0], 68).split('\n').length > 2 || wrapCaption(cue[1], 32).split('\n').length > 2) throw new Error('Caption requires splitting');
    }
  }
}
export function timestamp(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) throw new Error('Invalid timestamp');
  const ms = Math.round(seconds * 1000);
  const pad = (n, digits = 2) => String(n).padStart(digits, '0');
  return `${pad(Math.floor(ms / 3600000))}:${pad(Math.floor(ms / 60000) % 60)}:${pad(Math.floor(ms / 1000) % 60)},${pad(ms % 1000, 3)}`;
}
export function buildTimeline(story, durations) {
  if (durations.length !== story.scenes.flatMap(s => s.cues).length || durations.some(d => !Number.isFinite(d) || d <= 0)) throw new Error('Invalid speech durations');
  let frame = 0, index = 0;
  const cues = [], scenes = [];
  for (const [sceneIndex, scene] of story.scenes.entries()) {
    const start = frame / fps;
    for (const [localIndex, [en, zh]] of scene.cues.entries()) {
      const speechDuration = durations[index];
      const pause = localIndex === scene.cues.length - 1 ? 0.65 : 0.12;
      const frames = Math.ceil(Math.max(2.2, speechDuration + pause) * fps);
      cues.push({ index, sceneIndex, localIndex, en, zh, speechDuration, start: frame / fps, end: (frame + frames) / fps, frames, duration: frames / fps });
      frame += frames; index++;
    }
    scenes.push({ id: scene.id, chapter: scene.chapter, start, end: frame / fps });
  }
  return { fps, width: 1920, height: 1080, duration: frame / fps, cues, scenes };
}
const escape = text => String(text).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const run = (cmd, args) => execFileSync(cmd, args, { cwd: root, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'] });
const probe = file => JSON.parse(run(ffprobe, ['-v', 'error', '-show_format', '-show_streams', '-of', 'json', file]));
const json = (name, value) => fs.writeFileSync(path.join(output, name), JSON.stringify(value, null, 2) + '\n');
const assTime = seconds => timestamp(Math.round(seconds * 100) / 100).replace(/^0/, '').replace(',', '.').slice(0, -1);

function subtitles(timeline) {
  for (const lang of ['en', 'zh']) {
    fs.writeFileSync(path.join(output, lang === 'en' ? 'en.srt' : 'zh-CN.srt'), timeline.cues.map((c, i) => `${i + 1}\n${timestamp(c.start)} --> ${timestamp(c.end)}\n${wrapCaption(c[lang], lang === 'en' ? 68 : 32)}\n`).join('\n'));
  }
  const header = `[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: EN,Helvetica Neue,32,&H00D6D0C4,&H00D6D0C4,&H00221B10,&H00221B10,0,0,0,0,100,100,0,0,1,0,0,8,96,96,912,1\nStyle: ZH,Hiragino Sans GB,40,&H00F5F9FA,&H00F5F9FA,&H00221B10,&H00221B10,0,0,0,0,100,100,0,0,1,0,0,8,96,96,808,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n`;
  fs.writeFileSync(path.join(output, 'bilingual.ass'), header + timeline.cues.flatMap(c => ['zh', 'en'].map(lang => `Dialogue: 0,${assTime(c.start)},${assTime(c.end)},${lang.toUpperCase()},,0,0,0,,${wrapCaption(c[lang], lang === 'en' ? 68 : 32).replaceAll('\n', '\\N')}`)).join('\n') + '\n');
}
function frameHTML(story, timeline, evidence) {
  const data = JSON.stringify({ story, timeline, evidence }).replaceAll('<', '\\u003c');
  const renderer = fs.readFileSync(path.join(root, 'scripts', 'explainer-frames.js'), 'utf8');
  return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>HaaS film frames</title><style>*{box-sizing:border-box}body{margin:0;background:#101b22}#frame{width:1920px;height:1080px;overflow:hidden}svg{width:1920px;height:1080px;display:block}text{font-family:Arial,"Hiragino Sans GB",sans-serif}</style><div id="frame"></div><script>window.film=${data};${renderer}</script></html>`;
}
async function capture(story, timeline, evidence) {
  const archify = process.env.ARCHIFY_ROOT || path.join(os.homedir(), '.trae', 'skills', 'archify');
  const { ChromeVisualBrowser, findChrome } = await import(pathToFileURL(path.join(archify, 'bin', 'visual-check.mjs')).href);
  const chrome = findChrome();
  if (!chrome) throw new Error('Chrome is required');
  fs.writeFileSync(path.join(output, 'frames.html'), frameHTML(story, timeline, evidence));
  const browser = new ChromeVisualBrowser(chrome);
  const evaluate = async expression => {
    const r = await browser.cdp.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, await browser.sessionPromise);
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
    return r.result.value;
  };
  try {
    const session = await browser.sessionPromise;
    await browser.cdp.send('Emulation.setDeviceMetricsOverride', { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false }, session);
    const loaded = browser.cdp.waitFor('Page.loadEventFired', session);
    await browser.cdp.send('Page.navigate', { url: pathToFileURL(path.join(output, 'frames.html')).href }, session);
    await loaded;
    await evaluate('document.fonts.ready.then(()=>true)');
    for (const cue of timeline.cues) {
      const check = await evaluate(`renderFrame(${cue.index}); new Promise(r=>requestAnimationFrame(()=>r(checkFrame())))`);
      if (check.length) throw new Error('Frame overflow: ' + JSON.stringify(check));
      const shot = await browser.cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }, session, 30000);
      fs.writeFileSync(path.join(output, `frame-${cue.index}.png`), Buffer.from(shot.data, 'base64'));
    }
    await evaluate('renderCover(); new Promise(r=>requestAnimationFrame(()=>r(true)))');
    const cover = await browser.cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }, session, 30000);
    fs.writeFileSync(path.join(output, 'cover.png'), Buffer.from(cover.data, 'base64'));
  } finally { await browser.close(); }
}
function writeHandoff(story, timeline) {
  const repositoryLink = `<a href="${escape(story.repositoryUrl)}">${escape(story.repositoryUrl.replace('https://', ''))}</a>`;
  const chapters = timeline.scenes.map(s => `${timestamp(s.start).slice(3, 8)} ${s.chapter}`).join('\n');
  fs.writeFileSync(path.join(output, 'youtube-upload.txt'), `${story.title}\n${story.titleZh}\n\nGitHub: ${story.repositoryUrl}\n\nEnglish narration. Chinese and English subtitles.\n英语旁白，中英双语字幕。\n\nHaaS puts a stable HTTP/SSE service boundary around complete agent runtimes. This film explains the integration problem, architecture, execution lifecycle, and capability boundaries. Codex is the first implemented adapter; Pi, OpenCode, and AMP are planned.\n\nHaaS 为完整 Agent Runtime 建立稳定的 HTTP/SSE 服务边界。本片讲解接入问题、架构、执行生命周期和能力边界。Codex 是首个已实现 adapter；Pi、OpenCode 和 AMP 仍在规划中。\n\n${chapters}\n\nThis first cut uses local synthesized English narration. Confirm voice usage rights and listening quality before publication. Protocol demonstration uses an isolated test adapter, not real Codex/provider execution. No upload has been performed.\n初版采用本地合成英语旁白；公开发布前须确认音轨使用权和完整听审。协议演示使用隔离测试 adapter，不是真实 Codex/provider 执行。尚未上传。\n`);
  const buttons = timeline.scenes.map(s => `<button type="button" data-time="${s.start}">${escape(s.chapter)}</button>`).join('');
  fs.writeFileSync(path.join(output, 'index.html'), `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>HaaS · 视频初版</title><style>body{margin:32px auto;max-width:1200px;padding:0 20px;background:#101b22;color:#edf4ee;font:16px/1.7 -apple-system,sans-serif}h1{font-size:28px}p{color:#b3c5cc}video{display:block;width:100%;background:#101b22;border:1px solid #3f585b;border-radius:8px}nav{display:flex;gap:8px;flex-wrap:wrap;margin:22px 0}button,a{color:#b5f2d5}button{background:#1c332e;border:1px solid #4c7363;padding:10px;cursor:pointer}small{color:#b3c5cc}</style><h1>HaaS · Make agent execution a service.</h1><p>完整初版 · 英语合成旁白 · 中英双语字幕 · ${Math.floor(timeline.duration / 60)} 分 ${Math.round(timeline.duration % 60)} 秒</p><video id="video" controls preload="metadata" poster="cover.png" src="haas-explainer-bilingual.mp4"></video><nav>${buttons}</nav><p>项目 GitHub · ${repositoryLink}</p><p><a href="haas-explainer-bilingual.mp4" download>下载双语字幕版</a> · <a href="haas-explainer-clean.mp4" download>下载无烧录字幕版</a> · <a href="en.srt" download>English SRT</a> · <a href="zh-CN.srt" download>中文字幕</a> · <a href="cover.png" download>封面</a> · <a href="youtube-upload.txt">上传文案</a></p><small>协议演示使用测试 adapter，不是真实 Codex/provider 录像。旁白为本地合成初稿；公开上传前须确认语音使用权与完整听审。没有上传 YouTube，也没有修改 README 链接。</small><script>document.querySelectorAll('button').forEach(b=>b.onclick=()=>{document.getElementById('video').currentTime=Number(b.dataset.time)});</script></html>`);
}
async function main() {
  fs.mkdirSync(output, { recursive: true });
  const story = JSON.parse(fs.readFileSync(path.join(root, 'docs/architecture/haas-explainer.json')));
  validateStory(story);
  run(ffmpeg, ['-version']); run(ffprobe, ['-version']);
  const voice = process.env.NARRATION_VOICE || story.voice;
  const rate = Number(process.env.NARRATION_RATE || story.rate);
  if (!Number.isFinite(rate) || rate < 100 || rate > 200) throw new Error('Narration rate must be 100–200');
  const all = story.scenes.flatMap(s => s.cues), durations = [];
  for (const [i, [en]] of all.entries()) {
    const speech = speechText(en);
    const fingerprint = createHash('sha256').update(JSON.stringify({ voice, rate, speech })).digest('hex');
    const stamp = path.join(output, `speech-${i}.sha256`), wav = path.join(output, `speech-${i}.wav`);
    if (!fs.existsSync(wav) || !fs.existsSync(stamp) || fs.readFileSync(stamp, 'utf8') !== fingerprint) {
      run('say', ['-v', voice, '-r', String(rate), '-o', path.join(output, `speech-${i}.aiff`), speech]);
      run(ffmpeg, ['-v', 'error', '-i', path.join(output, `speech-${i}.aiff`), '-ar', '48000', '-ac', '1', '-y', wav]);
      fs.writeFileSync(stamp, fingerprint);
    }
    durations.push(Number(probe(wav).format.duration));
  }
  const timeline = buildTimeline(story, durations);
  json('timeline.json', timeline);
  if (timeline.duration < 165 || timeline.duration > 225) throw new Error(`Narration duration ${timeline.duration} is outside 165–225 seconds`);
  subtitles(timeline);
  const evidencePath = path.join(output, 'protocol-evidence.json');
  const evidence = fs.existsSync(evidencePath) ? JSON.parse(fs.readFileSync(evidencePath)) : null;
  console.log(`Narration: ${all.length} cues, ${timeline.duration.toFixed(2)}s, ${voice}`);
  await capture(story, timeline, evidence);
  console.log('Frames captured; encoding clips');
  for (const cue of timeline.cues) {
    const visual = cue.localIndex === 0 ? 'fade=t=in:st=0:d=0.3' : 'null';
    run(ffmpeg, ['-v', 'error', '-loop', '1', '-framerate', String(fps), '-i', path.join(output, `frame-${cue.index}.png`), '-i', path.join(output, `speech-${cue.index}.wav`), '-vf', visual, '-af', `apad,atrim=duration=${cue.duration}`, '-t', String(cue.duration), '-r', String(fps), '-c:v', 'libx264', '-threads', '4', '-preset', 'fast', '-crf', '17', '-tune', 'stillimage', '-pix_fmt', 'yuv420p', '-c:a', 'pcm_s16le', '-ar', '48000', '-y', path.join(output, `clip-${cue.index}.mkv`)]);
    console.log(`Encoded ${cue.index + 1}/${all.length}`);
  }
  fs.writeFileSync(path.join(output, 'clips.txt'), timeline.cues.map(c => `file 'clip-${c.index}.mkv'`).join('\n'));
  run(ffmpeg, ['-v', 'error', '-f', 'concat', '-safe', '1', '-i', path.join(output, 'clips.txt'), '-c', 'copy', '-y', path.join(output, 'assembled.mkv')]);
  const measure = file => {
    const result = spawnSync(ffmpeg, ['-hide_banner', '-i', file, '-vn', '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json', '-f', 'null', '-'], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
    if (result.status !== 0) throw new Error(result.stderr);
    return JSON.parse(result.stderr.slice(result.stderr.lastIndexOf('{'), result.stderr.lastIndexOf('}') + 1));
  };
  const measured = measure(path.join(output, 'assembled.mkv'));
  const normalize = `loudnorm=I=-16:TP=-1.5:LRA=11:measured_I=${measured.input_i}:measured_TP=${measured.input_tp}:measured_LRA=${measured.input_lra}:measured_thresh=${measured.input_thresh}:offset=${measured.target_offset}:linear=true,volume=-0.2dB`;
  run(ffmpeg, ['-v', 'error', '-i', path.join(output, 'assembled.mkv'), '-c:v', 'copy', '-af', normalize, '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-movflags', '+faststart', '-y', path.join(output, 'haas-explainer-clean.mp4')]);
  run(ffmpeg, ['-v', 'error', '-i', path.join(output, 'haas-explainer-clean.mp4'), '-vf', 'ass=dist/explainer/bilingual.ass', '-c:v', 'libx264', '-threads', '4', '-preset', 'slow', '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-movflags', '+faststart', '-y', path.join(output, 'haas-explainer-bilingual.mp4')]);
  const checks = [];
  for (const name of ['haas-explainer-clean.mp4', 'haas-explainer-bilingual.mp4']) {
    const file = path.join(output, name), info = probe(file);
    run(ffmpeg, ['-v', 'error', '-xerror', '-i', file, '-f', 'null', '-']);
    const video = info.streams.find(s => s.codec_type === 'video'), audio = info.streams.find(s => s.codec_type === 'audio');
    if (video.codec_name !== 'h264' || video.width !== 1920 || video.height !== 1080 || video.pix_fmt !== 'yuv420p' || video.r_frame_rate !== '30/1' || audio.codec_name !== 'aac' || audio.sample_rate !== '48000' || Math.abs(Number(info.format.duration) - timeline.duration) > 0.2) throw new Error('Invalid media output');
    checks.push({ name, duration: info.format.duration, bytes: fs.statSync(file).size, decode: 'passed' });
  }
  const loudness = measure(path.join(output, 'haas-explainer-bilingual.mp4'));
  if (Math.abs(Number(loudness.input_i) + 16) > 0.5 || Number(loudness.input_tp) > -1.5) throw new Error('Encoded audio exceeds loudness or peak limits');
  json('verification.json', { checks, loudness, voice, voiceReleaseRights: 'not_verified', fullListeningReview: 'not_run', protocol: evidence?.status || 'not_run' });
  writeHandoff(story, timeline);
  console.log('Finished: ' + output);
}
if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(import.meta.filename)) {
  main().catch(error => { console.error(error.stderr?.toString() || error.stack); process.exitCode = 1; });
}
