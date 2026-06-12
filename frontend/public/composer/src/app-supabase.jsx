/* CRM-adapted persistence layer for the embedded Bomedia Composer.
 *
 * Drop-in replacement for the original `app-supabase.jsx`. Keeps the
 * exact same public API on `window` so the rest of the Composer
 * (app-main.jsx, app-compositor.jsx, app-backoffice.jsx, etc.) is
 * unchanged — only the bytes flowing over the wire move from Supabase
 * to the CRM's `/api/composer/*` endpoints.
 *
 * Auth: `window.__CRM_TOKEN__` and `window.__crmFetch(url, opts)` are
 * injected by the bootstrap script at the top of `index.html`. The
 * token is the same JWT the CRM uses everywhere else (Bearer header).
 *
 * Endpoints (see backend/app/composer/routers):
 *   GET  /api/composer/state          → monolithic state blob
 *   PUT  /api/composer/state          → persist mutations
 *   POST /api/composer/backups        → take snapshot
 *   GET  /api/composer/backups        → list recent snapshots
 *   GET  /api/composer/backups/{id}   → restore one snapshot
 *   DELETE /api/composer/backups?keep=N → FIFO trim
 *   POST /api/composer/assets         → multipart upload, returns
 *                                       { public_url, ... }
 */

const STATE_URL = '/api/composer/state';
const BACKUPS_URL = '/api/composer/backups';
const ASSETS_URL = '/api/composer/assets';

// localStorage slots — same keys as the original so v4 drafts already
// in the browser keep loading after the swap.
const STORAGE_KEY = 'bomedia-composer-state-v4';
const DRAFT_KEY = 'bomedia-composer-draft';

// ─── These constants stay exported on window so any client code that
//     reads them keeps compiling. Supabase / Boprint values are now
//     placeholders since the CRM owns persistence + uploads.
const SUPABASE_URL = '';
const SUPABASE_KEY = '';
const SUPABASE_TABLE = '';
const SUPABASE_ROW_ID = '';
const SUPABASE_IMAGES_BUCKET = '';
const BOPRINT_WP_URL = '';
const BOPRINT_WP_USER = '';

// ─── Internal helpers ────────────────────────────────────────────────

function _fetchJson(url, opts) {
  // Prefer the bootstrap-installed helper so the JWT is injected
  // automatically; fall back to raw fetch if the bootstrap hasn't
  // run (older standalone development copies).
  const driver = typeof window.__crmFetch === 'function' ? window.__crmFetch : fetch;
  return driver(url, opts || {}).then(function (r) {
    if (!r.ok) {
      var err = new Error('HTTP ' + r.status + ' ' + r.statusText + ' — ' + url);
      err.status = r.status;
      return r.text().then(function (body) { err.body = body; throw err; });
    }
    if (r.status === 204) return null;
    var ct = (r.headers.get('Content-Type') || '');
    if (ct.indexOf('application/json') >= 0) return r.json();
    return r.text();
  });
}

// ─── State (the monolithic blob the embed reads on boot + writes on
//     every change) ────────────────────────────────────────────────

function loadFromSupabase() {
  return _fetchJson(STATE_URL).then(function (data) {
    return data || null;
  }).catch(function (e) {
    console.error('[composer.persist] loadFromSupabase failed', e);
    return null;
  });
}

function saveToSupabase(data) {
  return _fetchJson(STATE_URL, {
    method: 'PUT',
    body: JSON.stringify(data || {}),
  }).then(function () { return true; }).catch(function (e) {
    console.error('[composer.persist] saveToSupabase failed', e);
    return false;
  });
}

// The original took a (method, body, signal) tuple. Kept for any caller
// that hand-rolls a request; routes through `_fetchJson`.
function supabaseFetch(method, body, signal) {
  return _fetchJson(STATE_URL, {
    method: method || 'GET',
    body: body ? JSON.stringify(body) : undefined,
    signal: signal || undefined,
  });
}

// ─── Backups (point-in-time snapshots, FIFO trimmed by `keep`) ──────

function supabaseBackupFetch(method, rowId, body) {
  var url = BACKUPS_URL + (rowId ? '/' + encodeURIComponent(rowId) : '');
  return _fetchJson(url, {
    method: method || 'GET',
    body: body ? JSON.stringify(body) : undefined,
  });
}

function saveBackupToSupabase(data, reason) {
  return _fetchJson(BACKUPS_URL, {
    method: 'POST',
    body: JSON.stringify({ data: data || {}, reason: reason || '' }),
  }).then(function () { return true; }).catch(function (e) {
    console.error('[composer.persist] saveBackupToSupabase failed', e);
    return false;
  });
}

function pruneSupabaseBackups(keep) {
  var k = typeof keep === 'number' && keep > 0 ? keep : 50;
  return _fetchJson(BACKUPS_URL + '?keep=' + k, { method: 'DELETE' })
    .then(function () { return true; })
    .catch(function (e) {
      console.error('[composer.persist] pruneSupabaseBackups failed', e);
      return false;
    });
}

function listSupabaseBackups() {
  return _fetchJson(BACKUPS_URL).then(function (rows) {
    return Array.isArray(rows) ? rows : [];
  }).catch(function (e) {
    console.error('[composer.persist] listSupabaseBackups failed', e);
    return [];
  });
}

function loadSupabaseBackup(backupId) {
  if (!backupId) return Promise.resolve(null);
  return _fetchJson(BACKUPS_URL + '/' + encodeURIComponent(backupId))
    .catch(function (e) {
      console.error('[composer.persist] loadSupabaseBackup failed', e);
      return null;
    });
}

// ─── localStorage helpers (untouched — same keys as v4) ─────────────

function getStorageData() {
  try {
    var raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    console.error('[composer.persist] getStorageData failed', e);
    return null;
  }
}

function saveStorageData(data) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(data || {}));
    return true;
  } catch (e) {
    console.error('[composer.persist] saveStorageData failed', e);
    return false;
  }
}

function _draftKeyFor(userId) {
  return DRAFT_KEY + (userId ? ':' + userId : '');
}

function getDraftBlocks(userId) {
  try {
    var raw = window.localStorage.getItem(_draftKeyFor(userId));
    if (!raw) return null;
    var parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : (parsed && parsed.blocks) || null;
  } catch (e) {
    console.error('[composer.persist] getDraftBlocks failed', e);
    return null;
  }
}

function saveDraftBlocks(blocks, userId) {
  try {
    window.localStorage.setItem(
      _draftKeyFor(userId),
      JSON.stringify(blocks || []),
    );
    return true;
  } catch (e) {
    console.error('[composer.persist] saveDraftBlocks failed', e);
    return false;
  }
}

// ─── Clipboard (verbatim from original — no network) ────────────────

function copyHtmlAsRich(html) {
  var safe = String(html || '');
  if (navigator.clipboard && typeof navigator.clipboard.write === 'function' && typeof window.ClipboardItem === 'function') {
    return navigator.clipboard.write([
      new ClipboardItem({
        'text/html': new Blob([safe], { type: 'text/html' }),
        'text/plain': new Blob([safe], { type: 'text/plain' }),
      }),
    ]).then(function () { return { ok: true, mode: 'rich' }; })
      .catch(function (err) {
        return navigator.clipboard.writeText(safe)
          .then(function () { return { ok: true, mode: 'plain' }; })
          .catch(function (err2) { return { ok: false, mode: null, error: err2.message || String(err2) }; });
      });
  }
  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    return navigator.clipboard.writeText(safe)
      .then(function () { return { ok: true, mode: 'plain' }; })
      .catch(function (err) { return { ok: false, mode: null, error: err.message || String(err) }; });
  }
  return Promise.resolve({ ok: false, mode: null, error: 'Clipboard API not available' });
}

// ─── Image uploads → CRM /api/composer/assets ────────────────────────

function uploadImageToBoprint(file, opts) {
  return uploadImageToCrm(file, opts);
}

function uploadImageToSupabase(file, opts) {
  return uploadImageToCrm(file, opts);
}

function uploadImage(file, opts) {
  return uploadImageToCrm(file, opts);
}

function uploadImageToCrm(file, opts) {
  opts = opts || {};
  if (!file) return Promise.reject(new Error('Sin archivo seleccionado'));
  if (!file.type || file.type.indexOf('image/') !== 0) {
    return Promise.reject(new Error('El archivo no es una imagen (' + (file.type || 'tipo desconocido') + ')'));
  }
  var maxSize = opts.maxSize || (10 * 1024 * 1024);
  if (file.size > maxSize) {
    return Promise.reject(new Error(
      'Imagen demasiado grande: ' + Math.round(file.size / 1024) + ' KB (máx. ' +
      Math.round(maxSize / 1024 / 1024) + ' MB)'
    ));
  }
  var form = new FormData();
  form.append('file', file);
  return _fetchJson(ASSETS_URL, { method: 'POST', body: form }).then(function (data) {
    if (!data || !data.public_url) {
      throw new Error('El backend no devolvió public_url para el asset subido.');
    }
    return data.public_url;
  });
}

// ─── Export ──────────────────────────────────────────────────────────

Object.assign(window, {
  SUPABASE_URL, SUPABASE_KEY, SUPABASE_TABLE, SUPABASE_ROW_ID, STORAGE_KEY, DRAFT_KEY,
  SUPABASE_IMAGES_BUCKET,
  BOPRINT_WP_URL, BOPRINT_WP_USER,
  supabaseFetch, loadFromSupabase, saveToSupabase,
  supabaseBackupFetch, saveBackupToSupabase, pruneSupabaseBackups, listSupabaseBackups, loadSupabaseBackup,
  getStorageData, saveStorageData, getDraftBlocks, saveDraftBlocks,
  copyHtmlAsRich, uploadImage, uploadImageToBoprint, uploadImageToSupabase,
});
