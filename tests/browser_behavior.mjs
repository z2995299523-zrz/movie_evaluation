// Interface behaviour regression with synthetic in-memory records.
// Windows: node tests/browser_behavior.mjs
import assert from 'node:assert/strict';
import {readFile, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {launchBrowser, root} from './browser_harness.mjs';

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
const hex = index => index.toString(16).padStart(64, 'a');
const posterArchive = count => Array.from({length: count}, (_, index) => ({id: 'p' + index, title: '海报电影 ' + (index + 1), director: '导演' + index % 7,
  date: `20${10 + index % 15}-0${index % 9 + 1}-1${index % 9}`, rating: index % 5 + 1, tags: ['标签' + index % 4], comment: '合成记录',
  categories: index % 3 ? ['分类' + index % 6] : ['分类' + index % 6, '分类' + (index + 2) % 6], createdAt: 1700000000000 + index, updatedAt: 1700000000000 + index,
  ...(index % 6 === 5 ? {} : {image: {path: `media/${hex(index)}.jpg`, name: `p${index}.jpg`, mime: 'image/jpeg', size: 1000}})}));
const browser = await launchBrowser('browser-behavior');
const {page, loadApp} = browser;
const results = [];
const accountStyles = await readFile(path.join(root, 'web/account.css'), 'utf8');

// Match the production account CSS and header structure without logging into a real account.
async function installWebLayout() {
  await page(css => {
    window.MOVIE_REVIEW_WEB = true;
    window.MovieReviewWeb = {...window.pywebview.api, loadImageObjectUrl: async (image, onProgress, isCurrent) => {
      const info = await window.pywebview.api.get_review_image_info(image.path);
      if (!info.ok) throw new Error(info.error);
      const chunk = await window.pywebview.api.get_review_image_chunk(image.path);
      if (!isCurrent()) return '';
      onProgress(100);
      return URL.createObjectURL(new Blob([base64ToBytes(chunk.data)], {type: info.mime}));
    }};
    document.body.classList.add('web-mode');
    const styles = document.createElement('style'); styles.textContent = css; document.head.append(styles);
    const status = document.getElementById('storageStatus'); status.className = 'storage-status file';
    status.innerHTML = '<span class="storage-dot"></span><span class="storage-status-text">个人电影档案 · 仅你可见</span>';
    const account = document.createElement('button'); account.type = 'button'; account.id = 'webAccountButton';
    account.className = 'web-account-trigger'; account.setAttribute('aria-label', '打开测试账户菜单');
    account.setAttribute('aria-haspopup', 'menu'); account.setAttribute('aria-expanded', 'false');
    account.innerHTML = '<span class="web-avatar">测</span><span class="web-account-name">测试账户</span><svg class="web-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
    document.querySelector('.app-header').insertBefore(account, document.querySelector('.header-actions'));
  }, accountStyles);
}

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
  await check('latest viewing sorts by calendar day then creation time including milliseconds', async () => {
    const base = {...sample()[1], date: '2026-10-18'};
    await loadApp({reviews: [
      {...base, id: 'earlier', title: '同日较早新增', createdAt: 1700000001000},
      {...base, id: 'later', title: '同日较晚新增', createdAt: 1700000002000},
      {...base, id: 'previous-day', date: '2026-10-17', createdAt: 1700000003000},
      {...base, id: 'millisecond-later', createdAt: 1700000002001},
      {...base, id: 'rewatched', date: '2020-01-01', createdAt: 1700000000000,
        rewatches: [{watchedAt: '2026-10-18T23:59', feeling: '', createdAt: 1700000003000}]},
      {...base, id: 'next-day', date: '2026-10-19', createdAt: 1700000000000}
    ]});
    const order = await page(() => {
      document.getElementById('sortBy').value = 'date-desc';
      render();
      return getVisibleReviews().map(review => review.id);
    });
    assert.deepEqual(order, ['next-day', 'millisecond-later', 'later', 'earlier', 'rewatched', 'previous-day']);
  });

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
      settleKnowledgeGraph();
      const canvas = document.getElementById('knowledgeGraphCanvas'), bounds = canvas.getBoundingClientRect();
      const node = knowledgeGraphState.nodes.find(item => item.kind === 'relation');
      const position = {x: node.x * knowledgeGraphState.zoom + knowledgeGraphState.panX, y: node.y * knowledgeGraphState.zoom + knowledgeGraphState.panY};
      const hit = knowledgeGraphHit(position.x, position.y);
      const result = {headerHeight: Math.round(document.querySelector('.gallery-header').getBoundingClientRect().height),
        titleWidth: Math.round(document.getElementById('galleryTitle').getBoundingClientRect().width),
        exportRight: Math.round(document.getElementById('galleryExportButton').getBoundingClientRect().right),
        viewport: document.documentElement.clientWidth, logicalWidth: knowledgeGraphState.width, cssWidth: Math.round(bounds.width),
        nodeInsideCanvas: position.x >= 0 && position.x <= bounds.width && position.y >= 0 && position.y <= bounds.height, hitsOwnNode: hit?.id === node.id,
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
    const layout = await page(() => {
      document.getElementById('mobileFilterToggle').click();
      const select = document.getElementById('filterTag').getBoundingClientRect();
      return {viewport: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth,
        selectVisible: select.width > 0 && select.height > 0, selectRight: Math.round(select.right)};
    });
    assert.ok(layout.selectVisible && layout.scrollWidth <= layout.viewport && layout.selectRight <= layout.viewport, JSON.stringify(layout));
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

  await check('poster selection uses Monday to Sunday including rewatches and calendar-year boundaries', async () => {
    const base = {...sample()[1], date: '2020-01-01'};
    await loadApp({reviews: [
      {...base, id: 'monday', date: '2026-10-19'},
      {...base, id: 'sunday', date: '2026-10-25'},
      {...base, id: 'previous-sunday', date: '2026-10-18'},
      {...base, id: 'next-monday', date: '2026-10-26'},
      {...base, id: 'rewatched-this-week', rewatches: [
        {watchedAt: '2026-10-19T00:00', feeling: '', createdAt: 1},
        {watchedAt: '2026-10-25T23:59', feeling: '', createdAt: 2},
        {watchedAt: '2026-11-01T20:00', feeling: '', createdAt: 3}]},
      {...base, id: 'old'}
    ]});
    const week = await page(() => {
      openGallery(); setGalleryMode('wall');
      const result = {range: getGalleryWeekRange(new Date(2026, 9, 22, 12)),
        sundayRange: getGalleryWeekRange(new Date(2026, 9, 25, 23, 59)),
        yearBoundary: getGalleryWeekRange(new Date(2027, 0, 1, 12))};
      selectGalleryWeek(new Date(2026, 9, 22, 12));
      result.ids = getGalleryExportItems().map(review => review.id).sort();
      document.getElementById('searchInput').value = 'no matching movie';
      render(); renderGallery(getVisibleReviews());
      selectGalleryWeek(new Date(2026, 9, 22, 12));
      result.empty = getGalleryExportItems().length;
      result.emptyDisabled = document.getElementById('galleryExportButton').disabled;
      const previousWeb = window.MOVIE_REVIEW_WEB;
      try {
        window.MOVIE_REVIEW_WEB = true;
        result.shanghaiMonday = getGalleryWeekRange(new Date('2026-10-18T16:01:00Z'));
        result.shanghaiSunday = getGalleryWeekRange(new Date('2026-10-18T15:59:00Z'));
      } finally { window.MOVIE_REVIEW_WEB = previousWeb; }
      result.saves = window.__qa.saves;
      return result;
    });
    assert.deepEqual(week, {range: {start: '2026-10-19', end: '2026-10-25'},
      sundayRange: {start: '2026-10-19', end: '2026-10-25'}, yearBoundary: {start: '2026-12-28', end: '2027-01-03'},
      ids: ['monday', 'rewatched-this-week', 'sunday'], empty: 0, emptyDisabled: true,
      shanghaiMonday: {start: '2026-10-19', end: '2026-10-25'}, shanghaiSunday: {start: '2026-10-12', end: '2026-10-18'}, saves: 0});
  });

  await check('manual poster selection supports keyboard, preserves cards and resets when the wall closes', async () => {
    await loadApp({reviews: posterArchive(24).map(({image, ...review}) => review), ...phone});
    const selection = await page(async () => {
      const before = JSON.stringify(reviews);
      openGallery(); setGalleryMode('wall');
      await new Promise(resolve => requestAnimationFrame(resolve));
      const stage = document.getElementById('galleryStage');
      const result = {defaultCount: getGalleryExportItems().length};
      toggleGallerySelection();
      result.emptyCount = getGalleryExportItems().length;
      result.emptyDisabled = document.getElementById('galleryExportButton').disabled;
      const cards = [...document.querySelectorAll('#galleryWall .gallery-card')], card = cards[10];
      card.focus(); stage.scrollTop = 250;
      const scrollTop = stage.scrollTop;
      card.click();
      result.clicked = {role: card.getAttribute('role'), checked: card.getAttribute('aria-checked'), count: getGalleryExportItems().length,
        noDetail: !document.getElementById('detailOverlay').classList.contains('active'),
        sameCard: document.querySelector(`[data-gallery-review-id="${card.dataset.galleryReviewId}"]`) === card,
        sameScroll: stage.scrollTop === scrollTop};
      const key = value => card.dispatchEvent(new KeyboardEvent('keydown', {key: value, bubbles: true, cancelable: true}));
      key(' '); result.afterSpace = getGalleryExportItems().length;
      key('Enter'); result.afterEnter = getGalleryExportItems().length;
      const selected = getGalleryExportItems().map(review => review.id);
      setGalleryMode('relation'); setGalleryMode('wall');
      result.preservedAcrossModes = JSON.stringify(getGalleryExportItems().map(review => review.id)) === JSON.stringify(selected);
      setGallerySelection('all'); result.allCount = getGalleryExportItems().length;
      setGallerySelection('none'); result.noneCount = getGalleryExportItems().length;
      toggleGallerySelection();
      const detailCard = document.querySelector('#galleryWall .gallery-card');
      detailCard.focus(); detailCard.click();
      result.detailWorksAfterExit = document.getElementById('detailOverlay').classList.contains('active');
      closeDetail();
      result.detailReturnsToWall = document.getElementById('galleryOverlay').classList.contains('active') && document.activeElement === detailCard;
      toggleGallerySelection(); closeGallery(); openGallery();
      result.reopenedCount = getGalleryExportItems().length;
      result.reopenedRole = document.querySelector('#galleryWall .gallery-card').getAttribute('role');
      result.unchanged = JSON.stringify(reviews) === before;
      result.saves = window.__qa.saves;
      return result;
    });
    assert.deepEqual(selection, {defaultCount: 24, emptyCount: 0, emptyDisabled: true,
      clicked: {role: 'checkbox', checked: 'true', count: 1, noDetail: true, sameCard: true, sameScroll: true},
      afterSpace: 0, afterEnter: 1, preservedAcrossModes: true, allCount: 24, noneCount: 0,
      detailWorksAfterExit: true, detailReturnsToWall: true, reopenedCount: 24, reopenedRole: 'button', unchanged: true, saves: 0});
  });

  await check('random poster selection stays unique, respects filters and rejects invalid counts', async () => {
    await loadApp({reviews: posterArchive(20).map(({image, ...review}) => review)});
    const random = await page(() => {
      const before = JSON.stringify(reviews);
      document.getElementById('filterRating').value = '5'; render();
      openGallery(); setGalleryMode('wall');
      const input = document.getElementById('galleryRandomCount');
      input.value = '3'; selectGalleryRandom();
      const picked = getGalleryExportItems(), candidates = getVisibleReviews();
      const result = {count: picked.length, unique: new Set(picked.map(review => review.id)).size,
        withinFilter: picked.every(review => review.rating === 5),
        stableOrder: JSON.stringify(picked.map(review => review.id)) === JSON.stringify(candidates.filter(review => picked.includes(review)).map(review => review.id))};
      const selected = JSON.stringify(picked.map(review => review.id));
      result.invalidKeepsSelection = ['', '0', '-2', '1.5'].every(value => {
        input.value = value; selectGalleryRandom();
        return JSON.stringify(getGalleryExportItems().map(review => review.id)) === selected;
      });
      input.value = '999'; selectGalleryRandom(); result.overCount = getGalleryExportItems().length;
      const card = document.querySelector('#galleryWall .gallery-card'); card.click();
      result.manualAdjustment = getGalleryExportItems().length;
      result.unchanged = JSON.stringify(reviews) === before; result.saves = window.__qa.saves;
      return result;
    });
    assert.deepEqual(random, {count: 3, unique: 3, withinFilter: true, stableOrder: true,
      invalidKeepsSelection: true, overCount: 4, manualAdjustment: 3, unchanged: true, saves: 0});
  });

  await check('poster PNG contains only the selected snapshot and decodes as an actual image', async () => {
    await loadApp({reviews: posterArchive(4), stills: true});
    const exported = await page(async () => {
      openGallery(); setGalleryMode('wall'); toggleGallerySelection();
      const cards = [...document.querySelectorAll('#galleryWall .gallery-card')];
      cards[0].click(); cards[2].click();
      const expected = getGalleryExportItems().map(review => review.title), titles = new Set(reviews.map(review => review.title));
      const drawn = [], calls = [], originalText = CanvasRenderingContext2D.prototype.fillText;
      const originalExport = window.pywebview.api.export_graph_image;
      let changedDuringExport = false;
      CanvasRenderingContext2D.prototype.fillText = function (value, ...args) {
        if (titles.has(String(value))) {
          drawn.push(String(value));
          if (!changedDuringExport) { changedDuringExport = true; setGallerySelection('none'); }
        }
        return originalText.call(this, value, ...args);
      };
      window.pywebview.api.export_graph_image = async (data, name) => { calls.push({data, name}); return {ok: true}; };
      try {
        await exportGallery();
        const capture = calls[0], image = new Image(); image.src = capture?.data || ''; await image.decode();
        const result = {expected, drawn, calls: calls.length, png: capture.data.startsWith('data:image/png;base64,'),
          width: image.naturalWidth, height: image.naturalHeight, changedDuringExport,
          stillEmpty: getGalleryExportItems().length === 0, disabledAfterExport: document.getElementById('galleryExportButton').disabled};
        await exportGallery(); await exportPosterWall();
        result.emptyDoesNotExport = calls.length === 1;
        setGallerySelection('all'); result.enabledAfterReselect = !document.getElementById('galleryExportButton').disabled;
        result.saves = window.__qa.saves;
        return result;
      } finally {
        CanvasRenderingContext2D.prototype.fillText = originalText;
        window.pywebview.api.export_graph_image = originalExport;
      }
    });
    assert.deepEqual(exported.drawn, exported.expected);
    assert.equal(exported.calls, 1);
    assert.ok(exported.png && exported.width > 0 && exported.height > 0, JSON.stringify(exported));
    assert.ok(exported.changedDuringExport && exported.stillEmpty && exported.disabledAfterExport && exported.emptyDoesNotExport && exported.enabledAfterReselect, JSON.stringify(exported));
    assert.equal(exported.saves, 0);
  });

  await check('poster selection controls stay reachable on a phone and leave space for posters', async () => {
    await loadApp({reviews: posterArchive(12).map(({image, ...review}) => review), ...phone});
    const layout = await page(async () => {
      openGallery(); setGalleryMode('wall'); toggleGallerySelection();
      await new Promise(resolve => requestAnimationFrame(resolve));
      const overlay = document.getElementById('galleryOverlay'), stage = document.getElementById('galleryStage');
      const controls = [...overlay.querySelectorAll('button, input, select')].filter(control => control.getClientRects().length > 0);
      const outside = controls.filter(control => {
        const rect = control.getBoundingClientRect();
        return rect.left < -1 || rect.right > window.innerWidth + 1 || rect.top < -1 || rect.bottom > window.innerHeight + 1;
      }).map(control => control.id || control.textContent.trim());
      const result = {outside, stageHeight: stage.getBoundingClientRect().height,
        horizontalOverflow: overlay.scrollWidth > overlay.clientWidth + 1 || stage.scrollWidth > stage.clientWidth + 1,
        numberFont: parseFloat(getComputedStyle(document.getElementById('galleryRandomCount')).fontSize)};
      setGalleryMode('relation');
      result.randomHiddenInRelation = document.getElementById('galleryRandomCount').getClientRects().length === 0;
      return result;
    });
    assert.deepEqual(layout.outside, []);
    assert.ok(layout.stageHeight >= 200 && !layout.horizontalOverflow && layout.numberFont >= 16 && layout.randomHiddenInRelation, JSON.stringify(layout));
  });

  await check('scrolling the poster wall back and forth does not reload posters', async () => {
    await loadApp({reviews: posterArchive(60), stills: true, ...phone});
    const wall = await page(async () => {
      const stage = document.getElementById('galleryStage');
      const idle = async () => { for (let waited = 0; waited < 20000; waited += 100) { await new Promise(resolve => setTimeout(resolve, 100)); if (cardImageInFlight === 0 && cardImageQueue.length === 0) return; } };
      const sweep = async () => { const max = stage.scrollHeight - stage.clientHeight;
        for (const direction of [1, -1]) for (let step = 0; step <= 30; step++) { stage.scrollTop = direction > 0 ? max * step / 30 : max * (1 - step / 30); await new Promise(resolve => requestAnimationFrame(resolve)); await idle(); } };
      openGallery(); setGalleryMode('wall'); await idle();
      await sweep();
      const loadedOnce = window.__qa.imageReads;
      await sweep(); await sweep();
      const images = [...document.querySelectorAll('.gallery-card-image')];
      return {posters: images.length, readsForFirstPass: loadedOnce, extraReads: window.__qa.imageReads - loadedOnce,
        widest: Math.max(...images.map(image => image.naturalWidth)), ready: document.querySelectorAll('.gallery-card.image-ready').length};
    });
    assert.equal(wall.posters, 50, JSON.stringify(wall));
    assert.deepEqual({readsForFirstPass: wall.readsForFirstPass, extraReads: wall.extraReads, ready: wall.ready}, {readsForFirstPass: 50, extraReads: 0, ready: 50}, JSON.stringify(wall));
    assert.ok(wall.widest > 0 && wall.widest <= 480, JSON.stringify(wall));
  });

  await check('poster wall avoids per-card blur and pauses hover effects while scrolling', async () => {
    await loadApp({reviews: posterArchive(24), stills: true});
    const effects = await page(async () => {
      openGallery(); setGalleryMode('wall');
      await new Promise(resolve => setTimeout(resolve, 300));
      const stage = document.getElementById('galleryStage'), wallElement = document.getElementById('galleryWall'), card = document.querySelector('.gallery-card');
      const result = {wallChip: getComputedStyle(document.querySelector('.gallery-card-rating')).backdropFilter,
        homeChip: getComputedStyle(document.querySelector('.card-rating-chip')).backdropFilter};
      stage.scrollTop = 40; stage.dispatchEvent(new Event('scroll'));
      result.pausedWhileScrolling = wallElement.classList.contains('is-scrolling') && getComputedStyle(card).pointerEvents === 'none';
      await new Promise(resolve => setTimeout(resolve, 450));
      result.restoredAfterwards = !wallElement.classList.contains('is-scrolling') && getComputedStyle(card).pointerEvents !== 'none';
      return result;
    });
    assert.deepEqual(effects, {wallChip: 'none', homeChip: 'none', pausedWhileScrolling: true, restoredAfterwards: true});
  });

  await check('a poster that fails to load once is retried', async () => {
    await loadApp({reviews: posterArchive(4), stills: true});
    const retry = await page(async () => {
      const original = window.pywebview.api.get_review_image_info, failed = new Set();
      for (let waited = 0; waited < 10000 && (cardImageInFlight || cardImageQueue.length); waited += 50) await new Promise(resolve => setTimeout(resolve, 50));
      window.pywebview.api.get_review_image_info = async reference => {
        if (!failed.has(reference)) { failed.add(reference); return {ok: false, error: '图片处理繁忙，请重试'}; }
        return original(reference);
      };
      // Start from a quiet, empty cache so every poster goes through the failing first read.
      for (let waited = 0; waited < 10000 && (cardImageInFlight || cardImageQueue.length); waited += 50) await new Promise(resolve => setTimeout(resolve, 50));
      cardImageCache.clear(); renderAll();
      openGallery(); setGalleryMode('wall');
      await new Promise(resolve => setTimeout(resolve, 3500));
      return {failedOnce: failed.size, ready: document.querySelectorAll('.gallery-card.image-ready').length,
        unavailable: document.querySelectorAll('.gallery-card.image-unavailable').length};
    });
    assert.deepEqual(retry, {failedOnce: 4, ready: 4, unavailable: 0});
  });

  await check('relation graph settles into a force layout without overlapping nodes', async () => {
    await loadApp({reviews: posterArchive(36), stills: true, width: 1440, height: 900});
    const layout = await page(async () => {
      openGallery();
      await new Promise(resolve => setTimeout(resolve, 200));
      settleKnowledgeGraph();
      const state = knowledgeGraphState, nodes = state.nodes, screen = node => ({x: node.x * state.zoom + state.panX, y: node.y * state.zoom + state.panY});
      let overlaps = 0;
      for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++)
        if (Math.hypot(nodes[i].x - nodes[j].x, nodes[i].y - nodes[j].y) < nodes[i].size + nodes[j].size - 1) overlaps++;
      const linked = new Set(state.edges.map(edge => edge.source + '>' + edge.target)), hubs = nodes.filter(node => node.kind === 'relation'), movies = nodes.filter(node => node.kind === 'movie');
      const distances = {linked: [], unlinked: []};
      for (const movie of movies) for (const hub of hubs) distances[linked.has(movie.id + '>' + hub.id) ? 'linked' : 'unlinked'].push(Math.hypot(movie.x - hub.x, movie.y - hub.y));
      const mean = values => values.reduce((sum, value) => sum + value, 0) / values.length;
      const outside = nodes.filter(node => { const point = screen(node); return point.x < 0 || point.y < 0 || point.x > state.view.w || point.y > state.view.h; }).length;
      const biggestHub = [...hubs].sort((a, b) => b.degree - a.degree)[0], smallestHub = [...hubs].sort((a, b) => a.degree - b.degree)[0];
      return {hubs: hubs.length, movies: movies.length, overlaps, outside, settled: state.alpha < 0.01,
        linkedCloser: mean(distances.linked) < mean(distances.unlinked) * 0.7, hubSizeFollowsDegree: biggestHub.degree === smallestHub.degree || biggestHub.size > smallestHub.size};
    });
    assert.deepEqual(layout, {hubs: 6, movies: 36, overlaps: 0, outside: 0, settled: true, linkedCloser: true, hubSizeFollowsDegree: true});
  });

  await check('relation graph responds to hover, click, drag, double click and view tools', async () => {
    await loadApp({reviews: posterArchive(18), stills: true, width: 1440, height: 900});
    const interaction = await page(async () => {
      const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
      openGallery(); await wait(200); settleKnowledgeGraph();
      const state = knowledgeGraphState, canvas = state.canvas, box = canvas.getBoundingClientRect();
      const client = node => ({clientX: box.left + node.x * state.zoom + state.panX, clientY: box.top + node.y * state.zoom + state.panY});
      const fire = (type, point, extra = {}) => canvas.dispatchEvent(new PointerEvent(type, {bubbles: true, cancelable: true, pointerId: 7, pointerType: 'mouse', button: 0, ...point, ...extra}));
      const hub = state.nodes.find(node => node.kind === 'relation'), movie = state.nodes.find(node => node.kind === 'movie');
      const result = {};
      fire('pointermove', client(hub));
      result.hoverFocusesNeighbours = state.hoverId === hub.id && state.hoverNodes.has(hub.id) && state.hoverNodes.size === hub.degree + 1;
      fire('pointerdown', client(movie)); fire('pointerup', client(movie));
      result.clickSelects = state.selectedId === movie.id && document.getElementById('knowledgeGraphPanel').textContent.includes(movie.label);
      const start = client(movie), before = {x: movie.x, y: movie.y};
      fire('pointerdown', start); fire('pointermove', {clientX: start.clientX + 30, clientY: start.clientY + 20}); fire('pointermove', {clientX: start.clientX + 60, clientY: start.clientY + 40});
      result.dragMovesNode = Math.abs(movie.x - before.x - 60 / state.zoom) < 1 && Math.abs(movie.y - before.y - 40 / state.zoom) < 1;
      fire('pointerup', {clientX: start.clientX + 60, clientY: start.clientY + 40});
      const zoomBefore = state.zoom;
      document.querySelector('[data-graph-action="in"]').click(); settleKnowledgeGraph();
      result.zoomButtonZooms = knowledgeGraphState.zoom > zoomBefore;
      document.querySelector('[data-graph-action="fit"]').click(); settleKnowledgeGraph();
      result.fitShowsEverything = state.nodes.every(node => { const x = node.x * state.zoom + state.panX, y = node.y * state.zoom + state.panY; return x >= 0 && y >= 0 && x <= state.view.w && y <= state.view.h; });
      let exported = null;
      window.pywebview.api.export_interactive_graph = async graph => { exported = graph; return {ok: true, cancelled: false, error: ''}; };
      await exportKnowledgeGraph();
      const exportedIds = new Set((exported?.nodes || []).map(node => node.id));
      result.exportCarriesLayout = !!exported && exported.nodes.length === state.nodes.length && exported.nodes.every(node => Number.isFinite(node.x) && Number.isFinite(node.y)) &&
        exported.edges.length === state.edges.length && exported.edges.every(edge => exportedIds.has(edge.source) && exportedIds.has(edge.target));
      canvas.dispatchEvent(new MouseEvent('dblclick', {bubbles: true, ...client(movie)})); await wait(80);
      result.doubleClickOpensDetail = document.getElementById('detailOverlay').classList.contains('active') && document.getElementById('detailTitle').textContent === movie.label;
      return result;
    });
    assert.deepEqual(interaction, {hoverFocusesNeighbours: true, clickSelects: true, dragMovesNode: true, zoomButtonZooms: true,
      fitShowsEverything: true, exportCarriesLayout: true, doubleClickOpensDetail: true});
  });

  await check('relation graph draws movie nodes from cached poster thumbnails', async () => {
    await loadApp({reviews: posterArchive(18), stills: true, width: 1440, height: 900});
    const posters = await page(async () => {
      openGallery();
      for (let waited = 0; waited < 20000; waited += 100) { await new Promise(resolve => setTimeout(resolve, 100)); if (knowledgeGraphState.posters.size >= 15) break; }
      const reads = window.__qa.imageReads;
      setRelationDimension('director'); await new Promise(resolve => setTimeout(resolve, 600));
      return {sprites: knowledgeGraphState.posters.size, reads, readsAfterSwitchingDimension: window.__qa.imageReads - reads};
    });
    assert.deepEqual(posters, {sprites: 15, reads: 15, readsAfterSwitchingDimension: 0});
  });

  await check('cards show the film categories', async () => {
    await loadApp({reviews: sample()});
    const tags = await page(() => [...document.querySelector('[data-review-id="a"] .card-tags').children].map(tag => tag.textContent));
    assert.deepEqual(tags, ['科幻', '冒险', '时间']);
  });

  await check('the category field keeps its original suggestions first and adds genre, style and theme ones', async () => {
    await loadApp({reviews: []});
    const offered = await page(() => { openModal(); return [...document.getElementById('categoryInput').list.options].map(option => option.value); });
    assert.deepEqual(offered.slice(0, 26), ['剧情', '喜剧', '动作', '爱情', '科幻', '动画', '悬疑', '惊悚', '恐怖', '犯罪', '冒险', '奇幻', '战争',
      '历史', '传记', '音乐', '歌舞', '家庭', '纪录片', '短片', '西部', '灾难', '武侠', '古装', '运动', '儿童']);
    assert.deepEqual(['文艺', '青春', '赛博朋克', '黑色幽默', '超现实', '人生', '哲学', '亲情'].filter(name => !offered.includes(name)), []);
  });

  await check('a suggested theme category can be picked for a new review and is saved with it', async () => {
    await loadApp({reviews: []});
    const saved = await page(async () => {
      openModal();
      document.getElementById('movieTitle').value = '东京物语';
      const input = document.getElementById('categoryInput');
      input.value = [...input.list.options].map(option => option.value).find(value => value === '人生') ?? '';
      addCategoryFromInput();
      document.getElementById('movieForm').requestSubmit();
      await new Promise(resolve => setTimeout(resolve, 300));
      return window.__qa.store.map(review => ({title: review.title, categories: review.categories ?? null}));
    });
    assert.deepEqual(saved, [{title: '东京物语', categories: ['人生']}]);
  });

  await check('each category suggestion is offered once', async () => {
    await loadApp({reviews: []});
    const repeated = await page(() => {
      const offered = [...document.getElementById('categoryInput').list.options].map(option => option.value.trim());
      return offered.filter((value, index) => !value || offered.indexOf(value) !== index);
    });
    assert.deepEqual(repeated, []);
  });

  await check('archive health never reports two suggested categories as aliases of each other', async () => {
    await loadApp({reviews: []});
    const offered = await page(() => [...document.getElementById('categoryInput').list.options].map(option => option.value));
    await loadApp({reviews: Array.from({length: Math.ceil(offered.length / 8)}, (_, index) => ({id: 'h' + index, title: '分类电影 ' + index, director: '导演' + index,
      date: '2024-01-01', rating: 3, tags: [], comment: '', categories: offered.slice(index * 8, index * 8 + 8), createdAt: 1700000000000 + index, updatedAt: 1700000000000 + index}))});
    const health = await page(() => { const data = buildArchiveHealth(); return {checked: data.categories.length, aliases: data.aliases}; });
    assert.deepEqual(health, {checked: new Set(offered).size, aliases: []});
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

  for (const {width, height, minimumRows} of [
    {width: 375, height: 667, minimumRows: 3},
    {width: 390, height: 844, minimumRows: 4},
    {width: 320, height: 568, minimumRows: 2}
  ]) {
    await check(`web home at ${width}x${height} shows two columns and at least ${minimumRows} complete rows`, async () => {
      const archive = posterArchive(12).map((review, index) => ({...review,
        title: index % 3 === 0 ? '特别长的电影名称：在旅途与回忆之间寻找那些未曾说出口的话 ' + index : review.title,
        ...(index === 0 || index === 5 ? {rewatches: Array.from({length: index === 0 ? 11 : 99}, (_, viewing) => ({
          watchedAt: '2025-01-02T19:00', feeling: '', createdAt: 1700000001000 + viewing}))} : {})}));
      await loadApp({reviews: archive, stills: true, width, height, mobile: true});
      await installWebLayout();
      const layout = await page(async () => {
        await new Promise(resolve => setTimeout(resolve, 550));
        window.scrollTo(0, 0);
        const cards = [...document.querySelectorAll('#movieGrid .movie-card')];
        const rectangles = cards.map(card => {
          const rect = card.getBoundingClientRect();
          return {top: Math.round(rect.top), bottom: rect.bottom, left: rect.left, right: rect.right};
        });
        const rows = [...new Set(rectangles.map(rect => rect.top))].map(top => rectangles.filter(rect => rect.top === top));
        const header = document.querySelector('.app-header'), actions = document.querySelector('.header-actions');
        const account = document.getElementById('webAccountButton').getBoundingClientRect();
        const primaryActions = [...actions.querySelectorAll('button')].slice(0, 2);
        const isVisible = element => element.getBoundingClientRect().width > 0 && element.getBoundingClientRect().height > 0;
        return {viewport: [innerWidth, innerHeight], cardCount: cards.length,
          completeRows: rows.filter(row => row.length === 2 && row.every(rect => rect.top >= 0 && rect.bottom <= innerHeight)).length,
          cardsPerRow: rows.map(row => row.length), gridTop: rectangles[0].top,
          pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
          cardsWithinViewport: rectangles.every(rect => rect.left >= 0 && rect.right <= innerWidth),
          accountVisible: account.width > 0 && account.left >= 0 && account.right <= innerWidth && account.bottom <= innerHeight,
          directHeaderChild: actions.parentElement === header,
          primaryActionNames: primaryActions.map(button => button.textContent.trim()),
          primaryActionsVisible: primaryActions.every(button => {const rect = button.getBoundingClientRect(); return rect.left >= 0 && rect.right <= innerWidth && rect.height >= 40;}),
          toolsScrollable: actions.scrollWidth > actions.clientWidth && ['auto', 'scroll'].includes(getComputedStyle(actions).overflowX),
          cardBodiesHidden: cards.every(card => !isVisible(card.querySelector('.card-body'))),
          datesVisible: cards.every(card => isVisible(card.querySelector('.card-mobile-meta'))),
          datesFit: cards.every(card => {const meta = card.querySelector('.card-mobile-meta'); return meta.scrollWidth <= meta.clientWidth;}),
          longWatchCountCovered: cards.some(card => card.querySelector('.card-mobile-meta').textContent.includes('100')),
          loadedImages: document.querySelectorAll('#movieGrid .card-visual.image-ready').length,
          missingImages: document.querySelectorAll('#movieGrid .movie-card.no-image').length,
          filtersHidden: !isVisible(document.getElementById('filterControls')),
          memoryCollapsed: document.getElementById('memoryToggle').getAttribute('aria-expanded') === 'false' && !isVisible(document.getElementById('memoryList')),
          searchFont: parseFloat(getComputedStyle(document.getElementById('searchInput')).fontSize), saves: window.__qa.saves};
      });
      const screenshot = await browser.send('Page.captureScreenshot', {format: 'png'});
      await writeFile(path.join(browser.output, `mobile-home-${width}x${height}.png`), Buffer.from(screenshot.data, 'base64'));
      const diagnostic = JSON.stringify(layout);
      assert.deepEqual(layout.viewport, [width, height], diagnostic);
      assert.equal(layout.cardCount, 12, diagnostic);
      assert.ok(layout.cardsPerRow.every(count => count === 2), diagnostic);
      assert.ok(layout.completeRows >= minimumRows, diagnostic);
      assert.ok(!layout.pageOverflow && layout.cardsWithinViewport && layout.accountVisible, diagnostic);
      assert.ok(layout.directHeaderChild && layout.primaryActionsVisible && layout.toolsScrollable, diagnostic);
      assert.ok(layout.primaryActionNames[0].includes('写影评') && layout.primaryActionNames[1].includes('策展墙'), diagnostic);
      assert.ok(layout.cardBodiesHidden && layout.datesVisible && layout.datesFit && layout.longWatchCountCovered && layout.loadedImages > 0 && layout.missingImages > 0, diagnostic);
      assert.ok(layout.filtersHidden && layout.memoryCollapsed && layout.searchFont >= 16, diagnostic);
      assert.equal(layout.saves, 0, diagnostic);
    });
  }

  await check('web phone memories and filters expand without losing filtering or detail editing', async () => {
    await loadApp({reviews: [], ...phone});
    await installWebLayout();
    const state = await page(async () => {
      const today = archiveNowParts(), pad = value => String(value).padStart(2, '0');
      reviews = [1, 2, 3].map(offset => ({id: 'mobile-memory-' + offset, title: '往年今日 ' + offset, director: '合成导演',
        rating: 5, categories: ['记忆'], tags: [], comment: '保留完整评论与操作入口。',
        date: `${today.year - offset}-${pad(today.month)}-${pad(today.day)}`, createdAt: offset, updatedAt: offset}));
      reviews.push({id: 'mobile-other', title: '其他电影', rating: 2, categories: ['其他'], tags: [], comment: '',
        date: `${today.year}-${pad(today.month)}-${pad(today.day)}`, createdAt: 4, updatedAt: 4});
      renderAll();
      const visible = element => element.getBoundingClientRect().width > 0 && element.getBoundingClientRect().height > 0;
      const memoryToggle = document.getElementById('memoryToggle'), filterToggle = document.getElementById('mobileFilterToggle');
      const memoryList = document.getElementById('memoryList'), filters = document.getElementById('filterControls');
      const initial = {memoryCount: memoryList.children.length, memoryHidden: !visible(memoryList), filtersHidden: !visible(filters),
        memoryExpanded: memoryToggle.getAttribute('aria-expanded'), filtersExpanded: filterToggle.getAttribute('aria-expanded'),
        memoryControls: memoryToggle.getAttribute('aria-controls'), filterControls: filterToggle.getAttribute('aria-controls')};
      memoryToggle.click();
      const memoryExpanded = visible(memoryList) && memoryToggle.getAttribute('aria-expanded') === 'true';
      memoryList.querySelector('.btn-primary').click();
      const rewatchAvailable = document.getElementById('rewatchOverlay').classList.contains('active');
      closeRewatchModal(); memoryToggle.click();
      const memoryCollapsed = !visible(memoryList) && memoryToggle.getAttribute('aria-expanded') === 'false';
      filterToggle.click();
      const filtersExpanded = visible(filters) && filterToggle.getAttribute('aria-expanded') === 'true';
      for (const [id, value] of [['filterRating', '5'], ['filterTag', '记忆'], ['sortBy', 'title']]) {
        const select = document.getElementById(id); select.value = value; select.dispatchEvent(new Event('change', {bubbles: true}));
      }
      const filteredBefore = getVisibleReviews().map(review => review.id);
      filterToggle.click();
      const filteredAfter = getVisibleReviews().map(review => review.id);
      const collapsedFilters = !visible(filters) && filterToggle.getAttribute('aria-expanded') === 'false';
      const filterSummary = {text: filterToggle.textContent, aria: filterToggle.getAttribute('aria-label'), highlighted: filterToggle.classList.contains('has-filters')};
      const search = document.getElementById('searchInput'); search.value = '往年今日 2'; search.dispatchEvent(new Event('input', {bubbles: true}));
      const searchIds = getVisibleReviews().map(review => review.id);
      const card = document.querySelector('#movieGrid .movie-card'); card.click();
      const detailOpened = document.getElementById('detailOverlay').classList.contains('active');
      const detailActions = [...document.querySelectorAll('#detailOverlay .detail-actions button')].filter(visible).map(button => button.textContent);
      document.querySelector('#detailOverlay button[onclick="editFromDetail()"]').click();
      const editing = {open: document.getElementById('modalOverlay').classList.contains('active'), id: document.getElementById('editId').value,
        title: document.getElementById('movieTitle').value, comment: document.getElementById('movieComment').value};
      closeModal();
      card.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}));
      const keyboardDetail = document.getElementById('detailOverlay').classList.contains('active'); closeDetail();
      filterToggle.click();
      const preservedValues = ['filterRating', 'filterTag', 'sortBy'].map(id => document.getElementById(id).value);
      return {initial, memoryExpanded, rewatchAvailable, memoryCollapsed, filtersExpanded, filteredBefore, filteredAfter, collapsedFilters,
        filterSummary, searchIds, detailOpened, detailActions, editing, keyboardDetail, preservedValues, saves: window.__qa.saves,
        pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth};
    });
    const diagnostic = JSON.stringify(state);
    assert.deepEqual(state.initial, {memoryCount: 3, memoryHidden: true, filtersHidden: true, memoryExpanded: 'false', filtersExpanded: 'false',
      memoryControls: 'memoryList', filterControls: 'filterControls'}, diagnostic);
    assert.ok(state.memoryExpanded && state.rewatchAvailable && state.memoryCollapsed && state.filtersExpanded && state.collapsedFilters, diagnostic);
    assert.deepEqual(state.filteredBefore, ['mobile-memory-1', 'mobile-memory-2', 'mobile-memory-3'], diagnostic);
    assert.deepEqual(state.filteredAfter, state.filteredBefore, diagnostic);
    assert.ok(state.filterSummary.highlighted && state.filterSummary.text.includes('3') && state.filterSummary.aria.includes('3'), diagnostic);
    assert.deepEqual(state.searchIds, ['mobile-memory-2'], diagnostic);
    assert.ok(state.detailOpened && state.keyboardDetail && ['记录重看', '编辑影评', '删除影评'].every(label => state.detailActions.some(text => text.includes(label))), diagnostic);
    assert.deepEqual(state.editing, {open: true, id: 'mobile-memory-2', title: '往年今日 2', comment: '保留完整评论与操作入口。'}, diagnostic);
    assert.deepEqual(state.preservedValues, ['5', '记忆', 'title'], diagnostic);
    assert.ok(!state.pageOverflow && state.saves === 0, diagnostic);
  });

  await check('web desktop retains expanded filters, memories and full review cards', async () => {
    await loadApp({reviews: sample(), width: 1280, height: 800});
    await installWebLayout();
    const layout = await page(() => {
      const today = archiveNowParts(), pad = value => String(value).padStart(2, '0');
      reviews[0].date = `${today.year - 1}-${pad(today.month)}-${pad(today.day)}`;
      renderAll();
      const visible = selector => {const rect = document.querySelector(selector).getBoundingClientRect(); return rect.width > 0 && rect.height > 0;};
      const grid = document.getElementById('movieGrid');
      return {columns: getComputedStyle(grid).gridTemplateColumns.split(' ').length,
        filtersVisible: ['#filterControls', '#filterRating', '#filterTag', '#sortBy'].every(visible),
        memoryVisible: visible('#memoryList'), bodiesVisible: [...document.querySelectorAll('.card-body')].every(body => body.getBoundingClientRect().height > 0),
        mobileFilterHidden: !visible('#mobileFilterToggle'), mobileMemoryHidden: !visible('#memoryToggle'), mobileDatesHidden: !visible('.card-mobile-meta'),
        editVisible: visible('#movieGrid [data-action="edit"]'), accountVisible: visible('#webAccountButton'),
        pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth};
    });
    const diagnostic = JSON.stringify(layout);
    assert.ok(layout.columns >= 3 && layout.filtersVisible && layout.memoryVisible && layout.bodiesVisible, diagnostic);
    assert.ok(layout.mobileFilterHidden && layout.mobileMemoryHidden && layout.mobileDatesHidden, diagnostic);
    assert.ok(layout.editVisible && layout.accountVisible && !layout.pageOverflow, diagnostic);
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
