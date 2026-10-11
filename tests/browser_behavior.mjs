// Interface behaviour regression with synthetic in-memory records.
// Windows: node tests/browser_behavior.mjs
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
import {launchBrowser} from './browser_harness.mjs';

const context = {location: '上海 IMAX', companions: '大学室友', mood: '期待', lifeStage: '刚工作', impact: '开始想时间',
  favoriteScene: '看录像', favoriteQuote: '不要温和地走进那个良夜', recommendTo: '所有人', watchAgain: true};
const sample = () => [
  {id: 'a', title: '星际穿越', director: '诺兰', date: '2020-10-11', rating: 5, tags: ['时间'], categories: ['科幻', '冒险'],
    comment: '爱可以穿越时间。', createdAt: 1700000000000, updatedAt: 1700000000000, personalContext: context},
  {id: 'b', title: '还没评分', director: '', date: '2024-05-01', rating: 0, tags: [], comment: '',
    createdAt: 1700000001000, updatedAt: 1700000001000},
  {id: 'c', title: '寄生虫', director: '奉俊昊', date: '2021-02-13', rating: 4, tags: ['阶层'], categories: ['剧情'],
    comment: '气味是藏不住的。', createdAt: 1700000002000, updatedAt: 1700000002000,
    rewatches: [{watchedAt: '2022-03-05T19:00', feeling: '第二遍', rating: 3, createdAt: 1700000003000}]}
];
const phone = {width: 375, height: 812, mobile: true};
const browser = await launchBrowser('browser-behavior');
const {page, loadApp} = browser;
const results = [];

async function check(name, run) {
  try {
    await run();
    results.push({name, passed: true});
  } catch (error) {
    const lines = String(error.message || error).split('\n').map(line => line.trim()).filter(Boolean);
    results.push({name, passed: false, error: lines.slice(0, 16).join(' | ')});
  }
}

try {
  await check('personal context fields belong to the review form, not the rewatch form', async () => {
    await loadApp({reviews: sample()});
    const state = await page(() => ({
      inReviewForm: document.getElementById('movieForm').contains(document.getElementById('contextLocation')),
      inRewatchForm: document.getElementById('rewatchForm').contains(document.getElementById('contextLocation'))}));
    assert.deepEqual(state, {inReviewForm: true, inRewatchForm: false});
  });

  await check('a new review does not inherit the previously edited personal context', async () => {
    await loadApp({reviews: sample()});
    const created = await page(async () => {
      openModal('a'); closeModal(); openModal();
      document.getElementById('movieTitle').value = '新影评';
      document.getElementById('movieForm').requestSubmit();
      await new Promise(resolve => setTimeout(resolve, 300));
      const review = reviews.find(item => item.title === '新影评');
      return {saved: !!review, personalContext: review?.personalContext ?? null};
    });
    assert.deepEqual(created, {saved: true, personalContext: null});
  });

  await check('personal context typed into the review form is saved with the review', async () => {
    await loadApp({reviews: []});
    const saved = await page(async () => {
      openModal();
      document.getElementById('movieTitle').value = '带背景的影评';
      document.getElementById('contextLocation').value = '家里投影';
      document.getElementById('contextWatchAgain').value = 'yes';
      document.getElementById('movieForm').requestSubmit();
      await new Promise(resolve => setTimeout(resolve, 300));
      return reviews.find(item => item.title === '带背景的影评')?.personalContext ?? null;
    });
    assert.equal(saved?.location, '家里投影');
    assert.equal(saved?.watchAgain, true);
  });

  await check('taste profile and annual report bars are drawn', async () => {
    await loadApp({reviews: sample()});
    const bars = await page(() => {
      const size = selector => { const rect = document.querySelector(selector).getBoundingClientRect(); return [Math.round(rect.width), Math.round(rect.height)]; };
      openTasteProfile();
      const taste = size('#tasteContent .taste-bar-fill');
      closeTasteProfile(); openAnnualReport();
      const months = [...document.querySelectorAll('#annualContent .taste-bar-fill')].map(fill => Math.round(fill.getBoundingClientRect().width));
      closeAnnualReport();
      return {taste, widestMonth: Math.max(...months)};
    });
    assert.ok(bars.taste[0] > 20 && bars.taste[1] >= 6, JSON.stringify(bars));
    assert.ok(bars.widestMonth > 20, JSON.stringify(bars));
  });

  await check('home average rating ignores unrated reviews', async () => {
    await loadApp({reviews: sample()});
    const average = await page(() => document.querySelectorAll('#statsBar .stat-value')[1].textContent.trim());
    assert.match(average, /4\.5$/);
    await loadApp({reviews: sample().filter(item => item.id === 'b')});
    const empty = await page(() => document.querySelectorAll('#statsBar .stat-value')[1].textContent.trim());
    assert.match(empty, /—$/);
  });

  await check('rating stars are buttons that fit a phone-width form and can be cleared', async () => {
    await loadApp({reviews: sample(), ...phone});
    const stars = await page(async () => {
      openModal('a');
      await new Promise(resolve => setTimeout(resolve, 350));
      const modal = document.querySelector('#modalOverlay .modal'), buttons = [...document.querySelectorAll('#starSelector button')];
      const limit = modal.getBoundingClientRect().left + modal.clientWidth;
      const result = {count: buttons.length, overflowing: buttons.filter(button => button.getBoundingClientRect().right > limit + 1).length,
        pressed: buttons.filter(button => button.getAttribute('aria-pressed') === 'true').length};
      buttons[2]?.click(); result.afterThird = document.getElementById('movieRating').value;
      buttons[2]?.click(); result.afterSecondClick = document.getElementById('movieRating').value;
      closeModal();
      return result;
    });
    assert.deepEqual(stars, {count: 5, overflowing: 0, pressed: 5, afterThird: '3', afterSecondClick: '0'});
  });

  await check('curation wall fits a phone-width screen and keeps the graph undistorted', async () => {
    await loadApp({reviews: sample(), ...phone});
    const gallery = await page(async () => {
      openGallery();
      await new Promise(resolve => setTimeout(resolve, 500));
      knowledgeGraphState.progress = 1; drawKnowledgeGraph();
      const canvas = document.getElementById('knowledgeGraphCanvas'), bounds = canvas.getBoundingClientRect();
      const node = knowledgeGraphState.nodes.find(item => item.kind === 'relation'), position = knowledgeGraphPosition(node);
      const hit = knowledgeGraphHit(position.x * knowledgeGraphState.zoom + knowledgeGraphState.panX, position.y * knowledgeGraphState.zoom + knowledgeGraphState.panY);
      const result = {headerHeight: Math.round(document.querySelector('.gallery-header').getBoundingClientRect().height),
        titleWidth: Math.round(document.getElementById('galleryTitle').getBoundingClientRect().width),
        exportRight: Math.round(document.getElementById('galleryExportButton').getBoundingClientRect().right),
        viewport: document.documentElement.clientWidth, logicalWidth: knowledgeGraphState.width, cssWidth: Math.round(bounds.width),
        nodeInsideCanvas: position.x >= 0 && position.x <= bounds.width, hitsOwnNode: hit?.id === node.id,
        pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth};
      closeGallery();
      return result;
    });
    assert.ok(gallery.headerHeight <= 230, JSON.stringify(gallery));
    assert.ok(gallery.titleWidth >= 100, JSON.stringify(gallery));
    assert.ok(gallery.exportRight <= gallery.viewport, JSON.stringify(gallery));
    assert.ok(Math.abs(gallery.logicalWidth - gallery.cssWidth) <= 1, JSON.stringify(gallery));
    assert.equal(gallery.nodeInsideCanvas && gallery.hitsOwnNode && !gallery.pageOverflow, true, JSON.stringify(gallery));
  });

  await check('a very long tag does not make the phone layout overflow', async () => {
    const longTag = 'An exceptionally long supplementary tag used for layout checks 01';
    await loadApp({reviews: [{...sample()[0], tags: [longTag]}], ...phone});
    const layout = await page(() => ({viewport: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth,
      selectRight: Math.round(document.getElementById('filterTag').getBoundingClientRect().right)}));
    assert.ok(layout.scrollWidth <= layout.viewport && layout.selectRight <= layout.viewport, JSON.stringify(layout));
  });

  await check('closing an edited review asks before discarding, an untouched one closes at once', async () => {
    await loadApp({reviews: sample()});
    const flow = await page(async () => {
      const wait = () => new Promise(resolve => setTimeout(resolve, 80));
      const active = id => document.getElementById(id)?.classList.contains('active') ?? false;
      const escape = () => document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
      const result = {};
      openModal(); await wait(); escape(); await wait();
      result.untouchedClosed = !active('modalOverlay');
      openModal(); await wait();
      const title = document.getElementById('movieTitle');
      title.value = '写到一半'; title.dispatchEvent(new Event('input', {bubbles: true}));
      escape(); await wait();
      result.keptOpenOnEscape = active('modalOverlay'); result.askedOnEscape = active('confirmOverlay');
      document.querySelector('#confirmOverlay [data-confirm-cancel]')?.click(); await wait();
      result.keptAfterCancel = active('modalOverlay') && title.value === '写到一半';
      document.getElementById('modalOverlay').dispatchEvent(new MouseEvent('click', {bubbles: true})); await wait();
      result.askedOnBackdrop = active('modalOverlay') && active('confirmOverlay');
      document.querySelector('#confirmOverlay [data-confirm-accept]')?.click(); await wait();
      result.closedAfterAccept = !active('modalOverlay') && !active('confirmOverlay');
      return result;
    });
    assert.deepEqual(flow, {untouchedClosed: true, keptOpenOnEscape: true, askedOnEscape: true, keptAfterCancel: true,
      askedOnBackdrop: true, closedAfterAccept: true});
  });

  await check('dialogs take focus, hold it, stop background scrolling and give focus back', async () => {
    await loadApp({reviews: sample()});
    const focus = await page(async () => {
      const wait = () => new Promise(resolve => setTimeout(resolve, 120));
      const overlay = document.getElementById('detailOverlay'), card = document.querySelector('.movie-card');
      card.focus(); openDetail(card.dataset.reviewId); await wait();
      const result = {movedInside: overlay.contains(document.activeElement), backgroundLocked: getComputedStyle(document.body).overflow === 'hidden'};
      const focusable = [...overlay.querySelectorAll('button:not(:disabled)')].filter(item => item.getClientRects().length);
      focusable.at(-1).focus();
      document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key: 'Tab', bubbles: true, cancelable: true}));
      result.tabStaysInside = overlay.contains(document.activeElement);
      closeDetail(); await wait();
      result.returnedToCard = document.activeElement === card;
      result.backgroundReleased = getComputedStyle(document.body).overflow !== 'hidden';
      return result;
    });
    assert.deepEqual(focus, {movedInside: true, backgroundLocked: true, tabStaysInside: true, returnedToCard: true, backgroundReleased: true});
  });

  await check('form labels are associated with their controls', async () => {
    await loadApp({reviews: sample()});
    const unlabelled = await page(() => [...document.querySelectorAll('#movieForm label.form-label, #rewatchForm label.form-label')]
      .filter(label => !label.control && !(label.id && document.querySelector(`[aria-labelledby~="${label.id}"]`)))
      .map(label => label.textContent.trim().slice(0, 12)));
    assert.deepEqual(unlabelled, []);
  });

  await check('error messages stay readable and are announced', async () => {
    await loadApp({reviews: sample()});
    const toast = await page(async () => {
      const node = document.getElementById('toast');
      showToast('已保存');
      const successRole = node.getAttribute('role');
      showToast('其他设备已修改档案。当前编辑内容已保留，请保存时检查冲突。', 'error');
      await new Promise(resolve => setTimeout(resolve, 3200));
      return {successRole, errorRole: node.getAttribute('role'), stillVisible: node.classList.contains('show')};
    });
    assert.deepEqual(toast, {successRole: 'status', errorRole: 'alert', stillVisible: true});
  });

  await check('destructive actions use the in-app confirmation instead of the browser dialog', async () => {
    await loadApp({reviews: sample(), importReviews: [sample()[0]]});
    const flow = await page(async () => {
      const wait = () => new Promise(resolve => setTimeout(resolve, 120));
      const active = () => document.getElementById('confirmOverlay')?.classList.contains('active') ?? false;
      const answer = async accept => { document.querySelector(`#confirmOverlay [data-confirm-${accept ? 'accept' : 'cancel'}]`)?.click(); await wait(); };
      const result = {};
      const cancelled = deleteReview('c'); await wait();
      result.deleteAsks = active(); await answer(false); await cancelled;
      result.keptAfterCancel = reviews.some(item => item.id === 'c');
      const removed = deleteReview('c'); await wait(); await answer(true); await removed;
      result.deletedAfterAccept = !reviews.some(item => item.id === 'c');
      const imported = handleImport(); await wait();
      result.importAsks = active(); await answer(true); await imported;
      result.importApplied = reviews.length === 1 && reviews[0].id === 'a';
      result.nativeDialogs = window.__qa.nativeConfirms;
      return result;
    });
    assert.deepEqual(flow, {deleteAsks: true, keptAfterCancel: true, deletedAfterAccept: true, importAsks: true,
      importApplied: true, nativeDialogs: 0});
  });

  await check('cards show the film categories', async () => {
    await loadApp({reviews: sample()});
    const tags = await page(() => [...document.querySelector('[data-review-id="a"] .card-tags').children].map(tag => tag.textContent));
    assert.deepEqual(tags, ['科幻', '冒险', '时间']);
  });

  await check('phone layout keeps the list near the top and controls large enough', async () => {
    await loadApp({reviews: sample(), ...phone});
    const layout = await page(async () => {
      const today = archiveNowParts(), pad = value => String(value).padStart(2, '0');
      reviews = [1, 2, 3].map(offset => ({id: 'm' + offset, title: '往年今日 ' + offset, director: '', rating: 5, tags: [], comment: '当年的感受。',
        date: `${today.year - offset}-${pad(today.month)}-${pad(today.day)}`, createdAt: 1, updatedAt: 1}));
      renderAll();
      await new Promise(resolve => setTimeout(resolve, 200));
      const height = selector => Math.round(document.querySelector(selector).getBoundingClientRect().height);
      const fontSize = selector => parseFloat(getComputedStyle(document.querySelector(selector)).fontSize);
      openModal();
      const formFont = fontSize('#movieTitle');
      closeModal();
      return {memories: document.querySelectorAll('#memoryList .memory-card').length, viewportHeight: window.innerHeight,
        gridTop: Math.round(document.getElementById('movieGrid').getBoundingClientRect().top + window.scrollY),
        statsHeight: height('#statsBar'), smallestHeaderButton: Math.min(...[...document.querySelectorAll('.header-actions .btn')].map(button => Math.round(button.getBoundingClientRect().height))),
        searchFont: fontSize('#searchInput'), filterFont: fontSize('#filterRating'), formFont,
        pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth};
    });
    assert.equal(layout.memories, 3, JSON.stringify(layout));
    assert.ok(layout.gridTop < layout.viewportHeight, JSON.stringify(layout));
    assert.ok(layout.statsHeight <= 110 && layout.smallestHeaderButton >= 40, JSON.stringify(layout));
    assert.ok(Math.min(layout.searchFont, layout.filterFont, layout.formFont) >= 16 && !layout.pageOverflow, JSON.stringify(layout));
  });

  await check('review dialog keeps close and save controls reachable on a phone', async () => {
    await loadApp({reviews: sample(), ...phone});
    const controls = await page(async () => {
      openModal('a');
      await new Promise(resolve => setTimeout(resolve, 350));
      const modal = document.querySelector('#modalOverlay .modal'), close = modal.querySelector('.modal-close');
      const inView = element => { const rect = element.getBoundingClientRect(); return rect.top >= 0 && rect.bottom <= window.innerHeight && rect.width > 0; };
      const result = {hasClose: !!close, scrolls: modal.scrollHeight > modal.clientHeight + 40,
        saveVisibleAtTop: inView(document.getElementById('saveReviewButton'))};
      modal.scrollTop = modal.scrollHeight;
      await new Promise(resolve => setTimeout(resolve, 60));
      result.closeVisibleAtBottom = !!close && inView(close);
      closeModal();
      return result;
    });
    assert.deepEqual(controls, {hasClose: true, scrolls: true, saveVisibleAtTop: true, closeVisibleAtBottom: true});
  });

  await check('home text meets the contrast floor and minimum size', async () => {
    await loadApp({reviews: sample()});
    const audit = await page(() => {
      const parse = color => { const parts = color.match(/rgba?\(([^)]+)\)/)[1].split(',').map(Number); return {r: parts[0], g: parts[1], b: parts[2], a: parts[3] ?? 1}; };
      const luminance = ({r, g, b}) => { const channel = value => { value /= 255; return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4; }; return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b); };
      const blend = (top, under) => ({r: top.r * top.a + under.r * (1 - top.a), g: top.g * top.a + under.g * (1 - top.a), b: top.b * top.a + under.b * (1 - top.a), a: 1});
      const backdrop = element => { const layers = []; for (let node = element; node && node.nodeType === 1; node = node.parentElement) { const color = parse(getComputedStyle(node).backgroundColor); if (color.a > 0) { layers.push(color); if (color.a === 1) break; } } let base = {r: 13, g: 11, b: 12, a: 1}; for (const layer of layers.reverse()) base = blend(layer, base); return base; };
      const decorative = '.card-fallback-mark, .star-empty, .search-icon';
      const problems = [];
      for (const element of document.querySelectorAll('.app-wrapper *')) {
        if (!element.getClientRects().length || element.closest(decorative) || ![...element.childNodes].some(node => node.nodeType === 3 && node.textContent.trim())) continue;
        const style = getComputedStyle(element), background = backdrop(element), foreground = blend(parse(style.color), background);
        const [light, dark] = [luminance(foreground), luminance(background)].sort((x, y) => y - x), contrast = (light + 0.05) / (dark + 0.05);
        if (contrast < 4.5 || parseFloat(style.fontSize) < 12) problems.push(`${element.className || element.tagName}: ${contrast.toFixed(2)}:1 at ${parseFloat(style.fontSize).toFixed(1)}px`);
      }
      return [...new Set(problems)];
    });
    assert.deepEqual(audit, []);
  });

  await check('page declares an icon and a theme colour', async () => {
    await loadApp({reviews: []});
    const head = await page(() => ({icon: !!document.querySelector('link[rel~="icon"]'), theme: document.querySelector('meta[name="theme-color"]')?.content ?? null}));
    assert.deepEqual(head, {icon: true, theme: '#0d0b0c'});
  });
} finally {
  browser.close();
}

await writeFile(path.join(browser.output, 'results.json'), JSON.stringify(results, null, 2));
const failed = results.filter(result => !result.passed);
for (const result of results) console.log(`${result.passed ? 'ok  ' : 'FAIL'} ${result.name}${result.passed ? '' : '\n     ' + result.error}`);
console.log(`Browser behaviour: ${results.length - failed.length}/${results.length} passed; ${browser.output}`);
if (failed.length) process.exit(1);
