/* ───────────── EMAIL HTML GENERATION (ported from v2) ───────────── */
/* Table-based HTML for email client compatibility. */

function escapeHtml(str) {
  if (!str) return ''
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

function productCardHtml(p, lang) {
  // Sizes rebalanced 2026-04: image bumped to 220×170 (was 160×130) and
  // text sizes increased one step so they read at a comfortable 11-12px
  // body. Button padding reduced slightly so it doesn't dominate the card.
  // Apr 2026: escape every user-controlled string with escapeHtml so a
  // product name like "Tom & Jerry < Best" or a link with `"` doesn't
  // break the HTML/attributes. Color/CSS values stay raw — only edited
  // by admin from BO color pickers, not free-text.
  const areaLabel = lang==='fr'?'Surface':lang==='de'?'Fläche':lang==='en'?'Area':lang==='nl'?'Oppervlak':'Área'
  const altLabel = lang==='fr'?'Haut. max.':lang==='de'?'Max. Höhe':lang==='en'?'Max. height':lang==='nl'?'Max. hoogte':'Alt. máx.'
  const eName = escapeHtml(p.name)
  const eDesc = escapeHtml(p.desc)
  const eFeat1 = escapeHtml(p.feat1)
  const eFeat2 = escapeHtml(p.feat2)
  const eBadge = escapeHtml(p.badge)
  const eArea = escapeHtml(p.area)
  const eAlt = escapeHtml(p.alt)
  const ePrice = escapeHtml(p.price)
  const eImg = escapeHtml(p.img || '')
  const eLink = escapeHtml(p.link || '')
  let areaBlock = ''
  if (p.area !== '-') {
    areaBlock = '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0">' +
      '<tr><td width="38%" style="font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase">' + areaLabel + '</td><td style="font-size:11px;font-weight:700;color:#334155">' + eArea + '</td></tr>' +
      (p.alt !== '-' ? '<tr><td style="font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase">' + altLabel + '</td><td style="font-size:11px;font-weight:700;color:#334155">' + eAlt + '</td></tr>' : '') +
      '</table>'
  }
  const priceExtra = p.price !== 'Consultar' && p.price !== 'Sur demande' && p.price !== 'Auf Anfrage' && p.price !== 'On request' && p.price !== 'Op aanvraag' ? ' <span style="font-size:11px;font-weight:500;color:#475569">' + (lang==='fr'?'+ TVA':lang==='de'?'+ MwSt':lang==='en'?'+ VAT':lang==='nl'?'+ BTW':'+ IVA') + '</span>' : ''
  const ctaLabel = p.brand === 'pimpam' ? (lang==='fr'?'Détails':lang==='de'?'Details':lang==='en'?'Details':lang==='nl'?'Details':'Detalles') : (lang==='fr'?"Plus d'infos":lang==='de'?'Mehr Infos':lang==='en'?'More info':lang==='nl'?'Meer info':'Más info')
  const bgExtra = p.brand === 'pimpam' ? ';background:#fff7ed' : ''
  return '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1.5px solid ' + (p.brand==='pimpam'?'#fed7aa':'#e2e8f0') + ';border-radius:12px;overflow:hidden;background:#fff">' +
    '<tr><td style="background:#fff;padding:8px 4px 4px;border-bottom:1px solid ' + (p.brand==='pimpam'?'#ffedd5':'#f1f5f9') + ';text-align:center">' +
    // Image fills the entire column. No max-width caps so it grows with the
    // card; height stays proportional via height:auto.
    '<img src="' + eImg + '" alt="' + eName + '" style="display:block;width:100%;max-width:100%;height:auto;border-radius:8px">' +
    '</td></tr>' +
    '<tr><td style="padding:14px' + bgExtra + '">' +
    '<span style="display:inline-block;font-size:9px;font-weight:800;letter-spacing:1.5px;text-transform:uppercase;padding:3px 9px;border-radius:20px;margin-bottom:8px;background:' + p.badgeBg + ';color:' + p.badgeColor + '">' + eBadge + '</span>' +
    '<p style="font-size:15px;font-weight:900;color:#0f172a;margin:0;line-height:1.3">' + eName + '</p>' +
    '<p style="font-size:12px;color:#64748b;margin:5px 0 0;line-height:1.5">' + eDesc + '</p>' +
    areaBlock +
    '<p style="font-size:11px;color:#475569;padding:2px 0;margin:' + (p.area==='-'?'8px':'0') + ' 0 0">✓ ' + eFeat1 + '</p>' +
    '<p style="font-size:11px;color:#475569;padding:2px 0;margin:0">✓ ' + eFeat2 + '</p>' +
    '<p style="font-size:16px;font-weight:900;color:' + p.accent + ';margin:10px 0 0;text-align:center">' + ePrice + priceExtra + '</p>' +
    '<a href="' + eLink + '" style="display:block;text-align:center;font-size:12px;font-weight:700;text-decoration:none;padding:8px 10px;border-radius:8px;text-transform:uppercase;letter-spacing:0.4px;background:' + p.gradient + ';color:#fff;margin-top:8px">' + ctaLabel + ' →</a>' +
    '</td></tr></table>'
}

function productCardCompactHtml(p, lang) {
  // Compact (trio) — same rebalance applied at smaller scale: image 160×120
  // (was 120×90), text 9-13px (was 7-11), button slightly less heavy.
  // escapeHtml en todos los strings free-text (mismo motivo que la versión grande).
  const areaLabel = lang==='fr'?'Surface':lang==='de'?'Fläche':lang==='en'?'Area':lang==='nl'?'Oppervlak':'Área'
  const altLabel = lang==='fr'?'Haut.':lang==='de'?'Höhe':lang==='en'?'Height':lang==='nl'?'Hoogte':'Alt'
  const eName = escapeHtml(p.name)
  const eDesc = escapeHtml(p.desc)
  const eFeat1 = escapeHtml(p.feat1)
  const eFeat2 = escapeHtml(p.feat2)
  const eBadge = escapeHtml(p.badge)
  const eArea = escapeHtml(p.area)
  const eAlt = escapeHtml(p.alt)
  const ePrice = escapeHtml(p.price)
  const eImg = escapeHtml(p.img || '')
  const eLink = escapeHtml(p.link || '')
  let areaBlock = ''
  if (p.area !== '-') {
    areaBlock = '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:6px 0">' +
      '<tr><td style="font-size:9px;font-weight:700;color:#94a3b8;text-transform:uppercase">' + areaLabel + ': ' + eArea + '</td></tr>' +
      (p.alt !== '-' ? '<tr><td style="font-size:9px;font-weight:700;color:#94a3b8;text-transform:uppercase">' + altLabel + ': ' + eAlt + '</td></tr>' : '') +
      '</table>'
  }
  const priceExtra = p.price !== 'Consultar' && p.price !== 'Sur demande' && p.price !== 'Auf Anfrage' && p.price !== 'On request' && p.price !== 'Op aanvraag' ? ' <span style="font-size:9px;font-weight:500;color:#475569">(' + (lang==='fr'?'+ TVA':lang==='de'?'+ MwSt':lang==='en'?'+ VAT':lang==='nl'?'+ BTW':'+ IVA') + ')</span>' : ''
  const ctaLabel = p.brand === 'pimpam' ? (lang==='fr'?'Détails':lang==='de'?'Details':lang==='en'?'Details':lang==='nl'?'Details':'Detalles') : 'Info'
  const bgExtra = p.brand === 'pimpam' ? ';background:#fff7ed' : ''
  return '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid ' + (p.brand==='pimpam'?'#fed7aa':'#e2e8f0') + ';border-radius:10px;overflow:hidden;background:#fff">' +
    '<tr><td style="background:#fff;padding:6px 3px 3px;border-bottom:1px solid ' + (p.brand==='pimpam'?'#ffedd5':'#f1f5f9') + ';text-align:center">' +
    '<img src="' + eImg + '" alt="' + eName + '" style="display:block;width:100%;max-width:100%;height:auto;border-radius:6px">' +
    '</td></tr>' +
    '<tr><td style="padding:10px' + bgExtra + '">' +
    '<span style="display:inline-block;font-size:8px;font-weight:800;letter-spacing:1px;text-transform:uppercase;padding:2px 7px;border-radius:16px;margin-bottom:4px;background:' + p.badgeBg + ';color:' + p.badgeColor + '">' + eBadge + '</span>' +
    '<p style="font-size:13px;font-weight:900;color:#0f172a;margin:0;line-height:1.3">' + eName + '</p>' +
    '<p style="font-size:10px;color:#64748b;margin:3px 0 0;line-height:1.4">' + eDesc + '</p>' +
    areaBlock +
    '<p style="font-size:10px;color:#475569;padding:1px 0;margin:' + (p.area==='-'?'6px':'0') + ' 0 0">✓ ' + eFeat1 + '</p>' +
    '<p style="font-size:10px;color:#475569;padding:1px 0;margin:0">✓ ' + eFeat2 + '</p>' +
    '<p style="font-size:13px;font-weight:900;color:' + p.accent + ';margin:8px 0 0;text-align:center">' + ePrice + priceExtra + '</p>' +
    '<a href="' + eLink + '" style="display:block;text-align:center;font-size:10px;font-weight:700;text-decoration:none;padding:7px 8px;border-radius:6px;text-transform:uppercase;letter-spacing:0.3px;background:' + p.gradient + ';color:#fff;margin-top:6px">' + ctaLabel + ' →</a>' +
    '</td></tr></table>'
}

function brandStripHtml(key, lang, brands) {
  const b = brands.find(br => br.id === key)
  if (!b) return ''
  const url = (typeof b.url === 'object') ? (b.url[lang] || b.url.es) : b.url
  const urlLabel = (typeof b.urlLabel === 'object') ? (b.urlLabel[lang] || b.urlLabel.es) : b.urlLabel
  const h = parseInt(b.logoHeight) || 28
  const mw = parseInt(b.logoMaxWidth) || 180
  const imgTag = b.logo
    ? '<img src="' + b.logo + '" alt="' + b.label + '" style="max-height:' + h + 'px;max-width:' + mw + 'px;width:auto;height:auto;display:block">'
    : '<span style="font-size:14px;font-weight:800;color:' + b.color + '">' + b.label + '</span>'
  const linkTag = '<a href="' + url + '" style="font-size:12px;font-weight:700;color:' + b.color + ';text-decoration:none;white-space:nowrap">' + urlLabel + '</a>'
  if (b.logoBg) {
    return '<tr><td style="padding:16px 8px 8px"><table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:' + b.logoBg + ';border-radius:10px"><tr>' +
      '<td valign="middle" width="70%" style="padding:16px 24px">' + imgTag + '</td>' +
      '<td valign="middle" width="30%" align="right" style="padding:16px 24px">' + linkTag + '</td>' +
      '</tr></table></td></tr>'
  }
  return '<tr><td style="padding:16px 8px 8px"><table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>' +
    '<td valign="middle" width="70%" style="padding:4px 0">' + imgTag + '</td>' +
    '<td valign="middle" width="30%" align="right" style="padding:4px 0">' + linkTag + '</td>' +
    '</tr></table><div style="height:1px;background:' + b.divider + ';margin-top:8px"></div></td></tr>'
}

function textBlockHtml(text, opts) {
  const fs = (opts && opts.fontSize) || 14
  const align = (opts && opts.align) || 'left'
  const wrapOpen = '<tr><td style="padding:16px 8px;font-size:' + fs + 'px;color:#1e293b;line-height:1.65;text-align:' + align + '">\n'
  const wrapClose = '</td></tr>'
  if (text && /<[a-z][\s\S]*>/i.test(text)) {
    let richHtml = sanitizeHtml(text)
    richHtml = richHtml.replace(/<h1[^>]*>/gi, '<h1 style="font-size:22px;font-weight:800;color:#0f172a;margin:0 0 12px;font-family:system-ui,sans-serif">')
    richHtml = richHtml.replace(/<h2[^>]*>/gi, '<h2 style="font-size:18px;font-weight:700;color:#1e293b;margin:0 0 10px;font-family:system-ui,sans-serif">')
    richHtml = richHtml.replace(/<h3[^>]*>/gi, '<h3 style="font-size:15px;font-weight:700;color:#374151;margin:0 0 8px;font-family:system-ui,sans-serif">')
    richHtml = richHtml.replace(/<p[^>]*>/gi, '<p style="margin:0 0 14px">')
    richHtml = richHtml.replace(/<ul[^>]*>/gi, '<ul style="margin:0 0 14px;padding-left:20px">')
    richHtml = richHtml.replace(/<ol[^>]*>/gi, '<ol style="margin:0 0 14px;padding-left:20px">')
    richHtml = richHtml.replace(/<li[^>]*>/gi, '<li style="margin:0 0 4px">')
    richHtml = richHtml.replace(/<a /gi, '<a style="color:#2563eb;text-decoration:underline" ')
    return wrapOpen + richHtml + wrapClose
  }
  const lines = String(text || '').split('\n')
  let html = ''
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].trim()) html += '<p style="margin:0 0 14px">' + escapeHtml(lines[i]) + '</p>\n'
  }
  return wrapOpen + html + wrapClose
}

function productSingleHtml(p, lang) {
  return '<tr><td style="padding:8px 8px 16px"><table width="320" cellpadding="0" cellspacing="0" border="0" style="margin:0"><tr><td>' +
    productCardHtml(p, lang) + '</td></tr></table></td></tr>'
}

function productPairHtml(p1, p2, lang) {
  return '<tr><td style="padding:8px 8px 16px"><table width="100%" cellpadding="0" cellspacing="0" border="0"><tr class="prod-row">' +
    '<td class="col-half prod-cell" width="50%" valign="top" style="padding:0 5px 0 0">' + productCardHtml(p1, lang) + '</td>' +
    '<td class="col-half prod-cell" width="50%" valign="top" style="padding:0 0 0 5px">' + productCardHtml(p2, lang) + '</td>' +
    '</tr></table></td></tr>'
}

function productTrioHtml(p1, p2, p3, lang) {
  return '<tr><td style="padding:8px 8px 16px"><table width="100%" cellpadding="0" cellspacing="0" border="0"><tr class="prod-row">' +
    '<td class="col-third prod-cell" width="33%" valign="top" style="padding:0 4px 0 0">' + productCardCompactHtml(p1, lang) + '</td>' +
    '<td class="col-third prod-cell" width="33%" valign="top" style="padding:0 4px">' + productCardCompactHtml(p2, lang) + '</td>' +
    '<td class="col-third prod-cell" width="33%" valign="top" style="padding:0 0 0 4px">' + productCardCompactHtml(p3, lang) + '</td>' +
    '</tr></table></td></tr>'
}

function freebirdHtml(config, lang) {
  config = config || {}
  const youtubeUrl = config.youtubeUrl || 'https://www.youtube.com/watch?v=gp-x_jRBRcE'
  let thumbnailUrl = config.thumbnailOverride
  if (!thumbnailUrl && youtubeUrl) {
    const videoIdMatch = youtubeUrl.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/)([^&\n?#]+)/)
    if (videoIdMatch) thumbnailUrl = 'https://img.youtube.com/vi/' + videoIdMatch[1] + '/hqdefault.jpg'
  }
  if (!thumbnailUrl) thumbnailUrl = 'https://artisjet-printers.eu/wp-content/uploads/2025/02/3000-pro-freebirdok.png'
  const videoLabel = lang==='fr'?'Voir la vidéo':lang==='de'?'Video ansehen':lang==='en'?'Watch video':lang==='nl'?'Video bekijken':'Ver vídeo'
  return '<tr><td style="padding:8px 8px 16px"><table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-radius:12px;overflow:hidden;background:#0f172a">' +
    '<tr><td style="text-align:center;padding:0">' +
    '<a href="' + youtubeUrl + '" target="_blank" rel="noopener noreferrer" style="text-decoration:none">' +
    '<img src="' + thumbnailUrl + '" alt="Video" width="480" style="width:100%;max-width:480px;display:block;margin:0 auto;opacity:0.85"/>' +
    '</a></td></tr>' +
    '<tr><td style="text-align:center;padding:12px 16px;background:#0f172a">' +
    '<a href="' + youtubeUrl + '" target="_blank" rel="noopener noreferrer" style="color:#93c5fd;font-size:14px;font-weight:700;text-decoration:none;font-family:system-ui,sans-serif">▶ ' + videoLabel + '</a>' +
    '</td></tr></table></td></tr>'
}

function pimpamHeroHtml(config, lang) {
  const cfg = config || {}
  const hi = (cfg.i18n && lang && lang !== 'es' && cfg.i18n[lang]) ? cfg.i18n[lang] : null
  const imgUrl = cfg.heroImage || 'https://pimpam-vending.com/wp-content/uploads/2026/01/ChatGPT-Image-22-ene-2026-16_17_36.png'
  const title = (hi && hi.heroTitle) || cfg.heroTitle || 'Personaliza, imprime y vende… sin operario'
  const subtitle = (hi && hi.heroSubtitle) || cfg.heroSubtitle || 'Impresión UV-LED directa sobre fundas de móvil en autoservicio completo.'
  const bullets = (hi && hi.heroBullets) || cfg.heroBullets || ['Autoservicio 100% — sin personal','Pago con tarjeta, móvil o QR','Funda impresa en HD en 30 segundos','Compatible con +600 modelos de móvil']
  const imgLink = cfg.heroImageLink || ''
  const ctaText = (hi && hi.heroCtaText) || cfg.heroCtaText || ''
  const ctaUrl = (hi && hi.heroCtaUrl) || cfg.heroCtaUrl || ''
  const bgColor = cfg.heroBgColor || '#fff'

  // Detectar fondo oscuro para invertir colores de texto. Antes solo
  // soportaba #rrggbb estricto — falló para #fff (3 chars), rgb()/rgba(),
  // y nombres CSS como "black"/"transparent". Ahora cubrimos las cuatro
  // formas comunes; cualquier valor irreconocible cae en isDark=false
  // (asume fondo claro, texto oscuro — comportamiento anterior).
  let isDark = false
  const _luminance = (r, g, b) => (r*0.299 + g*0.587 + b*0.114)
  const bgRaw = String(bgColor || '').trim()
  // hex 6 chars: #rrggbb
  let m = bgRaw.match(/^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i)
  if (m) {
    isDark = _luminance(parseInt(m[1],16), parseInt(m[2],16), parseInt(m[3],16)) < 128
  } else if ((m = bgRaw.match(/^#?([0-9a-f])([0-9a-f])([0-9a-f])$/i))) {
    // hex 3 chars: #rgb → cada char duplicado
    isDark = _luminance(parseInt(m[1]+m[1],16), parseInt(m[2]+m[2],16), parseInt(m[3]+m[3],16)) < 128
  } else if ((m = bgRaw.match(/^rgba?\s*\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)/i))) {
    // rgb()/rgba() — ignoramos el alpha; si es totalmente transparente el
    // fondo del email lo determina el wrapper (blanco), así que isDark=false
    // es correcto. Si quieren un alpha bajo sobre oscuro tendrán que usar hex.
    isDark = _luminance(parseFloat(m[1]), parseFloat(m[2]), parseFloat(m[3])) < 128
  } else {
    // Nombres CSS comunes — solo los suficientemente oscuros donde el
    // texto blanco tiene sentido. Cualquier otro nombre cae en false.
    const darkNames = ['black','navy','maroon','darkblue','darkred','darkgreen','darkslategray','midnightblue','indigo','purple','brown']
    if (darkNames.includes(bgRaw.toLowerCase())) isDark = true
  }
  const titleColor = isDark ? '#ffffff' : '#0f172a'
  const subColor = isDark ? '#94a3b8' : '#64748b'
  const bulletColor = isDark ? '#cbd5e1' : '#475569'
  const ctaBg = isDark ? (cfg.heroCtaColor || '#00d4ff') : '#ea580c'
  const ctaTextColor = isDark ? '#0f172a' : '#fff'

  // Email-safe image: a plain <img> with width:100% / height:auto. Gmail and
  // Outlook ignore position:absolute and padding-bottom percentage tricks,
  // which made the previous square-wrapper version render the body cell
  // much taller than the image. Letting the image keep its natural aspect
  // ratio while the row aligns middle keeps both columns visually balanced.
  let imgInner = '<img src="' + imgUrl + '" alt="Hero" width="270" style="display:block;width:100%;max-width:100%;height:auto;border-radius:10px 0 0 10px">'
  if (imgLink) {
    imgInner = '<a href="' + imgLink + '" target="_blank" rel="noopener noreferrer" style="text-decoration:none;display:block">' + imgInner + '</a>'
  }
  const imgHtml = imgInner

  let bulletsHtml = ''
  for (let i = 0; i < bullets.length; i++) {
    bulletsHtml += '<p style="font-size:12px;color:' + bulletColor + ';margin:0 0 4px;line-height:1.5">✓ ' + bullets[i] + '</p>'
  }

  let ctaHtml = ''
  let ctaButtons = cfg.heroCtaButtons || []
  if (ctaButtons.length === 0 && ctaText && ctaUrl) ctaButtons = [{ text: ctaText, url: ctaUrl }]
  if (hi && hi.heroCtaButtons && hi.heroCtaButtons.length > 0) ctaButtons = hi.heroCtaButtons
  if (ctaButtons.length > 0) {
    let btnCells = ''
    for (let bi = 0; bi < ctaButtons.length; bi++) {
      const btn = ctaButtons[bi]
      if (btn.text && btn.url) {
        const btnBg = btn.bg || ctaBg
        const btnTxtC = btn.color || ctaTextColor
        if (bi > 0) btnCells += '<td style="width:8px"></td>'
        btnCells += '<td style="background:' + btnBg + ';border-radius:6px;padding:9px 20px"><a href="' + btn.url + '" target="_blank" rel="noopener noreferrer" style="color:' + btnTxtC + ';font-size:13px;font-weight:700;text-decoration:none;font-family:system-ui,sans-serif;white-space:nowrap">' + btn.text + '</a></td>'
      }
    }
    if (btnCells) ctaHtml = '<table cellpadding="0" cellspacing="0" border="0" style="margin-top:10px"><tr>' + btnCells + '</tr></table>'
  }

  return '<tr><td style="padding:12px 8px 16px"><table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:' + bgColor + ';border-radius:10px;overflow:hidden"><tr>' +
    '<td class="pp-img-cell" width="45%" valign="middle" style="padding:0;font-size:0;line-height:0">' + imgHtml + '</td>' +
    '<td class="pp-body-cell" valign="middle" style="padding:18px 20px 18px 18px">' +
    '<p style="font-size:17px;font-weight:900;color:' + titleColor + ';margin:0 0 10px;line-height:1.3">' + title + '</p>' +
    '<p style="font-size:13px;color:' + subColor + ';margin:0 0 12px;line-height:1.5">' + subtitle + '</p>' +
    bulletsHtml + ctaHtml +
    '</td></tr></table></td></tr>'
}

/* Single-image block. Renders an <img> centered (or aligned) with optional
   <a href> wrapper. Designed to look right inside a 280-300px column or at
   full 600px width. */
function imageBlockHtml(b) {
  const src = (b && b.src) || ''
  if (!src) return ''
  const align = b.align || 'center'
  const widthPct = (typeof b.widthPct === 'number' && b.widthPct > 0 && b.widthPct <= 100) ? b.widthPct : 100
  const alt = escapeHtml(b.alt || '')
  let img = '<img src="' + escapeHtml(src) + '" alt="' + alt + '" style="width:' + widthPct + '%;max-width:100%;height:auto;border-radius:6px;display:block;margin:0 auto" />'
  if (b.link) img = '<a href="' + escapeHtml(b.link) + '" target="_blank" style="text-decoration:none;display:block">' + img + '</a>'
  return '<tr><td align="' + align + '" style="padding:8px 20px">' + img + '</td></tr>'
}

/* Call-to-action card. Optional title / subtitle / bullets above a styled
   button. Uses <table> for Outlook compatibility. The whole card sits on
   an optional background "panel" (panelBg / panelBorder) so it can stand
   out from surrounding content. */
function ctaBlockHtml(b) {
  b = b || {}
  const text = escapeHtml(b.text || 'Más información')
  const rawUrl = (b.url || '').trim()
  const hasUrl = rawUrl && rawUrl !== '#'
  const bg = b.bg || '#1d4ed8'
  const color = b.color || '#ffffff'
  const align = b.align || 'center'
  const title = b.title || ''
  const subtitle = b.subtitle || ''
  const bullets = Array.isArray(b.bullets) ? b.bullets.filter(x => x && String(x).trim()) : []
  const panelBg = b.panelBg && b.panelBg !== 'transparent' ? b.panelBg : ''
  const panelBorder = b.panelBorder && b.panelBorder !== 'transparent' ? b.panelBorder : ''
  let inner = ''
  if (title) inner += '<h3 style="margin:0 0 6px;font-size:16px;font-weight:700;color:#1a1918;line-height:1.3">' + escapeHtml(title) + '</h3>'
  if (subtitle) inner += '<p style="margin:0 0 10px;font-size:13px;color:#475569;line-height:1.5">' + escapeHtml(subtitle) + '</p>'
  if (bullets.length) {
    inner += '<ul style="margin:0 0 14px;padding:0 0 0 18px;font-size:13px;color:#334155;line-height:1.55">'
    bullets.forEach(bp => { inner += '<li style="margin:0 0 4px">' + escapeHtml(bp) + '</li>' })
    inner += '</ul>'
  }
  // When no URL is set, render the button as a styled <span> instead of an
  // <a>. Visually identical, but not clickable — and email clients like
  // Gmail will let the user paste a real URL afterwards while keeping the
  // styling. Avoids the misleading href="#" that goes nowhere.
  const buttonSharedStyle = 'display:inline-block;padding:10px 22px;font-size:13px;font-weight:600;color:' + color + ';text-decoration:none;font-family:Helvetica,Arial,sans-serif'
  const button = hasUrl
    ? ('<a href="' + escapeHtml(rawUrl) + '" target="_blank" style="' + buttonSharedStyle + '">' + text + '</a>')
    : ('<!-- TODO: añadir URL al CTA antes de enviar --><span style="' + buttonSharedStyle + ';cursor:default">' + text + '</span>')
  inner +=
    '<table cellpadding="0" cellspacing="0" border="0" style="display:inline-table;margin:0 auto"><tr>' +
    '<td style="background:' + bg + ';border-radius:6px;padding:0">' +
      button +
    '</td></tr></table>'
  const panelStyle = (panelBg ? 'background:' + panelBg + ';' : '') + (panelBorder ? 'border:1px solid ' + panelBorder + ';' : '') + 'border-radius:8px;padding:' + (panelBg || panelBorder ? '16px 18px' : '0')
  if (panelBg || panelBorder) {
    return '<tr><td style="padding:8px 20px"><div style="' + panelStyle + ';text-align:' + align + '">' + inner + '</div></td></tr>'
  }
  return '<tr><td align="' + align + '" style="padding:8px 20px">' + inner + '</td></tr>'
}

/* Divisor visual entre bloques. 3 variantes:
   - line: línea horizontal fina full width
   - short: línea corta centrada (~80px), más elegante
   - dots: tres puntos centrados, separador ornamental
   Color y paddingV (vertical space) configurables. */
function dividerBlockHtml(b) {
  b = b || {}
  const style = b.style || 'line'
  const color = b.color || '#e2e8f0'
  const padV = (typeof b.paddingV === 'number') ? Math.max(8, Math.min(80, b.paddingV)) : 24
  if (style === 'dots') {
    return '<tr><td align="center" style="padding:' + padV + 'px 20px;font-family:Helvetica,Arial,sans-serif;font-size:18px;letter-spacing:8px;color:' + color + ';line-height:1">·&nbsp;·&nbsp;·</td></tr>'
  }
  if (style === 'short') {
    return '<tr><td align="center" style="padding:' + padV + 'px 20px"><div style="display:inline-block;width:80px;height:2px;background:' + color + ';border-radius:1px;line-height:0;font-size:0">&nbsp;</div></td></tr>'
  }
  // default: line
  return '<tr><td style="padding:' + padV + 'px 20px"><div style="height:1px;background:' + color + ';line-height:0;font-size:0">&nbsp;</div></td></tr>'
}

function pimpamStepsHtml(config, lang) {
  const cfg = config || {}
  const steps = cfg.steps || [
    {n:"1️⃣",t:"Elige diseño",s:"Pantalla táctil"},
    {n:"2️⃣",t:"Personaliza",s:"Texto, colores…"},
    {n:"3️⃣",t:"Paga",s:"Tarjeta / QR"},
    {n:"4️⃣",t:"¡Listo!",s:"Funda en 30s"},
  ]
  const bgColor = cfg.stepsBgColor || '#fff7ed'
  const borderColor = cfg.stepsBorderColor || '#fed7aa'
  let cells = ''
  for (let i = 0; i < steps.length; i++) {
    const pad = i===0?'0 4px 0 0':i===steps.length-1?'0 0 0 4px':'0 4px'
    cells += '<td class="step-cell" width="' + (100/steps.length) + '%" valign="top" style="padding:'+pad+'">' +
      '<div style="background:' + bgColor + ';border:1px solid ' + borderColor + ';border-radius:8px;padding:10px;text-align:center">' +
      '<div style="font-size:22px;margin-bottom:4px">'+steps[i].n+'</div>' +
      '<p style="font-size:10px;font-weight:800;color:#0f172a;margin:0 0 2px">'+steps[i].t+'</p>' +
      '<p style="font-size:9px;color:#64748b;margin:0">'+steps[i].s+'</p></div></td>'
  }
  return '<tr><td style="padding:0 8px 16px"><table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'+cells+'</tr></table></td></tr>'
}

const CSS_BLOCK = '<style>' +
  'body,table,td,p,a,h1,h2,h3,h4{margin:0;padding:0;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%}' +
  'table{border-collapse:collapse;mso-table-lspace:0;mso-table-rspace:0}' +
  'img{border:0;display:block;line-height:100%;outline:none;text-decoration:none;-ms-interpolation-mode:bicubic;max-width:100%}' +
  'body{font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;background:#ffffff;color:#1e293b}' +
  '@media only screen and (max-width:600px){' +
  '.wrap{width:100%!important}.col-half,.col-third{width:100%!important;display:block!important;padding:0 0 12px 0!important}' +
  '.prod-row{display:block!important;width:100%!important}.prod-cell{display:block!important;width:100%!important;padding:0 0 16px 0!important}' +
  '.prod-cell table{width:100%!important}.pp-img-cell{display:block!important;width:100%!important}' +
  '.pp-body-cell{display:block!important;width:100%!important;padding:18px 16px!important}' +
  '.step-cell{width:50%!important;display:inline-block!important;padding:0 4px 12px!important}' +
  '}' +
  '</style>'

function generateFullHtml(blocks, products, lang, brands, appState) {
  lang = lang || 'es'
  appState = appState || {}
  let rows = ''

  function resolveText(block) {
    // Per-lang rich HTML wins (newer schema). Fall back to legacy single
    // _richHtml only for ES so other languages don't get the wrong content.
    if (block._richHtmlByLang && block._richHtmlByLang[lang]) return block._richHtmlByLang[lang]
    if (block._richHtml != null && lang === 'es') return block._richHtml
    if (block._sourceType) return getTextInLanguage(block, lang, appState)
    if (block.i18n) return getLocalizedText(block, 'text', lang)
    return block.text || ''
  }

  function resolveHero(block) {
    if (block._sourceType) return getHeroDataInLanguage(block, lang, appState)
    return block
  }

  /* Wrap un grupo de <tr> en una tabla más estrecha (30-100%) alineada
     según `align` (left/center/right). Outlook-friendly: usa solo tables.
     Si widthPct >= 100 y align es center/null, devuelve tal cual. */
  function _wrapWithWidth(rowsHtml, widthPct, align) {
    const w = (typeof widthPct === 'number') ? Math.max(30, Math.min(100, widthPct)) : 100
    const a = (align === 'left' || align === 'right') ? align : 'center'
    if (w >= 100 && a === 'center') return rowsHtml
    return '<tr><td style="padding:0">' +
      '<table width="100%" cellpadding="0" cellspacing="0" border="0">' +
      '<tr><td align="' + a + '" style="padding:0">' +
      '<table width="' + w + '%" style="width:' + w + '%" cellpadding="0" cellspacing="0" border="0" align="' + a + '">' +
      '<tbody>' + rowsHtml + '</tbody>' +
      '</table>' +
      '</td></tr></table>' +
      '</td></tr>'
  }

  // Render one block to its <tr>...</tr> rows. Extracted so section blocks
  // can recurse into their column-children without duplicating the dispatch.
  function renderBlock(b) {
    let out = ''
    switch (b.type) {
      case 'section': {
        const cols = Array.isArray(b.columns) ? b.columns : []
        const colCount = cols.length || 2
        const colW = Math.floor(600 / colCount)
        out += '<tr><td style="padding:0">'
        out += '<table class="section-row" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto"><tr>'
        cols.forEach((col, ci) => {
          out += '<td class="col-' + (colCount === 2 ? 'half' : 'third') + '" valign="top" align="left" width="' + colW + '" style="vertical-align:top;width:' + colW + 'px;padding:0 6px">'
          out += '<table width="100%" cellpadding="0" cellspacing="0" border="0">'
          ;(col.blocks || []).forEach(ib => { out += renderBlock(ib) })
          out += '</table>'
          out += '</td>'
        })
        out += '</tr></table></td></tr>'
        return out
      }
      default: break
    }
    // Fallthrough: original switch
    switch (b.type) {
      case 'text': {
        const resolvedText = resolveText(b)
        if (resolvedText) out += textBlockHtml(resolvedText, { fontSize: b.fontSize, align: b.align })
        break
      }
      case 'brand_artisjet': out += brandStripHtml(b.brand||'artisjet', lang, brands); break
      case 'brand_mbo': out += brandStripHtml(b.brand||'mbo', lang, brands); break
      case 'brand_pimpam': out += brandStripHtml(b.brand||'pimpam', lang, brands); break
      case 'brand_smartjet': out += brandStripHtml(b.brand||'smartjet', lang, brands); break
      case 'brand_flux': out += brandStripHtml(b.brand||'flux', lang, brands); break
      case 'brand_strip': out += brandStripHtml(b.brand||'artisjet', lang, brands); break
      case 'product_single': {
        let ps = products.find(p => p.id === b.product1)
        if (ps) { ps = getLocalizedProduct(ps, lang); out += productSingleHtml(ps, lang) }
        break
      }
      case 'product_pair': {
        let p1 = products.find(p => p.id === b.product1)
        let p2 = products.find(p => p.id === b.product2)
        if (p1 && p2) { p1 = getLocalizedProduct(p1, lang); p2 = getLocalizedProduct(p2, lang); out += productPairHtml(p1, p2, lang) }
        break
      }
      case 'product_trio': {
        let pt1 = products.find(p => p.id === b.product1)
        let pt2 = products.find(p => p.id === b.product2)
        let pt3 = products.find(p => p.id === b.product3)
        if (pt1 && pt2 && pt3) { pt1 = getLocalizedProduct(pt1, lang); pt2 = getLocalizedProduct(pt2, lang); pt3 = getLocalizedProduct(pt3, lang); out += productTrioHtml(pt1, pt2, pt3, lang) }
        break
      }
      case 'freebird':
      case 'video': out += freebirdHtml(b.config || b, lang); break
      case 'image': out += imageBlockHtml(b); break
      case 'cta': out += ctaBlockHtml(b); break
      case 'divider': out += dividerBlockHtml(b); break
      case 'hero':
      case 'product_hero':
      case 'pimpam_hero': {
        const heroData = resolveHero(b)
        if (heroData === b) out += pimpamHeroHtml(b, lang)
        else out += pimpamHeroHtml(heroData, null)
        break
      }
      case 'pimpam_steps': out += pimpamStepsHtml(b.config || b, lang); break
      case 'composed': {
        const ibs = b.innerBlocks || []
        if (ibs.length === 0) {
          if (b.introText) ibs.push({type:'text', text:b.introText})
          if (b.brandStrip && b.brandStrip !== 'none') ibs.push({type:'brand_strip', brand:b.brandStrip})
          if (b.includeHero) ibs.push({type:'pimpam_hero'})
          const cProds = b.products || []
          if (b.blockType === 'product_trio' && cProds.length >= 3) ibs.push({type:'product_trio', product1:cProds[0], product2:cProds[1], product3:cProds[2]})
          else if (b.blockType === 'product_pair' && cProds.length >= 2) ibs.push({type:'product_pair', product1:cProds[0], product2:cProds[1]})
          else if (b.blockType === 'product_single' && cProds.length >= 1) ibs.push({type:'product_single', product1:cProds[0]})
          if (b.includeSteps) ibs.push({type:'pimpam_steps'})
        }
        ibs.forEach(ib => {
          if (ib.type === 'text') {
            const ibText = resolveText(ib)
            if (ibText) out += textBlockHtml(ibText)
          } else if (ib.type === 'brand_strip' && ib.brand) {
            out += brandStripHtml(ib.brand, lang, brands)
          } else if (ib.type === 'pimpam_hero') {
            const ibHero = resolveHero(ib)
            if (ibHero === ib) out += pimpamHeroHtml(ib.heroImage ? ib : b, lang)
            else out += pimpamHeroHtml(ibHero, null)
          } else if (ib.type === 'pimpam_steps') {
            out += pimpamStepsHtml(ib.steps ? ib : b, lang)
          } else if (ib.type === 'separator') {
            out += '<tr><td style="padding:8px 20px"><hr style="border:none;border-top:1px solid #e5e7eb;margin:0"></td></tr>'
          } else if (ib.type === 'product_trio') {
            let ct1 = products.find(p => p.id === ib.product1)
            let ct2 = products.find(p => p.id === ib.product2)
            let ct3 = products.find(p => p.id === ib.product3)
            if (ct1 && ct2 && ct3) { ct1 = getLocalizedProduct(ct1, lang); ct2 = getLocalizedProduct(ct2, lang); ct3 = getLocalizedProduct(ct3, lang); out += productTrioHtml(ct1, ct2, ct3, lang) }
          } else if (ib.type === 'product_pair') {
            let cp1 = products.find(p => p.id === ib.product1)
            let cp2 = products.find(p => p.id === ib.product2)
            if (cp1 && cp2) { cp1 = getLocalizedProduct(cp1, lang); cp2 = getLocalizedProduct(cp2, lang); out += productPairHtml(cp1, cp2, lang) }
          } else if (ib.type === 'product_single') {
            let cps = products.find(p => p.id === ib.product1)
            if (cps) { cps = getLocalizedProduct(cps, lang); out += productSingleHtml(cps, lang) }
          }
        })
        break
      }
    }
    // Aplicar widthPct + blockAlign si el bloque los tiene. Las secciones
    // (multi-columna) ya gestionan su propio layout y se ignoran.
    if (b.type !== 'section') {
      const w = (typeof b.widthPct === 'number') ? b.widthPct : 100
      const a = b.blockAlign || 'center'
      if (w < 100 || a !== 'center') {
        out = _wrapWithWidth(out, w, a)
      }
    }
    return out
  }

  for (let i = 0; i < blocks.length; i++) {
    rows += renderBlock(blocks[i])
  }

  return '<html><head>'+CSS_BLOCK+'</head><body style="font-family:\'Helvetica Neue\',Helvetica,Arial,sans-serif;margin:0;padding:0;background:#ffffff;color:#1e293b">' +
    '<table class="wrap" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto">' + rows + '</table></body></html>'
}

/* ───────────── v3 → v2 BLOCK BRIDGE ─────────────
   Converts v3 block shapes into v2 block shapes that generateFullHtml expects.
   Supports both the v3-only simplified types (product/brandstrip/hero/header/footer)
   AND v2-native types coming straight from STANDALONE_BLOCKS (product_pair, pimpam_hero, etc.). */
function v3BlocksToV2Blocks(v3Blocks, appState) {
  const out = []
  const standalone = (appState && appState.standaloneBlocks) || []
  const composedList = (appState && appState.composedBlocks) || []
  const v3Brands = ((appState && appState.brands) || []).filter(b => b.id !== 'bomedia').map(b => b.id)

  function resolveStandaloneConfig(b) {
    if (b._sourceId) {
      const sb = standalone.find(s => s.id === b._sourceId)
      return (sb && sb.config) || {}
    }
    if (b.standaloneId) {
      const sb = standalone.find(s => s.id === b.standaloneId)
      return (sb && sb.config) || {}
    }
    return {}
  }

  for (const b of (v3Blocks || [])) {
    // Capturamos ancho/alineación del bloque v3 para propagarlos al
    // bloque v2 correspondiente — sin esto, el renderBlock no aplica
    // _wrapWithWidth porque b.widthPct queda undefined tras el bridge.
    const lengthBefore = out.length
    switch (b.type) {
      // ─── section (multi-column) — recursively transform inner blocks ──
      case 'section': {
        const cols = Array.isArray(b.columns) ? b.columns : []
        out.push({
          id: b.id,
          type: 'section',
          layout: b.layout,
          columns: cols.map(col => ({
            blocks: v3BlocksToV2Blocks(col.blocks || [], appState),
          })),
        })
        break
      }
      // ─── text ────────────────────────────────────────────────
      case 'text': {
        const src = b.textId
        // Construir el bag de overrides aceptando los tres formatos que
        // pueden llegar aquí: (1) `overridesByLang` (forma moderna v3),
        // (2) `overrideText` (legacy single-shot ES), (3) `text` + `i18n`
        // (forma de plantillas/compuestos guardados en Supabase con el
        // schema antiguo). Sin el caso 3, los textos viejos de plantillas
        // pasaban como undefined al renderer y se renderizaban en blanco
        // (visible en el preview del editor BO antes de tocar nada).
        // Bug Apr 2026.
        const buildOverrides = () => {
          if (b.overridesByLang) return b.overridesByLang
          if (b.overrideText) return { es: b.overrideText }
          if (b.text != null || b.i18n) {
            const o = { es: b.text || '' }
            if (b.i18n) {
              for (const [l, v] of Object.entries(b.i18n)) {
                if (v && v.text != null) o[l] = v.text
              }
            }
            return o
          }
          return undefined
        }
        const overrides = buildOverrides()
        const base = src
          ? { type:'text', _sourceType:'prewritten', _sourceId:src, _overrides:overrides }
          : { type:'text', _sourceType:'manual', _overrides:overrides }
        if (b._richHtml != null) base._richHtml = b._richHtml
        if (b._richHtmlByLang) base._richHtmlByLang = b._richHtmlByLang
        // Carry the typography fields through so the renderer can apply them
        if (b.fontSize) base.fontSize = b.fontSize
        if (b.align) base.align = b.align
        out.push(base)
        break
      }

      // ─── products ────────────────────────────────────────────
      case 'product': {
        out.push({ type:'product_single', product1:b.productId })
        break
      }
      case 'product_single': {
        out.push({ type:'product_single', product1: b.product1 })
        break
      }
      case 'product_pair': {
        out.push({ type:'product_pair', product1: b.product1, product2: b.product2 })
        break
      }
      case 'product_trio': {
        out.push({ type:'product_trio', product1: b.product1, product2: b.product2, product3: b.product3 })
        break
      }

      // ─── brand strips ────────────────────────────────────────
      case 'brandstrip': {
        // v3-only: a multi-brand strip. Emit one brand_strip per enabled brand.
        const enabled = b.brands || v3Brands
        for (const brandId of enabled) out.push({ type:'brand_strip', brand:brandId })
        break
      }
      case 'brand_strip': {
        out.push({ type:'brand_strip', brand: b.brand })
        break
      }
      case 'brand_artisjet':
      case 'brand_mbo':
      case 'brand_pimpam':
      case 'brand_smartjet':
      case 'brand_flux': {
        out.push({ type: b.type, brand: b.brand || b.type.replace('brand_', '') })
        break
      }

      // ─── pimpam hero ─────────────────────────────────────────
      case 'hero': {
        // v3-only hero type → convert to pimpam_hero
        const heroSbConf = resolveStandaloneConfig(b)
        out.push({
          type: 'pimpam_hero',
          _sourceType: b._sourceType, _sourceId: b._sourceId, _overrides: b._overrides,
          heroTitle: b.heroTitle, heroSubtitle: b.heroSubtitle,
          heroBullets: b.heroBullets, heroCtaButtons: b.heroCtaButtons,
          heroImage: b.heroImage || heroSbConf.heroImage,
          heroBgColor: b.heroBgColor || heroSbConf.heroBgColor,
          heroCtaText: b.heroCtaText, heroCtaUrl: b.heroCtaUrl,
          heroImageLink: b.heroImageLink,
          i18n: heroSbConf.i18n,
        })
        break
      }
      case 'product_hero':
      case 'pimpam_hero': {
        const phSbConf = resolveStandaloneConfig(b)
        out.push({
          type: 'pimpam_hero',
          _sourceType: b._sourceType, _sourceId: b._sourceId, _overrides: b._overrides,
          heroTitle: b.heroTitle || phSbConf.heroTitle,
          heroSubtitle: b.heroSubtitle || phSbConf.heroSubtitle,
          heroBullets: (b.heroBullets && b.heroBullets.length) ? b.heroBullets : phSbConf.heroBullets,
          heroCtaButtons: (b.heroCtaButtons && b.heroCtaButtons.length) ? b.heroCtaButtons : phSbConf.heroCtaButtons,
          heroImage: b.heroImage || phSbConf.heroImage,
          heroBgColor: b.heroBgColor || phSbConf.heroBgColor,
          heroCtaText: b.heroCtaText || phSbConf.heroCtaText,
          heroCtaUrl: b.heroCtaUrl || phSbConf.heroCtaUrl,
          heroImageLink: b.heroImageLink || phSbConf.heroImageLink,
          // Block-level i18n wins over standalone-source i18n. This is what
          // makes a product_hero materialized in addBlock() switch language
          // when the user changes lang.
          i18n: b.i18n || phSbConf.i18n,
        })
        break
      }

      // ─── pimpam steps ────────────────────────────────────────
      case 'pimpam_steps': {
        const psSbConf = resolveStandaloneConfig(b)
        out.push({
          type: 'pimpam_steps',
          config: {
            steps: b.steps || psSbConf.steps,
            stepsBgColor: b.stepsBgColor || psSbConf.stepsBgColor,
            stepsBorderColor: b.stepsBorderColor || psSbConf.stepsBorderColor,
          }
        })
        break
      }

      // ─── video / freebird ────────────────────────────────────
      case 'video':
      case 'freebird': {
        const vSbConf = resolveStandaloneConfig(b)
        out.push({
          type: 'freebird',
          config: {
            youtubeUrl: b.youtubeUrl || vSbConf.youtubeUrl,
            thumbnailOverride: b.thumbnailOverride || vSbConf.thumbnailOverride,
          }
        })
        break
      }

      // ─── image / cta / divider — pass through unchanged (renderBlock handles them)
      case 'image':
      case 'cta':
      case 'divider': {
        out.push(Object.assign({}, b))
        break
      }
      // Compat: divider_line/short/dots pueden llegar literales desde
      // datos guardados con factories antiguos. Los renormalizamos al
      // shape canónico {type:'divider', style:…} que el renderer entiende.
      case 'divider_line':
      case 'divider_short':
      case 'divider_dots': {
        const style = b.type === 'divider_short' ? 'short' : b.type === 'divider_dots' ? 'dots' : 'line'
        out.push(Object.assign({}, b, { type: 'divider', style }))
        break
      }

      // ─── composed ───────────────────────────────────────────
      case 'composed': {
        const cb = b.composedId ? composedList.find(c => c.id === b.composedId) : null
        if (cb) {
          // Nuevo schema: compositorBlocks es una lista plana de bloques v3.
          // Recursión por el mismo bridge para que cada hijo (text, brand_strip,
          // product_*, image, cta, divider, video, hero, etc.) se renderice
          // con todo el vocabulario v3 disponible. Si no hay compositorBlocks
          // (legacy), pasa el cb tal cual y generateFullHtml usa los campos
          // antiguos (introText, brandStrip, products[], etc.).
          if (Array.isArray(cb.compositorBlocks) && cb.compositorBlocks.length > 0) {
            const childV2 = v3BlocksToV2Blocks(cb.compositorBlocks, appState)
            for (const x of childV2) out.push(x)
          } else {
            out.push(Object.assign({}, cb, { type: 'composed' }))
          }
        }
        break
      }

      // ─── header / footer (v3-only) ──────────────────────────
      case 'header': {
        const headerBrand = b.brand || 'bomedia'
        const headerSub = b.subtitle || 'Distribuidor oficial'
        out.push({ type:'text', _sourceType:'manual', _overrides:{ es:'<h2 style="text-align:center;font-size:18px;font-weight:800;color:#1a1918;margin:0">' + escapeHtml(headerBrand) + '</h2><p style="text-align:center;font-size:11px;color:#6b7280;margin:4px 0 0">' + escapeHtml(headerSub) + '</p>' } })
        break
      }
      case 'footer': {
        const legal = b.legal || 'Bomedia S.L.'
        const contact = b.contact || 'info@bomedia.es'
        const unsub = b.showUnsubscribe !== false ? ' · <a href="#" style="color:#6b7280">Darse de baja</a>' : ''
        out.push({ type:'text', _sourceType:'manual', _overrides:{ es:'<p style="text-align:center;font-size:11px;color:#6b7280;margin:0">' + escapeHtml(legal) + ' · ' + escapeHtml(contact) + unsub + '</p>' } })
        break
      }

      default:
        break
    }
    // Propagar widthPct/blockAlign del v3 al/los v2 generados (un v3 block
    // como composed puede expandir a varios v2 — todos heredan el ancho).
    if ((typeof b.widthPct === 'number' || b.blockAlign) && out.length > lengthBefore) {
      for (let i = lengthBefore; i < out.length; i++) {
        if (typeof b.widthPct === 'number') out[i].widthPct = b.widthPct
        if (b.blockAlign) out[i].blockAlign = b.blockAlign
      }
    }
  }
  return out
}

function renderEmailHtml(v3Blocks, appState, lang) {
  const v2Blocks = v3BlocksToV2Blocks(v3Blocks, appState)
  return generateFullHtml(v2Blocks, (appState && appState.products) || [], lang || 'es', (appState && appState.brands) || [], appState)
}

/* ───────────── UTM TRACKING ─────────────
   Portado de v2: cada vez que se copia/exporta el HTML, todos los <a> que
   apunten a páginas externas reciben query params utm_* para que el
   destino (Google Analytics, Plausible, Matomo, lo que sea) pueda
   atribuir la visita al email.

   - utm_source=email  (canal)
   - utm_medium=bomedia (plataforma)
   - utm_campaign=<id de campaña>  (autogenerado yyyymmdd-marca-idioma)
   - utm_term=<idioma>  (es/fr/de/en/nl)
   Se omite mailto:/tel:/#anchor que no son URL trackeables. */

function detectCampaignBrand(v3Blocks, appState) {
  const brandCounts = {}
  const products = (appState && appState.products) || []
  const inc = (b) => { if (!b) return; brandCounts[b] = (brandCounts[b] || 0) + 1 }
  ;(v3Blocks || []).forEach(b => {
    if (!b) return
    if (b.brand) inc(b.brand)
    if (b.type && b.type.startsWith && b.type.startsWith('brand_')) inc(b.type.replace('brand_', ''))
    if (b.product1) { const p = products.find(x => x.id === b.product1); if (p) inc(p.brand) }
    if (b.product2) { const p = products.find(x => x.id === b.product2); if (p) inc(p.brand) }
    if (b.product3) { const p = products.find(x => x.id === b.product3); if (p) inc(p.brand) }
    if (b.productId) { const p = products.find(x => x.id === b.productId); if (p) inc(p.brand) }
  })
  let top = 'mix', max = 0
  for (const k in brandCounts) { if (brandCounts[k] > max) { max = brandCounts[k]; top = k } }
  return top
}

function generateCampaignName(v3Blocks, lang, appState, customTitle) {
  const now = new Date()
  const yy = String(now.getFullYear())
  const mm = String(now.getMonth() + 1).padStart(2, '0')
  const dd = String(now.getDate()).padStart(2, '0')
  const brand = detectCampaignBrand(v3Blocks, appState)
  // Si el user ha dado un título al email, lo metemos slugificado para que
  // sea reconocible en GA. Si no, solo fecha-marca-idioma.
  const slug = customTitle
    ? '-' + String(customTitle).toLowerCase()
        .replace(/[áàäâ]/g, 'a').replace(/[éèëê]/g, 'e').replace(/[íìïî]/g, 'i')
        .replace(/[óòöô]/g, 'o').replace(/[úùüû]/g, 'u').replace(/[ñ]/g, 'n')
        .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40)
    : ''
  return yy + mm + dd + '-' + brand + '-' + (lang || 'es') + slug
}

/* Slugifica un nombre/id de usuario para que sea seguro en una URL. */
function _slugifyUser(s) {
  if (!s) return ''
  return String(s).toLowerCase()
    .replace(/[áàäâ]/g, 'a').replace(/[éèëê]/g, 'e').replace(/[íìïî]/g, 'i')
    .replace(/[óòöô]/g, 'o').replace(/[úùüû]/g, 'u').replace(/[ñ]/g, 'n')
    .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 30)
}

function addUtmParams(html, campaign, lang, userSlug) {
  if (!html) return ''
  let utmBase = 'utm_source=email&utm_medium=bomedia&utm_campaign=' + encodeURIComponent(campaign) +
    '&utm_term=' + encodeURIComponent(lang || 'es')
  // utm_content lleva el comercial que envía — permite atribuir aperturas
  // y clicks al usuario que copió el HTML. En GA aparece como dimension
  // "Ad Content" o "Content".
  if (userSlug) utmBase += '&utm_content=' + encodeURIComponent(userSlug)
  return html.replace(/<a\s([^>]*?)href="([^"]+)"([^>]*?)>/gi, function(match, pre, url, post) {
    // Saltar mailto:, tel:, javascript:, data: y anchors sin destino real
    if (/^(mailto:|tel:|javascript:|data:|#)/i.test(url)) return match
    // Si el href ya tiene utm_*, no duplicar
    if (/[?&]utm_(source|campaign|medium|term|content)=/i.test(url)) return match
    const separator = url.indexOf('?') >= 0 ? '&' : '?'
    return '<a ' + pre + 'href="' + url + separator + utmBase + '"' + post + '>'
  })
}

/* Composición conveniente: render + UTM en una sola llamada. Lo llaman
   los botones "Copiar HTML" y "Enviar". `currentUser` es opcional — si se
   pasa, su id (o nombre) va en utm_content. */
function renderEmailHtmlWithTracking(v3Blocks, appState, lang, customTitle, currentUser) {
  const html = renderEmailHtml(v3Blocks, appState, lang)
  const campaign = generateCampaignName(v3Blocks, lang, appState, customTitle)
  const userSlug = currentUser ? _slugifyUser(currentUser.id || currentUser.name) : ''
  return { html: addUtmParams(html, campaign, lang, userSlug), campaign, userSlug }
}

Object.assign(window, {
  CSS_BLOCK, escapeHtml,
  productCardHtml, productCardCompactHtml, productSingleHtml, productPairHtml, productTrioHtml,
  brandStripHtml, textBlockHtml, freebirdHtml, pimpamHeroHtml, pimpamStepsHtml, dividerBlockHtml,
  generateFullHtml, v3BlocksToV2Blocks, renderEmailHtml,
  addUtmParams, generateCampaignName, detectCampaignBrand, renderEmailHtmlWithTracking,
})