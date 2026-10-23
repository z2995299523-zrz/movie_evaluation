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
    const q = window.__posterQA = {images: new Map(), exports: [], draws: [], text: [], gradients: [], fills: [], strokes: [], paths: [], recording: false};
    q.specs = [
      {id: 'wide', title: '横向剧照 · 十六比九', width: 1600, height: 900, rating: 5, date: '2026-10-23', director: '克里斯托弗·诺兰', categories: ['剧情', '科幻']},
      {id: 'cinema', title: '超宽银幕 · 两侧人物完整保留', width: 2400, height: 900, rating: 4, date: '2026-10-22', director: '王家卫', categories: ['爱情', '剧情']},
      {id: 'portrait', title: '竖向原图 · 保留完整构图', width: 900, height: 1350, rating: 3, date: '2026-10-21', director: '宫崎骏', categories: ['动画', '奇幻']},
      {id: 'missing', title: '没有剧照也保留片名与观影信息', width: 0, height: 0, rating: 0, date: '', director: '', categories: []}
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
      q.exports.push({data, name, draws: q.draws.slice(), text: q.text.slice(), gradients: q.gradients.slice(),
        fills: q.fills.slice(), strokes: q.strokes.slice(), paths: q.paths.slice()});
      return {ok: true, cancelled: false};
    };
    const fillText = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (value, ...args) {
      if (q.recording && ['CURATED REVIEW WALL', '我的电影手记'].includes(value)) q.exportCanvas = this.canvas;
      if (q.recording && this.canvas === q.exportCanvas) {
        const metrics = this.measureText(String(value)), transform = this.getTransform();
        q.text.push({value: String(value), x: args[0], y: args[1], font: this.font, baseline: this.textBaseline,
          align: this.textAlign, color: this.fillStyle, width: metrics.width, maxWidth: args[2] ?? null,
          bounds: {left: args[0] - metrics.actualBoundingBoxLeft, right: args[0] + metrics.actualBoundingBoxRight,
            top: args[1] - metrics.actualBoundingBoxAscent, bottom: args[1] + metrics.actualBoundingBoxDescent},
          transform: [transform.a, transform.b, transform.c, transform.d, transform.e, transform.f]});
      }
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
    for (const [method, field] of [['fillRect', 'fills'], ['strokeRect', 'strokes']]) {
      const original = CanvasRenderingContext2D.prototype[method];
      CanvasRenderingContext2D.prototype[method] = function (...args) {
        if (q.recording && this.canvas === q.exportCanvas) q[field].push({args, color: method === 'fillRect' ? this.fillStyle : this.strokeStyle});
        return original.call(this, ...args);
      };
    }
    for (const method of ['beginPath', 'moveTo', 'lineTo', 'closePath', 'fill', 'stroke']) {
      const original = CanvasRenderingContext2D.prototype[method];
      CanvasRenderingContext2D.prototype[method] = function (...args) {
        if (q.recording && this.canvas === q.exportCanvas) {
          if (method === 'beginPath') q.currentPath = {points: [], closed: false};
          if (method === 'moveTo' || method === 'lineTo') q.currentPath?.points.push(args.slice());
          if (method === 'closePath' && q.currentPath) q.currentPath.closed = true;
          if ((method === 'fill' || method === 'stroke') && q.currentPath) q.paths.push({
            points: q.currentPath.points.map(point => point.slice()), closed: q.currentPath.closed, mode: method,
            color: method === 'fill' ? this.fillStyle : this.strokeStyle});
        }
        return original.call(this, ...args);
      };
    }
    q.capture = async style => {
      const select = document.getElementById('galleryExportStyle');
      select.value = style; select.dispatchEvent(new Event('change', {bubbles: true}));
      q.draws = []; q.text = []; q.gradients = []; q.fills = []; q.strokes = []; q.paths = []; q.currentPath = null; q.exportCanvas = null; q.recording = true;
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
      const cards = capture.fills.filter(fill => fill.args[2] === 560 && fill.args[3] === 315).map(fill => {
        const [x, y] = fill.args, imageBottom = y + 315;
        const footer = capture.text.find(text => text.x === x && text.y > imageBottom && /^(?:观看于 .+  ·  )?已看 \d+ 次$/.test(text.value));
        const bottom = footer.y + 18;
        const text = capture.text.filter(text => text.x >= x && text.x <= x + 560 && text.y > imageBottom && text.y < bottom);
        const stars = capture.paths.filter(paint => paint.closed && paint.points.length === 10 &&
          paint.points.every(([px, py]) => px >= x && px <= x + 560 && py > imageBottom && py < bottom)).map(paint => {
          const center = paint.points.reduce(([cx, cy], [px, py]) => [cx + px / 10, cy + py / 10], [0, 0]);
          return {...paint, center, pixel: pixel(...center)};
        });
        return {x, y, imageBottom, bottom, footerY: footer.y, text, stars};
      });
      return {width: canvas.width, height: canvas.height, background: pixel(0, 0), cards, draws, text: capture.text,
        gradients: capture.gradients, fills: capture.fills, strokes: capture.strokes, paths: capture.paths, name: capture.name, controls: capture.controls};
    };
    reviews = q.specs.map((spec, index) => ({id: spec.id, title: spec.title, date: spec.date, rating: spec.rating,
      director: spec.director, tags: ['自定义标签不应作为电影类型'], categories: spec.categories,
      comment: '海报导出合成测试', createdAt: 4 - index, updatedAt: 4 - index,
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

function assertCardTextFits(text, x, imageBottom, cardBottom) {
  for (const item of text) {
    const diagnostic = JSON.stringify(item);
    assert.equal(item.maxWidth, null, 'Card text must not be horizontally compressed: ' + diagnostic);
    assert.deepEqual(item.transform, [1, 0, 0, 1, 0, 0], 'Card text must retain its natural scale: ' + diagnostic);
    assert.equal(item.baseline, 'alphabetic', diagnostic);
    assert.ok(item.bounds.left >= x - 1 && item.bounds.right <= x + 561, diagnostic);
    assert.ok(item.bounds.top >= imageBottom && item.bounds.bottom < cardBottom, diagnostic);
  }
  for (let first = 0; first < text.length; first++) {
    for (let second = first + 1; second < text.length; second++) {
      const a = text[first].bounds, b = text[second].bounds;
      const overlapWidth = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      const overlapHeight = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      assert.ok(overlapWidth <= 0 || overlapHeight <= 0, 'Card text overlaps: ' + JSON.stringify([text[first], text[second]]));
    }
  }
}

function assertRating(card, rating) {
  const diagnostic = JSON.stringify(card);
  const fills = card.stars.filter(star => star.mode === 'fill');
  const outlines = card.stars.filter(star => star.mode === 'stroke').sort((a, b) => a.center[0] - b.center[0]);
  assert.equal(fills.length, rating, diagnostic);
  assert.equal(outlines.length, rating ? 5 : 0, diagnostic);
  if (!rating) {
    assert.ok(card.text.some(text => text.value === '暂未评分'), diagnostic);
    assert.ok(card.text.every(text => text.value !== '我的评分' && !/^\d+ 分$/.test(text.value)), diagnostic);
    return;
  }
  const label = card.text.find(text => text.value === '我的评分');
  const score = card.text.find(text => text.value === `${rating} 分`);
  assert.ok(label && score && label.y === score.y && score.color === '#ffad61', diagnostic);
  assert.ok(card.text.every(text => !/^[1-5]\s*\/\s*5$/.test(text.value)), diagnostic);
  for (let index = 0; index < outlines.length; index++) {
    const star = outlines[index], active = index < rating;
    assert.ok(Math.abs(star.center[0] - (card.x + 10 + index * 24)) < .001, diagnostic);
    assert.ok(Math.abs(star.center[1] - (score.y - 7)) < .001, diagnostic);
    assert.equal(star.color, active ? '#ffad61' : '#747b87', diagnostic);
    assert.deepEqual(star.pixel, active ? [255, 173, 97] : [17, 19, 21], diagnostic);
    for (let point = 0; point < star.points.length; point++) {
      const [px, py] = star.points[point];
      assert.ok(Math.abs(Math.hypot(px - star.center[0], py - star.center[1]) - (point % 2 ? 4.5 : 10)) < .001, diagnostic);
    }
    const bounds = {left: Math.min(...star.points.map(point => point[0])), right: Math.max(...star.points.map(point => point[0])),
      top: Math.min(...star.points.map(point => point[1])), bottom: Math.max(...star.points.map(point => point[1]))};
    for (const text of card.text) {
      assert.ok(Math.min(bounds.right, text.bounds.right) <= Math.max(bounds.left, text.bounds.left) ||
        Math.min(bounds.bottom, text.bounds.bottom) <= Math.max(bounds.top, text.bounds.top), 'Star overlaps text: ' + JSON.stringify({star, text}));
    }
  }
}

try {
  await check('landscape export retains all four edges of 16:9, ultrawide and portrait originals', async () => {
    await fixture();
    assert.equal(await page(() => document.getElementById('galleryExportStyle').value), 'landscape');
    const exported = await page(async () => __posterQA.analyze(await __posterQA.capture('landscape')));
    await saveExport('landscape-synthetic.png');
    assert.deepEqual([exported.width, exported.height], [1288, 1180]);
    assert.equal(exported.draws.length, 3);
    assert.equal(exported.gradients.length, 0, 'No gradient may obscure the still image');
    assert.deepEqual(exported.background, [17, 19, 21]);
    assert.equal(exported.strokes.length, 0, 'Landscape images must not have card frames');
    assert.equal(exported.fills.length, 4, 'Only the four image areas have a separate background');
    assert.ok(exported.text.some(text => text.value === '我的电影手记'));
    assert.ok(exported.fills.every(fill => fill.color === '#080a0c'), 'Image areas use the dark film background');
    assert.deepEqual(exported.controls, {during: {styleDisabled: true, exportDisabled: true, busy: true},
      after: {styleDisabled: false, exportDisabled: false, busy: false}});
    for (let index = 0; index < exported.draws.length; index++) {
      const draw = exported.draws[index], card = exported.cards[index];
      const diagnostic = JSON.stringify(draw);
      assert.ok(Object.values(draw.edges).every(edge => edge.visible), diagnostic);
      for (const [edgeName, edge] of Object.entries(draw.edges)) {
        assert.ok(edge.actual.every((value, channel) => Math.abs(value - edge.expected[channel]) <= 3), `${draw.id} ${edgeName}: ${JSON.stringify(edge)}`);
      }
      const [x, y, w, h] = draw.destination;
      const sourceRatio = draw.source[2] / draw.source[3];
      assert.ok(Math.abs(w / h - sourceRatio) < .001, diagnostic);
      assert.ok(y >= card.y + 2 && y + h <= card.imageBottom - 2 + .001, diagnostic);
      assert.ok(w <= 556.001 && x >= card.x + 2 && x + w <= card.x + 558.001, diagnostic);
    }
  });

  await check('cinematic captions show explicit ratings, accurate metadata and aligned viewing information', async () => {
    const state = await page(async () => ({analysis: await __posterQA.analyze(__posterQA.exports.at(-1)),
      specs: __posterQA.specs.map(spec => ({id: spec.id, title: spec.title, rating: spec.rating,
        director: spec.director, categories: spec.categories, date: formatCompactDate(spec.date)}))}));
    for (let index = 0; index < state.specs.length; index++) {
      const spec = state.specs[index], card = state.analysis.cards[index];
      const {x, imageBottom, bottom, text: cardText} = card;
      const diagnostic = JSON.stringify({spec, cardText});
      assert.equal(x, 64 + (index % 2) * 600, diagnostic);
      const title = cardText.filter(text => text.font.includes('25px') && text.font.endsWith('sans-serif'));
      assert.equal(title.map(text => text.value).join(''), spec.title, diagnostic);
      assert.equal(title[0].y, imageBottom + 32, diagnostic);
      const ratingY = title.at(-1).y + 32;
      assert.ok(cardText.some(text => text.value === (spec.rating ? '我的评分' : '暂未评分') && text.y === ratingY), diagnostic);
      const metadata = [spec.director ? `导演 ${spec.director}` : '', spec.categories.join(' / ')].filter(Boolean).join('  ·  ');
      const lastY = ratingY + (metadata ? 30 : 0);
      if (metadata) assert.ok(cardText.some(text => text.value === metadata && text.x === x && text.y === lastY && text.font === '15px sans-serif'), diagnostic);
      assert.ok(cardText.some(text => text.value === (spec.date ? `观看于 ${spec.date}  ·  已看 1 次` : '已看 1 次') && text.x === x && text.y === card.footerY), diagnostic);
      assert.ok(card.footerY >= lastY + 30, diagnostic);
      assertCardTextFits(cardText, x, imageBottom, bottom);
      assertRating(card, spec.rating);
    }
    const cards = state.analysis.cards;
    assert.equal(cards[0].footerY, cards[1].footerY); assert.equal(cards[2].footerY, cards[3].footerY);
    assert.equal(cards[2].y - cards[0].bottom, 40);
    assert.equal(state.analysis.height, cards.at(-1).bottom + 66);
    assert.ok(state.analysis.text.every(text => !text.value.includes('自定义标签') && !['导演', '类型', '未填写', '未分类', '观看日期未填写'].includes(text.value)), JSON.stringify(state.analysis.text));
    assert.equal(cards[3].text.length, 3, 'Missing optional metadata leaves only the title, unrated message and viewing count');
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
    assert.deepEqual(state.landscapeSize, [1288, 683]); assert.deepEqual(state.portraitSize, [586, 570]);
    assert.deepEqual(state.disabled, [true, true]);
    assert.ok(state.emptyDoesNotExport && state.reenabled && state.saves === 0, JSON.stringify(state));
  });

  await check('long titles, director names and category lists wrap or truncate without shrinking or overlapping', async () => {
    await fixture();
    const exported = await page(async () => {
      const review = reviews.find(item => item.id === 'wide');
      review.title = '一部关于漫长旅途与相遇的电影：在深夜追寻失落的城市以及没有说完的故事'.repeat(3);
      review.director = '亚历山大·康斯坦丁诺维奇·罗伯特·克里斯托弗·诺兰 / 超长联合导演姓名'.repeat(2);
      review.categories = ['剧情', '科幻', '爱情', '冒险', '悬疑', '纪录片', '动画', '奇幻', '音乐', '家庭', '历史', '战争', '犯罪', '黑色电影', '传记', '西部', '实验电影', '长篇电影类型名称'];
      review.categories = [...review.categories, ...review.categories];
      return __posterQA.analyze(await __posterQA.capture('landscape'));
    });
    await saveExport('landscape-long-metadata.png');
    const {x, imageBottom, bottom, text} = exported.cards[0];
    const diagnostic = JSON.stringify(text);
    const title = text.filter(item => item.x === x && [imageBottom + 32, imageBottom + 63].includes(item.y));
    const director = text.filter(item => item.x === x && item.y === imageBottom + 125);
    const categories = text.filter(item => item.x === x && [imageBottom + 147, imageBottom + 169].includes(item.y));
    assert.equal(title.length, 2, diagnostic); assert.ok(title.at(-1).value.endsWith('…'), diagnostic);
    assert.equal(director.length, 1, diagnostic); assert.ok(director[0].value.endsWith('…'), diagnostic);
    assert.equal(categories.length, 2, diagnostic); assert.ok(categories.at(-1).value.endsWith('…'), diagnostic);
    assert.ok(title.every(item => item.font.includes('25px') && item.font.endsWith('sans-serif') && item.width <= 560), diagnostic);
    assert.ok([...director, ...categories].every(item => item.font === '15px sans-serif' && item.width <= 560), diagnostic);
    assert.equal(exported.cards[0].footerY, categories.at(-1).y + 30, diagnostic);
    assert.equal(exported.cards[0].footerY, exported.cards[1].footerY);
    assert.equal(exported.cards[2].y - bottom, 40);
    assert.equal(exported.height, exported.cards.at(-1).bottom + 66);
    assertCardTextFits(text, x, imageBottom, bottom);
    assertRating(exported.cards[0], 5);
    assert.equal(await page(() => __qa.saves), 0);
  });

  await check('missing optional fields collapse naturally while keeping genre-only and director-only records accurate', async () => {
    await fixture();
    const exported = await page(async () => {
      reviews.find(item => item.id === 'wide').director = '   ';
      reviews.find(item => item.id === 'cinema').categories = ['   '];
      reviews.find(item => item.id === 'portrait').director = '';
      reviews.find(item => item.id === 'portrait').categories = [];
      return __posterQA.analyze(await __posterQA.capture('landscape'));
    });
    await saveExport('landscape-optional-metadata.png');
    const [genresOnly, directorOnly, neither] = exported.cards;
    assert.ok(genresOnly.text.some(text => text.value === '剧情 / 科幻' && text.y === genresOnly.imageBottom + 94));
    assert.ok(!genresOnly.text.some(text => text.value.includes('克里斯托弗·诺兰')));
    assert.ok(directorOnly.text.some(text => text.value === '导演 王家卫' && text.y === directorOnly.imageBottom + 94));
    assert.equal(neither.text.length, 4);
    assert.equal(genresOnly.footerY, directorOnly.imageBottom + 124);
    assert.equal(neither.footerY, neither.imageBottom + 94);
    assert.ok(neither.bottom - neither.y < genresOnly.bottom - genresOnly.y, 'Omitting metadata must reduce the row height');
    assert.ok(exported.text.every(text => !['导演', '类型', '未填写', '未分类', '观看日期未填写'].includes(text.value)));
    for (const card of exported.cards) assertCardTextFits(card.text, card.x, card.imageBottom, card.bottom);
    assert.equal(await page(() => __qa.saves), 0);
  });

  await check('all six rating states draw the correct orange stars and make an unrated film explicit', async () => {
    await fixture();
    const exported = await page(async () => {
      reviews = Array.from({length: 6}, (_, rating) => ({
        id: `rating-${rating}`, title: `评分检验 ${rating} 分`, rating, director: '', categories: [], tags: [],
        date: '2026-10-23', image: null, createdAt: 6 - rating, updatedAt: 6 - rating
      }));
      renderAll(); openGallery(); setGalleryMode('wall');
      return __posterQA.analyze(await __posterQA.capture('landscape'));
    });
    await saveExport('landscape-rating-states.png');
    assert.equal(exported.cards.length, 6);
    assert.equal(exported.paths.filter(path => path.mode === 'fill').length, 15, 'Filled stars total 1+2+3+4+5');
    assert.equal(exported.paths.filter(path => path.mode === 'stroke').length, 25, 'Every rated film has five star positions');
    for (let rating = 0; rating < exported.cards.length; rating++) {
      const card = exported.cards[rating];
      assert.ok(card.text.some(text => text.value === `评分检验 ${rating} 分`));
      assertRating(card, rating);
      assertCardTextFits(card.text, card.x, card.imageBottom, card.bottom);
    }
    assert.equal(await page(() => __qa.saves), 0);
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
