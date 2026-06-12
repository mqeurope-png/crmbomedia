/* ───────────── PERSISTENCE LAYER (ported from v2) ───────────── */

const SUPABASE_URL = 'https://midvgxxndddasxlnstkg.supabase.co'
const SUPABASE_KEY = 'sb_publishable_uiXB5JmZfPETeyGn3Rsw_Q_D7SLaJNd'
const SUPABASE_TABLE = 'composer_data'
const SUPABASE_ROW_ID = 'main'
const STORAGE_KEY = 'bomedia_composer_data'
const DRAFT_KEY = 'bomedia_draft_blocks'

function supabaseFetch(method, body, signal) {
  let url = SUPABASE_URL + '/rest/v1/' + SUPABASE_TABLE + '?id=eq.' + SUPABASE_ROW_ID
  const opts = {
    method,
    headers: {
      'apikey': SUPABASE_KEY,
      'Authorization': 'Bearer ' + SUPABASE_KEY,
      'Content-Type': 'application/json',
      'Prefer': method === 'GET' ? '' : 'return=minimal',
    },
  }
  if (method === 'GET') {
    url = SUPABASE_URL + '/rest/v1/' + SUPABASE_TABLE + '?select=data&id=eq.' + SUPABASE_ROW_ID
    delete opts.headers['Prefer']
  }
  if (body) opts.body = JSON.stringify(body)
  if (signal) opts.signal = signal
  return fetch(url, opts)
}

/* Carga el estado completo desde la nube. Contrato de retorno:
   - Promise resuelve con OBJETO → la nube tiene datos válidos
   - Promise resuelve con null  → la nube está vacía de verdad
                                   (200 OK con row inexistente o data:{})
   - Promise rechaza con Error  → fallo real (red, auth, HTTP 4xx/5xx,
                                   JSON malformado). El caller DEBE
                                   distinguir esto de "vacía" porque tras
                                   un error NO se puede pushear local
                                   sin riesgo de pisar datos reales que
                                   sí estaban en la nube. Bug crítico
                                   anterior: el `.catch(() => null)`
                                   convertía errores en "vacía" → el
                                   auto-save sobrescribía el catálogo
                                   con los defaults. Apr 2026 fix. */
function loadFromSupabase() {
  return supabaseFetch('GET')
    .then(r => {
      if (!r.ok) {
        const e = new Error('Supabase respondió HTTP ' + r.status)
        e.status = r.status
        throw e
      }
      return r.json()
    })
    .then(rows => {
      if (rows && rows.length > 0 && rows[0].data && Object.keys(rows[0].data).length > 0) {
        return rows[0].data
      }
      return null
    })
}

/* Estado privado del módulo para resolver el race de guardados:
   - `_inFlightController`: AbortController del PATCH actualmente en vuelo
   - `_saveSeq`: número de secuencia incremental — solo el último gana
   El debouncer del App ya cancela timers pendientes, pero esto NO ayudaba
   con guardados ya despachados al servidor: si el PATCH A (datos viejos)
   tarda 3s y B (datos nuevos) sale en t=1.6s y termina antes que A, A
   pisa la nube con datos antiguos. Ahora cancelamos A en cuanto sale B. */
let _inFlightSaveController = null
let _saveSeq = 0

function saveToSupabase(data) {
  // Aborta cualquier guardado anterior en vuelo. El fetch cancelado
  // resolverá con error AbortError que silenciamos abajo.
  if (_inFlightSaveController) {
    try { _inFlightSaveController.abort() } catch (e) {}
  }
  const controller = (typeof AbortController === 'function') ? new AbortController() : null
  _inFlightSaveController = controller
  const mySeq = ++_saveSeq
  return supabaseFetch('PATCH', { data, updated_at: new Date().toISOString() }, controller && controller.signal)
    .then(() => {
      // Si entre tanto se ha lanzado otro save, este resultado es viejo;
      // descartamos su éxito (irrelevante — el último marca el estado).
      if (mySeq !== _saveSeq) return false
      if (_inFlightSaveController === controller) _inFlightSaveController = null
      return true
    })
    .catch(err => {
      // AbortError es esperado cuando se solapa otro save — no es un fallo real.
      if (err && (err.name === 'AbortError' || /aborted/i.test(err.message || ''))) return false
      if (_inFlightSaveController === controller) _inFlightSaveController = null
      return false
    })
}

function supabaseBackupFetch(method, rowId, body) {
  let url = SUPABASE_URL + '/rest/v1/' + SUPABASE_TABLE
  if (method === 'GET') {
    url += '?select=id,updated_at&id=like.backup_%&order=updated_at.desc&limit=10'
  } else if (method === 'POST') {
    // just POST to base URL
  } else {
    url += '?id=eq.' + rowId
  }
  const opts = {
    method,
    headers: {
      'apikey': SUPABASE_KEY,
      'Authorization': 'Bearer ' + SUPABASE_KEY,
      'Content-Type': 'application/json',
      'Prefer': method === 'POST' ? 'return=minimal' : (method === 'GET' ? '' : 'return=minimal'),
    },
  }
  if (method === 'GET') delete opts.headers['Prefer']
  if (body) opts.body = JSON.stringify(body)
  return fetch(url, opts)
}

function saveBackupToSupabase(data, reason) {
  const backupId = 'backup_' + Date.now()
  const body = { id: backupId, data, updated_at: new Date().toISOString() }
  supabaseBackupFetch('POST', null, body).then(r => {
    if (r.ok) console.log('Supabase backup saved:', backupId, '(' + reason + ')')
    pruneSupabaseBackups()
  }).catch(e => console.error('Supabase backup error:', e))
  return backupId
}

function pruneSupabaseBackups() {
  supabaseBackupFetch('GET').then(r => r.json()).then(rows => {
    if (rows && rows.length > 5) {
      const toDelete = rows.slice(5)
      toDelete.forEach(row => {
        const delUrl = SUPABASE_URL + '/rest/v1/' + SUPABASE_TABLE + '?id=eq.' + row.id
        fetch(delUrl, { method:'DELETE', headers:{'apikey':SUPABASE_KEY,'Authorization':'Bearer '+SUPABASE_KEY} })
      })
    }
  }).catch(() => {})
}

function listSupabaseBackups() {
  return supabaseBackupFetch('GET').then(r => r.json()).then(rows => {
    return (rows || []).map(row => ({ id: row.id, date: row.updated_at }))
  }).catch(() => [])
}

function loadSupabaseBackup(backupId) {
  const url = SUPABASE_URL + '/rest/v1/' + SUPABASE_TABLE + '?select=data&id=eq.' + backupId
  return fetch(url, {
    headers: {'apikey':SUPABASE_KEY,'Authorization':'Bearer '+SUPABASE_KEY},
  }).then(r => r.json()).then(rows => {
    if (rows && rows.length > 0 && rows[0].data) return rows[0].data
    return null
  }).catch(() => null)
}

function getStorageData() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return stored ? JSON.parse(stored) : null
  } catch {
    return null
  }
}

function saveStorageData(data) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
  } catch (e) {
    console.error('Error saving to localStorage:', e)
  }
}

function _draftKeyFor(userId) {
  return userId ? DRAFT_KEY + '_' + userId : DRAFT_KEY
}

function getDraftBlocks(userId) {
  try {
    // Try per-user key first; fall back to the legacy shared key for
    // existing sessions that pre-date per-user drafts.
    const keys = userId ? [_draftKeyFor(userId), DRAFT_KEY] : [DRAFT_KEY]
    for (const k of keys) {
      const saved = localStorage.getItem(k)
      if (saved) {
        const parsed = JSON.parse(saved)
        if (Array.isArray(parsed)) return parsed
      }
    }
  } catch {}
  return null
}

function saveDraftBlocks(blocks, userId) {
  try { localStorage.setItem(_draftKeyFor(userId), JSON.stringify(blocks)) } catch {}
}

/* Copy email HTML to clipboard as RICH content — that's what makes Gmail
   and Outlook paste the rendered email instead of the source code. We put
   both text/html (for rich paste) and text/plain (fallback for editors that
   only accept plain) on the clipboard.

   Returns a Promise<{ ok: boolean, mode: 'rich' | 'plain' | null, error?: string }>. */
function copyHtmlAsRich(html) {
  const safe = String(html || '')
  // Modern path — ClipboardItem with both MIME types. Requires secure
  // context (HTTPS or localhost) and a user gesture, which the click
  // handler already provides.
  if (navigator.clipboard && typeof navigator.clipboard.write === 'function' && typeof window.ClipboardItem === 'function') {
    return navigator.clipboard.write([
      new ClipboardItem({
        'text/html': new Blob([safe], { type: 'text/html' }),
        'text/plain': new Blob([safe], { type: 'text/plain' }),
      }),
    ]).then(() => ({ ok: true, mode: 'rich' }))
      .catch(err => {
        // Fall back to plain text if the rich write was rejected (e.g.
        // permission policy in some browsers)
        return navigator.clipboard.writeText(safe)
          .then(() => ({ ok: true, mode: 'plain' }))
          .catch(err2 => ({ ok: false, mode: null, error: err2.message || String(err2) }))
      })
  }
  // Legacy fallback
  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    return navigator.clipboard.writeText(safe)
      .then(() => ({ ok: true, mode: 'plain' }))
      .catch(err => ({ ok: false, mode: null, error: err.message || String(err) }))
  }
  return Promise.resolve({ ok: false, mode: null, error: 'Clipboard API not available' })
}

/* ───────────── IMAGE UPLOAD — boprint.net WordPress Media REST ─────────────
   The user opted to host uploaded images on their existing WordPress install
   at boprint.net via the WP REST media endpoint. Authentication is a
   WordPress Application Password (NOT the user's login password) — these are
   revocable from the WP profile page if compromised.

   ⚠️ The password lives in client-side JS, so anyone who opens devtools can
   read it. The user explicitly accepted this risk. Mitigations:
   • Use a dedicated WP user with the minimum role to upload media (Author).
   • Revoke the application password if the app or repo gets exposed.
   • Don't reuse this password for anything else.
*/
const BOPRINT_WP_URL = 'https://boprint.net'
const BOPRINT_WP_USER = 'bo-uploader'
const BOPRINT_WP_APP_PASSWORD = 'Ru16 DDlk kHNy ctoq EIoQ 6TAA' // app password "uploader"

function uploadImageToBoprint(file, opts) {
  opts = opts || {}
  if (!file) return Promise.reject(new Error('Sin archivo seleccionado'))
  if (!file.type || file.type.indexOf('image/') !== 0) {
    return Promise.reject(new Error('El archivo no es una imagen (' + (file.type || 'tipo desconocido') + ')'))
  }
  const maxSize = opts.maxSize || (10 * 1024 * 1024)
  if (file.size > maxSize) {
    return Promise.reject(new Error('Imagen demasiado grande: ' + Math.round(file.size / 1024) + ' KB (máx. ' + Math.round(maxSize / 1024 / 1024) + ' MB)'))
  }
  // Build a safe filename: prefix-timestamp-originalname (sanitized)
  const rawName = (file.name || 'image').replace(/[^a-zA-Z0-9._-]/g, '_').slice(-120)
  const safeName = (opts.prefix ? opts.prefix.replace(/[^a-zA-Z0-9_-]/g, '-') + '-' : '') + Date.now() + '-' + rawName
  // WP App Passwords accept the password with or without spaces; strip them
  // for a slightly more compact Authorization header.
  const auth = btoa(BOPRINT_WP_USER + ':' + BOPRINT_WP_APP_PASSWORD.replace(/\s+/g, ''))
  const url = BOPRINT_WP_URL + '/wp-json/wp/v2/media'
  return fetch(url, {
    method: 'POST',
    headers: {
      'Authorization': 'Basic ' + auth,
      'Content-Type': file.type,
      'Content-Disposition': 'attachment; filename="' + safeName.replace(/"/g, '') + '"',
    },
    body: file,
  }).then(async r => {
    if (!r.ok) {
      let detail = ''
      try { const j = await r.json(); detail = j.message || j.code || JSON.stringify(j) } catch (e) { detail = await r.text() }
      if (r.status === 401 || r.status === 403) {
        throw new Error('Credenciales rechazadas por boprint.net (' + r.status + '). Comprueba el usuario / app password en WP.')
      }
      if (r.status === 413) {
        throw new Error('Imagen demasiado grande para boprint.net (límite del servidor PHP). Reduce el tamaño o aumenta upload_max_filesize en el hosting.')
      }
      throw new Error('Subida fallida (' + r.status + '): ' + String(detail).slice(0, 200))
    }
    return r.json()
  }).then(data => {
    if (!data || !data.source_url) {
      throw new Error('WP devolvió respuesta sin source_url (¿permisos del usuario insuficientes para crear media?)')
    }
    return data.source_url
  }).catch(err => {
    if (err && /failed to fetch|networkerror/i.test(err.message || '')) {
      throw new Error('No se pudo conectar con boprint.net. Posible CORS — añade un plugin tipo "WP CORS" o whitelist tu dominio en el wp-config.php.')
    }
    throw err
  })
}

/* Generic dispatcher — keeps the rest of the app decoupled from the chosen
   provider. Today: WordPress on boprint.net. */
function uploadImage(file, opts) {
  return uploadImageToBoprint(file, opts)
}

/* Upload an image File to Supabase Storage and return a public URL.
   Requires a public bucket (default name: 'bomedia-images') created from the
   Supabase dashboard with public read access. Returns Promise<string>. */
const SUPABASE_IMAGES_BUCKET = 'bomedia-images'

function uploadImageToSupabase(file, opts) {
  opts = opts || {}
  if (!file) return Promise.reject(new Error('Sin archivo seleccionado'))
  if (!file.type || file.type.indexOf('image/') !== 0) {
    return Promise.reject(new Error('El archivo no es una imagen (' + (file.type || 'tipo desconocido') + ')'))
  }
  const maxSize = opts.maxSize || (5 * 1024 * 1024)
  if (file.size > maxSize) {
    return Promise.reject(new Error('Imagen demasiado grande: ' + Math.round(file.size / 1024) + ' KB (máx. ' + Math.round(maxSize / 1024 / 1024) + ' MB)'))
  }
  const bucket = opts.bucket || SUPABASE_IMAGES_BUCKET
  const rawExt = (file.name && file.name.split('.').pop()) || 'png'
  const ext = String(rawExt).toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 5) || 'png'
  const path = (opts.prefix || 'uploads') + '/' + Date.now() + '-' + Math.random().toString(36).slice(2, 8) + '.' + ext
  const url = SUPABASE_URL + '/storage/v1/object/' + encodeURIComponent(bucket) + '/' + path
  return fetch(url, {
    method: 'POST',
    headers: {
      'apikey': SUPABASE_KEY,
      'Authorization': 'Bearer ' + SUPABASE_KEY,
      'Content-Type': file.type,
      'x-upsert': 'true',
    },
    body: file,
  }).then(async r => {
    if (!r.ok) {
      let detail = ''
      try { const j = await r.json(); detail = j.message || j.error || JSON.stringify(j) } catch (e) { detail = await r.text() }
      if (r.status === 404 || /bucket.*not.*found|not.*exist/i.test(detail)) {
        throw new Error('No existe el bucket "' + bucket + '" en Supabase Storage. Créalo desde el panel de Supabase (Storage → New bucket → marcar Public).')
      }
      if (r.status === 401 || r.status === 403) {
        throw new Error('Sin permisos para subir al bucket "' + bucket + '". Comprueba que sea público y tenga políticas de upload abiertas (o requiera autenticación adecuada).')
      }
      throw new Error('Subida fallida (' + r.status + '): ' + String(detail).slice(0, 200))
    }
    return SUPABASE_URL + '/storage/v1/object/public/' + bucket + '/' + path
  })
}

Object.assign(window, {
  SUPABASE_URL, SUPABASE_KEY, SUPABASE_TABLE, SUPABASE_ROW_ID, STORAGE_KEY, DRAFT_KEY,
  SUPABASE_IMAGES_BUCKET,
  BOPRINT_WP_URL, BOPRINT_WP_USER,
  supabaseFetch, loadFromSupabase, saveToSupabase,
  supabaseBackupFetch, saveBackupToSupabase, pruneSupabaseBackups, listSupabaseBackups, loadSupabaseBackup,
  getStorageData, saveStorageData, getDraftBlocks, saveDraftBlocks,
  copyHtmlAsRich, uploadImage, uploadImageToBoprint, uploadImageToSupabase,
})
