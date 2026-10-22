// Real browser image lifecycle regression with synthetic Blobs and controlled downloads.
// No account, server, private image or real archive is used.
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
import {launchBrowser} from './browser_harness.mjs';

const browser = await launchBrowser('browser-detail-images');
const {page, loadApp} = browser;
const results = [];

async function fixture(count = 6, seedThumbnails = true) {
  await loadApp({reviews: [], width: 390, height: 844, mobile: true});
  await page(async ({count, seedThumbnails}) => {
    const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
    const until = async (predicate, label) => {
      for (let attempt = 0; attempt < 150; attempt++) {
        if (predicate()) return;
        await delay(20);
      }
      throw new Error('Timed out: ' + label);
    };
    const makeBlob = async (width, height, hue = 180) => {
      const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
      const context = canvas.getContext('2d');
      context.fillStyle = `hsl(${hue} 45% 35%)`; context.fillRect(0, 0, width, height);
      context.fillStyle = '#dfbb6a'; context.fillRect(width * .6, height * .2, width * .1, height * .1);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
      canvas.width = canvas.height = 0;
      return blob;
    };
    const q = window.__imageQA = {requests: [], thumbs: new Map(), revoked: [], decodes: [], gates: new Map(), delay, until, makeBlob};
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.revokeObjectURL = url => {q.revoked.push(url); revoke(url);};
    const decode = HTMLImageElement.prototype.decode;
    HTMLImageElement.prototype.decode = async function () {
      const url = this.src;
      await decode.call(this);
      q.decodes.push(url);
      if (q.gates.has(url)) await q.gates.get(url).promise;
    };
    q.imagePath = id => `media/${id.charCodeAt(0).toString(16).padStart(64, '0')}.png`;
    q.thumb = async image => {
      if (!q.thumbs.has(image.path)) q.thumbs.set(image.path, URL.createObjectURL(await makeBlob(160, 90)));
      return q.thumbs.get(image.path);
    };
    q.finished = id => q.requests.filter(request => request.path === q.imagePath(id) && request.url).at(-1)?.url;
    q.count = id => q.requests.filter(request => request.path === q.imagePath(id)).length;
    q.finish = async (id, options = {}) => {
      const request = q.requests.find(item => item.path === q.imagePath(id) && !item.settled);
      if (!request) throw new Error('No active request for ' + id);
      request.settled = true;
      if (options.fail) {request.reject(new Error('合成网络故障')); return '';}
      const blob = options.corrupt ? new Blob(['broken image'], {type: 'image/png'})
        : await makeBlob(options.width || 1600, options.height || 900, id.charCodeAt(0) * 5);
      if (!request.current()) {request.resolve(''); return '';}
      const url = request.url = URL.createObjectURL(blob);
      if (options.holdDecode) {
        let release;
        const promise = new Promise(resolve => {release = resolve;});
        q.gates.set(url, {promise, release});
      }
      request.progress(100); request.resolve(url);
      return url;
    };
    window.MOVIE_REVIEW_WEB = true;
    window.MovieReviewWeb = {...window.pywebview.api, loadImageObjectUrl: async (image, progress, current, thumbnail) => {
      if (thumbnail) return q.thumb(image);
      return new Promise((resolve, reject) => q.requests.push({path: image.path, progress, current, resolve, reject, settled: false}));
    }};
    reviews = Array.from({length: count}, (_, index) => {
      const id = String.fromCharCode(97 + index);
      return {id, title: '合成影片 ' + id, date: '2026-10-01', rating: 4, comment: '图片生命周期测试', tags: [],
        createdAt: index + 1, updatedAt: index + 1,
        image: {path: q.imagePath(id), name: id + '.png', mime: 'image/png', size: 1000}};
    });
    if (seedThumbnails) {
      for (const review of reviews) rememberCardImage(review.image.path, await q.thumb(review.image));
    }
    renderAll();
    q.visible = element => {const rect = element.getBoundingClientRect(); return rect.width > 0 && rect.height > 0;};
    q.cacheBytes = () => [...detailImageCache.values()].reduce((total, entry) => total + entry.bytes, 0);
    q.loaded = async id => {
      const url = q.finished(id);
      await until(() => url && document.getElementById('detailImage').src === url
        && document.getElementById('detailImage').naturalWidth > 160
        && !document.getElementById('detailImageSection').classList.contains('preview'), 'full image ' + id);
      return url;
    };
    q.openAndFinish = async (id, options = {}) => {
      openDetail(id); await until(() => q.requests.some(request => request.path === q.imagePath(id) && !request.settled), 'request ' + id);
      const url = await q.finish(id, options); await q.loaded(id); return url;
    };
    await delay(50);
  }, {count, seedThumbnails});
}

async function check(name, run) {
  try {await run(); results.push({name, passed: true});}
  catch (error) {results.push({name, passed: false, error: String(error.stack || error)});}
}

try {
  await check('detail immediately borrows its thumbnail, upgrades only after decode and reuses original on reopen', async () => {
    await fixture();
    const state = await page(async () => {
      const q = __imageQA, thumb = q.thumbs.get(q.imagePath('a'));
      openDetail('a');
      const preview = {src: document.getElementById('detailImage').src, loaded: document.getElementById('detailImageSection').classList.contains('loaded')};
      openLightbox();
      const lightboxPreview = document.getElementById('lightboxImage').src;
      closeDetail(); openDetail('a');
      const inFlightCount = q.count('a');
      openLightbox();
      const original = await q.finish('a', {holdDecode: true});
      await q.until(() => q.decodes.includes(original), 'original decoded');
      const whileDecoding = {detail: document.getElementById('detailImage').src, lightbox: document.getElementById('lightboxImage').src};
      q.gates.get(original).release(); await q.loaded('a');
      const upgraded = {src: document.getElementById('detailImage').src, lightbox: document.getElementById('lightboxImage').src,
        lightboxOpen: document.getElementById('lightboxOverlay').classList.contains('active')};
      closeDetail();
      const cleared = !document.getElementById('detailImage').hasAttribute('src') && !document.getElementById('lightboxImage').hasAttribute('src');
      openDetail('a'); await q.until(() => document.getElementById('detailImage').src === original, 'cache hit');
      return {thumb, original, preview, lightboxPreview, inFlightCount, whileDecoding, upgraded, cleared,
        requests: q.count('a'), thumbRevoked: q.revoked.includes(thumb), originalRevoked: q.revoked.includes(original), saves: __qa.saves};
    });
    assert.deepEqual(state.preview, {src: state.thumb, loaded: true});
    assert.equal(state.lightboxPreview, state.thumb);
    assert.equal(state.inFlightCount, 1);
    assert.deepEqual(state.whileDecoding, {detail: state.thumb, lightbox: state.thumb});
    assert.deepEqual(state.upgraded, {src: state.original, lightbox: state.original, lightboxOpen: true});
    assert.ok(state.cleared && !state.thumbRevoked && !state.originalRevoked);
    assert.equal(state.requests, 1); assert.equal(state.saves, 0);
  });

  await check('late original responses cannot overwrite another film or reopen a closed detail', async () => {
    await fixture();
    const state = await page(async () => {
      const q = __imageQA;
      openDetail('a'); openDetail('b');
      const thumbB = q.thumbs.get(q.imagePath('b'));
      const originalA = await q.finish('a'); await q.delay(70);
      const afterA = {id: detailReviewId, src: document.getElementById('detailImage').src};
      closeDetail(); await q.finish('b'); await q.delay(70);
      const closed = !document.getElementById('detailOverlay').classList.contains('active')
        && !document.getElementById('detailImage').hasAttribute('src');
      openDetail('a'); await q.until(() => document.getElementById('detailImage').src === originalA, 'cached late original');
      return {thumbB, afterA, closed, requestsA: q.count('a'), requestsB: q.count('b'), finalTitle: document.getElementById('detailTitle').textContent};
    });
    assert.deepEqual(state.afterA, {id: 'b', src: state.thumbB});
    assert.ok(state.closed); assert.equal(state.requestsA, 1); assert.equal(state.requestsB, 1);
    assert.equal(state.finalTitle, '合成影片 a');
  });

  await check('network and image decode failures preserve preview, show feedback and allow retry', async () => {
    await fixture();
    const state = await page(async () => {
      const q = __imageQA, thumb = q.thumbs.get(q.imagePath('a'));
      openDetail('a'); await q.finish('a', {fail: true});
      await q.until(() => document.querySelector('#detailImageState button'), 'network retry button');
      const failure = {src: document.getElementById('detailImage').src, visible: q.visible(document.getElementById('detailImageState'))};
      document.querySelector('#detailImageState button').click();
      await q.until(() => q.count('a') === 2, 'network retry request');
      const corrupt = await q.finish('a', {corrupt: true});
      await q.until(() => q.revoked.includes(corrupt) && document.querySelector('#detailImageState button'), 'corrupt image released');
      const decodeFailure = {src: document.getElementById('detailImage').src, visible: q.visible(document.getElementById('detailImageState'))};
      document.querySelector('#detailImageState button').click();
      await q.until(() => q.count('a') === 3, 'decode retry request');
      await q.finish('a'); await q.loaded('a');
      return {thumb, failure, decodeFailure, recovered: !document.getElementById('detailImageSection').classList.contains('preview'), requests: q.count('a'), thumbRevoked: q.revoked.includes(thumb)};
    });
    assert.deepEqual(state.failure, {src: state.thumb, visible: true});
    assert.deepEqual(state.decodeFailure, {src: state.thumb, visible: true});
    assert.ok(state.recovered && !state.thumbRevoked); assert.equal(state.requests, 3);
  });

  await check('original cache keeps recent films and evicts the least recently viewed one', async () => {
    await fixture();
    const state = await page(async () => {
      const q = __imageQA;
      for (const id of ['a', 'b', 'c', 'd']) {await q.openAndFinish(id); closeDetail();}
      openDetail('a'); await q.loaded('a'); closeDetail();
      await q.openAndFinish('e'); closeDetail();
      const oldB = q.finished('b'), recentA = q.finished('a');
      const bounded = detailImageCache.size <= 4 && q.cacheBytes() <= 64 * 1024 * 1024;
      openDetail('a'); await q.loaded('a'); closeDetail();
      openDetail('b'); await q.until(() => q.count('b') === 2, 'evicted image requested again');
      return {bounded, aRequests: q.count('a'), bRequests: q.count('b'), oldBReleased: q.revoked.includes(oldB), recentAReleased: q.revoked.includes(recentA)};
    });
    assert.deepEqual(state, {bounded: true, aRequests: 1, bRequests: 2, oldBReleased: true, recentAReleased: false});
  });

  await check('cache enforces its byte budget and releases an oversized active original on close', async () => {
    await fixture();
    const state = await page(async () => {
      const q = __imageQA;
      reviews[0].image.size = 32 * 1024 * 1024; reviews[1].image.size = 32 * 1024 * 1024;
      const first = await q.openAndFinish('a', {width: 2000, height: 2000}); closeDetail();
      await q.openAndFinish('b', {width: 2000, height: 2000}); closeDetail();
      const afterTwo = {bytes: q.cacheBytes(), firstReleased: q.revoked.includes(first)};
      reviews[2].image.size = 50 * 1024 * 1024;
      const oversized = await q.openAndFinish('c', {width: 2000, height: 2000});
      const activeVisible = document.getElementById('detailImage').src === oversized && !q.revoked.includes(oversized);
      closeDetail();
      return {afterTwo, activeVisible, oversizedReleased: q.revoked.includes(oversized), finalBytes: q.cacheBytes(), finalCount: detailImageCache.size};
    });
    assert.ok(state.afterTwo.bytes <= 64 * 1024 * 1024 && state.afterTwo.firstReleased);
    assert.ok(state.activeVisible && state.oversizedReleased);
    assert.ok(state.finalBytes <= 64 * 1024 * 1024 && state.finalCount <= 4);
  });

  await check('removing or replacing an image clears old originals and blocks stale responses', async () => {
    await fixture();
    const state = await page(async () => {
      const q = __imageQA;
      const originalA = await q.openAndFinish('a'); closeDetail();
      openDetail('b');
      reviews[0].image = null;
      const replacement = {...reviews[1].image, path: q.imagePath('z'), name: 'z.png'};
      reviews[1].image = replacement; renderAll();
      await q.finish('b');
      await q.until(() => q.count('z') === 1, 'replacement original request');
      const replacementOriginal = await q.finish('z'); await q.loaded('z');
      return {oldReleased: q.revoked.includes(originalA), oldCached: detailImageCache.has(q.imagePath('a')),
        staleCached: detailImageCache.has(q.imagePath('b')), replacementShown: document.getElementById('detailImage').src === replacementOriginal,
        correctReview: detailReviewId === 'b', saves: __qa.saves};
    });
    assert.deepEqual(state, {oldReleased: true, oldCached: false, staleCached: false, replacementShown: true, correctReview: true, saves: 0});
  });

  await check('rapid navigation bounds original downloads and prioritizes the latest queued film', async () => {
    await fixture();
    const state = await page(async () => {
      const q = __imageQA;
      openDetail('a'); openDetail('b'); openDetail('c'); openDetail('d');
      await q.delay(30);
      const startedBefore = q.requests.map(request => request.path);
      await q.finish('a');
      await q.until(() => q.count('d') === 1, 'latest queued request');
      await q.finish('d'); await q.loaded('d');
      await q.finish('b'); await q.delay(70);
      return {startedBefore, a: q.imagePath('a'), b: q.imagePath('b'), obsoleteRequests: q.count('c'), totalRequests: q.requests.length,
        current: detailReviewId, src: document.getElementById('detailImage').src, expected: q.finished('d')};
    });
    assert.deepEqual(state.startedBefore, [state.a, state.b]);
    assert.equal(state.obsoleteRequests, 0); assert.equal(state.totalRequests, 3);
    assert.equal(state.current, 'd'); assert.equal(state.src, state.expected);
  });

  await check('a thumbnail arriving after opening detail is still used while its original is pending', async () => {
    await fixture(2, false);
    const state = await page(async () => {
      const q = __imageQA;
      // Remove the fixture's eager thumbnail cache to exercise the late-preview path.
      const old = cardImageCache.get(q.imagePath('a'));
      cardImageCache.delete(q.imagePath('a'));
      openDetail('a');
      await q.until(() => document.getElementById('detailImageSection').classList.contains('loaded'), 'late thumbnail preview');
      const preview = document.getElementById('detailImage').src;
      closeDetail();
      return {preview, expected: q.thumbs.get(q.imagePath('a')), originalRequests: q.count('a'), previewRevokedOnClose: q.revoked.includes(preview), old};
    });
    assert.equal(state.preview, state.expected); assert.equal(state.originalRequests, 1); assert.ok(!state.previewRevokedOnClose);
  });

  await check('replaced and evicted thumbnails remain usable until detail and lightbox upgrade to the original', async () => {
    for (const mode of ['replacement', 'eviction']) {
      await fixture(2);
      const state = await page(async mode => {
        const q = __imageQA, imagePath = q.imagePath('a'), borrowed = q.thumbs.get(imagePath);
        openDetail('a'); openLightbox();
        const blob = await q.makeBlob(160, 90, 250);
        let retained;
        if (mode === 'replacement') {
          retained = URL.createObjectURL(blob);
          rememberCardImage(imagePath, retained); applyCardImage(imagePath, retained);
        } else {
          // Exercise real eviction by filling the bounded cache with distinct Blob URLs.
          for (let index = 0; index < 600; index++) {
            retained = URL.createObjectURL(blob);
            rememberCardImage(`media/${(1000 + index).toString(16).padStart(64, '0')}.png`, retained);
          }
        }
        const retired = {noLongerCached: cardImageCache.get(imagePath) !== borrowed,
          notRevoked: !q.revoked.includes(borrowed), detail: document.getElementById('detailImage').src,
          lightbox: document.getElementById('lightboxImage').src, stillFetchable: (await fetch(borrowed)).ok};
        const freshConsumer = new Image(); freshConsumer.src = borrowed; await freshConsumer.decode();
        retired.stillDecodable = freshConsumer.naturalWidth === 160;
        const original = await q.finish('a'); await q.loaded('a');
        await q.until(() => q.revoked.includes(borrowed), 'retired thumbnail released after upgrade');
        const upgraded = {detail: document.getElementById('detailImage').src, lightbox: document.getElementById('lightboxImage').src};
        closeDetail();
        return {mode, borrowed, original, retired, upgraded, borrowedReleaseCount: q.revoked.filter(url => url === borrowed).length,
          cacheOwnedThumbRetained: !q.revoked.includes(retained), originalRequests: q.count('a')};
      }, mode);
      const diagnostic = JSON.stringify(state);
      assert.deepEqual(state.retired, {noLongerCached: true, notRevoked: true, detail: state.borrowed,
        lightbox: state.borrowed, stillFetchable: true, stillDecodable: true}, diagnostic);
      assert.deepEqual(state.upgraded, {detail: state.original, lightbox: state.original}, diagnostic);
      assert.equal(state.borrowedReleaseCount, 1, diagnostic);
      assert.ok(state.cacheOwnedThumbRetained, diagnostic); assert.equal(state.originalRequests, 1, diagnostic);
    }
  });

  await check('original failure before a late thumbnail keeps visible preview feedback and a working retry', async () => {
    await fixture(2);
    const state = await page(async () => {
      const q = __imageQA, imagePath = q.imagePath('a'), thumbnail = q.thumbs.get(imagePath);
      await q.until(() => !cardImageLoading.has(imagePath), 'fixture thumbnail finished');
      cardImageCache.delete(imagePath);
      const loader = window.MovieReviewWeb.loadImageObjectUrl;
      let releaseThumbnail;
      window.MovieReviewWeb.loadImageObjectUrl = async (image, progress, current, isThumbnail) => {
        if (isThumbnail && image.path === imagePath) return new Promise(resolve => {releaseThumbnail = () => resolve(thumbnail);});
        return loader(image, progress, current, isThumbnail);
      };
      openDetail('a');
      await q.until(() => releaseThumbnail && q.count('a') === 1, 'thumbnail and original requests started');
      await q.finish('a', {fail: true});
      await q.until(() => document.getElementById('detailImageRetry'), 'original failed before thumbnail');
      const beforePreview = {empty: !document.getElementById('detailImage').hasAttribute('src'),
        feedbackVisible: q.visible(document.getElementById('detailImageState'))};
      releaseThumbnail();
      await q.until(() => document.getElementById('detailImage').src === thumbnail, 'late thumbnail displayed after failure');
      const retry = document.getElementById('detailImageRetry');
      const afterPreview = {src: document.getElementById('detailImage').src,
        feedbackVisible: q.visible(document.getElementById('detailImageState')),
        previewExplained: document.getElementById('detailImageState').textContent.includes('预览图'),
        retryVisible: !!retry && q.visible(retry)};
      if (!retry) throw new Error('Late thumbnail removed the original-image retry button');
      retry.click();
      await q.until(() => q.count('a') === 2, 'retry after late thumbnail');
      await q.finish('a'); await q.loaded('a');
      return {thumbnail, beforePreview, afterPreview, recovered: !document.getElementById('detailImageSection').classList.contains('preview')
        && !document.getElementById('detailImageSection').classList.contains('load-failed'),
        requests: q.count('a'), previewRevoked: q.revoked.includes(thumbnail)};
    });
    const diagnostic = JSON.stringify(state);
    assert.deepEqual(state.beforePreview, {empty: true, feedbackVisible: true}, diagnostic);
    assert.deepEqual(state.afterPreview, {src: state.thumbnail, feedbackVisible: true, previewExplained: true, retryVisible: true}, diagnostic);
    assert.ok(state.recovered && !state.previewRevoked, diagnostic); assert.equal(state.requests, 2, diagnostic);
  });
} finally {
  browser.close();
}

await writeFile(path.join(browser.output, 'results.json'), JSON.stringify(results, null, 2));
const failed = results.filter(result => !result.passed);
for (const result of results) console.log(`${result.passed ? 'ok  ' : 'FAIL'} ${result.name}${result.passed ? '' : '\n' + result.error}`);
console.log(`Detail images: ${results.length - failed.length}/${results.length} passed; ${browser.output}`);
if (failed.length) process.exitCode = 1;
