/* ───────────── SECURITY LAYER (ported from v2) ───────────── */

const DEFAULT_BO_HASH = 'a1bfe0bf4fa8f02f1969c64276b15f55e455b3dd9f50f11a22fb8c284a9c2f48'
const DEFAULT_DATA_HASH = '6fab52fdf6384db663c2aae68b921a907422f9bdd102fbd35a90fc71dc8423ea'

function sha256Hash(str) {
  const encoder = new TextEncoder()
  const data = encoder.encode(str)
  return crypto.subtle.digest('SHA-256', data).then(buf => {
    const arr = Array.from(new Uint8Array(buf))
    return arr.map(b => b.toString(16).padStart(2, '0')).join('')
  })
}

function sanitizeHtml(html) {
  if (!html) return ''
  // Scripts y handlers inline
  html = html.replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '')
  html = html.replace(/\s+on\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, '')
  html = html.replace(/(href|src|action)\s*=\s*(?:"javascript:[^"]*"|'javascript:[^']*')/gi, '$1=""')
  html = html.replace(/src\s*=\s*(?:"data:[^"]*"|'data:[^']*')/gi, 'src=""')
  // Embeds que pueden ejecutar
  html = html.replace(/<iframe\b[^<]*(?:(?!<\/iframe>)<[^<]*)*<\/iframe>/gi, '')
  html = html.replace(/<iframe\b[^>]*\/?>/gi, '')
  html = html.replace(/<(?:object|embed)\b[^>]*\/?>/gi, '')
  // <style> en rich HTML pegado por el user puede pisar el CSS del email
  // entero (ej: body{display:none}) o ejecutar @import a recursos externos.
  // El email-gen tiene su propio CSS_BLOCK a nivel de documento — los
  // bloques de texto NO necesitan <style>. Apr 2026.
  html = html.replace(/<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>/gi, '')
  html = html.replace(/<style\b[^>]*\/?>/gi, '')
  // <link rel=stylesheet> idem — carga CSS externo en el iframe del preview
  // y filtra info de caché. <base> permitiría redirigir todos los enlaces
  // relativos a un dominio hostil. <meta http-equiv=refresh> permite
  // redirección temporizada.
  html = html.replace(/<(?:link|base|meta)\b[^>]*\/?>/gi, '')
  return html
}

function sanitizeJsonObj(obj) {
  if (obj === null || typeof obj !== 'object') return obj
  if (Array.isArray(obj)) return obj.map(sanitizeJsonObj)
  const clean = {}
  const keys = Object.keys(obj)
  for (let i = 0; i < keys.length; i++) {
    const k = keys[i]
    if (k === '__proto__' || k === 'constructor' || k === 'prototype') continue
    clean[k] = sanitizeJsonObj(obj[k])
  }
  return clean
}

function checkPassword(input, storedHash, defaultHash) {
  return sha256Hash(input).then(inputHash => {
    const match = storedHash ? (inputHash === storedHash) : (inputHash === defaultHash)
    return { match, inputHash }
  })
}

function checkPasswordAsync(input, storedHash, defaultHash, callback) {
  checkPassword(input, storedHash, defaultHash).then(r => callback(r.match, r.inputHash))
}

Object.assign(window, {
  DEFAULT_BO_HASH, DEFAULT_DATA_HASH,
  sha256Hash, sanitizeHtml, sanitizeJsonObj,
  checkPassword, checkPasswordAsync,
})
