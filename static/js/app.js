(() => {
  const STATE_VERSION = 11;
  const initialQuery = new URLSearchParams(window.location.search);
  const requestedIntent = initialQuery.get('intent') || 'discover';
  const validIntents = ['discover', 'find', 'curate', 'buy', 'learn'];
  const initialIntent = requestedIntent === 'taste' ? 'discover' : (validIntents.includes(requestedIntent) ? requestedIntent : 'discover');

  const state = {
    profile: { loves: [], avoids: [], notes: '', discoveryLevel: 50, updatedAt: null },
    tasteDraft: null,
    saved: [],
    recent: [],
    purchases: [],
    collections: [],
    alerts: [],
    compare: [],
    pendingCollectionArtworkId: null,
    last: null,
    preferences: {
      theme: (() => {
        try { return (window.localStorage.getItem('vincentThemePreference') ?? window.localStorage.getItem('artcoThemePreference')) === 'dark' ? 'dark' : 'light'; }
        catch (_) { return 'light'; }
      })(),
      fontScale: 'md',
      regionScope: 'CA',
    },
    intent: initialIntent,
    personalizeExplore: requestedIntent === 'taste' || initialQuery.get('personalize_taste') === '1',
    inspiration: { images: [], preparing: false },
    searchRunActive: false,
    user: null,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const INSPIRATION_DB_NAME = 'artCoInspirationDB';
  const INSPIRATION_STORE = 'images'; // deleted by the v3 migration; never used for writes
  const INSPIRATION_SETTINGS_STORE = 'settings';
  const MAX_REQUEST_IMAGES = 5;
  const MAX_UPLOAD_BYTES = 12 * 1024 * 1024;
  const MAX_COMPRESSED_BYTES = 360 * 1024;
  const activeInspirationUrls = new Set();
  let inspirationDbPromise = null;

  function openInspirationDb() {
    if (!('indexedDB' in window)) return Promise.reject(new Error('This browser does not support local image storage.'));
    if (inspirationDbPromise) return inspirationDbPromise;
    inspirationDbPromise = new Promise((resolve, reject) => {
      const request = window.indexedDB.open(INSPIRATION_DB_NAME, 3);
      request.onupgradeneeded = () => {
        const db = request.result;
        const transaction = request.transaction;
        // Migrate older Vincent builds by deleting the persistent inspiration-image
        // store and its selected-image setting. Images are now request-only.
        if (db.objectStoreNames.contains(INSPIRATION_STORE)) db.deleteObjectStore(INSPIRATION_STORE);
        if (!db.objectStoreNames.contains(INSPIRATION_SETTINGS_STORE)) {
          db.createObjectStore(INSPIRATION_SETTINGS_STORE, { keyPath: 'key' });
        } else if (transaction) {
          transaction.objectStore(INSPIRATION_SETTINGS_STORE).delete('selectedIds');
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => { inspirationDbPromise = null; reject(new Error('Could not open local image storage.')); };
      request.onblocked = () => { inspirationDbPromise = null; reject(new Error('Close another Vincent tab to finish updating local image storage.')); };
    });
    return inspirationDbPromise;
  }

  async function getInspirationSetting(key, fallback = null) {
    const db = await openInspirationDb();
    return new Promise((resolve, reject) => {
      const request = db.transaction(INSPIRATION_SETTINGS_STORE, 'readonly').objectStore(INSPIRATION_SETTINGS_STORE).get(key);
      request.onsuccess = () => resolve(request.result ? request.result.value : fallback);
      request.onerror = () => reject(new Error('Could not read the inspiration settings.'));
    });
  }

  async function putInspirationSetting(key, value) {
    const db = await openInspirationDb();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(INSPIRATION_SETTINGS_STORE, 'readwrite');
      transaction.objectStore(INSPIRATION_SETTINGS_STORE).put({ key, value });
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(new Error('Could not save the inspiration settings.'));
    });
  }

  function appStateSnapshot() {
    return {
      version: STATE_VERSION,
      profile: state.profile,
      saved: state.saved,
      recent: state.recent,
      purchases: state.purchases,
      collections: state.collections,
      alerts: state.alerts,
      compare: state.compare,
      last: state.last,
      preferences: state.preferences,
    };
  }

  function persistAppState() {
    return putInspirationSetting('appState', appStateSnapshot()).catch((error) => {
      console.warn('Vincent could not persist app state in IndexedDB.', error);
    });
  }

  async function accountFetch(url, options = {}) {
    const response = await fetch(url, { credentials: 'same-origin', ...options, headers: { Accept: 'application/json', ...(options.headers || {}) } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.success === false) throw new Error(data.detail || `Account request failed (${response.status}).`);
    return data;
  }

  function updateAuthControls() {
    const link = $('#authLink');
    const logout = $('#logoutButton');
    const conciergeCta = $('#homeConciergeCta');
    const email = String(state.user?.email || '');
    const fallbackName = email.includes('@') ? email.split('@')[0].replace(/[._-]+/g, ' ') : 'there';
    const displayName = String(state.user?.name || fallbackName).trim().replace(/\b\w/g, (character) => character.toUpperCase()) || 'there';
    if (link) {
      link.textContent = state.user ? `Welcome, ${displayName}` : 'Sign in';
      link.href = state.user ? '/dashboard' : '/login?next=%2Fconcierge';
      link.title = state.user?.email || 'Sign in to Vincent';
    }
    if (logout) logout.hidden = !state.user;
    if (conciergeCta) conciergeCta.href = state.user ? '/concierge' : '/login?next=%2Fconcierge';
  }

  async function syncProfileToServer() {
    if (!state.user) return;
    await accountFetch('/api/account/profile', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ loves: state.profile.loves, avoids: state.profile.avoids, notes: state.profile.notes, discoveryLevel: state.profile.discoveryLevel }),
    });
  }

  async function syncSavedToServer(artwork, action = 'save') {
    if (!state.user || state.user.demo || !artwork?.id) return;
    const data = await accountFetch('/api/account/saved', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(action === 'remove' ? { action, artwork_id: artwork.id } : { action, artwork }),
    });
    if (Array.isArray(data.saved)) state.saved = data.saved;
    await persistAppState();
  }

  function makeId(prefix = 'vincent') {
    return `${prefix}-${(window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`}`;
  }

  async function syncCollectionState(collection, action = 'save', artwork = null) {
    if (!state.user || state.user.demo) return;
    let data;
    if (action === 'create') {
      data = await accountFetch('/api/account/collections', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(collection) });
    } else if (action === 'delete') {
      data = await accountFetch(`/api/account/collections/${encodeURIComponent(collection.id)}`, { method: 'DELETE' });
    } else if (action === 'add') {
      data = await accountFetch(`/api/account/collections/${encodeURIComponent(collection.id)}/items`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ artwork }) });
    } else if (action === 'remove') {
      data = await accountFetch(`/api/account/collections/${encodeURIComponent(collection.id)}/items/${encodeURIComponent(artwork.id)}`, { method: 'DELETE' });
    }
    if (Array.isArray(data?.collections)) state.collections = data.collections;
    await persistAppState();
  }

  async function syncAlertState(alert, action = 'create') {
    if (!state.user || state.user.demo) return;
    const data = action === 'delete'
      ? await accountFetch(`/api/account/alerts/${encodeURIComponent(alert.id)}`, { method: 'DELETE' })
      : await accountFetch('/api/account/alerts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(alert) });
    if (Array.isArray(data?.alerts)) state.alerts = data.alerts;
    await persistAppState();
  }

  async function hydrateAccountState() {
    try {
      const session = await accountFetch('/api/auth/me');
      state.user = session.authenticated ? session.user : null;
      updateAuthControls();
      if (!state.user) return;
      const data = await accountFetch('/api/account/state');
      const localProfile = cloneProfile(state.profile);
      const hasLocalProfile = localProfile.loves.length || localProfile.avoids.length || localProfile.notes.trim() || Number(localProfile.discoveryLevel) !== 50;
      if (data.profile && typeof data.profile === 'object') {
        state.profile = { ...cloneProfile(data.profile), updatedAt: data.profile_updated_at ? new Date(Number(data.profile_updated_at) * 1000).toISOString() : (state.profile.updatedAt || null) };
      } else if (hasLocalProfile) {
        await syncProfileToServer();
      }
      if (Array.isArray(data.saved) && !data.demo_mode) {
        if (data.saved.length) state.saved = data.saved;
        else if (state.saved.length) {
          for (const artwork of state.saved) {
            try { await syncSavedToServer(artwork, 'save'); } catch (_) { break; }
          }
        }
      }
      if (Array.isArray(data.recent) && data.recent.length) state.recent = data.recent;
      if (Array.isArray(data.purchases)) state.purchases = data.purchases;
      if (!data.demo_mode && Array.isArray(data.collections)) state.collections = data.collections;
      if (!data.demo_mode && Array.isArray(data.alerts)) state.alerts = data.alerts;
      state.tasteDraft = cloneProfile(state.profile);
      await persistAppState();
    } catch (error) {
      // Guest browsing remains available if account storage has not been configured.
      if (error?.message && !/Sign in|Account request failed \(401\)/i.test(error.message)) console.warn('Vincent account sync is unavailable.', error.message);
      state.user = null;
      updateAuthControls();
    }
  }

  async function initAuthPage() {
    const form = $('#authForm');
    if (!form) return;
    const message = $('#authMessage');
    const submit = $('#authSubmit');
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const email = String($('#authEmail')?.value || '').trim();
      const password = String($('#authPassword')?.value || '');
      const mode = form.dataset.mode === 'register' ? 'register' : 'login';
      if (submit) { submit.disabled = true; submit.textContent = mode === 'register' ? 'Creating account…' : 'Signing in…'; }
      if (message) message.textContent = '';
      try {
        await accountFetch(`/api/auth/${mode}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) });
        const params = new URLSearchParams(window.location.search);
        let next = params.get('next') || window.ART_AUTH_NEXT || '/concierge';
        if (next === '/') next = '/concierge';
        window.location.assign(next.startsWith('/') && !next.startsWith('//') ? next : '/concierge');
      } catch (error) {
        if (message) message.textContent = error.message || 'Account request failed.';
      } finally {
        if (submit) { submit.disabled = false; submit.innerHTML = `${mode === 'register' ? 'Create account' : 'Sign in'} <span aria-hidden="true">→</span>`; }
      }
    });
  }

  async function hydrateAppState() {
    try {
      const existing = await getInspirationSetting('appState', null);
      if (existing && typeof existing === 'object') {
        const profile = existing.profile || {};
        state.profile = {
          loves: Array.isArray(profile.loves) ? profile.loves.slice(0, 12) : [],
          avoids: Array.isArray(profile.avoids || profile.avoid_references) ? (profile.avoids || profile.avoid_references).slice(0, 12) : [],
          notes: String(profile.notes || ''),
          discoveryLevel: Number(profile.discoveryLevel ?? 50),
          updatedAt: profile.updatedAt || null,
        };
        state.saved = Array.isArray(existing.saved) ? existing.saved.slice(0, 40) : [];
        state.recent = Array.isArray(existing.recent) ? existing.recent.slice(0, 8) : [];
        state.purchases = Array.isArray(existing.purchases) ? existing.purchases.slice(0, 12) : [];
        state.collections = Array.isArray(existing.collections) ? existing.collections.slice(0, 30) : [];
        state.alerts = Array.isArray(existing.alerts) ? existing.alerts.slice(0, 50) : [];
        state.compare = Array.isArray(existing.compare) ? existing.compare.slice(0, 3) : [];
        state.last = existing.last || null;
        let immediateTheme = null;
        try { immediateTheme = (window.localStorage.getItem('vincentThemePreference') ?? window.localStorage.getItem('artcoThemePreference')); } catch (_) { /* storage may be blocked */ }
        state.preferences = {
          theme: immediateTheme === 'dark' || (immediateTheme !== 'light' && existing.preferences?.theme === 'dark') ? 'dark' : 'light',
          fontScale: ['sm', 'md', 'lg'].includes(existing.preferences?.fontScale) ? existing.preferences.fontScale : 'md',
          regionScope: ['CA', 'US', 'BOTH'].includes(existing.preferences?.regionScope) ? existing.preferences.regionScope : 'CA',
        };
      } else {
        // App profile/results persist locally; user-supplied inspiration images do not.
        await putInspirationSetting('appState', appStateSnapshot());
      }
      state.inspiration.images = [];
      state.inspiration.preparing = false;
    } catch (error) {
      // Continue with the in-memory defaults if browser storage is unavailable.
      // No server account or cross-device sync is implied.
      console.warn('Vincent could not load its browser library.', error);
    }
  }

  function clearInspirationObjectUrls() {
    activeInspirationUrls.forEach((url) => URL.revokeObjectURL(url));
    activeInspirationUrls.clear();
  }

  function imageObjectUrl(record) {
    const url = URL.createObjectURL(record.blob);
    activeInspirationUrls.add(url);
    return url;
  }

  function safeImageName(value) {
    const clean = String(value || 'Inspiration image').replace(/[\\/]+/g, ' ').trim();
    return clean.slice(0, 120) || 'Inspiration image';
  }

  function loadImageSource(file) {
    if (typeof window.createImageBitmap === 'function') {
      return window.createImageBitmap(file).then((bitmap) => ({
        image: bitmap,
        width: bitmap.width,
        height: bitmap.height,
        cleanup: () => bitmap.close?.(),
      }));
    }
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const image = new Image();
      image.onload = () => resolve({ image, width: image.naturalWidth, height: image.naturalHeight, cleanup: () => URL.revokeObjectURL(url) });
      image.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Vincent could not open that image. Try a JPEG, PNG, or WebP file.')); };
      image.src = url;
    });
  }

  function canvasToBlob(canvas, type, quality) {
    return new Promise((resolve, reject) => {
      canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error('This browser could not prepare the image for storage.')), type, quality);
    });
  }

  async function compressInspirationImage(file) {
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
      throw new Error(`${safeImageName(file.name)} is not a supported format. Use JPEG, PNG, or WebP.`);
    }
    if (file.size > MAX_UPLOAD_BYTES) throw new Error(`${safeImageName(file.name)} is larger than 12 MB.`);
    const source = await loadImageSource(file);
    try {
      if (!source.width || !source.height) throw new Error('Vincent could not read the image dimensions.');
      let bestBlob = null;
      const canvas = document.createElement('canvas');
      const maxDimensions = [1280, 1080, 900, 760];
      const qualities = [0.82, 0.74, 0.66, 0.58, 0.5];
      for (const edge of maxDimensions) {
        const ratio = Math.min(1, edge / Math.max(source.width, source.height));
        canvas.width = Math.max(1, Math.round(source.width * ratio));
        canvas.height = Math.max(1, Math.round(source.height * ratio));
        const context = canvas.getContext('2d', { alpha: false });
        if (!context) throw new Error('Image processing is not available in this browser.');
        context.fillStyle = '#ffffff';
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(source.image, 0, 0, canvas.width, canvas.height);
        for (const quality of qualities) {
          const blob = await canvasToBlob(canvas, 'image/jpeg', quality);
          bestBlob = blob;
          if (blob.size <= MAX_COMPRESSED_BYTES) return { blob, width: canvas.width, height: canvas.height };
        }
      }
      if (!bestBlob || bestBlob.size > MAX_COMPRESSED_BYTES) {
        throw new Error(`${safeImageName(file.name)} could not be compressed enough. Try a smaller image.`);
      }
      return { blob: bestBlob, width: canvas.width, height: canvas.height };
    } finally {
      source.cleanup?.();
    }
  }

  function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const value = String(reader.result || '');
        const comma = value.indexOf(',');
        if (comma < 0) reject(new Error('Could not prepare the image for visual analysis.'));
        else resolve(value.slice(comma + 1));
      };
      reader.onerror = () => reject(new Error('Could not read the saved image for this request.'));
      reader.readAsDataURL(blob);
    });
  }

  function saveProfile() {
    state.profile.updatedAt = new Date().toISOString();
    persistAppState();
    syncProfileToServer().catch((error) => console.warn('Could not sync taste profile to account.', error.message));
  }

  function cloneProfile(profile) {
    return {
      loves: [...(profile?.loves || [])],
      avoids: [...(profile?.avoids || [])],
      notes: String(profile?.notes || ''),
      discoveryLevel: Number(profile?.discoveryLevel ?? 50),
      updatedAt: profile?.updatedAt || null,
    };
  }

  function tasteComparable(profile) {
    return JSON.stringify({
      loves: [...(profile?.loves || [])],
      avoids: [...(profile?.avoids || [])],
      notes: String(profile?.notes || ''),
      discoveryLevel: Number(profile?.discoveryLevel ?? 50),
    });
  }

  function tasteDraftIsDirty() {
    return Boolean(state.tasteDraft && tasteComparable(state.tasteDraft) !== tasteComparable(state.profile));
  }

  function markTasteDraftChanged() {
    const updated = $('#tasteUpdated');
    if (updated) updated.textContent = tasteDraftIsDirty() ? 'Unsaved changes' : (state.profile.updatedAt ? `Updated ${new Date(state.profile.updatedAt).toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' })}` : 'Not yet saved');
  }

  async function saveTasteDraft() {
    if (!state.tasteDraft) state.tasteDraft = cloneProfile(state.profile);
    state.profile = { ...cloneProfile(state.tasteDraft), updatedAt: new Date().toISOString() };
    state.tasteDraft = cloneProfile(state.profile);
    await persistAppState();
    try { await syncProfileToServer(); } catch (error) { console.warn('Could not sync taste profile to account.', error.message); }
    renderTastePage();
  }

  function discardTasteDraft() {
    state.tasteDraft = cloneProfile(state.profile);
    renderTastePage();
  }

  function addDraftReference(value) {
    const clean = String(value || '').trim().replace(/,$/, '');
    if (!state.tasteDraft) state.tasteDraft = cloneProfile(state.profile);
    if (!clean || state.tasteDraft.loves.some((item) => item.toLowerCase() === clean.toLowerCase()) || state.tasteDraft.loves.length >= 12) return;
    state.tasteDraft.loves.push(clean);
    state.tasteDraft.avoids = state.tasteDraft.avoids.filter((item) => item.toLowerCase() !== clean.toLowerCase());
    markTasteDraftChanged();
  }

  function addDraftAvoid(value) {
    const clean = String(value || '').trim().replace(/,$/, '');
    if (!state.tasteDraft) state.tasteDraft = cloneProfile(state.profile);
    if (!clean || state.tasteDraft.avoids.some((item) => item.toLowerCase() === clean.toLowerCase()) || state.tasteDraft.avoids.length >= 12) return;
    state.tasteDraft.avoids.push(clean);
    state.tasteDraft.loves = state.tasteDraft.loves.filter((item) => item.toLowerCase() !== clean.toLowerCase());
    markTasteDraftChanged();
  }

  function saveLastResult(data) {
    state.last = data;
    persistAppState();
  }

  function saveRecent(request, result) {
    const item = {
      id: `${Date.now()}`,
      intent: request.intent,
      prompt: request.goal || intentLabel(request.intent),
      createdAt: new Date().toISOString(),
      request: structuredCloneSafe(request),
      data: result,
    };
    state.recent = [item, ...state.recent.filter((entry) => entry.prompt !== item.prompt)].slice(0, 8);
    persistAppState();
  }

  function structuredCloneSafe(value) {
    try { return JSON.parse(JSON.stringify(value)); } catch { return value; }
  }

  function intentLabel(intent, personalized = false) {
    if ((intent === 'discover' || intent === 'taste') && personalized) return 'Explore art with my saved taste';
    return {
      discover: 'Explore art',
      find: 'Find art',
      taste: 'Explore art with my saved taste',
      curate: 'Curate a collection',
      buy: 'Buy art',
      learn: 'Learn about art',
    }[intent] || 'New request';
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
    }[char]));
  }

  function escapeAttribute(value) { return escapeHtml(value); }

  function setActiveNav() {
    const page = window.ART_CONCIERGE_PAGE || '';
    $$('[data-page]').forEach((link) => {
      if (link.dataset.page === page) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
  }

  function applyThemeControls() {
    const theme = state.preferences.theme === 'dark' ? 'dark' : 'light';
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    try { window.localStorage.setItem('vincentThemePreference', theme); } catch (_) { /* theme still works for this page */ }
    const font = state.preferences.fontScale || 'md';
    document.documentElement.style.setProperty('--scale', ({ sm: .92, md: 1, lg: 1.12 }[font] || 1));
    const select = $('#fontScale');
    if (select) select.value = font;
    const button = $('#themeToggle');
    if (button) button.setAttribute('aria-label', theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
  }

  function setupGlobalControls() {
    $('#logoutButton')?.addEventListener('click', async (event) => {
      event.preventDefault();
      try { await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json' } }); } catch (_) {}
      state.user = null;
      updateAuthControls();
      window.location.assign('/');
    });
    $('#themeToggle')?.addEventListener('click', () => {
      const next = (document.documentElement.dataset.theme || 'light') === 'dark' ? 'light' : 'dark';
      state.preferences.theme = next;
      persistAppState();
      applyThemeControls();
    });
    $('#fontScale')?.addEventListener('change', (event) => {
      const value = event.target.value;
      state.preferences.fontScale = value;
      persistAppState();
      document.documentElement.style.setProperty('--scale', ({ sm: .92, md: 1, lg: 1.12 }[value] || 1));
    });
  }

  function recentMarkup(item) {
    return `
      <article class="recent-item">
        <div class="recent-copy">
          <div class="recent-title">${escapeHtml(item.prompt)}</div>
          <div class="recent-meta">${escapeHtml(intentLabel(item.intent))} · ${escapeHtml(new Date(item.createdAt || item.savedAt || Date.now()).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }))}</div>
        </div>
        <button class="secondary-button recent-open" type="button" data-recent-id="${escapeAttribute(item.id)}">Continue</button>
      </article>`;
  }

  function clearCurrentInspirationImages() {
    clearInspirationObjectUrls();
    state.inspiration.images = [];
    state.inspiration.preparing = false;
    const selectedWrap = $('#selectedInspirationList');
    if (selectedWrap) selectedWrap.innerHTML = '';
    const files = $('#inspirationFiles');
    if (files) files.value = '';
  }

  function renderInspirationUI() {
    const selectedWrap = $('#selectedInspirationList');
    if (!selectedWrap) return;
    clearInspirationObjectUrls();
    const records = state.inspiration.images.slice(0, MAX_REQUEST_IMAGES);
    selectedWrap.innerHTML = records.map((record) => `
      <article class="selected-inspiration-item">
        <img src="${escapeAttribute(imageObjectUrl(record))}" alt="${escapeAttribute(record.name)}" loading="lazy" decoding="async">
        <div class="selected-inspiration-copy"><span>${escapeHtml(record.name)}</span><button class="text-button" type="button" data-remove-selected-image="${escapeAttribute(record.id)}">Remove</button></div>
      </article>`).join('');
    const status = $('#inspirationMessage');
    if (status && !state.inspiration.preparing && records.length === 0) status.textContent = '';
  }

  async function handleInspirationUpload(fileList) {
    const files = [...(fileList || [])];
    if (!files.length) return;
    const status = $('#inspirationMessage');
    const errors = [];
    let addedCount = 0;
    state.inspiration.preparing = true;
    try {
      for (const file of files) {
        if (state.inspiration.images.length >= MAX_REQUEST_IMAGES) {
          errors.push(`Only ${MAX_REQUEST_IMAGES} images can be attached to one search.`);
          break;
        }
        try {
          if (status) status.textContent = `Preparing ${safeImageName(file.name)}…`;
          const compressed = await compressInspirationImage(file);
          const record = {
            id: (window.crypto && typeof window.crypto.randomUUID === 'function') ? window.crypto.randomUUID() : `inspiration-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`,
            name: safeImageName(file.name),
            mimeType: 'image/jpeg',
            blob: compressed.blob,
            width: compressed.width,
            height: compressed.height,
            bytes: compressed.blob.size,
            notes: '',
          };
          state.inspiration.images.push(record);
          addedCount += 1;
        } catch (error) {
          errors.push(error.message || `${safeImageName(file.name)} could not be prepared.`);
        }
      }
    } finally {
      state.inspiration.preparing = false;
    }
    renderInspirationUI();
    if (status) {
      const successText = addedCount
        ? `${addedCount} image${addedCount === 1 ? '' : 's'} attached to this search only. They will be discarded when this search finishes.`
        : '';
      status.textContent = [successText, ...errors].filter(Boolean).join(' ');
    }
  }

  function purchaseStatusInfo(purchase) {
    const paymentStatus = String(purchase.paymentStatus || '').toLowerCase();
    const checkoutStatus = String(purchase.checkoutStatus || '').toLowerCase();
    if (paymentStatus === 'paid' || paymentStatus === 'no_payment_required') return { key: 'paid', label: 'Payment confirmed' };
    if (paymentStatus === 'completed' || paymentStatus === 'captured') return { key: 'paid', label: 'Payment confirmed' };
    if (paymentStatus === 'external_pending' || checkoutStatus === 'redirected') return { key: 'seller', label: 'Seller checkout opened' };
    if (checkoutStatus === 'sent' || paymentStatus === 'unpaid' && String(purchase.provider || '').includes('invoice')) return { key: 'invoice', label: 'Invoice sent' };
    if (checkoutStatus === 'expired') return { key: 'expired', label: 'Checkout expired' };
    if (checkoutStatus === 'complete') return { key: 'processing', label: 'Payment processing' };
    if (checkoutStatus === 'open' || checkoutStatus === 'created' || paymentStatus === 'approval_pending' || paymentStatus === 'approved') return { key: 'awaiting', label: 'Awaiting payment' };
    return { key: 'unknown', label: 'Status not verified' };
  }

  function formatPurchaseAmount(amountTotal, currency) {
    if (amountTotal === null || amountTotal === undefined || !Number.isFinite(Number(amountTotal))) return 'Not available';
    try {
      return new Intl.NumberFormat(undefined, {
        style: 'currency', currency: String(currency || 'CAD').toUpperCase(),
      }).format(Number(amountTotal) / 100);
    } catch {
      return `${(Number(amountTotal) / 100).toFixed(2)} ${String(currency || 'CAD').toUpperCase()}`;
    }
  }

  function renderPurchaseHistory() {
    const list = $('#dashboardPurchases');
    if (!list) return;
    const purchases = Array.isArray(state.purchases) ? state.purchases.slice(0, 10) : [];
    const empty = $('#dashboardPurchasesEmpty');
    if (!purchases.length) {
      list.innerHTML = '';
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;
    list.innerHTML = purchases.map((purchase) => {
      const status = purchaseStatusInfo(purchase);
      const createdAt = purchase.createdAt ? new Date(purchase.createdAt).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : 'Date not available';
      const reference = String(purchase.sessionId || '');
      const provider = String(purchase.provider || 'stripe');
      const action = provider === 'seller_redirect' && purchase.sourceUrl
        ? `<a class="secondary-button" href="${escapeAttribute(purchase.sourceUrl)}" target="_blank" rel="noopener noreferrer">Open seller site</a>`
        : provider === 'paypal_invoice'
          ? `${purchase.invoiceUrl ? `<a class="secondary-button" href="${escapeAttribute(purchase.invoiceUrl)}" target="_blank" rel="noopener noreferrer">View invoice</a>` : ''}<button class="secondary-button purchase-check-button" type="button" data-purchase-session="${escapeAttribute(reference)}" data-purchase-provider="paypal_invoice">Check invoice</button>`
          : (provider === 'stripe' || provider === 'paypal')
            ? `<button class="secondary-button purchase-check-button" type="button" data-purchase-session="${escapeAttribute(reference)}" data-purchase-provider="${escapeAttribute(provider)}">Check payment</button>`
            : '';
      const shortReference = reference.length > 19 ? `${reference.slice(0, 9)}…${reference.slice(-7)}` : reference;
      return `<article class="purchase-record">
        <div class="purchase-record-main">
          <strong>${escapeHtml(purchase.title || purchase.artworkId || 'Artwork checkout')}</strong>
          <span>${escapeHtml(purchase.artist || 'Artist not provided')}</span>
          <small>${escapeHtml(createdAt)} · Ref ${escapeHtml(shortReference)}</small>
        </div>
        <div class="purchase-record-summary">
          <span class="purchase-status purchase-status--${status.key}">${escapeHtml(status.label)}</span>
          <strong>${escapeHtml(formatPurchaseAmount(purchase.amountTotal, purchase.currency))}</strong>
          ${action}
        </div>
      </article>`;
    }).join('');
  }

  function renderCompareDock() {
    const dock = $('#compareDock');
    const count = $('#compareDockCount');
    if (!dock) return;
    const selected = state.compare.length;
    dock.hidden = selected === 0;
    if (count) count.textContent = `${selected} artwork${selected === 1 ? '' : 's'} selected`;
    const open = $('#openComparisonButton');
    if (open) { open.disabled = selected < 2; open.textContent = selected < 2 ? 'Select one more to compare' : `Compare ${selected} artworks`; }
  }

  function toggleComparison(id) {
    const existing = state.compare.findIndex((work) => work.id === id);
    if (existing >= 0) state.compare.splice(existing, 1);
    else {
      const work = getArtworkById(id);
      if (!work) return;
      if (state.compare.length >= 3) {
        const note = $('#purchaseMessage') || $('#collectionsMessage');
        if (note) note.textContent = 'Compare up to three artworks at a time. Remove one to add another.';
        return;
      }
      state.compare.push(structuredCloneSafe(work));
    }
    persistAppState();
    renderCompareDock();
    if (state.last && window.ART_CONCIERGE_PAGE === 'results') renderResults(state.last);
    if (window.ART_CONCIERGE_PAGE === 'saved') renderSavedPage();
    if (window.ART_CONCIERGE_PAGE === 'dashboard') { renderComparisonPanel(); renderCollectionsPanel(); }
  }

  function artworkPriceLabel(work) {
    if (work.price === null || work.price === undefined || !Number.isFinite(Number(work.price))) return 'Not listed';
    try { return new Intl.NumberFormat(undefined, { style: 'currency', currency: String(work.currency || 'CAD').toUpperCase() }).format(Number(work.price)); }
    catch (_) { return `${Number(work.price).toFixed(2)} ${String(work.currency || 'CAD').toUpperCase()}`; }
  }

  function comparisonTableMarkup(works) {
    if (works.length < 2) return '<p class="empty-state">Select at least two artworks to compare.</p>';
    const fields = [
      ['Artist', (work) => work.artist || 'Not listed'],
      ['Price', artworkPriceLabel],
      ['Medium', (work) => work.medium || 'Not listed'],
      ['Dimensions', (work) => work.dimensions || 'Not listed'],
      ['Year', (work) => work.year || 'Not listed'],
      ['Seller / source', (work) => work.seller_name || work.source || 'Not listed'],
      ['Availability', (work) => work.availability === 'available' ? 'Listed as available' : 'Not listed as available'],
      ['Description', (work) => work.description || 'Not provided'],
    ];
    const imageCells = works.map((work) => {
      const image = work.display_image_url || work.image_url || artworkFallbackUrl(work.title, work.artist);
      return `<th scope="col"><img class="comparison-image" src="${escapeAttribute(image)}" alt="${escapeAttribute(work.title || 'Artwork')}" loading="lazy"><strong>${escapeHtml(work.title || 'Untitled')}</strong><button class="text-button comparison-remove" type="button" data-compare-id="${escapeAttribute(work.id)}">Remove</button></th>`;
    }).join('');
    const rows = fields.map(([label, getter]) => `<tr><th scope="row">${escapeHtml(label)}</th>${works.map((work) => `<td>${escapeHtml(getter(work))}</td>`).join('')}</tr>`).join('');
    return `<div class="comparison-table-wrap"><table class="comparison-table"><thead><tr><th scope="col">Artwork</th>${imageCells}</tr></thead><tbody>${rows}<tr><th scope="row">Open listing</th>${works.map((work) => `<td>${work.detail_url ? `<a href="${escapeAttribute(work.detail_url)}" target="_blank" rel="noopener noreferrer">View seller listing ↗</a>` : 'Not available'}</td>`).join('')}</tr></tbody></table></div>`;
  }

  function renderComparison() {
    const content = $('#comparisonContent');
    if (!content) return;
    content.innerHTML = comparisonTableMarkup(state.compare.slice(0, 3));
    bindArtworkImageFallbacks(content);
  }

  function renderComparisonPanel() {
    const content = $('#dashboardComparisonContent');
    const empty = $('#dashboardComparisonEmpty');
    const clear = $('#dashboardClearComparison');
    if (!content) return;
    const works = state.compare.slice(0, 3);
    if (empty) { empty.hidden = works.length >= 2; empty.textContent = works.length === 1 ? 'One artwork selected. Select one more artwork to compare.' : 'No artworks selected yet. Open an artwork in Concierge or Saved ArtWorks and select Compare.'; }
    if (clear) clear.hidden = works.length === 0;
    content.innerHTML = works.length >= 2 ? comparisonTableMarkup(works) : '';
    bindArtworkImageFallbacks(content);
  }

  function openComparison() {
    const modal = $('#comparisonModal');
    if (!modal) return;
    renderComparison();
    modal.hidden = false;
    modal.querySelector('[role="dialog"]')?.focus();
  }

  function closeFeatureModal(id) {
    const modal = $(`#${id}`);
    if (modal) modal.hidden = true;
  }

  function renderCollectionPicker() {
    const choices = $('#collectionPickerChoices');
    if (!choices) return;
    if (!state.collections.length) {
      choices.innerHTML = '<p class="empty-state">Create a collection below to get started.</p>';
      return;
    }
    choices.innerHTML = state.collections.map((collection) => `<button class="collection-choice" type="button" data-collection-choice="${escapeAttribute(collection.id)}"><strong>${escapeHtml(collection.name)}</strong><span>${(collection.artworks || []).length} artwork${(collection.artworks || []).length === 1 ? '' : 's'}</span></button>`).join('');
  }

  function openCollectionPicker(artworkId) {
    state.pendingCollectionArtworkId = artworkId;
    renderCollectionPicker();
    const modal = $('#collectionPickerModal');
    if (modal) { modal.hidden = false; modal.querySelector('[role="dialog"]')?.focus(); }
  }

  async function createCollection(name, description = '') {
    const clean = String(name || '').trim();
    if (clean.length < 2) throw new Error('Collection names must contain at least two characters.');
    if (state.collections.length >= 30) throw new Error('You can create up to 30 collections.');
    const collection = { id: makeId('collection').slice(0, 36), name: clean.slice(0, 100), description: String(description || '').trim().slice(0, 500), artworks: [], createdAt: Date.now(), updatedAt: Date.now() };
    state.collections.unshift(collection);
    await persistAppState();
    if (state.user && !state.user.demo) await syncCollectionState(collection, 'create');
    renderCollectionsPanel();
    return state.collections.find((item) => item.id === collection.id) || collection;
  }

  async function addArtworkToCollection(collectionId, artwork) {
    if (!artwork) throw new Error('Choose an artwork first.');
    const collection = state.collections.find((item) => item.id === collectionId);
    if (!collection) throw new Error('That collection could not be found.');
    collection.artworks = Array.isArray(collection.artworks) ? collection.artworks : [];
    if (collection.artworks.some((item) => item.id === artwork.id)) return;
    if (collection.artworks.length >= 100) throw new Error('A collection can contain up to 100 artworks.');
    collection.artworks.push(structuredCloneSafe(artwork));
    collection.updatedAt = Date.now();
    await persistAppState();
    if (state.user && !state.user.demo) await syncCollectionState(collection, 'add', artwork);
    renderCollectionsPanel();
  }

  async function removeArtworkFromCollection(collectionId, artworkId) {
    const collection = state.collections.find((item) => item.id === collectionId);
    if (!collection) return;
    const artwork = (collection.artworks || []).find((item) => item.id === artworkId);
    collection.artworks = (collection.artworks || []).filter((item) => item.id !== artworkId);
    collection.updatedAt = Date.now();
    await persistAppState();
    if (state.user && !state.user.demo && artwork) await syncCollectionState(collection, 'remove', artwork);
    renderCollectionsPanel();
  }

  async function removeCollection(collectionId) {
    const collection = state.collections.find((item) => item.id === collectionId);
    if (!collection) return;
    state.collections = state.collections.filter((item) => item.id !== collectionId);
    await persistAppState();
    if (state.user && !state.user.demo) await syncCollectionState(collection, 'delete');
    renderCollectionsPanel();
  }

  function renderCollectionsPanel() {
    const list = $('#collectionsList');
    const empty = $('#collectionsEmpty');
    if (!list) return;
    if (empty) empty.hidden = state.collections.length > 0;
    list.innerHTML = state.collections.map((collection) => {
      const works = Array.isArray(collection.artworks) ? collection.artworks : [];
      const pricedWorks = works.filter((work) => work.price !== null && work.price !== undefined && Number.isFinite(Number(work.price)));
      const currencies = [...new Set(pricedWorks.map((work) => String(work.currency || '').toUpperCase()).filter(Boolean))];
      const allPriced = pricedWorks.length === works.length && works.length > 0;
      const total = pricedWorks.reduce((sum, work) => sum + Number(work.price), 0);
      const totalLabel = currencies.length > 1 ? 'Multiple currencies' : allPriced && currencies.length === 1
        ? `${total.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${currencies[0]}`
        : pricedWorks.length && currencies.length === 1 ? `Known prices: ${total.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${currencies[0]}`
          : 'Prices not available';
      return `<section class="collection-card" data-collection-id="${escapeAttribute(collection.id)}">
        <header class="collection-card-header"><div><h3>${escapeHtml(collection.name)}</h3><p>${escapeHtml(collection.description || '')}</p><span>${works.length} artwork${works.length === 1 ? '' : 's'} · ${escapeHtml(totalLabel)}</span></div><button class="text-button danger-text" type="button" data-delete-collection="${escapeAttribute(collection.id)}">Delete collection</button></header>
        ${works.length ? `<div class="art-grid collection-art-grid">${works.map((work) => renderCard(work, { savedLayout: true, collectionId: collection.id })).join('')}</div>` : '<p class="empty-state">No artworks in this collection yet. Add works from your search results or Saved ArtWorks.</p>'}
      </section>`;
    }).join('');
    bindArtworkImageFallbacks(list);
  }

  async function createArtworkAlert(id) {
    const artwork = getArtworkById(id);
    if (!artwork) throw new Error('Artwork details are no longer available.');
    if (state.alerts.some((alert) => alert.type === 'artwork' && alert.target?.artwork?.id === id)) return;
    if (state.alerts.length >= 50) throw new Error('You can follow up to 50 artworks and shops.');
    const alert = {
      id: makeId('alert').slice(0, 36), type: 'artwork', active: true,
      target: { artwork: structuredCloneSafe(artwork), source: artwork.source, seller_name: artwork.seller_name || artwork.source, market_code: artwork.market_code || 'CA' },
      baseline: { price: artwork.price ?? null, currency: artwork.currency || '', availability: artwork.availability || '', known_ids: [] },
      events: [], createdAt: Date.now(), lastCheckedAt: null,
    };
    state.alerts.unshift(alert);
    await persistAppState();
    if (state.user && !state.user.demo) await syncAlertState(alert, 'create');
    renderAlertsPanel();
  }

  async function createSellerAlert(name, market, sourceUrl, knownWorks = []) {
    const seller = String(name || '').trim();
    if (!seller) return;
    if (state.alerts.some((alert) => alert.type === 'seller' && alert.target?.seller_name === seller)) return;
    if (state.alerts.length >= 50) throw new Error('You can follow up to 50 artworks and shops.');
    const alert = {
      id: makeId('alert').slice(0, 36), type: 'seller', active: true,
      target: { seller_name: seller, source: seller, market_code: market || 'CA', source_url: sourceUrl || '' },
      // Leave the initial set empty: the first successful check establishes the
      // seller's baseline instead of treating the existing catalogue as new.
      baseline: { known_ids: [] },
      events: [], createdAt: Date.now(), lastCheckedAt: null,
    };
    state.alerts.unshift(alert);
    await persistAppState();
    if (state.user && !state.user.demo) await syncAlertState(alert, 'create');
    renderAlertsPanel();
  }

  async function deleteAlert(alertId) {
    const alert = state.alerts.find((item) => item.id === alertId);
    state.alerts = state.alerts.filter((item) => item.id !== alertId);
    await persistAppState();
    if (state.user && !state.user.demo && alert) await syncAlertState(alert, 'delete');
    renderAlertsPanel();
  }

  function renderAlertsPanel() {
    const list = $('#alertsList');
    const empty = $('#alertsEmpty');
    if (!list) return;
    if (empty) empty.hidden = state.alerts.length > 0;
    list.innerHTML = state.alerts.map((alert) => {
      const target = alert.target || {};
      const work = target.artwork || {};
      const title = alert.type === 'artwork' ? (work.title || work.artist || 'Artwork') : (target.seller_name || 'Art shop');
      const detail = alert.type === 'artwork' ? `${work.artist || 'Artist not listed'} · ${target.seller_name || target.source || 'Seller not listed'}` : `${target.market_code === 'US' ? 'United States' : 'Canada'} · New artwork listings`;
      const events = Array.isArray(alert.events) ? alert.events.slice(0, 5) : [];
      const latest = alert.lastCheckedAt ? `Last checked ${new Date(Number(alert.lastCheckedAt) * (Number(alert.lastCheckedAt) < 1e12 ? 1000 : 1)).toLocaleString()}` : 'Not checked yet';
      return `<article class="alert-card"><div class="alert-card-heading"><div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span><small>${escapeHtml(latest)}</small></div><button class="text-button danger-text" type="button" data-delete-alert="${escapeAttribute(alert.id)}">Remove alert</button></div>
        ${alert.checkError ? `<p class="alert-error">${escapeHtml(alert.checkError)}</p>` : ''}
        ${events.length ? `<ul class="alert-events">${events.map((item) => `<li><span>${escapeHtml(item.message || 'Update found')}</span><small>${escapeHtml(new Date(Number(item.createdAt || Date.now()) * (Number(item.createdAt || 0) < 1e12 ? 1000 : 1)).toLocaleString())}</small></li>`).join('')}</ul>` : '<p class="field-note">No changes detected yet.</p>'}
      </article>`;
    }).join('');
  }

  async function checkArtworkAlerts(button = null) {
    const message = $('#alertsMessage');
    const checkButton = button || $('#checkAlertsButton');
    if (!state.alerts.length) { if (message) message.textContent = 'Follow an artwork or shop first.'; return; }
    if (checkButton) { checkButton.disabled = true; checkButton.textContent = 'Checking…'; }
    if (message) message.textContent = 'Checking public shop listings…';
    try {
      const data = await accountFetch('/api/account/alerts/check', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ alerts: state.user?.demo ? state.alerts : [] }),
      });
      if (Array.isArray(data.all_alerts)) {
        const checksById = new Map((data.alerts || []).map((item) => [item.id, item]));
        state.alerts = data.all_alerts.map((alert) => ({ ...alert, checkError: checksById.get(alert.id)?.checkError || '' }));
      } else if (Array.isArray(data.alerts)) {
        const byId = new Map(data.alerts.map((item) => [item.id, item]));
        state.alerts = state.alerts.map((alert) => {
          const checked = byId.get(alert.id);
          if (!checked) return alert;
          const updated = { ...alert, baseline: checked.baseline || alert.baseline, lastCheckedAt: checked.lastCheckedAt || Date.now(), checkError: checked.checkError || '' };
          if (checked.newEvent) updated.events = [{ ...checked.newEvent, createdAt: Date.now(), id: makeId('event'), read: false }, ...(updated.events || [])].slice(0, 30);
          return updated;
        });
      }
      await persistAppState();
      const eventCount = Array.isArray(data.alerts) ? data.alerts.filter((alert) => Boolean(alert.newEvent)).length : 0;
      const failedCount = Array.isArray(data.alerts) ? data.alerts.filter((alert) => Boolean(alert.checkError)).length : 0;
      if (message) message.textContent = failedCount
        ? `${failedCount} alert${failedCount === 1 ? '' : 's'} could not be checked. Other checks completed${eventCount ? `; ${eventCount} new update${eventCount === 1 ? '' : 's'} found` : ''}.`
        : eventCount ? `${eventCount} new update${eventCount === 1 ? '' : 's'} found.` : 'Checks completed. No new changes were detected.';
    } catch (error) {
      if (message) message.textContent = error.message || 'Alerts could not be checked right now.';
    } finally {
      if (checkButton) { checkButton.disabled = false; checkButton.textContent = 'Check for updates'; }
      renderAlertsPanel();
    }
  }

  function renderDashboard() {
    const recent = $('#dashboardRecent');
    const saved = $('#dashboardSaved');
    const savedEmpty = $('#dashboardSavedEmpty');
    if (!recent && !saved && !$('#dashboardPurchases')) return;

    if (recent) {
      recent.innerHTML = state.recent.length
        ? state.recent.slice(0, 5).map(recentMarkup).join('')
        : '<p class="empty-state">Your recent conversations will appear here after your first search.</p>';
    }

    if (saved) {
      const works = state.saved.slice(0, 6);
      saved.innerHTML = works.map((work) => {
        const id = escapeAttribute(work.id);
        const savedImageUrl = work.display_image_url || work.image_url || artworkPreviewUrl(work.detail_url) || artworkFallbackUrl(work.title, work.artist);
        const image = `<img data-artwork-image data-artwork-title="${escapeAttribute(work.title || '')}" data-artwork-artist="${escapeAttribute(work.artist || '')}" data-detail-url="${escapeAttribute(work.detail_url || '')}" ${work.display_image_url && work.display_image_url !== work.image_url && work.image_url ? `data-original-src="${escapeAttribute(work.image_url)}"` : ''} ${work.detail_url && savedImageUrl === artworkPreviewUrl(work.detail_url) ? 'data-tried-detail-preview="true"' : ''} ${savedImageUrl === artworkFallbackUrl(work.title, work.artist) ? 'data-tried-image-fallback="true"' : ''} src="${escapeAttribute(savedImageUrl)}" alt="${escapeAttribute(work.title || 'Artwork')} by ${escapeAttribute(work.artist || 'Unknown artist')}" loading="eager" decoding="async" fetchpriority="high" referrerpolicy="no-referrer">`;
        return `<button class="saved-thumb" type="button" data-saved-id="${id}" aria-label="Open ${escapeAttribute(work.title || 'saved artwork')}">${image}</button>`;
      }).join('');
      bindArtworkImageFallbacks(saved);
      if (savedEmpty) savedEmpty.hidden = works.length > 0;
    }

    renderPurchaseHistory();
    renderTastePage();
    renderComparisonPanel();
    renderCollectionsPanel();
    renderAlertsPanel();

    const dashboardTabs = [
      { name: 'overview', tab: $('#overviewTab'), panel: $('#overviewPanel') },
      { name: 'taste', tab: $('#tasteTab'), panel: $('#tastePanel') },
      { name: 'compare', tab: $('#compareTab'), panel: $('#comparePanel') },
      { name: 'collections', tab: $('#collectionsTab'), panel: $('#collectionsPanel') },
      { name: 'alerts', tab: $('#alertsTab'), panel: $('#alertsPanel') },
      { name: 'purchases', tab: $('#purchasesTab'), panel: $('#purchasesPanel') },
    ].filter((item) => item.tab && item.panel);
    const activateTab = (tabName, moveFocus = false, updateHash = false) => {
      const selected = dashboardTabs.find((item) => item.name === tabName) || dashboardTabs[0];
      dashboardTabs.forEach((item) => {
        const active = item === selected;
        item.tab.classList.toggle('is-active', active);
        item.tab.setAttribute('aria-selected', String(active));
        item.tab.setAttribute('tabindex', active ? '0' : '-1');
        item.panel.hidden = !active;
        item.panel.classList.toggle('is-active', active);
      });
      if (updateHash) {
        const suffix = selected?.name === 'overview' ? '/dashboard' : `/dashboard#${selected?.name}`;
        window.history.replaceState(null, '', suffix);
      }
      if (moveFocus) selected?.tab.focus();
    };
    dashboardTabs.forEach((item, index) => {
      item.tab.addEventListener('click', () => requestTasteNavigation(() => activateTab(item.name, false, true)));
      item.tab.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
          event.preventDefault();
          requestTasteNavigation(() => activateTab(dashboardTabs[(index + 1) % dashboardTabs.length].name, true, true));
        } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
          event.preventDefault();
          requestTasteNavigation(() => activateTab(dashboardTabs[(index - 1 + dashboardTabs.length) % dashboardTabs.length].name, true, true));
        } else if (event.key === 'Home') {
          event.preventDefault();
          requestTasteNavigation(() => activateTab(dashboardTabs[0].name, true, true));
        } else if (event.key === 'End') {
          event.preventDefault();
          requestTasteNavigation(() => activateTab(dashboardTabs[dashboardTabs.length - 1].name, true, true));
        }
      });
    });
    const hash = window.location.hash.toLowerCase();
    activateTab(hash === '#taste' ? 'taste' : hash === '#compare' ? 'compare' : hash === '#collections' ? 'collections' : hash === '#alerts' ? 'alerts' : hash === '#purchases' ? 'purchases' : 'overview');
  }

  function renderHome() {
    // The landing page is intentionally focused on the product proposition and primary CTA.
  }

  function intentCopy(intent) {
    return {
      discover: ['Explore art', 'Discover surprising connections. Turn on saved-taste personalization to build on your artworks, preferences, and earlier ideas.', 'I love punk music and bold patterns. Find art with a similar energy, but take me somewhere unexpected.'],
      find: ['Find art', 'Search for a particular artist, artwork, style, subject, or visual detail.', 'Find an artist or artwork with this style, colour palette, or subject.'],
      taste: ['Explore art', 'Discover art in the context of your saved artworks, taste profile, and earlier ideas.', 'Use my saved artworks and ideas to help me understand why I am drawn to bold patterns, expressive colour, and unusual textures, then find related art I can buy.'],
      curate: ['Curate a collection', 'Build a coherent collection of purchasable works around a space, palette, mood, or theme.', 'Find three works for a small living room, with warm colours and an experimental feel.'],
      buy: ['Buy art', 'Find artwork actively listed for acquisition from North American galleries and seller inventory. Availability and price are shown when the listing provides them; the seller remains the source of truth.', 'Find an original work by a Canadian artist under CAD 1,500 that I can purchase now.'],
      learn: ['Learn about art', 'Generate a learning guide from a written topic. Image uploads are not used in this mode, so enter the artwork title, artist, movement, technique, or idea.', 'Tsumari in Bloom by Yayoi Kusama: explain the artwork, artist, context, and what to look for.'],
    }[intent] || ['Explore art', 'Discover new directions or personalise the search with your saved taste.', 'Tell me what you are looking for.'];
  }

  function setIntent(intent) {
    const requested = intent === 'taste' ? 'discover' : intent;
    state.intent = ['discover', 'find', 'curate', 'buy', 'learn'].includes(requested) ? requested : 'discover';
    const [serviceTitle, description, promptPlaceholder] = intentCopy(state.intent);
    const descriptionNode = $('#serviceDescription');
    const input = $('#requestInput');
    const personalization = $('#personalizationControl');
    const personalizationInput = $('#personalizeExplore');
    const askLabel = $('#askConciergeLabel');
    if (descriptionNode) descriptionNode.textContent = description;
    if (input) {
      input.placeholder = `${promptPlaceholder}\n\nAdd as much or as little detail as you like.`;
      input.setCustomValidity('');
    }
    const inspirationSection = $('#visualInspirationSection');
    if (inspirationSection) inspirationSection.hidden = state.intent === 'learn';
    const marketScopeControl = $('#marketScopeControl');
    const marketScopeSelect = $('#marketScope');
    if (marketScopeControl) marketScopeControl.hidden = state.intent === 'learn';
    if (marketScopeSelect) marketScopeSelect.value = ['CA', 'US', 'BOTH'].includes(state.preferences.regionScope) ? state.preferences.regionScope : 'CA';
    if (askLabel) askLabel.textContent = String(serviceTitle).trim().split(/\s+/)[0] || 'Explore';
    if (personalization) personalization.hidden = state.intent !== 'discover';
    if (personalizationInput) personalizationInput.checked = Boolean(state.personalizeExplore);
  }

  function addProfileReference(value) {
    const clean = value.trim().replace(/,$/, '');
    if (!clean || state.profile.loves.includes(clean) || state.profile.loves.length >= 12) return;
    state.profile.loves.push(clean);
    saveProfile();
  }

  function addProfileAvoid(value) {
    const clean = value.trim().replace(/,$/, '');
    if (!clean || state.profile.avoids.includes(clean) || state.profile.avoids.length >= 12) return;
    state.profile.avoids.push(clean);
    state.profile.loves = state.profile.loves.filter((item) => item.toLowerCase() !== clean.toLowerCase());
    saveProfile();
  }

  function learnFromFeedback(feedback, artwork) {
    if (!artwork?.artist) return;
    const artist = artwork.artist.trim();
    if (/^More like/i.test(feedback || '')) {
      if (!state.profile.loves.some((item) => item.toLowerCase() === artist.toLowerCase())) {
        state.profile.loves.push(artist);
      }
      state.profile.avoids = state.profile.avoids.filter((item) => item.toLowerCase() !== artist.toLowerCase());
      state.profile.loves = state.profile.loves.slice(-12);
      saveProfile();
    } else if (/^Not for me/i.test(feedback || '')) {
      addProfileAvoid(artist);
    }
  }

  function parseBudget(text) {
    const match = text.replace(/,/g, '').match(/(?:\$|CAD\s?)?(\d{3,7})/i);
    return match ? Number(match[1]) : null;
  }

  function inferIntent(text) {
    const lower = text.toLowerCase();
    if (/buy|purchase|acquire|for sale|budget|price/.test(lower)) return 'buy';
    if (/curate|collection|set of|several/.test(lower)) return 'curate';
    if (/learn.*taste|develop.*taste|understand.*like|help me.*taste/.test(lower)) return 'discover';
    if (/specific|looking for|need a|find me/.test(lower)) return 'find';
    if (/artist|artwork|movement|history|about|learn|explain|who is|what is/.test(lower)) return 'learn';
    return 'discover';
  }

  function cleanServicePrompt(intent) {
    return ({ taste: 'explore art with my saved taste', discover: 'explore art', curate: 'curate a collection', buy: 'buy art' })[intent] || '';
  }

  function buildRequest(prompt, feedback = null, explicitIntent = null) {
    const inferred = explicitIntent || inferIntent(prompt);
    const promptRequestsPersonalTaste = /develop(?:ing)? my taste|help me .*taste|understand (?:what|why) .* (?:like|drawn to)|why am i drawn|what i am drawn to|build on my (?:saved )?(?:taste|artworks)/i.test(prompt || '');
    const useSavedTaste = inferred === 'taste' || (inferred === 'discover' && (state.personalizeExplore || promptRequestsPersonalTaste));
    const budget = parseBudget(prompt);
    return {
      intent: inferred === 'taste' ? 'discover' : inferred,
      personalize_with_saved_taste: useSavedTaste,
      loves: [...state.profile.loves],
      avoid_references: [...state.profile.avoids],
      additional_interests: [],
      profile_notes: useSavedTaste ? (String(state.profile.notes || '').trim().slice(0, 1000) || null) : null,
      existing_ideas: useSavedTaste
        ? state.recent.map((item) => String(item.prompt || '').trim()).filter((item) => item && item.toLowerCase() !== cleanServicePrompt(inferred)).slice(0, 4)
        : [],
      existing_artworks: useSavedTaste ? state.saved.slice(0, 6).map((work) => ({
        title: String(work.title || '').slice(0, 180),
        artist: String(work.artist || '').slice(0, 140),
        medium: String(work.medium || '').slice(0, 120),
        description: String(work.description || '').slice(0, 300),
        year: String(work.year || '').slice(0, 40),
      })) : [],
      art_interests: [],
      mediums: [],
      budget_min: null,
      budget_max: budget,
      room: null,
      size_preference: null,
      preferred_market: ({ CA: 'Canada', US: 'United States', BOTH: 'Canada and United States' })[['CA', 'US', 'BOTH'].includes(state.preferences.regionScope) ? state.preferences.regionScope : 'CA'],
      region_scope: ['CA', 'US', 'BOTH'].includes(state.preferences.regionScope) ? state.preferences.regionScope : 'CA',
      discovery_level: state.profile.discoveryLevel,
      purchase_required: inferred === 'buy' || useSavedTaste,
      goal: prompt.trim() || null,
      number_of_works: 3,
      feedback,
      exclude_artwork_ids: [],
      inspiration_image_ids: [],
      inspiration_images: [],
    };
  }

  function resultsRunUrl(intent, fromConcierge = false, personalizeWithSavedTaste = state.personalizeExplore) {
    const runId = (window.crypto && typeof window.crypto.randomUUID === 'function')
      ? window.crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    const params = new URLSearchParams({ intent: intent || 'discover', run: '1', run_id: runId });
    if (fromConcierge) params.set('from', 'concierge');
    if (personalizeWithSavedTaste) params.set('personalize_taste', '1');
    return `/results?${params.toString()}`;
  }

  async function beginRequest(prompt) {
    // Read the live textarea at submission time rather than relying on a value
    // captured by an earlier event. The selected dropdown is authoritative: the
    // wording of a prompt must not silently switch Find or Curate to another mode.
    const input = $('#requestInput');
    const selectedIntent = $('#serviceSelect')?.value;
    if (['discover', 'find', 'curate', 'buy', 'learn'].includes(selectedIntent)) state.intent = selectedIntent;
    const clean = String(input?.value ?? prompt ?? '').trim();
    const learnNeedsTopic = state.intent === 'learn';
    // Every Concierge mode requires a written brief. Inspiration images can
    // refine the request, but must never satisfy the text requirement alone.
    if (!clean) {
      if (input) {
        input.setCustomValidity(learnNeedsTopic
          ? 'Enter an artist, artwork title, movement, technique, or idea to learn about.'
          : 'Enter a short description of what you would like Vincent to do. Inspiration images can support your request but cannot replace the text.');
        input.reportValidity();
        input.focus();
      }
      return;
    }
    if (input) input.setCustomValidity('');
    const inferred = state.intent;
    const request = buildRequest(clean, null, inferred);
    try {
      const selectedImages = state.inspiration.images.slice(0, MAX_REQUEST_IMAGES);
      request.inspiration_images = await Promise.all(selectedImages.map(async (record) => ({
        image_id: record.id,
        name: record.name,
        notes: record.notes || '',
        mime_type: record.mimeType || 'image/jpeg',
        data: await blobToBase64(record.blob),
      })));
      request.inspiration_image_ids = request.inspiration_images.map((image) => image.image_id);
      sessionStorage.setItem('artConciergePendingRequest', JSON.stringify(request));
      // Images live only long enough to cross the same-tab page navigation.
      clearCurrentInspirationImages();
      window.location.assign(resultsRunUrl(request.intent, true, request.personalize_with_saved_taste));
    } catch (error) {
      const status = $('#inspirationMessage');
      if (status) status.textContent = error?.name === 'QuotaExceededError'
        ? 'The selected images are too large for this browser tab to pass to the search. Remove one or choose smaller images.'
        : (error.message || 'Vincent could not prepare the request. Please try again.');
    }
  }

  function beginNotForMeSearch(artwork, button = null) {
    if (!artwork) return;
    const title = String(artwork.title || 'this artwork').trim();
    const artist = String(artwork.artist || '').trim();
    const knownArtist = artist && !/^(unknown|anonymous|various) artist$/i.test(artist);
    const prompt = knownArtist
      ? `Find alternatives to “${title}” with a different visual direction. Do not recommend work by ${artist}.`
      : `Find alternatives to “${title}” with a different visual direction. Do not return this artwork.`;
    const request = buildRequest(prompt, `Not for me: ${knownArtist ? artist : title}`, 'discover');
    request.exclude_artwork_ids = [String(artwork.id || '')].filter(Boolean).slice(0, 20);
    request.avoid_references = [...new Set([
      ...state.profile.avoids,
      ...(knownArtist ? [artist] : []),
    ])].slice(-12);
    request.art_interests = [];
    request.number_of_works = 3;
    request.purchase_required = false;
    request.inspiration_image_ids = [];
    request.inspiration_images = [];
    if (button) { button.disabled = true; button.textContent = 'Searching alternatives…'; }
    try {
      sessionStorage.setItem('artConciergePendingRequest', JSON.stringify(request));
      window.location.assign(resultsRunUrl('discover'));
    } catch (error) {
      if (button) { button.disabled = false; button.textContent = 'Not for me'; }
      const message = $('#resultsSummary');
      if (message) message.textContent = 'Could not start a new search. Please try again.';
    }
  }

  function beginSimilarSearch(artwork) {
    if (!artwork) return;
    const title = String(artwork.title || 'this artwork').trim();
    const artist = String(artwork.artist || '').trim();
    const subject = [title, artist && `by ${artist}`].filter(Boolean).join(' ');
    const prompt = `Find artworks similar to ${subject}. Prioritise related subject matter, visual style, medium, composition, and period. Do not return the exact same work.`;
    const request = buildRequest(prompt, `More like ${artist || title}`, 'discover');
    request.art_interests = [...new Set([title, artist, artwork.medium].filter((value) => typeof value === 'string' && value.trim()))].slice(0, 8);
    request.number_of_works = 3;
    request.purchase_required = false;
    // This action is anchored to the saved artwork, not any previously selected
    // inspiration image that may still be in the one-tab request state.
    request.inspiration_image_ids = [];
    request.inspiration_images = [];
    try {
      sessionStorage.setItem('artConciergePendingRequest', JSON.stringify(request));
    } catch (error) {
      const count = $('#savedCount');
      if (count) count.textContent = 'Could not start the search in this browser tab. Please allow session storage and try again.';
      return;
    }
    window.location.assign(resultsRunUrl('discover'));
  }

  async function runPendingRequest() {
    const resultsPage = $('#resultsPage');
    if (!resultsPage) return false;
    const raw = sessionStorage.getItem('artConciergePendingRequest');
    if (!raw) {
      // Never fall back to a cached previous search on a URL that explicitly
      // represents a new run. The request handoff may have been cleared by a
      // browser restore or storage failure; show a clean recoverable state.
      const heading = $('#resultsHeading');
      const summary = $('#resultsSummary');
      const grid = $('#artGrid');
      if (heading) heading.textContent = 'This search could not be started.';
      if (summary) summary.textContent = 'Return to your concierge and submit the request again.';
      if (grid) grid.innerHTML = '<p class="empty-state">No current request was received.</p>';
      return false;
    }
    let payload;
    try { payload = JSON.parse(raw); } catch {
      sessionStorage.removeItem('artConciergePendingRequest');
      const heading = $('#resultsHeading');
      const summary = $('#resultsSummary');
      const grid = $('#artGrid');
      if (heading) heading.textContent = 'This search could not be started.';
      if (summary) summary.textContent = 'The request could not be read. Return to your concierge and try again.';
      if (grid) grid.innerHTML = '<p class="empty-state">No current request was received.</p>';
      return false;
    }
    // Consume the one-time handoff before the API call. The image bytes are not
    // retained in sessionStorage, the current result object, or recent-search history.
    sessionStorage.removeItem('artConciergePendingRequest');

    // Invalidate the previously persisted result for this page instance before
    // any asynchronous request starts. This prevents delayed UI events, BFCache
    // restores, or save actions from repainting the prior search mid-transition.
    state.searchRunActive = true;
    state.last = null;
    state.intent = payload.intent || state.intent;
    state.personalizeExplore = Boolean(payload.personalize_with_saved_taste);
    const resultsHeading = $('#resultsHeading');
    const resultsSummary = $('#resultsSummary');
    const resultsMeta = $('#resultsMeta');
    const grid = $('#artGrid');
    const notForSaleSection = $('#notForSaleSection');
    const notForSaleGrid = $('#notForSaleGrid');
    const recommendationSection = $('#recommendationsSection');
    const refinePanel = $('#refinePanel');
    const tasteSummary = $('#tasteSummary');
    const visualSummary = $('#visualInspirationSummary');
    const learningGuide = $('#learningGuideSection');
    const refineButton = $('#refineButton');
    if (resultsHeading) resultsHeading.textContent = 'I’m researching that now.';
    if (resultsSummary) resultsSummary.textContent = 'Starting a fresh search for your current request.';
    if (resultsMeta) { resultsMeta.textContent = ''; resultsMeta.hidden = true; }
    if (grid) grid.innerHTML = '<p class="empty-state">Searching and narrowing the field…</p>';
    if (notForSaleSection) notForSaleSection.hidden = true;
    if (notForSaleGrid) notForSaleGrid.innerHTML = '';
    if (tasteSummary) tasteSummary.hidden = true;
    if (visualSummary) { visualSummary.hidden = true; visualSummary.innerHTML = ''; }
    if (learningGuide) learningGuide.hidden = true;
    if (recommendationSection) recommendationSection.hidden = state.intent === 'learn';
    if (refinePanel) refinePanel.hidden = state.intent === 'learn';
    if (refineButton) { refineButton.disabled = true; refineButton.textContent = 'Researching…'; }

    let apiPayload = null;
    try {
      apiPayload = {
        ...payload,
        inspiration_images: Array.isArray(payload.inspiration_images) ? payload.inspiration_images.slice(0, MAX_REQUEST_IMAGES) : [],
        inspiration_image_ids: Array.isArray(payload.inspiration_images) ? payload.inspiration_images.slice(0, MAX_REQUEST_IMAGES).map((image) => image.image_id) : [],
      };
      const response = await fetch('/api/concierge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(apiPayload),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        throw new Error(detail?.detail?.[0]?.msg || detail?.detail || `Request failed (${response.status})`);
      }
      const data = await response.json();
      if (data.success === false) throw new Error(data.detail || 'The image-informed search could not be completed.');
      const historyRequest = { ...payload };
      delete historyRequest.inspiration_images;
      delete historyRequest.inspiration_image_ids;
      data.request = historyRequest;
      state.searchRunActive = false;
      saveLastResult(data);
      saveRecent(historyRequest, data);
      renderResults(data);
      return true;
    } catch (error) {
      state.searchRunActive = false;
      state.last = null;
      if (resultsHeading) resultsHeading.textContent = 'I couldn’t complete that search.';
      if (resultsSummary) resultsSummary.textContent = error.message || 'Please try again.';
      if (resultsMeta) { resultsMeta.textContent = ''; resultsMeta.hidden = true; }
      if (grid) grid.innerHTML = '<p class="empty-state">Return to your concierge and try the request again.</p>';
      if (refineButton) { refineButton.disabled = true; refineButton.textContent = 'Refine'; }
      return false;
    } finally {
      state.searchRunActive = false;
      if (refineButton) { refineButton.disabled = false; refineButton.textContent = 'Refine'; }
      if (apiPayload) apiPayload.inspiration_images = [];
      if (payload && typeof payload === 'object') {
        delete payload.inspiration_images;
        delete payload.inspiration_image_ids;
      }
      apiPayload = null;
    }
  }

  function renderTasteSummary(data) {
    const box = $('#tasteSummary');
    if (!box) return;
    const inputs = data.taste?.inputs || [];
    const visual = data.visual_inspiration?.visual_attributes || [];
    const colours = data.visual_inspiration?.colour_palette || [];
    const tags = [...new Set([...inputs, ...visual, ...colours].filter(Boolean))].slice(0, 8);
    if (!tags.length) { box.hidden = true; return; }
    box.hidden = false;
    const hasCulturalInputs = inputs.length > 0;
    const hasVisualInputs = visual.length > 0 || colours.length > 0;
    $('#tasteSummaryCopy').textContent = hasCulturalInputs && hasVisualInputs
      ? 'Your stated interests and visual references guided this search.'
      : hasVisualInputs
        ? 'The visual traits in your inspiration images guided this search.'
        : 'Your stated interests guided this search.';
    $('#tasteTags').innerHTML = tags.map((tag) => `<span class="taste-tag">${escapeHtml(tag)}</span>`).join('');
  }

  function artworkFallbackUrl(title, artist) {
    const params = new URLSearchParams({ title: String(title || ''), artist: String(artist || '') });
    return `/api/artwork-image-fallback?${params.toString()}`;
  }

  function bindArtworkImageFallbacks(root) {
    if (!root) return;
    root.querySelectorAll('img[data-artwork-image]').forEach((image) => {
      if (image.dataset.fallbackBound === 'true') return;
      image.dataset.fallbackBound = 'true';
      const advanceFallback = () => {
        // Use the provider's direct artwork URL first. Generic Open Graph metadata
        // often points at a square logo or site placeholder rather than the artwork.
        const originalSrc = image.dataset.originalSrc;
        if (originalSrc && image.dataset.triedOriginalSrc !== 'true') {
          image.dataset.triedOriginalSrc = 'true';
          image.src = originalSrc;
          return;
        }
        // Next search institutional sources for this exact title and artist.
        if (image.dataset.triedImageFallback !== 'true') {
          image.dataset.triedImageFallback = 'true';
          image.src = artworkFallbackUrl(image.dataset.artworkTitle, image.dataset.artworkArtist);
          return;
        }
        // Only try source-page metadata last because it may be a generic square logo.
        const detailUrl = image.dataset.detailUrl;
        if (detailUrl && image.dataset.triedDetailPreview !== 'true') {
          image.dataset.triedDetailPreview = 'true';
          image.src = `/api/artwork-preview?url=${encodeURIComponent(detailUrl)}`;
          return;
        }
        // A failed remote image must never remove an artwork result. The source
        // listing can still be useful for its title, artist, price and seller link.
        // Keep the card and replace only the image area with a neutral placeholder.
        if (image.dataset.imageUnavailable === 'true') return;
        image.dataset.imageUnavailable = 'true';
        image.style.display = 'none';
        const host = image.closest('.art-image-wrap, .saved-thumb') || image.parentElement;
        if (host && !host.querySelector('.art-image-unavailable')) {
          if (host.classList.contains('saved-thumb')) host.classList.add('no-image');
          const placeholder = document.createElement('div');
          placeholder.className = 'art-image-unavailable';
          placeholder.setAttribute('role', 'img');
          const artworkTitle = String(image.dataset.artworkTitle || image.alt || 'Artwork').trim();
          placeholder.setAttribute('aria-label', `Image unavailable for ${artworkTitle}`);
          const label = document.createElement('span');
          label.textContent = 'Image unavailable';
          placeholder.appendChild(label);
          host.appendChild(placeholder);
        }
      };
      image.addEventListener('error', advanceFallback);
      // A cached request can fail before handlers are attached after the card
      // markup is inserted. Check that case as well, not only future error events.
      if (image.complete && image.naturalWidth === 0) advanceFallback();
    });
  }

  function artworkPreviewUrl(detailUrl) {
    return detailUrl ? `/api/artwork-preview?url=${encodeURIComponent(detailUrl)}` : '';
  }

  function renderCard(work, options = {}) {
    const savedLayout = options.savedLayout === true;
    const source = String(work.source || '').trim();
    const locationLine = savedLayout && source
      ? `<div class="art-location" title="${escapeAttribute(source)}">${escapeHtml(source)}</div>`
      : '';
    const footerSource = savedLayout ? '' : `<span class="art-source" title="${escapeAttribute(source)}">${escapeHtml(source)}</span>`;
    const available = work.availability === 'available' && work.source_kind === 'commercial';
    const availability = available ? 'available' : 'not_for_sale';
    const meta = [work.medium, work.dimensions, work.year, work.price_label].filter(Boolean).join(' · ');
    const directImageUrl = work.image_url || '';
    const pagePreviewUrl = artworkPreviewUrl(work.detail_url);
    const imageDisplayUrl = work.display_image_url || directImageUrl || pagePreviewUrl || artworkFallbackUrl(work.title, work.artist);
    const previewIsInitial = Boolean(pagePreviewUrl && imageDisplayUrl === pagePreviewUrl);
    const fallbackIsInitial = imageDisplayUrl === artworkFallbackUrl(work.title, work.artist);
    const image = `<img data-artwork-image data-artwork-title="${escapeAttribute(work.title || '')}" data-artwork-artist="${escapeAttribute(work.artist || '')}" data-detail-url="${escapeAttribute(work.detail_url || '')}" ${work.display_image_url && work.display_image_url !== directImageUrl && directImageUrl ? `data-original-src="${escapeAttribute(directImageUrl)}"` : ''} ${previewIsInitial ? 'data-tried-detail-preview="true"' : ''} ${fallbackIsInitial ? 'data-tried-image-fallback="true"' : ''} src="${escapeAttribute(imageDisplayUrl)}" alt="${escapeAttribute(work.title || 'Artwork')} by ${escapeAttribute(work.artist || 'Unknown artist')}" loading="eager" decoding="async" fetchpriority="high" referrerpolicy="no-referrer">`;
    const id = escapeAttribute(work.id);
    const sellerRedirectUrl = available && work.detail_url
      ? `/api/acquisition/redirect?${new URLSearchParams({ artwork_id: String(work.id || ''), url: String(work.detail_url), title: String(work.title || ''), artist: String(work.artist || ''), seller: String(work.seller_name || work.source || ''), amount: work.price == null ? '' : String(work.price), currency: String(work.currency || ''), market: String(work.market_code || '') }).toString()}`
      : String(work.detail_url || '');
    const linkLabel = available ? `Buy at ${String(work.source || work.seller_name || 'seller').slice(0, 36)}` : 'See at source';
    const externalLink = work.detail_url
      ? `<a class="card-action" href="${escapeAttribute(sellerRedirectUrl)}" ${available ? '' : 'target="_blank"'} rel="noopener noreferrer">${escapeHtml(linkLabel)} →</a>`
      : '';
    const checkoutButton = available && work.stripe_checkout_available
      ? `<button class="card-action buy-action" type="button" data-id="${id}">Pay securely</button>`
      : '';
    const paypalButton = available && work.paypal_checkout_available
      ? `<button class="card-action paypal-buy-action" type="button" data-id="${id}" data-source-url="${escapeAttribute(work.detail_url || '')}">Buy securely</button>`
      : '';
    const invoiceButton = available && work.paypal_invoice_available
      ? `<button class="card-action paypal-invoice-action" type="button" data-id="${id}" data-source-url="${escapeAttribute(work.detail_url || '')}">Request invoice</button>`
      : '';
    const link = `${checkoutButton}${paypalButton}${invoiceButton}${externalLink}`;
    const saved = state.saved.some((item) => item.id === work.id);
    const compared = state.compare.some((item) => item.id === work.id);
    const watched = state.alerts.some((item) => item.type === 'artwork' && item.target?.artwork?.id === work.id);
    const shopUrlByName = { ArtMatch: 'https://artmatch.ca/canadian-art-for-sale/', 'Rookleys Canadian Art Gallery': 'https://rookleys.com/artworks/', UGallery: 'https://www.ugallery.com/collections/all-artworks', 'Saatchi Art': 'https://www.saatchiart.com/all' };
    const watchable = work.source_kind === 'commercial' && Boolean(shopUrlByName[work.seller_name || work.source]);
    const followingSeller = state.alerts.some((item) => item.type === 'seller' && item.target?.seller_name === (work.seller_name || work.source));
    const reasons = (work.why || []).map((item) => String(item)).join(' · ');
    const collectionRemove = options.collectionId
      ? `<button class="card-action collection-remove-action" type="button" data-collection-remove="${escapeAttribute(options.collectionId)}" data-id="${id}">Remove from collection</button>` : '';
    return `
      <article class="art-card${savedLayout ? ' saved-art-card' : ''}" data-art-id="${id}">
        <div class="art-image-wrap">${image}</div>
        <div class="art-body">
          <div class="art-title">${escapeHtml(work.title || 'Untitled')}</div>
          <div class="art-artist">${escapeHtml(work.artist || 'Unknown artist')}</div>
          ${locationLine}
          <div class="art-meta"${meta ? '' : ' hidden'}>${escapeHtml(meta)}</div>
          <div class="art-reason">${escapeHtml(reasons)}</div>
          <div class="art-footer"><span class="availability ${availability}">${available ? 'AVAILABLE TO ACQUIRE' : 'NOT FOR SALE'}</span>${footerSource}</div>
          <div class="art-actions">
            ${link}
            <button class="card-action save-action" type="button" data-id="${id}" aria-pressed="${saved}">${saved ? 'Saved' : 'Save'}</button>
            <button class="card-action compare-action" type="button" data-compare-id="${id}" aria-pressed="${compared}">${compared ? 'In comparison' : 'Compare'}</button>
            <button class="card-action add-collection-action" type="button" data-add-to-collection="${id}">Add to collection</button>
            ${watchable ? `<button class="card-action watch-artwork-action" type="button" data-watch-artwork="${id}" ${watched ? 'disabled' : ''}>${watched ? 'Watching artwork' : 'Watch artwork'}</button><button class="card-action follow-shop-action" type="button" data-follow-seller="${escapeAttribute(work.seller_name || work.source)}" data-market="${escapeAttribute(work.market_code || 'CA')}" data-shop-url="${escapeAttribute(shopUrlByName[work.seller_name || work.source])}" ${followingSeller ? 'disabled' : ''}>${followingSeller ? 'Following seller' : 'Follow seller'}</button>` : ''}
            ${collectionRemove}
            <button class="card-action feedback-action" type="button" data-id="${id}" data-feedback="More like ${escapeAttribute(work.artist || work.title)}">More like this</button>
            <button class="card-action feedback-action" type="button" data-id="${id}" data-feedback="Not for me: ${escapeAttribute(work.artist || work.title)}">Not for me</button>
          </div>
        </div>
      </article>`;
  }

  function renderLearningGuide(guide, intent) {
    const section = $('#learningGuideSection');
    if (!section) return;
    if (intent !== 'learn' || !guide || !guide.overview) {
      section.hidden = true;
      return;
    }
    section.hidden = false;
    $('#learningGuideTopic').textContent = guide.topic ? `A starting point: ${guide.topic}` : 'A starting point for exploring the subject';
    $('#learningGuideOverview').textContent = guide.overview;
    const artistWorksSection = $('#learningArtistWorksSection');
    const artistWorksHeading = $('#learningArtistWorksHeading');
    const artistWorksList = $('#learningMajorWorksList');
    const majorWorks = Array.isArray(guide.major_works) ? guide.major_works.slice(0, 5) : [];
    if (artistWorksSection && artistWorksList) {
      artistWorksSection.hidden = !guide.artist_name || majorWorks.length === 0;
      if (artistWorksHeading) artistWorksHeading.textContent = guide.artist_name ? `Selected works by ${guide.artist_name}` : 'Selected works';
      artistWorksList.innerHTML = majorWorks.map((work) => {
        const meta = work.period ? `<small>${escapeHtml(work.period)}</small>` : '';
        return `<li><strong>${escapeHtml(work.title || '')}</strong>${meta}<span>${escapeHtml(work.significance || '')}</span></li>`;
      }).join('');
    }
    const list = (id, values) => {
      const node = $(`#${id}`);
      if (node) node.innerHTML = (values || []).map((value) => `<li>${escapeHtml(value)}</li>`).join('');
    };
    list('learningKeyIdeas', guide.key_ideas);
    list('learningLookCloser', guide.look_closer);
    list('learningQuestions', guide.questions);
    const terms = $('#learningTerms');
    if (terms) terms.innerHTML = (guide.terms || []).map((item) => `<div class="learning-term"><dt>${escapeHtml(item.term || '')}</dt><dd>${escapeHtml(item.definition || '')}</dd></div>`).join('');
    const examplesSection = $('#learningExamplesSection');
    const examples = $('#learningExamples');
    const works = Array.isArray(guide.related_works) ? guide.related_works : [];
    if (examplesSection && examples) {
      examplesSection.hidden = works.length === 0;
      examples.innerHTML = works.map((work) => {
        const details = [work.medium, work.year, work.source].filter(Boolean).join(' · ');
        const link = work.detail_url ? `<a href="${escapeAttribute(work.detail_url)}" target="_blank" rel="noopener noreferrer">View source record →</a>` : '';
        return `<article class="learning-example"><strong>${escapeHtml(work.title || 'Untitled')}</strong><span>${escapeHtml(work.artist || 'Unknown artist')}</span><small>${escapeHtml(details)}</small>${link}</article>`;
      }).join('');
    }
    const note = $('#learningSourceNote');
    if (note) note.textContent = guide.source_note || '';
  }

  async function saveCuratedResultsAsCollection() {
    const message = $('#saveCuratedCollectionMessage');
    const button = $('#saveCuratedCollectionButton');
    const input = $('#curatedCollectionName');
    const data = state.last;
    const name = String(input?.value || '').trim();
    if (!data || data.intent !== 'curate') throw new Error('Run Curate a collection in Concierge first.');
    if (name.length < 2) throw new Error('Give this collection a name of at least two characters.');
    const works = (data.results || []).filter((work) => work.source_kind === 'commercial' && work.availability === 'available').slice(0, 3);
    if (!works.length) throw new Error('There are no purchasable artworks in this curated result to save.');
    if (button) { button.disabled = true; button.textContent = 'Saving collection…'; }
    if (message) message.textContent = '';
    try {
      const description = String(data.request?.goal || data.summary || '').trim().slice(0, 500);
      const collection = await createCollection(name, description);
      for (const work of works) await addArtworkToCollection(collection.id, work);
      if (message) message.innerHTML = `Saved ${works.length} artwork${works.length === 1 ? '' : 's'} to <strong>${escapeHtml(collection.name)}</strong>. <a href="/dashboard#collections">View your collections</a>.`;
      if (input) input.value = '';
      const section = $('#curatedCollectionSection');
      if (section) section.dataset.savedCollectionId = collection.id;
    } finally {
      if (button) { button.disabled = false; button.textContent = 'Save collection'; }
    }
  }

  function renderResults(data) {
    // Ignore stale render attempts while a fresh request owns the Results view.
    if (state.searchRunActive) return;
    const intent = data.intent || state.intent;
    const labels = {
      discover: data.request?.personalize_with_saved_taste ? 'Art to explore from your taste.' : 'Works worth a closer look.',
      find: 'The closest fits I found.',
      taste: 'Art to explore from your taste.',
      curate: 'Works that make sense together.',
      buy: 'Art you can act on now.',
      learn: 'Your learning guide.',
    };
    $('#resultsHeading').textContent = labels[intent] || labels.discover;
    $('#resultsSummary').textContent = data.summary || '';
    const learningOnly = intent === 'learn';
    const recommendationSection = $('#recommendationsSection');
    const refinePanel = $('#refinePanel');
    if (recommendationSection) recommendationSection.hidden = learningOnly;
    if (refinePanel) refinePanel.hidden = learningOnly;
    const resultsMeta = $('#resultsMeta');
    if (resultsMeta) resultsMeta.hidden = learningOnly;
    // Curate, personalised Explore, and Buy only show commercial inventory explicitly
    // listed as available. This guard also protects against older cached responses.
    const usesSavedTaste = intent === 'taste' || Boolean(data.request?.personalize_with_saved_taste);
    const purchaseOnlyIntent = ['buy', 'curate', 'taste'].includes(intent) || usesSavedTaste;
    const results = (data.results || []).filter((work) => !purchaseOnlyIntent
      || (work.source_kind === 'commercial' && work.availability === 'available')).slice(0, 3);
    const notForSale = [];
    const visibleCount = results.length + notForSale.length;
    const visiblePurchasable = results.filter((work) => work.availability === 'available' && work.source_kind === 'commercial').length;
    const curatedCollectionSection = $('#curatedCollectionSection');
    const curatedCollectionName = $('#curatedCollectionName');
    if (curatedCollectionSection) {
      const canSaveCuratedSet = intent === 'curate' && results.length > 0;
      curatedCollectionSection.hidden = !canSaveCuratedSet;
      if (canSaveCuratedSet) {
        curatedCollectionSection.dataset.savedCollectionId = '';
        if (curatedCollectionName && !curatedCollectionName.value) curatedCollectionName.placeholder = 'e.g. Warm-toned landscapes';
      }
    }
    const buyShopsSection = $('#buyShopsSection');
    const buyShopLinks = $('#buyShopLinks');
    if (buyShopsSection && buyShopLinks) {
      const showBuyShops = intent === 'buy';
      buyShopsSection.hidden = !showBuyShops;
      if (showBuyShops) {
        const market = ['CA', 'US', 'BOTH'].includes(data.region_scope)
          ? data.region_scope
          : (['CA', 'US', 'BOTH'].includes(state.preferences.regionScope) ? state.preferences.regionScope : 'CA');
        const canadianShops = [
          { name: 'ArtMatch', description: 'Original art from Canadian artists', url: 'https://artmatch.ca/canadian-art-for-sale/' },
          { name: 'Rookleys Canadian Art Gallery', description: 'Canadian artworks listed for sale', url: 'https://rookleys.com/artworks/' },
        ];
        const usShops = [
          { name: 'UGallery', description: 'Original paintings and artworks from a U.S.-based online gallery', url: 'https://www.ugallery.com/collections/all-artworks' },
          { name: 'Saatchi Art', description: 'Original art, paintings, photography, and sculpture', url: 'https://www.saatchiart.com/all' },
        ];
        const shops = market === 'CA' ? canadianShops : market === 'US' ? usShops : [...canadianShops, ...usShops];
        const heading = $('#buyShopsHeading');
        if (heading) heading.textContent = market === 'CA'
          ? 'Browse Canadian art shops'
          : market === 'US' ? 'Browse U.S. art shops' : 'Browse Canadian and U.S. art shops';
        const shopMarkets = { ArtMatch: 'CA', 'Rookleys Canadian Art Gallery': 'CA', UGallery: 'US', 'Saatchi Art': 'US' };
        buyShopLinks.innerHTML = shops.map((shop) => `<article class="buy-shop-link"><a href="${escapeAttribute(shop.url)}" target="_blank" rel="noopener noreferrer"><strong>${escapeHtml(shop.name)}</strong><span>${escapeHtml(shop.description)}</span><span class="buy-shop-action">Browse shop ↗</span></a><button class="text-button follow-shop-action" type="button" data-follow-seller="${escapeAttribute(shop.name)}" data-market="${shopMarkets[shop.name] || market}" data-shop-url="${escapeAttribute(shop.url)}">${state.alerts.some((alert) => alert.type === 'seller' && alert.target?.seller_name === shop.name) ? 'Following shop' : 'Notify me about new art'}</button></article>`).join('');
      } else {
        buyShopLinks.innerHTML = '';
      }
    }
    if (intent === 'buy') {
      if (visiblePurchasable > 0) {
        const shopNames = [...new Set(results.map((work) => work.source).filter(Boolean))];
        $('#resultsSummary').textContent = `Found ${visiblePurchasable} listing${visiblePurchasable === 1 ? '' : 's'} marked available${shopNames.length ? ` from ${shopNames.join(', ')}` : ''}. Confirm final price and availability with the seller.`;
      } else {
        const diagnostics = data.diagnostics || {};
        const region = ['CA', 'US', 'BOTH'].includes(data.region_scope)
          ? data.region_scope
          : (['CA', 'US', 'BOTH'].includes(state.preferences.regionScope) ? state.preferences.regionScope : 'CA');
        const shopNames = region === 'CA' ? ['ArtMatch', 'Rookleys Canadian Art Gallery']
          : region === 'US' ? ['UGallery', 'Saatchi Art']
          : ['ArtMatch', 'Rookleys Canadian Art Gallery', 'UGallery', 'Saatchi Art'];
        const stats = (diagnostics.source_diagnostics || []).filter((source) => shopNames.includes(source.provider));
        const allFailed = stats.length > 0 && stats.every((source) => Array.isArray(source.errors) && source.errors.length > 0);
        const marketLabel = region === 'CA' ? 'Canadian' : region === 'US' ? 'U.S.' : 'Canadian and U.S.';
        $('#resultsSummary').textContent = allFailed
          ? `I could not retrieve live listings from the ${marketLabel} shops just now. Browse the shops below or retry.`
          : `No listings matched this search in the ${marketLabel} shops. Browse the shops below or broaden your search.`;
      }
    }
    $('#resultsMeta').textContent = purchaseOnlyIntent
      ? `${visibleCount} work${visibleCount === 1 ? '' : 's'} shown · all available to acquire`
      : `${visibleCount} recommendation${visibleCount === 1 ? '' : 's'} shown`;
    if (results.length) {
      $('#artGrid').innerHTML = results.map(renderCard).join('');
      bindArtworkImageFallbacks($('#artGrid'));
    } else {
      const emptyMessage = purchaseOnlyIntent
        ? 'No matching artwork cards were returned. Use the shop links below to browse current inventory directly.'
        : 'The connected art collections did not return a match with a retrievable image. Try a broader art style, artist, subject, or medium.';
      // Provider configuration and HTTP failures stay in /api/diagnostics and
      // server logs. They are not user-facing copy and must never leak a museum
      // outage into the Buy experience.
      $('#artGrid').innerHTML = `<p class="empty-state">${escapeHtml(emptyMessage)}</p>`;
    }

    $('#notForSaleSection').hidden = purchaseOnlyIntent || !notForSale.length;
    if (notForSale.length) {
      $('#notForSaleGrid').innerHTML = notForSale.map(renderCard).join('');
      bindArtworkImageFallbacks($('#notForSaleGrid'));
      $('#notForSaleHint').textContent = 'Additional works from museum and institutional collections. These are not available for purchase.';
    }
    renderLearningGuide(data.learning_guide, intent);
    renderVisualInspirationSummary(data.visual_inspiration || {}, intent);
    renderTasteSummary(data);
    if (learningOnly) {
      const tasteSummary = $('#tasteSummary');
      if (tasteSummary) tasteSummary.hidden = true;
    }
  }

  function renderVisualInspirationSummary(inspiration, intent = state.intent) {
    const panel = $('#visualInspirationSummary');
    if (!panel) return;
    const count = Number(inspiration.image_count || 0);
    if (!count) { panel.hidden = true; panel.innerHTML = ''; return; }
    panel.hidden = false;
    const heading = intent === 'learn' ? 'How your image informed the guide' : inspiration.analyzed ? 'How your images guided the search' : 'Your images were received';
    const detail = inspiration.analyzed
      ? (inspiration.summary || (intent === 'learn' ? 'Vincent used the visual characteristics in your image as part of this learning guide.' : 'Vincent used the visual characteristics in your images to guide the artwork search.'))
      : (inspiration.message || 'Visual analysis was unavailable, so this search used your written request and saved taste.');
    const tags = [...new Set([...(inspiration.visual_attributes || []), ...(inspiration.colour_palette || [])])].slice(0, 8);
    panel.innerHTML = `<div class="visual-inspiration-result-copy"><strong>${escapeHtml(heading)}</strong><p>${escapeHtml(detail)}</p></div>${tags.length ? `<div class="visual-inspiration-result-tags">${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join('')}</div>` : ''}`;
  }

  function getArtworkById(id) {
    return state.last?.results?.find((work) => work.id === id)
      || state.last?.not_for_sale?.find((work) => work.id === id)
      || state.saved.find((work) => work.id === id)
      || state.compare.find((work) => work.id === id)
      || state.collections.flatMap((collection) => collection.artworks || []).find((work) => work.id === id)
      || null;
  }

  async function toggleSave(id) {
    const artwork = getArtworkById(id);
    if (!artwork) return;
    const index = state.saved.findIndex((item) => item.id === id);
    const removing = index >= 0;
    if (removing) state.saved.splice(index, 1);
    else state.saved.unshift(artwork);
    state.saved = state.saved.slice(0, 40);
    await persistAppState();
    if (state.user) {
      try { await syncSavedToServer(artwork, removing ? 'remove' : 'save'); }
      catch (error) { console.warn('Saved artwork could not sync to the account.', error.message); }
    }
    if (window.ART_CONCIERGE_PAGE === 'results' && state.last) renderResults(state.last);
    if (window.ART_CONCIERGE_PAGE === 'saved') renderSavedPage();
  }

  function renderSavedPage() {
    const grid = $('#savedGrid');
    if (!grid) return;
    const count = $('#savedCount');
    if (count) count.textContent = state.saved.length ? `${state.saved.length} saved work${state.saved.length === 1 ? '' : 's'}` : '';
    if (!state.saved.length) {
      grid.innerHTML = '<p class="empty-state">You have not saved any works yet. Start a conversation with your concierge to find something worth keeping.</p>';
      return;
    }
    grid.innerHTML = state.saved.map((work) => renderCard(work, { savedLayout: true })).join('');
    bindArtworkImageFallbacks(grid);
  }

  function renderTastePage() {
    if (!state.tasteDraft) state.tasteDraft = cloneProfile(state.profile);
    const profile = state.tasteDraft;
    const chips = $('#tasteChips');
    const avoids = $('#avoidChips');
    const notes = $('#tasteNotes');
    const slider = $('#discoveryLevel');
    const updated = $('#tasteUpdated');
    if (!chips || !notes) return;
    chips.innerHTML = profile.loves.map((value, index) => `
      <span class="chip">${escapeHtml(value)} <button type="button" data-remove-index="${index}" aria-label="Remove ${escapeAttribute(value)} from things you love">×</button></span>
    `).join('');
    if (avoids) avoids.innerHTML = profile.avoids.map((value, index) => `
      <span class="chip">${escapeHtml(value)} <button type="button" data-remove-list="avoids" data-remove-index="${index}" aria-label="Remove ${escapeAttribute(value)} from things you avoid">×</button></span>
    `).join('');
    notes.value = profile.notes;
    if (slider) slider.value = String(profile.discoveryLevel);
    if (updated) updated.textContent = tasteDraftIsDirty() ? 'Unsaved changes' : (state.profile.updatedAt ? `Updated ${new Date(state.profile.updatedAt).toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' })}` : 'Not yet saved');
  }

  async function recordCheckoutStarted(session, artwork) {
    const now = new Date().toISOString();
    const sessionId = String(session.session_id || '').trim();
    if (!sessionId) throw new Error('The checkout session was created without a reference. Please try again.');
    const record = {
      sessionId,
      provider: String(session.provider || 'stripe'),
      artworkId: String(artwork?.id || session.artwork_id || ''),
      title: String(artwork?.title || 'Artwork checkout'),
      artist: String(artwork?.artist || 'Artist not provided'),
      imageUrl: String(artwork?.display_image_url || artwork?.image_url || ''),
      checkoutStatus: 'open',
      paymentStatus: 'unpaid',
      amountTotal: null,
      currency: null,
      createdAt: now,
      updatedAt: now,
    };
    state.purchases = [record, ...state.purchases.filter((item) => item.sessionId !== sessionId)].slice(0, 12);
    await persistAppState();
    return record;
  }

  async function recordVerifiedPurchase(data) {
    const sessionId = String(data.session_id || '').trim();
    if (!sessionId) return null;
    const existing = state.purchases.find((item) => item.sessionId === sessionId) || {};
    const paymentStatus = String(data.payment_status || existing.paymentStatus || 'unpaid').toLowerCase();
    const checkoutStatus = String(data.status || existing.checkoutStatus || 'open').toLowerCase();
    const artworkId = String(data.artwork_id || existing.artworkId || '');
    const record = {
      ...existing,
      sessionId,
      provider: String(data.provider || existing.provider || 'stripe'),
      artworkId,
      title: String(data.title || existing.title || artworkId || 'Artwork checkout'),
      artist: String(data.artist || existing.artist || 'Artist not provided'),
      sellerName: String(data.seller_name || existing.sellerName || ''),
      sourceUrl: String(data.source_url || existing.sourceUrl || ''),
      checkoutStatus,
      paymentStatus,
      amountTotal: data.amount_total ?? existing.amountTotal ?? null,
      currency: data.currency || existing.currency || null,
      createdAt: existing.createdAt || (data.created ? new Date(Number(data.created) * 1000).toISOString() : new Date().toISOString()),
      updatedAt: new Date().toISOString(),
    };
    state.purchases = [record, ...state.purchases.filter((item) => item.sessionId !== sessionId)].slice(0, 12);
    await persistAppState();
    return record;
  }

  async function checkTrackedPurchase(sessionId, button, provider = 'stripe') {
    const message = $('#dashboardPurchasesMessage');
    const session = String(sessionId || '').trim();
    if (!session) return;
    if (button) { button.disabled = true; button.textContent = 'Checking…'; }
    if (message) message.textContent = provider === 'paypal_invoice' ? 'Checking invoice status with PayPal…' : `Checking payment status with ${provider === 'paypal' ? 'PayPal' : 'Stripe'}…`;
    try {
      const response = provider === 'paypal_invoice'
        ? await fetch(`/api/paypal/invoices/${encodeURIComponent(session)}/status`, { headers: { Accept: 'application/json' } })
        : provider === 'paypal'
          ? await fetch(`/api/paypal/orders/${encodeURIComponent(session)}/capture`, { method: 'POST', headers: { Accept: 'application/json' } })
          : await fetch(`/api/checkout/status?session_id=${encodeURIComponent(session)}`, { headers: { Accept: 'application/json' } });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.success) throw new Error(data.detail || 'Payment status could not be checked.');
      await recordVerifiedPurchase(data);
      renderPurchaseHistory();
      if (message) message.textContent = provider === 'paypal_invoice' ? 'Invoice status updated from PayPal.' : `Payment status updated from ${provider === 'paypal' ? 'PayPal' : 'Stripe'}.`;
    } catch (error) {
      if (message) message.textContent = error.message || 'Payment status could not be checked. Try again.';
      if (button) { button.disabled = false; button.textContent = provider === 'paypal_invoice' ? 'Check invoice' : 'Check payment'; }
    }
  }

  async function startCheckout(id, button) {
    const message = $('#purchaseMessage');
    const artwork = getArtworkById(String(id));
    if (!(await requireSignInForPurchase())) return;
    if (button) {
      button.disabled = true;
      button.textContent = 'Opening checkout…';
    }
    if (message) message.textContent = 'Opening secure checkout…';
    try {
      const response = await fetch('/api/checkout/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ artwork_id: id }),
      });
      const data = await response.json().catch(() => ({}));
      if (response.status === 401) { await requireSignInForPurchase(); return; }
      if (!response.ok || !data.success || !data.checkout_url || !data.session_id) {
        throw new Error(data.detail || 'Checkout is not available for this work.');
      }
      await recordCheckoutStarted(data, artwork);
      window.location.assign(data.checkout_url);
    } catch (error) {
      if (message) message.textContent = error.message || 'Checkout could not be started.';
      if (button) {
        button.disabled = false;
        button.textContent = 'Buy securely';
      }
    }
  }

  async function requireSignInForPurchase() {
    if (state.user) return true;
    const next = window.location.pathname + window.location.search + window.location.hash;
    window.location.assign(`/login?next=${encodeURIComponent(next)}`);
    return false;
  }

  async function startPayPalCheckout(id, button) {
    const artwork = getArtworkById(String(id));
    if (!artwork) return;
    if (!(await requireSignInForPurchase())) return;
    if (button) { button.disabled = true; button.textContent = 'Opening secure checkout…'; }
    const message = $('#purchaseMessage');
    if (message) message.textContent = 'Starting secure checkout…';
    try {
      const data = await accountFetch('/api/paypal/orders', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ artwork_id: id, source_url: artwork.detail_url, title: artwork.title, artist: artwork.artist, seller_name: artwork.seller_name || artwork.source }),
      });
      window.location.assign(data.checkout_url);
    } catch (error) {
      if (message) message.textContent = error.message || 'PayPal Sandbox checkout could not be started. Try again later.';
      if (button) { button.disabled = false; button.textContent = 'Buy securely'; }
    }
  }

  async function startPayPalSandboxTest(button) {
    if (!(await requireSignInForPurchase())) return;
    const message = $('#dashboardPurchasesMessage');
    if (button) { button.disabled = true; button.textContent = 'Opening PayPal Sandbox…'; }
    if (message) message.textContent = 'Creating a $1.00 CAD Sandbox test order…';
    try {
      const data = await accountFetch('/api/paypal/sandbox-test/orders', {
        method: 'POST', headers: { Accept: 'application/json' },
      });
      await recordCheckoutStarted(data, { id: data.artwork_id, title: data.title, artist: data.artist });
      if (message) message.textContent = 'Opening PayPal Sandbox. Sign in with your Sandbox buyer account to approve the test payment.';
      window.location.assign(data.checkout_url);
    } catch (error) {
      if (message) message.textContent = error.message || 'The PayPal Sandbox test could not be started.';
      if (button) { button.disabled = false; button.textContent = 'Test PayPal Sandbox · $1.00 CAD'; }
    }
  }

  async function requestPayPalInvoice(id, button) {
    const artwork = getArtworkById(String(id));
    if (!artwork) return;
    if (!(await requireSignInForPurchase())) return;
    if (button) { button.disabled = true; button.textContent = 'Requesting invoice…'; }
    const message = $('#purchaseMessage');
    if (message) message.textContent = 'Requesting an invoice…';
    try {
      const data = await accountFetch('/api/paypal/invoices', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ artwork_id: id, source_url: artwork.detail_url, title: artwork.title, artist: artwork.artist, seller_name: artwork.seller_name || artwork.source }),
      });
      if (message) message.textContent = 'Invoice created. The payment details are available in Purchases.';
      if (data.invoice_url && message) message.innerHTML = `Invoice created. <a href="${escapeAttribute(data.invoice_url)}" target="_blank" rel="noopener noreferrer">View invoice</a>`;
      try {
        const account = await accountFetch('/api/account/state');
        state.purchases = account.purchases || state.purchases;
        renderPurchaseHistory();
      } catch (_) {}
    } catch (error) {
      if (message) message.textContent = 'The invoice could not be created. Try the seller’s purchasing options or try again later.';
      if (button) { button.disabled = false; button.textContent = 'Request invoice'; }
    }
  }

  async function refreshCheckoutStatus() {
    const card = $('#checkoutStatusCard');
    if (!card) return;
    const heading = $('#checkoutStatusHeading');
    const message = $('#checkoutStatusMessage');
    const details = $('#checkoutOrderDetails');
    const reference = $('#checkoutStatusReference');
    const provider = card.dataset.provider || 'stripe';
    const params = new URLSearchParams(window.location.search);
    const sessionId = card.dataset.sessionId || params.get(provider === 'paypal' ? 'token' : 'session_id') || '';
    if (!sessionId) {
      if (heading) heading.textContent = 'Payment status unavailable';
      if (message) message.textContent = provider === 'paypal' ? 'This return link does not include a PayPal order reference.' : 'This return link does not include a Stripe Checkout Session ID, so Vincent cannot verify payment.';
      return;
    }
    const retry = $('#refreshCheckoutStatus');
    if (retry) { retry.disabled = true; retry.textContent = 'Checking…'; }
    try {
      const response = provider === 'paypal'
        ? await fetch(`/api/paypal/orders/${encodeURIComponent(sessionId)}/capture`, { method: 'POST', headers: { Accept: 'application/json' } })
        : await fetch(`/api/checkout/status?session_id=${encodeURIComponent(sessionId)}`, { headers: { Accept: 'application/json' } });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.success) throw new Error(data.detail || `${provider === 'paypal' ? 'PayPal' : 'Stripe'} could not verify this checkout.`);
      data.provider = provider;
      const payment = String(data.payment_status || 'unpaid').toLowerCase();
      const status = String(data.status || 'open').toLowerCase();
      await recordVerifiedPurchase(data);
      if (payment === 'paid' || payment === 'no_payment_required') {
        if (heading) heading.textContent = 'Payment confirmed';
        if (message) message.textContent = `${provider === 'paypal' ? 'PayPal' : 'Stripe'} confirms this payment is complete. Keep the order reference for any questions about the purchase.`;
      } else if (status === 'complete') {
        if (heading) heading.textContent = 'Payment processing';
        if (message) message.textContent = 'Stripe reports that checkout finished, but payment is not yet marked as paid. Check again shortly before treating this order as confirmed.';
      } else if (status === 'expired') {
        if (heading) heading.textContent = 'Checkout expired';
        if (message) message.textContent = 'Stripe reports that this checkout session expired before payment was confirmed.';
      } else {
        if (heading) heading.textContent = 'Payment not confirmed';
        if (message) message.textContent = `${provider === 'paypal' ? 'PayPal' : 'Stripe'} has not marked this checkout as paid. Check again before treating it as confirmed.`;
      }
      if (details) {
        const amount = Number.isFinite(Number(data.amount_total)) && data.amount_total !== null
          ? new Intl.NumberFormat(undefined, { style: 'currency', currency: String(data.currency || 'CAD').toUpperCase() }).format(Number(data.amount_total) / 100)
          : 'Not available';
        const rows = [
          ['Payment', payment === 'paid' || payment === 'no_payment_required' ? 'Paid' : payment === 'unpaid' ? 'Not paid' : payment],
          ['Artwork reference', data.artwork_id || 'Not provided'],
          ['Total', amount],
          ['Checkout state', status],
        ];
        details.innerHTML = rows.map(([label, value]) => `<div class="checkout-order-row"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('');
        details.hidden = false;
      }
      if (reference) reference.textContent = `Checkout reference: ${data.session_id || sessionId}`;
      card.dataset.checkoutStatus = payment === 'paid' || payment === 'no_payment_required' ? 'paid' : status;
    } catch (error) {
      if (heading) heading.textContent = 'Unable to verify payment';
      if (message) message.textContent = error.message || `${provider === 'paypal' ? 'PayPal' : 'Stripe'} could not verify this checkout. Try checking again.`;
      if (reference) reference.textContent = `Checkout reference: ${sessionId}`;
    } finally {
      if (retry) { retry.disabled = false; retry.textContent = 'Check status again'; }
    }
  }

  function initCheckoutStatus() {
    $('#refreshCheckoutStatus')?.addEventListener('click', refreshCheckoutStatus);
    refreshCheckoutStatus();
  }

  function handleGlobalClicks() {
    document.addEventListener('click', (event) => {
      const closeModal = event.target.closest('[data-close-modal]');
      if (closeModal) { closeFeatureModal(closeModal.dataset.closeModal); return; }
      if (event.target.classList?.contains('modal-backdrop')) { event.target.hidden = true; return; }
      const compareButton = event.target.closest('[data-compare-id]');
      if (compareButton) { toggleComparison(compareButton.dataset.compareId); return; }
      const addCollection = event.target.closest('[data-add-to-collection]');
      if (addCollection) { openCollectionPicker(addCollection.dataset.addToCollection); return; }
      const collectionChoice = event.target.closest('[data-collection-choice]');
      if (collectionChoice) {
        const work = getArtworkById(state.pendingCollectionArtworkId);
        addArtworkToCollection(collectionChoice.dataset.collectionChoice, work).then(() => closeFeatureModal('collectionPickerModal')).catch((error) => { const choices = $('#collectionPickerChoices'); if (choices) choices.insertAdjacentHTML('beforeend', `<p class="alert-error">${escapeHtml(error.message)}</p>`); });
        return;
      }
      const removeFromCollection = event.target.closest('[data-collection-remove]');
      if (removeFromCollection) { removeArtworkFromCollection(removeFromCollection.dataset.collectionRemove, removeFromCollection.dataset.id); return; }
      const deleteCollectionButton = event.target.closest('[data-delete-collection]');
      if (deleteCollectionButton) { removeCollection(deleteCollectionButton.dataset.deleteCollection); return; }
      const watchArtwork = event.target.closest('[data-watch-artwork]');
      if (watchArtwork) { createArtworkAlert(watchArtwork.dataset.watchArtwork).then(() => { watchArtwork.textContent = 'Watching'; watchArtwork.disabled = true; }).catch((error) => window.alert(error.message)); return; }
      const followSeller = event.target.closest('[data-follow-seller]');
      if (followSeller) {
        const name = followSeller.dataset.followSeller;
        const known = (state.last?.results || []).filter((work) => (work.seller_name || work.source) === name);
        createSellerAlert(name, followSeller.dataset.market, followSeller.dataset.shopUrl, known).then(() => { followSeller.textContent = 'Following shop'; followSeller.disabled = true; }).catch((error) => window.alert(error.message));
        return;
      }
      const removeAlert = event.target.closest('[data-delete-alert]');
      if (removeAlert) { deleteAlert(removeAlert.dataset.deleteAlert); return; }
      if (event.target.closest('#openComparisonButton')) { openComparison(); return; }
      if (event.target.closest('#clearComparisonButton') || event.target.closest('#clearComparisonFromModal') || event.target.closest('#dashboardClearComparison')) {
        state.compare = []; persistAppState(); renderCompareDock(); renderComparisonPanel();
        if (state.last && window.ART_CONCIERGE_PAGE === 'results') renderResults(state.last);
        if (window.ART_CONCIERGE_PAGE === 'saved') renderSavedPage();
        closeFeatureModal('comparisonModal'); return;
      }
      const purchaseCheck = event.target.closest('[data-purchase-session]');
      if (purchaseCheck) { checkTrackedPurchase(purchaseCheck.dataset.purchaseSession, purchaseCheck, purchaseCheck.dataset.purchaseProvider || 'stripe'); return; }

      const paypalSandboxTest = event.target.closest('#paypalSandboxTestButton');
      if (paypalSandboxTest) { startPayPalSandboxTest(paypalSandboxTest); return; }

      const removeSelectedImage = event.target.closest('[data-remove-selected-image]');
      if (removeSelectedImage) {
        state.inspiration.images = state.inspiration.images.filter((image) => image.id !== removeSelectedImage.dataset.removeSelectedImage);
        renderInspirationUI();
        const status = $('#inspirationMessage');
        if (status) status.textContent = state.inspiration.images.length
          ? `${state.inspiration.images.length} image${state.inspiration.images.length === 1 ? '' : 's'} attached to this search only.`
          : 'No inspiration images attached. Add images for this search only.';
        return;
      }

      const save = event.target.closest('.save-action');
      if (save) { toggleSave(save.dataset.id); return; }

      const paypalBuy = event.target.closest('.paypal-buy-action');
      if (paypalBuy) { startPayPalCheckout(paypalBuy.dataset.id, paypalBuy); return; }

      const paypalInvoice = event.target.closest('.paypal-invoice-action');
      if (paypalInvoice) { requestPayPalInvoice(paypalInvoice.dataset.id, paypalInvoice); return; }

      const buy = event.target.closest('.buy-action');
      if (buy) { startCheckout(buy.dataset.id, buy); return; }

      const feedback = event.target.closest('.feedback-action');
      if (feedback) {
        const artwork = getArtworkById(feedback.dataset.id);
        const text = feedback.dataset.feedback || '';
        learnFromFeedback(text, artwork);
        if (/^Not for me/i.test(text) && artwork) {
          beginNotForMeSearch(artwork, feedback);
          return;
        }
        if (window.ART_CONCIERGE_PAGE === 'saved' && /^More like/i.test(text) && artwork) {
          beginSimilarSearch(artwork);
          return;
        }
        const input = $('#feedback');
        if (input) { input.value = text; input.focus(); }
        return;
      }

      const recent = event.target.closest('.recent-open');
      if (recent) {
        const item = state.recent.find((entry) => entry.id === recent.dataset.recentId);
        if (item?.request) {
          sessionStorage.setItem('artCoResumeRequest', JSON.stringify(item.request));
          window.location.href = `/concierge?intent=${encodeURIComponent(item.intent)}&resume=1`;
        } else if (item?.data) {
          saveLastResult(item.data);
          window.location.href = `/results?intent=${encodeURIComponent(item.intent)}`;
        }
        return;
      }

      const savedThumb = event.target.closest('.saved-thumb');
      if (savedThumb) {
        const work = state.saved.find((item) => item.id === savedThumb.dataset.savedId);
        if (work) {
          const data = { intent: 'find', summary: 'Saved from an earlier conversation with your concierge.', results: [work], not_for_sale: work.availability === 'available' ? [] : [work], purchase_available: work.availability === 'available' ? 1 : 0, total_found: 1, taste: { inputs: state.profile.loves } };
          saveLastResult(data);
          window.location.href = '/results?intent=find';
        }
      }

      const remove = event.target.closest('[data-remove-index]');
      if (remove) {
        const tastePanel = $('#tastePanel');
        if (tastePanel && !tastePanel.hidden) {
          if (!state.tasteDraft) state.tasteDraft = cloneProfile(state.profile);
          const list = remove.dataset.removeList === 'avoids' ? state.tasteDraft.avoids : state.tasteDraft.loves;
          list.splice(Number(remove.dataset.removeIndex), 1);
          renderTastePage();
          markTasteDraftChanged();
        } else {
          const list = remove.dataset.removeList === 'avoids' ? state.profile.avoids : state.profile.loves;
          list.splice(Number(remove.dataset.removeIndex), 1);
          saveProfile();
          renderTastePage();
        }
      }
    });
    $('#collectionPickerCreateForm')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const input = $('#collectionPickerName');
      try {
        const collection = await createCollection(input?.value || '');
        const artwork = getArtworkById(state.pendingCollectionArtworkId);
        if (artwork) await addArtworkToCollection(collection.id, artwork);
        form.reset();
        closeFeatureModal('collectionPickerModal');
      } catch (error) {
        const choices = $('#collectionPickerChoices');
        if (choices) choices.innerHTML = `<p class="alert-error">${escapeHtml(error.message)}</p>${choices.innerHTML}`;
      }
    });
  }

  function initHome() { renderHome(); }
  function initDashboard() {
    renderDashboard();
    initTasteEditor();
    installTasteNavigationGuard();
    $('#checkAlertsButton')?.addEventListener('click', (event) => checkArtworkAlerts(event.currentTarget));
  }

  async function initConcierge() {
    const params = new URLSearchParams(window.location.search);
    clearCurrentInspirationImages();
    setIntent(state.intent);
    const serviceSelect = $('#serviceSelect');
    if (serviceSelect) serviceSelect.value = state.intent;
    const marketScopeSelect = $('#marketScope');
    if (marketScopeSelect) {
      marketScopeSelect.value = ['CA', 'US', 'BOTH'].includes(state.preferences.regionScope) ? state.preferences.regionScope : 'CA';
      marketScopeSelect.addEventListener('change', () => {
        state.preferences.regionScope = ['CA', 'US', 'BOTH'].includes(marketScopeSelect.value) ? marketScopeSelect.value : 'CA';
        persistAppState();
      });
    }
    renderInspirationUI();
    $('#inspirationFiles')?.addEventListener('change', (event) => {
      $('#requestInput')?.setCustomValidity('');
      handleInspirationUpload(event.target.files);
      event.target.value = '';
    });

    const resume = params.get('resume');
    if (resume === '1') {
      try {
        const request = JSON.parse(sessionStorage.getItem('artCoResumeRequest') || 'null');
        if (request?.goal) {
          const input = $('#requestInput');
          if (input) input.value = request.goal;
        }
    if (request?.goal) {
          const history = $('#conversationHistory');
          if (history) {
            history.innerHTML = `<div class="conversation-entry"><p class="label">Previous request</p><p>${escapeHtml(request.goal)}</p></div><div class="conversation-entry"><p class="label">Continue from here</p><p>Refine the request, add a preference, or ask me to take it in a new direction.</p></div>`;
          }
        }
        sessionStorage.removeItem('artCoResumeRequest');
      } catch {}
    }

    serviceSelect?.addEventListener('change', (event) => {
      const intent = event.target.value || 'discover';
      const wasLearning = state.intent === 'learn';
      if (intent === 'learn' && state.inspiration.images.length) {
        clearCurrentInspirationImages();
        renderInspirationUI();
        const status = $('#inspirationMessage');
        if (status) status.textContent = 'Image uploads are not used for Learn about art. Enter the artwork title, artist, or topic in the text area instead.';
      } else if (wasLearning && intent !== 'learn') {
        const status = $('#inspirationMessage');
        if (status) status.textContent = '';
      }
      state.intent = intent;
      setIntent(intent);
      const url = new URL(window.location.href);
      url.searchParams.set('intent', intent);
      if (state.personalizeExplore && intent === 'discover') url.searchParams.set('personalize_taste', '1');
      else url.searchParams.delete('personalize_taste');
      window.history.replaceState({}, '', url.toString());
    });
    $('#personalizeExplore')?.addEventListener('change', (event) => {
      state.personalizeExplore = Boolean(event.target.checked);
      const url = new URL(window.location.href);
      if (state.personalizeExplore) url.searchParams.set('personalize_taste', '1');
      else url.searchParams.delete('personalize_taste');
      window.history.replaceState({}, '', url.toString());
    });

    const form = $('#requestForm');
    const input = $('#requestInput');
    // Keep submission under application control so a stale custom-validity
    // message can never block the submit handler before it rechecks live input.
    if (form) form.noValidate = true;
    form?.addEventListener('submit', (event) => {
      event.preventDefault();
      beginRequest($('#requestInput')?.value || '');
    });
    // Clear a prior native validation message as soon as the user starts
    // entering a valid description. Otherwise setCustomValidity can keep the
    // browser from firing the submit event even after text has been entered.
    input?.addEventListener('input', () => {
      if (input.value.trim()) input.setCustomValidity('');
    });
    input?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        beginRequest($('#requestInput')?.value || '');
      }
    });
  }
  function initResults() {
    const params = new URLSearchParams(window.location.search);
    $('#saveCuratedCollectionForm')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      try { await saveCuratedResultsAsCollection(); }
      catch (error) { const message = $('#saveCuratedCollectionMessage'); if (message) message.textContent = error.message || 'The collection could not be saved.'; }
    });
    const isNewRun = params.get('run') === '1';
    // A new request must never flash or briefly restore the previous search.
    if (isNewRun) {
      state.last = null;
      runPendingRequest();
    } else if (state.last) {
      renderResults(state.last);
    }
    $('#refineButton')?.addEventListener('click', () => {
      const feedback = $('#feedback')?.value.trim();
      if (!feedback || !state.last) return;
      const base = { ...(state.last.request || buildRequest(state.last.summary || '', null, state.last.intent || 'discover')), feedback };
      sessionStorage.setItem('artConciergePendingRequest', JSON.stringify(base));
      window.location.href = resultsRunUrl(base.intent);
    });
    $('#feedback')?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        $('#refineButton')?.click();
      }
    });
  }

  function initSaved() { renderSavedPage(); }

  function initTasteEditor() {
    state.tasteDraft = cloneProfile(state.profile);
    renderTastePage();
    $('#tasteInput')?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ',') {
        event.preventDefault();
        addDraftReference(event.target.value);
        event.target.value = '';
        renderTastePage();
        markTasteDraftChanged();
      }
    });
    $('#tasteInput')?.addEventListener('blur', (event) => {
      if (event.target.value.trim()) {
        addDraftReference(event.target.value);
        event.target.value = '';
        renderTastePage();
        markTasteDraftChanged();
      }
    });
    $('#avoidInput')?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ',') {
        event.preventDefault();
        addDraftAvoid(event.target.value);
        event.target.value = '';
        renderTastePage();
        markTasteDraftChanged();
      }
    });
    $('#avoidInput')?.addEventListener('blur', (event) => {
      if (event.target.value.trim()) {
        addDraftAvoid(event.target.value);
        event.target.value = '';
        renderTastePage();
        markTasteDraftChanged();
      }
    });
    $('#tasteNotes')?.addEventListener('input', (event) => {
      if (!state.tasteDraft) state.tasteDraft = cloneProfile(state.profile);
      state.tasteDraft.notes = event.target.value;
      markTasteDraftChanged();
    });
    $('#discoveryLevel')?.addEventListener('input', (event) => {
      if (!state.tasteDraft) state.tasteDraft = cloneProfile(state.profile);
      state.tasteDraft.discoveryLevel = Number(event.target.value);
      const label = $('#discoveryValue');
      if (label) label.textContent = `${state.tasteDraft.discoveryLevel}%`;
      markTasteDraftChanged();
    });
    $('#saveTaste')?.addEventListener('click', async () => {
      await saveTasteDraft();
      const button = $('#saveTaste');
      if (button) {
        button.textContent = 'Saved';
        window.setTimeout(() => { button.textContent = 'Save my taste'; }, 1400);
      }
    });
  }

  let pendingTasteNavigation = null;
  function isTastePanelActive() {
    const panel = $('#tastePanel');
    return Boolean(panel && !panel.hidden);
  }

  function hideTasteUnsavedModal() {
    const modal = $('#tasteUnsavedModal');
    if (modal) modal.hidden = true;
  }

  function showTasteUnsavedModal(action) {
    pendingTasteNavigation = action;
    const modal = $('#tasteUnsavedModal');
    if (!modal) return;
    modal.hidden = false;
    modal.querySelector('[role="dialog"]')?.focus();
    $('#saveTasteAndContinue')?.focus();
  }

  function requestTasteNavigation(action) {
    if (isTastePanelActive() && tasteDraftIsDirty()) {
      showTasteUnsavedModal(action);
      return;
    }
    action();
  }

  function installTasteNavigationGuard() {
    document.addEventListener('click', (event) => {
      const anchor = event.target.closest('a[href]');
      if (!anchor || anchor.target === '_blank' || !isTastePanelActive() || !tasteDraftIsDirty()) return;
      const href = anchor.getAttribute('href') || '';
      if (!href || href.startsWith('#') || href.toLowerCase().startsWith('javascript:')) return;
      event.preventDefault();
      event.stopPropagation();
      requestTasteNavigation(() => window.location.assign(anchor.href));
    }, true);
    window.addEventListener('beforeunload', (event) => {
      if (!isTastePanelActive() || !tasteDraftIsDirty()) return;
      event.preventDefault();
      event.returnValue = '';
    });
    $('#saveTasteAndContinue')?.addEventListener('click', async () => {
      const action = pendingTasteNavigation;
      await saveTasteDraft();
      pendingTasteNavigation = null;
      hideTasteUnsavedModal();
      action?.();
    });
    $('#discardTasteAndContinue')?.addEventListener('click', () => {
      const action = pendingTasteNavigation;
      pendingTasteNavigation = null;
      discardTasteDraft();
      hideTasteUnsavedModal();
      action?.();
    });
    $('#stayOnTaste')?.addEventListener('click', () => {
      pendingTasteNavigation = null;
      hideTasteUnsavedModal();
      $('#tasteTab')?.focus();
    });
  }


  function initTaste() {
    window.location.href = '/dashboard#taste';
  }


  window.addEventListener('pageshow', (event) => {
    if (!event.persisted) return;
    const params = new URLSearchParams(window.location.search);
    if (window.ART_CONCIERGE_PAGE !== 'results' || params.get('run') !== '1') return;
    // If navigation restored a cached Results document while a newer Concierge
    // request is waiting in session storage, rerun that request instead of
    // leaving the restored document's old result cards on screen.
    if (sessionStorage.getItem('artConciergePendingRequest')) {
      state.last = null;
      runPendingRequest();
    }
  });

  async function initializeVincent() {
    await hydrateAppState();
    await hydrateAccountState();
    applyThemeControls();
    setupGlobalControls();
    setActiveNav();
    renderCompareDock();
    handleGlobalClicks();

    switch (window.ART_CONCIERGE_PAGE) {
      case 'home': initHome(); break;
      case 'dashboard': initDashboard(); break;
      case 'concierge': await initConcierge(); break;
      case 'results': initResults(); break;
      case 'saved': initSaved(); break;
      case 'taste': initTaste(); break;
      case 'checkout': initCheckoutStatus(); break;
      case 'auth': initAuthPage(); break;
      default: break;
    }
  }
  initializeVincent();
})();
