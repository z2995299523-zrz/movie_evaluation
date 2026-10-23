// Real Chrome export regression: synthetic images and an in-memory desktop bridge only.
// The saved PNGs are reviewable examples; no account, server or personal archive is used.
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
import {launchBrowser} from './browser_harness.mjs';

const browser = await launchBrowser('browser-poster-export');
const {page, loadApp} = browser;
const results = [];
const examples = [];

async function fixture(viewport = {}) {
  await loadApp({reviews: [], width: 1440, height: 1000, ...viewport});
  await page(() => {
    const q = window.__posterQA = {images: new Map(), exports: [], draws: [], text: [], gradients: [], recording: false};
    q.specs = [
      {id: 'wide', title: '横向剧照 · 十六比九', width: 1600, height: 900, rating: 5, date: '2026-10-23'},
      {id: 'cinema', title: '超宽银幕 · 两侧人物完整保留', width: 2400, height: 900, rating: 4, date: '2026-10-22'},
      {id: 'portrait', title: '竖向原图 · 保留完整构图', width: 900, height: 1350, rating: 3, date: '2026-10-21'},
      {id: 'missing', title: '没有剧照也保留片名与观影信息', width: 0, height: 0, rating: 0, date: '2026-10-20'}
    ];
    q.colors = {left: [239, 68, 68], right: [250, 204, 21], top: [34, 211, 238], bottom: [74, 222, 128]};
    for (const spec of q.specs.filter(item => item.width)) {
      const canvas = document.createElement('canvas'); canvas.width = spec.width; canvas.height = spec.height;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#26354d'; ctx.fillRect(0, 0, spec.width, spec.height);
      const band = Math.floor(Math.min(spec.width, spec.height) * .065);
      ctx.fillStyle = '#ef4444'; ctx.fillRect(0, 0, band, spec.height);
      ctx.fillStyle = '#facc15'; ctx.fillRect(spec.width - band, 0, band, spec.height);
      ctx.fillStyle = '#22d3ee'; ctx.fillRect(band, 0, spec.width - band * 2, band);
      ctx.fillStyle = '#4ade80'; ctx.fillRect(band, spec.height - band, spec.width - band * 2, band);
      ctx.strokeStyle = '#7e91af'; ctx.lineWidth = 8;
      ctx.beginPath(); ctx.moveTo(band, band); ctx.lineTo(spec.width - band, spec.height - band);
      ctx.moveTo(spec.width - band, band); ctx.lineTo(band, spec.height - band); ctx.stroke();
      ctx.fillStyle = '#f3ede5'; ctx.font = '700 80px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText(`${spec.width} × ${spec.height}`, spec.width / 2, spec.height / 2 - 30);
      ctx.font = '40px sans-serif'; ctx.fillText('四边色带应完整保留', spec.width / 2, spec.height / 2 + 50);
      const data = canvas.toDataURL('image/png').split(',')[1];
      spec.path = `media/${spec.id}.png`; spec.band = band;
      q.images.set(spec.path, {data, size: atob(data).length});
    }
    window.pywebview.api.get_review_image_info = async reference => {
      const image = q.images.get(reference); if (!image) return {ok: false, error: '合成缺图'};
      return {ok: true, path: reference, size: image.size, mime: 'image/png', chunkSize: 64 * 1024 * 1024};
    };
    window.pywebview.api.get_review_image_chunk = async reference => {
      const image = q.images.get(reference); return {ok: true, path: reference, offset: 0, nextOffset: image.size, size: image.size, data: image.data, done: true};
    };
    window.pywebview.api.export_graph_image = async (data, name) => {
      q.exports.push({data, name, draws: q.draws.slice(), text: q.text.slice(), gradients: q.gradients.slice()});
      return {ok: true, cancelled: false};
    };
    const fillText = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (value, ...args) {
      if (q.recording && value === 'CURATED REVIEW WALL') q.exportCanvas = this.canvas;
      if (q.recording && this.canvas === q.exportCanvas) q.text.push({value: String(value), x: args[0], y: args[1], font: this.font, baseline: this.textBaseline});
      return fillText.call(this, value, ...args);
    };
    const drawImage = CanvasRenderingContext2D.prototype.drawImage;
    CanvasRenderingContext2D.prototype.drawImage = function (image, ...args) {
      if (q.recording && this.canvas === q.exportCanvas && image instanceof HTMLImageElement) q.draws.push({width: image.naturalWidth, height: image.naturalHeight, args});
      return drawImage.call(this, image, ...args);
    };
    const gradient = CanvasRenderingContext2D.prototype.createLinearGradient;
    CanvasRenderingContext2D.prototype.createLinearGradient = function (...args) {
      if (q.recording && this.canvas === q.exportCanvas) q.gradients.push(args);
      return gradient.call(this, ...args);
    };
    q.capture = async style => {
      const select = document.getElementById('galleryExportStyle');
      select.value = style; select.dispatchEvent(new Event('change', {bubbles: true}));
      q.draws = []; q.text = []; q.gradients = []; q.exportCanvas = null; q.recording = true;
      const before = q.exports.length;
      let during;
      try {
        const pending = exportGallery();
        during = {styleDisabled: select.disabled, exportDisabled: document.getElementById('galleryExportButton').disabled, busy: galleryExportBusy};
        await pending;
      } finally { q.recording = false; }
      if (q.exports.length !== before + 1) throw new Error('Expected one PNG export');
      const capture = q.exports.at(-1);
      capture.controls = {during, after: {styleDisabled: select.disabled, exportDisabled: document.getElementById('galleryExportButton').disabled, busy: galleryExportBusy}};
      return capture;
    };
    q.analyze = async capture => {
      const image = new Image(); image.src = capture.data; await image.decode();
      const canvas = document.createElement('canvas'); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
      const ctx = canvas.getContext('2d', {willReadFrequently: true}); ctx.drawImage(image, 0, 0);
      const pixel = (x, y) => Array.from(ctx.getImageData(Math.round(x), Math.round(y), 1, 1).data).slice(0, 3);
      const draws = capture.draws.map(draw => {
        const spec = q.specs.find(item => item.width === draw.width && item.height === draw.height);
        const source = draw.args.length === 8 ? draw.args.slice(0, 4) : [0, 0, draw.width, draw.height];
        const destination = draw.args.length === 8 ? draw.args.slice(4) : draw.args;
        const [sx, sy, sw, sh] = source, [dx, dy, dw, dh] = destination;
        const samples = {
          left: [spec.band / 2, spec.height / 2], right: [spec.width - spec.band / 2, spec.height / 2],
          top: [spec.width / 2, spec.band / 2], bottom: [spec.width / 2, spec.height - spec.band / 2]
        };
        const edges = Object.fromEntries(Object.entries(samples).map(([edge, [x, y]]) => [edge, {
          visible: x >= sx && x < sx + sw && y >= sy && y < sy + sh,
          actual: pixel(dx + (x - sx) / sw * dw, dy + (y - sy) / sh * dh), expected: q.colors[edge]
        }]));
        return {id: spec.id, source, destination, edges};
      });
      return {width: canvas.width, height: canvas.height, draws, text: capture.text, gradients: capture.gradients, name: capture.name, controls: capture.controls};
    };
    reviews = q.specs.map((spec, index) => ({id: spec.id, title: spec.title, date: spec.date, rating: spec.rating,
      tags: [], categories: [], comment: '海报导出合成测试', createdAt: 4 - index, updatedAt: 4 - index,
      image: spec.path ? {path: spec.path, name: spec.id + '.png', mime: 'image/png', size: q.images.get(spec.path).size} : null}));
    renderAll(); openGallery(); setGalleryMode('wall');
  });
}

async function check(name, run) {
  try { await run(); results.push({name, passed: true}); }
  catch (error) { results.push({name, passed: false, error: String(error.stack || error)}); }
}

async function saveExport(filename) {
  const data = await page(() => __posterQA.exports.at(-1).data);
  const target = path.join(browser.output, filename);
  await writeFile(target, Buffer.from(data.split(',')[1], 'base64'));
  examples.push(target);
}

try {
  await check('landscape export retains all four edges of 16:9, ultrawide and portrait originals', async () => {
    await fixture();
    assert.equal(await page(() => document.getElementById('galleryExportStyle').value), 'landscape');
    const exported = await page(async () => __posterQA.analyze(await __posterQA.capture('landscape')));
    await saveExport('landscape-synthetic.png');
    assert.deepEqual([exported.width, exported.height], [1364, 924]);
    assert.equal(exported.draws.length, 3);
    assert.equal(exported.gradients.length, 0, 'No gradient may obscure the still image');
    assert.deepEqual(exported.controls, {during: {styleDisabled: true, exportDisabled: true, busy: true},
      after: {styleDisabled: false, exportDisabled: false, busy: false}});
    for (const draw of exported.draws) {
      const diagnostic = JSON.stringify(draw);
      assert.ok(Object.values(draw.edges).every(edge => edge.visible), diagnostic);
      for (const [edgeName, edge] of Object.entries(draw.edges)) {
        assert.ok(edge.actual.every((value, channel) => Math.abs(value - edge.expected[channel]) <= 3), `${draw.id} ${edgeName}: ${JSON.stringify(edge)}`);
      }
      const [x, y, w, h] = draw.destination;
      const sourceRatio = draw.source[2] / draw.source[3];
      assert.ok(Math.abs(w / h - sourceRatio) < .001, diagnostic);
      assert.ok(y >= 174 + 2 && y + h <= 174 + 225 - 2 + .001, diagnostic);
      assert.ok(w <= 396.001 && x >= 66, diagnostic);
    }
  });

  await check('landscape titles, ratings and viewing dates are in separate information areas', async () => {
    const state = await page(async () => ({analysis: await __posterQA.analyze(__posterQA.exports.at(-1)),
      specs: __posterQA.specs.map(spec => ({id: spec.id, title: spec.title, rating: spec.rating, date: formatCompactDate(spec.date)}))}));
    for (let index = 0; index < state.specs.length; index++) {
      const spec = state.specs[index], x = 64 + (index % 3) * 418, imageBottom = 174 + Math.floor(index / 3) * 351 + 225;
      const cardText = state.analysis.text.filter(text => text.x >= x && text.x < x + 400 && text.y > imageBottom && text.y < imageBottom + 108);
      assert.ok(cardText.some(text => text.value === spec.title), JSON.stringify({spec, cardText}));
      assert.ok(cardText.some(text => spec.rating ? text.value.includes(`${spec.rating}`) && text.value.includes('★') : text.value.includes('未评分')), JSON.stringify({spec, cardText}));
      assert.ok(cardText.some(text => text.value.includes(spec.date)), JSON.stringify({spec, cardText}));
      assert.ok(cardText.every(text => text.y - parseFloat(text.font.match(/([\d.]+)px/)?.[1] || 0) >= imageBottom), JSON.stringify(cardText));
    }
    assert.equal(await page(() => __qa.saves), 0);
  });

  await check('classic portrait export retains its 220 by 330 cover crop and overlay layout', async () => {
    const exported = await page(async () => __posterQA.analyze(await __posterQA.capture('portrait')));
    await saveExport('portrait-synthetic.png');
    assert.deepEqual([exported.width, exported.height], [1062, 570]);
    assert.equal(exported.draws.length, 3);
    assert.equal(exported.gradients.length, 4);
    const wide = exported.draws.find(draw => draw.id === 'wide');
    assert.deepEqual(wide.destination, [64, 174, 220, 330]);
    assert.ok(wide.source[0] > 0 && wide.source[2] < 1600);
    assert.ok(!wide.edges.left.visible && !wide.edges.right.visible);
    assert.ok(exported.text.some(text => text.value === '5 ★' && text.y === 201));
    assert.ok(exported.text.some(text => text.value === '横向剧照 · 十六比九' && text.y === 452));
    assert.ok(exported.text.some(text => text.value.includes('2026/10/23') && text.y === 481));
  });

  await check('style switching preserves selected films and empty selection blocks both exports', async () => {
    const state = await page(async () => {
      toggleGallerySelection(); toggleGalleryCard('wide'); toggleGalleryCard('portrait');
      const selected = getGalleryExportItems().map(review => review.id);
      const landscape = await __posterQA.analyze(await __posterQA.capture('landscape'));
      const portrait = await __posterQA.analyze(await __posterQA.capture('portrait'));
      const afterSwitch = getGalleryExportItems().map(review => review.id);
      setGallerySelection('none'); const before = __posterQA.exports.length;
      const disabled = [];
      for (const style of ['landscape', 'portrait']) {
        document.getElementById('galleryExportStyle').value = style; updateGallerySelection();
        disabled.push(document.getElementById('galleryExportButton').disabled);
        await exportGallery(); await exportPosterWall();
      }
      setGallerySelection('all');
      return {selected, afterSwitch, landscapeIds: landscape.draws.map(draw => draw.id), portraitIds: portrait.draws.map(draw => draw.id),
        landscapeSize: [landscape.width, landscape.height], portraitSize: [portrait.width, portrait.height], disabled,
        emptyDoesNotExport: before === __posterQA.exports.length, reenabled: !document.getElementById('galleryExportButton').disabled, saves: __qa.saves};
    });
    assert.deepEqual(state.selected, ['wide', 'portrait']);
    assert.deepEqual(state.afterSwitch, state.selected);
    assert.deepEqual(state.landscapeIds, state.selected); assert.deepEqual(state.portraitIds, state.selected);
    assert.deepEqual(state.landscapeSize, [946, 573]); assert.deepEqual(state.portraitSize, [586, 570]);
    assert.deepEqual(state.disabled, [true, true]);
    assert.ok(state.emptyDoesNotExport && state.reenabled && state.saves === 0, JSON.stringify(state));
  });

  for (const width of [320, 390]) {
    await check(`export style and selection controls fit the ${width}px phone viewport`, async () => {
      await fixture({width, height: 844, mobile: true});
      const layout = await page(async () => {
        toggleGallerySelection();
        await new Promise(resolve => setTimeout(resolve, 400));
        const overlay = document.getElementById('galleryOverlay'), stage = document.getElementById('galleryStage');
        const controls = [...document.getElementById('gallerySelectionTools').querySelectorAll('button,input,select')]
          .filter(element => element.getClientRects().length);
        const outside = controls.filter(element => {const rect = element.getBoundingClientRect(); return rect.left < -1 || rect.right > innerWidth + 1;})
          .map(element => element.id);
        const select = document.getElementById('galleryExportStyle'), rect = select.getBoundingClientRect();
        const before = {outside, selectVisible: rect.width > 0 && rect.height >= 36,
          selectFont: parseFloat(getComputedStyle(select).fontSize),
          horizontalOverflow: overlay.scrollWidth > overlay.clientWidth + 1 || stage.scrollWidth > stage.clientWidth + 1};
        setGalleryMode('relation'); const hiddenInRelation = select.getClientRects().length === 0;
        setGalleryMode('wall'); return {...before, hiddenInRelation, visibleAgain: select.getClientRects().length > 0, saves: __qa.saves};
      });
      const screenshot = await browser.send('Page.captureScreenshot', {format: 'png'});
      await writeFile(path.join(browser.output, `controls-${width}px.png`), Buffer.from(screenshot.data, 'base64'));
      assert.deepEqual(layout.outside, [], JSON.stringify(layout));
      assert.ok(layout.selectVisible && layout.selectFont >= 16 && !layout.horizontalOverflow && layout.hiddenInRelation && layout.visibleAgain && layout.saves === 0, JSON.stringify(layout));
    });
  }
} finally {
  browser.close();
}

await writeFile(path.join(browser.output, 'results.json'), JSON.stringify({results, examples}, null, 2));
for (const result of results) console.log(`${result.passed ? 'ok  ' : 'FAIL'} ${result.name}${result.passed ? '' : '\n     ' + result.error}`);
const failed = results.filter(result => !result.passed);
console.log(`Poster export: ${results.length - failed.length}/${results.length} passed; ${browser.output}`);
if (failed.length) process.exit(1);
