/* ───────────── AI AGENT — TOOL-USE LAYER ─────────────
   Lets the user ask the assistant to operate the email composer in natural
   language ("crea un email para vender la 5000U a un cliente alemán").

   Architecture:
   - buildAgentContext() — produces a compact catalog index injected into
     the system prompt so the model knows what's available without round
     trips. Approach C from the design doc: full IDs/names of every
     resource, but no full-text payloads (text bodies, hero markup…).
   - agentTools — array of tool definitions following OpenAI's function-
     calling shape. Each has a description, parameters schema, and an
     `execute(args, work, ctx)` function that mutates a working copy of
     the canvas state.
   - runAgent() — multi-turn loop: send messages + tools to OpenAI, the
     model either returns content (final answer) or tool_calls (we run
     them, append results, loop). After the loop, the working state is
     committed to the live App via callbacks. */

const AGENT_MAX_ITERATIONS = 12

const AGENT_SYSTEM_PROMPT = (
  'You are an AI assistant that operates a Spanish/multi-language email composer for Bomedia, a B2B distributor of UV-LED printing machines (artisJet, MBO, PimPam Vending, FLUX, SmartJet) and DTF printers (MBO DTF).\n' +
  '\n' +
  '## Reglas de oro\n' +
  '\n' +
  '1. **USA SIEMPRE LOS IDs LITERALES** del catálogo que aparece más abajo. Por ejemplo:\n' +
  '   - Productos: "young", "5000u", "mbo3050", "casebox" — NO inventes IDs como "art-young" o "mbo-3050".\n' +
  '   - Textos pre-escritos: "text-001", "text-002" — NO uses "txt-intro-uv".\n' +
  '   - Plantillas: "tpl-001", "tpl-002" — NO inventes nombres.\n' +
  '   - Composed/standalone: "block-001", "sb-001" — NO inventes "sb-brand-mbo".\n' +
  '   Si la herramienta devuelve "not found", revisa el catálogo del system prompt antes de reintentar.\n' +
  '\n' +
  '2. **Tipos de bloque VÁLIDOS** (úsalos exactamente, sin variantes):\n' +
  '   `text`, `text_from_library`, `product_single`, `product_pair`, `product_trio`,\n' +
  '   `brand_strip`, `cta`, `saved_cta`, `image`, `video`, `pimpam_hero`, `pimpam_steps`,\n' +
  '   `composed`, `section_2col`, `section_3col`.\n' +
  '   NO existen tipos como "sb-brand-mbo", "sb-hero-artisjet" o "sb-vid-mbo-uv". Para añadir un brand strip de MBO usa `brand_strip` con params={brand:"mbo"}. Para un hero existente usa `composed` con su composedId.\n' +
  '\n' +
  '3. **Parámetros**: cada llamada a `add_block` lleva `{type, params}`. El campo `params` es OBLIGATORIO y va anidado, ej:\n' +
  '   `add_block({type:"text_from_library", params:{textId:"text-001"}})` ✅\n' +
  '   `add_block({type:"text_from_library", textId:"text-001"})` ❌ (textId tiene que ir DENTRO de params).\n' +
  '\n' +
  '4. **add_block_to_column** requiere un `sectionId` REAL — el id que devolvió `add_block` cuando creaste la sección (algo tipo "a5-mokjm..."). NO uses "section_2col" como sectionId; ese es el TIPO, no el id.\n' +
  '   Ejemplo correcto:\n' +
  '   1) `add_block({type:"section_2col"})` → devuelve `{id:"a5-abc123-xyz"}`\n' +
  '   2) `add_block_to_column({sectionId:"a5-abc123-xyz", columnIndex:0, type:"product_single", params:{productId:"young"}})`\n' +
  '\n' +
  '5. **Idioma**: si el user pide otro idioma, primero `set_language(lang)` y LUEGO los add_block con texto en ese idioma. Match the active canvas language for any text content you generate.\n' +
  '\n' +
  '6. **NUNCA traduzcas nombres de máquinas** (artisJet 5000U, MBO 4060, PimPam, FLUX, SmartJet, etc.) ni marcas — son nombres propios.\n' +
  '\n' +
  '7. **Estructura recomendada para un email** completo (de arriba abajo):\n' +
  '   `brand_strip` (opcional) → `text` o `text_from_library` (intro) → producto/s → `cta` (opcional) → `text` (cierre/firma).\n' +
  '   Para comparar 2-3 productos en una fila, usa `section_2col` o `section_3col` y luego `add_block_to_column` para cada producto.\n' +
  '\n' +
  '8. **Tono**: ES = tutea al cliente, cercano y profesional. FR/DE/EN/NL = formal pero amistoso. Frases breves, B2B.\n' +
  '\n' +
  '9. **Al terminar**, responde en 1-2 frases en español describiendo qué hiciste.\n' +
  '\n' +
  '10. Si una herramienta falla 2 veces seguidas, NO sigas reintentando con los mismos parámetros. Lee el error, ajusta, o pide al user más detalles en tu respuesta final.\n' +
  '\n' +
  '## Detalles importantes de cada tipo de bloque\n' +
  '\n' +
  '- **CTA**: `text` es la ETIQUETA del botón (lo que aparece dentro del botón). `title` es un h3 OPCIONAL encima del botón. Si el usuario solo dice "CTA que diga X" o "botón que ponga X", pon `text:"X"` y deja `title:""`. Solo añade `title` si el usuario describe específicamente un encabezado encima del botón.\n' +
  '- **text**: para crear un texto manual usa `params.content`. Para reusar un texto pre-escrito usa `text_from_library` con `textId`.\n' +
  '\n' +
  '## update_block — modificar bloques existentes\n' +
  '\n' +
  'Para cambiar el contenido de un bloque ya creado, llama a `update_block(id, patch)`. Casos comunes:\n' +
  '\n' +
  '- **Cambiar contenido de un text en un idioma concreto** (lo más común al traducir):\n' +
  '  `update_block({id:"<blockId>", patch:{overridesByLang:{<lang>:"<contenido>"}}})`\n' +
  '  El campo se llama `overridesByLang`, NO `_overrides`. Solo afecta al idioma indicado; los demás idiomas se preservan.\n' +
  '\n' +
  '- **Cambiar el producto de un product_single**:\n' +
  '  `update_block({id:"<blockId>", patch:{product1:"<newProductId>"}})`\n' +
  '\n' +
  '- **Cambiar colores/texto de un CTA**:\n' +
  '  `update_block({id:"<blockId>", patch:{bg:"#16a34a", color:"#fff", text:"Nuevo botón"}})`\n' +
  '\n' +
  '## Borrar varios bloques (ej: "borra todos los X")\n' +
  '\n' +
  'Cuando el user pide borrar TODOS los bloques de un tipo o que cumplan un criterio, SIEMPRE llama PRIMERO a `read_canvas` para listar los IDs reales, y LUEGO llama a `delete_block(id)` por cada uno que coincida. NO asumas que solo hay uno.\n' +
  '\n' +
  '## Anti-alucinación (CRÍTICO)\n' +
  '\n' +
  '- **PROHIBIDO inventar URLs**. Si no conoces el URL real, deja el campo `url`/`src`/`link`/`youtubeUrl` como string VACÍO `""`. NUNCA escribas placeholders como `"url_de_contacto"`, `"sb-vid-mbo-uv"`, `"https://example.com"`, `"placeholder"`, `"#"`. El usuario lo rellenará después.\n' +
  '- **PROHIBIDO inventar imágenes**. Si añades un bloque `image`, su `src` debe venir del catálogo (un product.img que conozcas exacto) o quedarse vacío.\n' +
  '- **No añadas bloques redundantes**. Cada producto ya muestra su imagen, badge, precio, features y botón "Más info" en su tarjeta. NO añadas un bloque `image` con la foto del mismo producto, ni un `cta` con el mismo enlace que la tarjeta del producto. Una tarjeta de producto YA es completa.\n' +
  '\n' +
  '## Filtrado por marca (CRÍTICO)\n' +
  '\n' +
  'Cuando el usuario pide algo sobre una marca específica (MBO, artisJet, PimPam, FLUX, SmartJet), FILTRA los recursos por esa marca antes de elegir:\n' +
  '- Productos: usa solo los que tienen `[brand_id]` que coincida (ej. `[mbo]` para "MBO compactas").\n' +
  '- Textos pre-escritos: solo los que tienen `[mbo]` o `[mix]`. NO uses textos `[smartjet]` si el usuario pide MBO.\n' +
  '- Plantillas/Compuestos: igual.\n' +
  '\n' +
  'Si el usuario menciona use cases (fundas, señalética, packaging, textiles…), elige productos cuya descripción encaje:\n' +
  '- "Fundas/casebox/cases de móvil" → PimPam (casebox, custom) o artisJet Young (compact, ideal para personalización).\n' +
  '- "Señalética/signage/displays" → artisJet 6090 Trust, MBO UV1612G/UV1812/UV2513.\n' +
  '- "Pequeño formato/compactas" → artisJet Young, MBO 3050, MBO 4060.\n' +
  '- "Textil/DTF" → MBO DTF (productos brand=mbo_dtf).\n' +
  '- "Packaging/cajas/envases" → SmartJet FLEX (flexone, flex297, flexultra, flex324).\n' +
  '\n' +
  '## Coherencia de la generación\n' +
  '\n' +
  'ANTES de empezar a llamar tools, planifica brevemente la estructura. Un email B2B típico:\n' +
  '1. (opcional) brand_strip de la marca principal\n' +
  '2. Texto intro saludando al cliente y contextualizando — usa text_from_library si encaja, o genera con `text` mencionando el nombre del cliente\n' +
  '3. 1-3 productos (o una plantilla pre-armada)\n' +
  '4. (opcional) bloques compuestos / hero / video relevantes\n' +
  '5. Texto de cierre + CTA opcional con URL VACÍO\n' +
  '\n' +
  'No abras secciones de columnas si no las vas a llenar. No dejes secciones vacías o con una sola columna llena.\n' +
  '\n' +
  '## CRÍTICO: si el usuario pide algo explícito, INCLÚYELO\n' +
  '\n' +
  'Cuando el usuario lista elementos de la estructura ("brand strip, intro saludando, sección 2col, cierre, CTA"), TODOS son obligatorios y debes añadirlos en ese orden. NO te saltes pasos. Si el usuario menciona "CTA para reservar demo" añade un block tipo `cta` con título "Reservar demo" / "Book a demo" / etc. y url:""; un párrafo de cierre NO equivale a un CTA — el CTA es un block aparte con botón.\n' +
  '\n' +
  'Cuando el usuario describe un producto por su CARACTERÍSTICA ("entry-level", "versátil", "alta producción", "compacto", "industrial"), CONSULTA el `[badge]` del catálogo para encontrar el producto exacto que coincide:\n' +
  '- "entry/básico" → badge "Entry Level" o "Nuevo" o equivalente\n' +
  '- "versátil/balanced" → badge "Versátil"\n' +
  '- "avanzado/premium" → badge "Avanzada"\n' +
  '- "alta producción/industrial" → badge "Alta Producción" o "Industrial" o "Gran Formato"\n' +
  'Si el badge no encaja directamente, usa el snippet del `desc` que viene tras "—" en el catálogo para confirmar.\n' +
  '\n' +
  '## Fin\n' +
  '\n' +
  'A continuación tienes el estado del canvas y los recursos disponibles. ÚSALOS literalmente — no inventes IDs ni URLs.'
)

/* Build a compact index of everything the agent might want to reference.
   Targets ~600-1200 tokens so it stays in the system prompt without
   blowing the context budget. Per-item details (full text, i18n, etc.)
   are fetched on demand via get_* tools. */
function buildAgentContext({ appState, blocks, lang }) {
  appState = appState || {}
  blocks = blocks || []
  const products = (appState.products || []).filter(p => p.visible !== false)
  const texts = (appState.prewrittenTexts || []).filter(t => t.visible !== false)
  const templates = (appState.templates || []).filter(t => t.visible !== false)
  const composed = (appState.composedBlocks || []).filter(c => c.visible !== false)
  const standalones = (appState.standaloneBlocks || []).filter(s => s.visible !== false)
  const ctas = (appState.ctaBlocks || []).filter(c => c.visible !== false)
  const brands = (appState.brands || []).filter(b => b.id !== 'bomedia')

  let ctx = '## Estado actual del canvas\n'
  ctx += '- Idioma activo: ' + lang + '\n'
  ctx += '- Bloques en canvas: ' + blocks.length + '\n'
  if (blocks.length > 0) {
    ctx += '\n### Bloques actuales\n'
    blocks.forEach((b, i) => {
      ctx += (i + 1) + '. [' + b.id + '] ' + b.type
      if (b.type === 'text') {
        const ovr = (b._overrides && (b._overrides[lang] || b._overrides.es)) || b.text || ''
        ctx += ovr ? ' "' + String(ovr).replace(/\s+/g, ' ').slice(0, 60) + '…"' : ''
        if (b.textId) ctx += ' (linked to text-prewritten:' + b.textId + ')'
      } else if (b.type === 'product' || b.type === 'product_single') {
        ctx += ' product=' + (b.productId || b.product1)
      } else if (b.type === 'product_pair') {
        ctx += ' products=[' + b.product1 + ',' + b.product2 + ']'
      } else if (b.type === 'product_trio') {
        ctx += ' products=[' + b.product1 + ',' + b.product2 + ',' + b.product3 + ']'
      } else if (b.type === 'brand_strip' || b.type.startsWith('brand_')) {
        ctx += ' brand=' + (b.brand || b.type.replace('brand_', ''))
      } else if (b.type === 'cta') {
        ctx += ' "' + (b.title || b.text || 'CTA') + '"'
      } else if (b.type === 'image') {
        ctx += ' src=' + (b.src ? '(set)' : '(empty)')
      } else if (b.type === 'video' || b.type === 'freebird') {
        ctx += ' yt=' + (b.youtubeUrl ? '(set)' : '(empty)')
      } else if (b.type === 'pimpam_hero' || b.type === 'hero') {
        ctx += ' hero="' + (b.heroTitle || '').slice(0, 40) + '"'
      } else if (b.type === 'composed') {
        ctx += ' composed=' + b.composedId
      } else if (b.type === 'section') {
        const colCounts = (b.columns || []).map(c => (c.blocks || []).length).join('/')
        ctx += ' (' + (b.columns || []).length + ' cols, blocks-per-col=' + colCounts + ')'
      }
      ctx += '\n'
    })
  }

  ctx += '\n## Recursos disponibles\n'

  ctx += '\n### Marcas (' + brands.length + ')\n'
  brands.forEach(b => { ctx += '- ' + b.id + ': ' + b.label + '\n' })

  ctx += '\n### Productos (' + products.length + ')\n'
  products.forEach(p => {
    // Incluye el badge ("Versátil", "Top ventas", "Avanzada"…) y un
    // snippet del desc para que la IA pueda elegir el producto correcto
    // por use case sin tener que llamar a get_product cada vez.
    const badge = p.badge ? ' [' + p.badge + ']' : ''
    const desc = (p.desc || '').replace(/\s+/g, ' ').slice(0, 90)
    ctx += '- ' + p.id + ' (' + p.brand + ')' + badge + ': ' + p.name + ' · ' + (p.area || '-') + ' · ' + (p.price || '-')
    if (desc) ctx += ' — ' + desc
    ctx += '\n'
  })

  if (templates.length > 0) {
    ctx += '\n### Plantillas (' + templates.length + ')\n'
    templates.forEach(t => {
      ctx += '- ' + t.id + ' [' + (t.brand || 'mix') + ']: ' + t.name + (t.desc ? ' — ' + t.desc : '') + '\n'
    })
  }
  if (texts.length > 0) {
    ctx += '\n### Textos pre-escritos (' + texts.length + ')\n'
    texts.forEach(t => {
      const preview = (t.text || '').replace(/\s+/g, ' ').slice(0, 60)
      ctx += '- ' + t.id + ' [' + (t.brand || 'mix') + ']: ' + t.name + ' — "' + preview + '…"\n'
    })
  }
  if (composed.length > 0) {
    ctx += '\n### Bloques compuestos (' + composed.length + ')\n'
    composed.forEach(c => {
      ctx += '- ' + c.id + ' [' + (c.brand || c.brandStrip || '-') + ']: ' + c.title + (c.desc ? ' — ' + c.desc : '') + '\n'
    })
  }
  if (standalones.length > 0) {
    ctx += '\n### Bloques sueltos (' + standalones.length + ')\n'
    standalones.forEach(s => {
      ctx += '- ' + s.id + ' [' + (s.brand || '-') + ']: ' + s.title + ' (type:' + (s.blockType || s.type) + ')\n'
    })
  }
  if (ctas.length > 0) {
    ctx += '\n### CTAs guardados (' + ctas.length + ')\n'
    ctas.forEach(c => {
      ctx += '- ' + c.id + ': ' + (c.name || c.title || c.text) + (c.url ? ' → ' + c.url : '') + '\n'
    })
  }

  return ctx
}

/* Generate a stable id for blocks the agent creates. Uses the same scheme
   as createBlock() in app-data.jsx so they're indistinguishable from
   user-added blocks. */
let _agentBlockCounter = 0
function _agentMkId() {
  return 'a' + (++_agentBlockCounter) + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 6)
}

/* Detect "placeholder" values the LLM sometimes invents when it doesn't
   know the real URL/id. Returns true for things like "url_de_contacto",
   "https://example.com", "placeholder", etc. so we can blank them out. */
function _isPlaceholderValue(v) {
  if (!v || typeof v !== 'string') return false
  const s = v.trim().toLowerCase()
  if (!s) return false
  if (s === '#' || s === 'url' || s === 'src' || s === 'tbd' || s === 'todo') return true
  if (/^url[_-]/i.test(s)) return true                    // url_de_contacto, url-de-foto
  if (/_de_(contacto|la|los|las|el|tu|su)/i.test(s)) return true  // _de_la_foto, _de_contacto
  if (/example\.com|placeholder|lorem|insert[_-]?(here|url|link)/i.test(s)) return true
  if (/^sb-(vid|hero|brand|cta)-/i.test(s)) return true   // invented standalone-style ids
  if (/^txt-/i.test(s)) return true                       // invented text ids
  return false
}

/* Strip placeholder strings from a block's URL-like fields. Run on every
   block the agent creates so its hallucinations don't leak into the
   email HTML. Returns the cleaned block. */
function _stripPlaceholders(block) {
  if (!block || typeof block !== 'object') return block
  const urlFields = ['url','src','link','href','youtubeUrl','heroImage','heroImageLink']
  urlFields.forEach(f => {
    if (block[f] !== undefined && _isPlaceholderValue(block[f])) block[f] = ''
  })
  return block
}

/* Build a v3-shaped block from the (loosely typed) agent params object.
   Returns null on unknown type. */
function _buildBlockFromAgent(type, params, ctx) {
  params = params || {}
  const id = _agentMkId()
  switch (type) {
    case 'text': {
      const content = params.content || params.text || ''
      const lang = (ctx && ctx.workLang) || (ctx && ctx.lang) || 'es'
      // The v3 schema uses `overridesByLang` (the v3→v2 bridge in
      // app-email-gen looks for this). We also write `_overrides` for
      // compatibility with parts of the app that read the v2 shape
      // directly. Both keys carry the same map.
      const overrides = { [lang]: content }
      return { id, type: 'text', overridesByLang: overrides, _overrides: overrides, _sourceType: 'manual' }
    }
    case 'text_from_library': {
      if (!params.textId) return null
      // Validate the textId exists in the catalog so we don't push a
      // dangling ref into the canvas. If invalid, return null and let
      // the caller surface the error so the model retries with a real id.
      const txts = (ctx && ctx.appState && ctx.appState.prewrittenTexts) || []
      if (!txts.some(t => t.id === params.textId)) return null
      return { id, type: 'text', textId: params.textId, _sourceType: 'prewritten', _sourceId: params.textId }
    }
    case 'product_single':
    case 'product':
      return { id, type: 'product_single', product1: params.productId || params.product1 }
    case 'product_pair':
      return { id, type: 'product_pair', product1: params.productId1 || params.product1, product2: params.productId2 || params.product2 }
    case 'product_trio':
      return { id, type: 'product_trio',
        product1: params.productId1 || params.product1,
        product2: params.productId2 || params.product2,
        product3: params.productId3 || params.product3,
      }
    case 'brand_strip':
      return { id, type: 'brand_strip', brand: params.brand || 'artisjet' }
    case 'cta':
      return {
        id, type: 'cta',
        title: params.title || '',
        subtitle: params.subtitle || '',
        bullets: Array.isArray(params.bullets) ? params.bullets : [],
        text: params.text || params.buttonText || 'Más información',
        url: params.url || '',
        bg: params.bg || params.backgroundColor || '#1d4ed8',
        color: params.color || params.textColor || '#ffffff',
        align: params.align || 'center',
        panelBg: params.panelBg || 'transparent',
        panelBorder: params.panelBorder || 'transparent',
      }
    case 'saved_cta': {
      if (!params.ctaId) return null
      const src = ((ctx.appState && ctx.appState.ctaBlocks) || []).find(c => c.id === params.ctaId)
      if (!src) return null
      const out = { id, type: 'cta', _ctaSourceId: src.id }
      ;['title','subtitle','bullets','text','url','bg','color','align','panelBg','panelBorder'].forEach(k => {
        if (src[k] !== undefined) out[k] = Array.isArray(src[k]) ? src[k].slice() : src[k]
      })
      return out
    }
    case 'image':
      return { id, type: 'image',
        src: params.src || '',
        alt: params.alt || '',
        link: params.link || '',
        align: params.align || 'center',
        widthPct: params.widthPct || 100,
      }
    case 'video':
    case 'freebird':
      return { id, type: 'video', youtubeUrl: params.youtubeUrl || params.url || '' }
    case 'pimpam_hero':
    case 'hero':
      return { id, type: 'pimpam_hero',
        heroTitle: params.title || params.heroTitle || '',
        heroSubtitle: params.subtitle || params.heroSubtitle || '',
        heroImage: params.image || params.heroImage || '',
        heroBullets: Array.isArray(params.bullets) ? params.bullets : (params.heroBullets || []),
      }
    case 'pimpam_steps':
      return { id, type: 'pimpam_steps' }
    case 'composed': {
      if (!params.composedId) return null
      return { id, type: 'composed', composedId: params.composedId }
    }
    case 'section_2col':
      return { id, type: 'section', layout: '2col', columns: [{ blocks:[] }, { blocks:[] }] }
    case 'section_3col':
      return { id, type: 'section', layout: '3col', columns: [{ blocks:[] }, { blocks:[] }, { blocks:[] }] }
    default:
      return null
  }
}

/* Tool registry — each tool has a description, JSON schema for params,
   and an execute(args, work, ctx) that mutates `work` (the agent's
   working copy of canvas state) and returns a JSON string for the model. */
const agentTools = [
  {
    name: 'read_canvas',
    description: 'Read the current state of the canvas. Returns an array of blocks with their id, type, and key fields. Call this before making changes if you need to know what is already there.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    execute: (args, w, ctx) => {
      const summary = w.blocks.map(b => {
        const o = { id: b.id, type: b.type }
        if (b.type === 'text') {
          o.contentPreview = (b._overrides && (b._overrides[w.lang] || b._overrides.es) || b.text || '').slice(0, 120)
          if (b.textId) o.textId = b.textId
        }
        if (b.product1) o.product1 = b.product1
        if (b.product2) o.product2 = b.product2
        if (b.product3) o.product3 = b.product3
        if (b.brand) o.brand = b.brand
        if (b.composedId) o.composedId = b.composedId
        if (b.type === 'cta') { o.title = b.title; o.text = b.text; o.url = b.url }
        if (b.type === 'image') { o.src = b.src; o.alt = b.alt }
        if (b.type === 'video') o.youtubeUrl = b.youtubeUrl
        if (b.type === 'section') o.columns = (b.columns || []).map((c, i) => ({ index: i, blocks: (c.blocks || []).map(ib => ({ id: ib.id, type: ib.type })) }))
        return o
      })
      return JSON.stringify({ lang: w.lang, blocks: summary })
    },
  },

  {
    name: 'set_language',
    description: 'Change the canvas active language. Affects which i18n version of texts/products gets shown. Use this before adding text content if the user asked for a specific language.',
    parameters: {
      type: 'object',
      properties: {
        lang: { type: 'string', enum: ['es','fr','de','en','nl'] },
      },
      required: ['lang'],
      additionalProperties: false,
    },
    execute: (args, w) => {
      w.lang = args.lang
      return JSON.stringify({ ok: true, lang: w.lang })
    },
  },

  {
    name: 'add_block',
    description: (
      'Add a new block to the canvas. The "type" field determines which fields are valid in "params":\n' +
      '- text: { content: string }  — manual text in the active language\n' +
      '- text_from_library: { textId: string }  — reuse a saved pre-written text\n' +
      '- product_single: { productId: string }\n' +
      '- product_pair: { productId1, productId2 }\n' +
      '- product_trio: { productId1, productId2, productId3 }\n' +
      '- brand_strip: { brand: brandId }\n' +
      '- cta: { title?, subtitle?, bullets?[], text, url, bg?, color?, align?, panelBg?, panelBorder? }\n' +
      '- saved_cta: { ctaId: string }  — instantiate a stored CTA\n' +
      '- image: { src, alt?, link?, align?, widthPct? }\n' +
      '- video: { youtubeUrl }\n' +
      '- pimpam_hero: { title?, subtitle?, image?, bullets[]? }\n' +
      '- pimpam_steps: {}\n' +
      '- composed: { composedId }  — instantiate a saved composite\n' +
      '- section_2col: {}, section_3col: {}  — layout containers (initially empty)\n\n' +
      'After creating a section, use add_block_to_column to fill its columns.'
    ),
    parameters: {
      type: 'object',
      properties: {
        type: { type: 'string', description: 'Block type — see description for available types' },
        params: { type: 'object', description: 'Parameters specific to the chosen type' },
        position: { type: 'integer', description: 'Optional: index where to insert (default = end)' },
      },
      required: ['type'],
    },
    execute: (args, w, ctx) => {
      // Be lenient with how the model packages params: accept either
      // {type, params:{textId:'…'}} (correct) or {type, textId:'…'}
      // (flattened — common LLM mistake). Merge top-level fields into
      // params so both work.
      const merged = Object.assign({}, args.params || {})
      Object.keys(args || {}).forEach(k => {
        if (k !== 'type' && k !== 'position' && k !== 'params' && merged[k] === undefined) merged[k] = args[k]
      })
      const validTypes = ['text','text_from_library','product_single','product','product_pair','product_trio','brand_strip','cta','saved_cta','image','video','freebird','pimpam_hero','hero','pimpam_steps','composed','section_2col','section_3col']
      if (!validTypes.includes(args.type)) {
        return JSON.stringify({ ok: false, error: 'Tipo inválido: "' + args.type + '". Tipos válidos: ' + validTypes.join(', ') })
      }
      const rawBlock = _buildBlockFromAgent(args.type, merged, Object.assign({}, ctx, { workLang: w.lang }))
      const block = _stripPlaceholders(rawBlock)
      if (!block) {
        // Tipo-specific error hints with concrete suggestions when the
        // failure was a non-existent id (very common LLM mistake).
        if (args.type === 'text_from_library' && merged.textId) {
          const samples = ((ctx.appState && ctx.appState.prewrittenTexts) || []).slice(0, 6).map(t => t.id + ' (' + t.name + ')').join(', ')
          return JSON.stringify({ ok: false, error: 'textId "' + merged.textId + '" no existe. IDs reales (primeros 6): ' + samples + '. Mira el catálogo del system prompt para la lista completa.' })
        }
        if (args.type === 'product_single' && merged.productId) {
          const samples = ((ctx.appState && ctx.appState.products) || []).slice(0, 8).map(p => p.id).join(', ')
          return JSON.stringify({ ok: false, error: 'productId "' + merged.productId + '" no existe. Algunos IDs reales: ' + samples + '.' })
        }
        if (args.type === 'composed' && merged.composedId) {
          const samples = ((ctx.appState && ctx.appState.composedBlocks) || []).slice(0, 4).map(c => c.id).join(', ')
          return JSON.stringify({ ok: false, error: 'composedId "' + merged.composedId + '" no existe. Algunos IDs reales: ' + samples + '.' })
        }
        const need = {
          text_from_library: 'textId',
          product_single: 'productId',
          product_pair: 'productId1, productId2',
          product_trio: 'productId1, productId2, productId3',
          composed: 'composedId',
          saved_cta: 'ctaId',
        }[args.type]
        return JSON.stringify({ ok: false, error: 'Faltan parámetros requeridos para ' + args.type + (need ? ' (necesita: ' + need + ')' : '') + '. Recibido params: ' + JSON.stringify(merged).slice(0, 160) })
      }
      const pos = (typeof args.position === 'number' && args.position >= 0 && args.position <= w.blocks.length) ? args.position : w.blocks.length
      w.blocks = [...w.blocks.slice(0, pos), block, ...w.blocks.slice(pos)]
      return JSON.stringify({ ok: true, id: block.id, type: block.type, position: pos, hint: block.type === 'section' ? 'Usa este "id" como sectionId al llamar a add_block_to_column.' : undefined })
    },
  },

  {
    name: 'add_block_to_column',
    description: 'Add a block inside a specific column of a section block. Use this after creating a section_2col / section_3col to fill its columns.',
    parameters: {
      type: 'object',
      properties: {
        sectionId: { type: 'string' },
        columnIndex: { type: 'integer', description: '0-indexed column position' },
        type: { type: 'string', description: 'Same block types as add_block' },
        params: { type: 'object' },
      },
      required: ['sectionId','columnIndex','type'],
    },
    execute: (args, w, ctx) => {
      // Lenient param parsing (same as add_block)
      const merged = Object.assign({}, args.params || {})
      Object.keys(args || {}).forEach(k => {
        if (!['type','params','sectionId','columnIndex'].includes(k) && merged[k] === undefined) merged[k] = args[k]
      })
      // Common mistake: model passes the type ('section_2col') as sectionId.
      // Suggest the real ids found in the working canvas.
      const sectionsAvailable = w.blocks.filter(b => b.type === 'section').map(b => ({ id: b.id, columns: (b.columns || []).length }))
      if (args.sectionId === 'section_2col' || args.sectionId === 'section_3col' || args.sectionId === 'section') {
        const hint = sectionsAvailable.length > 0
          ? 'sectionId debe ser el id real, no el tipo. Secciones disponibles: ' + JSON.stringify(sectionsAvailable)
          : 'No hay secciones en el canvas. Crea una primero con add_block({type:"section_2col"}).'
        return JSON.stringify({ ok: false, error: hint })
      }
      const rawBlock = _buildBlockFromAgent(args.type, merged, Object.assign({}, ctx, { workLang: w.lang }))
      const block = _stripPlaceholders(rawBlock)
      if (!block) return JSON.stringify({ ok: false, error: 'Tipo o parámetros inválidos para el bloque interno: ' + args.type + '. Recibido: ' + JSON.stringify(merged).slice(0, 120) })
      let found = false
      w.blocks = w.blocks.map(b => {
        if (b.id !== args.sectionId) return b
        if (b.type !== 'section' || !Array.isArray(b.columns)) return b
        const cols = b.columns.slice()
        const ci = args.columnIndex
        if (ci < 0 || ci >= cols.length) return b
        cols[ci] = { ...cols[ci], blocks: [...(cols[ci].blocks || []), block] }
        found = true
        return { ...b, columns: cols }
      })
      if (!found) {
        const hint = sectionsAvailable.length > 0
          ? 'Sección "' + args.sectionId + '" no encontrada. Secciones disponibles: ' + JSON.stringify(sectionsAvailable)
          : 'No hay secciones en el canvas todavía.'
        return JSON.stringify({ ok: false, error: hint })
      }
      return JSON.stringify({ ok: true, addedTo: { sectionId: args.sectionId, columnIndex: args.columnIndex }, blockId: block.id })
    },
  },

  {
    name: 'update_block',
    description: 'Modify fields of an existing block. Pass only the fields you want to change. For text blocks, pass { _overrides: { es: "...", fr: "..." } } to update content per language.',
    parameters: {
      type: 'object',
      properties: {
        id: { type: 'string' },
        patch: { type: 'object', description: 'Object with fields to merge into the block' },
      },
      required: ['id','patch'],
    },
    execute: (args, w) => {
      // Lenient param parsing: model often sends fields flat instead of
      // nested in `patch`. Merge top-level fields into patch.
      let patch = Object.assign({}, args.patch || {})
      Object.keys(args || {}).forEach(k => {
        if (k !== 'id' && k !== 'patch' && patch[k] === undefined) patch[k] = args[k]
      })
      // Common LLM mistake: patches _overrides on a text block, but the
      // v3→v2 bridge looks for overridesByLang. Mirror the patch into
      // both keys so the change actually takes effect.
      if (patch._overrides && typeof patch._overrides === 'object') {
        patch.overridesByLang = Object.assign({}, patch.overridesByLang || {}, patch._overrides)
      }
      if (patch.overridesByLang && typeof patch.overridesByLang === 'object') {
        patch._overrides = Object.assign({}, patch._overrides || {}, patch.overridesByLang)
      }
      // Strip placeholders the model might invent on link/url/src patches.
      const safe = _stripPlaceholders(Object.assign({}, patch))
      let found = false
      const recur = (arr) => arr.map(b => {
        if (b.id === args.id) {
          found = true
          // Deep-merge the overrides maps so we don't wipe other languages
          const next = { ...b, ...safe }
          if (b.overridesByLang && safe.overridesByLang) {
            next.overridesByLang = Object.assign({}, b.overridesByLang, safe.overridesByLang)
          }
          if (b._overrides && safe._overrides) {
            next._overrides = Object.assign({}, b._overrides, safe._overrides)
          }
          return next
        }
        if (b.type === 'section' && Array.isArray(b.columns)) {
          return { ...b, columns: b.columns.map(c => ({ ...c, blocks: recur(c.blocks || []) })) }
        }
        return b
      })
      w.blocks = recur(w.blocks)
      return JSON.stringify({ ok: found, id: args.id, appliedPatch: Object.keys(safe) })
    },
  },

  {
    name: 'delete_block',
    description: 'Remove a block from the canvas (top-level or inside a section column). Pass the block id.',
    parameters: {
      type: 'object',
      properties: {
        id: { type: 'string' },
      },
      required: ['id'],
    },
    execute: (args, w) => {
      const recur = (arr) => arr.filter(b => b.id !== args.id).map(b => {
        if (b.type === 'section' && Array.isArray(b.columns)) {
          return { ...b, columns: b.columns.map(c => ({ ...c, blocks: recur(c.blocks || []) })) }
        }
        return b
      })
      const before = JSON.stringify(w.blocks)
      w.blocks = recur(w.blocks)
      return JSON.stringify({ ok: before !== JSON.stringify(w.blocks), id: args.id })
    },
  },

  {
    name: 'reorder_blocks',
    description: 'Reorder top-level canvas blocks. Pass an array of block ids in the desired new order. Any ids missing from the array are appended at the end in their current order.',
    parameters: {
      type: 'object',
      properties: {
        idOrder: { type: 'array', items: { type: 'string' } },
      },
      required: ['idOrder'],
    },
    execute: (args, w) => {
      const map = new Map(w.blocks.map(b => [b.id, b]))
      const ordered = []
      const seen = new Set()
      args.idOrder.forEach(id => { if (map.has(id) && !seen.has(id)) { ordered.push(map.get(id)); seen.add(id) } })
      w.blocks.forEach(b => { if (!seen.has(b.id)) ordered.push(b) })
      w.blocks = ordered
      return JSON.stringify({ ok: true, order: w.blocks.map(b => b.id) })
    },
  },

  {
    name: 'clear_canvas',
    description: 'Remove ALL blocks from the canvas. DESTRUCTIVE. Only call this if the user EXPLICITLY asks to "start over", "vaciar todo", "borrar todo el canvas" or similar. Never call it as a side-effect of another request.',
    parameters: { type: 'object', properties: {
      confirmed: { type: 'boolean', description: 'Set to true only when the user explicitly asked to clear the canvas (not as a guess).' },
    }, additionalProperties: false },
    execute: (args, w) => {
      // Defensive: require explicit confirmation flag from the model. If
      // the user's intent was ambiguous, refuse and let the model ask.
      if (args && args.confirmed !== true) {
        return JSON.stringify({ ok: false, error: 'clear_canvas requiere confirmed:true. Solo llámalo si el user pidió EXPLÍCITAMENTE vaciar/borrar todo el canvas. Si dudas, no lo llames.' })
      }
      const removed = w.blocks.length
      w.blocks = []
      return JSON.stringify({ ok: true, removed })
    },
  },

  {
    name: 'load_template',
    description: 'Replace the canvas with the blocks from a saved template. DESTRUCTIVE — clears the current canvas first. Only call this if the user EXPLICITLY asks to load a template (e.g. "abre la plantilla X", "carga la plantilla Y", "úsame la plantilla Z"). Never call it as a guess or to "start fresh" without explicit user request. Requires confirmed:true.',
    parameters: {
      type: 'object',
      properties: {
        templateId: { type: 'string' },
        confirmed: { type: 'boolean', description: 'Set to true only when the user explicitly asked to load this template. If you would also wipe pre-existing user content, double-check the user wanted that.' },
      },
      required: ['templateId'],
    },
    execute: (args, w, ctx) => {
      // Defensive gate: same pattern as clear_canvas. Sin esto, el modelo
      // cargaba plantillas como side-effect ("para empezar de cero…")
      // pisando el trabajo del usuario sin confirmación.
      if (args && args.confirmed !== true) {
        return JSON.stringify({ ok: false, error: 'load_template requiere confirmed:true. Es destructivo (vacía el canvas actual). Solo llámalo si el user pidió EXPLÍCITAMENTE cargar esa plantilla. Si dudas, pregunta antes.' })
      }
      const t = ((ctx.appState && ctx.appState.templates) || []).find(x => x.id === args.templateId)
      if (!t) return JSON.stringify({ ok: false, error: 'Template not found: ' + args.templateId })
      // Use the live expandTemplate helper if available; otherwise manual expansion
      let expanded = []
      if (typeof window !== 'undefined' && typeof window.expandTemplate === 'function') {
        expanded = window.expandTemplate(args.templateId)
      } else if (Array.isArray(t.compositorBlocks) && t.compositorBlocks.length > 0) {
        expanded = t.compositorBlocks.map(b => Object.assign({ id: _agentMkId() }, b))
      } else if (Array.isArray(t.blocks)) {
        expanded = t.blocks.map(ref => ({ id: _agentMkId(), type: 'text', textId: ref }))
      }
      const previousCount = w.blocks.length
      w.blocks = expanded.map(b => Object.assign({}, b, b.id ? {} : { id: _agentMkId() }))
      return JSON.stringify({ ok: true, template: t.name, blockCount: w.blocks.length, replacedBlocks: previousCount })
    },
  },

  {
    name: 'get_product',
    description: 'Get full details of a product, including i18n translations of name/desc/feat/price/link. Use this when you need to reference exact pricing or feature lists in a specific language.',
    parameters: {
      type: 'object',
      properties: { productId: { type: 'string' } },
      required: ['productId'],
    },
    execute: (args, w, ctx) => {
      const p = ((ctx.appState && ctx.appState.products) || []).find(x => x.id === args.productId)
      if (!p) return JSON.stringify({ ok: false, error: 'Product not found' })
      return JSON.stringify({ ok: true, product: p })
    },
  },

  {
    name: 'get_text',
    description: 'Get full details of a pre-written text including its body and i18n translations. Use when you need to inspect or quote a saved text.',
    parameters: {
      type: 'object',
      properties: { textId: { type: 'string' } },
      required: ['textId'],
    },
    execute: (args, w, ctx) => {
      const t = ((ctx.appState && ctx.appState.prewrittenTexts) || []).find(x => x.id === args.textId)
      if (!t) return JSON.stringify({ ok: false, error: 'Text not found' })
      return JSON.stringify({ ok: true, text: t })
    },
  },

  {
    name: 'get_template',
    description: 'Get full details of a template, including its blocks list. Use to inspect what would be loaded.',
    parameters: {
      type: 'object',
      properties: { templateId: { type: 'string' } },
      required: ['templateId'],
    },
    execute: (args, w, ctx) => {
      const t = ((ctx.appState && ctx.appState.templates) || []).find(x => x.id === args.templateId)
      if (!t) return JSON.stringify({ ok: false, error: 'Template not found' })
      return JSON.stringify({ ok: true, template: t })
    },
  },

  {
    name: 'get_composed',
    description: 'Get full details of a saved composite block, including intro text per language and product list.',
    parameters: {
      type: 'object',
      properties: { composedId: { type: 'string' } },
      required: ['composedId'],
    },
    execute: (args, w, ctx) => {
      const c = ((ctx.appState && ctx.appState.composedBlocks) || []).find(x => x.id === args.composedId)
      if (!c) return JSON.stringify({ ok: false, error: 'Composed not found' })
      return JSON.stringify({ ok: true, composed: c })
    },
  },
]

/* The actual OpenAI request with tools enabled. Wrapping in its own
   function makes it easy to add retry / fallback later. */
async function callAgentApi({ messages, tools }) {
  const key = (typeof getOpenaiKey === 'function' ? getOpenaiKey() : '').trim()
  if (!key) throw new Error('Configura tu API key de OpenAI en Backoffice → Asistente IA antes de usar el agente.')
  if (!key.startsWith('sk-')) throw new Error('La API key no parece válida.')
  const r = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key },
    body: JSON.stringify({
      // gpt-4o-mini hallucinaba IDs/URLs y mezclaba marcas. gpt-4o tiene
      // mejor reasoning multi-step y respeta las constraints del system
      // prompt (filtrar por marca, no inventar placeholders, etc.).
      // Coste: ~$0.01 por interacción típica vs ~$0.001 con mini —
      // negligible para B2B de bajo volumen.
      model: 'gpt-4o',
      messages,
      tools,
      tool_choice: 'auto',
      temperature: 0.3,
      max_tokens: 2000,
    }),
  })
  if (!r.ok) {
    let detail = ''
    try { const j = await r.json(); detail = j.error?.message || JSON.stringify(j) } catch (e) { detail = await r.text() }
    throw new Error('OpenAI ' + r.status + ': ' + String(detail).slice(0, 300))
  }
  const data = await r.json()
  return data.choices?.[0]?.message
}

/* Run the agent loop. Calls onStep for every action so the UI can show
   progress. After completion, the caller commits the working state. */
async function runAgent({ prompt, ctx, onStep }) {
  const work = {
    blocks: (ctx.blocks || []).slice(),
    lang: ctx.lang || 'es',
  }
  // Log de actividad: registramos el prompt del user (truncado a 240
  // chars para no inflar el log) y el nº de bloques antes de ejecutar.
  // El resultado/iteraciones se logea aparte al final.
  if (typeof window !== 'undefined' && typeof window.logActivity === 'function') {
    window.logActivity('ai_agent_run', {
      prompt: String(prompt || '').slice(0, 240),
      blocksBefore: work.blocks.length,
      lang: work.lang,
    })
  }
  const sysContent = AGENT_SYSTEM_PROMPT + '\n\n' + buildAgentContext({ appState: ctx.appState, blocks: work.blocks, lang: work.lang })
  const messages = [
    { role: 'system', content: sysContent },
    { role: 'user', content: prompt },
  ]
  const toolsForApi = agentTools.map(t => ({
    type: 'function',
    function: { name: t.name, description: t.description, parameters: t.parameters },
  }))

  for (let step = 0; step < AGENT_MAX_ITERATIONS; step++) {
    onStep && onStep({ kind: 'thinking', step })
    let assistantMsg
    try {
      assistantMsg = await callAgentApi({ messages, tools: toolsForApi })
    } catch (err) {
      onStep && onStep({ kind: 'error', error: err.message || String(err) })
      return { ok: false, work, error: err.message || String(err) }
    }
    if (!assistantMsg) {
      onStep && onStep({ kind: 'error', error: 'No assistant message returned' })
      return { ok: false, work, error: 'No response' }
    }
    messages.push(assistantMsg)

    const toolCalls = assistantMsg.tool_calls
    if (!Array.isArray(toolCalls) || toolCalls.length === 0) {
      // Final answer
      onStep && onStep({ kind: 'final', text: assistantMsg.content || '' })
      return { ok: true, work, finalText: assistantMsg.content || '' }
    }

    // Execute tools in order
    for (const tc of toolCalls) {
      const fn = tc.function && tc.function.name
      let args = {}
      try { args = JSON.parse(tc.function.arguments || '{}') } catch (e) {}
      const tool = agentTools.find(t => t.name === fn)
      onStep && onStep({ kind: 'tool', name: fn, args })
      if (!tool) {
        messages.push({ role: 'tool', tool_call_id: tc.id, content: JSON.stringify({ error: 'Unknown tool ' + fn }) })
        continue
      }
      let result
      try {
        result = tool.execute(args, work, ctx)
      } catch (err) {
        result = JSON.stringify({ error: err.message || String(err) })
        onStep && onStep({ kind: 'tool_error', name: fn, error: err.message || String(err) })
      }
      messages.push({ role: 'tool', tool_call_id: tc.id, content: result })
      onStep && onStep({ kind: 'tool_result', name: fn, result })
    }
  }

  onStep && onStep({ kind: 'error', error: 'Max iterations (' + AGENT_MAX_ITERATIONS + ') exceeded' })
  return { ok: false, work, error: 'Max iterations exceeded' }
}

Object.assign(window, {
  AGENT_SYSTEM_PROMPT,
  buildAgentContext,
  agentTools,
  callAgentApi,
  runAgent,
})
