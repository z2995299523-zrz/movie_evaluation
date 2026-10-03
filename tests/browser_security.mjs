// Browser regression using synthetic in-memory records, with no account or server.
// Windows: node tests/browser_security.mjs
// Override CHROME_PATH to use a different Chrome/Chromium executable.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {createServer} from 'node:net';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.join(root, '.runtime-test', 'browser-security-' + Date.now());
await mkdir(output, {recursive: true});
const portServer = createServer();
await new Promise(resolve => portServer.listen(0, '127.0.0.1', resolve));
const port = portServer.address().port;
await new Promise(resolve => portServer.close(resolve));
const chromePath = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const chrome = spawn(chromePath, ['--headless=new', '--disable-gpu', '--no-first-run',
  '--no-proxy-server', '--no-default-browser-check', `--user-data-dir=${path.join(output, 'profile')}`,
  '--remote-debugging-address=127.0.0.1', `--remote-debugging-port=${port}`, 'about:blank'],
{windowsHide: true, stdio: ['ignore', 'ignore', 'pipe']});
let diagnostics = '';
chrome.stderr.on('data', chunk => { diagnostics += chunk.toString().slice(0, 2000); });
chrome.on('error', error => { diagnostics += error.message; });
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let socket;
let nextId = 0;
const pending = new Map();
function send(method, params = {}) {
  const id = ++nextId;
  return new Promise((resolve, reject) => {
    pending.set(id, {resolve, reject});
    socket.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const result = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
try {
  let target;
  for (let attempt = 0; attempt < 60; attempt++) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      target = targets.find(item => item.type === 'page');
      if (target) break;
    } catch {}
    await pause(100);
  }
  if (!target) throw new Error('Chrome did not start: ' + diagnostics.slice(0, 2000));
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {socket.onopen = resolve; socket.onerror = reject;});
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const {resolve, reject} = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(JSON.stringify(message.error)));
      else resolve(message.result);
    }
  };
  await send('Page.enable');
  await send('Runtime.enable');
  const {frameTree} = await send('Page.getFrameTree');
  await send('Page.setDocumentContent', {frameId: frameTree.frame.id,
    html: await readFile(path.join(root, 'index.html'), 'utf8')});
  const cases = [
    'x" onpointerenter="window.__securityExecuted=true//',
    "x' onpointerenter='window.__securityExecuted=true//",
    '<img src=x onerror="window.__securityExecuted=true">',
    'A & B "引用"'
  ];
  const results = [];
  for (const text of cases) {
    const result = await evaluate(`(() => {
      const value = ${JSON.stringify(text)};
      window.__securityExecuted = false;
      reviews = [{id:'security_test', title:value, director:value, date:'2026-01-01',
        rating:4, tags:[value], categories:[value], comment:value, rewatches:[]}];
      render(); renderTasteProfile(); renderGallery(getVisibleReviews());
      const card = document.querySelector('.movie-card');
      const label = document.querySelector('#tasteContent .taste-bar-label');
      const injected = document.querySelector('[onpointerenter], [onerror]');
      card.dispatchEvent(new Event('pointerenter'));
      label.dispatchEvent(new Event('pointerenter'));
      return {executed:window.__securityExecuted, hasInjectedEvent:!!injected,
        titleUnchanged:card.getAttribute('aria-label') === '查看《' + value + '》影评详情',
        categoryUnchanged:label.textContent === value && label.title === value,
        hasInjectedImage:!!document.querySelector('#tasteContent img')};
    })()`);
    assert.equal(result.executed, false, text);
    assert.equal(result.hasInjectedEvent, false, text);
    assert.equal(result.titleUnchanged, true, text);
    assert.equal(result.categoryUnchanged, true, text);
    assert.equal(result.hasInjectedImage, false, text);
    results.push(result);
  }
  await writeFile(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
  console.log(`Browser security passed: ${results.length} text/attribute injection cases; ${output}`);
} finally {
  if (socket) socket.close();
  chrome.kill();
}
