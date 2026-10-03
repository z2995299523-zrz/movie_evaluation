// Optional Windows browser smoke test for a locally running web server.
// Set MOVIE_REVIEW_QA_URL and MOVIE_REVIEW_QA_USER/PASSWORD before running.
import {spawn} from 'node:child_process';
import {mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';

const chromePath = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const url = process.env.MOVIE_REVIEW_QA_URL || 'http://127.0.0.1:8765/';
const username = process.env.MOVIE_REVIEW_QA_USER;
const password = process.env.MOVIE_REVIEW_QA_PASSWORD;
const checkUsers = process.env.MOVIE_REVIEW_QA_USERS === '1';
const managedUsername = 'qa-user-' + Date.now();
const managedPassword = 'qa-created-long-password';
const resetPassword = 'qa-reset-long-password';
if (!username || !password) throw new Error('Set MOVIE_REVIEW_QA_USER and MOVIE_REVIEW_QA_PASSWORD');
const port = Number(process.env.MOVIE_REVIEW_QA_CDP_PORT || '9237');
const output = path.resolve(process.env.MOVIE_REVIEW_QA_OUTPUT_DIR || '.runtime-web/browser-qa-' + Date.now());
const profile = path.join(output, 'chrome-profile');
await mkdir(profile, {recursive: true});
const chrome = spawn(chromePath, [
  '--headless=new', '--no-sandbox', '--disable-gpu', '--no-first-run', '--no-proxy-server',
  '--no-default-browser-check', `--user-data-dir=${profile}`,
  `--remote-debugging-port=${port}`, url
], {windowsHide: true, stdio: ['ignore', 'ignore', 'pipe']});
let chromeDiagnostics = '';
chrome.stderr.on('data', chunk => { chromeDiagnostics += chunk.toString().slice(0, 4000); });

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let socket;
let nextId = 0;
const pending = new Map();
const exceptions = [];
async function devtools() {
  for (let attempt = 0; attempt < 60; attempt++) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const page = targets.find(item => item.type === 'page' && item.url.startsWith(url));
      if (page) return page.webSocketDebuggerUrl;
    } catch (_) {}
    await pause(200);
  }
  throw new Error('Chrome DevTools did not become ready: ' + chromeDiagnostics.slice(0, 2000));
}
function send(method, params = {}) {
  const id = ++nextId;
  return new Promise((resolve, reject) => {
    pending.set(id, {resolve, reject});
    socket.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const result = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
  if (result.result.exceptionDetails) throw new Error(result.result.exceptionDetails.text);
  return result.result.result.value;
}
async function until(expression, expected = true) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await evaluate(expression) === expected) return;
    await pause(100);
  }
  throw new Error(`Timed out: ${expression}`);
}
async function screenshot(name) {
  // Transient success notifications should not cover the interface being reviewed.
  await evaluate("window.__qaToastDisplay=document.querySelector('#toast')?.style.display;document.querySelector('#toast').style.display='none';new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))");
  try {
    const result = await send('Page.captureScreenshot', {format: 'png'});
    await writeFile(path.join(output, name + '.png'), Buffer.from(result.result.data, 'base64'));
  } finally {
    await evaluate("document.querySelector('#toast').style.display=window.__qaToastDisplay||'';true");
  }
}
async function confirmAccountAction() {
  await until("!!document.querySelector('.web-inline-confirm [data-yes]')");
  await evaluate("document.querySelector('.web-inline-confirm [data-yes]').click();true");
}

try {
  socket = new WebSocket(await devtools());
  await new Promise((resolve, reject) => {socket.onopen = resolve; socket.onerror = reject;});
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails.text);
    if (message.id && pending.has(message.id)) {
      const {resolve, reject} = pending.get(message.id);
      pending.delete(message.id);
      message.error ? reject(new Error(message.error.message)) : resolve(message);
    }
  };
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
  await send('Page.reload', {ignoreCache: true});
  await until("!!document.querySelector('#webUsername')");
  const loginWidth = await evaluate('document.documentElement.scrollWidth');
  if (loginWidth > 390) throw new Error(`Login has horizontal overflow: ${loginWidth}px`);
  await screenshot('account-ui-login-mobile');
  await evaluate("document.querySelector('[data-password=webPassword]').click();true");
  if (await evaluate("document.querySelector('#webPassword').type") !== 'text') throw new Error('Password visibility toggle failed');
  await evaluate("document.querySelector('[data-password=webPassword]').click();true");
  await send('Emulation.setDeviceMetricsOverride', {width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false});
  await screenshot('account-ui-login-desktop');
  await send('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
  await evaluate(`document.querySelector('#webUsername').value=${JSON.stringify(username)};
    document.querySelector('#webPassword').value=${JSON.stringify(password)};
    document.querySelector('#webUsername').closest('form').requestSubmit(); true`);
  await until("document.querySelector('#setupOverlay').classList.contains('hidden')");
  const beijingDate = await evaluate("localDateTimeValue(new Date('2026-09-28T20:00:00Z'))");
  if (beijingDate !== '2026-09-29T04:00') throw new Error(`Wrong Beijing date: ${beijingDate}`);
  const before = await evaluate('reviews.length');
  const title = '浏览器验收-' + Date.now();
  await evaluate(`openModal(); document.querySelector('#movieTitle').value=${JSON.stringify(title)};
    document.querySelector('#movieDate').value='2026-09-29';
    document.querySelector('#movieRating').value='4';
    document.querySelector('#movieComment').value='手机宽度保存检查';
    document.querySelector('#movieForm').requestSubmit(); true`);
  await until(`reviews.length===${before + 1}&&reviews.some(review=>review.title===${JSON.stringify(title)})`);
  const retryTitle = '断网重试-' + Date.now();
  await evaluate(`openModal(); document.querySelector('#movieTitle').value=${JSON.stringify(retryTitle)};
    document.querySelector('#movieDate').value='2026-09-29';
    document.querySelector('#movieRating').value='5';
    window.__realFetch=window.fetch;window.__dropOnce=false;
    window.fetch=(...args)=>{
      if(args[0]==='/api/reviews'&&!window.__dropOnce){
        window.__dropOnce=true;
        return window.__realFetch(...args).then(()=>Promise.reject(new TypeError('simulated response loss')));
      }
      return window.__realFetch(...args);
    };
    document.querySelector('#movieForm').requestSubmit(); true`);
  await until("window.__dropOnce&&document.querySelector('#toast').textContent.includes('网络连接中断')");
  await evaluate("window.fetch=window.__realFetch;document.querySelector('#movieForm').requestSubmit();true");
  await until(`reviews.length===${before + 2}&&reviews.filter(review=>review.title===${JSON.stringify(retryTitle)}).length===1`);
  const appWidth = await evaluate('document.documentElement.scrollWidth');
  if (appWidth > 390) throw new Error(`Archive has horizontal overflow: ${appWidth}px`);
  if (checkUsers) {
    await evaluate("document.querySelector('#webAccountButton').click();true");
    await until("document.querySelector('#webAccountMenu')!==null");
    await send('Input.dispatchKeyEvent', {type:'keyDown',key:'ArrowDown',code:'ArrowDown',windowsVirtualKeyCode:40});
    if (await evaluate('document.activeElement.id') !== 'webPasswordButton') throw new Error('Account menu keyboard navigation failed');
    await send('Input.dispatchKeyEvent', {type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
    if (!await evaluate("!document.querySelector('#webAccountMenu')&&document.activeElement.id==='webAccountButton'")) throw new Error('Account menu close did not restore focus');
    await evaluate("document.querySelector('#webAccountButton').click();true");
    await screenshot('account-ui-menu-mobile');
    await evaluate("document.querySelector('#webProfileButton').click();true");
    await until("!!document.querySelector('#webProfileDialog')");
    if (await evaluate("document.querySelector('#webProfileDialog .web-metric strong').textContent") !== String(before + 2)) throw new Error('Profile shows incorrect movie count');
    if (!await evaluate("document.querySelector('.app-wrapper').inert")) throw new Error('Account dialog does not isolate background interaction');
    await evaluate("[...document.querySelectorAll('#webProfileDialog button')].at(-1).focus();true");
    await send('Input.dispatchKeyEvent', {type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});
    if (!await evaluate("document.activeElement===document.querySelector('#webProfileDialog [data-close]')")) throw new Error('Dialog focus escaped with Tab');
    for (const width of [320,360]) {
      await send('Emulation.setDeviceMetricsOverride', {width,height:844,deviceScaleFactor:1,mobile:true});
      if (await evaluate('document.documentElement.scrollWidth') > width) throw new Error(`Profile overflows ${width}px viewport`);
      if (await evaluate("document.querySelector('#webProfileDialog .web-panel').scrollWidth>document.querySelector('#webProfileDialog .web-panel').clientWidth")) throw new Error(`Profile panel overflows ${width}px viewport`);
    }
    await send('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:true});
    await screenshot('account-ui-profile-mobile');
    await send('Emulation.setDeviceMetricsOverride', {width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false});
    await screenshot('account-ui-profile-desktop');
    await evaluate("document.querySelector('#webProfileDialog [data-action=edit]').click();true");
    await evaluate("document.querySelector('#webOwnDisplayName').value='银幕记忆 · 测试记录者';document.querySelector('#webProfileForm').requestSubmit();true");
    await until("!!document.querySelector('#webProfileDialog')&&document.querySelector('#webAccountButton').textContent.includes('银幕记忆')");
    await evaluate("window.__realUsersFetch=window.fetch;window.fetch=(...args)=>args[0]==='/api/users'?Promise.reject(new TypeError('simulated offline')):window.__realUsersFetch(...args);document.querySelector('#webProfileDialog [data-action=users]').click();true");
    await until("document.querySelector('#webRetryUsers')?.hidden===false");
    await evaluate("window.fetch=window.__realUsersFetch;document.querySelector('#webRetryUsers').click();true");
    await send('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
    await until("!!document.querySelector('#webUserList button')");
    await evaluate("document.querySelector('#webAddUser').click();true");
    await evaluate(`document.querySelector('#webManagedUsername').value=${JSON.stringify(managedUsername)};
      document.querySelector('#webManagedDisplayName').value='测试用户';
      document.querySelector('#webManagedPassword').value=${JSON.stringify(managedPassword)};
      document.querySelector('#webUserForm').requestSubmit(); true`);
    await until(`document.querySelector('#webUserList').textContent.includes(${JSON.stringify(managedUsername)})`);
    await evaluate(`document.querySelector('#webManagedDisplayName').value='<img src=x onerror=alert(1)> 测试用户';
      document.querySelector('#webUserForm').requestSubmit(); true`);
    await until("document.querySelector('#webUserList').textContent.includes('<img src=x onerror=alert(1)>')");
    if (await evaluate("!!document.querySelector('#webUserList img')")) throw new Error('User name became HTML');
    await evaluate("document.querySelector('#webManagedDisplayName').value='测试用户 · 独立档案';document.querySelector('#webUserForm').requestSubmit();true");
    await until("document.querySelector('#webUserList').textContent.includes('测试用户 · 独立档案')");
    await evaluate(`document.querySelector('#webResetSection').open=true;document.querySelector('#webResetPassword').value=${JSON.stringify(resetPassword)};
      document.querySelector('#webResetUserPassword').click(); true`);
    await confirmAccountAction();
    await until("document.querySelector('#toast').textContent.includes('密码已重置')");
    await evaluate("document.querySelector('#webManagedActive').checked=false;document.querySelector('#webUserForm').requestSubmit();true");
    await confirmAccountAction();
    await until("document.querySelector('#webUserList').textContent.includes('停用')");
    await evaluate("document.querySelector('#webManagedActive').checked=true;document.querySelector('#webUserForm').requestSubmit();true");
    await confirmAccountAction();
    await until("!document.querySelector('#webUserList').textContent.includes('停用')");
    await evaluate("document.querySelector('#webManagedDisplayName').value='影'.repeat(80);document.querySelector('#webUserForm').requestSubmit();true");
    await until("document.querySelector('#webUserList').textContent.includes('影'.repeat(80))");
    for (const width of [320,360,390]) {
      await send('Emulation.setDeviceMetricsOverride', {width,height:844,deviceScaleFactor:1,mobile:true});
      if (await evaluate("document.querySelector('#webUsersDialog .web-panel').scrollWidth>document.querySelector('#webUsersDialog .web-panel').clientWidth")) throw new Error(`Long-name editor overflows ${width}px viewport`);
    }
    await evaluate("document.querySelector('#webManagedDisplayName').value='测试用户 · 独立档案';document.querySelector('#webUserForm').requestSubmit();true");
    await until("document.querySelector('#webUserList').textContent.includes('测试用户 · 独立档案')");
    await screenshot('account-ui-user-editor-mobile');
    await evaluate("document.querySelector('#webManagedDisplayName').value='未保存的改动';document.querySelector('#webUserBack').click();true");
    await until("!!document.querySelector('.web-inline-confirm')");
    await evaluate("document.querySelector('.web-inline-confirm [data-no]').click();true");
    if (!await evaluate("document.querySelector('#webManagedDisplayName').value==='未保存的改动'&&document.querySelector('#webUsersDialog .web-panel').classList.contains('is-editing')")) throw new Error('Discard cancel lost unsaved changes');
    await evaluate("document.querySelector('#webUserBack').click();true");
    await confirmAccountAction();
    await until("!document.querySelector('#webUsersDialog .web-panel').classList.contains('is-editing')");
    await evaluate("document.querySelector('#webUserSearch').value='no-such-user';document.querySelector('#webUserSearch').dispatchEvent(new Event('input'));true");
    if (!await evaluate("document.querySelector('#webUserList').textContent.includes('没有符合条件')")) throw new Error('User search empty state missing');
    await evaluate("document.querySelector('#webUserSearch').value='';document.querySelector('#webUserSearch').dispatchEvent(new Event('input'));document.querySelector('#webUserRoleFilter').value='user';document.querySelector('#webUserRoleFilter').dispatchEvent(new Event('change'));true");
    if (await evaluate("document.querySelectorAll('#webUserList button').length") !== 1) throw new Error('Role filter failed');
    await evaluate("document.querySelector('#webUserRoleFilter').value='all';document.querySelector('#webUserRoleFilter').dispatchEvent(new Event('change'));true");
    await evaluate("document.querySelector('#webUserStatusFilter').value='inactive';document.querySelector('#webUserStatusFilter').dispatchEvent(new Event('change'));true");
    if (await evaluate("document.querySelectorAll('#webUserList button').length") !== 0) throw new Error('Status filter failed');
    await evaluate("document.querySelector('#webUserStatusFilter').value='all';document.querySelector('#webUserStatusFilter').dispatchEvent(new Event('change'));true");
    const labelWidth = await evaluate("document.querySelector('#webUserList .web-user-label').getBoundingClientRect().width");
    if (labelWidth < 120) throw new Error(`User label was squeezed: ${labelWidth}px`);
    await evaluate("document.querySelector('#webUsersDialog .setup-card').scrollTop=0;true");
    if (await evaluate('document.documentElement.scrollWidth') > 390) throw new Error('User management overflows mobile viewport');
    await screenshot('account-ui-user-list-mobile');
    for (const width of [320, 360, 768]) {
      await send('Emulation.setDeviceMetricsOverride', {width, height: 844, deviceScaleFactor: 1, mobile: width < 700});
      if (await evaluate('document.documentElement.scrollWidth') > width) throw new Error(`User management overflows ${width}px viewport`);
      if (await evaluate("document.querySelector('#webUsersDialog .web-panel').scrollWidth>document.querySelector('#webUsersDialog .web-panel').clientWidth")) throw new Error(`Account panel overflows ${width}px viewport`);
    }
    await send('Emulation.setDeviceMetricsOverride', {width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false});
    await evaluate(`document.querySelector('#webUserList button[data-user-id]').click();true`);
    await screenshot('account-ui-users-desktop');
    await evaluate("document.querySelector('#webUsersDialog [data-close]').click(); true");
    await until("!document.querySelector('#webUsersDialog')");
    if (await evaluate("document.querySelector('.app-wrapper').inert||document.body.style.overflow==='hidden'")) throw new Error('Closing dialog did not restore page interaction');
    await send('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
  }
  const pngExport = await evaluate(`(async () => {
    const canvas=document.createElement('canvas');canvas.width=2;canvas.height=2;
    const oldCreate=URL.createObjectURL,oldClick=HTMLAnchorElement.prototype.click;
    let exported;
    URL.createObjectURL=blob=>{exported=blob;return oldCreate.call(URL,blob);};
    HTMLAnchorElement.prototype.click=function(){};
    try {
      const response=await window.MovieReviewWeb.export_graph_image(canvas.toDataURL('image/png'),'电影海报墙');
      const bytes=new Uint8Array(await exported.arrayBuffer());
      let rejectsInvalid=false;
      try{await window.MovieReviewWeb.export_graph_image('data:text/html;base64,PHNjcmlwdD4=','电影海报墙');}catch{rejectsInvalid=true;}
      return response.ok&&exported.type==='image/png'&&bytes[0]===137&&bytes[1]===80&&rejectsInvalid;
    } finally {URL.createObjectURL=oldCreate;HTMLAnchorElement.prototype.click=oldClick;}
  })()`);
  if (pngExport !== true) throw new Error('PNG export failed under browser security policy');
  await send('Page.reload', {ignoreCache: true});
  await pause(300);
  await until(`document.querySelector('#setupOverlay')?.classList.contains('hidden')&&reviews.some(review=>review.title===${JSON.stringify(title)})`);
  await evaluate('window.MovieReviewWeb.logout(); true');
  await until("!!document.querySelector('#webUsername')&&reviews.length===0");
  if (checkUsers) {
    await evaluate(`document.querySelector('#webUsername').value=${JSON.stringify(managedUsername)};
      document.querySelector('#webPassword').value=${JSON.stringify(resetPassword)};
      document.querySelector('#webUsername').closest('form').requestSubmit(); true`);
    await until("document.querySelector('#setupOverlay').classList.contains('hidden')");
    if (await evaluate("reviews.length!==0||!!document.querySelector('#webUsersButton')")) throw new Error('Ordinary user sees another archive or admin controls');
    await evaluate("document.querySelector('#webAccountButton').click();true");
    if (await evaluate("!!document.querySelector('#webUsersButton')")) throw new Error('Ordinary account menu contains admin entry');
    await evaluate("document.querySelector('#webProfileButton').click();true");
    if (await evaluate("!!document.querySelector('#webProfileDialog [data-action=users]')")) throw new Error('Ordinary profile contains admin entry');
    await evaluate("document.querySelector('#webProfileDialog [data-action=security]').click();true");
    await screenshot('account-ui-security-mobile');
    await evaluate(`document.querySelector('#webCurrentPassword').value=${JSON.stringify(resetPassword)};
      document.querySelector('#webNewPassword').value='qa-own-updated-password';
      document.querySelector('#webConfirmPassword').value='qa-own-updated-password';
      document.querySelector('#webOwnPasswordForm').requestSubmit(); true`);
    await until("!!document.querySelector('#webUsername')");
    await evaluate(`document.querySelector('#webUsername').value=${JSON.stringify(managedUsername)};
      document.querySelector('#webPassword').value='qa-own-updated-password';
      document.querySelector('#webUsername').closest('form').requestSubmit(); true`);
    await until("document.querySelector('#setupOverlay').classList.contains('hidden')");
    if (await evaluate('reviews.length') !== 0) throw new Error('Archive changed after password update');
  }
  if (exceptions.length) throw new Error(`Browser errors: ${exceptions.join('; ')}`);
  console.log(JSON.stringify({ok: true, loginWidth, appWidth, beijingDate, saved: true, retryDeduplicated: true, reload: true, logout: true, pngExport, userManagement: checkUsers, profileEdit: checkUsers, searchAndFilters: checkUsers, discardConfirmation: checkUsers, keyboardAndFocus: checkUsers, directoryRetry: checkUsers, longDisplayNames: checkUsers, responsiveWidths: [320,360,390,768,1440], passwordChange: checkUsers}));
} finally {
  socket?.close();
  chrome.kill();
}
