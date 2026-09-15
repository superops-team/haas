/* global film */
const INK = '#f7f5ef', MUTED = '#a4b8c2', MINT = '#a8f0cf', LINE = '#45616b';
function xml(value) { return String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]); }
function text(x, y, value, size = 28, fill = INK, weight = 400, extra = '') {
  return `<text x="${x}" y="${y}" font-size="${size}" fill="${fill}" font-weight="${weight}" ${extra}>${xml(value)}</text>`;
}
function rect(x, y, w, h, fill = '#192d36', stroke = LINE, radius = 7) {
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${radius}" fill="${fill}" stroke="${stroke}" stroke-width="1.5"/>`;
}
function line(x1, y1, x2, y2, color = LINE, width = 2, dashed = false) {
  return `<path d="M${x1} ${y1}L${x2} ${y2}" fill="none" stroke="${color}" stroke-width="${width}" ${dashed ? 'stroke-dasharray="8 8"' : ''}/>`;
}
function arrow(x1, y, x2, color = MINT) {
  return line(x1, y, x2, y, color, 3) + `<path d="m${x2 - 12} ${y - 7} 12 7 -12 7" fill="none" stroke="${color}" stroke-width="3"/>`;
}
function card(x, y, w, h, tag, title, detail, active = true) {
  return `<g opacity="${active ? 1 : 0.43}">${rect(x, y, w, h, active ? '#1b352f' : '#182932', active ? '#83b59c' : LINE)}${text(x + 28, y + 37, tag, 18, active ? MINT : MUTED, 500, 'letter-spacing="2"')}${text(x + 28, y + 87, title, 32, INK, 600)}${text(x + 28, y + 130, detail, 24, MUTED)}</g>`;
}
function group(opacity, content) { return `<g opacity="${opacity}">${content}</g>`; }
function shell(scene, sceneIndex, body, progress) {
  const titleSize = scene.id === 'value' ? 83 : 88;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080"><rect width="1920" height="1080" fill="${scene.id === 'value' ? '#152820' : '#101b22'}"/>
  <path d="M1713 112V785M1769 112V785M1825 112V785" stroke="#2a3e44" opacity=".28"/>
  ${text(96, 91, `${String(sceneIndex + 1).padStart(2, '0')} / ${scene.chapter}`, 20, MINT, 600, 'letter-spacing="3"')}
  ${text(1824, 91, 'HaaS / HARNESS AS A SERVICE', 19, MUTED, 400, 'text-anchor="end" letter-spacing="2"')}
  ${text(96, 215, scene.title[0], titleSize, INK, 650, 'letter-spacing="-3"')}
  ${text(96, 312, scene.title[1], titleSize, scene.id === 'value' ? MINT : INK, 650, 'letter-spacing="-3"')}
  ${text(99, 374, scene.subtitle, 29, MUTED)}
  <g transform="translate(0 -24)">${body}</g>
  <rect x="0" y="790" width="1920" height="290" fill="#101b22"/>
  ${line(96, 794, 1824, 794, '#354750', 1)}
  ${line(96, 1057, 1824, 1057, '#2d444a', 2)}${line(96, 1057, 96 + progress * 1728, 1057, MINT, 3)}
  </svg>`;
}
function problem(c) {
  const p = c.localIndex;
  let out = card(96, 502, 312, 177, 'YOUR PRODUCT', 'App / Manager', '一份业务逻辑');
  out += `<path d="M408 588H462C515 588 496 488 571 488H705M462 588H705M462 588C519 588 492 688 571 688H705" fill="none" stroke="#62828e" stroke-width="2"/>`;
  for (const [i, name] of ['Codex', 'Pi', 'OpenCode / …'].entries()) out += rect(705, 459 + i * 100, 248, 61) + text(733, 500 + i * 100, name, 29);
  out += group(p >= 2 ? 1 : 0.4, rect(1050, 452, 774, 281, '#2b2927', '#7c6455') + text(1082, 498, 'THE INTEGRATION TAX / 重复接入成本', 21, '#e9b99f', 500, 'letter-spacing="1"') + text(1082, 556, 'Sessions & cancellation', 32) + text(1788, 556, '会话与取消', 25, '#d3b5a4', 400, 'text-anchor="end"') + line(1082, 579, 1788, 579, '#665346', 1) + text(1082, 620, 'Credentials & permissions', 32) + text(1788, 620, '凭据与权限', 25, '#d3b5a4', 400, 'text-anchor="end"') + line(1082, 643, 1788, 643, '#665346', 1) + text(1082, 684, 'Events & recovery', 32) + text(1788, 684, '事件与恢复', 25, '#d3b5a4', 400, 'text-anchor="end"'));
  return out + text(98, 791, 'Industry integration problem · 行业接入困境示意，不代表所有 runtime 已被 HaaS 支持', 22, MUTED);
}
function definition(c) {
  let out = rect(96, 451, 520, 298, '#172a32', '#486571') + text(126, 497, 'MODEL / 模型', 22, MUTED, 500, 'letter-spacing="2"') + text(126, 561, 'Reasoning & generation', 35, INK, 600) + text(126, 610, '提供推理与生成能力', 27, MUTED) + text(126, 707, 'Necessary. Not the whole runtime.', 25, MUTED);
  out += arrow(645, 597, 755, '#668794');
  out += rect(785, 451, 1039, 298, '#19352e', '#7eac95') + text(819, 497, 'HARNESS / 完整 AGENT RUNTIME', 22, MINT, 500, 'letter-spacing="2"');
  const names = [['Reason', '推理'], ['Use tools', '调用工具'], ['Edit files', '修改文件'], ['Keep state', '维护会话']];
  for (let i = 0; i < names.length; i++) out += group(c.localIndex >= 2 || i === 0 ? 1 : .4, rect(819 + i * 242, 539, 220, 120, '#102b26', '#4b7965') + text(841 + i * 242, 584, names[i][0], 28, INK, 500) + text(841 + i * 242, 626, names[i][1], 25, MUTED));
  out += text(819, 708, 'HaaS adds the service boundary, not a replacement engine.', 26, MINT);
  return out + text(98, 795, 'HaaS = Harness as a Service', 25, MUTED);
}
function architecture(c) {
  const p = c.localIndex;
  if (p === 3 || p === 4) {
    return card(96, 475, 400, 181, 'EXECUTION', 'Harness runtime', '仅持有限定范围的访问能力') + arrow(496, 566, 627) + text(561, 530, 'scoped access', 20, MINT, 400, 'text-anchor="middle"') + card(649, 475, 571, 181, 'TRUSTED CONTROL BOUNDARY', 'Model / MCP proxies', 'Provider 凭据在可信控制边界内') + arrow(1220, 566, 1351) + card(1373, 475, 451, 181, 'EXTERNAL SERVICES', 'Providers / tools', '授权的模型与工具服务') + rect(649, 696, 1175, 91, '#233e31', '#456958') + text(680, 753, 'Security design: keep real provider credentials out of the harness.', 29, MINT);
  }
  let out = card(96, 504, 300, 172, 'BUILD HERE', 'App / Manager', '产品与用户体验');
  out += arrow(396, 584, 567) + text(485, 540, 'HTTP + SSE', 24, MINT, 500, 'text-anchor="middle"');
  out += rect(593, 446, 644, 327, '#19352e', '#8aba9f') + text(627, 491, 'HaaS control sidecar', 35, MINT, 600);
  out += text(627, 542, 'Session + invocation', 29) + text(1203, 542, '会话与执行', 24, MUTED, 400, 'text-anchor="end"') + text(627, 587, 'Policy + scoped access', 29) + text(1203, 587, '权限边界', 24, MUTED, 400, 'text-anchor="end"') + text(627, 632, 'Canonical events + replay', 29) + text(1203, 632, '事件与回放', 24, MUTED, 400, 'text-anchor="end"');
  out += rect(624, 673, 583, 66, '#102b25', '#507a66') + text(915, 716, 'Adapter / 原生协议隔离', 28, MINT, 500, 'text-anchor="middle"');
  out += arrow(1237, 584, 1350) + card(1374, 504, 450, 172, 'FIRST IMPLEMENTATION', 'Codex app-server', 'Workspace · tools · MCP', p >= 2);
  return out + text(1375, 723, 'Pi / OpenCode / AMP: planned', 24, MUTED) + text(1375, 760, '隔离方式由运行模式决定', 24, MUTED);
}
function execution(c) {
  const facts = film.evidence;
  const active = [0, 1, 1, 2, 3, 4, 4][c.localIndex];
  const steps = [['Discover', '发现 harness'], ['Submit', '提交执行'], ['Stream', '统一事件流'], ['Terminal', '明确终态'], ['Read back', '回读执行结果']];
  let out = '';
  for (let i = 0; i < steps.length; i++) {
    const x = 96 + i * 352;
    out += card(x, 458, 317, 176, `0${i + 1}`, steps[i][0], steps[i][1], i <= active);
    if (i < 4) out += arrow(x + 317, 540, x + 347, i < active ? MINT : LINE);
  }
  out += rect(96, 674, 1728, 112, '#172b34', '#49646f');
  const label = facts?.status === 'passed' ? 'PROTOCOL DEMONSTRATION · TEST ADAPTER / 协议演示 · 测试 adapter' : 'ARCHITECTURE WALKTHROUGH / 架构流程示意';
  out += text(125, 711, label, 21, MINT, 500);
  if (facts?.status === 'passed') out += text(125, 758, `Discovery HTTP ${facts.discoveryStatus}   →   SSE HTTP ${facts.streamStatus}   →   ${facts.eventCount} events   →   ${facts.terminalStatus}   →   readback HTTP ${facts.readbackStatus}`, 28, INK);
  else out += text(125, 758, 'Execution evidence unavailable. This is an illustration, not a runtime recording.', 27, MUTED);
  return out;
}
function lifecycle(c) {
  const p = c.localIndex;
  const data = [['RETRY / 重试', 'Idempotency', '同一请求不重复启动'], ['CONCURRENCY / 并发', 'Session lease', '同一 session 单 active turn'], ['DISCONNECT / 断线', 'Event replay', '连接中断不等于任务取消']];
  let out = '';
  for (let i = 0; i < 3; i++) out += card(96 + i * 588, 464, 552, 196, ...data[i], p >= i + 1);
  out += rect(96, 704, 1728, 84, '#302b24', '#806c4e') + text(126, 756, 'Recovery depends on runtime + retained state. Failure stays explicit.', 30, '#f0d8b5');
  return out;
}
function deployment(c) {
  const p = c.localIndex;
  let out = card(96, 456, 552, 193, 'DESKTOP DESIGN', 'Local managed sidecar', '本地进程 ≠ 容器隔离', p >= 1) + card(684, 456, 552, 193, 'EXPLICIT OPTION', 'Remote endpoint', '远端入口 ≠ workspace 自动同步', p >= 2) + card(1272, 456, 552, 193, 'EXPLICIT OPTION', 'Delegated containers', 'Lite：最小执行 · AIO：桌面/浏览器', p >= 2);
  out += rect(96, 692, 1728, 101, '#1b302c', '#527568') + text(125, 733, 'IMPLEMENTED FIRST: Codex', 24, MINT, 500) + text(749, 733, 'PLANNED: Pi / OpenCode / AMP', 24, MUTED, 500) + text(125, 774, 'Source code is not a verified release. Check the current capability and validation status.', 25, MUTED);
  return out;
}
function value(c) {
  const data = [['01 / INTEGRATE', '稳定接入', 'One application-facing contract'], ['02 / OPERATE', '执行有据', 'Track work beyond the connection'], ['03 / GOVERN', '权限有界', 'Explicit policy and credential scope']];
  let out = '';
  for (let i = 0; i < 3; i++) {
    const x = 96 + i * 588;
    out += line(x, 457, x + 552, 457, '#638574', 2) + text(x, 501, data[i][0], 22, MINT, 500) + text(x, 565, data[i][1], 49, INK, 600) + text(x, 616, data[i][2], 29, MUTED);
  }
  out += rect(96, 666, 1728, 128, '#244633', '#527b5e') + text(129, 710, 'EXPLORE THE CODE / 探索代码与文档', 22, MINT, 500) + text(129, 764, film.story.repositoryUrl.replace('https://', ''), 43, INK, 600);
  return out;
}
window.renderFrame = index => {
  const cue = film.timeline.cues[index];
  const scene = film.story.scenes[cue.sceneIndex];
  const renderers = { problem, definition, architecture, execution, lifecycle, deployment, value };
  document.getElementById('frame').innerHTML = shell(scene, cue.sceneIndex, renderers[scene.id](cue), cue.end / film.timeline.duration);
};
window.checkFrame = () => [...document.querySelectorAll('svg text')].flatMap(el => {
  const r = el.getBBox();
  return r.x < 90 || r.x + r.width > 1832 || r.y < 45 || r.y + r.height > 815 ? [el.textContent] : [];
});
window.renderCover = () => {
  document.getElementById('frame').innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080"><rect width="1920" height="1080" fill="#101b22"/>
  <path d="M1530 260v520M1730 260v520M1530 520h200" fill="none" stroke="#a8f0cf" stroke-width="40"/><path d="M1450 520h80m200 0h80" fill="none" stroke="#e6eee6" stroke-width="14"/>
  ${text(96, 127, 'HaaS / HARNESS AS A SERVICE', 30, MINT, 600, 'letter-spacing="3"')}
  ${text(96, 319, 'Make agent execution', 93, INK, 650, 'letter-spacing="-3"')}
  ${text(96, 430, 'a service.', 116, MINT, 650, 'letter-spacing="-3"')}
  ${text(100, 520, '把 Agent 执行变成产品可以依赖的服务。', 43, MUTED)}
  ${text(100, 665, 'Why it exists. How it works. What it changes.', 36, INK)}
  ${rect(100, 760, 393, 90, '#a8f0cf', '#a8f0cf')}<path d="m137 785 0 40 34-20Z" fill="#173329"/>${text(193, 818, 'Watch the explainer', 27, '#173329', 600)}
  ${text(100, 974, 'ENGLISH NARRATION  /  中英双语字幕', 25, MUTED, 500, 'letter-spacing="1"')}</svg>`;
};
renderFrame(0);
