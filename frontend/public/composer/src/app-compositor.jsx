/* ───────────── COMPOSITOR VIEW ───────────── */

/* Always read live data published by App from appState — without this the
   Sidebar / BlockCard / CommandPalette show stale info after Backoffice
   edits (or after Supabase hydration with IDs different from the bundled
   defaults). */
const _liveProducts2 = () => (typeof window !== 'undefined' && window.PRODUCTS) || PRODUCTS || [];
const _liveBrands2   = () => (typeof window !== 'undefined' && window.BRANDS) || BRANDS || [];

/* Tiny localStorage-backed useState — used to persist sidebar filters */
function usePersistentState(key, defaultValue) {
  const [value, setValue] = React.useState(() => {
    try {
      const raw = localStorage.getItem('bomedia-ui-' + key);
      return raw != null ? JSON.parse(raw) : defaultValue;
    } catch (e) { return defaultValue; }
  });
  React.useEffect(() => {
    try { localStorage.setItem('bomedia-ui-' + key, JSON.stringify(value)); } catch (e) {}
  }, [key, value]);
  return [value, setValue];
}

/* Parse a price string into a number (rough). Returns null for "Consultar" / "On request". */
function priceToNumber(price) {
  if (!price) return null;
  const s = String(price).toLowerCase();
  if (s.includes('consultar') || s.includes('request') || s.includes('demande') || s.includes('anfrage') || s.includes('aanvraag')) return null;
  // Strip currency, "desde/from/à partir de", "/mes" etc., keep digits and separators.
  const m = s.replace(/[€$]/g, '').match(/(\d[\d.,\s]*)/);
  if (!m) return null;
  const num = parseFloat(m[1].replace(/[.,\s]/g, ''));
  return Number.isFinite(num) ? num : null;
}

/* Map a numeric price into a coarse bucket for the price filter. */
function priceBucket(price) {
  const n = priceToNumber(price);
  if (n == null) return 'consultar';
  if (n < 10000) return 'low';
  if (n < 20000) return 'mid';
  return 'high';
}

/* Map a standalone blockType to the unified type filter taxonomy. */
function standaloneTypeKey(blockType) {
  if (blockType === 'pimpam_hero' || blockType === 'product_hero' || blockType === 'hero') return 'heroes';
  if (blockType === 'pimpam_steps') return 'pasos';
  if (blockType === 'video' || blockType === 'freebird') return 'videos';
  if (blockType === 'brand_strip') return 'marcas';
  if (blockType === 'product_single' || blockType === 'product_pair' || blockType === 'product_trio') return 'productos';
  return 'otros';
}

const TYPE_FILTERS = [
  { id: 'all', label: 'Todos' },
  { id: 'productos', label: 'Productos' },
  { id: 'compuestos', label: 'Compuestos' },
  { id: 'heroes', label: 'Heroes' },
  { id: 'videos', label: 'Vídeos' },
  { id: 'marcas', label: 'Marcas' },
];

const PRICE_FILTERS = [
  { id: 'all', label: 'Todos' },
  { id: 'low', label: '< 10k' },
  { id: 'mid', label: '10–20k' },
  { id: 'high', label: '≥ 20k' },
  { id: 'consultar', label: 'Consultar' },
];

/* Returns true if `id` is in the current user's per-collection hidden list. */
function isHiddenForUser(currentUser, collection, id) {
  if (!currentUser) return false;
  const list = currentUser.hiddenItems?.[collection];
  return Array.isArray(list) && list.includes(id);
}

function Sidebar({ collapsed, onToggle, blocks, onAddBlock, brandFilter, setBrandFilter, lang, currentUser }) {
  const [tab, setTab] = usePersistentState('sidebar-tab', 'library');
  const [search, setSearch] = React.useState('');
  const [typeFilter, setTypeFilter] = usePersistentState('sidebar-type', 'all');
  const [priceFilter, setPriceFilter] = usePersistentState('sidebar-price', 'all');

  if (collapsed) {
    return (
      <aside className="sidebar">
        <div className="sidebar-rail">
          <button className="rail-btn active" onClick={onToggle} title="Expandir">
            <Icon name="sidebar" />
          </button>
          <button className="rail-btn" title="Biblioteca"><Icon name="layers" /></button>
          <button className="rail-btn" title="Plantillas"><Icon name="template" /></button>
          <button className="rail-btn" title="Productos"><Icon name="box" /></button>
          <button className="rail-btn" title="Textos"><Icon name="text" /></button>
        </div>
      </aside>
    );
  }

  const q = search.trim().toLowerCase();
  const matchesQ = (s) => !q || (s || '').toLowerCase().includes(q);
  // brandFilter 'all'  → todo
  // brandFilter 'mix'  → solo items con brand:'mix' o sin marca (Multi-marca)
  // brandFilter '<id>' → items de esa marca + items mix (los multi-marca
  //                      aparecen junto a cualquier marca específica para
  //                      no esconderlos accidentalmente al filtrar)
  const matchesBrand = (b) => {
    if (brandFilter === 'all') return true;
    if (brandFilter === 'mix') return !b || b === 'mix';
    return b === brandFilter || b === 'mix' || !b;
  };

  const showProducts = typeFilter === 'all' || typeFilter === 'productos';
  const showCompuestos = typeFilter === 'all' || typeFilter === 'compuestos';
  const showStandaloneType = (sbType) => {
    if (typeFilter === 'all') return true;
    return standaloneTypeKey(sbType) === typeFilter;
  };

  // Read live data published from appState (so Backoffice / Supabase edits are visible)
  // with fallback to the module-level defaults.
  const productsAll  = (typeof window !== 'undefined' && window.PRODUCTS) || PRODUCTS || [];
  const textsAll     = (typeof window !== 'undefined' && window.PREWRITTEN_TEXTS) || PREWRITTEN_TEXTS || [];
  const templatesAll = (typeof window !== 'undefined' && window.TEMPLATES) || TEMPLATES || [];
  const standaloneAll = (typeof window !== 'undefined' && window.STANDALONE_BLOCKS) || STANDALONE_BLOCKS || [];
  const composedAll  = (typeof window !== 'undefined' && window.COMPOSED_BLOCKS) || COMPOSED_BLOCKS || [];

  const filteredProducts = showProducts
    ? productsAll.filter(p =>
        p.visible !== false &&
        !isHiddenForUser(currentUser, 'products', p.id) &&
        matchesBrand(p.brand) &&
        matchesQ(p.name) &&
        (priceFilter === 'all' || priceBucket(p.price) === priceFilter)
      )
    : [];

  const filteredTexts = textsAll.filter(t =>
    t.visible !== false && !isHiddenForUser(currentUser, 'prewrittenTexts', t.id)
    && matchesBrand(t.brand) && (matchesQ(t.name) || matchesQ(t.text))
  );

  const filteredTemplates = templatesAll.filter(t =>
    t.visible !== false && !isHiddenForUser(currentUser, 'templates', t.id)
    && matchesBrand(t.brand) && (matchesQ(t.name) || matchesQ(t.desc))
  );

  const filteredStandalone = standaloneAll.filter(b =>
    b.visible !== false && !isHiddenForUser(currentUser, 'standaloneBlocks', b.id)
    && matchesBrand(b.brand) && matchesQ(b.title) && showStandaloneType(b.blockType || b.type)
  );
  // Composed blocks don't carry an explicit `brand` field — derive it from
  // brandStrip (preferred) or the first product, so the brand chip filter
  // actually has something to match against.
  const composedBrand = (c) => {
    if (c.brand) return c.brand;
    if (c.brandStrip && c.brandStrip !== 'none') return c.brandStrip;
    const firstPid = (c.products || [])[0];
    if (firstPid) {
      const p = productsAll.find(x => x.id === firstPid);
      if (p && p.brand) return p.brand;
    }
    return 'mix';
  };

  const filteredComposed = showCompuestos
    ? composedAll.filter(c =>
        c.visible !== false &&
        !isHiddenForUser(currentUser, 'composedBlocks', c.id) &&
        matchesBrand(composedBrand(c)) &&
        (matchesQ(c.title) || matchesQ(c.desc))
      )
    : [];

  const onLibraryTab = tab === 'library';
  const showPriceRow = onLibraryTab && (typeFilter === 'all' || typeFilter === 'productos');

  const totalLibrary = filteredProducts.length + filteredComposed.length + filteredStandalone.length;
  const noResults = onLibraryTab && totalLibrary === 0
    || tab === 'templates' && filteredTemplates.length === 0
    || tab === 'texts' && filteredTexts.length === 0;

  const resetFilters = () => {
    setSearch('');
    setBrandFilter('all');
    setTypeFilter('all');
    setPriceFilter('all');
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <span className="sidebar-title">Biblioteca</span>
        <button className="icon-btn" onClick={onToggle} title="Colapsar" style={{width:24,height:24}}>
          <Icon name="sidebar" size={14} />
        </button>
      </div>

      <div className="local-search">
        <Icon name="search" size={14} />
        <input
          placeholder="Buscar en biblioteca…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>

      <div className="brand-chips">
        <button
          className={'brand-chip' + (brandFilter === 'all' ? ' active' : '')}
          onClick={() => setBrandFilter('all')}
        >Todas</button>
        {/* Chip "Multi-marca" — filtra a los items con brand:'mix' o sin
            marca asignada. Por convención no es una marca real, pero el
            user puede querer ver solo lo cross-brand. */}
        <button
          className={'brand-chip' + (brandFilter === 'mix' ? ' active' : '')}
          onClick={() => setBrandFilter('mix')}
          style={brandFilter === 'mix' ? {} : { color: '#94a3b8' }}
        >
          <span className="brand-chip-dot" style={{ background: '#94a3b8' }} />
          Multi-marca
        </button>
        {BRANDS.filter(b => b.id !== 'bomedia').map(b => (
          <button
            key={b.id}
            className={'brand-chip' + (brandFilter === b.id ? ' active' : '')}
            onClick={() => setBrandFilter(b.id)}
            style={brandFilter === b.id ? {} : { color: b.color }}
          >
            <span className="brand-chip-dot" style={{ background: b.color }} />
            {b.label}
          </button>
        ))}
      </div>

      {onLibraryTab && (
        <>
          <div className="filter-row">
            <span className="filter-row-label">Tipo</span>
            {TYPE_FILTERS.map(f => (
              <button
                key={f.id}
                className={'filter-chip' + (typeFilter === f.id ? ' active' : '')}
                onClick={() => setTypeFilter(f.id)}
              >{f.label}</button>
            ))}
          </div>
          {showPriceRow && (
            <div className="filter-row">
              <span className="filter-row-label">Precio</span>
              {PRICE_FILTERS.map(f => (
                <button
                  key={f.id}
                  className={'filter-chip' + (priceFilter === f.id ? ' active' : '')}
                  onClick={() => setPriceFilter(f.id)}
                >{f.label}</button>
              ))}
            </div>
          )}
        </>
      )}

      <div className="nav-tabs">
        {[
          { id: 'library', label: 'Bloques' },
          { id: 'templates', label: 'Plantillas' },
          { id: 'texts', label: 'Textos' },
        ].map(t => (
          <button
            key={t.id}
            className={'nav-tab' + (tab === t.id ? ' active' : '')}
            onClick={() => setTab(t.id)}
          >{t.label}</button>
        ))}
      </div>

      <div className="sidebar-body scroll">
        {onLibraryTab && (
          <>
            {/* Layout / sections: multi-column containers. Always shown so the
                user can build emails with side-by-side blocks. */}
            <div className="group">
              <div className="group-header">
                Layout <span className="count mono">5</span>
              </div>
              <button className="lib-item" onClick={() => onAddBlock({ type: 'section_2col' })}>
                <div className="lib-icon mix"><Icon name="grid" size={14} /></div>
                <div style={{minWidth:0}}>
                  <div className="lib-title">2 columnas</div>
                  <div className="lib-sub">Sección con dos columnas iguales (50/50). Stack en móvil.</div>
                  <div className="lib-meta">
                    <span className="lib-badge" style={{background:'color-mix(in oklch, var(--accent) 12%, transparent)', color:'var(--accent-ink)', fontWeight:600}}>layout</span>
                  </div>
                </div>
                <span className="lib-add"><Icon name="plus" size={14} /></span>
              </button>
              <button className="lib-item" onClick={() => onAddBlock({ type: 'section_3col' })}>
                <div className="lib-icon mix"><Icon name="grid" size={14} /></div>
                <div style={{minWidth:0}}>
                  <div className="lib-title">3 columnas</div>
                  <div className="lib-sub">Sección con tres columnas iguales (33/33/33). Stack en móvil.</div>
                  <div className="lib-meta">
                    <span className="lib-badge" style={{background:'color-mix(in oklch, var(--accent) 12%, transparent)', color:'var(--accent-ink)', fontWeight:600}}>layout</span>
                  </div>
                </div>
                <span className="lib-add"><Icon name="plus" size={14} /></span>
              </button>
              <button className="lib-item" onClick={() => onAddBlock({ type: 'divider_line' })}>
                <div className="lib-icon mix" style={{display:'flex', alignItems:'center', justifyContent:'center'}}>
                  <div style={{width:18, height:1, background:'currentColor'}}/>
                </div>
                <div style={{minWidth:0}}>
                  <div className="lib-title">Línea fina</div>
                  <div className="lib-sub">Línea horizontal sutil, full width. Ideal entre secciones.</div>
                  <div className="lib-meta">
                    <span className="lib-badge" style={{background:'var(--bg-sunken)', color:'var(--text-muted)'}}>divisor</span>
                  </div>
                </div>
                <span className="lib-add"><Icon name="plus" size={14} /></span>
              </button>
              <button className="lib-item" onClick={() => onAddBlock({ type: 'divider_short' })}>
                <div className="lib-icon mix" style={{display:'flex', alignItems:'center', justifyContent:'center'}}>
                  <div style={{width:10, height:2, background:'currentColor', borderRadius:1}}/>
                </div>
                <div style={{minWidth:0}}>
                  <div className="lib-title">Línea corta centrada</div>
                  <div className="lib-sub">Línea corta de 80px centrada. Separador elegante de capítulos.</div>
                  <div className="lib-meta">
                    <span className="lib-badge" style={{background:'var(--bg-sunken)', color:'var(--text-muted)'}}>divisor</span>
                  </div>
                </div>
                <span className="lib-add"><Icon name="plus" size={14} /></span>
              </button>
              <button className="lib-item" onClick={() => onAddBlock({ type: 'divider_dots' })}>
                <div className="lib-icon mix" style={{display:'flex', alignItems:'center', justifyContent:'center', letterSpacing:2, fontSize:14}}>
                  ···
                </div>
                <div style={{minWidth:0}}>
                  <div className="lib-title">Puntos ornamentales</div>
                  <div className="lib-sub">Tres puntos centrados con espaciado. Separador refinado.</div>
                  <div className="lib-meta">
                    <span className="lib-badge" style={{background:'var(--bg-sunken)', color:'var(--text-muted)'}}>divisor</span>
                  </div>
                </div>
                <span className="lib-add"><Icon name="plus" size={14} /></span>
              </button>
            </div>

            {filteredProducts.length > 0 && (
              <div className="group">
                <div className="group-header">
                  Productos <span className="count mono">{filteredProducts.length}</span>
                </div>
                {filteredProducts.map(p => {
                  const brand = BRANDS.find(b => b.id === p.brand);
                  // Resolve per-lang fields (price, badge, etc.) — name typically
                  // isn't translated but getLocalizedProduct falls back gracefully.
                  const lp = (typeof window.getLocalizedProduct === 'function') ? window.getLocalizedProduct(p, lang) : p;
                  return (
                    <button key={p.id} className="lib-item"
                            draggable
                            onClick={() => onAddBlock({ type: 'product', productId: p.id })}>
                      <div className={'lib-icon ' + p.brand}>
                        <img src={lp.img} alt="" style={{width:28,height:28,objectFit:'contain'}} />
                      </div>
                      <div style={{minWidth:0}}>
                        <div className="lib-title">{lp.name}</div>
                        <div className="lib-sub">{lp.area} · <span className="mono">{lp.price}</span></div>
                        <div className="lib-meta">
                          {brand && (
                            <span className="lib-brand-tag" style={{ color: brand.color }}>{brand.label}</span>
                          )}
                          {lp.badge && (
                            <span className="lib-badge" style={{ background: p.badgeBg || 'var(--bg-sunken)', color: p.badgeColor || 'var(--text-muted)' }}>
                              {lp.badge}
                            </span>
                          )}
                        </div>
                      </div>
                      <span className="lib-add"><Icon name="plus" size={14} /></span>
                    </button>
                  );
                })}
              </div>
            )}

            {filteredComposed.length > 0 && (
              <div className="group">
                <div className="group-header">
                  Compuestos <span className="count mono">{filteredComposed.length}</span>
                </div>
                {filteredComposed.map(c => {
                  const brand = BRANDS.find(b => b.id === c.brand);
                  const cTitle = (typeof window.getLocalizedText === 'function') ? window.getLocalizedText(c, 'title', lang) : c.title;
                  const cDesc = (typeof window.getLocalizedText === 'function') ? window.getLocalizedText(c, 'desc', lang) : c.desc;
                  return (
                    <button key={c.id} className="lib-item"
                            onClick={() => onAddBlock({ type: 'composed', composedId: c.id })}>
                      <div className={'lib-icon ' + (c.brand || 'mix')}>
                        <Icon name="layers" size={14} />
                      </div>
                      <div style={{minWidth:0}}>
                        <div className="lib-title">
                          {c.colorTag && <span className={'lib-color-tag ' + c.colorTag} />}
                          {cTitle}
                        </div>
                        <div className="lib-sub">{cDesc}</div>
                        <div className="lib-meta">
                          {brand && (
                            <span className="lib-brand-tag" style={{ color: brand.color }}>{brand.label}</span>
                          )}
                          {c.priceRange && c.priceRange !== '-' && (
                            <span className="lib-badge" style={{ background: 'var(--bg-sunken)', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                              {c.priceRange}
                            </span>
                          )}
                        </div>
                      </div>
                      <span className="lib-add"><Icon name="plus" size={14} /></span>
                    </button>
                  );
                })}
              </div>
            )}

            {filteredStandalone.length > 0 && (
              <div className="group">
                <div className="group-header">
                  Composiciones <span className="count mono">{filteredStandalone.length}</span>
                </div>
                {filteredStandalone.map(b => {
                  const brand = BRANDS.find(x => x.id === b.brand);
                  const bTitle = (typeof window.getLocalizedText === 'function') ? window.getLocalizedText(b, 'title', lang) : b.title;
                  return (
                    <button key={b.id} className="lib-item"
                            onClick={() => onAddBlock({ type: b.type, standaloneId: b.id })}>
                      <div className={'lib-icon ' + b.brand}>{b.icon}</div>
                      <div style={{minWidth:0}}>
                        <div className="lib-title">{bTitle}</div>
                        <div className="lib-sub serif">{b.section}</div>
                        <div className="lib-meta">
                          {brand && b.brand !== 'mix' && (
                            <span className="lib-brand-tag" style={{ color: brand.color }}>{brand.label}</span>
                          )}
                        </div>
                      </div>
                      <span className="lib-add"><Icon name="plus" size={14} /></span>
                    </button>
                  );
                })}
              </div>
            )}
          </>
        )}

        {tab === 'templates' && (
          <div className="group">
            <div className="group-header">
              Plantillas <span className="count mono">{filteredTemplates.length}</span>
            </div>
            {filteredTemplates.map(t => {
              const brand = BRANDS.find(b => b.id === t.brand);
              const tName = (typeof window.getLocalizedText === 'function') ? window.getLocalizedText(t, 'name', lang) : t.name;
              const tDesc = (typeof window.getLocalizedText === 'function') ? window.getLocalizedText(t, 'desc', lang) : t.desc;
              return (
                <button key={t.id} className="lib-item"
                        onClick={() => onAddBlock({ type: 'template', templateId: t.id })}>
                  <div className={'lib-icon ' + t.brand}><Icon name="template" size={14} /></div>
                  <div style={{minWidth:0}}>
                    <div className="lib-title">
                      {t.colorClass && <span className={'lib-color-tag ' + t.colorClass} />}
                      {tName}
                    </div>
                    <div className="lib-sub">{tDesc}</div>
                    <div className="lib-meta">
                      {brand && (
                        <span className="lib-brand-tag" style={{ color: brand.color }}>{brand.label}</span>
                      )}
                      <span className="lib-badge" style={{ background: 'var(--bg-sunken)', color: 'var(--text-muted)' }}>
                        {((t.blocks && t.blocks.length) || (t.compositorBlocks && t.compositorBlocks.length) || 0)} bloques
                      </span>
                    </div>
                  </div>
                  <span className="lib-add"><Icon name="plus" size={14} /></span>
                </button>
              );
            })}
          </div>
        )}

        {tab === 'texts' && (
          <div className="group">
            <div className="group-header">
              Texto <span className="count mono">{filteredTexts.length + 1}</span>
            </div>
            {/* Always-on "blank text" item — adds an empty editable text block to the canvas */}
            <button className="lib-item"
                    onClick={() => onAddBlock({ type: 'text-blank' })}>
              <div className="lib-icon mix"><Icon name="text" size={14} /></div>
              <div style={{minWidth:0}}>
                <div className="lib-title">Texto en blanco</div>
                <div className="lib-sub">Bloque vacío para escribir desde cero</div>
                <div className="lib-meta">
                  <span className="lib-badge" style={{background:'color-mix(in oklch, var(--accent) 12%, transparent)', color:'var(--accent-ink)', fontWeight:600}}>nuevo</span>
                </div>
              </div>
              <span className="lib-add"><Icon name="plus" size={14} /></span>
            </button>
            {filteredTexts.map(t => {
              const brand = BRANDS.find(b => b.id === t.brand);
              const tName = (typeof window.getLocalizedText === 'function') ? window.getLocalizedText(t, 'name', lang) : t.name;
              const tText = (typeof window.getLocalizedText === 'function') ? window.getLocalizedText(t, 'text', lang) : t.text;
              return (
                <button key={t.id} className="lib-item"
                        onClick={() => onAddBlock({ type: 'text', textId: t.id })}>
                  <div className={'lib-icon ' + t.brand}>{t.icon}</div>
                  <div style={{minWidth:0}}>
                    <div className="lib-title">{tName}</div>
                    <div className="lib-sub">{(tText || '').slice(0, 60)}…</div>
                    <div className="lib-meta">
                      {brand && t.brand !== 'mix' && (
                        <span className="lib-brand-tag" style={{ color: brand.color }}>{brand.label}</span>
                      )}
                    </div>
                  </div>
                  <span className="lib-add"><Icon name="plus" size={14} /></span>
                </button>
              );
            })}
          </div>
        )}

        {noResults && (
          <div style={{padding:'24px 16px', textAlign:'center', color:'var(--text-muted)', fontSize:12}}>
            <div className="serif" style={{fontSize:14, marginBottom:6}}>Sin resultados</div>
            <div style={{fontSize:11, marginBottom:10}}>Prueba a quitar algún filtro o cambiar el término.</div>
            <button className="btn btn-ghost" style={{fontSize:11}} onClick={resetFilters}>
              <Icon name="x" size={11} /> Limpiar filtros
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}

/* Small reusable mini-card for product references inside a block. */
function MiniProduct({ p, lang, compact }) {
  if (!p) {
    return (
      <div style={{
        border:'1px dashed var(--border-strong)', borderRadius:'var(--r-sm)',
        padding:12, textAlign:'center', fontSize:11, color:'var(--text-subtle)',
      }}>
        Producto no seleccionado
      </div>
    );
  }
  const lp = (typeof getLocalizedProduct === 'function') ? getLocalizedProduct(p, lang) : p;
  // Fixed-height image area so two cards rendered side-by-side in the same
  // row (product_pair / product_trio) start at the same vertical position
  // regardless of the source image aspect ratio. Without this, a wide-but-
  // short image left a smaller box and pushed the title up, while a tall
  // image filled the box and pushed the title down — the two cards looked
  // unaligned even though they shared a grid row.
  const imgBoxH = compact ? 70 : 100;
  return (
    <div style={{
      border: '1px solid ' + (p.brand === 'pimpam' ? '#fed7aa' : 'var(--border)'),
      borderRadius: 'var(--r-sm)',
      background: p.brand === 'pimpam' ? '#fff7ed' : 'var(--bg-panel)',
      padding: compact ? 8 : 10,
      overflow: 'hidden',
      display:'flex',
      flexDirection:'column',
    }}>
      <div style={{
        height: imgBoxH,
        marginBottom: 6,
        display:'flex',
        alignItems:'center',
        justifyContent:'center',
        flexShrink: 0,
      }}>
        <img src={lp.img} alt="" style={{maxWidth:'100%', maxHeight:'100%', objectFit:'contain'}} />
      </div>
      {lp.badge && (
        <span style={{
          display:'inline-block', fontSize:8, fontWeight:800, letterSpacing:1,
          textTransform:'uppercase', padding:'2px 6px', borderRadius:10,
          background: lp.badgeBg || '#f1f5f9', color: lp.badgeColor || '#475569',
          marginBottom: 4,
        }}>{lp.badge}</span>
      )}
      <div style={{fontWeight:800, fontSize: compact ? 11 : 12, color:'var(--text)'}}>{lp.name}</div>
      {!compact && (
        <div style={{fontSize:10, color:'var(--text-muted)', marginTop:3, lineHeight:1.4}}>
          {lp.desc}
        </div>
      )}
      <div style={{fontWeight:800, fontSize: compact ? 11 : 13, color: lp.accent || 'var(--text)', marginTop:6, textAlign:'center'}}>
        {lp.price}
      </div>
    </div>
  );
}

/* Single-brand strip (logo + localized URL). */
function BrandStripPreview({ brandId, lang }) {
  const _brands = (typeof window !== 'undefined' && window.BRANDS) || BRANDS || [];
  const b = _brands.find(x => x.id === brandId);
  if (!b) return <div style={{padding:12, fontSize:12, color:'var(--text-subtle)'}}>Marca no encontrada: {brandId}</div>;
  const url = (typeof b.url === 'object') ? (b.url[lang] || b.url.es) : b.url;
  const urlLabel = (typeof b.urlLabel === 'object') ? (b.urlLabel[lang] || b.urlLabel.es) : b.urlLabel;
  return (
    <div style={{display:'flex', alignItems:'center', gap:10, padding:'12px 4px', borderBottom: `1px solid ${b.divider || 'var(--border)'}`}}>
      {b.logo ? (
        <img src={b.logo} alt={b.label} style={{maxHeight: (b.logoHeight || 22) + 'px', maxWidth:180, width:'auto', height:'auto'}} />
      ) : (
        <strong style={{color: b.color, fontSize:14}}>{b.label}</strong>
      )}
      <a href={url || '#'} target="_blank" rel="noreferrer" style={{
        marginLeft:'auto', fontSize:12, fontWeight:700,
        color:b.color, textDecoration:'none', whiteSpace:'nowrap',
      }} onClick={e => e.stopPropagation()}>
        {urlLabel}
      </a>
    </div>
  );
}

/* Inline editor for text blocks rendered inside the canvas. Always rich —
   plain mode was confusing and added little value (the toggle constantly
   needed re-syncing). Plain content from old blocks is auto-promoted to
   rich on first display. */
function InlineTextBlock({ block, text, selected, lang, onUpdate }) {
  const [aiOpen, setAiOpen] = React.useState(false);

  // Resolve the source text in the current language. Prewritten texts have
  // translations under text.i18n[lang].text — without this, the canvas
  // would always show Spanish even when EN/FR/DE/NL are active.
  const localizedSourceText = (text && typeof window.getLocalizedText === 'function')
    ? window.getLocalizedText(text, 'text', lang)
    : (text?.text || '');
  const plainSeed = block.overridesByLang?.[lang] ?? block.overrideText ?? localizedSourceText ?? '';
  // Rich HTML is now stored per-language. Legacy blocks with a single
  // _richHtml string are honoured for ES.
  const richByLang = block._richHtmlByLang || {};
  const legacyRich = lang === 'es' && typeof block._richHtml === 'string' ? block._richHtml : null;
  const storedRich = richByLang[lang] ?? legacyRich;
  const richHtml = storedRich != null
    ? storedRich
    : (plainSeed
      ? '<p>' + String(plainSeed).split('\n').filter(Boolean).join('</p><p>') + '</p>'
      : '');
  const fontSize = block.fontSize || 14;

  const setRich = (html) => {
    const stripped = String(html || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
    const nextRichByLang = { ...(block._richHtmlByLang || {}), [lang]: html };
    const next = {
      ...block,
      _richHtmlByLang: nextRichByLang,
      overridesByLang: { ...block.overridesByLang, [lang]: stripped },
    };
    // Drop the legacy single-string field so it doesn't shadow per-lang
    if ('_richHtml' in next) delete next._richHtml;
    onUpdate(block.id, next);
  };

  const applyAi = (generated) => {
    const html = '<p>' + generated.split(/\n\n+/).map(p => p.trim()).filter(Boolean).join('</p><p>') + '</p>';
    setRich(html);
  };

  // Read-only preview when not selected
  if (!selected) {
    const sanitized = (typeof window !== 'undefined' && window.sanitizeHtml) ? window.sanitizeHtml(richHtml || '') : (richHtml || '');
    return (
      <div className="block-text">
        <div
          className="block-text-rich"
          style={{ padding: '8px 0', minHeight: 60, textAlign: block.align || 'left', fontSize: fontSize + 'px' }}
          dangerouslySetInnerHTML={{ __html: sanitized || '<span style="color:var(--text-subtle); font-style:italic">Texto vacío</span>' }}
        />
      </div>
    );
  }

  // Selected: rich editor inline
  return (
    <div className="block-text inline-edit" onClick={e => e.stopPropagation()}>
      <div className="inline-edit-toolbar">
        <button className="btn btn-ghost" style={{fontSize:11, padding:'4px 10px', marginLeft:'auto'}} onClick={() => setAiOpen(true)} title="Generar/reescribir con IA">
          <Icon name="sparkles" size={11} /> IA
        </button>
      </div>
      {typeof RichTextEditor !== 'undefined'
        ? <RichTextEditor value={richHtml || ''} onChange={setRich} placeholder="Escribe el texto…" fontSize={fontSize} />
        : <div style={{padding:10, fontSize:12, color:'var(--text-muted)'}}>RichTextEditor no cargado.</div>}
      {aiOpen && typeof window.AiTextPopover === 'function' && (
        <window.AiTextPopover
          lang={lang}
          currentText={(richHtml || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()}
          onApply={applyAi}
          onClose={() => setAiOpen(false)}
        />
      )}
    </div>
  );
}

function BlockCard({ block, idx, total, selected, onSelect, onUpdate, onDelete, onMove, onReorder, onDuplicate, onUngroup, lang, onOpenInnerPalette, onPickColumnAdd, appState, isInner, selectedId }) {
  // Drag-drop reorder. The block becomes draggable only when the user
  // mousedowns on the .block-handle so clicks elsewhere (inputs, buttons,
  // selects) keep working normally. `dropEdge` is 'top'/'bottom' to draw a
  // marker showing where the dragged block will land.
  const [dragArmed, setDragArmed] = React.useState(false);
  const [dropEdge, setDropEdge] = React.useState(null);
  const armDrag = () => setDragArmed(true);
  const disarmDrag = () => setDragArmed(false);
  const handleDragStart = (e) => {
    if (!onReorder) { e.preventDefault(); return; }
    try {
      e.dataTransfer.setData('text/x-block-id', block.id);
      e.dataTransfer.setData('text/plain', block.id);
      e.dataTransfer.effectAllowed = 'move';
    } catch (err) {}
  };
  const handleDragEnd = () => { setDragArmed(false); setDropEdge(null); };
  const handleDragOver = (e) => {
    if (!onReorder) return;
    const sourceId = (e.dataTransfer && e.dataTransfer.types.includes('text/x-block-id')) ? 'block' : null;
    if (!sourceId) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const rect = e.currentTarget.getBoundingClientRect();
    const isTopHalf = (e.clientY - rect.top) < rect.height / 2;
    setDropEdge(isTopHalf ? 'top' : 'bottom');
  };
  const handleDragLeave = () => setDropEdge(null);
  const handleDrop = (e) => {
    if (!onReorder) return;
    e.preventDefault();
    const sourceId = e.dataTransfer.getData('text/x-block-id') || e.dataTransfer.getData('text/plain');
    setDropEdge(null);
    if (!sourceId || sourceId === block.id) return;
    onReorder(sourceId, block.id, dropEdge === 'bottom' ? 'after' : 'before');
  };

  // Read live data published from appState — never the frozen module-level
  // copy (otherwise blocks added from templates with newer product IDs show
  // "no seleccionado").
  const _products = (typeof window !== 'undefined' && window.PRODUCTS) || PRODUCTS || [];
  const _texts = (typeof window !== 'undefined' && window.PREWRITTEN_TEXTS) || PREWRITTEN_TEXTS || [];
  const _brands = (typeof window !== 'undefined' && window.BRANDS) || BRANDS || [];

  const product = block.type === 'product' && _products.find(p => p.id === block.productId);
  const text = block.type === 'text' && _texts.find(t => t.id === block.textId);

  // Derive brand for the top-bar dot
  const typeBrandFromPrefix = block.type && block.type.startsWith('brand_') && block.type !== 'brand_strip'
    ? block.type.replace('brand_', '') : null;
  const pairFirstProd = (block.type === 'product_single' || block.type === 'product_pair' || block.type === 'product_trio') && _products.find(p => p.id === block.product1);
  const brandId = product?.brand || text?.brand || block.brand || typeBrandFromPrefix || pairFirstProd?.brand || 'mix';
  const brand = _brands.find(b => b.id === brandId);

  const typeLabel = {
    text: 'Texto',
    product: 'Producto',
    product_single: 'Producto',
    product_pair: '2 Productos',
    product_trio: '3 Productos',
    // Unified hero: pimpam_hero / product_hero / hero all surface as "Hero"
    hero: 'Hero',
    pimpam_hero: 'Hero',
    product_hero: 'Hero',
    pimpam_steps: '4 Pasos',
    brand_strip: 'Strip de marca',
    brand_artisjet: 'Strip artisJet',
    brand_mbo: 'Strip MBO',
    brand_pimpam: 'Strip PimPam',
    brand_flux: 'Strip FLUX',
    freebird: 'Vídeo YouTube',
    video: 'Vídeo',
    brandstrip: 'Strip multi-marca',
    header: 'Cabecera',
    footer: 'Pie',
    composed: 'Bloque compuesto',
    section: 'Sección · ' + ((block.columns && block.columns.length) || 2) + ' columnas',
    divider: 'Divisor · ' + (block.style === 'short' ? 'línea corta' : block.style === 'dots' ? 'puntos' : 'línea'),
    // Etiquetas para tipos legacy literales (datos no migrados todavía).
    divider_line: 'Divisor · línea',
    divider_short: 'Divisor · línea corta',
    divider_dots: 'Divisor · puntos',
  }[block.type] || block.type;

  // Treat the three legacy hero variants as a single "Hero" concept for
  // rendering — same fields, same editor, same email markup.
  const isHero = block.type === 'pimpam_hero' || block.type === 'product_hero' || block.type === 'hero';

  // Look up a composed block (for type='composed') — prefer live appState data via window.*
  const composedSource = (typeof window !== 'undefined' && window.COMPOSED_BLOCKS) || (typeof COMPOSED_BLOCKS !== 'undefined' ? COMPOSED_BLOCKS : []);
  const composed = block.type === 'composed' && block.composedId
    ? composedSource.find(c => c.id === block.composedId)
    : null;

  // Look up standalone source (for hero/steps/video) — prefer live data.
  // Accept either `_sourceId` (older blocks) or `standaloneId` (set by
  // addBlock when picked from the sidebar) so the hero variants from
  // Supabase resolve correctly.
  const standaloneSource = (typeof window !== 'undefined' && window.STANDALONE_BLOCKS) || (typeof STANDALONE_BLOCKS !== 'undefined' ? STANDALONE_BLOCKS : []);
  const sbLookupId = block._sourceId || block.standaloneId;
  const sbSource = sbLookupId
    ? standaloneSource.find(s => s.id === sbLookupId)
    : null;

  // ── Section block — multi-column layout container ──
  // Renders columns side-by-side. Each column has its own list of blocks
  // (rendered with isInner=true so they don't get the up/down arrows that
  // wouldn't make sense inside a column for v1) and a "+ Añadir" button
  // that opens the command palette targeting that column.
  if (block.type === 'section' && Array.isArray(block.columns)) {
    const cols = block.columns;
    return (
      <div
        className={'block section-block' + (selected ? ' selected' : '')}
        draggable={dragArmed}
        onClick={() => onSelect(block.id)}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <div className="block-bar">
          <span
            className="block-handle"
            title="Arrastrar para reordenar"
            onMouseDown={armDrag}
            onMouseUp={disarmDrag}
            style={{cursor: dragArmed ? 'grabbing' : 'grab'}}
          >
            <Icon name="drag" size={14} />
          </span>
          <span className="block-tag">
            <Icon name="grid" size={12} />
            {typeLabel}
          </span>
          <div className="block-bar-actions">
            {!isInner && onMove && (
              <>
                <button className="block-action" disabled={idx === 0} onClick={e => { e.stopPropagation(); onMove(block.id, -1); }} title="Subir">
                  <Icon name="arrowUp" size={13} />
                </button>
                <button className="block-action" disabled={idx === total - 1} onClick={e => { e.stopPropagation(); onMove(block.id, 1); }} title="Bajar">
                  <Icon name="arrowDown" size={13} />
                </button>
              </>
            )}
            <button className="block-action" onClick={e => { e.stopPropagation(); onDuplicate(block.id); }} title="Duplicar">
              <Icon name="copy" size={13} />
            </button>
            <button className="block-action danger" onClick={e => { e.stopPropagation(); onDelete(block.id); }} title="Eliminar">
              <Icon name="trash" size={13} />
            </button>
          </div>
        </div>

        <div className="section-cols" style={{display:'grid', gridTemplateColumns:`repeat(${cols.length}, 1fr)`, gap:12, padding:12, background:'var(--bg-sunken)'}}>
          {cols.map((col, ci) => (
            <div key={ci} className="section-col" style={{display:'flex', flexDirection:'column', gap:8, minWidth:0}}>
              {(col.blocks || []).map((ib, ii) => (
                <BlockCard
                  key={ib.id}
                  block={ib}
                  idx={ii}
                  total={(col.blocks || []).length}
                  selected={selectedId === ib.id}
                  selectedId={selectedId}
                  onSelect={onSelect}
                  onUpdate={onUpdate}
                  onDelete={onDelete}
                  onDuplicate={onDuplicate}
                  onMove={onMove}
                  onUngroup={onUngroup}
                  onOpenInnerPalette={onOpenInnerPalette}
                  onPickColumnAdd={onPickColumnAdd}
                  appState={appState}
                  lang={lang}
                  isInner
                />
              ))}
              <ColumnAddPicker
                onPick={spec => onPickColumnAdd && onPickColumnAdd(block.id, ci, spec)}
                columnLabel={ci + 1}
                appState={appState}
              />
            </div>
          ))}
        </div>
      </div>
    );
  }

  // Estilo de ancho + alineación del bloque a nivel canvas. No aplica
  // a inner blocks dentro de una sección (ya están limitados a su columna).
  const _widthPct = (!isInner && typeof block.widthPct === 'number' && block.widthPct >= 30 && block.widthPct < 100) ? block.widthPct : null;
  const _blockAlign = block.blockAlign || 'center';
  const blockSizeStyle = _widthPct ? {
    maxWidth: _widthPct + '%',
    width: _widthPct + '%',
    marginLeft: _blockAlign === 'left' ? 0 : 'auto',
    marginRight: _blockAlign === 'right' ? 0 : 'auto',
  } : {};
  return (
    <div
      className={'block' + (selected ? ' selected' : '') + (dropEdge ? ' drop-' + dropEdge : '')}
      style={blockSizeStyle}
      draggable={dragArmed}
      onClick={() => onSelect(block.id)}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {selected && <span className="block-edit-hint">Editando</span>}
      <div className="block-bar">
        <span
          className="block-handle"
          title="Arrastrar para reordenar"
          onMouseDown={armDrag}
          onMouseUp={disarmDrag}
          style={{cursor: dragArmed ? 'grabbing' : 'grab'}}
        >
          <Icon name="drag" size={14} />
        </span>
        <span className="block-tag">
          <span className="dot" style={{ background: brand?.color || 'var(--text-subtle)' }} />
          {typeLabel}
        </span>
        {product && (
          <span style={{fontSize:11, color:'var(--text-muted)', fontFamily:'var(--font-mono)'}}>
            {product.name}
          </span>
        )}
        {text && (
          <span style={{fontSize:11, color:'var(--text-muted)'}}>
            {text.name}
          </span>
        )}
        <div className="block-bar-actions">
          <button className="block-action edit-btn" onClick={e => { e.stopPropagation(); onSelect(block.id); }} title="Editar bloque" style={{
            paddingLeft: 8, paddingRight: 8, width: 'auto',
            background: selected ? 'var(--accent)' : 'transparent',
            color: selected ? 'white' : 'var(--text-muted)',
            fontSize: 11, fontWeight: 500, gap: 4, display:'inline-flex', alignItems:'center',
          }}>
            <Icon name="settings" size={12} />
            {selected ? 'Editando' : 'Editar'}
          </button>
          {onMove && (
            <>
              <button
                className="block-action"
                disabled={idx === 0}
                onClick={e => { e.stopPropagation(); onMove(block.id, -1); }}
                title={isInner ? 'Subir dentro de la columna' : 'Subir'}
              >
                <Icon name="arrowUp" size={13} />
              </button>
              <button
                className="block-action"
                disabled={idx === total - 1}
                onClick={e => { e.stopPropagation(); onMove(block.id, 1); }}
                title={isInner ? 'Bajar dentro de la columna' : 'Bajar'}
              >
                <Icon name="arrowDown" size={13} />
              </button>
            </>
          )}
          {block.type === 'composed' && onUngroup && (
            <button
              className="block-action"
              onClick={e => { e.stopPropagation(); onUngroup(block.id); }}
              title="Desagrupar — convierte el bloque compuesto en sus piezas individuales editables (intro, brand strip, productos, hero/steps)"
              style={{paddingLeft:8, paddingRight:8, width:'auto', fontSize:11, fontWeight:600, gap:4, display:'inline-flex', alignItems:'center'}}
            >
              <Icon name="layers" size={12} /> Desagrupar
            </button>
          )}
          <button className="block-action" onClick={e => { e.stopPropagation(); onDuplicate(block.id); }} title="Duplicar">
            <Icon name="copy" size={13} />
          </button>
          <button className="block-action danger" onClick={e => { e.stopPropagation(); onDelete(block.id); }} title="Eliminar">
            <Icon name="trash" size={13} />
          </button>
        </div>
      </div>

      <div className="block-body">
        {/* Reconocer también divider_line/short/dots legacy: la migración al
            cargar normaliza estos a type:'divider', pero hacemos render
            defensivo por si algún dato vivo escapa (ej. AI agent que lo
            crea sin pasar por createBlock, o sincronización desde otro
            cliente con código viejo). */}
        {(block.type === 'divider' || block.type === 'divider_line' || block.type === 'divider_short' || block.type === 'divider_dots') && (() => {
          const style = block.style
            || (block.type === 'divider_short' ? 'short'
              : block.type === 'divider_dots' ? 'dots'
              : 'line');
          const color = block.color || '#e2e8f0';
          const padV = (typeof block.paddingV === 'number') ? block.paddingV : 24;
          if (style === 'dots') return (
            <div style={{padding: padV + 'px 20px', textAlign:'center', letterSpacing:8, fontSize:18, color, lineHeight:1, fontFamily:'Helvetica,Arial,sans-serif'}}>
              ·&nbsp;·&nbsp;·
            </div>
          );
          if (style === 'short') return (
            <div style={{padding: padV + 'px 20px', textAlign:'center'}}>
              <div style={{display:'inline-block', width:80, height:2, background:color, borderRadius:1}} />
            </div>
          );
          return (
            <div style={{padding: padV + 'px 20px'}}>
              <div style={{height:1, background:color}} />
            </div>
          );
        })()}
        {block.type === 'image' && (
          <div style={{padding:12, textAlign: block.align || 'center'}}>
            {block.src ? (
              <img src={block.src} alt={block.alt || ''} style={{maxWidth:'100%', maxHeight:200, borderRadius:6, display:'inline-block'}} />
            ) : (
              <div style={{padding:'30px 20px', background:'var(--bg-sunken)', border:'1px dashed var(--border-strong)', borderRadius:6, color:'var(--text-muted)', fontSize:12}}>
                Imagen sin URL — ábrelo en Editar para elegir una de la biblioteca o subir una nueva
              </div>
            )}
          </div>
        )}
        {block.type === 'cta' && (() => {
          const bullets = Array.isArray(block.bullets) ? block.bullets.filter(x => x && String(x).trim()) : [];
          const hasPanel = (block.panelBg && block.panelBg !== 'transparent') || (block.panelBorder && block.panelBorder !== 'transparent');
          return (
            <div style={{padding:12}}>
              <div style={{textAlign: block.align || 'center', background: hasPanel ? (block.panelBg || 'transparent') : 'transparent', border: hasPanel ? '1px solid ' + (block.panelBorder || 'transparent') : 'none', borderRadius: hasPanel ? 8 : 0, padding: hasPanel ? '16px 18px' : 0}}>
                {block.title && <div style={{fontSize:16, fontWeight:700, color:'#1a1918', marginBottom:6, lineHeight:1.3}}>{block.title}</div>}
                {block.subtitle && <div style={{fontSize:13, color:'#475569', marginBottom:10, lineHeight:1.5}}>{block.subtitle}</div>}
                {bullets.length > 0 && (
                  <ul style={{margin:'0 0 14px', padding:'0 0 0 18px', fontSize:13, color:'#334155', lineHeight:1.55, textAlign:'left', display:'inline-block'}}>
                    {bullets.map((b, i) => <li key={i} style={{margin:'0 0 4px'}}>{b}</li>)}
                  </ul>
                )}
                <div>
                  <a href={block.url || '#'} target="_blank" rel="noopener noreferrer"
                    onClick={e => e.preventDefault()}
                    style={{display:'inline-block', padding:'10px 22px', fontSize:13, fontWeight:600, color: block.color || '#fff', background: block.bg || '#1d4ed8', borderRadius:6, textDecoration:'none'}}
                  >
                    {block.text || 'Más información'}
                  </a>
                </div>
                {!block.url && (
                  <div style={{marginTop:6, fontSize:11, color:'var(--text-muted)'}}>Sin URL — añádela en Editar</div>
                )}
              </div>
            </div>
          );
        })()}
        {block.type === 'text' && (
          <InlineTextBlock
            block={block}
            text={text}
            selected={selected}
            lang={lang}
            onUpdate={onUpdate}
          />
        )}

        {block.type === 'product' && product && (() => {
          const ov = block.overrides?.[lang] || {};
          const showPrice = block.showPrice !== false;
          const showSpecs = block.showSpecs !== false;
          return (
          <div className="prod-card">
            <img src={ov.img ?? product.img} alt={ov.name ?? product.name} className="prod-img" />
            <div className="prod-info">
              <div className="prod-name">{ov.name ?? product.name}</div>
              <div className="prod-desc">{ov.desc ?? product.desc}</div>
              {showSpecs && (
                <div className="prod-meta">
                  <span className="prod-meta-item">{ov.area ?? product.area}</span>
                  <span className="prod-meta-item">{ov.feat1 ?? product.feat1}</span>
                  <span className="prod-meta-item">{ov.feat2 ?? product.feat2}</span>
                </div>
              )}
              {showPrice && (
                <div className="prod-select">
                  <span>Precio</span>
                  <span className="prod-price">{ov.price ?? product.price}</span>
                </div>
              )}
              {block.showCta && (
                <div style={{marginTop:8}}>
                  <span style={{display:'inline-block', padding:'6px 12px', background:'var(--bg-inverse)', color:'var(--bg)', borderRadius:'var(--r-sm)', fontSize:12, fontWeight:600}}>
                    {block.ctaText || 'Más información'}
                  </span>
                </div>
              )}
            </div>
          </div>
          );
        })()}

        {block.type === 'brandstrip' && (
          <div className="brand-strip-preview">
            {BRANDS.filter(b => b.id !== 'bomedia').map(b => (
              <span key={b.id} style={{ color: b.color, fontWeight: 600 }}>{b.logoText}</span>
            ))}
          </div>
        )}

        {(block.type === 'brand_strip'
          || block.type === 'brand_artisjet' || block.type === 'brand_mbo'
          || block.type === 'brand_pimpam' || block.type === 'brand_flux') && (
          <BrandStripPreview
            brandId={block.brand || (block.type.startsWith('brand_') && block.type !== 'brand_strip' ? block.type.replace('brand_','') : 'artisjet')}
            lang={lang}
          />
        )}

        {block.type === 'product_single' && (() => {
          const p = _products.find(x => x.id === block.product1);
          return (
            <div style={{maxWidth:320, margin:'0 auto'}}>
              <MiniProduct p={p} lang={lang} />
            </div>
          );
        })()}

        {block.type === 'product_pair' && (() => {
          const p1 = _products.find(x => x.id === block.product1);
          const p2 = _products.find(x => x.id === block.product2);
          return (
            <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:8, alignItems:'start'}}>
              <MiniProduct p={p1} lang={lang} />
              <MiniProduct p={p2} lang={lang} />
            </div>
          );
        })()}

        {block.type === 'product_trio' && (() => {
          const p1 = _products.find(x => x.id === block.product1);
          const p2 = _products.find(x => x.id === block.product2);
          const p3 = _products.find(x => x.id === block.product3);
          return (
            <div style={{display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:6, alignItems:'start'}}>
              <MiniProduct p={p1} lang={lang} compact />
              <MiniProduct p={p2} lang={lang} compact />
              <MiniProduct p={p3} lang={lang} compact />
            </div>
          );
        })()}

        {isHero && (() => {
          const cfg = Object.assign({}, sbSource?.config || {}, block);
          const ovr = (block._overrides && block._overrides[lang]) || {};
          const hi = cfg.i18n && cfg.i18n[lang] ? cfg.i18n[lang] : null;
          const title = ovr.heroTitle || (hi && hi.heroTitle) || cfg.heroTitle || 'Personaliza, imprime y vende';
          const subtitle = ovr.heroSubtitle || (hi && hi.heroSubtitle) || cfg.heroSubtitle || '';
          const bullets = ovr.heroBullets || (hi && hi.heroBullets) || cfg.heroBullets || [];
          const img = cfg.heroImage;
          const bg = cfg.heroBgColor || '#fff';
          let ctas = cfg.heroCtaButtons || [];
          if (!ctas.length && cfg.heroCtaText && cfg.heroCtaUrl) ctas = [{text:cfg.heroCtaText, url:cfg.heroCtaUrl}];
          return (
            <div style={{display:'flex', gap:14, padding:14, background:bg, borderRadius:'var(--r-md)', border:'1px solid var(--border)'}}>
              {img && (
                <div style={{flexShrink:0, width:120, height:120, borderRadius:'var(--r-sm)', overflow:'hidden'}}>
                  <img src={img} alt="" style={{width:'100%', height:'100%', objectFit:'cover'}} />
                </div>
              )}
              <div style={{flex:1, minWidth:0}}>
                <div style={{fontWeight:800, fontSize:14, color:'#0f172a'}}>{title}</div>
                {subtitle && <div style={{fontSize:12, color:'var(--text-muted)', margin:'4px 0 6px', lineHeight:1.5}}>{subtitle}</div>}
                {bullets.length > 0 && (
                  <ul style={{margin:'4px 0 0', padding:0, listStyle:'none'}}>
                    {bullets.map((b, i) => (
                      <li key={i} style={{fontSize:11, color:'var(--text-muted)', margin:'2px 0'}}>✓ {b}</li>
                    ))}
                  </ul>
                )}
                {ctas.length > 0 && (
                  <div style={{display:'flex', gap:6, marginTop:8, flexWrap:'wrap'}}>
                    {ctas.map((c, i) => c.text && (
                      <span key={i} style={{
                        display:'inline-block', padding:'5px 10px', borderRadius:6,
                        background: c.bg || '#ea580c', color: c.color || '#fff',
                        fontSize:11, fontWeight:700,
                      }}>{c.text}</span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        })()}

        {block.type === 'pimpam_steps' && (() => {
          const cfg = Object.assign({}, sbSource?.config || {}, block);
          const steps = cfg.steps || [];
          const bg = cfg.stepsBgColor || '#fff7ed';
          const border = cfg.stepsBorderColor || '#fed7aa';
          if (!steps.length) {
            return <div style={{padding:12, fontSize:12, color:'var(--text-subtle)'}}>Sin pasos configurados</div>;
          }
          return (
            <div style={{display:'grid', gridTemplateColumns:`repeat(${steps.length}, 1fr)`, gap:6}}>
              {steps.map((s, i) => (
                <div key={i} style={{background:bg, border:`1px solid ${border}`, borderRadius:'var(--r-sm)', padding:10, textAlign:'center'}}>
                  <div style={{fontSize:20, marginBottom:4}}>{s.n}</div>
                  <div style={{fontWeight:800, fontSize:11, color:'#0f172a'}}>{s.t}</div>
                  <div style={{fontSize:10, color:'var(--text-muted)', marginTop:2}}>{s.s}</div>
                </div>
              ))}
            </div>
          );
        })()}

        {(block.type === 'freebird' || block.type === 'video') && (() => {
          const cfg = Object.assign({}, sbSource?.config || {}, block);
          const yt = cfg.youtubeUrl || 'https://www.youtube.com/watch?v=gp-x_jRBRcE';
          const m = yt.match(/(?:v=|youtu\.be\/)([^&\n?#]+)/);
          const thumb = cfg.thumbnailOverride || (m ? `https://img.youtube.com/vi/${m[1]}/hqdefault.jpg` : '');
          const videoLabel = lang==='fr'?'Voir la vidéo':lang==='de'?'Video ansehen':lang==='en'?'Watch video':lang==='nl'?'Video bekijken':'Ver vídeo';
          return (
            <div style={{borderRadius:'var(--r-md)', overflow:'hidden', background:'#0f172a'}}>
              {thumb && <img src={thumb} alt="" style={{display:'block', width:'100%', opacity:0.85}} />}
              <div style={{padding:'10px 14px', color:'#93c5fd', fontSize:13, fontWeight:700, textAlign:'center'}}>
                ▶ {videoLabel}
              </div>
            </div>
          );
        })()}

        {block.type === 'composed' && (() => {
          const cb = composed;
          if (!cb) return <div style={{padding:12, fontSize:12, color:'var(--text-subtle)'}}>Bloque compuesto no encontrado</div>;

          // v5: si compositorBlocks existe lo usamos como fuente de verdad;
          // si no, caemos al schema legacy (introText + brandStrip + products).
          const compChildren = Array.isArray(cb.compositorBlocks) ? cb.compositorBlocks : null;
          const introTextVal = (() => {
            if (compChildren) {
              const t = compChildren.find(c => c && c.type === 'text');
              if (!t) return '';
              if (t.overridesByLang && t.overridesByLang[lang] != null) return t.overridesByLang[lang];
              if (t.i18n && t.i18n[lang] && t.i18n[lang].text) return t.i18n[lang].text;
              return (t.overridesByLang && t.overridesByLang.es) || t.text || '';
            }
            return (cb.i18n && cb.i18n[lang] && cb.i18n[lang].introText) || cb.introText || '';
          })();
          const brandStripVal = compChildren
            ? ((compChildren.find(c => c && c.type === 'brand_strip') || {}).brand || null)
            : (cb.brandStrip && cb.brandStrip !== 'none' ? cb.brandStrip : null);
          const prodIds = compChildren
            ? compChildren.flatMap(c => [c.product1, c.product2, c.product3]).filter(Boolean)
            : (cb.products || []);
          const prods = prodIds.map(pid => _products.find(p => p.id === pid)).filter(Boolean);
          const summary = compChildren
            ? compChildren.length + ' bloque' + (compChildren.length === 1 ? '' : 's')
            : (cb.blockType || 'composed');
          return (
            <div style={{padding:12, border:'1px dashed var(--border-strong)', borderRadius:'var(--r-md)'}}>
              <div style={{fontSize:10, textTransform:'uppercase', letterSpacing:1, color:'var(--text-subtle)', fontFamily:'var(--font-mono)'}}>
                Compuesto · {summary}
              </div>
              <div style={{fontWeight:700, fontSize:13, marginTop:4}}>{cb.title}</div>
              {introTextVal && (
                <p style={{fontSize:12, color:'var(--text-muted)', margin:'6px 0 0', lineHeight:1.5}}>
                  {introTextVal.length > 160 ? introTextVal.slice(0,160) + '…' : introTextVal}
                </p>
              )}
              {brandStripVal && (
                <div style={{fontSize:11, color:'var(--text-subtle)', marginTop:6}}>→ Strip {brandStripVal}</div>
              )}
              {prods.length > 0 && (
                <div style={{display:'flex', gap:6, marginTop:8, flexWrap:'wrap'}}>
                  {prods.map(p => (
                    <div key={p.id} style={{display:'flex', alignItems:'center', gap:6, background:'var(--bg-sunken)', padding:'3px 8px', borderRadius:4, fontSize:11}}>
                      <img src={p.img} alt="" style={{width:18, height:18, objectFit:'contain'}} />
                      <span>{p.name}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })()}

        {block.type === 'header' && (
          <div style={{padding:'14px 16px', background:'var(--bg-inverse)', color:'var(--bg)', borderRadius:'var(--r-sm)', display:'flex', justifyContent:'space-between', alignItems:'center'}}>
            <strong style={{fontSize:14}}>bomedia</strong>
            <span style={{fontSize:11, opacity:.7, fontFamily:'var(--font-mono)'}}>cabecera corporativa</span>
          </div>
        )}

        {block.type === 'footer' && (
          <div style={{padding:'12px 16px', background:'var(--bg-sunken)', borderRadius:'var(--r-sm)', fontSize:11, color:'var(--text-muted)', textAlign:'center'}}>
            Bomedia S.L. · Aviso legal · Política de privacidad · Darse de baja
          </div>
        )}
      </div>
    </div>
  );
}

/* Restricted picker shown inside section columns. Only offers single-cell
   block types that look right in a narrow column: text, single product,
   image, video, CTA. The full command palette (with heroes, brand strips,
   pairs, trios, etc.) is hidden because those don't fit a column. */
function ColumnAddPicker({ onPick, columnLabel, appState }) {
  const [open, setOpen] = React.useState(false);
  const [mode, setMode] = React.useState(null); // null | 'product' | 'image' | 'text' | 'video' | 'cta'
  const wrapRef = React.useRef(null);

  // Click-outside to close
  React.useEffect(() => {
    if (!open) return;
    const onDoc = (e) => { if (wrapRef.current && !wrapRef.current.contains(e.target)) { setOpen(false); setMode(null); } };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const close = () => { setOpen(false); setMode(null); };
  const pick = (spec) => { onPick(spec); close(); };

  const products = (appState && appState.products) || (typeof window !== 'undefined' && window.PRODUCTS) || [];
  const prewrittenTexts = (appState && appState.prewrittenTexts) || (typeof window !== 'undefined' && window.PREWRITTEN_TEXTS) || [];
  const standaloneBlocks = (appState && appState.standaloneBlocks) || (typeof window !== 'undefined' && window.STANDALONE_BLOCKS) || [];
  const ctaBlocks = (appState && appState.ctaBlocks) || [];
  const videoStandalones = standaloneBlocks.filter(s => (s.blockType === 'video' || s.blockType === 'freebird' || s.type === 'video' || s.type === 'freebird') && s.visible !== false);

  const popoverStyle = { position:'absolute', top:'100%', left:0, marginTop:4, background:'var(--bg-panel)', border:'1px solid var(--border-strong)', borderRadius:'var(--r-sm)', boxShadow:'0 10px 30px rgba(0,0,0,0.18), var(--sh-md)', zIndex:50, padding:6, maxHeight:320, overflowY:'auto' };

  return (
    <div ref={wrapRef} onClick={e => e.stopPropagation()} style={{position:'relative'}}>
      <button
        className="btn btn-ghost"
        style={{fontSize:11, justifyContent:'center', border:'1px dashed var(--border-strong)', background:'var(--bg-panel)', width:'100%'}}
        onClick={e => { e.stopPropagation(); setOpen(o => !o); setMode(null); }}
        title={'Añadir un bloque a la columna ' + columnLabel}
      >
        <Icon name="plus" size={11} /> Añadir a columna {columnLabel}
      </button>

      {open && mode == null && (
        <div style={Object.assign({}, popoverStyle, { minWidth:220, display:'flex', flexDirection:'column', gap:2 })}>
          <button className="btn btn-ghost" style={{fontSize:12, justifyContent:'flex-start', padding:'8px 10px', gap:8}} onClick={() => setMode('text')}>
            <Icon name="text" size={14}/> Texto
          </button>
          <button className="btn btn-ghost" style={{fontSize:12, justifyContent:'flex-start', padding:'8px 10px', gap:8}} onClick={() => setMode('product')}>
            <Icon name="box" size={14}/> Producto
          </button>
          <button className="btn btn-ghost" style={{fontSize:12, justifyContent:'flex-start', padding:'8px 10px', gap:8}} onClick={() => setMode('image')}>
            <Icon name="copy" size={14}/> Imagen
          </button>
          <button className="btn btn-ghost" style={{fontSize:12, justifyContent:'flex-start', padding:'8px 10px', gap:8}} onClick={() => setMode('video')}>
            <Icon name="layers" size={14}/> Vídeo
          </button>
          <button className="btn btn-ghost" style={{fontSize:12, justifyContent:'flex-start', padding:'8px 10px', gap:8}} onClick={() => setMode('cta')}>
            <Icon name="zap" size={14}/> Botón CTA
          </button>
          <div style={{height:1, background:'var(--border)', margin:'4px 0'}}/>
          <button className="btn btn-ghost" style={{fontSize:12, justifyContent:'flex-start', padding:'8px 10px', gap:8}} onClick={() => pick({ type:'divider_line' })}>
            <span style={{width:14, height:1, background:'currentColor', display:'inline-block'}}/> Línea fina
          </button>
          <button className="btn btn-ghost" style={{fontSize:12, justifyContent:'flex-start', padding:'8px 10px', gap:8}} onClick={() => pick({ type:'divider_short' })}>
            <span style={{width:8, height:2, background:'currentColor', borderRadius:1, display:'inline-block'}}/> Línea corta
          </button>
          <button className="btn btn-ghost" style={{fontSize:12, justifyContent:'flex-start', padding:'8px 10px', gap:8}} onClick={() => pick({ type:'divider_dots' })}>
            <span style={{letterSpacing:2, fontSize:14}}>···</span> Puntos
          </button>
        </div>
      )}

      {open && mode === 'product' && (
        <div style={Object.assign({}, popoverStyle, { minWidth:260 })}>
          <div style={{padding:'4px 6px', fontSize:10, color:'var(--text-muted)', fontWeight:600, textTransform:'uppercase'}}>Elige producto</div>
          {products.filter(p => p.visible !== false).map(p => (
            <button key={p.id} className="btn btn-ghost" style={{fontSize:11, justifyContent:'flex-start', width:'100%', padding:'6px 8px'}}
              onClick={() => pick({ type:'product', productId: p.id })}
            >
              <img src={p.img} alt="" style={{width:24, height:24, objectFit:'contain', marginRight:6}}/>
              <span style={{flex:1, textAlign:'left', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>{p.name}</span>
              <span className="mono" style={{fontSize:10, color:'var(--text-muted)'}}>{p.brand}</span>
            </button>
          ))}
        </div>
      )}

      {open && mode === 'text' && (
        <div style={Object.assign({}, popoverStyle, { minWidth:280 })}>
          <div style={{padding:'4px 6px', fontSize:10, color:'var(--text-muted)', fontWeight:600, textTransform:'uppercase'}}>Texto</div>
          <button className="btn btn-ghost" style={{fontSize:11, justifyContent:'flex-start', width:'100%', padding:'6px 8px', borderBottom:'1px solid var(--border)'}}
            onClick={() => pick({ type:'text-blank' })}>
            <Icon name="plus" size={11} style={{marginRight:6}}/> <strong>Texto en blanco</strong>
          </button>
          <div style={{padding:'4px 6px', fontSize:10, color:'var(--text-muted)', fontWeight:600, textTransform:'uppercase', marginTop:4}}>Pre-escritos</div>
          {prewrittenTexts.filter(t => t.visible !== false).map(t => (
            <button key={t.id} className="btn btn-ghost" style={{fontSize:11, justifyContent:'flex-start', width:'100%', padding:'6px 8px'}}
              onClick={() => pick({ type:'text', textId: t.id })}>
              <span style={{marginRight:6, fontSize:14}}>{t.icon || '📝'}</span>
              <span style={{flex:1, textAlign:'left', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>{t.name}</span>
            </button>
          ))}
        </div>
      )}

      {open && mode === 'video' && (
        <div style={Object.assign({}, popoverStyle, { minWidth:280 })}>
          <div style={{padding:'4px 6px', fontSize:10, color:'var(--text-muted)', fontWeight:600, textTransform:'uppercase'}}>Vídeo</div>
          <button className="btn btn-ghost" style={{fontSize:11, justifyContent:'flex-start', width:'100%', padding:'6px 8px', borderBottom:'1px solid var(--border)'}}
            onClick={() => pick({ type:'video' })}>
            <Icon name="plus" size={11} style={{marginRight:6}}/> <strong>Vídeo en blanco</strong>
          </button>
          <div style={{padding:'4px 6px', fontSize:10, color:'var(--text-muted)', fontWeight:600, textTransform:'uppercase', marginTop:4}}>Vídeos guardados</div>
          {videoStandalones.length === 0 && (
            <div style={{padding:'8px', fontSize:10, color:'var(--text-muted)', fontStyle:'italic'}}>Aún no hay vídeos guardados — créalos en Backoffice → Bloques sueltos.</div>
          )}
          {videoStandalones.map(s => (
            <button key={s.id} className="btn btn-ghost" style={{fontSize:11, justifyContent:'flex-start', width:'100%', padding:'6px 8px'}}
              onClick={() => pick({ type:'video', standaloneId: s.id })}>
              <span style={{marginRight:6, fontSize:14}}>{s.icon || '▶'}</span>
              <span style={{flex:1, textAlign:'left', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>{s.title}</span>
            </button>
          ))}
        </div>
      )}

      {open && mode === 'cta' && (
        <div style={Object.assign({}, popoverStyle, { minWidth:280 })}>
          <div style={{padding:'4px 6px', fontSize:10, color:'var(--text-muted)', fontWeight:600, textTransform:'uppercase'}}>CTA</div>
          <button className="btn btn-ghost" style={{fontSize:11, justifyContent:'flex-start', width:'100%', padding:'6px 8px', borderBottom:'1px solid var(--border)'}}
            onClick={() => pick({ type:'cta' })}>
            <Icon name="plus" size={11} style={{marginRight:6}}/> <strong>CTA en blanco</strong>
          </button>
          <div style={{padding:'4px 6px', fontSize:10, color:'var(--text-muted)', fontWeight:600, textTransform:'uppercase', marginTop:4}}>CTAs guardados</div>
          {ctaBlocks.filter(c => c.visible !== false).length === 0 && (
            <div style={{padding:'8px', fontSize:10, color:'var(--text-muted)', fontStyle:'italic'}}>Aún no hay CTAs guardados — créalos en Backoffice → CTAs.</div>
          )}
          {ctaBlocks.filter(c => c.visible !== false).map(c => (
            <button key={c.id} className="btn btn-ghost" style={{fontSize:11, justifyContent:'flex-start', width:'100%', padding:'6px 8px'}}
              onClick={() => pick({ type:'cta', _ctaSourceId: c.id })}>
              <Icon name="zap" size={12} style={{marginRight:6}}/>
              <span style={{flex:1, textAlign:'left', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>{c.name || c.title || c.text}</span>
            </button>
          ))}
        </div>
      )}

      {open && mode === 'image' && typeof window.ImageLibraryModal === 'function' && (
        <window.ImageLibraryModal
          appState={appState}
          setAppState={(...args) => { if (typeof window.__setAppState === 'function') window.__setAppState(...args); }}
          onPick={(url) => pick({ type:'image', _imgUrl: url })}
          onClose={close}
        />
      )}
    </div>
  );
}

function Canvas({ blocks, onUpdate, onDelete, onMove, onReorder, onDuplicate, onUngroup, selectedId, setSelectedId, onOpenPalette, onOpenInnerPalette, onAddBlock, onAddBlockToColumn, onClearBlocks, onExpandPreview, editingTemplate, onExitTemplateEdit, onSaveCurrentTemplate, onSaveAsTemplate, lang, variant, emailHtml, onUndo, onRedo, appState, onSetBlocks, onSetLang, emailTitle, onEmailTitleChange }) {
  // AI Agent modal state — opens when user clicks the "✨ IA" button.
  const [agentOpen, setAgentOpen] = React.useState(false);
  const liveTemplates = (typeof window !== 'undefined' && window.TEMPLATES) || TEMPLATES || [];
  const visibleTemplates = liveTemplates.filter(t => t.visible !== false).slice(0, 6);
  const [toast, setToast] = React.useState('');
  const [htmlMenu, setHtmlMenu] = React.useState(false);
  const [saveTplModal, setSaveTplModal] = React.useState(false);

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(''), 2400);
  };

  // Helper compartido para los logs de export — evita repetir el mismo
  // payload (título del email + nº de bloques + idioma) en cada handler.
  const _logExport = (action) => {
    if (typeof window.logActivity === 'function') {
      window.logActivity(action, {
        title: emailTitle || '',
        blockCount: blocks.length,
        lang,
      });
    }
  };

  const handleDownloadHtml = () => {
    const blob = new Blob([emailHtml || '<html><body></body></html>'], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'bomedia-email-' + new Date().toISOString().slice(0, 10) + '.html';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setHtmlMenu(false);
    showToast('HTML descargado');
    _logExport('email_html_download');
  };

  // Export as Word .doc — Word opens HTML files saved with .doc extension and
  // converts them. We wrap the email HTML with the Word-friendly XML/MS Office
  // namespace declarations so the document opens cleanly in Word desktop and
  // Word Online (without those, some versions show a "convert" dialog first).
  const handleDownloadDoc = () => {
    const safeTitle = (emailTitle || 'Bomedia email').replace(/[<>&"']/g, '');
    const docHtml =
      '<html xmlns:o="urn:schemas-microsoft-com:office:office" ' +
      'xmlns:w="urn:schemas-microsoft-com:office:word" ' +
      'xmlns="http://www.w3.org/TR/REC-html40">' +
      '<head><meta charset="utf-8"><title>' + safeTitle + '</title>' +
      '<!--[if gte mso 9]><xml><w:WordDocument><w:View>Print</w:View>' +
      '<w:Zoom>90</w:Zoom><w:DoNotOptimizeForBrowser/></w:WordDocument></xml><![endif]-->' +
      '<style>body{font-family:Calibri,Arial,sans-serif;}@page{size:auto;margin:1.5cm;}</style>' +
      '</head><body>' + (emailHtml || '') + '</body></html>';
    const blob = new Blob(['﻿', docHtml], { type: 'application/msword' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'bomedia-email-' + new Date().toISOString().slice(0, 10) + '.doc';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setHtmlMenu(false);
    showToast('Documento Word descargado · ábrelo con Word u Office Online');
    _logExport('email_doc_export');
  };

  // Export as PDF — opens an off-screen iframe with the email HTML and
  // triggers the browser print dialog with "Save as PDF" pre-selected. Works
  // in all modern browsers without extra libraries (no jsPDF/html2canvas
  // dependency, which kept the v4 "no build step" philosophy intact).
  const handleDownloadPdf = () => {
    const safeTitle = (emailTitle || 'Bomedia email').replace(/[<>&"']/g, '');
    const printHtml =
      '<!doctype html><html><head><meta charset="utf-8"><title>' + safeTitle + '</title>' +
      '<style>@page{size:A4;margin:12mm;}body{margin:0;}@media print{body{-webkit-print-color-adjust:exact;print-color-adjust:exact;}}</style>' +
      '</head><body>' + (emailHtml || '') + '</body></html>';
    const iframe = document.createElement('iframe');
    iframe.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden';
    document.body.appendChild(iframe);
    const cleanup = () => {
      try { document.body.removeChild(iframe); } catch (_) {}
    };
    iframe.onload = () => {
      try {
        const win = iframe.contentWindow;
        win.focus();
        // Give layout one tick to settle (especially with images) before printing.
        setTimeout(() => {
          try { win.print(); } catch (_) {}
          // Remove the iframe shortly after — browsers keep it alive while the
          // print dialog is open, so this just frees DOM after the user closes it.
          setTimeout(cleanup, 1500);
        }, 350);
      } catch (e) {
        cleanup();
      }
    };
    // Use srcdoc when supported (modern browsers); fall back to document.write.
    if ('srcdoc' in iframe) {
      iframe.srcdoc = printHtml;
    } else {
      const doc = iframe.contentDocument || iframe.contentWindow?.document;
      if (doc) { doc.open(); doc.write(printHtml); doc.close(); }
    }
    setHtmlMenu(false);
    showToast('Abriendo diálogo de impresión · selecciona "Guardar como PDF"');
    _logExport('email_pdf_export');
  };

  const handleCopyHtml = async () => {
    const r = (typeof copyHtmlAsRich === 'function')
      ? await copyHtmlAsRich(emailHtml || '')
      : { ok: false, mode: null };
    if (r.ok && r.mode === 'rich') showToast('HTML copiado · pégalo en Gmail/Outlook y verás el email renderizado');
    else if (r.ok) showToast('HTML copiado (texto plano)');
    else showToast('No se pudo copiar (revisa permisos)');
    setHtmlMenu(false);
    if (r && r.ok) _logExport('email_copy');
  };

  const handleClear = () => {
    if (blocks.length === 0) return;
    const ok = window.confirm('¿Vaciar el lienzo? Se eliminarán los ' + blocks.length + ' bloques actuales.');
    if (ok && onClearBlocks) onClearBlocks();
  };

  // Close HTML menu on outside click
  React.useEffect(() => {
    if (!htmlMenu) return;
    const close = () => setHtmlMenu(false);
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, [htmlMenu]);

  const inTplMode = !!editingTemplate;

  const handleSaveTpl = () => {
    if (onSaveCurrentTemplate) {
      onSaveCurrentTemplate();
      showToast('Plantilla "' + editingTemplate.name + '" actualizada');
    }
  };

  const handleSaveAsNewTpl = (name, brand, colorClass) => {
    const tpl = onSaveAsTemplate && onSaveAsTemplate(name, { brand, colorClass });
    setSaveTplModal(false);
    if (tpl) showToast('Plantilla "' + tpl.name + '" creada');
  };

  return (
    <main className="canvas scroll">
      <div className="canvas-inner">
        {inTplMode && (
          <div className="tpl-mode-banner">
            <Icon name="template" size={13} />
            <span>Editando plantilla: <strong>{editingTemplate.name}</strong></span>
            <span className="mono" style={{fontSize:10, opacity:0.7, marginLeft:4}}>· {blocks.length} bloques</span>
            <button className="btn btn-primary" style={{fontSize:11, padding:'4px 10px', marginLeft:'auto'}} onClick={handleSaveTpl}>
              <Icon name="zap" size={11}/> Guardar plantilla
            </button>
            <button className="btn btn-ghost" style={{fontSize:11, padding:'4px 10px'}} onClick={onExitTemplateEdit}>
              Salir sin guardar
            </button>
          </div>
        )}
        <div className="canvas-header">
          <div>
            <EditableEmailTitle
              value={inTplMode ? editingTemplate.name : (emailTitle || '')}
              onChange={onEmailTitleChange}
              variant={variant}
              readOnly={inTplMode}
              placeholder="Email sin título"
            />
            <div className="canvas-meta" style={{marginTop:6}}>
              <span className="sync-ok">●</span>
              <span>{inTplMode ? 'Plantilla' : 'Sincronizado'}</span>
              <span className="dot" />
              <span>{blocks.length} bloques</span>
              <span className="dot" />
              <span>{lang.toUpperCase()}</span>
            </div>
          </div>
          <div style={{display:'flex', gap:8, position:'relative', flexWrap:'wrap', justifyContent:'flex-end'}}>
            {onUndo && (
              <button className="btn btn-ghost" onClick={onUndo} title="Deshacer (Ctrl+Z)" style={{padding:'6px 8px'}}>
                <Icon name="undo" size={14} />
              </button>
            )}
            {onRedo && (
              <button className="btn btn-ghost" onClick={onRedo} title="Rehacer (Ctrl+Shift+Z)" style={{padding:'6px 8px'}}>
                <Icon name="redo" size={14} />
              </button>
            )}
            <button
              className="btn btn-primary"
              onClick={() => setAgentOpen(true)}
              title="Pídele a la IA que cree o modifique el email"
              style={{background:'linear-gradient(135deg, #8b5cf6 0%, #ec4899 100%)', border:'none'}}
            >
              <Icon name="sparkles" size={14} /> IA
            </button>
            <button className="btn btn-ghost" onClick={handleClear} title="Vaciar el lienzo y empezar uno nuevo">
              <Icon name="plus" size={14} /> Nuevo
            </button>
            {!inTplMode && onSaveAsTemplate && (
              <button className="btn btn-ghost" onClick={() => setSaveTplModal(true)} disabled={blocks.length === 0} title="Guardar este email como plantilla reutilizable">
                <Icon name="template" size={14} /> Guardar como plantilla
              </button>
            )}
            <div style={{position:'relative'}}>
              <button className="btn btn-outline" onClick={e => { e.stopPropagation(); setHtmlMenu(v => !v); }}>
                <Icon name="code" size={14} /> HTML
              </button>
              {htmlMenu && (
                <div className="dropdown-menu" onClick={e => e.stopPropagation()}>
                  <button className="dropdown-item" onClick={handleCopyHtml}>
                    <Icon name="copy" size={13} /> Copiar al portapapeles
                  </button>
                  <button className="dropdown-item" onClick={handleDownloadHtml}>
                    <Icon name="download" size={13} /> Descargar .html
                  </button>
                  <button className="dropdown-item" onClick={handleDownloadPdf}>
                    <Icon name="download" size={13} /> Exportar a PDF
                  </button>
                  <button className="dropdown-item" onClick={handleDownloadDoc}>
                    <Icon name="download" size={13} /> Exportar a Word (.doc)
                  </button>
                  {onExpandPreview && (
                    <button className="dropdown-item" onClick={() => { onExpandPreview(); setHtmlMenu(false); }}>
                      <Icon name="eye" size={13} /> Vista previa ampliada
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
        {toast && <div className="canvas-toast">{toast}</div>}
        {saveTplModal && (
          <SaveAsTemplateModal
            onClose={() => setSaveTplModal(false)}
            onSave={handleSaveAsNewTpl}
            blocksCount={blocks.length}
          />
        )}
        {agentOpen && (
          <AiAgentModal
            blocks={blocks}
            lang={lang}
            appState={appState}
            onClose={() => setAgentOpen(false)}
            onCommit={(work) => {
              if (typeof onSetBlocks === 'function') onSetBlocks(work.blocks);
              if (typeof onSetLang === 'function' && work.lang && work.lang !== lang) onSetLang(work.lang);
            }}
          />
        )}

        {blocks.length === 0 ? (
          <div className="empty">
            <div className="empty-ill">
              <Icon name="layers" size={32} />
            </div>
            <div className="empty-title">Un lienzo en blanco</div>
            <div className="empty-sub">Empieza con una plantilla — o crea tu email desde cero</div>

            {visibleTemplates.length > 0 && onAddBlock && (
              <div className="empty-templates">
                {visibleTemplates.map(t => {
                  const brand = BRANDS.find(b => b.id === t.brand);
                  const tName = (typeof window.getLocalizedText === 'function') ? window.getLocalizedText(t, 'name', lang) : t.name;
                  const tDesc = (typeof window.getLocalizedText === 'function') ? window.getLocalizedText(t, 'desc', lang) : t.desc;
                  return (
                    <div key={t.id} className="empty-tpl"
                         onClick={() => onAddBlock({ type: 'template', templateId: t.id })}>
                      <div className={'empty-tpl-bar ' + (t.colorClass || 'gray')} />
                      <div className="empty-tpl-name">{tName}</div>
                      <div className="empty-tpl-desc">{tDesc}</div>
                      <div className="empty-tpl-meta">
                        {brand?.label || t.brand} · {((t.blocks && t.blocks.length) || (t.compositorBlocks && t.compositorBlocks.length) || 0)} bloques
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            <div className="empty-divider">o</div>
            <button className="btn btn-accent" onClick={() => onOpenPalette()}>
              <Icon name="plus" size={14} /> Añadir primer bloque
            </button>
          </div>
        ) : (
          <>
            {blocks.map((b, i) => (
              <React.Fragment key={b.id}>
                <BlockCard
                  block={b}
                  idx={i}
                  total={blocks.length}
                  selected={selectedId === b.id}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                  onUpdate={onUpdate}
                  onDelete={onDelete}
                  onMove={onMove}
                  onReorder={onReorder}
                  onDuplicate={onDuplicate}
                  onUngroup={onUngroup}
                  onOpenInnerPalette={onOpenInnerPalette}
                  onPickColumnAdd={onAddBlockToColumn}
                  appState={appState}
                  lang={lang}
                />
                {i < blocks.length - 1 && (
                  <div className="insert-zone">
                    <button className="insert-btn" onClick={() => onOpenPalette(i)} title="Insertar bloque aquí">+</button>
                  </div>
                )}
              </React.Fragment>
            ))}
            <div className="quick-add" onClick={() => onOpenPalette()}>
              <Icon name="plus" size={14} />
              <span>Añadir bloque</span>
              <span className="mono" style={{fontSize:11, opacity:.6, marginLeft:8}}>⌘K</span>
            </div>
          </>
        )}
      </div>
    </main>
  );
}

function EmailIframe({ html, device }) {
  const wrapperRef = React.useRef(null);
  const [scale, setScale] = React.useState(1);

  // Render the email at its NATIVE design width (620px / 380px) and use
  // CSS scale to fit the right panel — this prevents the email's own
  // @media (max-width:600px) from firing prematurely and collapsing
  // pair/trio columns when the preview panel is narrow.
  const baseWidth = device === 'mobile' ? 380 : 620;

  // sandbox="" + srcdoc → no scripts, no top-nav, origen único opaco. Si
  // un user inyecta `<script>` o un `<style>body{display:none}` en un texto
  // (incluso a través de un fallo de sanitizeHtml), se queda contenido en
  // el iframe sin acceso a localStorage / cookies / Supabase. Antes
  // usábamos doc.open()+doc.write() que requiere mismo origen — y por
  // tanto NO podíamos sandboxear el iframe sin perder el render.
  // Apr 2026 audit fix.
  const srcDoc = html || '<html><body></body></html>';

  React.useEffect(() => {
    const w = wrapperRef.current;
    if (!w || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(entries => {
      const cw = entries[0].contentRect.width;
      const next = cw < baseWidth ? Math.max(0.4, cw / baseWidth) : 1;
      setScale(next);
    });
    ro.observe(w);
    return () => ro.disconnect();
  }, [baseWidth]);

  return (
    <div ref={wrapperRef} style={{
      width: '100%',
      height: '75vh',
      minHeight: 500,
      overflow: 'hidden',
      display: 'flex',
      justifyContent: 'center',
    }}>
      <iframe
        title="Email preview"
        srcDoc={srcDoc}
        sandbox=""
        style={{
          width: baseWidth + 'px',
          height: (100 / scale) + '%',
          flexShrink: 0,
          border: 'none',
          background: '#fff',
          borderRadius: 'var(--r-sm)',
          boxShadow: 'var(--sh-md)',
          display: 'block',
          transform: 'scale(' + scale + ')',
          transformOrigin: 'top center',
        }}
      />
    </div>
  );
}

function PreviewPanel({ blocks, device, setDevice, tab, setTab, lang, embedded, emailHtml, onExpand }) {
  const html = emailHtml || '';
  return (
    <section className="preview" style={embedded ? {borderLeft:'none', flex:1, minHeight:0} : {}}>
      <div className="preview-header">
        <div className="preview-tabs">
          <button className={'preview-tab' + (tab === 'visual' ? ' active' : '')} onClick={() => setTab('visual')}>Visual</button>
          <button className={'preview-tab' + (tab === 'html' ? ' active' : '')} onClick={() => setTab('html')}>HTML</button>
        </div>
        <div className="device-toggle">
          <button className={'icon-btn' + (device === 'desktop' ? ' active' : '')} onClick={() => setDevice('desktop')} title="Desktop (620 px)">
            <Icon name="monitor" size={14} />
          </button>
          <button className={'icon-btn' + (device === 'mobile' ? ' active' : '')} onClick={() => setDevice('mobile')} title="Móvil (380 px)">
            <Icon name="smartphone" size={14} />
          </button>
          {onExpand && (
            <button className="icon-btn" onClick={onExpand} title="Vista ampliada" style={{marginLeft:6}}>
              <Icon name="panel" size={14} />
            </button>
          )}
        </div>
      </div>
      <div className="preview-meta">
        <span>{lang.toUpperCase()}</span>
        <span>·</span>
        <span>{device === 'mobile' ? '380 px' : '620 px'}</span>
        <span>·</span>
        <span>{blocks.length} bloques</span>
        <span>·</span>
        <span style={{cursor:'pointer'}} onClick={() => { if (typeof copyHtmlAsRich === 'function') copyHtmlAsRich(html); else navigator.clipboard?.writeText(html).catch(() => {}); }} title="Copiar email — pégalo en Gmail/Outlook y verás el render, no el código">
          <Icon name="copy" size={11} /> Copiar HTML
        </span>
      </div>
      <div className="preview-body" style={{padding: embedded ? 12 : 16, overflow:'auto'}}>
        {tab === 'visual' ? (
          <EmailIframe html={html} device={device} />
        ) : (
          <div className="preview-frame" style={{padding:16, fontFamily:'var(--font-mono)', fontSize:11, color:'var(--text-muted)', lineHeight:1.55, background:'var(--bg-panel)', overflow:'auto', maxHeight:'75vh'}}>
            <pre style={{whiteSpace:'pre-wrap', wordBreak:'break-word', margin:0}}>{html}</pre>
          </div>
        )}
      </div>
    </section>
  );
}

const CMDK_SCOPES = [
  { id: 'all', label: 'Todos' },
  { id: 'productos', label: 'Productos', groups: ['Productos'] },
  { id: 'textos', label: 'Textos', groups: ['Textos'] },
  { id: 'plantillas', label: 'Plantillas', groups: ['Plantillas'] },
  { id: 'compuestos', label: 'Compuestos', groups: ['Compuestos'] },
  { id: 'composiciones', label: 'Composiciones', groups: ['Composiciones'] },
  { id: 'layout', label: 'Layout', groups: ['Layout'] },
];

// Layout items shown in the command palette so dividers and column sections
// can be inserted from the inline "+" picker (not just the sidebar). The
// onPick handler in app-main.jsx routes spec.type through addBlock(), which
// already handles section_2col / section_3col / divider_*.
const CMDK_LAYOUT_ITEMS = [
  { type: 'section_2col',  id: 'layout-2col',         title: '2 columnas',       sub: 'Sección con dos columnas',          group: 'Layout', icon: '▥' },
  { type: 'section_3col',  id: 'layout-3col',         title: '3 columnas',       sub: 'Sección con tres columnas',         group: 'Layout', icon: '▦' },
  { type: 'divider_line',  id: 'layout-div-line',     title: 'Línea fina',       sub: 'Divisor sutil de ancho completo',   group: 'Layout', icon: '─' },
  { type: 'divider_short', id: 'layout-div-short',    title: 'Línea corta',      sub: 'Divisor centrado breve',            group: 'Layout', icon: '⎯' },
  { type: 'divider_dots',  id: 'layout-div-dots',     title: 'Puntos',           sub: 'Divisor de puntos elegante',        group: 'Layout', icon: '⋯' },
];

function CommandPalette({ onClose, onPick, appState, currentUser }) {
  const [q, setQ] = React.useState('');
  const [active, setActive] = React.useState(0);
  const [scope, setScope] = usePersistentState('cmdk-scope', 'all');
  const inputRef = React.useRef(null);
  React.useEffect(() => { inputRef.current?.focus(); }, []);

  const products = (appState?.products) || PRODUCTS;
  const texts = (appState?.prewrittenTexts) || PREWRITTEN_TEXTS;
  const templates = (appState?.templates) || TEMPLATES;
  const composed = (appState?.composedBlocks) || COMPOSED_BLOCKS || [];
  const standalones = (appState?.standaloneBlocks || STANDALONE_BLOCKS).map(sb => Object.assign({}, sb, {
    type: sb.type || sb.blockType,
  }));

  const ql = q.toLowerCase();
  const matches = (s) => !ql || (s || '').toLowerCase().includes(ql);

  const allUnscoped = [
    ...products
      .filter(p => p.visible !== false && !isHiddenForUser(currentUser, 'products', p.id))
      .map(p => ({ type: 'product', id: p.id, title: p.name, sub: p.price || '', group: 'Productos', icon: '▣', brand: p.brand, badge: p.badge })),
    ...texts
      .filter(t => t.visible !== false && !isHiddenForUser(currentUser, 'prewrittenTexts', t.id))
      .map(t => ({ type: 'text', id: t.id, title: t.name, sub: (t.text || '').slice(0, 50) + '…', group: 'Textos', icon: t.icon || '¶', brand: t.brand })),
    ...templates
      .filter(t => t.visible !== false && !isHiddenForUser(currentUser, 'templates', t.id))
      .map(t => ({ type: 'template', id: t.id, title: t.name, sub: t.desc || (((t.blocks && t.blocks.length) || (t.compositorBlocks && t.compositorBlocks.length) || 0) + ' bloques'), group: 'Plantillas', icon: '▦', brand: t.brand, colorClass: t.colorClass })),
    ...composed
      .filter(c => c.visible !== false && !isHiddenForUser(currentUser, 'composedBlocks', c.id))
      .map(c => ({ type: 'composed', id: c.id, title: c.title, sub: c.desc || c.priceRange || '', group: 'Compuestos', icon: '◧', brand: c.brand, colorClass: c.colorTag })),
    ...standalones
      .filter(b => !isHiddenForUser(currentUser, 'standaloneBlocks', b.id))
      .map(b => ({ type: b.type, id: b.id, title: b.title, sub: b.section || b.desc || '', group: 'Composiciones', icon: b.icon || '□', brand: b.brand })),
    ...CMDK_LAYOUT_ITEMS,
  ];

  const counts = {};
  allUnscoped.forEach(i => { counts[i.group] = (counts[i.group] || 0) + 1; });

  const activeScope = CMDK_SCOPES.find(s => s.id === scope) || CMDK_SCOPES[0];
  const inScope = (i) => !activeScope.groups || activeScope.groups.includes(i.group);

  const all = allUnscoped.filter(i => inScope(i) && (matches(i.title) || matches(i.sub)));

  const groups = {};
  all.forEach(i => { (groups[i.group] ||= []).push(i); });

  const handleKey = (e) => {
    if (e.key === 'Escape') onClose();
    if (e.key === 'ArrowDown') { setActive(a => Math.min(all.length - 1, a + 1)); e.preventDefault(); }
    if (e.key === 'ArrowUp') { setActive(a => Math.max(0, a - 1)); e.preventDefault(); }
    if (e.key === 'Enter' && all[active]) { onPick(all[active]); onClose(); }
  };

  React.useEffect(() => { setActive(0); }, [scope, q]);

  let flatIdx = 0;
  return (
    <div className="cmdk-overlay" onClick={onClose}>
      <div className="cmdk" onClick={e => e.stopPropagation()}>
        <input
          ref={inputRef}
          className="cmdk-input"
          placeholder="Buscar bloques, productos, plantillas…"
          value={q}
          onChange={e => setQ(e.target.value)}
          onKeyDown={handleKey}
        />
        <div className="cmdk-scopes">
          {CMDK_SCOPES.map(s => {
            const n = s.id === 'all'
              ? allUnscoped.length
              : (s.groups || []).reduce((acc, g) => acc + (counts[g] || 0), 0);
            return (
              <button
                key={s.id}
                className={'cmdk-scope' + (scope === s.id ? ' active' : '')}
                onClick={() => setScope(s.id)}
              >
                {s.label} <span className="cmdk-scope-count">{n}</span>
              </button>
            );
          })}
        </div>
        <div className="cmdk-body scroll">
          {Object.entries(groups).map(([group, items]) => (
            <div key={group} className="cmdk-group">
              <div className="cmdk-group-title">{group}</div>
              {items.map(item => {
                const myIdx = flatIdx++;
                return (
                  <button
                    key={item.id + item.type}
                    className={'cmdk-item' + (myIdx === active ? ' active' : '')}
                    onClick={() => { onPick(item); onClose(); }}
                    onMouseEnter={() => setActive(myIdx)}
                  >
                    <div className="cmdk-item-icon">{item.icon}</div>
                    <div className="cmdk-item-title">
                      {item.colorClass && <span className={'lib-color-tag ' + item.colorClass} />}
                      {item.title}
                    </div>
                    <div className="cmdk-item-sub">{item.sub}</div>
                  </button>
                );
              })}
            </div>
          ))}
          {all.length === 0 && (
            <div style={{padding:40, textAlign:'center', color:'var(--text-muted)', fontSize:13}} className="serif">
              Nada por aquí. Prueba con otro término o cambia de scope.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* Modal: capture the current email as a new template. */
function SaveAsTemplateModal({ onClose, onSave, blocksCount }) {
  const [name, setName] = React.useState('');
  const [brand, setBrand] = React.useState('mix');
  const [colorClass, setColorClass] = React.useState('gray');
  const _brands = (typeof window !== 'undefined' && window.BRANDS) || BRANDS || [];
  React.useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  const submit = () => {
    if (!name.trim()) return;
    onSave(name.trim(), brand, colorClass);
  };
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()} style={{maxWidth:440}}>
        <h2>Guardar como plantilla</h2>
        <p style={{marginBottom:12}}>Vas a guardar los <strong>{blocksCount} bloques</strong> actuales como plantilla reutilizable.</p>
        <div className="field">
          <label className="field-label">Nombre</label>
          <input
            className="input"
            value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && submit()}
            placeholder="ej. Promo MBO Verano"
            autoFocus
          />
        </div>
        <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:12}}>
          <div className="field">
            <label className="field-label">Marca</label>
            <select className="select" value={brand} onChange={e => setBrand(e.target.value)}>
              <option value="mix">Multi-marca</option>
              {_brands.filter(b => b.id !== 'bomedia').map(b => (
                <option key={b.id} value={b.id}>{b.label}</option>
              ))}
            </select>
          </div>
          <div className="field">
            <label className="field-label">Color</label>
            <select className="select" value={colorClass} onChange={e => setColorClass(e.target.value)}>
              <option value="blue">Azul</option>
              <option value="purple">Morado</option>
              <option value="orange">Naranja</option>
              <option value="teal">Teal</option>
              <option value="gray">Gris</option>
            </select>
          </div>
        </div>
        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose}>Cancelar</button>
          <button className="btn btn-primary" onClick={submit} disabled={!name.trim()}>
            <Icon name="zap" size={13}/> Guardar plantilla
          </button>
        </div>
      </div>
    </div>
  );
}

/* Full-screen preview modal — opens the email HTML in a wide centered viewport
   so the user can see the design at native widths without the panel constraint. */
function EmailPreviewModal({ html, lang, onClose }) {
  const [device, setDevice] = React.useState('desktop');
  const [toast, setToast] = React.useState('');

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(''), 2400);
  };

  // sandbox="" + srcdoc → contiene cualquier script o style malicioso que
  // se haya colado por sanitizeHtml. Sin acceso a localStorage del padre
  // ni a las claves de Supabase. Apr 2026 audit fix.
  const srcDoc = html || '<html><body></body></html>';

  React.useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const handleCopy = async () => {
    const r = (typeof copyHtmlAsRich === 'function')
      ? await copyHtmlAsRich(html || '')
      : (await (async () => { try { await navigator.clipboard.writeText(html || ''); return { ok:true, mode:'plain' }; } catch (e) { return { ok:false }; } })());
    if (r && r.ok && r.mode === 'rich') showToast('HTML copiado · pégalo en Gmail/Outlook y verás el email renderizado');
    else if (r && r.ok) showToast('HTML copiado (texto plano)');
    else showToast('No se pudo copiar (revisa permisos del navegador)');
  };
  const handleDownload = () => {
    const blob = new Blob([html || ''], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'bomedia-email-' + new Date().toISOString().slice(0, 10) + '.html';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    showToast('HTML descargado');
  };
  // Word export — same approach as in Canvas: HTML wrapped with the Word/MSO
  // namespaces, served as application/msword so the browser downloads it as
  // a .doc that Word opens cleanly.
  const handleDownloadDoc = () => {
    const docHtml =
      '<html xmlns:o="urn:schemas-microsoft-com:office:office" ' +
      'xmlns:w="urn:schemas-microsoft-com:office:word" ' +
      'xmlns="http://www.w3.org/TR/REC-html40">' +
      '<head><meta charset="utf-8"><title>Bomedia email</title>' +
      '<!--[if gte mso 9]><xml><w:WordDocument><w:View>Print</w:View>' +
      '<w:Zoom>90</w:Zoom><w:DoNotOptimizeForBrowser/></w:WordDocument></xml><![endif]-->' +
      '<style>body{font-family:Calibri,Arial,sans-serif;}@page{size:auto;margin:1.5cm;}</style>' +
      '</head><body>' + (html || '') + '</body></html>';
    const blob = new Blob(['﻿', docHtml], { type: 'application/msword' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'bomedia-email-' + new Date().toISOString().slice(0, 10) + '.doc';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    showToast('Documento Word descargado');
  };
  // PDF export — print dialog approach (no extra libs). User picks "Save as
  // PDF" as the print destination.
  const handleDownloadPdf = () => {
    const printHtml =
      '<!doctype html><html><head><meta charset="utf-8"><title>Bomedia email</title>' +
      '<style>@page{size:A4;margin:12mm;}body{margin:0;}@media print{body{-webkit-print-color-adjust:exact;print-color-adjust:exact;}}</style>' +
      '</head><body>' + (html || '') + '</body></html>';
    const iframe = document.createElement('iframe');
    iframe.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden';
    document.body.appendChild(iframe);
    const cleanup = () => { try { document.body.removeChild(iframe); } catch (_) {} };
    iframe.onload = () => {
      try {
        const win = iframe.contentWindow;
        win.focus();
        setTimeout(() => {
          try { win.print(); } catch (_) {}
          setTimeout(cleanup, 1500);
        }, 350);
      } catch (e) { cleanup(); }
    };
    if ('srcdoc' in iframe) iframe.srcdoc = printHtml;
    else { const doc = iframe.contentDocument || iframe.contentWindow?.document; if (doc) { doc.open(); doc.write(printHtml); doc.close(); } }
    showToast('Abriendo diálogo de impresión · selecciona "Guardar como PDF"');
  };
  const handleOpenTab = () => {
    const w = window.open('about:blank', '_blank');
    if (w) { w.document.write(html || ''); w.document.close(); }
  };

  const width = device === 'mobile' ? 380 : 720;

  return (
    <div className="preview-modal-overlay" onClick={onClose}>
      <div className="preview-modal" onClick={e => e.stopPropagation()}>
        <div className="preview-modal-head">
          <div style={{display:'flex', alignItems:'center', gap:10}}>
            <strong style={{fontSize:14}}>Vista previa del email</strong>
            <span className="mono" style={{fontSize:11, color:'var(--text-subtle)'}}>{lang.toUpperCase()} · {width}px</span>
          </div>
          <div style={{display:'flex', gap:8, alignItems:'center'}}>
            <div className="device-toggle">
              <button className={'icon-btn' + (device === 'desktop' ? ' active' : '')} onClick={() => setDevice('desktop')} title="Desktop">
                <Icon name="monitor" size={14} />
              </button>
              <button className={'icon-btn' + (device === 'mobile' ? ' active' : '')} onClick={() => setDevice('mobile')} title="Móvil">
                <Icon name="smartphone" size={14} />
              </button>
            </div>
            <button className="btn btn-ghost" onClick={handleCopy} style={{fontSize:12}}>
              <Icon name="copy" size={12} /> Copiar HTML
            </button>
            <button className="btn btn-ghost" onClick={handleDownload} style={{fontSize:12}} title="Descargar como .html">
              <Icon name="download" size={12} /> HTML
            </button>
            <button className="btn btn-ghost" onClick={handleDownloadPdf} style={{fontSize:12}} title="Exportar como PDF (vía diálogo de impresión)">
              <Icon name="download" size={12} /> PDF
            </button>
            <button className="btn btn-ghost" onClick={handleDownloadDoc} style={{fontSize:12}} title="Descargar como documento Word (.doc)">
              <Icon name="download" size={12} /> Word
            </button>
            <button className="btn btn-ghost" onClick={handleOpenTab} style={{fontSize:12}}>
              <Icon name="share" size={12} /> Pestaña nueva
            </button>
            <button className="icon-btn" onClick={onClose} title="Cerrar (Esc)">
              <Icon name="x" size={16} />
            </button>
          </div>
        </div>
        <div className="preview-modal-body">
          <iframe
            title="Email preview ampliada"
            srcDoc={srcDoc}
            sandbox=""
            style={{
              width: width + 'px',
              height: '100%',
              minHeight: 600,
              border: 'none',
              background: '#fff',
              borderRadius: 'var(--r-md)',
              boxShadow: 'var(--sh-lg)',
              display: 'block',
              margin: '0 auto',
            }}
          />
          {toast && (
            <div style={{
              position:'absolute', bottom:24, left:'50%', transform:'translateX(-50%)',
              background:'var(--bg-inverse)', color:'var(--bg)',
              padding:'10px 18px', borderRadius:'var(--r-md)',
              fontSize:13, fontWeight:500,
              boxShadow:'0 12px 30px rgba(0,0,0,0.25)',
              zIndex:10, maxWidth:'90%',
              animation:'fadeInUp 0.2s ease-out',
            }}>
              {toast}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* Editable canvas title — the user clicks the "Email sin título" line and
   types the actual subject of the email. Persists per user. When editing
   a template, falls back to the template's name (read-only). The styling
   matches the variant ('serif' or default) and reuses the existing CSS
   classes so it looks identical to the static h1 it replaces. */
function EditableEmailTitle({ value, onChange, variant, readOnly, placeholder }) {
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(value || '');
  const inputRef = React.useRef(null);
  React.useEffect(() => { setDraft(value || ''); }, [value]);
  React.useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const commit = () => {
    setEditing(false);
    const v = (draft || '').trim();
    if (v !== (value || '').trim() && typeof onChange === 'function') onChange(v);
  };
  const cancel = () => {
    setEditing(false);
    setDraft(value || '');
  };
  const onKey = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { e.preventDefault(); cancel(); }
  };

  if (readOnly) {
    return variant === 'serif'
      ? <h1 className="canvas-title"><span style={{fontStyle:'italic'}}>{value}</span></h1>
      : <h1 className="canvas-title-plain">{value}</h1>;
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        className={variant === 'serif' ? 'canvas-title' : 'canvas-title-plain'}
        style={{background:'transparent', border:'none', outline:'2px solid var(--accent)', outlineOffset:4, borderRadius:4, width:'100%', maxWidth:'100%', display:'block', font:'inherit', padding:'2px 4px'}}
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={onKey}
        placeholder={placeholder}
        maxLength={120}
      />
    );
  }

  const display = (value || '').trim();
  const showPlaceholder = !display;
  const titleClass = variant === 'serif' ? 'canvas-title' : 'canvas-title-plain';
  return (
    <h1
      className={titleClass}
      onClick={() => setEditing(true)}
      style={{cursor:'text', color: showPlaceholder ? 'var(--text-subtle)' : undefined}}
      title="Clic para nombrar este email"
    >
      {showPlaceholder
        ? (variant === 'serif'
            ? <>Email <span style={{fontStyle:'italic'}}>sin título</span></>
            : (placeholder || 'Email sin título'))
        : (variant === 'serif' ? <span style={{fontStyle:'italic'}}>{display}</span> : display)}
    </h1>
  );
}

/* Modal that drives the AI agent. The user types a prompt in natural
   language; the agent runs through OpenAI tool-use, mutating a working
   copy of the canvas. We render a live log of steps (thinking → tool
   call → tool result → final text) and only commit to the live state
   when the agent finishes successfully. */
function AiAgentModal({ blocks, lang, appState, onClose, onCommit }) {
  const [prompt, setPrompt] = React.useState('');
  const [running, setRunning] = React.useState(false);
  const [steps, setSteps] = React.useState([]);
  const [done, setDone] = React.useState(null); // { ok, finalText, work }
  const inputRef = React.useRef(null);
  const logRef = React.useRef(null);

  React.useEffect(() => { inputRef.current && inputRef.current.focus(); }, []);
  React.useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [steps, done]);

  const examples = [
    'Crea un email para vender la artisJet 5000U a un cliente alemán',
    'Vacía el canvas y mete una sección de 2 columnas con MBO 3050 y MBO 4060',
    'Traduce todo el texto del canvas al francés',
    'Añade un CTA al final que diga "Reserva tu demo" enlazando a https://bomedia.es',
    'Borra el último bloque',
  ];

  const run = async () => {
    if (!prompt.trim()) return;
    if (typeof window.runAgent !== 'function') {
      setSteps([{ kind: 'error', error: 'Módulo del agente no cargado.' }]);
      return;
    }
    setRunning(true);
    setSteps([{ kind: 'user', text: prompt.trim() }]);
    setDone(null);
    try {
      const result = await window.runAgent({
        prompt: prompt.trim(),
        ctx: { appState, blocks, lang },
        onStep: (s) => setSteps(prev => [...prev, s]),
      });
      setDone(result);
    } catch (err) {
      setSteps(prev => [...prev, { kind: 'error', error: err.message || String(err) }]);
      setDone({ ok: false, error: err.message || String(err) });
    } finally {
      setRunning(false);
    }
  };

  const commit = () => {
    if (done && done.ok && done.work) onCommit(done.work);
    onClose();
  };

  const renderStep = (s, i) => {
    if (s.kind === 'user') return (
      <div key={i} style={{padding:'8px 10px', background:'var(--bg-sunken)', borderRadius:6, fontSize:12.5, marginBottom:6}}>
        <span style={{fontSize:10, fontWeight:700, color:'var(--accent)', marginRight:8}}>TÚ</span>
        {s.text}
      </div>
    );
    if (s.kind === 'thinking') return (
      <div key={i} style={{fontSize:11, color:'var(--text-muted)', padding:'4px 10px', fontStyle:'italic'}}>
        🤔 Pensando… (paso {(s.step || 0) + 1})
      </div>
    );
    if (s.kind === 'tool') return (
      <div key={i} style={{padding:'6px 10px', fontSize:11.5, fontFamily:'var(--font-mono)', background:'color-mix(in oklch, var(--accent) 6%, transparent)', borderRadius:4, marginBottom:4}}>
        <span style={{color:'var(--accent-ink)', fontWeight:700}}>→ {s.name}</span>
        <span style={{color:'var(--text-muted)', marginLeft:6}}>{(JSON.stringify(s.args) || '').slice(0, 200)}</span>
      </div>
    );
    if (s.kind === 'tool_result') {
      const ok = !/error/i.test(s.result || '');
      return (
        <div key={i} style={{padding:'4px 10px 4px 26px', fontSize:11, fontFamily:'var(--font-mono)', color: ok ? 'var(--success)' : 'var(--danger)', marginBottom:4}}>
          ✓ {(s.result || '').slice(0, 160)}{(s.result || '').length > 160 ? '…' : ''}
        </div>
      );
    }
    if (s.kind === 'tool_error') return (
      <div key={i} style={{padding:'4px 10px', fontSize:11, color:'var(--danger)'}}>
        ✗ Error en {s.name}: {s.error}
      </div>
    );
    if (s.kind === 'final') return (
      <div key={i} style={{padding:'10px 12px', background:'color-mix(in oklch, var(--success) 10%, var(--bg-panel))', border:'1px solid color-mix(in oklch, var(--success) 30%, var(--border))', borderRadius:6, fontSize:13, marginTop:8, marginBottom:6, lineHeight:1.5}}>
        <span style={{fontSize:10, fontWeight:700, color:'var(--success)', marginRight:8}}>IA</span>
        {s.text || '(sin respuesta de texto)'}
      </div>
    );
    if (s.kind === 'error') return (
      <div key={i} style={{padding:'10px 12px', background:'color-mix(in oklch, var(--danger) 10%, var(--bg-panel))', border:'1px solid var(--danger)', borderRadius:6, fontSize:12, color:'var(--danger)', marginBottom:6}}>
        ⚠ {s.error}
      </div>
    );
    return null;
  };

  return (
    <>
      <div className="bo-drawer-overlay" onClick={onClose} style={{zIndex:60}}/>
      <div style={{position:'fixed', top:'5%', left:'50%', transform:'translateX(-50%)', width:'min(820px, 95vw)', maxHeight:'90vh', background:'var(--bg-panel)', border:'1px solid var(--border)', borderRadius:'var(--r-md)', display:'flex', flexDirection:'column', zIndex:61, overflow:'hidden', boxShadow:'0 30px 80px rgba(0,0,0,0.3)'}}>
        <div style={{padding:'14px 18px', borderBottom:'1px solid var(--border)', display:'flex', alignItems:'center', gap:12, background:'linear-gradient(135deg, color-mix(in oklch, #8b5cf6 8%, var(--bg-panel)) 0%, color-mix(in oklch, #ec4899 8%, var(--bg-panel)) 100%)'}}>
          <Icon name="sparkles" size={18}/>
          <div>
            <div style={{fontSize:14, fontWeight:700, letterSpacing:'-0.01em'}}>Asistente IA · agente</div>
            <div style={{fontSize:11, color:'var(--text-muted)'}}>Pídeselo en lenguaje natural — la IA usa herramientas para construir el email por ti.</div>
          </div>
          <button className="icon-btn" onClick={onClose} style={{marginLeft:'auto'}}><Icon name="x" size={16}/></button>
        </div>

        <div ref={logRef} style={{padding:14, overflowY:'auto', flex:1, minHeight:200, maxHeight:'50vh'}}>
          {steps.length === 0 && (
            <div style={{padding:'10px 4px'}}>
              <div style={{fontSize:11, color:'var(--text-muted)', marginBottom:8, textTransform:'uppercase', letterSpacing:'0.08em', fontWeight:600}}>Ejemplos</div>
              <div style={{display:'flex', flexDirection:'column', gap:6}}>
                {examples.map((ex, i) => (
                  <button key={i} className="btn btn-ghost" style={{fontSize:12, justifyContent:'flex-start', textAlign:'left', padding:'8px 10px', background:'var(--bg-sunken)'}} onClick={() => setPrompt(ex)}>
                    <Icon name="zap" size={11} style={{marginRight:6, color:'var(--accent)'}}/>{ex}
                  </button>
                ))}
              </div>
            </div>
          )}
          {steps.map(renderStep)}
        </div>

        <div style={{padding:12, borderTop:'1px solid var(--border)', display:'flex', gap:8, alignItems:'flex-end'}}>
          <textarea
            ref={inputRef}
            className="textarea"
            rows={2}
            placeholder="Pídele algo a la IA…"
            value={prompt}
            onChange={e => setPrompt(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); run(); }
            }}
            disabled={running}
            style={{flex:1, fontSize:13, lineHeight:1.5, resize:'none', minHeight:50}}
          />
          <button className="btn btn-primary" onClick={run} disabled={running || !prompt.trim()} style={{whiteSpace:'nowrap'}}>
            {running ? 'Trabajando…' : <><Icon name="send" size={12}/> Enviar</>}
          </button>
        </div>

        {done && done.ok && (
          <div style={{padding:'10px 14px', borderTop:'1px solid var(--border)', background:'var(--bg-sunken)', display:'flex', gap:8, alignItems:'center', justifyContent:'flex-end'}}>
            <span style={{fontSize:11, color:'var(--text-muted)', flex:1}}>
              {done.work && done.work.blocks.length} bloques en el resultado · idioma: {done.work && done.work.lang}
            </span>
            <button className="btn btn-ghost" onClick={() => { setSteps([]); setDone(null); setPrompt(''); inputRef.current && inputRef.current.focus(); }} style={{fontSize:12}}>
              Otra petición
            </button>
            <button className="btn btn-primary" onClick={commit} style={{fontSize:12}}>
              <Icon name="zap" size={12}/> Aplicar al canvas
            </button>
          </div>
        )}
        {done && !done.ok && (
          <div style={{padding:'10px 14px', borderTop:'1px solid var(--border)', background:'var(--bg-sunken)', display:'flex', gap:8, justifyContent:'flex-end'}}>
            <button className="btn btn-ghost" onClick={() => { setSteps([]); setDone(null); inputRef.current && inputRef.current.focus(); }} style={{fontSize:12}}>
              Reintentar
            </button>
          </div>
        )}
      </div>
    </>
  );
}

Object.assign(window, { Sidebar, Canvas, PreviewPanel, CommandPalette, EmailIframe, MiniProduct, BrandStripPreview, EmailPreviewModal, SaveAsTemplateModal, AiAgentModal, EditableEmailTitle });
