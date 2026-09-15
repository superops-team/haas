import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { buildTimeline, timestamp, wrapCaption, speechText, validateStory } from './generate-explainer.mjs';

const story = JSON.parse(fs.readFileSync(new URL('../docs/architecture/haas-explainer.json', import.meta.url)));

test('approved story has seven scenes and paired, bounded captions', () => {
  validateStory(story);
  assert.equal(story.scenes.length, 7);
  for (const scene of story.scenes) for (const [en, zh] of scene.cues) {
    assert(wrapCaption(en, 68).split('\n').length <= 2);
    assert(wrapCaption(zh, 32).split('\n').length <= 2);
  }
});
test('spoken content preserves the approved English script verbatim', () => {
  const spec = fs.readFileSync(new URL('../specs/architecture/VISUAL-STORYTELLING.md', import.meta.url), 'utf8');
  const section = spec.slice(spec.indexOf('**1. The integration problem**'), spec.indexOf('### 13.3'));
  const paragraphs = [...section.matchAll(/\*\*\d\. [^\n]+\*\*\n\n([^\n]+)/g)].map(m => m[1]);
  assert.equal(paragraphs.length, 7);
  assert.deepEqual(story.scenes.map(s => s.cues.map(c => c[0]).join(' ')), paragraphs);
});
test('missing translations and subtitle control syntax are rejected', () => {
  for (const cue of [['Hello', ''], ['Hello{bad}', '你好'], ['Hi\nthere', '你好']]) {
    const bad = structuredClone(story); bad.scenes[0].cues[0] = cue;
    assert.throws(() => validateStory(bad));
  }
});
test('timeline aligns cues to frames and preserves speech with scene pauses', () => {
  const sample = { scenes: [{ id: 'one', cues: [['a', '甲'], ['b', '乙']] }, { id: 'two', cues: [['c', '丙']] }] };
  const result = buildTimeline(sample, [1.2, 3.31, 2.5]);
  assert.equal(result.cues[0].start, 0);
  for (let i = 0; i < result.cues.length; i++) {
    const cue = result.cues[i];
    assert(cue.duration >= cue.speechDuration + 0.1);
    assert(cue.duration >= 2.2);
    assert(Math.abs(cue.end * 30 - Math.round(cue.end * 30)) < 1e-6);
    if (i) assert.equal(result.cues[i - 1].end, cue.start);
  }
  assert.equal(result.duration, result.cues.at(-1).end);
  assert.throws(() => buildTimeline(sample, [0, 1, 2]));
  assert.throws(() => buildTimeline(sample, [1]));
  assert.throws(() => buildTimeline(sample, [1, NaN, 2]));
});
test('timestamps round milliseconds with carry and captions wrap without loss', () => {
  assert.equal(timestamp(59.9996), '00:01:00,000');
  assert.equal(timestamp(3661.123), '01:01:01,123');
  assert.throws(() => timestamp(-1));
  const en = 'A stable integration boundary. Execution you can track.';
  assert.equal(wrapCaption(en, 30).replace(/\n/g, ' '), en);
  const zh = '把 Agent 执行变成服务，把精力留给真正的产品。';
  assert.equal(wrapCaption(zh, 20).replace(/\n/g, ''), zh);
});
test('Chinese captions balance lines at word boundaries without orphan punctuation', () => {
  const zh = story.scenes[0].cues[2][1];
  const lines = wrapCaption(zh, 32).split('\n');
  assert.equal(lines.length, 2);
  assert(lines.every(line => line.length >= 12 && line.length <= 32));
  assert.equal(lines.join(''), zh);
  assert(lines.some(line => line.includes('恢复规则')));
  for (const scene of story.scenes) for (const [, caption] of scene.cues) {
    const wrapped = wrapCaption(caption, 32).split('\n');
    assert.equal(wrapped.join(''), caption);
    assert(wrapped.every(line => !/^[，。；：！？、）]/u.test(line)));
  }
});
test('speech pronunciation changes do not change displayed text', () => {
  assert.equal(speechText('HaaS uses an ADK API and MCP with AIO.'), 'Hass uses an ADK API and MCP with all-in-one.');
  assert.equal(speechText('HAAS and HTTP.'), 'Hass and web.');
  assert(!speechText(story.scenes.flatMap(s => s.cues).map(c => c[0]).join(' ')).includes('H, A'));
});
test('the final scene provides a verified GitHub destination and spoken invitation', () => {
  assert.equal(story.repositoryUrl, 'https://github.com/superops-team/haas');
  assert.match(story.scenes.at(-1).cues.at(-1)[0], /GitHub/);
  assert.match(story.scenes.at(-1).cues.at(-1)[1], /GitHub/);
  for (const repositoryUrl of ['javascript:alert(1)', 'https://example.com/repo', 'https://github.com/team/repo?token=x']) {
    assert.throws(() => validateStory({ ...story, repositoryUrl }));
  }
});
