/* Browser adapter for the existing archive interface. Loaded only by web.main. */
window.MOVIE_REVIEW_WEB = true;
window.MovieReviewWeb = (() => {
  let csrf = '';
  let baseReviews = [];
  let revisions = new Map();
  let archiveRevision = 0;
  const createKeys = new Map();
  let loginPromise = null;
  let currentUser = null;
  let activeAccountDialog = null;
  let closeAccountMenu = () => {};
  document.body.classList.add('web-mode');

  const iconPaths = {
    user: '<circle cx="12" cy="8" r="3.5"/><path d="M5 21v-2a7 7 0 0 1 14 0v2"/>',
    users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.87M16 3a4 4 0 0 1 0 8"/><circle cx="9" cy="7" r="4"/>',
    lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/>',
    film: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 8h4m10 0h4M3 16h4m10 0h4"/>',
    chart: '<path d="M4 4v16h16M8 16v-5m5 5V7m5 9V4"/>',
    download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
    logout: '<path d="M9 5H4v14h5m5-14 7 7-7 7m-5-7h12"/>',
    chevron: '<path d="m9 5 7 7-7 7"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    back: '<path d="m14 5-7 7 7 7"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    plus: '<path d="M12 5v14M5 12h14"/>'
  };
  const icon = name => `<svg class="web-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${iconPaths[name] || iconPaths.user}</svg>`;
  function safeText(value) {
    const span = document.createElement('span'); span.textContent = String(value ?? ''); return span.innerHTML;
  }
  const avatar = (name, large = false) => `<span class="web-avatar${large ? ' large' : ''}" aria-hidden="true">${safeText(Array.from(name || '影')[0])}</span>`;
  const dateLabel = value => value ? new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date(value * 1000)) : '—';
  function passwordField(id, label, autocomplete = 'new-password', required = true) {
    return `<div class="web-field"><label class="form-label" for="${id}">${label}</label>
      <div class="web-password-input"><input class="form-input" id="${id}" type="password" autocomplete="${autocomplete}" ${required ? 'required' : ''} ${required && autocomplete === 'new-password' ? 'minlength="12"' : ''} maxlength="1024">
        <button type="button" class="web-password-toggle" data-password="${id}" aria-label="显示${label}" aria-pressed="false">${icon('eye')}</button></div></div>`;
  }
  function bindPasswordToggles(panel) {
    panel.querySelectorAll('[data-password]').forEach(button => button.onclick = () => {
      const input = panel.querySelector('#' + button.dataset.password);
      const showing = input.type === 'password'; input.type = showing ? 'text' : 'password';
      button.setAttribute('aria-pressed', String(showing));
      button.setAttribute('aria-label', (showing ? '隐藏' : '显示') + panel.querySelector(`label[for="${input.id}"]`).textContent);
    });
  }
  function trapFocus(panel, onEscape) {
    panel.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.stopPropagation(); event.preventDefault(); onEscape?.(); return; }
      if (event.key !== 'Tab') return;
      const items = [...panel.querySelectorAll('button,input,select,textarea,a[href],summary,[tabindex="0"]')]
        .filter(item => !item.disabled && item.getClientRects().length && !item.closest('[hidden]'));
      if (!items.length) { event.preventDefault(); return; }
      const first = items[0], last = items.at(-1);
      if (event.shiftKey && (document.activeElement === first || !panel.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !panel.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
    });
  }

  function showLogin() {
    if (loginPromise) return loginPromise;
    closeAccountMenu();
    const overlay = document.createElement('div');
    overlay.className = 'setup-overlay web-login';
    overlay.setAttribute('role', 'dialog'); overlay.setAttribute('aria-modal', 'true'); overlay.setAttribute('aria-labelledby', 'webLoginTitle');
    overlay.innerHTML = `<div class="web-login-card">
      <aside class="web-login-story"><div class="web-login-brand">${icon('film')} 我的影评记录</div>
        <div class="web-film-art" aria-hidden="true"><span></span><span></span><span></span></div>
        <div class="web-login-quote"><div class="web-eyebrow">YOUR LIFE, IN FILMS</div><h2>让每一部电影，<br>留在你的故事里。</h2><p>记录银幕上的相遇，也收藏生活中的自己。属于你的私人电影档案。</p></div>
        <div class="web-login-foot">A PERSONAL CINEMA ARCHIVE</div></aside>
      <form class="web-login-form"><h2 id="webLoginTitle">欢迎回来</h2><p>登录，继续收藏你的银幕记忆。</p>
        <div class="web-field"><label class="form-label" for="webUsername">账户名</label><input class="form-input" id="webUsername" autocomplete="username" placeholder="输入你的账户名" maxlength="80" required></div>
        ${passwordField('webPassword', '密码', 'current-password')}
        <p class="setup-error" role="alert"></p><button class="btn btn-primary web-login-submit" type="submit">登录我的档案 ${icon('chevron')}</button>
        <div class="web-login-caption">${icon('lock')} 私人档案 · 仅你可见</div>
        <small class="web-field-help">首次使用或忘记密码？请联系管理员开通账户或重置密码。</small>
      </form></div>`;
    document.body.append(overlay);
    const previousOverflow = document.body.style.overflow; document.body.style.overflow = 'hidden';
    const wrapper = document.querySelector('.app-wrapper'); const previousInert = wrapper.inert; wrapper.inert = true;
    trapFocus(overlay); bindPasswordToggles(overlay);
    overlay.querySelector('#webUsername').focus();
    loginPromise = new Promise(resolve => {
      const form = overlay.querySelector('form');
      form.addEventListener('submit', async event => {
        event.preventDefault();
        const button = form.querySelector('[type="submit"]');
        button.disabled = true;
        button.textContent = '正在登录…';
        form.querySelector('.setup-error').textContent = '';
        try {
          const response = await fetch('/api/auth/login', {
            method: 'POST', credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({username: form.querySelector('#webUsername').value,
                                  password: form.querySelector('#webPassword').value})
          });
          const result = await response.json();
          if (!response.ok) throw new Error(result.detail || '登录失败');
          if (currentUser && currentUser.id !== result.user.id) {
            window.location.reload();
            return;
          }
          csrf = result.csrfToken;
          currentUser = result.user;
          form.reset();
          overlay.remove();
          document.body.style.overflow = previousOverflow; wrapper.inert = previousInert;
          loginPromise = null;
          resolve();
        } catch (error) {
          form.querySelector('.setup-error').textContent = error.message || '登录失败';
        } finally { button.disabled = false; button.innerHTML = `登录我的档案 ${icon('chevron')}`; }
      });
    });
    return loginPromise;
  }

  async function ensureAuth() {
    if (csrf) return;
    try {
      const response = await fetch('/api/auth/me', {credentials: 'same-origin'});
      if (response.ok) {
        const identity = await response.json();
        csrf = identity.csrfToken; currentUser = identity.user;
        return;
      }
    } catch (_) { /* The login dialog keeps the browser on this page. */ }
    await showLogin();
  }

  async function request(path, options = {}) {
    await ensureAuth();
    const method = options.method || 'GET';
    const headers = {...(options.headers || {})};
    if (method !== 'GET' && method !== 'HEAD') headers['X-CSRF-Token'] = csrf;
    let response;
    try {
      response = await fetch(path, {...options, headers, credentials: 'same-origin'});
    } catch (_) { throw new Error('网络连接中断。当前编辑内容仍在页面中，请恢复网络后重试。'); }
    if (response.status === 401) {
      csrf = '';
      showLogin();
      throw new Error('登录已失效。请登录后重试；当前编辑内容仍在页面中。');
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const error = new Error(body.detail || `请求失败（${response.status}）`);
      error.status = response.status;
      if (response.status === 409 && /\/api\/(reviews|archive|imports)/.test(path)) showConflict(error.message);
      throw error;
    }
    return response;
  }

  async function json(path, method, body) {
    const response = await request(path, {method, headers: {'Content-Type': 'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body)});
    return response.json();
  }

  function acceptSnapshot(snapshot) {
    baseReviews = snapshot.reviews.map(item => item.review);
    revisions = new Map(snapshot.reviews.map(item => [item.review.id, item.revision]));
    archiveRevision = snapshot.archiveRevision;
    return baseReviews;
  }

  async function refresh() {
    return acceptSnapshot(await (await request('/api/reviews')).json());
  }

  function showConflict(message) {
    const previous = document.getElementById('webConflictNotice');
    if (previous) previous.remove();
    const panel = document.createElement('div');
    panel.id = 'webConflictNotice';
    panel.className = 'setup-overlay';
    panel.style.zIndex = '9998';
    panel.innerHTML = `<div class="setup-card" style="max-width:460px;width:calc(100% - 32px)">
      <h2>保存冲突</h2><p class="setup-desc"></p>
      <p class="setup-desc">页面上的编辑内容已保留。你可以先复制文字，再查看服务器最新版本。</p>
      <button class="btn btn-primary" type="button" data-action="reload">查看服务器版本</button>
      <button class="btn btn-ghost" type="button" data-action="close">继续编辑</button>
    </div>`;
    panel.querySelector('.setup-desc').textContent = message;
    panel.querySelector('[data-action="close"]').onclick = () => panel.remove();
    panel.querySelector('[data-action="reload"]').onclick = async () => {
      try { reviews = await refresh(); renderAll(); closeModal(); panel.remove(); }
      catch (error) { showToast(error.message, 'error'); }
    };
    document.body.append(panel);
  }

  async function get_state() {
    const current = await refresh();
    return {ok: true, reviews: current, dataFile: '服务器档案', dataDirectory: '', error: '', user: currentUser};
  }

  async function save_reviews(next) {
    const before = new Map(baseReviews.map(item => [item.id, item]));
    const after = new Map(next.map(item => [item.id, item]));
    const added = next.filter(item => !before.has(item.id));
    const changed = next.filter(item => before.has(item.id) && JSON.stringify(item) !== JSON.stringify(before.get(item.id)));
    const removed = baseReviews.filter(item => !after.has(item.id));
    const count = added.length + changed.length + removed.length;
    if (count === 0) return {ok: true, reviews: baseReviews, dataFile: '服务器档案'};
    if (count === 1) {
      if (added.length) {
        if (!createKeys.has(added[0].id)) createKeys.set(added[0].id, crypto.randomUUID());
        await json('/api/reviews', 'POST', {review: added[0], requestKey: createKeys.get(added[0].id)});
      }
      else if (changed.length) await json('/api/reviews/' + encodeURIComponent(changed[0].id), 'PUT',
        {review: changed[0], revision: revisions.get(changed[0].id)});
      else await json('/api/reviews/' + encodeURIComponent(removed[0].id) + '?revision=' + revisions.get(removed[0].id), 'DELETE');
    } else {
      await json('/api/archive/reviews', 'PUT', {reviews: next, archiveRevision});
    }
    const saved = await refresh();
    added.forEach(item => createKeys.delete(item.id));
    return {ok: true, reviews: saved, dataFile: '服务器档案', error: ''};
  }

  async function loadImageObjectUrl(image, onProgress, isCurrent, thumbnail) {
    const name = image.path.split('/').at(-1);
    const path = '/api/media/' + encodeURIComponent(name) + (thumbnail ? '/thumbnail' : '');
    const response = await request(path);
    const blob = await response.blob();
    if (!isCurrent()) return '';
    onProgress(100);
    return URL.createObjectURL(blob);
  }

  async function uploadFile(file) {
    if (!file) return {ok: false, cancelled: true};
    if (file.size > 50 * 1024 * 1024) throw new Error('剧照不能超过 50 MB');
    if (!/\.(jpe?g|png|webp)$/i.test(file.name)) throw new Error('仅支持 JPG、PNG、WebP；HEIC 暂不支持');
    await ensureAuth();
    const progress = document.getElementById('formImageProgress');
    const body = new FormData(); body.append('file', file);
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/media');
      xhr.withCredentials = true;
      xhr.setRequestHeader('X-CSRF-Token', csrf);
      xhr.upload.onprogress = event => {
        if (event.lengthComputable) progress.textContent = `正在上传 ${Math.round(event.loaded / event.total * 100)}%`;
      };
      xhr.onerror = () => reject(new Error('上传中断，请检查网络后重试'));
      xhr.onload = () => {
        const result = JSON.parse(xhr.responseText || '{}');
        if (xhr.status >= 200 && xhr.status < 300) resolve(result);
        else reject(new Error(result.detail || '上传失败'));
      };
      xhr.send(body);
    });
  }

  async function select_review_image() {
    const picker = document.createElement('input');
    picker.type = 'file'; picker.accept = '.jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp';
    return new Promise(resolve => {
      picker.onchange = async () => {
        try { resolve(await uploadFile(picker.files[0])); }
        catch (error) { resolve({ok: false, error: error.message}); }
      };
      picker.click();
      window.addEventListener('focus', () => setTimeout(() => { if (!picker.files?.length) resolve({ok: false, cancelled: true}); }, 300), {once: true});
    });
  }

  function bindImageDrop(picker) {
    picker.addEventListener('dragover', event => { event.preventDefault(); picker.classList.add('is-drag-over'); });
    picker.addEventListener('dragleave', () => picker.classList.remove('is-drag-over'));
    picker.addEventListener('drop', async event => {
      event.preventDefault(); picker.classList.remove('is-drag-over');
      const file = event.dataTransfer?.files?.[0]; if (!file) return;
      setImagePickerBusy(true, '正在上传剧照…');
      try { const result = await uploadFile(file); await setFormImage(result.image); }
      catch (error) { showToast(error.message, 'error'); }
      finally { setImagePickerBusy(false); }
    });
    document.addEventListener('dragover', event => event.preventDefault());
    document.addEventListener('drop', event => event.preventDefault());
  }

  function download(blob, name) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url; link.download = name;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  async function export_backup() {
    const response = await request('/api/exports/backup');
    download(await response.blob(), 'movie-reviews-backup.zip');
    return {ok: true};
  }

  async function export_annual_report(filename, html) {
    download(new Blob([html], {type: 'text/html;charset=utf-8'}), filename);
    return {ok: true};
  }

  async function export_graph_image(dataUrl, title) {
    const prefix = 'data:image/png;base64,';
    if (typeof dataUrl !== 'string' || !dataUrl.startsWith(prefix) || dataUrl.length > 70_000_000 ||
        !['电影知识图谱', '电影海报墙'].includes(title)) throw new Error('图谱图片数据无效或过大');
    const encoded = dataUrl.slice(prefix.length);
    if (!encoded || encoded.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)) throw new Error('图谱图片编码无效');
    let binary;
    try { binary = atob(encoded); } catch (_) { throw new Error('图谱图片编码无效'); }
    const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
    const signature = [137, 80, 78, 71, 13, 10, 26, 10];
    if (signature.some((byte, index) => bytes[index] !== byte)) throw new Error('图谱图片不是有效 PNG');
    const blob = new Blob([bytes], {type: 'image/png'});
    download(blob, `${title}.png`);
    return {ok: true};
  }

  async function export_interactive_graph(graph) {
    const response = await request('/api/exports/graph', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(graph)});
    download(await response.blob(), '互动电影关系图.html');
    return {ok: true};
  }

  async function select_import_file() {
    const picker = document.createElement('input'); picker.type = 'file'; picker.accept = '.zip,.json';
    return new Promise(resolve => {
      picker.onchange = async () => {
        const file = picker.files[0];
        if (!file) { resolve({ok: false, cancelled: true}); return; }
        if (file.size > 200 * 1024 * 1024) { resolve({ok: false, error: '备份不能超过 200 MB'}); return; }
        try {
          await ensureAuth();
          const body = new FormData(); body.append('file', file);
          const response = await request('/api/imports/preview', {method: 'POST', body});
          resolve(await response.json());
        } catch (error) { resolve({ok: false, error: error.message}); }
      };
      picker.click();
      window.addEventListener('focus', () => setTimeout(() => { if (!picker.files?.length) resolve({ok: false, cancelled: true}); }, 300), {once: true});
    });
  }

  async function apply_import(token) {
    const result = await json('/api/imports/confirm', 'POST', {importToken: token});
    return {ok: true, reviews: await refresh(), dataFile: '服务器档案'};
  }

  async function logout() {
    try {
      await json('/api/auth/logout', 'POST');
      window.location.reload();
    } catch (error) { showToast(error.message, 'error'); }
  }

  function renderAccountStatus(element) {
    closeAccountMenu();
    element.replaceChildren();
    const dot = document.createElement('span'); dot.className = 'storage-dot';
    const text = document.createElement('span'); text.className = 'storage-status-text';
    text.textContent = '个人电影档案 · 仅你可见';
    element.append(dot, text);
    let trigger = document.getElementById('webAccountButton');
    if (!trigger) {
      trigger = document.createElement('button'); trigger.type = 'button'; trigger.id = 'webAccountButton';
      trigger.className = 'web-account-trigger';
      document.querySelector('.app-header').insertBefore(trigger, document.querySelector('.header-actions'));
    }
    trigger.innerHTML = `${avatar(currentUser?.displayName)}<span class="web-account-name">${safeText(currentUser?.displayName || '我的账户')}</span>${icon('down')}`;
    trigger.setAttribute('aria-label', `打开${currentUser?.displayName || '我的'}账户菜单`);
    trigger.setAttribute('aria-haspopup', 'menu'); trigger.setAttribute('aria-expanded', 'false');
    trigger.onclick = () => trigger.getAttribute('aria-expanded') === 'true' ? closeAccountMenu() : openAccountMenu(trigger);
  }

  function openAccountMenu(trigger) {
    closeAccountMenu();
    const menu = document.createElement('div'); menu.className = 'web-account-menu'; menu.id = 'webAccountMenu';
    menu.setAttribute('role', 'menu'); menu.setAttribute('aria-label', '我的账户');
    menu.innerHTML = `<div class="web-menu-identity">${avatar(currentUser.displayName)}<div><strong>${safeText(currentUser.displayName)}</strong><small>@${safeText(currentUser.username)}</small></div></div>`;
    const actions = [ ['个人中心', 'user', openAccount, 'webProfileButton'], ['账户安全', 'lock', openPassword, 'webPasswordButton'] ];
    if (currentUser.role === 'admin') actions.push(['用户管理', 'users', openUsers, 'webUsersButton']);
    actions.push(['退出登录', 'logout', logout, 'webLogoutButton']);
    actions.forEach(([label, name, action, id]) => {
      const button = document.createElement('button'); button.type = 'button'; button.id = id;
      button.className = 'web-menu-item' + (id === 'webLogoutButton' ? ' danger' : ''); button.setAttribute('role', 'menuitem');
      button.innerHTML = `${icon(name)}<span>${label}</span>`;
      button.onclick = () => { closeAccountMenu(); action(); }; menu.append(button);
    });
    document.body.append(menu); trigger.setAttribute('aria-expanded', 'true'); trigger.setAttribute('aria-controls', menu.id);
    const rect = trigger.getBoundingClientRect();
    menu.style.left = Math.max(12, Math.min(rect.right - 272, window.innerWidth - 284)) + 'px';
    menu.style.top = Math.max(12, Math.min(rect.bottom + 10, window.innerHeight - menu.offsetHeight - 12)) + 'px';
    const buttons = [...menu.querySelectorAll('button')]; buttons[0].focus();
    const outside = event => { if (!menu.contains(event.target) && !trigger.contains(event.target)) closeAccountMenu(false); };
    const dismiss = () => closeAccountMenu(false);
    document.addEventListener('pointerdown', outside); window.addEventListener('resize', dismiss); window.addEventListener('scroll', dismiss);
    menu.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.stopPropagation(); event.preventDefault(); closeAccountMenu(); }
      if (event.key === 'Tab') closeAccountMenu(false);
      if (['ArrowDown','ArrowUp','Home','End'].includes(event.key)) {
        event.preventDefault(); const index = buttons.indexOf(document.activeElement);
        buttons[event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length].focus();
      }
    });
    closeAccountMenu = (restore = true) => {
      menu.remove(); trigger.setAttribute('aria-expanded', 'false'); trigger.removeAttribute('aria-controls');
      document.removeEventListener('pointerdown', outside); window.removeEventListener('resize', dismiss); window.removeEventListener('scroll', dismiss);
      if (restore) trigger.focus(); closeAccountMenu = () => {};
    };
  }

  function accountDialog(id, title, html, variant = '') {
    closeAccountMenu(); activeAccountDialog?.close();
    const previousFocus = document.activeElement, previousOverflow = document.body.style.overflow;
    const wrapper = document.querySelector('.app-wrapper'), previousInert = wrapper.inert;
    const overlay = document.createElement('div'); overlay.id = id; overlay.className = 'setup-overlay web-account-dialog';
    overlay.style.zIndex = '3500'; overlay.setAttribute('role', 'dialog'); overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-labelledby', id + 'Title');
    overlay.innerHTML = `<div class="setup-card web-panel ${variant ? 'web-panel-' + variant : ''}">
      <header class="web-panel-header"><div><h2 id="${id}Title">${title}</h2></div><button type="button" class="web-close" data-close aria-label="关闭${title}">${icon('close')}</button></header>${html}</div>`;
    overlay.close = () => {
      if (!overlay.isConnected) return;
      overlay.remove(); document.body.style.overflow = previousOverflow; wrapper.inert = previousInert;
      if (activeAccountDialog === overlay) activeAccountDialog = null;
      if (previousFocus?.isConnected) previousFocus.focus(); else document.getElementById('webAccountButton')?.focus();
    };
    overlay.requestClose = () => overlay.close();
    overlay.querySelector('[data-close]').onclick = () => overlay.requestClose();
    overlay.addEventListener('click', event => { if (event.target === overlay) overlay.requestClose(); });
    trapFocus(overlay, () => overlay.requestClose());
    document.body.append(overlay);
    document.body.style.overflow = 'hidden'; wrapper.inert = true; activeAccountDialog = overlay;
    overlay.querySelector('[data-close]').focus(); bindPasswordToggles(overlay);
    return overlay;
  }

  function openAccount() {
    if (!currentUser) return;
    const records = typeof reviews === 'undefined' ? baseReviews : reviews;
    const rated = records.filter(item => item.rating > 0);
    const average = rated.length ? (rated.reduce((sum, item) => sum + item.rating, 0) / rated.length).toFixed(1) : '—';
    const rewatches = records.reduce((sum, item) => sum + (item.rewatches?.length || 0), 0);
    const panel = accountDialog('webProfileDialog', '个人中心', `<div class="web-profile-layout">
      <aside class="web-profile-sidebar"><div class="web-profile-identity">${avatar(currentUser.displayName, true)}<h3>${safeText(currentUser.displayName)}</h3><p>@${safeText(currentUser.username)}</p><span class="web-badge">${currentUser.role === 'admin' ? '管理员' : '电影记录者'}</span></div>
        <nav class="web-profile-nav" aria-label="个人中心"><button type="button" class="web-menu-item is-current" aria-current="page">${icon('film')} 我的档案</button>
          <button type="button" class="web-menu-item" data-action="security">${icon('lock')} 账户安全</button>
          ${currentUser.role === 'admin' ? `<button type="button" class="web-menu-item" data-action="users">${icon('users')} 用户管理</button>` : ''}
          <button type="button" class="web-menu-item danger" data-action="logout">${icon('logout')} 退出登录</button></nav></aside>
      <main class="web-profile-content"><div class="web-section-heading"><div><div class="web-eyebrow">MY CINEMA ARCHIVE</div><h3>你的银幕记忆</h3><p class="web-subtitle">每一次记录，都让电影与生活多一点联系。</p></div><button type="button" class="btn btn-ghost btn-sm" data-action="edit">编辑资料</button></div>
        <div class="web-metrics"><div class="web-metric"><strong>${records.length}</strong><span>已记录电影</span></div><div class="web-metric"><strong>${average}</strong><span>平均评分 / 5</span></div><div class="web-metric"><strong>${rewatches}</strong><span>重看次数</span></div></div>
        <h4 class="web-section-label">探索我的档案</h4><div class="web-profile-shortcuts">
          <button type="button" class="web-shortcut" data-action="taste">${icon('chart')}<span><strong>品味画像</strong><small>发现自己的观影偏好</small></span></button>
          <button type="button" class="web-shortcut" data-action="annual">${icon('film')}<span><strong>年度报告</strong><small>回顾一年的银幕时光</small></span></button>
          <button type="button" class="web-shortcut" data-action="export">${icon('download')}<span><strong>备份我的档案</strong><small>下载影评与原始剧照</small></span></button>
          <button type="button" class="web-shortcut" data-action="health">${icon('lock')}<span><strong>档案健康</strong><small>检查记录的完整程度</small></span></button></div>
        <h4 class="web-section-label">最近记录</h4><div id="webRecentReviews"></div>
        <div class="web-profile-footer">${icon('lock')} 私人档案 · 仅你可见 · ${dateLabel(currentUser.createdAt)} 加入</div></main></div>`);
    const actions = {security: openPassword, users: openUsers, logout, edit: openProfileEditor,
      taste: () => openTasteProfile(), annual: () => openAnnualReport(), export: () => handleExport(), health: () => openArchiveHealth()};
    panel.querySelectorAll('[data-action]').forEach(button => button.onclick = () => { panel.close(); actions[button.dataset.action](); });
    const list = panel.querySelector('#webRecentReviews');
    [...records].sort((a,b) => (b.date || '').localeCompare(a.date || '') || b.createdAt - a.createdAt).slice(0,3).forEach(review => {
      const button = document.createElement('button'); button.className = 'web-recent-row'; button.type = 'button';
      button.innerHTML = `<span><strong>${safeText(review.title)}</strong><small>${safeText(review.date || '未填写观影日期')}</small></span><span>${review.rating > 0 ? '★ ' + review.rating.toFixed(1) : '未评分'}</span>`;
      button.onclick = () => { panel.close(); openDetail(review.id); }; list.append(button);
    });
    if (!records.length) {
      list.innerHTML = '<div class="web-empty">还没有观影记录。<br>从第一篇影评开始，收藏你的银幕记忆。</div>';
      const button = document.createElement('button'); button.className = 'btn btn-primary'; button.textContent = '写第一篇影评';
      button.onclick = () => { panel.close(); openModal(); }; list.append(button);
    }
  }

  function openProfileEditor() {
    const panel = accountDialog('webProfileEditor', '编辑个人资料', `<form id="webProfileForm" class="web-form-body">
      <button type="button" class="web-back" data-back>${icon('back')} 返回个人中心</button>
      <div class="web-field"><label class="form-label" for="webOwnDisplayName">显示名称</label><input class="form-input" id="webOwnDisplayName" required maxlength="80"><small class="web-field-help">显示在头像入口和个人中心，最多 80 个字符。</small></div>
      <div class="web-field"><label class="form-label" for="webOwnUsername">登录账户名</label><input class="form-input" id="webOwnUsername" disabled><small class="web-field-help">如需调整登录账户，请联系管理员。</small></div>
      <p class="setup-error" role="alert"></p><div class="web-form-actions"><button type="button" class="btn btn-ghost" data-cancel>取消</button><button class="btn btn-primary" type="submit">保存资料</button></div></form>`, 'small');
    const form = panel.querySelector('form'); form.querySelector('#webOwnDisplayName').value = currentUser.displayName;
    form.querySelector('#webOwnUsername').value = currentUser.username;
    let busy = false;
    const back = () => { if (!busy) { panel.close(); openAccount(); } };
    panel.querySelector('[data-back]').onclick = back; panel.querySelector('[data-cancel]').onclick = back;
    panel.requestClose = () => { if (!busy) panel.close(); };
    form.onsubmit = async event => {
      event.preventDefault(); if (busy) return;
      const error = form.querySelector('.setup-error'), button = form.querySelector('[type="submit"]'); error.textContent = '';
      if (!form.querySelector('#webOwnDisplayName').value.trim()) { error.textContent = '请输入显示名称'; return; }
      busy = true; button.disabled = true; button.textContent = '正在保存…';
      try {
        const result = await json('/api/auth/profile', 'PUT', {displayName: form.querySelector('#webOwnDisplayName').value});
        currentUser = result.user; renderAccountStatus(document.getElementById('storageStatus'));
        panel.close(); openAccount(); showToast('个人资料已更新');
      } catch (failure) { error.textContent = failure.message; }
      finally { busy = false; button.disabled = false; button.textContent = '保存资料'; }
    };
    form.querySelector('#webOwnDisplayName').focus();
  }

  function openPassword() {
    const panel = accountDialog('webPasswordDialog', '账户安全', `<form id="webOwnPasswordForm" class="web-form-body">
        <button type="button" class="web-back" data-back>${icon('back')} 返回个人中心</button>
        <div class="web-notice">修改密码后，所有设备都需要重新登录。</div>
        ${passwordField('webCurrentPassword', '当前密码', 'current-password')}
        ${passwordField('webNewPassword', '新密码')}
        <small class="web-field-help" style="margin:-10px 0 18px">至少 12 个字符，建议结合字母、数字与符号。</small>
        ${passwordField('webConfirmPassword', '确认新密码')}
        <p class="setup-error" role="alert"></p><div class="web-form-actions"><button class="btn btn-primary" type="submit">更新密码</button></div>
      </form>`, 'small');
    const form = panel.querySelector('form');
    let busy = false;
    panel.requestClose = () => { if (!busy) panel.close(); };
    panel.querySelector('[data-back]').onclick = () => { if (!busy) { panel.close(); openAccount(); } };
    form.onsubmit = async event => {
      event.preventDefault(); if (busy) return;
      const error = form.querySelector('.setup-error'); error.textContent = '';
      if (form.querySelector('#webNewPassword').value !== form.querySelector('#webConfirmPassword').value) {
        error.textContent = '两次新密码不同'; return;
      }
      const button = form.querySelector('[type="submit"]'); button.disabled = true; busy = true; button.textContent = '正在更新…';
      try {
        await json('/api/auth/password', 'POST', {currentPassword: form.querySelector('#webCurrentPassword').value,
          password: form.querySelector('#webNewPassword').value});
        form.reset(); window.location.reload();
      } catch (failure) { error.textContent = failure.message; }
      finally { button.disabled = false; busy = false; button.textContent = '更新密码'; }
    };
    form.querySelector('input').focus();
  }

  async function openUsers() {
    if (currentUser?.role !== 'admin') return;
    const panel = accountDialog('webUsersDialog', '用户管理', `<div class="web-users-summary">
      <span><strong id="webTotalUsers">—</strong>位用户</span><span><strong id="webActiveUsers">—</strong>已启用</span><span><strong id="webAdminUsers">—</strong>管理员</span>
      <button type="button" class="btn btn-primary" id="webAddUser">${icon('plus')} 新增用户</button></div>
      <div class="web-users-layout"><section class="web-user-directory" aria-label="用户目录">
        <div class="web-search">${icon('search')}<input class="form-input" id="webUserSearch" type="search" placeholder="搜索名称或账户" aria-label="搜索用户"></div>
        <div class="web-user-filters"><select id="webUserRoleFilter" aria-label="按角色筛选"><option value="all">全部角色</option><option value="admin">管理员</option><option value="user">普通用户</option></select>
          <select id="webUserStatusFilter" aria-label="按状态筛选"><option value="all">全部状态</option><option value="active">已启用</option><option value="inactive">已停用</option></select></div>
        <div id="webUserList" class="web-user-list" aria-live="polite"><div class="web-empty">正在加载用户…</div></div>
        <p class="web-directory-note" id="webUserListNote">每位用户拥有独立的电影档案。</p><p class="setup-error" id="webUserListError" role="alert"></p>
        <button class="btn btn-ghost btn-sm" type="button" id="webRetryUsers" hidden>重新加载</button></section>
      <section class="web-user-editor" aria-label="用户资料编辑"><div id="webUserPlaceholder" class="web-editor-placeholder">${icon('users')}<h3>选择一位电影记录者</h3><p>从左侧选择用户查看资料，或创建一个新的私人电影档案。</p></div>
        <div id="webUserEditor" hidden><button type="button" class="web-back web-mobile-back" id="webUserBack">${icon('back')} 返回用户列表</button>
          <div class="web-editor-identity"><div id="webEditorAvatar">${avatar('新')}</div><div><h3 id="webUserEditorTitle">新增用户</h3><p id="webUserEditorNote">为电影记录者创建独立档案</p></div></div>
          <form id="webUserForm" autocomplete="off">
            <div class="web-field"><label class="form-label" for="webManagedUsername">账户名</label><input class="form-input" id="webManagedUsername" required maxlength="80" placeholder="用于登录的账户名"></div>
            <div class="web-field"><label class="form-label" for="webManagedDisplayName">显示名称</label><input class="form-input" id="webManagedDisplayName" required maxlength="80" placeholder="个人中心显示的名称"></div>
            <div class="web-field"><label class="form-label" for="webManagedRole">用户角色</label><select class="form-input" id="webManagedRole"><option value="user">普通用户</option><option value="admin">管理员</option></select><small class="web-field-help">管理员可管理账户；每位用户仅可访问自己的档案。</small></div>
            <label class="web-toggle-row"><span><strong>启用账户</strong><small>停用后退出全部设备，保留已有档案。</small></span><input class="web-toggle" id="webManagedActive" type="checkbox" checked></label>
            <div id="webInitialPassword">${passwordField('webManagedPassword', '初始密码（至少 12 个字符）')}</div>
            <p class="setup-error" role="alert"></p><div class="web-form-actions"><button class="btn btn-ghost" type="button" id="webCancelUser">取消</button><button class="btn btn-primary" type="submit" id="webSaveUser">创建用户</button></div>
            <details class="web-reset-section" id="webResetSection" hidden><summary>重置登录密码</summary><p class="web-field-help">重置后，该用户所有设备均需重新登录。</p>
              ${passwordField('webResetPassword', '新密码（至少 12 个字符）', 'new-password', false)}<button class="btn btn-ghost" type="button" id="webResetUserPassword">重置密码</button></details>
          </form></div></section></div>`, 'users');
    const form = panel.querySelector('#webUserForm');
    const field = id => panel.querySelector('#' + id);
    const error = form.querySelector('.setup-error');
    const card = panel.querySelector('.web-panel');
    let selected = null, users = [], busy = false, baseline = '';
    const formValue = () => JSON.stringify([field('webManagedUsername').value, field('webManagedDisplayName').value,
      field('webManagedRole').value, field('webManagedActive').checked, field('webManagedPassword').value, field('webResetPassword').value]);
    const dirty = () => !field('webUserEditor').hidden && baseline !== formValue();
    function setBusy(value) {
      busy = value;
      panel.querySelectorAll('input,select,button,summary').forEach(control => {
        if (control.tagName !== 'SUMMARY') control.disabled = value;
      });
      if (!value) field('webManagedActive').disabled = !selected;
    }
    function confirmAction(message, label = '确认') {
      return new Promise(resolve => {
        const notice = document.createElement('section'); notice.className = 'web-inline-confirm'; notice.setAttribute('role','alertdialog'); notice.setAttribute('aria-modal','false');
        notice.innerHTML = `<p id="webConfirmMessage">${safeText(message)}</p><div class="web-form-actions"><button type="button" class="btn btn-ghost" data-no>取消</button><button type="button" class="btn btn-primary" data-yes>${label}</button></div>`;
        notice.setAttribute('aria-labelledby', 'webConfirmMessage'); panel.querySelector('.web-user-editor').append(notice);
        const finish = value => { notice.remove(); resolve(value); };
        notice.querySelector('[data-no]').onclick = () => finish(false); notice.querySelector('[data-yes]').onclick = () => finish(true);
        notice.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(false); } });
        notice.scrollIntoView({block:'nearest'}); notice.querySelector('[data-no]').focus();
      });
    }
    async function allowDiscard() {
      if (busy) return false;
      if (!dirty()) return true;
      setBusy(true);
      try { return await confirmAction('资料还未保存，是否放弃本次修改？', '放弃修改'); }
      finally { setBusy(false); }
    }
    const back = async () => {
      if (!await allowDiscard()) return;
      card.classList.remove('is-editing'); field('webUserEditor').hidden = true; field('webUserPlaceholder').hidden = false;
      selected = null; renderUsers(); field('webUserSearch').focus();
    };
    panel.requestClose = async () => { if (await allowDiscard()) panel.close(); };
    field('webUserBack').onclick = back; field('webCancelUser').onclick = back;
    function edit(user) {
      selected = user; form.reset(); error.textContent = '';
      field('webUserEditor').hidden = false; field('webUserPlaceholder').hidden = true; card.classList.add('is-editing');
      field('webUserEditorTitle').textContent = user ? '编辑用户资料' : '新增用户';
      field('webUserEditorNote').textContent = user ? `${user.id === currentUser.id ? '当前账户 · ' : ''}${dateLabel(user.createdAt)} 加入` : '为电影记录者创建独立档案';
      field('webEditorAvatar').innerHTML = avatar(user?.displayName || '新');
      field('webManagedUsername').value = user?.username || '';
      field('webManagedDisplayName').value = user?.displayName || '';
      field('webManagedRole').value = user?.role || 'user';
      field('webManagedActive').checked = user?.isActive ?? true;
      field('webManagedActive').disabled = !user;
      field('webManagedPassword').required = !user;
      field('webInitialPassword').hidden = !!user;
      field('webResetSection').hidden = !user; field('webResetSection').open = false;
      field('webSaveUser').textContent = user ? '保存用户' : '创建用户';
      baseline = formValue(); renderUsers();
      card.scrollTop = 0; field('webManagedUsername').focus();
    }
    function renderUsers() {
      field('webTotalUsers').textContent = users.length;
      field('webActiveUsers').textContent = users.filter(user => user.isActive).length;
      field('webAdminUsers').textContent = users.filter(user => user.role === 'admin').length;
      const query = field('webUserSearch').value.trim().toLocaleLowerCase();
      const filtered = users.filter(user => (!query || (user.displayName + ' ' + user.username).toLocaleLowerCase().includes(query)) &&
        (field('webUserRoleFilter').value === 'all' || user.role === field('webUserRoleFilter').value) &&
        (field('webUserStatusFilter').value === 'all' || user.isActive === (field('webUserStatusFilter').value === 'active')));
      const list = field('webUserList'); list.replaceChildren();
      for (const user of filtered) {
        const row = document.createElement('button'); row.type = 'button'; row.dataset.userId = String(user.id);
        row.className = 'web-user-row' + (selected?.id === user.id ? ' is-selected' : ''); row.disabled = busy;
        row.setAttribute('aria-label', `编辑 ${user.displayName}，${user.username}`); row.setAttribute('aria-pressed', String(selected?.id === user.id));
        row.innerHTML = `${avatar(user.displayName)}<span class="web-user-label"><strong>${safeText(user.displayName)}${user.id === currentUser.id ? ' <span class="web-badge">你</span>' : ''}</strong><small>@${safeText(user.username)}</small>
          <span class="web-user-badges"><span class="web-badge ${user.role === 'admin' ? 'admin' : ''}">${user.role === 'admin' ? '管理员' : '普通用户'}</span><span class="web-badge ${user.isActive ? 'active' : 'inactive'}">${user.isActive ? '已启用' : '已停用'}</span></span></span>${icon('chevron')}`;
        row.onclick = async () => { if (await allowDiscard()) edit(user); }; list.append(row);
      }
      if (!filtered.length) list.innerHTML = '<div class="web-empty">没有符合条件的用户<br>试试其他名称或筛选条件。</div>';
      field('webUserListNote').textContent = `显示 ${filtered.length} / ${users.length} 位用户 · 每人独立档案`;
    }
    async function loadUsers() {
      field('webUserListError').textContent = ''; field('webRetryUsers').hidden = true;
      if (!users.length) field('webUserList').innerHTML = '<div class="web-empty">正在加载用户…</div>';
      try { const result = await (await request('/api/users')).json(); users = result.users; renderUsers(); }
      catch (failure) {
        if (!users.length) field('webUserList').innerHTML = '<div class="web-empty">暂时无法加载用户</div>';
        field('webUserListError').textContent = failure.message; field('webRetryUsers').hidden = false; throw failure;
      }
    }
    field('webRetryUsers').onclick = () => { loadUsers().catch(() => {}); };
    field('webUserSearch').oninput = renderUsers; field('webUserRoleFilter').onchange = renderUsers; field('webUserStatusFilter').onchange = renderUsers;
    field('webAddUser').onclick = async () => { if (await allowDiscard()) edit(null); };
    form.onsubmit = async event => {
      event.preventDefault(); if (busy) return;
      const target = selected;
      const body = {username: field('webManagedUsername').value, displayName: field('webManagedDisplayName').value, role: field('webManagedRole').value};
      if (target) body.isActive = field('webManagedActive').checked;
      else body.password = field('webManagedPassword').value;
      setBusy(true); error.textContent = ''; field('webSaveUser').textContent = '正在保存…';
      try {
        if (target && (body.username !== target.username || body.role !== target.role || body.isActive !== target.isActive)) {
          if (!await confirmAction(`确认更新「${target.displayName}」的账户权限或登录信息？该用户所有设备将退出登录，已有档案会保留。`, '确认更新')) return;
        }
        const result = await json(target ? `/api/users/${target.id}` : '/api/users', target ? 'PUT' : 'POST', body);
        if (target?.id === currentUser.id) {
          if (result.user.username !== currentUser.username || result.user.role !== currentUser.role || !result.user.isActive) {
            window.location.reload(); return;
          }
          currentUser = result.user; renderAccountStatus(document.getElementById('storageStatus'));
        }
        edit(result.user); await loadUsers(); showToast(target ? '用户已保存' : '用户已创建');
      } catch (failure) { error.textContent = failure.message; }
      finally { setBusy(false); field('webSaveUser').textContent = selected ? '保存用户' : '创建用户'; }
    };
    field('webResetUserPassword').onclick = async () => {
      if (busy || !selected) return;
      const password = field('webResetPassword').value;
      if (password.length < 12) { error.textContent = '新密码至少需要 12 个字符'; return; }
      setBusy(true); error.textContent = '';
      try {
        if (!await confirmAction(`确认重置「${selected.displayName}」的密码？该用户所有设备都需要使用新密码重新登录。`, '确认重置')) return;
        await json(`/api/users/${selected.id}/password`, 'POST', {password});
        field('webResetPassword').value = ''; field('webResetSection').open = false;
        if (selected.id === currentUser.id) { window.location.reload(); return; }
        showToast('密码已重置，该用户需要重新登录');
      } catch (failure) { error.textContent = failure.message; }
      finally { setBusy(false); }
    };
    try { await loadUsers(); } catch (_) { /* The directory displays its own retry action. */ }
  }

  window.addEventListener('focus', async () => {
    if (!csrf || loginPromise) return;
    try {
      const latest = await (await request('/api/archive/version')).json();
      if (latest.archiveRevision === archiveRevision) return;
      const editing = ['modalOverlay', 'rewatchOverlay'].some(id =>
        document.getElementById(id)?.classList.contains('active'));
      if (editing) {
        showToast('其他设备已修改档案。当前编辑内容已保留，请保存时检查冲突。', 'error');
        return;
      }
      reviews = await refresh();
      renderAll();
      showToast('已更新其他设备保存的影评');
    } catch (_) { /* A failed version check must not erase the current page. */ }
  });
  window.addEventListener('offline', () => {
    if (typeof showToast === 'function') showToast('网络已断开。当前编辑内容仍在页面中。', 'error');
  });

  return {get_state, save_reviews, select_review_image, loadImageObjectUrl, bindImageDrop,
    export_backup, export_annual_report, export_graph_image, export_interactive_graph,
    select_import_file, apply_import, logout, renderAccountStatus, openAccount, openUsers, openPassword};
})();
