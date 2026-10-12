// Headless Chrome session for interface tests: no account, server or real archive.
// Override CHROME_PATH to use a different Chrome/Chromium executable.
import {spawn} from 'node:child_process';
import {mkdir} from 'node:fs/promises';
import {createServer} from 'node:net';
import {fileURLToPath, pathToFileURL} from 'node:url';
import path from 'node:path';

export const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

// Runs before the page scripts: an in-memory desktop bridge and a native-dialog probe.
function installBridge(seed) {
  const clone = value => JSON.parse(JSON.stringify(value));
  const qa = window.__qa = {store: seed.reviews, importReviews: seed.importReviews, saves: 0, nativeConfirms: 0, imageReads: 0};
  window.confirm = () => { qa.nativeConfirms++; return true; };
  const state = (message = '') => ({ok: true, needsSetup: false, reviews: clone(qa.store),
    dataDirectory: 'C:\\qa', dataFile: 'C:\\qa\\movie-reviews.json', message, error: ''});
  const noImage = async () => ({ok: false, error: '测试环境没有剧照'});
  // Optional synthetic stills: one generated JPEG per reference, served like a desktop original.
  const stills = new Map();
  const still = reference => {
    if (!stills.has(reference)) {
      const canvas = document.createElement('canvas'), context = canvas.getContext('2d');
      canvas.width = 900; canvas.height = 1350;
      const hue = [...reference].reduce((sum, character) => (sum * 31 + character.charCodeAt(0)) % 360, 7);
      const paint = context.createLinearGradient(0, 0, 900, 1350);
      paint.addColorStop(0, `hsl(${hue} 55% 42%)`); paint.addColorStop(1, `hsl(${(hue + 70) % 360} 45% 12%)`);
      context.fillStyle = paint; context.fillRect(0, 0, 900, 1350);
      context.strokeStyle = 'rgba(255,240,200,.8)'; context.lineWidth = 10; context.beginPath(); context.arc(450, 520, 260, 0, Math.PI * 2); context.stroke();
      const data = canvas.toDataURL('image/jpeg', 0.85).split(',')[1];
      stills.set(reference, {data, size: atob(data).length});
    }
    return stills.get(reference);
  };
  const imageInfo = async reference => { qa.imageReads++; const {size} = still(reference); return {ok: true, path: reference, size, mime: 'image/jpeg', chunkSize: 64 * 1024 * 1024, error: ''}; };
  const imageChunk = async reference => { const {data, size} = still(reference); return {ok: true, path: reference, offset: 0, nextOffset: size, size, data, done: true, error: ''}; };
  window.pywebview = {api: {
    get_state: async () => state(),
    choose_data_directory: async () => state(),
    save_reviews: async reviews => { qa.store = clone(reviews); qa.saves++; return state('数据已保存'); },
    select_review_image: async () => ({ok: false, cancelled: true, error: ''}),
    get_review_image_info: seed.stills ? imageInfo : noImage,
    get_review_image_chunk: seed.stills ? imageChunk : noImage,
    select_import_file: async () => ({ok: true, cancelled: false, reviews: clone(qa.importReviews),
      importToken: 'qa-import', format: 'json', error: ''}),
    apply_import: async () => { qa.store = clone(qa.importReviews); return state(); },
    export_backup: async () => ({ok: true, cancelled: false, error: ''}),
    export_annual_report: async () => ({ok: true, cancelled: false, error: ''}),
    export_graph_image: async () => ({ok: true, cancelled: false, error: ''}),
    export_interactive_graph: async () => ({ok: true, cancelled: false, error: ''}),
    open_data_directory: async () => ({ok: true, error: ''}),
    exit_app: async () => null
  }};
}

export async function launchBrowser(label) {
  const output = path.join(root, '.runtime-test', `${label}-${Date.now()}`);
  await mkdir(output, {recursive: true});
  const portServer = createServer();
  await new Promise(resolve => portServer.listen(0, '127.0.0.1', resolve));
  const port = portServer.address().port;
  await new Promise(resolve => portServer.close(resolve));
  const chromePath = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
  const chrome = spawn(chromePath, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-proxy-server',
    '--no-default-browser-check', '--hide-scrollbars', `--user-data-dir=${path.join(output, 'profile')}`,
    '--remote-debugging-address=127.0.0.1', `--remote-debugging-port=${port}`, 'about:blank'],
  {windowsHide: true, stdio: ['ignore', 'ignore', 'pipe']});
  let diagnostics = '';
  chrome.stderr.on('data', chunk => { diagnostics += chunk.toString().slice(0, 2000); });
  chrome.on('error', error => { diagnostics += error.message; });

  let target;
  for (let attempt = 0; attempt < 300 && !target; attempt++) {  // a cold hosted runner can need well over 8 s
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      target = targets.find(item => item.type === 'page');
    } catch {}
    if (!target) await pause(100);
  }
  if (!target) { chrome.kill(); throw new Error('Chrome did not start: ' + diagnostics.slice(0, 2000)); }
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let nextId = 0;
  const pending = new Map();
  const loadWaiters = [];
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.method === 'Page.loadEventFired') loadWaiters.splice(0).forEach(resolve => resolve());
    if (!message.id || !pending.has(message.id)) return;
    const {resolve, reject} = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(JSON.stringify(message.error)));
    else resolve(message.result);
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, {resolve, reject});
    socket.send(JSON.stringify({id, method, params}));
  });
  await send('Page.enable');
  await send('Runtime.enable');
  let bridgeScript = null;

  // Evaluate a function in the page; top-level bindings such as `reviews` are in scope.
  async function page(callback, argument) {
    const expression = `(${callback.toString()})(${JSON.stringify(argument ?? null)})`;
    const result = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if (result.exceptionDetails) {
      const details = result.exceptionDetails;
      throw new Error(details.exception?.description || details.text || JSON.stringify(details));
    }
    return result.result.value;
  }

  // Load index.html in a fresh page with the given records and viewport.
  async function loadApp({reviews = [], importReviews = [], stills = false, width = 1280, height = 800, mobile = false} = {}) {
    if (bridgeScript) await send('Page.removeScriptToEvaluateOnNewDocument', {identifier: bridgeScript});
    const source = `(${installBridge.toString()})(${JSON.stringify({reviews, importReviews, stills})})`;
    bridgeScript = (await send('Page.addScriptToEvaluateOnNewDocument', {source})).identifier;
    await send('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile});
    const loaded = new Promise(resolve => loadWaiters.push(resolve));
    await send('Page.navigate', {url: pathToFileURL(path.join(root, 'index.html')).href});
    await loaded;
    for (let attempt = 0; attempt < 100; attempt++) {
      if (await page(() => typeof appInitialized !== 'undefined' && appInitialized &&
          document.getElementById('setupOverlay').classList.contains('hidden'))) return;
      await pause(50);
    }
    throw new Error('The interface did not initialise with the test bridge');
  }

  return {output, page, loadApp, send, close: () => { socket.close(); chrome.kill(); }};
}
