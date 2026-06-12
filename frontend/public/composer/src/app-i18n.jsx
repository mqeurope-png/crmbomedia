/* ───────────── I18N LAYER (ported from v2) ───────────── */

function getLocalizedProduct(product, lang) {
  if (!lang || lang === 'es' || !product.i18n || !product.i18n[lang]) return product
  const localized = Object.assign({}, product)
  const overrides = product.i18n[lang]
  for (const key in overrides) {
    if (overrides[key]) localized[key] = overrides[key]
  }
  return localized
}

function getLocalizedText(obj, field, lang) {
  if (!lang || lang === 'es' || !obj.i18n || !obj.i18n[lang] || !obj.i18n[lang][field]) {
    return obj[field]
  }
  return obj.i18n[lang][field]
}

function isAvailableInLang(item, lang) {
  if (!lang || !item.langs || item.langs.length === 0) return true
  return item.langs.indexOf(lang) >= 0
}

function getTextInLanguage(block, lang, appState) {
  if (block._overrides && block._overrides[lang]) return block._overrides[lang]

  if (block._sourceType === 'prewritten' && block._sourceId) {
    const src = (appState.prewrittenTexts || []).find(t => t.id === block._sourceId)
    if (src) {
      if (!lang || lang === 'es') return src.text
      if (src.i18n && src.i18n[lang] && src.i18n[lang].text) return src.i18n[lang].text
      return src.text
    }
  }

  if (block._sourceType === 'composed_inner' && block._composedSourceId) {
    const srcCb = (appState.composedBlocks || []).find(cb => cb.id === block._composedSourceId)
    if (srcCb) {
      const innerIdx = typeof block._innerIdx === 'number' ? block._innerIdx : -1
      let srcIb = null
      if (innerIdx >= 0 && srcCb.innerBlocks && srcCb.innerBlocks[innerIdx]) {
        srcIb = srcCb.innerBlocks[innerIdx]
      } else if (srcCb.innerBlocks) {
        srcIb = srcCb.innerBlocks.find(ib => ib.type === 'text')
      }
      if (srcIb) {
        if (!lang || lang === 'es') return srcIb.text
        if (srcIb.i18n && srcIb.i18n[lang] && srcIb.i18n[lang].text) return srcIb.i18n[lang].text
        return srcIb.text
      }
      if (!lang || lang === 'es') return srcCb.introText || ''
      if (srcCb.i18n && srcCb.i18n[lang] && srcCb.i18n[lang].introText) return srcCb.i18n[lang].introText
      return srcCb.introText || ''
    }
  }

  if (block._sourceType === 'manual') {
    if (block._overrides && block._overrides[lang]) return block._overrides[lang]
    if (block._overrides && block._overrides.es) return block._overrides.es
    return block.text || ''
  }

  if (block.i18n) return getLocalizedText(block, 'text', lang)
  return block.text || ''
}

function getHeroDataInLanguage(block, lang, appState) {
  const result = {
    heroTitle: block.heroTitle || '',
    heroSubtitle: block.heroSubtitle || '',
    heroBullets: block.heroBullets || [],
    heroCtaText: block.heroCtaText || '',
    heroCtaUrl: block.heroCtaUrl || '',
    heroCtaButtons: block.heroCtaButtons || [],
    heroImage: block.heroImage || '',
    heroImageLink: block.heroImageLink || '',
    heroBgColor: block.heroBgColor || '#fff',
  }

  let srcConfig = null
  if (block._sourceType === 'standalone' && block._sourceId) {
    const srcSb = (appState.standaloneBlocks || []).find(sb => sb.id === block._sourceId)
    if (srcSb && srcSb.config) srcConfig = srcSb.config
  } else if (block._sourceType === 'composed_inner' && block._composedSourceId) {
    const srcCb2 = (appState.composedBlocks || []).find(cb => cb.id === block._composedSourceId)
    if (srcCb2) {
      const innerIdx2 = typeof block._innerIdx === 'number' ? block._innerIdx : -1
      if (innerIdx2 >= 0 && srcCb2.innerBlocks && srcCb2.innerBlocks[innerIdx2]) {
        const srcIb2 = srcCb2.innerBlocks[innerIdx2]
        if (srcIb2.type === 'pimpam_hero') srcConfig = srcIb2
      }
    }
  }

  if (srcConfig) {
    // Block fields (user edits) take priority over source config defaults
    result.heroTitle = result.heroTitle || srcConfig.heroTitle || ''
    result.heroSubtitle = result.heroSubtitle || srcConfig.heroSubtitle || ''
    result.heroBullets = (result.heroBullets && result.heroBullets.length > 0) ? result.heroBullets : (srcConfig.heroBullets || [])
    result.heroCtaText = result.heroCtaText || srcConfig.heroCtaText || ''
    result.heroCtaUrl = result.heroCtaUrl || srcConfig.heroCtaUrl || ''
    result.heroCtaButtons = (result.heroCtaButtons && result.heroCtaButtons.length > 0) ? result.heroCtaButtons : (srcConfig.heroCtaButtons || [])
    result.heroImage = result.heroImage || srcConfig.heroImage || ''
    result.heroImageLink = result.heroImageLink || srcConfig.heroImageLink || ''
    result.heroBgColor = result.heroBgColor || srcConfig.heroBgColor || '#fff'

    if (lang && lang !== 'es' && srcConfig.i18n && srcConfig.i18n[lang]) {
      const hi = srcConfig.i18n[lang]
      if (hi.heroTitle) result.heroTitle = hi.heroTitle
      if (hi.heroSubtitle) result.heroSubtitle = hi.heroSubtitle
      if (hi.heroBullets) result.heroBullets = hi.heroBullets
      if (hi.heroCtaText) result.heroCtaText = hi.heroCtaText
      if (hi.heroCtaButtons) result.heroCtaButtons = hi.heroCtaButtons
    }
  } else if (block.i18n && lang && lang !== 'es' && block.i18n[lang]) {
    const bhi = block.i18n[lang]
    if (bhi.heroTitle) result.heroTitle = bhi.heroTitle
    if (bhi.heroSubtitle) result.heroSubtitle = bhi.heroSubtitle
    if (bhi.heroBullets) result.heroBullets = bhi.heroBullets
    if (bhi.heroCtaText) result.heroCtaText = bhi.heroCtaText
  }

  if (block._overrides && block._overrides[lang]) {
    const ovr = block._overrides[lang]
    if (typeof ovr === 'object') {
      if (ovr.heroTitle) result.heroTitle = ovr.heroTitle
      if (ovr.heroSubtitle) result.heroSubtitle = ovr.heroSubtitle
      if (ovr.heroBullets) result.heroBullets = ovr.heroBullets
      if (ovr.heroCtaText) result.heroCtaText = ovr.heroCtaText
    }
  }

  return result
}

function mergeI18nFromDefaults(loadedData) {
  const defaults = getDefaultState()

  if (loadedData.products && defaults.products) {
    const defaultMap = {}
    defaults.products.forEach(dp => { defaultMap[dp.id] = dp })
    loadedData.products = loadedData.products.map(p => {
      const dp = defaultMap[p.id]
      if (dp && dp.i18n && !p.i18n) p.i18n = dp.i18n
      return p
    })
  }

  if (loadedData.composedBlocks && defaults.composedBlocks) {
    const blockMap = {}
    defaults.composedBlocks.forEach(db => { blockMap[db.id] = db })
    loadedData.composedBlocks = loadedData.composedBlocks.map(b => {
      const db = blockMap[b.id]
      if (db && db.i18n && !b.i18n) b.i18n = db.i18n
      return b
    })
  }

  if (loadedData.prewrittenTexts && defaults.prewrittenTexts) {
    const textMap = {}
    defaults.prewrittenTexts.forEach(dt => { textMap[dt.id] = dt })
    loadedData.prewrittenTexts = loadedData.prewrittenTexts.map(t => {
      const dt = textMap[t.id]
      if (dt && dt.i18n && !t.i18n) t.i18n = dt.i18n
      return t
    })
  }

  if (loadedData.standaloneBlocks && defaults.standaloneBlocks) {
    const sbMap = {}
    defaults.standaloneBlocks.forEach(dsb => { sbMap[dsb.id] = dsb })
    loadedData.standaloneBlocks = loadedData.standaloneBlocks.map(sb => {
      const dsb = sbMap[sb.id]
      if (dsb && dsb.config && dsb.config.i18n && sb.config && !sb.config.i18n) sb.config.i18n = dsb.config.i18n
      return sb
    })
  }

  if (loadedData.composedBlocks && defaults.composedBlocks) {
    const cbMap = {}
    defaults.composedBlocks.forEach(dcb => { cbMap[dcb.id] = dcb })
    loadedData.composedBlocks = loadedData.composedBlocks.map(cb => {
      const dcb = cbMap[cb.id]
      if (dcb && dcb.innerBlocks && cb.innerBlocks) {
        cb.innerBlocks = cb.innerBlocks.map((ib, idx) => {
          const dib = dcb.innerBlocks[idx]
          if (dib && dib.i18n && !ib.i18n) ib.i18n = dib.i18n
          return ib
        })
      }
      return cb
    })
  }
  return loadedData
}

Object.assign(window, {
  getLocalizedProduct, getLocalizedText, isAvailableInLang,
  getTextInLanguage, getHeroDataInLanguage, mergeI18nFromDefaults,
})
