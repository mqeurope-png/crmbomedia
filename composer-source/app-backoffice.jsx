/* ───────────── BACKOFFICE VIEW ───────────── */

/* Generate a fresh blank record for a given editor kind. The id uses a
   timestamp + short random suffix to stay unique across rapid clicks. */
function _newBoId(prefix) {
  return prefix + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 6);
}
function blankItemForKind(kind) {
  const t = Date.now();
  switch (kind) {
    case 'product':
      return { id: _newBoId('p'), brand: 'mbo', name: '', desc: '', price: '', area: '', img: '', feat1: '', feat2: '', badge: '', visible: true, i18n: {} };
    case 'brand':
      return { id: _newBoId('br'), label: 'Nueva marca', logo: '', url: { es:'',fr:'',de:'',en:'',nl:'' }, urlLabel: { es:'',fr:'',de:'',en:'',nl:'' }, color: '#64748b', divider: '#e2e8f0', logoHeight: '18', visible: true, logoText: 'Nueva' };
    case 'text':
      return { id: _newBoId('text'), name: 'Texto nuevo', icon: '✏️', brand: 'mix', text: '', visible: true, i18n: {} };
    case 'template':
      return { id: _newBoId('tpl'), name: 'Plantilla nueva', desc: '', colorClass: 'gray', brand: 'mix', compositorBlocks: [], visible: true, i18n: {} };
    case 'standalone':
      return { id: _newBoId('sb'), title: 'Bloque nuevo', desc: '', icon: '🧩', iconBg: '#e5e7eb', brand: 'mix', section: 'otros', blockType: 'product_single', config: { defaultProduct: '' }, visible: true, i18n: {} };
    case 'composed':
      // v5: el compuesto ahora se modela como `compositorBlocks` (lista de
      // bloques v3 idéntica a la de las plantillas). Los campos legacy se
      // mantienen vacíos para compat hacia atrás con datos antiguos.
      return { id: _newBoId('cb'), title: 'Compuesto nuevo', desc: '', priceRange: '', colorTag: 'gray', brand: 'mix', compositorBlocks: [], visible: true, i18n: {} };
    case 'cta':
      return { id: _newBoId('cta'), name: 'CTA nuevo', title: '', subtitle: '', bullets: [], text: 'Más información', url: '', bg: '#1d4ed8', color: '#ffffff', align: 'center', panelBg: 'transparent', panelBorder: 'transparent', visible: true };
    case 'user':
      return { id: _newBoId('u'), name: 'Nuevo usuario', role: 'commercial', passwordHash: '', hiddenItems: {}, aiStyles: {} };
    default: return null;
  }
}

function Backoffice({ brandFilter, setBrandFilter, appState, setAppState, onLoadTemplateInCompositor, currentUser, lang, isItemHidden, setItemHiddenForCurrentUser, autoHideForOthers }) {
  // Helper: read a localized field, falling back to the base ES value when no
  // translation exists. The BO list views honor the active language so the
  // user can verify how each item looks in FR/DE/EN/NL without having to dig
  // into each editor.
  const L = (obj, field) => (typeof window.getLocalizedText === 'function')
    ? window.getLocalizedText(obj, field, lang)
    : (obj && obj[field]);
  const isAdmin = currentUser?.role === 'admin';
  const [tab, setTab] = React.useState('products');
  const [search, setSearch] = React.useState('');
  const [editing, setEditing] = React.useState(null); // { kind, item }

  // Read live data from appState (falls back to globals so the BO still works if not wired)
  const products = (appState && appState.products) || PRODUCTS;
  const brands = (appState && appState.brands) || BRANDS;
  const texts = (appState && appState.prewrittenTexts) || PREWRITTEN_TEXTS;
  const templates = (appState && appState.templates) || TEMPLATES;
  const blocks = (appState && appState.standaloneBlocks) || STANDALONE_BLOCKS;
  const composed = (appState && appState.composedBlocks) || COMPOSED_BLOCKS || [];

  // Resolver una marca por ID con fallback sensato. Antes el código usaba
  // `brands.find(...) || brands[brands.length - 1] || { label, color:'#999' }`
  // — el segundo fallback era un bug latente: cuando el item tenía
  // brand:'mix' (que no existe como entrada en `brands[]`, es una
  // convención UI para "Multi-marca"), el find fallaba y la card mostraba
  // la ÚLTIMA marca del array. Bomedia estaba al final, así que pasaba
  // desapercibido — hasta que añadí las marcas Muestras al final, y los
  // items multi-marca empezaron a verse como "Muestras Textil". Apr 2026.
  const _MIX_BRAND = { id: 'mix', label: 'Multi-marca', color: '#94a3b8' };
  const resolveBrand = (id) => {
    if (!id || id === 'mix') return _MIX_BRAND;
    return brands.find(b => b.id === id) || { label: id, color: '#94a3b8' };
  };
  const ctaBlocks = (appState && appState.ctaBlocks) || [];

  // Persist edits from the drawer back into appState.
  const onSave = (kind, data) => {
    if (!setAppState) return;
    const collection = {
      product: 'products',
      brand: 'brands',
      text: 'prewrittenTexts',
      template: 'templates',
      standalone: 'standaloneBlocks',
      composed: 'composedBlocks',
      cta: 'ctaBlocks',
      user: 'users',
    }[kind];
    if (!collection) { setEditing(null); return; }
    let isNew = false;
    setAppState(prev => {
      const list = prev[collection] || [];
      const i = list.findIndex(x => x.id === data.id);
      isNew = i < 0;
      // Stamp createdBy on first save (non-admin). This lets the BO show a
      // "Tuyo" / "Privado" badge so the creator can see at a glance which
      // items are their own (and therefore hidden from other commercials by
      // default). Admin-created items go visible to everyone — no stamp.
      let stamped = data;
      if (isNew && currentUser && currentUser.role !== 'admin' && !data.createdBy) {
        stamped = Object.assign({}, data, { createdBy: currentUser.id, createdAt: Date.now() });
      }
      const next = i >= 0
        ? list.map((x, idx) => idx === i ? Object.assign({}, x, data) : x)
        : [...list, stamped];
      return Object.assign({}, prev, { [collection]: next });
    });
    // For non-user collections: if the current commercial user just created
    // the item, auto-hide it from other users so each operator owns their
    // additions until they explicitly share them. The visibility rule is
    // universal across collections (templates, composed, standalone, cta,
    // prewrittenTexts, products, brands) — anything a non-admin creates is
    // private by default; other users opt in via the eye toggle on each card.
    if (isNew && collection !== 'users' && typeof autoHideForOthers === 'function') {
      setTimeout(() => autoHideForOthers(collection, data.id), 0);
    }
    // Log la actividad: action `<kind>_create` o `<kind>_update` con el
    // id y nombre/título del item para que el panel admin pueda mostrar
    // "Sara creó la plantilla 'Demo PimPam'" sin tener que cruzar refs.
    if (typeof window.logActivity === 'function') {
      const action = (isNew ? kind + '_create' : kind + '_update');
      window.logActivity(action, {
        collection, id: data.id,
        name: data.name || data.title || data.label || data.id,
      });
    }
    setEditing(null);
  };

  const users = (appState && appState.users) || [];

  const baseNavItems = [
    { id: 'products', label: 'Productos', icon: 'box', count: products.length },
    { id: 'brands', label: 'Marcas', icon: 'palette', count: brands.length },
    { id: 'texts', label: 'Textos', icon: 'text', count: texts.length },
    { id: 'templates', label: 'Plantillas', icon: 'template', count: templates.length },
    { id: 'blocks', label: 'Bloques sueltos', icon: 'layers', count: blocks.length },
    { id: 'composed', label: 'Compuestos', icon: 'box', count: composed.length },
    { id: 'ctas', label: 'CTAs', icon: 'zap', count: ctaBlocks.length },
  ];
  const adminNavItems = [
    { id: 'users', label: 'Usuarios', icon: 'lock', count: users.length },
    { id: 'images', label: 'Imágenes', icon: 'copy', count: ((appState && appState.uploadedImages) || []).length },
    { id: 'activity', label: 'Actividad', icon: 'eye', count: ((appState && appState.activityLog) || []).length },
    { id: 'ai', label: 'Asistente IA', icon: 'sparkles' },
    { id: 'settings', label: 'Ajustes', icon: 'settings' },
  ];
  // Commercial users get a "Mi tono IA" tab so they can tune their own
  // assistant without admin intervention.
  const commercialNavItems = [
    { id: 'mytone', label: 'Mi tono IA', icon: 'sparkles' },
    { id: 'myaccount', label: 'Mi cuenta', icon: 'lock' },
  ];
  const navItems = isAdmin ? [...baseNavItems, ...adminNavItems] : [...baseNavItems, ...commercialNavItems];

  // Bounce non-admin users away from admin-only tabs (defensive — they
  // shouldn't be able to reach them via UI, but state may be stale)
  React.useEffect(() => {
    if (!isAdmin && (tab === 'users' || tab === 'ai' || tab === 'settings' || tab === 'images' || tab === 'activity')) {
      setTab('products');
    }
  }, [isAdmin, tab]);

  const titleMap = {
    products: { title: 'Productos', sub: 'Catálogo multi-marca. 5 idiomas por producto.' },
    brands: { title: 'Marcas', sub: 'Logos, URLs y colores por idioma.' },
    texts: { title: 'Textos pre-escritos', sub: 'Plantillas de texto reutilizables.' },
    templates: { title: 'Plantillas', sub: 'Emails pre-montados listos para editar.' },
    blocks: { title: 'Bloques sueltos', sub: 'Strips, heroes, vídeos y selectores de producto.' },
    composed: { title: 'Bloques compuestos', sub: 'Combos pre-montados (intro + productos + brand strip).' },
    ctas: { title: 'CTAs guardados', sub: 'Llamadas a la acción reutilizables (título + bullets + botón).' },
    myaccount: { title: 'Mi cuenta', sub: 'Cambia tu contraseña y revisa tus datos básicos.' },
    users: { title: 'Usuarios', sub: 'Gestiona accesos y roles. Solo admin.' },
    ai: { title: 'Asistente de redacción', sub: 'API key y tono por idioma. Solo admin.' },
    settings: { title: 'Ajustes', sub: 'Sincronización, acceso y preferencias. Solo admin.' },
    images: { title: 'Biblioteca de imágenes', sub: 'Gestiona las imágenes subidas. Borrar aquí solo las quita de la biblioteca; el archivo en WordPress se conserva (puedes borrarlo desde boprint.net/wp-admin → Media).' },
    activity: { title: 'Actividad', sub: 'Registro de acciones por usuario: logins, emails generados, plantillas guardadas, subidas de imagen, etc. Solo admin.' },
    mytone: { title: 'Mi tono IA', sub: 'Tu prompt personal por idioma. Solo lo ves tú y se aplica cuando pides ayuda al asistente.' },
  };
  const current = titleMap[tab] || titleMap.products;

  const _matches = (str) => !search || (str || '').toLowerCase().includes(search.toLowerCase());
  // Helper compartido: 'all' pasa todo; 'mix' aísla items con brand:'mix'
  // o sin marca; un brand id específico hace match exacto. Antes era inline
  // `p.brand === brandFilter` lo que dejaba fuera 'mix' del filtro propio
  // de Multi-marca. Apr 2026.
  const _matchesBrand = (b) => {
    if (brandFilter === 'all') return true;
    if (brandFilter === 'mix') return !b || b === 'mix';
    return b === brandFilter;
  };
  const filteredProducts = products.filter(p =>
    _matchesBrand(p.brand) &&
    (_matches(p.name) || _matches(L(p, 'name')))
  );
  const filteredTexts = texts.filter(t =>
    _matchesBrand(t.brand) &&
    (_matches(t.name) || _matches(L(t, 'name')) || _matches(t.text))
  );
  const filteredTemplates = templates.filter(t =>
    _matchesBrand(t.brand) &&
    (_matches(t.name) || _matches(L(t, 'name')) || _matches(t.desc))
  );
  const filteredBlocks = blocks.filter(b =>
    _matchesBrand(b.brand) &&
    (_matches(b.title) || _matches(L(b, 'title')) || _matches(b.section))
  );
  const filteredComposed = composed.filter(c => {
    const cBrand = c.brand || c.brandStrip;
    return _matchesBrand(cBrand) &&
      (_matches(c.title) || _matches(L(c, 'title')) || _matches(c.desc) || _matches(c.introText));
  });
  const filteredCtas = ctaBlocks.filter(c =>
    _matchesBrand(c.brand) &&
    (_matches(c.name) || _matches(c.title) || _matches(c.text) || _matches(c.url))
  );

  // Map current tab → drawer kind so the "+ Nuevo" button knows what blank
  // template to create. Tabs without an editable kind (brands/users/ai/etc)
  // are handled separately or hide the button entirely.
  const tabToKind = {
    products: 'product',
    brands: 'brand',
    texts: 'text',
    templates: 'template',
    blocks: 'standalone',
    composed: 'composed',
    ctas: 'cta',
  };
  const newKind = tabToKind[tab] || null;
  const onNew = () => {
    if (!newKind) return;
    const item = blankItemForKind(newKind);
    if (item) setEditing({ kind: newKind, item, isNew: true });
  };

  // Reusable toolbar row used across Textos / Plantillas / Bloques tabs.
  const renderToolbar = (placeholder) => (
    <div className="bo-toolbar">
      <div className="bo-search">
        <Icon name="search" size={14} />
        <input placeholder={placeholder} value={search} onChange={e => setSearch(e.target.value)} />
      </div>
      <div className="brand-chips" style={{padding:0, margin:0}}>
        <button className={'brand-chip' + (brandFilter === 'all' ? ' active' : '')} onClick={() => setBrandFilter('all')}>Todas</button>
        {/* "Multi-marca" como filtro: aísla items con brand:'mix' o sin marca
            asignada. La compartición con marca específica (mix items
            apareciendo junto a 'artisjet') la hace el matchesBrand del
            sidebar — aquí en BO simplemente filtramos. */}
        <button
          className={'brand-chip' + (brandFilter === 'mix' ? ' active' : '')}
          onClick={() => setBrandFilter('mix')}
          style={brandFilter === 'mix' ? {} : { color: '#94a3b8' }}
        >
          <span className="brand-chip-dot" style={{ background: '#94a3b8' }} />
          Multi-marca
        </button>
        {brands.filter(b => b.id !== 'bomedia').map(b => (
          <button key={b.id} className={'brand-chip' + (brandFilter === b.id ? ' active' : '')} onClick={() => setBrandFilter(b.id)} style={brandFilter === b.id ? {} : { color: b.color }}>
            <span className="brand-chip-dot" style={{ background: b.color }} />
            {b.label}
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <div className="bo-shell">
      <nav className="bo-nav">
        <div className="bo-nav-title">Gestión</div>
        {navItems.map(n => (
          <button
            key={n.id}
            className={'bo-nav-item' + (tab === n.id ? ' active' : '')}
            onClick={() => setTab(n.id)}
          >
            <Icon name={n.icon} size={15} />
            <span>{n.label}</span>
            {n.count != null && <span className="count mono">{n.count}</span>}
          </button>
        ))}
      </nav>

      <main className="bo-main scroll">
        <div className="bo-main-header">
          <div>
            <h1 className="bo-title">{current.title}</h1>
            <div className="bo-subtitle">{current.sub}</div>
          </div>
          <div style={{display:'flex', gap:8}}>
            <button className="btn btn-outline" onClick={() => exportAppStateAsJson(appState)} title="Descargar todo el estado como JSON">
              <Icon name="download" size={14}/> Exportar
            </button>
            {newKind && (
              <button className="btn btn-primary" onClick={onNew} title="Crear un nuevo item en este tab">
                <Icon name="plus" size={14}/> Nuevo
              </button>
            )}
          </div>
        </div>

        {tab === 'products' && (
          <>
            {renderToolbar('Buscar productos…')}

            <div className="product-grid">
              {filteredProducts.map(p => {
                const brand = brands.find(b => b.id === p.brand) || { label: p.brand, color: '#999' };
                const hidden = isItemHidden && isItemHidden('products', p.id);
                const lp = (typeof window.getLocalizedProduct === 'function') ? window.getLocalizedProduct(p, lang) : p;
                return (
                  <div key={p.id} className={'product-card' + (hidden ? ' hidden-for-user' : '')} onClick={() => setEditing({ kind: 'product', item: p })}>
                    <button
                      className="card-visibility-btn"
                      title={hidden ? 'Mostrarme este producto en el composer' : 'Ocultarme este producto en el composer'}
                      onClick={e => { e.stopPropagation(); setItemHiddenForCurrentUser && setItemHiddenForCurrentUser('products', p.id, !hidden); }}
                    >
                      <Icon name={hidden ? 'eyeOff' : 'eye'} size={14} />
                    </button>
                    <div className="product-card-img">
                      <img src={p.img} alt={lp.name} />
                      <span className="product-card-badge" style={{background: 'color-mix(in oklch, ' + brand.color + ' 15%, transparent)', color: brand.color}}>
                        {p.badge}
                      </span>
                    </div>
                    <div className="product-card-body">
                      <div className={'product-card-brand ' + p.brand}>{brand.label}</div>
                      <div className="product-card-name">{lp.name}</div>
                      <div className="product-card-desc">{lp.desc}</div>
                      <div className="product-card-footer">
                        <span>{p.area}</span>
                        <span className="price">{lp.price || p.price}</span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        )}

        {tab === 'brands' && (
          <div style={{display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(280px, 1fr))', gap:14}}>
            {brands.map(b => (
              <div key={b.id} className="product-card" style={{padding:20}} onClick={() => setEditing({ kind: 'brand', item: b })}>
                <div style={{display:'flex', alignItems:'center', gap:12, marginBottom:12}}>
                  <div style={{width:40, height:40, borderRadius:'var(--r-sm)', background:'color-mix(in oklch, ' + b.color + ' 15%, transparent)', display:'grid', placeItems:'center', color:b.color, fontWeight:700}}>
                    {(b.logoText || b.label || '?')[0]}
                  </div>
                  <div>
                    <div style={{fontSize:15, fontWeight:600, letterSpacing:'-0.01em'}}>{b.label}</div>
                    <div style={{fontSize:11, color:'var(--text-muted)', fontFamily:'var(--font-mono)'}}>{b.id}</div>
                  </div>
                </div>
                <div style={{fontSize:12, color:'var(--text-muted)', marginBottom:10}}>5 idiomas configurados · {products.filter(p => p.brand === b.id).length} productos</div>
                <div style={{display:'flex', gap:6, flexWrap:'wrap'}}>
                  {['ES','FR','DE','EN','NL'].map(l => (
                    <span key={l} style={{fontSize:10, fontFamily:'var(--font-mono)', padding:'2px 7px', background:'var(--bg-sunken)', borderRadius:4, color:'var(--text-muted)'}}>{l}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        {tab === 'texts' && (
          <>
          {renderToolbar('Buscar textos…')}
          <div style={{display:'flex', flexDirection:'column', gap:8}}>
            {filteredTexts.map(t => {
              const brand = resolveBrand(t.brand);
              const hidden = isItemHidden && isItemHidden('prewrittenTexts', t.id);
              return (
                <div key={t.id} className={'product-card' + (hidden ? ' hidden-for-user' : '')} style={{padding:16, display:'grid', gridTemplateColumns:'auto 36px 1fr auto', gap:14, alignItems:'center', cursor:'pointer'}} onClick={() => setEditing({ kind: 'text', item: t })}>
                  <button className="card-visibility-btn inline" title={hidden ? 'Mostrarme' : 'Ocultarme'}
                    onClick={e => { e.stopPropagation(); setItemHiddenForCurrentUser && setItemHiddenForCurrentUser('prewrittenTexts', t.id, !hidden); }}>
                    <Icon name={hidden ? 'eyeOff' : 'eye'} size={13} />
                  </button>
                  <div className={'lib-icon ' + t.brand} style={{width:36, height:36}}>{t.icon}</div>
                  <div>
                    <div style={{fontSize:14, fontWeight:500, marginBottom:3}}>{L(t, 'name')}</div>
                    <div style={{fontSize:12, color:'var(--text-muted)', lineHeight:1.5}}>{L(t, 'text')}</div>
                  </div>
                  <div style={{display:'flex', gap:4}}>
                    <span style={{fontSize:10, fontFamily:'var(--font-mono)', padding:'3px 8px', background:'color-mix(in oklch, ' + brand.color + ' 12%, transparent)', color:brand.color, borderRadius:4, fontWeight:500}}>{brand.label}</span>
                  </div>
                </div>
              );
            })}
          </div>
          </>
        )}

        {tab === 'templates' && (
          <>
          {renderToolbar('Buscar plantillas…')}
          <div style={{display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(320px, 1fr))', gap:14}}>
            {filteredTemplates.map(t => {
              const brand = resolveBrand(t.brand);
              const colorClassMap = { blue:'oklch(60% 0.18 255)', purple:'oklch(60% 0.18 295)', orange:'oklch(65% 0.17 45)', teal:'oklch(60% 0.12 180)', gray:'oklch(70% 0.02 250)' };
              const tplColor = colorClassMap[t.colorClass || 'gray'];
              const hidden = isItemHidden && isItemHidden('templates', t.id);
              return (
                <div key={t.id} className={'product-card' + (hidden ? ' hidden-for-user' : '')} style={{padding:18, cursor:'pointer', position:'relative', overflow:'hidden'}} onClick={() => setEditing({ kind: 'template', item: t })}>
                  <button className="card-visibility-btn" title={hidden ? 'Mostrarme' : 'Ocultarme'}
                    onClick={e => { e.stopPropagation(); setItemHiddenForCurrentUser && setItemHiddenForCurrentUser('templates', t.id, !hidden); }}>
                    <Icon name={hidden ? 'eyeOff' : 'eye'} size={14} />
                  </button>
                  <div style={{position:'absolute', left:0, top:0, bottom:0, width:4, background:tplColor}}/>
                  <div style={{display:'flex', alignItems:'start', justifyContent:'space-between', marginBottom:10}}>
                    <div style={{width:40, height:40, borderRadius:'var(--r-sm)', background:'color-mix(in oklch, ' + brand.color + ' 15%, transparent)', display:'grid', placeItems:'center', color:brand.color}}>
                      <Icon name="template" size={18}/>
                    </div>
                    <div style={{display:'flex', flexDirection:'column', alignItems:'flex-end', gap:4}}>
                      <span className="mono" style={{fontSize:10, color:'var(--text-muted)'}}>{((t.blocks && t.blocks.length) || (t.compositorBlocks && t.compositorBlocks.length) || 0)} bloques</span>
                      {t.colorClass && (
                        <span style={{fontSize:9, fontFamily:'var(--font-mono)', textTransform:'uppercase', letterSpacing:1, color:tplColor, fontWeight:700}}>● {t.colorClass}</span>
                      )}
                    </div>
                  </div>
                  <div style={{fontSize:16, fontWeight:600, marginBottom:4, letterSpacing:'-0.01em'}}>{L(t, 'name')}</div>
                  <div style={{fontSize:12, color:'var(--text-muted)', lineHeight:1.5, marginBottom:12}}>{L(t, 'desc')}</div>
                  <div style={{display:'flex', gap:6, paddingTop:12, borderTop:'1px solid var(--border)'}}>
                    <button
                      className="btn btn-outline"
                      style={{fontSize:12, padding:'5px 10px'}}
                      onClick={e => { e.stopPropagation(); onLoadTemplateInCompositor && onLoadTemplateInCompositor(t.id); }}
                      title="Abrir esta plantilla en el Compositor para editar su contenido"
                    >
                      <Icon name="layers" size={11}/> Editar contenido
                    </button>
                    <button
                      className="btn btn-ghost"
                      style={{fontSize:12, padding:'5px 10px'}}
                      onClick={e => { e.stopPropagation(); setEditing({ kind: 'template', item: t }); }}
                    >
                      Estructura
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
          </>
        )}

        {tab === 'blocks' && (
          <>
          {renderToolbar('Buscar bloques…')}
          <div style={{display:'flex', flexDirection:'column', gap:8}}>
            {filteredBlocks.map(b => {
              const brand = resolveBrand(b.brand);
              const hidden = isItemHidden && isItemHidden('standaloneBlocks', b.id);
              return (
                <div key={b.id} className={'product-card' + (hidden ? ' hidden-for-user' : '')} style={{padding:16, display:'grid', gridTemplateColumns:'auto 40px 1fr auto auto', gap:14, alignItems:'center', cursor:'pointer'}} onClick={() => setEditing({ kind: 'standalone', item: b })}>
                  <button className="card-visibility-btn inline" title={hidden ? 'Mostrarme' : 'Ocultarme'}
                    onClick={e => { e.stopPropagation(); setItemHiddenForCurrentUser && setItemHiddenForCurrentUser('standaloneBlocks', b.id, !hidden); }}>
                    <Icon name={hidden ? 'eyeOff' : 'eye'} size={13} />
                  </button>
                  <div className={'lib-icon ' + b.brand} style={{width:40, height:40, fontSize:16}}>{b.icon}</div>
                  <div>
                    <div style={{fontSize:14, fontWeight:500}}>{L(b, 'title')}</div>
                    <div style={{fontSize:11, color:'var(--text-muted)'}} className="serif">{L(b, 'desc') || b.section}</div>
                  </div>
                  <span style={{fontSize:11, fontFamily:'var(--font-mono)', color:'var(--text-muted)'}}>{b.type || b.blockType}</span>
                  <button className="btn btn-ghost" style={{fontSize:12}} onClick={e => { e.stopPropagation(); setEditing({ kind: 'standalone', item: b }); }}>Editar</button>
                </div>
              );
            })}
          </div>
          </>
        )}

        {tab === 'composed' && (
          <>
          {renderToolbar('Buscar bloques compuestos…')}
          <div style={{display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(320px, 1fr))', gap:14}}>
            {filteredComposed.map(c => {
              const cBrand = c.brand || c.brandStrip;
              const brand = resolveBrand(cBrand);
              const colorClassMap = { blue:'oklch(60% 0.18 255)', purple:'oklch(60% 0.18 295)', orange:'oklch(65% 0.17 45)', teal:'oklch(60% 0.12 180)', gray:'oklch(70% 0.02 250)' };
              const tag = colorClassMap[c.colorTag || 'gray'];
              const hidden = isItemHidden && isItemHidden('composedBlocks', c.id);
              const productCount = Array.isArray(c.products) ? c.products.length : 0;
              return (
                <div key={c.id} className={'product-card' + (hidden ? ' hidden-for-user' : '')} style={{padding:18, cursor:'pointer', position:'relative', overflow:'hidden'}} onClick={() => setEditing({ kind: 'composed', item: c })}>
                  <button className="card-visibility-btn" title={hidden ? 'Mostrarme' : 'Ocultarme'}
                    onClick={e => { e.stopPropagation(); setItemHiddenForCurrentUser && setItemHiddenForCurrentUser('composedBlocks', c.id, !hidden); }}>
                    <Icon name={hidden ? 'eyeOff' : 'eye'} size={14} />
                  </button>
                  <div style={{position:'absolute', left:0, top:0, bottom:0, width:4, background:tag}}/>
                  <div style={{display:'flex', alignItems:'start', justifyContent:'space-between', marginBottom:10}}>
                    <div style={{width:40, height:40, borderRadius:'var(--r-sm)', background:'color-mix(in oklch, ' + brand.color + ' 15%, transparent)', display:'grid', placeItems:'center', color:brand.color}}>
                      <Icon name="box" size={18}/>
                    </div>
                    <div style={{display:'flex', flexDirection:'column', alignItems:'flex-end', gap:4}}>
                      <span className="mono" style={{fontSize:10, color:'var(--text-muted)'}}>{productCount} prod · {c.blockType}</span>
                      {c.priceRange && (
                        <span style={{fontSize:10, color:'var(--text-muted)', fontStyle:'italic'}}>{c.priceRange}</span>
                      )}
                    </div>
                  </div>
                  <div style={{fontSize:15, fontWeight:600, marginBottom:4, letterSpacing:'-0.01em'}}>{L(c, 'title')}</div>
                  <div style={{fontSize:12, color:'var(--text-muted)', lineHeight:1.5, marginBottom:10}}>{L(c, 'desc')}</div>
                  <div style={{fontSize:11, color:'var(--text-subtle)', lineHeight:1.5, paddingTop:10, borderTop:'1px solid var(--border)', maxHeight:60, overflow:'hidden', display:'-webkit-box', WebkitLineClamp:3, WebkitBoxOrient:'vertical'}}>
                    {(L(c, 'introText') || '').slice(0, 180)}{(L(c, 'introText') || '').length > 180 ? '…' : ''}
                  </div>
                </div>
              );
            })}
          </div>
          </>
        )}

        {tab === 'ctas' && (
          <>
          {renderToolbar('Buscar CTAs…')}
          <div style={{display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(280px, 1fr))', gap:14}}>
            {filteredCtas.map(c => {
              const hidden = isItemHidden && isItemHidden('ctaBlocks', c.id);
              const bullets = Array.isArray(c.bullets) ? c.bullets.filter(x => x && x.trim()) : [];
              return (
                <div key={c.id} className={'product-card' + (hidden ? ' hidden-for-user' : '')} style={{padding:16, cursor:'pointer', position:'relative'}} onClick={() => setEditing({ kind: 'cta', item: c })}>
                  <button className="card-visibility-btn" title={hidden ? 'Mostrarme' : 'Ocultarme'}
                    onClick={e => { e.stopPropagation(); setItemHiddenForCurrentUser && setItemHiddenForCurrentUser('ctaBlocks', c.id, !hidden); }}>
                    <Icon name={hidden ? 'eyeOff' : 'eye'} size={14} />
                  </button>
                  <div style={{display:'flex', alignItems:'center', gap:10, marginBottom:10}}>
                    <div style={{width:36, height:36, borderRadius:'var(--r-sm)', background: c.bg || '#1d4ed8', display:'grid', placeItems:'center', color: c.color || '#fff'}}>
                      <Icon name="zap" size={16}/>
                    </div>
                    <div style={{minWidth:0, flex:1}}>
                      <div style={{fontSize:13, fontWeight:600, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>{c.name || c.title || c.text || '(sin nombre)'}</div>
                      <div style={{fontSize:10, color:'var(--text-muted)', fontFamily:'var(--font-mono)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>{c.url || 'sin url'}</div>
                    </div>
                  </div>
                  {c.title && <div style={{fontSize:12, fontWeight:500, marginBottom:4}}>{c.title}</div>}
                  {c.subtitle && <div style={{fontSize:11, color:'var(--text-muted)', marginBottom:6, lineHeight:1.45}}>{c.subtitle}</div>}
                  {bullets.length > 0 && (
                    <div style={{fontSize:10, color:'var(--text-muted)', marginBottom:8}}>{bullets.length} bullet{bullets.length === 1 ? '' : 's'}</div>
                  )}
                  <div style={{paddingTop:10, borderTop:'1px solid var(--border)'}}>
                    <span style={{display:'inline-block', padding:'5px 12px', fontSize:11, fontWeight:600, color: c.color || '#fff', background: c.bg || '#1d4ed8', borderRadius:5}}>{c.text || 'Más información'}</span>
                  </div>
                </div>
              );
            })}
          </div>
          </>
        )}

        {tab === 'users' && isAdmin && (
          <UsersPanel users={users} setAppState={setAppState} currentUser={currentUser} setEditing={setEditing} />
        )}

        {tab === 'mytone' && currentUser && !isAdmin && (
          <MyToneIaPanel currentUser={currentUser} setAppState={setAppState} />
        )}

        {tab === 'myaccount' && currentUser && !isAdmin && (
          <MyAccountPanel currentUser={currentUser} setAppState={setAppState} />
        )}

        {tab === 'images' && isAdmin && (
          <ImageLibraryAdminPanel appState={appState} setAppState={setAppState} />
        )}

        {tab === 'activity' && isAdmin && (
          <ActivityPanel appState={appState} setAppState={setAppState} />
        )}

        {tab === 'ai' && <AISettingsPanel appState={appState} setAppState={setAppState} />}

        {tab === 'settings' && (
          <SettingsPanel appState={appState} setAppState={setAppState} />
        )}
      </main>

      {editing && <BackofficeDrawer editing={editing} appState={appState} onClose={() => setEditing(null)} onSave={onSave} />}
    </div>
  );
}

function BackofficeDrawer({ editing, appState, onClose, onSave }) {
  const { kind, item } = editing;
  const [lang, setLang] = React.useState('es');
  const [data, setData] = React.useState(item);

  // Track de cambios sin guardar — comparación shallow JSON entre el `data`
  // en curso y el `item` original. Se evalúa en cada render: barato porque
  // los items rara vez son enormes (un template con 50 bloques son ~30KB
  // de JSON). El close handler usa esto para mostrar `confirm()` cuando
  // el user va a perder cambios. Antes click fuera del drawer / X / Esc /
  // overlay descartaban silenciosamente todo lo tipeado.
  const dirty = React.useMemo(() => {
    try { return JSON.stringify(data) !== JSON.stringify(item); }
    catch (e) { return true; }
  }, [data, item]);
  const guardedClose = React.useCallback(() => {
    if (dirty) {
      const ok = window.confirm('Tienes cambios sin guardar.\n\n¿Cerrar y descartarlos?');
      if (!ok) return;
    }
    onClose();
  }, [dirty, onClose]);
  // Esc también pasa por el guardado.
  React.useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') guardedClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [guardedClose]);
  // Para que el preview en vivo refleje los compositorBlocks RECIÉN
  // editados, "inyectamos" el data en curso en una copia del appState
  // antes de pasarla al BoEditPreviewPane. Sin esto, el preview leería
  // el composedBlocks/templates ya guardado y no mostraría los cambios
  // hasta hacer Guardar.
  const previewState = React.useMemo(() => {
    if (!appState || !data) return appState || {};
    if (kind === 'composed') {
      const list = (appState.composedBlocks || []).slice();
      const idx = list.findIndex(c => c.id === data.id);
      if (idx >= 0) list[idx] = data; else list.push(data);
      return Object.assign({}, appState, { composedBlocks: list });
    }
    if (kind === 'template') {
      const list = (appState.templates || []).slice();
      const idx = list.findIndex(t => t.id === data.id);
      if (idx >= 0) list[idx] = data; else list.push(data);
      return Object.assign({}, appState, { templates: list });
    }
    return appState;
  }, [appState, data, kind]);

  const titleByKind = {
    product: editing.isNew ? 'Nuevo producto' : 'Editar producto',
    brand: editing.isNew ? 'Nueva marca' : 'Editar marca',
    text: editing.isNew ? 'Nuevo texto' : 'Editar texto',
    template: editing.isNew ? 'Nueva plantilla' : 'Editar plantilla',
    standalone: editing.isNew ? 'Nuevo bloque' : 'Editar bloque',
    composed: editing.isNew ? 'Nuevo compuesto' : 'Editar compuesto',
    cta: editing.isNew ? 'Nuevo CTA' : 'Editar CTA',
    user: editing.isNew ? 'Nuevo usuario' : 'Editar usuario',
  };

  // Plantillas y bloques compuestos abren un drawer ancho con split
  // editor + preview en vivo (mismo estilo que el composer). El resto de
  // tipos siguen con el drawer normal de 540px.
  const isWide = kind === 'template' || kind === 'composed';
  const previewBlocks = isWide ? (data.compositorBlocks || []) : null;
  // Templates se editan en idioma neutral (el lang switch sirve solo para
  // ver cómo queda el preview). Para composed sí afecta al editor (el
  // título/desc tienen i18n por idioma).
  const showLangPill = ['product','text','composed','template'].includes(kind);
  // Para templates añadimos `lang` al estado local solo para previsualizar
  // en el idioma actual del editor.
  if (kind === 'template') {
    // No-op — usamos `lang` del padre (state interno del drawer wrapper).
  }

  return (
    <>
      <div className="bo-drawer-overlay" onClick={guardedClose} />
      <div className={'bo-drawer' + (isWide ? ' wide' : '')} onClick={e => e.stopPropagation()}>
        <div className="bo-drawer-header">
          <div style={{flex:1, minWidth:0}}>
            <div style={{fontSize:10, fontWeight:600, textTransform:'uppercase', letterSpacing:'0.1em', color:'var(--text-muted)', marginBottom:4}}>{titleByKind[kind]}</div>
            <div className="bo-drawer-title">
              {data.name || data.label || data.title}
              {dirty && <span style={{marginLeft:10, fontSize:11, fontWeight:600, color:'var(--accent)', fontStyle:'normal', letterSpacing:0.5}} title="Hay cambios sin guardar">●</span>}
            </div>
          </div>
          {showLangPill && (
            <div className="lang-pill">
              {['es','fr','de','en','nl'].map(l => (
                <button key={l} className={lang === l ? 'active' : ''} onClick={() => setLang(l)}>{l.toUpperCase()}</button>
              ))}
            </div>
          )}
          <button className="icon-btn" onClick={guardedClose}><Icon name="x" size={16}/></button>
        </div>

        {isWide ? (
          <div className="bo-drawer-split">
            <div className="bo-drawer-pane-editor">
              {kind === 'template' && (
                <TemplateBOEdit data={data} setData={setData} lang={lang} />
              )}
              {kind === 'composed' && (
                <ComposedBOEdit data={data} setData={setData} lang={lang} />
              )}
            </div>
            <div className="bo-drawer-pane-preview">
              <BoEditPreviewPane blocks={previewBlocks} appState={previewState} lang={lang} />
            </div>
          </div>
        ) : (
          <div className="bo-drawer-body">
            {kind === 'product' && (
              <ProductBOEdit data={data} setData={setData} lang={lang} />
            )}
            {kind === 'brand' && (
              <BrandBOEdit data={data} setData={setData} />
            )}
            {kind === 'text' && (
              <TextBOEdit data={data} setData={setData} lang={lang} />
            )}
            {kind === 'standalone' && (
              <StandaloneBOEdit data={data} setData={setData} />
            )}
            {kind === 'cta' && (
              <CtaSavedBOEdit data={data} setData={setData} />
            )}
            {kind === 'user' && (
              <UserBOEdit data={data} setData={setData} />
            )}
          </div>
        )}

        <div className="bo-drawer-footer">
          <button className="btn btn-ghost danger" style={{color:'var(--danger)', marginRight:'auto'}}>
            <Icon name="trash" size={13}/> Eliminar
          </button>
          <button className="btn btn-ghost" onClick={guardedClose}>Cancelar</button>
          <button className="btn btn-primary" onClick={() => onSave ? onSave(kind, data) : onClose()}>
            <Icon name="zap" size={13}/> Guardar cambios
          </button>
        </div>
      </div>
    </>
  );
}

function ProductBOEdit({ data, setData, lang }) {
  // Spanish (base) writes to top-level fields. Other languages write to
  // data.i18n[lang].{desc,feat1,feat2,price,link,badge} — that's the schema
  // the email-gen + getLocalizedProduct expect. Name + image + brand + area
  // + badge colours are NOT translated (the model is always the same across
  // languages).
  const TRANSLATABLE = ['desc','feat1','feat2','price','link','badge'];
  const isBase = lang === 'es';
  const i18nAll = data.i18n || {};
  const tr = i18nAll[lang] || {};

  const setBase = (k, v) => setData({...data, [k]: v});

  const trVal = (k) => (isBase ? (data[k] ?? '') : (tr[k] ?? ''));
  const setTr = (k, v) => {
    if (isBase) { setData({...data, [k]: v}); return; }
    const next = {...tr};
    if (v === '' || v == null) delete next[k]; else next[k] = v;
    const nextI18n = {...i18nAll, [lang]: next};
    if (Object.keys(next).length === 0) delete nextI18n[lang];
    setData({...data, i18n: nextI18n});
  };

  const placeholder = (k) => isBase ? '' : (data[k] || '');

  // Translation status — a language counts as "translated" if its i18n entry
  // has *any* value. ES is always considered translated (it is the base).
  const isTranslated = (l) => {
    if (l === 'es') return true;
    const t = i18nAll[l];
    return !!(t && TRANSLATABLE.some(k => t[k]));
  };

  return (
    <>
      <div className="field">
        <div className="field-label-row">
          <label className="field-label">Imagen</label>
          <span style={{fontSize:10, color:'var(--text-subtle)'}}>común a todos los idiomas</span>
        </div>
        <div style={{display:'grid', gridTemplateColumns:'120px 1fr', gap:14, alignItems:'start'}}>
          <div style={{aspectRatio:'1', background:'var(--bg-sunken)', borderRadius:'var(--r-sm)', padding:8, display:'grid', placeItems:'center', border:'1px solid var(--border)'}}>
            {data.img ? <img src={data.img} alt="" style={{maxWidth:'100%', maxHeight:'100%', objectFit:'contain'}} onError={e => { e.target.style.display='none'; }}/> : <Icon name="box" size={32}/>}
          </div>
          <ImageUploadInput value={data.img || ''} onChange={v => setBase('img', v)} prefix={'products/' + (data.id || 'new')} placeholder="https://… o pulsa Subir" brand={data.brand} />
        </div>
      </div>

      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:12, marginTop:14}}>
        <div className="field">
          <label className="field-label">Nombre <span style={{fontSize:10, color:'var(--text-subtle)', fontWeight:400}}>(común)</span></label>
          <input className="input" value={data.name || ''} onChange={e => setBase('name', e.target.value)} />
        </div>
        <div className="field">
          <label className="field-label">Badge ({lang.toUpperCase()})</label>
          <input className="input" value={trVal('badge')} placeholder={placeholder('badge')} onChange={e => setTr('badge', e.target.value)} />
        </div>
      </div>

      <div className="field">
        <div className="field-label-row">
          <label className="field-label">Descripción ({lang.toUpperCase()})</label>
          {!isBase && (
            <button className="field-reset" onClick={() => setTr('desc', '')} title="Borrar override de este idioma (vuelve a usar el español)">
              Restaurar
            </button>
          )}
        </div>
        <textarea className="textarea" rows={3} value={trVal('desc')} placeholder={placeholder('desc')} onChange={e => setTr('desc', e.target.value)} />
      </div>

      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:12}}>
        <div className="field">
          <label className="field-label">Precio ({lang.toUpperCase()})</label>
          <input className="input mono" value={trVal('price')} placeholder={placeholder('price')} onChange={e => setTr('price', e.target.value)} />
        </div>
        <div className="field">
          <label className="field-label">Área <span style={{fontSize:10, color:'var(--text-subtle)', fontWeight:400}}>(común)</span></label>
          <input className="input" value={data.area || ''} onChange={e => setBase('area', e.target.value)} />
        </div>
        <div className="field">
          <label className="field-label">Marca <span style={{fontSize:10, color:'var(--text-subtle)', fontWeight:400}}>(común)</span></label>
          <select className="select" value={data.brand || ''} onChange={e => setBase('brand', e.target.value)}>
            {BRANDS.filter(b => b.id !== 'bomedia').map(b => (
              <option key={b.id} value={b.id}>{b.label}</option>
            ))}
          </select>
        </div>
      </div>

      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:12}}>
        <div className="field">
          <label className="field-label">Feature 1 ({lang.toUpperCase()})</label>
          <input className="input" value={trVal('feat1')} placeholder={placeholder('feat1')} onChange={e => setTr('feat1', e.target.value)} />
        </div>
        <div className="field">
          <label className="field-label">Feature 2 ({lang.toUpperCase()})</label>
          <input className="input" value={trVal('feat2')} placeholder={placeholder('feat2')} onChange={e => setTr('feat2', e.target.value)} />
        </div>
      </div>

      <div className="field">
        <label className="field-label">Enlace ({lang.toUpperCase()})</label>
        <input className="input mono" style={{fontSize:11}} value={trVal('link')} placeholder={placeholder('link')} onChange={e => setTr('link', e.target.value)} />
      </div>

      <div className="field">
        <label className="field-label">Idiomas traducidos</label>
        <div style={{display:'flex', gap:6, flexWrap:'wrap'}}>
          {['es','fr','de','en','nl'].map(l => {
            const ok = isTranslated(l);
            const isActive = l === lang;
            return (
              <span key={l} style={{
                fontSize:11, fontFamily:'var(--font-mono)', padding:'3px 8px', borderRadius:4, fontWeight:500,
                background: ok ? (isActive ? 'var(--success)' : 'color-mix(in oklch, var(--success) 35%, var(--bg-sunken))') : 'var(--bg-sunken)',
                color: ok && isActive ? 'white' : (ok ? 'var(--text)' : 'var(--text-subtle)'),
                border: isActive ? '1px solid var(--accent)' : '1px solid transparent',
              }}>
                {l.toUpperCase()} {ok && '✓'}
              </span>
            );
          })}
        </div>
        <div style={{fontSize:10, color:'var(--text-subtle)', marginTop:6, lineHeight:1.4}}>
          Si dejas un campo vacío en un idioma, el email usará automáticamente el español como fallback.
        </div>
      </div>
    </>
  );
}

function BrandBOEdit({ data, setData }) {
  const set = (k, v) => setData({...data, [k]: v});
  const setLangField = (field, lang, value) => {
    const next = Object.assign({}, data[field] || {}, { [lang]: value });
    set(field, next);
  };
  const liveProducts = (typeof window !== 'undefined' && window.PRODUCTS) || PRODUCTS || [];
  const labelInitial = (data.logoText || data.label || data.id || '?').slice(0, 1);
  const safeColor = data.color || '#94a3b8';

  return (
    <>
      <div style={{display:'grid', gridTemplateColumns:'80px 1fr', gap:14, alignItems:'center', marginBottom:16}}>
        <div style={{width:80, height:80, borderRadius:'var(--r-md)', background:'color-mix(in oklch, ' + safeColor + ' 15%, transparent)', display:'grid', placeItems:'center', color:safeColor, fontWeight:700, fontSize:28, overflow:'hidden'}}>
          {data.logo ? (
            <img src={data.logo} alt="" style={{maxWidth:'90%', maxHeight:'90%', objectFit:'contain'}} />
          ) : (
            <span>{labelInitial}</span>
          )}
        </div>
        <div>
          <div className="field">
            <label className="field-label">Nombre</label>
            <input className="input" value={data.label || ''} onChange={e => set('label', e.target.value)} />
          </div>
        </div>
      </div>

      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:12}}>
        <div className="field">
          <label className="field-label">Texto del logo (fallback)</label>
          <input className="input" value={data.logoText || ''} onChange={e => set('logoText', e.target.value)} />
        </div>
        <div className="field">
          <label className="field-label">Color de marca</label>
          <div style={{display:'flex', gap:6, alignItems:'center'}}>
            <input type="color" value={safeColor} onChange={e => set('color', e.target.value)} style={{width:32, height:30, border:'1px solid var(--border)', borderRadius:4, padding:0, cursor:'pointer'}} />
            <input className="input mono" style={{fontSize:11}} value={data.color || ''} onChange={e => set('color', e.target.value)} />
          </div>
        </div>
      </div>

      <div className="field">
        <label className="field-label">Logo</label>
        <ImageUploadInput value={data.logo || ''} onChange={v => set('logo', v)} prefix={'brands/' + (data.id || 'new')} placeholder="https://…/logo.png" brand={data.id} />
      </div>

      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:12}}>
        <div className="field">
          <label className="field-label">Altura del logo (px)</label>
          <input className="input" value={data.logoHeight || ''} placeholder="22" onChange={e => set('logoHeight', e.target.value)} />
        </div>
        <div className="field">
          <label className="field-label">Color del divider</label>
          <div style={{display:'flex', gap:6, alignItems:'center'}}>
            <input type="color" value={data.divider || '#e2e8f0'} onChange={e => set('divider', e.target.value)} style={{width:32, height:30, border:'1px solid var(--border)', borderRadius:4, padding:0, cursor:'pointer'}} />
            <input className="input mono" style={{fontSize:11}} value={data.divider || ''} onChange={e => set('divider', e.target.value)} />
          </div>
        </div>
      </div>

      <div className="field">
        <label className="field-label">URLs por idioma</label>
        <div style={{display:'flex', flexDirection:'column', gap:6}}>
          {['es','fr','de','en','nl'].map(l => {
            const urlObj = (data.url && typeof data.url === 'object') ? data.url : {};
            return (
              <div key={l} style={{display:'grid', gridTemplateColumns:'36px 1fr', gap:8, alignItems:'center'}}>
                <span className="mono" style={{fontSize:11, color:'var(--text-muted)'}}>{l.toUpperCase()}</span>
                <input
                  className="input mono"
                  style={{fontSize:11}}
                  placeholder={'https://' + (data.id || '') + '.com'}
                  value={urlObj[l] || ''}
                  onChange={e => setLangField('url', l, e.target.value)}
                />
              </div>
            );
          })}
        </div>
      </div>

      <div className="field">
        <label className="field-label">Texto del enlace por idioma</label>
        <div style={{display:'flex', flexDirection:'column', gap:6}}>
          {['es','fr','de','en','nl'].map(l => {
            const labelObj = (data.urlLabel && typeof data.urlLabel === 'object') ? data.urlLabel : {};
            return (
              <div key={l} style={{display:'grid', gridTemplateColumns:'36px 1fr', gap:8, alignItems:'center'}}>
                <span className="mono" style={{fontSize:11, color:'var(--text-muted)'}}>{l.toUpperCase()}</span>
                <input
                  className="input"
                  style={{fontSize:12}}
                  placeholder="ej. boprint.net →"
                  value={labelObj[l] || ''}
                  onChange={e => setLangField('urlLabel', l, e.target.value)}
                />
              </div>
            );
          })}
        </div>
      </div>

      <div className="field">
        <label className="field-label">Uso</label>
        <div style={{fontSize:12, color:'var(--text-muted)', padding:'10px 12px', background:'var(--bg-sunken)', borderRadius:'var(--r-sm)'}}>
          {liveProducts.filter(p => p.brand === data.id).length} productos asignados a esta marca
        </div>
      </div>
    </>
  );
}

function TextBOEdit({ data, setData, lang }) {
  // Spanish (base) writes to `data.text`; other languages write to
  // `data.i18n[lang].text`. Name + icon + brand are common across languages.
  const isBase = lang === 'es';
  const i18nAll = data.i18n || {};
  const tr = i18nAll[lang] || {};

  const setBase = (k, v) => setData({...data, [k]: v});
  const text = isBase ? (data.text || '') : (tr.text || '');
  const setText = (v) => {
    if (isBase) { setData({...data, text: v}); return; }
    const next = {...tr};
    if (v === '') delete next.text; else next.text = v;
    const nextI18n = {...i18nAll, [lang]: next};
    if (Object.keys(next).length === 0) delete nextI18n[lang];
    setData({...data, i18n: nextI18n});
  };

  const isTranslated = (l) => l === 'es' ? true : !!(i18nAll[l] && i18nAll[l].text);

  // Asistente IA — misma popover que el composer e inspector. Permite generar
  // texto desde cero (modo "create") o reescribir el existente (modo "rewrite")
  // en el idioma activo. El resultado se aplica al campo correspondiente
  // (data.text si lang=es, data.i18n[lang].text si no).
  const [aiOpen, setAiOpen] = React.useState(false);
  const applyAi = (newText) => {
    setText(newText);
    setAiOpen(false);
  };

  return (
    <>
      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:12}}>
        <div className="field">
          <label className="field-label">Nombre interno <span style={{fontSize:10, color:'var(--text-subtle)', fontWeight:400}}>(común)</span></label>
          <input className="input" value={data.name || ''} onChange={e => setBase('name', e.target.value)} />
        </div>
        <div className="field">
          <label className="field-label">Icono <span style={{fontSize:10, color:'var(--text-subtle)', fontWeight:400}}>(común)</span></label>
          <input className="input" value={data.icon || ''} onChange={e => setBase('icon', e.target.value)} />
        </div>
      </div>

      <div className="field">
        <label className="field-label">Categoría / marca <span style={{fontSize:10, color:'var(--text-subtle)', fontWeight:400}}>(común)</span></label>
        <select className="select" value={data.brand || 'mix'} onChange={e => setBase('brand', e.target.value)}>
          <option value="mix">Multi-marca</option>
          {BRANDS.map(b => <option key={b.id} value={b.id}>{b.label}</option>)}
        </select>
      </div>

      <div className="field">
        <div className="field-label-row">
          <label className="field-label">Texto ({lang.toUpperCase()})</label>
          <div style={{display:'flex', gap:6, alignItems:'center'}}>
            <button
              className="btn btn-ghost"
              style={{fontSize:11, padding:'3px 10px', background:'linear-gradient(135deg, color-mix(in oklch, #8b5cf6 18%, transparent), color-mix(in oklch, #ec4899 18%, transparent))'}}
              onClick={() => setAiOpen(true)}
              title="Generar/reescribir con IA en el idioma activo"
            >
              <Icon name="sparkles" size={11}/> IA
            </button>
            {!isBase && (
              <button className="field-reset" onClick={() => setText('')} title="Borrar override de este idioma">Restaurar</button>
            )}
          </div>
        </div>
        <textarea className="textarea" rows={6} value={text} placeholder={isBase ? '' : (data.text || '')} onChange={e => setText(e.target.value)} />
        <div className="char-count mono">{text.length} caracteres</div>
        {aiOpen && typeof window.AiTextPopover === 'function' && (
          <window.AiTextPopover
            lang={lang}
            currentText={text}
            onApply={applyAi}
            onClose={() => setAiOpen(false)}
          />
        )}
      </div>

      <div className="field">
        <label className="field-label">Variables detectadas</label>
        <div className="var-chips">
          {(text.match(/\{\{\w+\}\}/g) || []).map((v,i) => (
            <span key={i} className="var-chip mono">{v}</span>
          ))}
          {(text.match(/\{\{\w+\}\}/g) || []).length === 0 && (
            <span style={{fontSize:11, color:'var(--text-subtle)', fontStyle:'italic'}}>Ninguna — usa {"{{nombre}}"} o similar</span>
          )}
        </div>
      </div>

      <div className="field">
        <label className="field-label">Idiomas traducidos</label>
        <div style={{display:'flex', gap:6, flexWrap:'wrap'}}>
          {['es','fr','de','en','nl'].map(l => {
            const ok = isTranslated(l);
            const isActive = l === lang;
            return (
              <span key={l} style={{
                fontSize:11, fontFamily:'var(--font-mono)', padding:'3px 8px', borderRadius:4, fontWeight:500,
                background: ok ? (isActive ? 'var(--success)' : 'color-mix(in oklch, var(--success) 35%, var(--bg-sunken))') : 'var(--bg-sunken)',
                color: ok && isActive ? 'white' : (ok ? 'var(--text)' : 'var(--text-subtle)'),
                border: isActive ? '1px solid var(--accent)' : '1px solid transparent',
              }}>
                {l.toUpperCase()} {ok && '✓'}
              </span>
            );
          })}
        </div>
      </div>
    </>
  );
}

/* Generate a short, human-readable label for a compositorBlocks entry. */
function describeTplBlock(block) {
  if (!block) return 'Bloque vacío';
  switch (block.type) {
    case 'text': {
      const txt = (block.text || '').trim();
      if (!txt) return 'Texto (vacío)';
      return 'Texto · ' + txt.slice(0, 50) + (txt.length > 50 ? '…' : '');
    }
    case 'brand_strip': return 'Brand strip · ' + (block.brand || '?');
    case 'product_single': return 'Producto · ' + (block.product1 || '?');
    case 'product_pair': return '2 productos · ' + (block.product1 || '?') + ' + ' + (block.product2 || '?');
    case 'product_trio': return '3 productos · ' + [block.product1, block.product2, block.product3].filter(Boolean).join(' + ');
    case 'pimpam_hero': return 'Hero · ' + (block.heroTitle || 'sin título');
    case 'pimpam_steps': return 'Pasos PimPam';
    case 'video':
    case 'freebird': return 'Vídeo';
    case 'block': return 'Bloque · ' + (block.id || '?');
    default: return block.type || 'desconocido';
  }
}

function TemplateBOEdit({ data, setData, lang }) {
  const set = (k, v) => setData({...data, [k]: v});
  const compBlocks = Array.isArray(data.compositorBlocks) ? data.compositorBlocks : [];
  const setCompBlocks = (next) => set('compositorBlocks', next);
  const legacyRefs = Array.isArray(data.blocks) ? data.blocks : [];

  return (
    <>
      <div className="field">
        <label className="field-label">Nombre de la plantilla</label>
        <input className="input" value={data.name || ''} onChange={e => set('name', e.target.value)} />
      </div>
      <div className="field">
        <label className="field-label">Descripción</label>
        <textarea className="textarea" rows={2} value={data.desc || ''} onChange={e => set('desc', e.target.value)} />
      </div>
      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:12}}>
        <div className="field">
          <label className="field-label">Marca principal</label>
          <select className="select" value={data.brand || 'mix'} onChange={e => set('brand', e.target.value)}>
            <option value="mix">Multi-marca</option>
            {BRANDS.map(b => <option key={b.id} value={b.id}>{b.label}</option>)}
          </select>
        </div>
        <div className="field">
          <label className="field-label">Color</label>
          <select className="select" value={data.colorClass || 'gray'} onChange={e => set('colorClass', e.target.value)}>
            <option value="blue">Azul</option>
            <option value="purple">Morado</option>
            <option value="orange">Naranja</option>
            <option value="teal">Teal</option>
            <option value="gray">Gris</option>
          </select>
        </div>
      </div>

      {/* Plantillas legacy (con tpl.blocks que son refs a textos/composeds
          antiguos) se muestran como read-only — para editarlas hay que
          abrirlas en el composer y resaverlas como compositorBlocks. */}
      {legacyRefs.length > 0 && compBlocks.length === 0 && (
        <div className="field">
          <label className="field-label">Plantilla legacy · {legacyRefs.length} referencias</label>
          <div style={{display:'flex', flexDirection:'column', gap:4, padding:8, background:'var(--bg-sunken)', borderRadius:'var(--r-sm)'}}>
            {legacyRefs.map((ref, i) => (
              <div key={'legacy-'+i} style={{display:'flex', alignItems:'center', gap:8, padding:'6px 10px', background:'var(--bg-panel)', borderRadius:4, fontSize:12, border:'1px dashed var(--border)', opacity:0.75}}>
                <span className="mono" style={{color:'var(--text-muted)', fontSize:10, minWidth:18}}>{String(i+1).padStart(2,'0')}</span>
                <span style={{flex:1, fontFamily:'var(--font-mono)', fontSize:11}}>ref · {ref}</span>
              </div>
            ))}
            <div style={{padding:'6px 8px', fontSize:11, color:'var(--text-muted)', fontStyle:'italic'}}>
              Para convertir a editable: ábrela en el compositor y guarda los cambios — se materializa como compositorBlocks editables.
            </div>
          </div>
        </div>
      )}

      <CompositorBlocksListEditor
        compBlocks={compBlocks}
        setCompBlocks={setCompBlocks}
        lang={lang}
        label="Bloques de la plantilla"
        defaultBrand={data.brand}
      />
    </>
  );
}

/* List-editor reutilizable para compositorBlocks: lo usan tanto el editor
   de plantillas como el de bloques compuestos para que ambos compartan
   el mismo flujo de edición (lista de hijos + ChildEditor inline + dos
   filas de pickers de añadir). El caller pasa el array, su setter y el
   idioma activo. `defaultBrand` se usa como semilla del brand_strip
   recién creado para que coincida con la marca del item parent. */
function CompositorBlocksListEditor({ compBlocks, setCompBlocks, lang, label, defaultBrand }) {
  const standalones = (typeof window !== 'undefined' && window.STANDALONE_BLOCKS) || STANDALONE_BLOCKS || [];
  const heroStandalones = standalones.filter(s => (s.blockType || s.type) === 'pimpam_hero' && s.visible !== false);
  const defaultHero = heroStandalones[0] || null;

  const updateChild = (idx, patch) => {
    const next = compBlocks.map((b, i) => i === idx ? Object.assign({}, b, patch) : b);
    setCompBlocks(next);
  };
  const removeChild = (idx) => setCompBlocks(compBlocks.filter((_, i) => i !== idx));
  const moveChild = (idx, dir) => {
    const j = idx + dir;
    if (j < 0 || j >= compBlocks.length) return;
    const next = compBlocks.slice();
    [next[idx], next[j]] = [next[j], next[idx]];
    setCompBlocks(next);
  };
  const addChild = (kind) => {
    const factory = {
      text: () => ({ type: 'text', overridesByLang: { es: '' } }),
      brand_strip: () => ({ type: 'brand_strip', brand: defaultBrand && defaultBrand !== 'mix' ? defaultBrand : 'mbo' }),
      product_single: () => ({ type: 'product_single', product1: '' }),
      product_pair: () => ({ type: 'product_pair', product1: '', product2: '' }),
      product_trio: () => ({ type: 'product_trio', product1: '', product2: '', product3: '' }),
      image: () => ({ type: 'image', src: '', alt: '', align: 'center', widthPct: 100 }),
      // Dividers: el renderer (dividerBlockHtml) espera type:'divider' +
      // style:'line/short/dots'. Antes el factory escribía type:'divider_line'
      // literal y caían a default: en el bridge, sin renderizar. Bug fix.
      divider_line: () => ({ type: 'divider', style: 'line', color: '#e2e8f0', paddingV: 24 }),
      divider_short: () => ({ type: 'divider', style: 'short', color: '#cbd5e1', paddingV: 32 }),
      divider_dots: () => ({ type: 'divider', style: 'dots', color: '#94a3b8', paddingV: 28 }),
      video: () => ({ type: 'video', youtubeUrl: '' }),
      pimpam_hero: () => defaultHero
        ? { type: 'pimpam_hero', standaloneId: defaultHero.id, _sourceType: 'standalone', _sourceId: defaultHero.id }
        : { type: 'pimpam_hero', heroTitle: '', heroSubtitle: '', heroBullets: [], heroCtaButtons: [] },
      cta: () => ({ type: 'cta', text: 'Más información', url: '', bg: '#1d4ed8', color: '#ffffff', align: 'center' }),
      section_2col: () => ({ type: 'section', layout: '2col', columns: [{ blocks: [] }, { blocks: [] }] }),
      section_3col: () => ({ type: 'section', layout: '3col', columns: [{ blocks: [] }, { blocks: [] }, { blocks: [] }] }),
    };
    const f = factory[kind];
    if (!f) return;
    setCompBlocks([...compBlocks, f()]);
  };

  return (
    <div className="field">
      <div style={{display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:8}}>
        <label className="field-label" style={{margin:0}}>{label || 'Bloques'} · {compBlocks.length}</label>
      </div>
      <div style={{
        display:'flex', flexDirection:'column', gap:8,
        border:'1px dashed var(--border-strong)', borderRadius:'var(--r-md)',
        padding:10, background:'var(--bg-sunken)',
      }}>
        {compBlocks.length === 0 && (
          <div style={{padding:18, textAlign:'center', color:'var(--text-subtle)', fontSize:12}}>
            Sin bloques. Añade el primero abajo.
          </div>
        )}
        {compBlocks.map((b, i) => (
          <ComposedChildEditor
            key={i}
            block={b}
            index={i}
            total={compBlocks.length}
            lang={lang}
            onUpdate={(patch) => updateChild(i, patch)}
            onRemove={() => removeChild(i)}
            onMove={(dir) => moveChild(i, dir)}
          />
        ))}
      </div>
      <div style={{display:'flex', flexDirection:'column', gap:4, marginTop:8}}>
        <div style={{display:'flex', flexWrap:'wrap', gap:6, alignItems:'center'}}>
          <span style={{fontSize:11, color:'var(--text-muted)', marginRight:4, fontWeight:600, letterSpacing:0.3}}>Contenido:</span>
          <button className="btn btn-ghost" style={{fontSize:11, padding:'4px 10px'}} onClick={() => addChild('text')}><Icon name="text" size={11}/> Texto</button>
          <button className="btn btn-ghost" style={{fontSize:11, padding:'4px 10px'}} onClick={() => addChild('product_single')}><Icon name="box" size={11}/> 1 producto</button>
          <button className="btn btn-ghost" style={{fontSize:11, padding:'4px 10px'}} onClick={() => addChild('product_pair')}><Icon name="box" size={11}/> 2 productos</button>
          <button className="btn btn-ghost" style={{fontSize:11, padding:'4px 10px'}} onClick={() => addChild('product_trio')}><Icon name="box" size={11}/> 3 productos</button>
          <button className="btn btn-ghost" style={{fontSize:11, padding:'4px 10px'}} onClick={() => addChild('image')}><Icon name="copy" size={11}/> Imagen</button>
          <button className="btn btn-ghost" style={{fontSize:11, padding:'4px 10px'}} onClick={() => addChild('video')}><Icon name="zap" size={11}/> Vídeo</button>
        </div>
        <div style={{display:'flex', flexWrap:'wrap', gap:6, alignItems:'center'}}>
          <span style={{fontSize:11, color:'var(--text-muted)', marginRight:4, fontWeight:600, letterSpacing:0.3}}>Estructura:</span>
          <button className="btn btn-ghost" style={{fontSize:11, padding:'4px 10px'}} onClick={() => addChild('pimpam_hero')} disabled={!defaultHero} title={!defaultHero ? 'No hay heros sueltos creados — crea uno primero en "Bloques sueltos"' : ''}><Icon name="zap" size={11}/> Hero</button>
          <button className="btn btn-ghost" style={{fontSize:11, padding:'4px 10px'}} onClick={() => addChild('cta')}><Icon name="zap" size={11}/> CTA</button>
          <button className="btn btn-ghost" style={{fontSize:11, padding:'4px 10px'}} onClick={() => addChild('brand_strip')}><Icon name="palette" size={11}/> Strip de marca</button>
          <button className="btn btn-ghost" style={{fontSize:11, padding:'4px 10px'}} onClick={() => addChild('section_2col')}><Icon name="grid" size={11}/> 2 columnas</button>
          <button className="btn btn-ghost" style={{fontSize:11, padding:'4px 10px'}} onClick={() => addChild('section_3col')}><Icon name="grid" size={11}/> 3 columnas</button>
          <button className="btn btn-ghost" style={{fontSize:11, padding:'4px 10px'}} onClick={() => addChild('divider_line')}>— Divisor</button>
        </div>
      </div>
    </div>
  );
}

/* Pane lateral con preview en vivo del HTML del email. Lo usan los
   editores de plantilla y compuesto en modo wide-drawer para que el
   admin vea cómo queda lo que está montando sin tener que cerrar y
   reabrir el composer. Renderiza los compositorBlocks via la misma
   función que el composer (renderEmailHtml) para que el resultado sea
   100% fiel. Toggle desktop/mobile + abrir en pestaña nueva. */
function BoEditPreviewPane({ blocks, appState, lang }) {
  const [device, setDevice] = React.useState('desktop');
  // Debounce: el render del HTML se recalcula 250ms tras dejar de tipear
  // en vez de en cada keystroke. Antes el iframe parpadeaba en blanco
  // entre cada tecla con plantillas grandes (8+ bloques con imágenes)
  // porque doc.write se ejecutaba sincrónico en cada cambio. Ahora con
  // srcDoc + debounce, el iframe se actualiza solo cuando hay una pausa.
  // Apr 2026 audit fix.
  const debouncedDeps = useDebounced({ blocks, appState, lang }, 250);
  const html = React.useMemo(() => {
    const fn = (typeof window !== 'undefined' && window.renderEmailHtml) || (typeof renderEmailHtml === 'function' ? renderEmailHtml : null);
    if (fn) {
      try {
        return fn(debouncedDeps.blocks || [], debouncedDeps.appState || {}, debouncedDeps.lang || 'es');
      } catch (e) {
        return '<html><body style="padding:20px;font-family:system-ui;color:#dc2626">Error al renderizar: ' + (e.message || e) + '</body></html>';
      }
    }
    return '<html><body><pre>' + JSON.stringify(debouncedDeps.blocks, null, 2) + '</pre></body></html>';
  }, [debouncedDeps.blocks, debouncedDeps.appState, debouncedDeps.lang]);

  // Indicador "actualizando…" si el debounce está pendiente: comparamos
  // los deps actuales con los debounced — si difieren, hay edits in flight.
  const updating = blocks !== debouncedDeps.blocks
    || appState !== debouncedDeps.appState
    || lang !== debouncedDeps.lang;

  // Para abrir en pestaña nueva usamos el HTML actual (no el debounced) —
  // el user pulsa el botón porque quiere ver el resultado AHORA.
  const openInNewTab = () => {
    const fn = (typeof window !== 'undefined' && window.renderEmailHtml) || null;
    const liveHtml = fn ? fn(blocks || [], appState || {}, lang || 'es') : html;
    const w = window.open('about:blank', '_blank');
    if (w) { w.document.write(liveHtml); w.document.close(); }
  };

  return (
    <div style={{display:'flex', flexDirection:'column', height:'100%', background:'var(--bg-sunken)'}}>
      <div style={{padding:'10px 14px', borderBottom:'1px solid var(--border)', display:'flex', alignItems:'center', gap:8, background:'var(--bg-panel)'}}>
        <Icon name="eye" size={13}/>
        <strong style={{fontSize:12, flex:1}}>Preview en vivo {updating && <span style={{fontSize:10, color:'var(--text-subtle)', fontWeight:400, marginLeft:4}}>· actualizando…</span>}</strong>
        <span className="mono" style={{fontSize:10, color:'var(--text-subtle)'}}>{(blocks || []).length} bloques · {(lang || 'es').toUpperCase()}</span>
        <div className="device-toggle">
          <button className={'icon-btn' + (device === 'desktop' ? ' active' : '')} onClick={() => setDevice('desktop')} title="Desktop">
            <Icon name="monitor" size={12}/>
          </button>
          <button className={'icon-btn' + (device === 'mobile' ? ' active' : '')} onClick={() => setDevice('mobile')} title="Móvil">
            <Icon name="smartphone" size={12}/>
          </button>
        </div>
        <button className="icon-btn" onClick={openInNewTab} title="Abrir en pestaña nueva">
          <Icon name="share" size={12}/>
        </button>
      </div>
      <div style={{flex:1, overflow:'auto', padding:14, display:'flex', justifyContent:'center'}}>
        {(blocks || []).length === 0 ? (
          <div style={{alignSelf:'center', padding:40, textAlign:'center', color:'var(--text-muted)', fontSize:12}}>
            Añade bloques en el editor de la izquierda · el preview se actualiza en vivo.
          </div>
        ) : (
          /* sandbox="" + srcDoc: contiene scripts y styles maliciosos que
             se hayan colado por sanitizeHtml; el iframe queda como origen
             único opaco sin acceso a localStorage/Supabase del padre. */
          <iframe
            title="Preview email"
            srcDoc={html}
            sandbox=""
            style={{
              width: device === 'mobile' ? 380 : '100%',
              maxWidth: device === 'mobile' ? 380 : 720,
              height: '100%',
              minHeight: 400,
              border: '1px solid var(--border)',
              borderRadius: 'var(--r-sm)',
              background: '#fff',
            }}
          />
        )}
      </div>
    </div>
  );
}

/* Hook genérico de debounce de un valor — devuelve el valor "asentado"
   tras `delay` ms de inactividad. Lo usamos para evitar recalcular el
   HTML del preview en cada keystroke. */
function useDebounced(value, delay) {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

/* Editor for composedBlocks — desde v5 los compuestos son una agrupación
   ordenada de bloques v3 individuales (idéntica forma a la de las
   plantillas). Aquí se editan como una "mini-canvas": metadata arriba
   (título / descripción / marca / color tag) y debajo una lista de
   bloques que se pueden añadir, reordenar, editar y eliminar uno a uno.
   Los heroes/pasos pre-existentes han desaparecido como checkboxes
   especiales — si quieres uno, lo añades como un bloque más. */
function ComposedBOEdit({ data, setData, lang }) {
  const set = (k, v) => setData({...data, [k]: v});
  const trVal = (field) => {
    if (lang === 'es' || !data.i18n || !data.i18n[lang]) return data[field] || '';
    return (data.i18n[lang][field] !== undefined && data.i18n[lang][field] !== null) ? data.i18n[lang][field] : (data[field] || '');
  };
  const setTr = (field, value) => {
    if (lang === 'es') { set(field, value); return; }
    const i18n = Object.assign({}, data.i18n || {});
    i18n[lang] = Object.assign({}, i18n[lang] || {}, { [field]: value });
    set('i18n', i18n);
  };
  const placeholder = (field) => (lang === 'es' || !data[field]) ? '' : data[field];

  const allBrands = (typeof window !== 'undefined' && window.BRANDS) || BRANDS || [];
  const compBlocks = Array.isArray(data.compositorBlocks) ? data.compositorBlocks : [];
  const setCompBlocks = (next) => set('compositorBlocks', next);

  return (
    <>
      <div className="field">
        <label className="field-label">Título ({lang.toUpperCase()})</label>
        <input className="input" value={trVal('title')} placeholder={placeholder('title')} onChange={e => setTr('title', e.target.value)} />
      </div>
      <div className="field">
        <label className="field-label">Descripción corta ({lang.toUpperCase()})</label>
        <input className="input" value={trVal('desc')} placeholder={placeholder('desc')} onChange={e => setTr('desc', e.target.value)} />
      </div>

      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:12}}>
        <div className="field">
          <label className="field-label">Marca <span style={{fontSize:10, color:'var(--text-subtle)', fontWeight:400}}>(común)</span></label>
          <select className="select" value={data.brand || 'mix'} onChange={e => set('brand', e.target.value)}>
            <option value="mix">Mixto</option>
            {allBrands.filter(b => b.id !== 'bomedia').map(b => <option key={b.id} value={b.id}>{b.label}</option>)}
          </select>
        </div>
        <div className="field">
          <label className="field-label">Rango de precio <span style={{fontSize:10, color:'var(--text-subtle)', fontWeight:400}}>(común)</span></label>
          <input className="input" value={data.priceRange || ''} placeholder="ej. 9.300 €" onChange={e => set('priceRange', e.target.value)} />
        </div>
        <div className="field">
          <label className="field-label">Color tag <span style={{fontSize:10, color:'var(--text-subtle)', fontWeight:400}}>(común)</span></label>
          <select className="select" value={data.colorTag || 'gray'} onChange={e => set('colorTag', e.target.value)}>
            <option value="blue">Azul</option>
            <option value="purple">Morado</option>
            <option value="orange">Naranja</option>
            <option value="teal">Teal</option>
            <option value="gray">Gris</option>
          </select>
        </div>
      </div>

      <CompositorBlocksListEditor
        compBlocks={compBlocks}
        setCompBlocks={setCompBlocks}
        lang={lang}
        label="Bloques que componen esta agrupación"
        defaultBrand={data.brand}
      />
    </>
  );
}

/* Mini-editor inline para un bloque hijo dentro de un compuesto. Soporta
   los mismos tipos que el composer: texto (con picker de pre-escritos +
   asistente IA), productos (1/2/3), strip de marca, imagen, divisores,
   vídeo, hero (linkado a un standalone), CTA y secciones de columnas
   (recursivas — la sección renderiza un mini-editor por cada columna). */
function ComposedChildEditor({ block, index, total, lang, onUpdate, onRemove, onMove }) {
  const allProducts = (typeof window !== 'undefined' && window.PRODUCTS) || PRODUCTS || [];
  const allBrands = (typeof window !== 'undefined' && window.BRANDS) || BRANDS || [];
  const allTexts = (typeof window !== 'undefined' && window.PREWRITTEN_TEXTS) || PREWRITTEN_TEXTS || [];
  const allStandalones = (typeof window !== 'undefined' && window.STANDALONE_BLOCKS) || STANDALONE_BLOCKS || [];
  const heroStandalones = allStandalones.filter(s => (s.blockType || s.type) === 'pimpam_hero' && s.visible !== false);

  const [aiOpen, setAiOpen] = React.useState(false);
  const [showSections, setShowSections] = React.useState(false);

  const typeLabel = {
    text: 'Texto',
    brand_strip: 'Strip de marca',
    product_single: '1 producto',
    product_pair: '2 productos',
    product_trio: '3 productos',
    image: 'Imagen',
    // Tipos divider_* legacy se siguen reconociendo por si un dato viejo
    // los trae así. El nuevo schema es type:'divider' + style.
    divider_line: 'Divisor · línea',
    divider_short: 'Divisor · línea corta',
    divider_dots: 'Divisor · puntos',
    divider: 'Divisor · ' + (block.style === 'short' ? 'línea corta' : block.style === 'dots' ? 'puntos' : 'línea'),
    video: 'Vídeo',
    freebird: 'Vídeo',
    pimpam_hero: 'Hero',
    pimpam_steps: 'Pasos',
    cta: 'CTA',
    section: (block.layout === '3col' ? '3 columnas' : '2 columnas'),
  }[block.type] || block.type;

  const visibleProducts = allProducts.filter(p => p.visible !== false);
  const productOpts = (
    <>
      <option value="">— Selecciona —</option>
      {visibleProducts.map(p => <option key={p.id} value={p.id}>{p.name} ({p.brand})</option>)}
    </>
  );

  // Para 'text' usamos overridesByLang (mismo formato que el resto del v3).
  // Si el bloque viene en formato legacy con `text` + `i18n`, lo migramos
  // de vuelta al formato moderno cuando el user edita.
  const linkedText = block.textId ? allTexts.find(t => t.id === block.textId) : null;
  // Resolvemos el texto a mostrar: si hay textId y NO hay override del idioma
  // activo, mostramos el texto del source (read-only por defecto). Si el user
  // escribe algo, se vuelca a overridesByLang[lang] (override por idioma).
  const resolveSourceText = (t, l) => {
    if (!t) return '';
    if (l !== 'es' && t.i18n && t.i18n[l] && t.i18n[l].text != null) return t.i18n[l].text;
    return t.text || '';
  };
  const textValue = (() => {
    if (block.overridesByLang && block.overridesByLang[lang] != null) return block.overridesByLang[lang];
    if (linkedText) return resolveSourceText(linkedText, lang);
    if (lang !== 'es' && block.i18n && block.i18n[lang] && block.i18n[lang].text != null) return block.i18n[lang].text;
    if (lang === 'es') return block.text || '';
    return '';
  })();
  const setTextValue = (v) => {
    const overridesByLang = Object.assign({}, block.overridesByLang || {});
    if (lang === 'es') {
      overridesByLang.es = v;
    } else {
      overridesByLang.es = overridesByLang.es != null ? overridesByLang.es : (block.text || '');
      overridesByLang[lang] = v;
    }
    onUpdate({ overridesByLang, text: undefined, i18n: undefined });
  };
  // Aplicar resultado de IA: machacamos el override del idioma activo.
  // Si el bloque venía con textId, mantenemos la referencia (override puntual
  // sobre el source).
  const applyAi = (newText) => {
    setTextValue(newText);
    setAiOpen(false);
  };

  // Cambiar el textId enlazado. Si el user pasa de "vacío" a un pre-escrito,
  // limpiamos overridesByLang para que se herede del source. Si pasa de un
  // pre-escrito a "vacío", copiamos el texto actual al override ES para no
  // perder lo que estaba pintado en pantalla.
  const setLinkedTextId = (newId) => {
    if (newId === '') {
      const seedEs = textValue || '';
      onUpdate({ textId: undefined, overridesByLang: { es: seedEs } });
    } else {
      onUpdate({ textId: newId, overridesByLang: undefined, text: undefined, i18n: undefined });
    }
  };

  // Editor de hijos para secciones (recursivo). Cada columna tiene su propia
  // lista de bloques con su propio picker — mismas operaciones que el
  // top-level pero scoped al subarray columns[ci].blocks.
  const updateSectionChild = (colIdx, childIdx, patch) => {
    const cols = (block.columns || []).slice();
    const col = cols[colIdx] || { blocks: [] };
    const blocks = (col.blocks || []).map((b, i) => i === childIdx ? Object.assign({}, b, patch) : b);
    cols[colIdx] = { ...col, blocks };
    onUpdate({ columns: cols });
  };
  const removeSectionChild = (colIdx, childIdx) => {
    const cols = (block.columns || []).slice();
    const col = cols[colIdx] || { blocks: [] };
    cols[colIdx] = { ...col, blocks: (col.blocks || []).filter((_, i) => i !== childIdx) };
    onUpdate({ columns: cols });
  };
  const moveSectionChild = (colIdx, childIdx, dir) => {
    const cols = (block.columns || []).slice();
    const col = cols[colIdx] || { blocks: [] };
    const blocks = (col.blocks || []).slice();
    const j = childIdx + dir;
    if (j < 0 || j >= blocks.length) return;
    [blocks[childIdx], blocks[j]] = [blocks[j], blocks[childIdx]];
    cols[colIdx] = { ...col, blocks };
    onUpdate({ columns: cols });
  };
  const addSectionChild = (colIdx, kind) => {
    const colFactory = {
      text: () => ({ type: 'text', overridesByLang: { es: '' } }),
      brand_strip: () => ({ type: 'brand_strip', brand: 'mbo' }),
      product_single: () => ({ type: 'product_single', product1: '' }),
      image: () => ({ type: 'image', src: '', alt: '', align: 'center', widthPct: 100 }),
      cta: () => ({ type: 'cta', text: 'Más información', url: '', bg: '#1d4ed8', color: '#ffffff', align: 'center' }),
      // Mismo fix de dividers que en el factory padre — emite el shape
      // canónico que dividerBlockHtml renderiza.
      divider_line: () => ({ type: 'divider', style: 'line', color: '#e2e8f0', paddingV: 24 }),
      video: () => ({ type: 'video', youtubeUrl: '' }),
    };
    const f = colFactory[kind];
    if (!f) return;
    const cols = (block.columns || []).slice();
    const col = cols[colIdx] || { blocks: [] };
    cols[colIdx] = { ...col, blocks: [...(col.blocks || []), f()] };
    onUpdate({ columns: cols });
  };

  const heroSource = block._sourceId ? allStandalones.find(s => s.id === block._sourceId) : null;

  return (
    <div style={{
      background:'var(--bg-panel)', border:'1px solid var(--border)',
      borderRadius:'var(--r-sm)', padding:10,
    }}>
      <div style={{display:'flex', alignItems:'center', gap:8, marginBottom:8}}>
        <span style={{
          fontSize:10, fontFamily:'var(--font-mono)', textTransform:'uppercase',
          letterSpacing:1, color:'var(--text-muted)', padding:'2px 8px',
          background:'var(--bg-sunken)', borderRadius:4,
        }}>
          {index + 1} · {typeLabel}
        </span>
        <div style={{flex:1}}/>
        <button className="icon-btn" disabled={index === 0} onClick={() => onMove(-1)} title="Subir"><Icon name="arrowUp" size={11}/></button>
        <button className="icon-btn" disabled={index === total - 1} onClick={() => onMove(1)} title="Bajar"><Icon name="arrowDown" size={11}/></button>
        <button className="icon-btn" onClick={onRemove} title="Eliminar bloque"><Icon name="trash" size={11}/></button>
      </div>

      {block.type === 'text' && (
        <div style={{display:'flex', flexDirection:'column', gap:6}}>
          <div style={{display:'flex', gap:6, alignItems:'center'}}>
            <span style={{fontSize:10, color:'var(--text-subtle)', fontWeight:600, letterSpacing:0.3}}>Pre-escrito:</span>
            <select
              className="select" style={{flex:1, fontSize:11}}
              value={block.textId || ''}
              onChange={e => setLinkedTextId(e.target.value)}
              title="Enlaza con un texto pre-escrito de la biblioteca; el override de cada idioma sigue siendo editable abajo"
            >
              <option value="">— Sin enlazar (texto libre) —</option>
              {allTexts.filter(t => t.visible !== false).map(t => (
                <option key={t.id} value={t.id}>{t.icon ? t.icon + '  ' : ''}{t.name}</option>
              ))}
            </select>
            <button
              className="btn btn-ghost"
              style={{fontSize:11, padding:'4px 8px', whiteSpace:'nowrap', background:'linear-gradient(135deg, color-mix(in oklch, #8b5cf6 18%, transparent), color-mix(in oklch, #ec4899 18%, transparent))'}}
              onClick={() => setAiOpen(true)}
              title="Generar/reescribir con IA en el idioma activo"
            >
              <Icon name="sparkles" size={11}/> IA
            </button>
          </div>
          <textarea
            className="textarea" rows={3}
            value={textValue}
            placeholder={
              linkedText
                ? (block.overridesByLang && block.overridesByLang[lang] != null
                    ? ''
                    : '(usando el texto del pre-escrito · escribe aquí para sobreescribir solo en ' + lang.toUpperCase() + ')')
                : (lang === 'es' ? 'Escribe el texto…' : '(traducción ' + lang.toUpperCase() + ')')
            }
            onChange={e => setTextValue(e.target.value)}
            style={{fontSize:12}}
          />
          {linkedText && block.overridesByLang && block.overridesByLang[lang] != null && (
            <button
              className="btn btn-ghost"
              style={{fontSize:10, padding:'2px 8px', alignSelf:'flex-start'}}
              onClick={() => {
                const ovr = Object.assign({}, block.overridesByLang || {});
                delete ovr[lang];
                onUpdate({ overridesByLang: Object.keys(ovr).length ? ovr : undefined });
              }}
              title="Quitar override · vuelve a heredar del texto pre-escrito"
            >
              ↻ Restaurar herencia
            </button>
          )}
          {aiOpen && typeof window.AiTextPopover === 'function' && (
            <window.AiTextPopover
              lang={lang}
              currentText={textValue}
              onApply={applyAi}
              onClose={() => setAiOpen(false)}
            />
          )}
        </div>
      )}

      {block.type === 'brand_strip' && (
        <select className="select" value={block.brand || 'mbo'} onChange={e => onUpdate({ brand: e.target.value })}>
          {allBrands.filter(b => b.id !== 'bomedia').map(b => <option key={b.id} value={b.id}>{b.label}</option>)}
        </select>
      )}

      {block.type === 'product_single' && (
        <select className="select" value={block.product1 || ''} onChange={e => onUpdate({ product1: e.target.value })}>
          {productOpts}
        </select>
      )}

      {block.type === 'product_pair' && (
        <div style={{display:'flex', flexDirection:'column', gap:4}}>
          <select className="select" value={block.product1 || ''} onChange={e => onUpdate({ product1: e.target.value })}>{productOpts}</select>
          <select className="select" value={block.product2 || ''} onChange={e => onUpdate({ product2: e.target.value })}>{productOpts}</select>
        </div>
      )}

      {block.type === 'product_trio' && (
        <div style={{display:'flex', flexDirection:'column', gap:4}}>
          <select className="select" value={block.product1 || ''} onChange={e => onUpdate({ product1: e.target.value })}>{productOpts}</select>
          <select className="select" value={block.product2 || ''} onChange={e => onUpdate({ product2: e.target.value })}>{productOpts}</select>
          <select className="select" value={block.product3 || ''} onChange={e => onUpdate({ product3: e.target.value })}>{productOpts}</select>
        </div>
      )}

      {block.type === 'image' && (
        <div style={{display:'flex', flexDirection:'column', gap:6}}>
          <ImageUploadInput
            value={block.src || ''}
            onChange={v => onUpdate({ src: v })}
            prefix="composed-image"
            placeholder="https://… (URL de la imagen)"
            brand={block.brand}
          />
          <input className="input" value={block.alt || ''} placeholder="Alt (descripción accesible)" onChange={e => onUpdate({ alt: e.target.value })} style={{fontSize:11}} />
          <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:6}}>
            <select className="select" value={block.align || 'center'} onChange={e => onUpdate({ align: e.target.value })}>
              <option value="left">Izquierda</option>
              <option value="center">Centro</option>
              <option value="right">Derecha</option>
            </select>
            <input className="input" type="number" min={20} max={100} value={block.widthPct || 100} onChange={e => onUpdate({ widthPct: parseInt(e.target.value) || 100 })} style={{fontSize:11}} />
          </div>
        </div>
      )}

      {(block.type === 'divider' || block.type === 'divider_line' || block.type === 'divider_short' || block.type === 'divider_dots') && (() => {
        // Resolver el style canónico: si el bloque es type:'divider' lee block.style;
        // si es legacy type:'divider_line' lo derivamos de la parte tras el guión bajo.
        const currentStyle = block.style
          || (block.type === 'divider_short' ? 'short'
            : block.type === 'divider_dots' ? 'dots'
            : 'line');
        const setStyle = (newStyle) => onUpdate({ type: 'divider', style: newStyle });
        return (
          <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:6}}>
            <select className="select" value={currentStyle} onChange={e => setStyle(e.target.value)}>
              <option value="line">Línea fina</option>
              <option value="short">Línea corta</option>
              <option value="dots">Puntos</option>
            </select>
            <input className="input" type="color" value={block.color || '#e2e8f0'} onChange={e => onUpdate({ color: e.target.value })} style={{padding:2, height:30}} />
          </div>
        );
      })()}

      {(block.type === 'video' || block.type === 'freebird') && (
        <input className="input" value={block.youtubeUrl || ''} placeholder="https://www.youtube.com/watch?v=…" onChange={e => onUpdate({ youtubeUrl: e.target.value })} style={{fontSize:11, fontFamily:'var(--font-mono)'}} />
      )}

      {block.type === 'pimpam_hero' && (
        <div style={{display:'flex', flexDirection:'column', gap:6}}>
          {heroStandalones.length === 0 ? (
            <div style={{fontSize:11, color:'var(--text-muted)', fontStyle:'italic', padding:6}}>
              No hay heros sueltos. Crea uno primero en <strong>Bloques sueltos</strong>.
            </div>
          ) : (
            <>
              <div style={{fontSize:10, color:'var(--text-subtle)', fontWeight:600, letterSpacing:0.3}}>Hero source:</div>
              <select
                className="select"
                value={block._sourceId || ''}
                onChange={e => onUpdate({ standaloneId: e.target.value, _sourceType: 'standalone', _sourceId: e.target.value })}
              >
                <option value="">— Selecciona un hero —</option>
                {heroStandalones.map(h => <option key={h.id} value={h.id}>{h.title || h.id}</option>)}
              </select>
              {heroSource && (
                <div style={{fontSize:11, color:'var(--text-muted)', padding:'4px 0', lineHeight:1.5}}>
                  → "<strong>{heroSource.config?.heroTitle || heroSource.title}</strong>"<br/>
                  <span style={{fontSize:10, fontStyle:'italic'}}>Edita los detalles del hero desde "Bloques sueltos" o desde el composer al insertarlo en el lienzo.</span>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {block.type === 'cta' && (
        <div style={{display:'flex', flexDirection:'column', gap:6}}>
          <input className="input" value={block.text || ''} placeholder="Texto del botón" onChange={e => onUpdate({ text: e.target.value })} style={{fontSize:12}} />
          <input className="input" value={block.url || ''} placeholder="https://…" onChange={e => onUpdate({ url: e.target.value })} style={{fontSize:11, fontFamily:'var(--font-mono)'}} />
          <div style={{display:'grid', gridTemplateColumns:'auto 1fr auto 1fr 1fr', gap:6, alignItems:'center'}}>
            <span style={{fontSize:10, color:'var(--text-subtle)'}}>Fondo</span>
            <input type="color" value={block.bg || '#1d4ed8'} onChange={e => onUpdate({ bg: e.target.value })} style={{height:28, padding:2}} />
            <span style={{fontSize:10, color:'var(--text-subtle)'}}>Texto</span>
            <input type="color" value={block.color || '#ffffff'} onChange={e => onUpdate({ color: e.target.value })} style={{height:28, padding:2}} />
            <select className="select" value={block.align || 'center'} onChange={e => onUpdate({ align: e.target.value })} style={{fontSize:11}}>
              <option value="left">Izq.</option>
              <option value="center">Centro</option>
              <option value="right">Der.</option>
            </select>
          </div>
        </div>
      )}

      {block.type === 'pimpam_steps' && (
        <div style={{fontSize:11, color:'var(--text-muted)', fontStyle:'italic', padding:6}}>
          Edita los detalles de este bloque desde el editor del composer (al insertarlo en el lienzo).
        </div>
      )}

      {block.type === 'section' && (() => {
        const cols = Array.isArray(block.columns) ? block.columns : [];
        return (
          <div style={{display:'flex', flexDirection:'column', gap:8}}>
            <div style={{display:'grid', gridTemplateColumns: cols.length === 3 ? '1fr 1fr 1fr' : '1fr 1fr', gap:8}}>
              {cols.map((col, ci) => (
                <div key={ci} style={{
                  background:'var(--bg-sunken)', border:'1px dashed var(--border-strong)',
                  borderRadius:'var(--r-sm)', padding:8,
                }}>
                  <div style={{fontSize:10, fontFamily:'var(--font-mono)', color:'var(--text-subtle)', textTransform:'uppercase', letterSpacing:1, marginBottom:6}}>
                    Col {ci + 1}
                  </div>
                  <div style={{display:'flex', flexDirection:'column', gap:6}}>
                    {(col.blocks || []).length === 0 && (
                      <div style={{fontSize:10, color:'var(--text-subtle)', fontStyle:'italic', padding:'4px 0'}}>Vacía</div>
                    )}
                    {(col.blocks || []).map((cb, ii) => (
                      <ComposedChildEditor
                        key={ii}
                        block={cb}
                        index={ii}
                        total={(col.blocks || []).length}
                        lang={lang}
                        onUpdate={(patch) => updateSectionChild(ci, ii, patch)}
                        onRemove={() => removeSectionChild(ci, ii)}
                        onMove={(dir) => moveSectionChild(ci, ii, dir)}
                      />
                    ))}
                  </div>
                  <div style={{display:'flex', flexWrap:'wrap', gap:4, marginTop:6}}>
                    <button className="btn btn-ghost" style={{fontSize:10, padding:'2px 6px'}} onClick={() => addSectionChild(ci, 'text')}>+ Texto</button>
                    <button className="btn btn-ghost" style={{fontSize:10, padding:'2px 6px'}} onClick={() => addSectionChild(ci, 'product_single')}>+ Producto</button>
                    <button className="btn btn-ghost" style={{fontSize:10, padding:'2px 6px'}} onClick={() => addSectionChild(ci, 'image')}>+ Imagen</button>
                    <button className="btn btn-ghost" style={{fontSize:10, padding:'2px 6px'}} onClick={() => addSectionChild(ci, 'cta')}>+ CTA</button>
                    <button className="btn btn-ghost" style={{fontSize:10, padding:'2px 6px'}} onClick={() => addSectionChild(ci, 'video')}>+ Vídeo</button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })()}
    </div>
  );
}

/* Saved CTA editor — same fields as the inline CTA block but with an extra
   `name` so the user can identify it in the picker / library list. */
function CtaSavedBOEdit({ data, setData }) {
  const set = (k, v) => setData({...data, [k]: v});
  const bullets = Array.isArray(data.bullets) ? data.bullets : [];
  const setBullet = (i, v) => set('bullets', bullets.map((x, idx) => idx === i ? v : x));
  const addBullet = () => set('bullets', [...bullets, '']);
  const delBullet = (i) => set('bullets', bullets.filter((_, idx) => idx !== i));
  return (
    <>
      <div className="field">
        <label className="field-label">Nombre interno (lo verás en el picker)</label>
        <input className="input" value={data.name || ''} onChange={e => set('name', e.target.value)} placeholder="Ej. CTA Demo presencial" />
      </div>
      <div className="field">
        <label className="field-label">Título (opcional)</label>
        <input className="input" value={data.title || ''} onChange={e => set('title', e.target.value)} placeholder="Ej. ¿Listo para empezar?" />
      </div>
      <div className="field">
        <label className="field-label">Subtítulo (opcional)</label>
        <textarea className="textarea" rows={2} value={data.subtitle || ''} onChange={e => set('subtitle', e.target.value)} placeholder="Línea descriptiva debajo del título" />
      </div>
      <div className="field">
        <div className="field-label-row">
          <label className="field-label">Lista ({bullets.length})</label>
          <button className="btn btn-ghost" style={{fontSize:11}} onClick={addBullet}><Icon name="plus" size={11}/> Añadir bullet</button>
        </div>
        <div style={{display:'flex', flexDirection:'column', gap:4}}>
          {bullets.map((bp, i) => (
            <div key={i} style={{display:'flex', gap:4, alignItems:'center'}}>
              <input className="input" style={{flex:1}} value={bp} onChange={e => setBullet(i, e.target.value)} placeholder={'Bullet ' + (i+1)} />
              <button className="icon-btn" onClick={() => delBullet(i)} title="Eliminar"><Icon name="trash" size={11}/></button>
            </div>
          ))}
        </div>
      </div>
      <div className="field">
        <label className="field-label">Texto del botón</label>
        <input className="input" value={data.text || ''} onChange={e => set('text', e.target.value)} placeholder="Más información" />
      </div>
      <div className="field">
        <label className="field-label">URL de destino</label>
        <input className="input mono" style={{fontSize:11}} value={data.url || ''} onChange={e => set('url', e.target.value)} placeholder="https://… o mailto:…" />
      </div>
      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:12}}>
        <div className="field">
          <label className="field-label">Fondo del botón</label>
          <div style={{display:'flex', gap:6, alignItems:'center'}}>
            <input type="color" value={data.bg || '#1d4ed8'} onChange={e => set('bg', e.target.value)} style={{width:34, height:30, padding:0, border:'1px solid var(--border)', borderRadius:4, cursor:'pointer'}}/>
            <input className="input mono" style={{fontSize:11}} value={data.bg || '#1d4ed8'} onChange={e => set('bg', e.target.value)} />
          </div>
        </div>
        <div className="field">
          <label className="field-label">Texto del botón</label>
          <div style={{display:'flex', gap:6, alignItems:'center'}}>
            <input type="color" value={data.color || '#ffffff'} onChange={e => set('color', e.target.value)} style={{width:34, height:30, padding:0, border:'1px solid var(--border)', borderRadius:4, cursor:'pointer'}}/>
            <input className="input mono" style={{fontSize:11}} value={data.color || '#ffffff'} onChange={e => set('color', e.target.value)} />
          </div>
        </div>
      </div>
      <div className="field">
        <label className="field-label">Alineación</label>
        <select className="select" value={data.align || 'center'} onChange={e => set('align', e.target.value)}>
          <option value="left">Izquierda</option>
          <option value="center">Centro</option>
          <option value="right">Derecha</option>
        </select>
      </div>
      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:12}}>
        <div className="field">
          <label className="field-label">Fondo de panel</label>
          <input className="input mono" style={{fontSize:11}} value={data.panelBg || ''} placeholder="transparent" onChange={e => set('panelBg', e.target.value || 'transparent')} />
        </div>
        <div className="field">
          <label className="field-label">Borde de panel</label>
          <input className="input mono" style={{fontSize:11}} value={data.panelBorder || ''} placeholder="transparent" onChange={e => set('panelBorder', e.target.value || 'transparent')} />
        </div>
      </div>
    </>
  );
}

function StandaloneBOEdit({ data, setData }) {
  const set = (k, v) => setData({...data, [k]: v});
  // Helpers para tocar el config (campos específicos del tipo de bloque)
  const cfg = data.config || {};
  const setCfg = (k, v) => setData({...data, config: { ...cfg, [k]: v }});
  // Tipo de bloque (campo .blockType o .type — ambos se usan en defaults)
  const blockType = data.blockType || data.type || '';
  const isHero = blockType === 'pimpam_hero' || blockType === 'product_hero' || blockType === 'hero';
  const isVideo = blockType === 'video' || blockType === 'freebird';
  const isBrandStrip = blockType === 'brand_strip';
  const isProduct = blockType === 'product_single' || blockType === 'product_pair' || blockType === 'product_trio';
  const isSteps = blockType === 'pimpam_steps';

  const products = (typeof window !== 'undefined' && window.PRODUCTS) || PRODUCTS || [];

  // Editor de bullets (heros)
  const bullets = Array.isArray(cfg.heroBullets) ? cfg.heroBullets : [];
  const setBullet = (i, v) => setCfg('heroBullets', bullets.map((x, idx) => idx === i ? v : x));
  const addBullet = () => setCfg('heroBullets', [...bullets, '']);
  const delBullet = (i) => setCfg('heroBullets', bullets.filter((_, idx) => idx !== i));

  // Editor de CTA buttons (heros). Si no hay heroCtaButtons (forma moderna)
  // pero existen los campos legacy heroCtaText/heroCtaUrl, los promocionamos
  // a un botón único para que aparezca en el editor. Sin este fallback,
  // standalones viejos mostraban "+ Añadir CTA" vacío aunque el preview
  // renderizara perfectamente el botón. Bug fix Apr 2026 — replica la
  // misma conversión que hace pimpamHeroHtml() al renderizar.
  const ctaButtons = (() => {
    if (Array.isArray(cfg.heroCtaButtons) && cfg.heroCtaButtons.length) return cfg.heroCtaButtons;
    if (cfg.heroCtaText && cfg.heroCtaUrl) {
      return [{ text: cfg.heroCtaText, url: cfg.heroCtaUrl, bg: cfg.heroCtaColor || '#1d4ed8', color: '#ffffff' }];
    }
    return [];
  })();
  // Cuando se edita por primera vez un hero legacy, materializamos
  // heroCtaButtons con el botón derivado y limpiamos heroCtaText/heroCtaUrl
  // para que el origen único de verdad sea el array (evita doble-render
  // si quedaran campos legacy + array al mismo tiempo).
  const _materializeCtaArr = (next) => {
    const patch = { heroCtaButtons: next };
    if (cfg.heroCtaText || cfg.heroCtaUrl) {
      patch.heroCtaText = '';
      patch.heroCtaUrl = '';
    }
    setData({ ...data, config: { ...cfg, ...patch } });
  };
  const setCtaBtn = (i, k, v) => _materializeCtaArr(ctaButtons.map((x, idx) => idx === i ? { ...x, [k]: v } : x));
  const addCtaBtn = () => _materializeCtaArr([...ctaButtons, { text: 'Más info', url: '', bg: '#1d4ed8', color: '#ffffff' }]);
  const delCtaBtn = (i) => _materializeCtaArr(ctaButtons.filter((_, idx) => idx !== i));

  // Editor de pasos (pimpam_steps)
  const steps = Array.isArray(cfg.steps) ? cfg.steps : [];
  const setStep = (i, k, v) => setCfg('steps', steps.map((x, idx) => idx === i ? { ...x, [k]: v } : x));
  const addStep = () => setCfg('steps', [...steps, { n: String(steps.length + 1), t: '', s: '' }]);
  const delStep = (i) => setCfg('steps', steps.filter((_, idx) => idx !== i));

  const Upload = (typeof window !== 'undefined' && window.ImageUploadInput) || null;

  return (
    <>
      <div className="field">
        <label className="field-label">Título (interno)</label>
        <input className="input" value={data.title || ''} onChange={e => set('title', e.target.value)} />
      </div>
      <div className="field">
        <label className="field-label">Descripción (interna)</label>
        <input className="input" value={data.desc || ''} onChange={e => set('desc', e.target.value)} placeholder="Descripción que se ve en el sidebar" />
      </div>
      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:12}}>
        <div className="field">
          <label className="field-label">Sección</label>
          <select className="select" value={data.section || 'otros'} onChange={e => set('section', e.target.value)}>
            {['heroes','marcas','cabeceras','otros'].map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="field">
          <label className="field-label">Tipo de bloque</label>
          <select className="select" value={blockType} onChange={e => { set('blockType', e.target.value); set('type', e.target.value); }}>
            <option value="">— Ninguno —</option>
            <option value="pimpam_hero">Hero</option>
            <option value="video">Vídeo</option>
            <option value="brand_strip">Brand strip</option>
            <option value="product_single">1 Producto</option>
            <option value="product_pair">2 Productos</option>
            <option value="product_trio">3 Productos</option>
            <option value="pimpam_steps">Pasos</option>
          </select>
        </div>
      </div>
      <div className="field">
        <label className="field-label">Marca asociada</label>
        <select className="select" value={data.brand || 'mix'} onChange={e => set('brand', e.target.value)}>
          <option value="mix">Multi-marca</option>
          {BRANDS.map(b => <option key={b.id} value={b.id}>{b.label}</option>)}
        </select>
      </div>

      {/* ─── HERO ───────────────────────────────────────── */}
      {isHero && (
        <>
          <hr style={{border:'none', borderTop:'1px solid var(--border)', margin:'18px 0 10px'}}/>
          <div style={{fontSize:12, fontWeight:600, color:'var(--text-muted)', textTransform:'uppercase', letterSpacing:'0.05em', marginBottom:8}}>Contenido del hero</div>
          <div className="field">
            <label className="field-label">Título</label>
            <input className="input" value={cfg.heroTitle || ''} onChange={e => setCfg('heroTitle', e.target.value)} />
          </div>
          <div className="field">
            <label className="field-label">Subtítulo</label>
            <textarea className="textarea" rows={2} value={cfg.heroSubtitle || ''} onChange={e => setCfg('heroSubtitle', e.target.value)} />
          </div>
          <div className="field">
            <label className="field-label">Imagen</label>
            {Upload
              ? <Upload value={cfg.heroImage || ''} onChange={v => setCfg('heroImage', v)} prefix={'standalone-hero/' + (data.id || 'new')} placeholder="https://… o pulsa Subir" />
              : <input className="input mono" style={{fontSize:11}} value={cfg.heroImage || ''} onChange={e => setCfg('heroImage', e.target.value)} />
            }
          </div>
          <div className="field">
            <label className="field-label">Enlace al pulsar la imagen (opcional)</label>
            <input className="input mono" style={{fontSize:11}} value={cfg.heroImageLink || ''} onChange={e => setCfg('heroImageLink', e.target.value)} placeholder="https://…" />
          </div>
          <div className="field">
            <div className="field-label-row">
              <label className="field-label">Bullets ({bullets.length})</label>
              <button className="btn btn-ghost" style={{fontSize:11}} onClick={addBullet}><Icon name="plus" size={11}/> Añadir bullet</button>
            </div>
            <div style={{display:'flex', flexDirection:'column', gap:4}}>
              {bullets.map((b, i) => (
                <div key={i} style={{display:'flex', gap:4, alignItems:'center'}}>
                  <span style={{fontSize:11, color:'var(--text-subtle)', width:16, textAlign:'center'}}>✓</span>
                  <input className="input" style={{flex:1, fontSize:12}} value={b} onChange={e => setBullet(i, e.target.value)} />
                  <button className="icon-btn" style={{width:20, height:20}} onClick={() => delBullet(i)}><Icon name="x" size={10}/></button>
                </div>
              ))}
            </div>
          </div>
          <div className="field">
            <div className="field-label-row">
              <label className="field-label">Botones CTA ({ctaButtons.length})</label>
              <button className="btn btn-ghost" style={{fontSize:11}} onClick={addCtaBtn}><Icon name="plus" size={11}/> Añadir botón</button>
            </div>
            <div style={{display:'flex', flexDirection:'column', gap:8}}>
              {ctaButtons.map((c, i) => (
                <div key={i} style={{padding:8, border:'1px solid var(--border)', borderRadius:'var(--r-sm)', background:'var(--bg-sunken)'}}>
                  <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:6}}>
                    <input className="input" style={{fontSize:12}} placeholder="Texto del botón" value={c.text || ''} onChange={e => setCtaBtn(i, 'text', e.target.value)} />
                    <input className="input mono" style={{fontSize:11}} placeholder="https://…" value={c.url || ''} onChange={e => setCtaBtn(i, 'url', e.target.value)} />
                  </div>
                  <div style={{display:'flex', gap:6, marginTop:6, alignItems:'center'}}>
                    <span style={{fontSize:11, color:'var(--text-muted)'}}>Fondo</span>
                    <input type="color" value={c.bg || '#1d4ed8'} style={{width:28, height:22, border:'none', padding:0, cursor:'pointer'}} onChange={e => setCtaBtn(i, 'bg', e.target.value)} />
                    <span style={{fontSize:11, color:'var(--text-muted)'}}>Texto</span>
                    <input type="color" value={c.color || '#ffffff'} style={{width:28, height:22, border:'none', padding:0, cursor:'pointer'}} onChange={e => setCtaBtn(i, 'color', e.target.value)} />
                    <button className="icon-btn" style={{marginLeft:'auto', width:22, height:22}} onClick={() => delCtaBtn(i)}><Icon name="trash" size={11}/></button>
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="field">
            <label className="field-label">Color de fondo del hero</label>
            <input type="color" value={cfg.heroBgColor || '#ffffff'} style={{width:50, height:30, border:'1px solid var(--border)', borderRadius:4, cursor:'pointer'}} onChange={e => setCfg('heroBgColor', e.target.value)} />
            <span style={{marginLeft:8, fontSize:11, fontFamily:'var(--font-mono)', color:'var(--text-muted)'}}>{cfg.heroBgColor || '#ffffff'}</span>
          </div>
        </>
      )}

      {/* ─── VIDEO ──────────────────────────────────────── */}
      {isVideo && (
        <>
          <hr style={{border:'none', borderTop:'1px solid var(--border)', margin:'18px 0 10px'}}/>
          <div style={{fontSize:12, fontWeight:600, color:'var(--text-muted)', textTransform:'uppercase', letterSpacing:'0.05em', marginBottom:8}}>Vídeo</div>
          <div className="field">
            <label className="field-label">URL de YouTube</label>
            <input className="input mono" style={{fontSize:11}} value={cfg.youtubeUrl || ''} onChange={e => setCfg('youtubeUrl', e.target.value)} placeholder="https://www.youtube.com/watch?v=..." />
          </div>
          <div className="field">
            <label className="field-label">Miniatura personalizada</label>
            {Upload
              ? <Upload value={cfg.thumbnailOverride || ''} onChange={v => setCfg('thumbnailOverride', v)} prefix={'video-thumbs/' + (data.id || 'new')} placeholder="Dejar vacío para auto-generar desde YouTube" />
              : <input className="input mono" style={{fontSize:11}} value={cfg.thumbnailOverride || ''} onChange={e => setCfg('thumbnailOverride', e.target.value)} />
            }
          </div>
        </>
      )}

      {/* ─── BRAND STRIP ────────────────────────────────── */}
      {isBrandStrip && (
        <>
          <hr style={{border:'none', borderTop:'1px solid var(--border)', margin:'18px 0 10px'}}/>
          <div style={{fontSize:12, fontWeight:600, color:'var(--text-muted)', textTransform:'uppercase', letterSpacing:'0.05em', marginBottom:8}}>Brand strip</div>
          <div className="field">
            <label className="field-label">Marca a mostrar</label>
            <select className="select" value={cfg.brand || 'artisjet'} onChange={e => setCfg('brand', e.target.value)}>
              {BRANDS.filter(b => b.id !== 'bomedia').map(b => <option key={b.id} value={b.id}>{b.label}</option>)}
            </select>
            <div style={{fontSize:11, color:'var(--text-muted)', marginTop:6}}>El logo, color y URL se toman de la marca seleccionada (editable en BO → Marcas).</div>
          </div>
        </>
      )}

      {/* ─── PRODUCT (single/pair/trio) ─────────────────── */}
      {isProduct && (
        <>
          <hr style={{border:'none', borderTop:'1px solid var(--border)', margin:'18px 0 10px'}}/>
          <div style={{fontSize:12, fontWeight:600, color:'var(--text-muted)', textTransform:'uppercase', letterSpacing:'0.05em', marginBottom:8}}>Productos por defecto</div>
          <div className="field">
            <label className="field-label">Producto 1</label>
            <select className="select" value={cfg.defaultProduct || cfg.defaultProduct1 || ''} onChange={e => setCfg(blockType === 'product_single' ? 'defaultProduct' : 'defaultProduct1', e.target.value)}>
              <option value="">— Sin defecto —</option>
              {products.map(p => <option key={p.id} value={p.id}>{p.name} ({p.brand})</option>)}
            </select>
          </div>
          {(blockType === 'product_pair' || blockType === 'product_trio') && (
            <div className="field">
              <label className="field-label">Producto 2</label>
              <select className="select" value={cfg.defaultProduct2 || ''} onChange={e => setCfg('defaultProduct2', e.target.value)}>
                <option value="">— Sin defecto —</option>
                {products.map(p => <option key={p.id} value={p.id}>{p.name} ({p.brand})</option>)}
              </select>
            </div>
          )}
          {blockType === 'product_trio' && (
            <div className="field">
              <label className="field-label">Producto 3</label>
              <select className="select" value={cfg.defaultProduct3 || ''} onChange={e => setCfg('defaultProduct3', e.target.value)}>
                <option value="">— Sin defecto —</option>
                {products.map(p => <option key={p.id} value={p.id}>{p.name} ({p.brand})</option>)}
              </select>
            </div>
          )}
        </>
      )}

      {/* ─── STEPS ──────────────────────────────────────── */}
      {isSteps && (
        <>
          <hr style={{border:'none', borderTop:'1px solid var(--border)', margin:'18px 0 10px'}}/>
          <div style={{fontSize:12, fontWeight:600, color:'var(--text-muted)', textTransform:'uppercase', letterSpacing:'0.05em', marginBottom:8}}>Pasos</div>
          <div className="field">
            <div className="field-label-row">
              <label className="field-label">Pasos ({steps.length})</label>
              <button className="btn btn-ghost" style={{fontSize:11}} onClick={addStep}><Icon name="plus" size={11}/> Añadir paso</button>
            </div>
            {steps.map((st, i) => (
              <div key={i} style={{padding:8, border:'1px solid var(--border)', borderRadius:'var(--r-sm)', marginBottom:6}}>
                <div style={{display:'grid', gridTemplateColumns:'80px 1fr', gap:6, marginBottom:4}}>
                  <input className="input" style={{fontSize:12}} placeholder="🔢" value={st.n || ''} onChange={e => setStep(i, 'n', e.target.value)} />
                  <input className="input" style={{fontSize:12}} placeholder="Título" value={st.t || ''} onChange={e => setStep(i, 't', e.target.value)} />
                </div>
                <div style={{display:'flex', gap:6}}>
                  <input className="input" style={{flex:1, fontSize:12}} placeholder="Subtítulo" value={st.s || ''} onChange={e => setStep(i, 's', e.target.value)} />
                  <button className="icon-btn" style={{width:22, height:22}} onClick={() => delStep(i)}><Icon name="trash" size={11}/></button>
                </div>
              </div>
            ))}
          </div>
          <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:8}}>
            <div className="field">
              <label className="field-label">Color fondo</label>
              <input type="color" value={cfg.stepsBgColor || '#fff7ed'} style={{width:50, height:30, border:'1px solid var(--border)', borderRadius:4, cursor:'pointer'}} onChange={e => setCfg('stepsBgColor', e.target.value)} />
            </div>
            <div className="field">
              <label className="field-label">Color borde</label>
              <input type="color" value={cfg.stepsBorderColor || '#fed7aa'} style={{width:50, height:30, border:'1px solid var(--border)', borderRadius:4, cursor:'pointer'}} onChange={e => setCfg('stepsBorderColor', e.target.value)} />
            </div>
          </div>
        </>
      )}
    </>
  );
}

/* Users panel — admin-only. Lists all users, lets admin create new ones,
   change passwords, switch role, or delete (except themselves). */
function UsersPanel({ users, setAppState, currentUser, setEditing }) {
  const createUser = () => {
    const id = 'u-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 6);
    const newUser = {
      id,
      name: 'Nuevo usuario',
      role: 'commercial',
      passwordHash: '', // empty until admin sets one in the drawer
      hiddenItems: {},
      aiStyles: {},
    };
    setEditing({ kind: 'user', item: newUser });
  };
  const remove = (u) => {
    if (u.id === currentUser?.id) { alert('No puedes eliminar el usuario con el que estás conectado.'); return; }
    if (!window.confirm('¿Eliminar usuario "' + u.name + '"? Esta acción no se puede deshacer.')) return;
    setAppState(prev => ({ ...prev, users: (prev.users || []).filter(x => x.id !== u.id) }));
  };
  return (
    <div style={{display:'flex', flexDirection:'column', gap:10}}>
      <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:6}}>
        <div style={{fontSize:12, color:'var(--text-muted)'}}>{users.length} usuario{users.length === 1 ? '' : 's'}</div>
        <button className="btn btn-primary" style={{fontSize:12}} onClick={createUser}>
          <Icon name="plus" size={12}/> Nuevo usuario
        </button>
      </div>
      {users.map(u => {
        const isMe = u.id === currentUser?.id;
        const hasPw = !!u.passwordHash;
        return (
          <div key={u.id} className="product-card" style={{padding:16, display:'grid', gridTemplateColumns:'40px 1fr auto auto', gap:14, alignItems:'center', cursor:'pointer'}} onClick={() => setEditing({ kind: 'user', item: u })}>
            <div className={'lib-icon ' + (u.role === 'admin' ? 'mbo' : 'artisjet')} style={{width:40, height:40, fontSize:16, fontWeight:700}}>
              {u.name?.[0]?.toUpperCase() || '?'}
            </div>
            <div>
              <div style={{fontSize:14, fontWeight:500, display:'flex', alignItems:'center', gap:8}}>
                {u.name}
                {isMe && <span style={{fontSize:10, fontFamily:'var(--font-mono)', padding:'1px 6px', background:'var(--accent-soft)', color:'var(--accent-ink)', borderRadius:4}}>tú</span>}
              </div>
              <div style={{fontSize:11, color:'var(--text-muted)', fontFamily:'var(--font-mono)'}}>{u.id}</div>
            </div>
            <span style={{fontSize:10, fontFamily:'var(--font-mono)', textTransform:'uppercase', letterSpacing:1, padding:'3px 8px', borderRadius:4, fontWeight:700,
              background: u.role === 'admin' ? 'color-mix(in oklch, var(--mbo) 15%, transparent)' : 'color-mix(in oklch, var(--artisjet) 12%, transparent)',
              color: u.role === 'admin' ? 'var(--mbo)' : 'var(--artisjet)',
            }}>{u.role}</span>
            <div style={{display:'flex', gap:4, alignItems:'center'}}>
              {!hasPw && <span style={{fontSize:10, color:'var(--danger)', fontFamily:'var(--font-mono)'}}>sin pw</span>}
              <button className="btn btn-ghost" style={{fontSize:12}} onClick={e => { e.stopPropagation(); setEditing({ kind: 'user', item: u }); }}>Editar</button>
              <button className="btn btn-ghost" style={{fontSize:12, color: isMe ? 'var(--text-subtle)' : 'var(--danger)'}} disabled={isMe} onClick={e => { e.stopPropagation(); remove(u); }}>
                <Icon name="trash" size={12}/>
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* User editor — admin sets name, role, password, AI tone per language. */
function UserBOEdit({ data, setData }) {
  const set = (k, v) => setData({ ...data, [k]: v });
  const [pwInput, setPwInput] = React.useState('');
  const [pwHashing, setPwHashing] = React.useState(false);
  const aiStyles = (data.aiStyles && typeof data.aiStyles === 'object') ? data.aiStyles : {};
  const langLabels = { es:'Español', fr:'Français', de:'Deutsch', en:'English', nl:'Nederlands' };

  const setAi = (lang, v) => {
    const next = { ...aiStyles, [lang]: v };
    set('aiStyles', next);
  };
  const applyPassword = () => {
    if (!pwInput) return;
    if (typeof window.sha256Hash !== 'function') { alert('sha256Hash no disponible'); return; }
    setPwHashing(true);
    window.sha256Hash(pwInput).then(hash => {
      set('passwordHash', hash);
      setPwInput('');
      setPwHashing(false);
    });
  };

  return (
    <>
      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:12}}>
        <div className="field">
          <label className="field-label">Nombre</label>
          <input className="input" value={data.name || ''} onChange={e => set('name', e.target.value)} placeholder="ej. Sara López"/>
        </div>
        <div className="field">
          <label className="field-label">Rol</label>
          <select className="select" value={data.role || 'commercial'} onChange={e => set('role', e.target.value)}>
            <option value="admin">Admin</option>
            <option value="commercial">Comercial</option>
          </select>
        </div>
      </div>

      <div className="field">
        <label className="field-label">ID interno</label>
        <input className="input mono" style={{fontSize:11}} value={data.id || ''} onChange={e => set('id', e.target.value)} placeholder="u-abc123"/>
        <div style={{fontSize:10, color:'var(--text-subtle)', marginTop:4}}>Identificador único. Cámbialo solo si tienes una razón.</div>
      </div>

      <div className="field">
        <label className="field-label">Contraseña</label>
        <div style={{display:'flex', gap:6, alignItems:'center'}}>
          <input
            type="password"
            className="input"
            style={{flex:1}}
            value={pwInput}
            onChange={e => setPwInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && applyPassword()}
            placeholder={data.passwordHash ? 'Escribe nueva contraseña (sustituye la actual)' : 'Define una contraseña'}
          />
          <button className="btn btn-primary" onClick={applyPassword} disabled={!pwInput || pwHashing} style={{fontSize:12}}>
            {pwHashing ? '…' : 'Aplicar'}
          </button>
        </div>
        <div style={{fontSize:11, color: data.passwordHash ? 'var(--success)' : 'var(--danger)', marginTop:6, fontFamily:'var(--font-mono)'}}>
          {data.passwordHash ? '✓ Contraseña configurada (hash SHA-256 guardado)' : '⚠ Sin contraseña — el usuario no podrá iniciar sesión'}
        </div>
      </div>

      <div className="field">
        <label className="field-label">Tono y estilo IA por idioma</label>
        <div style={{fontSize:11, color:'var(--text-muted)', marginBottom:8, lineHeight:1.5}}>
          Cada idioma tiene su propio prompt de tono que se inyecta cuando este usuario pide al asistente que escriba o reescriba un texto.
        </div>
        <div style={{display:'flex', flexDirection:'column', gap:8}}>
          {['es','fr','de','en','nl'].map(l => (
            <div key={l}>
              <div style={{fontSize:10, fontWeight:600, textTransform:'uppercase', letterSpacing:'0.06em', marginBottom:3, color:'var(--text-muted)'}}>{langLabels[l]} · {l.toUpperCase()}</div>
              <textarea
                className="textarea"
                rows={2}
                value={aiStyles[l] || ''}
                placeholder={'Tono para ' + langLabels[l] + '…'}
                onChange={e => setAi(l, e.target.value)}
              />
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

/* Per-user "Mi tono IA" panel — visible to commercial users so they can
   tune their own assistant prompts without needing admin to intervene. */
/* "Mi cuenta" — self-service para que el comercial se cambie la
   contraseña sin tener que pedírselo al admin. La validación contra el
   hash actual evita que alguien que coge una pestaña abierta pueda
   cambiar la password. */
function MyAccountPanel({ currentUser, setAppState }) {
  const [current, setCurrent] = React.useState('');
  const [next, setNext] = React.useState('');
  const [confirm, setConfirm] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState(null); // { kind:'ok'|'err', text }

  const hasCurrentPw = !!(currentUser && currentUser.passwordHash);

  const reset = () => { setCurrent(''); setNext(''); setConfirm(''); };

  const submit = async () => {
    setMsg(null);
    if (next.length < 4) {
      setMsg({ kind: 'err', text: 'La nueva contraseña debe tener al menos 4 caracteres.' });
      return;
    }
    if (next !== confirm) {
      setMsg({ kind: 'err', text: 'La nueva contraseña y la confirmación no coinciden.' });
      return;
    }
    if (typeof window.sha256Hash !== 'function') {
      setMsg({ kind: 'err', text: 'sha256Hash no disponible. Recarga la página.' });
      return;
    }
    setBusy(true);
    try {
      // 1. Verifica la contraseña actual (si existe)
      if (hasCurrentPw) {
        const currHash = await window.sha256Hash(current);
        if (currHash !== currentUser.passwordHash) {
          setMsg({ kind: 'err', text: 'La contraseña actual no es correcta.' });
          setBusy(false);
          return;
        }
      }
      // 2. Hashea la nueva y guárdala
      const newHash = await window.sha256Hash(next);
      setAppState(prev => {
        const users = (prev.users || []).map(u =>
          u.id === currentUser.id ? Object.assign({}, u, { passwordHash: newHash }) : u
        );
        return Object.assign({}, prev, { users });
      });
      reset();
      setMsg({ kind: 'ok', text: 'Contraseña actualizada. La nueva se usará en el próximo login.' });
    } catch (e) {
      setMsg({ kind: 'err', text: 'Error: ' + (e.message || String(e)) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{maxWidth:560, display:'flex', flexDirection:'column', gap:14}}>
      <div className="product-card" style={{padding:20}}>
        <div style={{fontSize:14, fontWeight:600, marginBottom:4}}>Tus datos</div>
        <div style={{fontSize:12, color:'var(--text-muted)', display:'grid', gridTemplateColumns:'80px 1fr', gap:'4px 12px', marginTop:10}}>
          <span>Nombre</span><span style={{color:'var(--text)', fontWeight:500}}>{currentUser.name}</span>
          <span>Rol</span><span style={{color:'var(--text)', fontFamily:'var(--font-mono)', fontSize:11}}>{currentUser.role}</span>
          <span>ID</span><span style={{color:'var(--text)', fontFamily:'var(--font-mono)', fontSize:11}}>{currentUser.id}</span>
        </div>
      </div>

      <div className="product-card" style={{padding:20}}>
        <div style={{display:'flex', alignItems:'center', gap:10, marginBottom:6}}>
          <Icon name="lock" size={16}/>
          <div style={{fontSize:14, fontWeight:600}}>Cambiar contraseña</div>
        </div>
        <div style={{fontSize:11.5, color:'var(--text-muted)', marginBottom:14, lineHeight:1.55}}>
          {hasCurrentPw
            ? 'Introduce tu contraseña actual para confirmar tu identidad y luego la nueva.'
            : 'No tienes contraseña configurada. Define una para poder iniciar sesión sin pedírselo a un admin.'}
        </div>

        <div style={{display:'flex', flexDirection:'column', gap:10}}>
          {hasCurrentPw && (
            <div className="field">
              <label className="field-label">Contraseña actual</label>
              <input className="input" type="password" value={current} onChange={e => setCurrent(e.target.value)} autoComplete="current-password" />
            </div>
          )}
          <div className="field">
            <label className="field-label">Nueva contraseña</label>
            <input className="input" type="password" value={next} onChange={e => setNext(e.target.value)} autoComplete="new-password" />
          </div>
          <div className="field">
            <label className="field-label">Confirmar nueva contraseña</label>
            <input className="input" type="password" value={confirm} onChange={e => setConfirm(e.target.value)} autoComplete="new-password" />
          </div>
        </div>

        <div style={{display:'flex', gap:8, alignItems:'center', marginTop:14}}>
          <button className="btn btn-primary" disabled={busy || !next || !confirm || (hasCurrentPw && !current)} onClick={submit}>
            {busy ? 'Guardando…' : 'Cambiar contraseña'}
          </button>
          {msg && (
            <span style={{fontSize:12, color: msg.kind === 'err' ? 'var(--danger)' : 'var(--success)', fontWeight:500}}>
              {msg.text}
            </span>
          )}
        </div>

        <div style={{marginTop:14, padding:'10px 12px', background:'var(--bg-sunken)', borderRadius:'var(--r-sm)', fontSize:11, color:'var(--text-muted)', lineHeight:1.55}}>
          La contraseña se guarda como hash SHA-256 en Supabase, no en texto plano. Si la olvidas, pídele a un admin que la resetee desde Backoffice → Usuarios.
        </div>
      </div>
    </div>
  );
}

function MyToneIaPanel({ currentUser, setAppState }) {
  const styles = (currentUser && currentUser.aiStyles) || {};
  const langLabels = { es:'Español', fr:'Français', de:'Deutsch', en:'English', nl:'Nederlands' };

  const updateStyle = (lang, value) => {
    setAppState(prev => ({
      ...prev,
      users: (prev.users || []).map(u => {
        if (u.id !== currentUser.id) return u;
        const aiStyles = Object.assign({}, u.aiStyles || {});
        if (value) aiStyles[lang] = value; else delete aiStyles[lang];
        return { ...u, aiStyles };
      }),
    }));
  };

  return (
    <div style={{maxWidth:680, display:'flex', flexDirection:'column', gap:14}}>
      <div className="product-card" style={{padding:20}}>
        <div style={{fontSize:14, fontWeight:600, marginBottom:4}}>
          Tu tono y estilo · {currentUser.name}
        </div>
        <div style={{fontSize:12, color:'var(--text-muted)', marginBottom:14, lineHeight:1.5}}>
          Estas instrucciones se inyectan como <em>system prompt</em> cuando pides al asistente que escriba o reescriba un texto. Solo afectan a tus generaciones — cada usuario tiene su propio tono.
        </div>
        <div style={{display:'flex', flexDirection:'column', gap:12}}>
          {['es','fr','de','en','nl'].map(l => (
            <div key={l}>
              <div style={{fontSize:11, fontWeight:600, textTransform:'uppercase', letterSpacing:'0.08em', marginBottom:4, color:'var(--text-muted)'}}>{langLabels[l]} · {l.toUpperCase()}</div>
              <textarea
                className="textarea"
                rows={3}
                value={styles[l] || ''}
                placeholder={'Tono para ' + langLabels[l] + '…'}
                onChange={e => updateStyle(l, e.target.value)}
              />
            </div>
          ))}
        </div>
      </div>

      <div style={{padding:'12px 14px', background:'color-mix(in oklch, var(--accent) 8%, var(--bg-panel))', borderRadius:'var(--r-sm)', fontSize:11.5, color:'var(--text-muted)', lineHeight:1.6}}>
        <strong style={{color:'var(--text)'}}>Cómo se usa:</strong> Compositor → click en cualquier bloque de texto → botón <Icon name="sparkles" size={11}/> IA → describe la idea en lenguaje natural. El asistente responde en el idioma activo siguiendo tu tono.
      </div>
    </div>
  );
}

/* Admin-only overview of every user's AI tone prompts. Shows all 5
   languages × all users in a collapsible per-user section, lets the admin
   edit anyone's prompts in place. */
function AiStylesAdminOverview({ appState, setAppState }) {
  const users = (appState && appState.users) || [];
  const [openId, setOpenId] = React.useState(users[0]?.id || null);
  const langLabels = { es:'Español', fr:'Français', de:'Deutsch', en:'English', nl:'Nederlands' };

  const updateStyle = (userId, lang, value) => {
    setAppState(prev => ({
      ...prev,
      users: (prev.users || []).map(u => {
        if (u.id !== userId) return u;
        const aiStyles = Object.assign({}, u.aiStyles || {});
        if (value) aiStyles[lang] = value; else delete aiStyles[lang];
        return { ...u, aiStyles };
      }),
    }));
  };

  if (users.length === 0) return <div style={{fontSize:12, color:'var(--text-muted)'}}>Sin usuarios.</div>;

  return (
    <div style={{display:'flex', flexDirection:'column', gap:8}}>
      {users.map(u => {
        const open = openId === u.id;
        const styles = u.aiStyles || {};
        const langCount = Object.keys(styles).filter(k => styles[k]).length;
        return (
          <div key={u.id} style={{border:'1px solid var(--border)', borderRadius:'var(--r-sm)', overflow:'hidden'}}>
            <button
              onClick={() => setOpenId(open ? null : u.id)}
              style={{
                width:'100%', display:'flex', alignItems:'center', gap:10, padding:'10px 12px',
                background: open ? 'var(--bg-sunken)' : 'var(--bg-panel)', textAlign:'left', borderBottom: open ? '1px solid var(--border)' : 'none',
              }}
            >
              <span style={{fontSize:11, fontFamily:'var(--font-mono)', textTransform:'uppercase', letterSpacing:1, padding:'2px 6px', borderRadius:4, fontWeight:700,
                background: u.role === 'admin' ? 'color-mix(in oklch, var(--mbo) 15%, transparent)' : 'color-mix(in oklch, var(--artisjet) 12%, transparent)',
                color: u.role === 'admin' ? 'var(--mbo)' : 'var(--artisjet)'}}>{u.role}</span>
              <span style={{fontWeight:500}}>{u.name}</span>
              <span style={{marginLeft:'auto', fontSize:11, color:'var(--text-muted)', fontFamily:'var(--font-mono)'}}>
                {langCount}/5 idioma{langCount === 1 ? '' : 's'}
              </span>
              <Icon name="chevron" size={12} />
            </button>
            {open && (
              <div style={{padding:12, display:'flex', flexDirection:'column', gap:10, background:'var(--bg-panel)'}}>
                {['es','fr','de','en','nl'].map(l => (
                  <div key={l}>
                    <div style={{fontSize:10, fontWeight:600, textTransform:'uppercase', letterSpacing:'0.06em', marginBottom:3, color:'var(--text-muted)'}}>{langLabels[l]} · {l.toUpperCase()}</div>
                    <textarea
                      className="textarea"
                      rows={2}
                      value={styles[l] || ''}
                      placeholder={'Tono ' + langLabels[l] + ' para ' + u.name + '…'}
                      onChange={e => updateStyle(u.id, l, e.target.value)}
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* OpenAI key panel — admin-only. The API key persists in appState (Supabase
   + localStorage) so every device that opens the app gets it. Per-language
   tone prompts no longer live here — they moved to each user's profile
   under Backoffice → Usuarios. */
function AISettingsPanel({ appState, setAppState }) {
  const apiKey = (appState && appState.openaiKey) || '';
  const [tested, setTested] = React.useState('');
  const [testing, setTesting] = React.useState(false);

  const onChangeKey = (v) => {
    if (setAppState) setAppState(prev => ({ ...prev, openaiKey: v }));
    try { sessionStorage.removeItem('bomedia_openai_key'); } catch (e) {}
    setTested('');
  };

  const test = () => {
    if (typeof callOpenAI !== 'function') return;
    setTesting(true);
    callOpenAI({ notes: 'Test rápido: di "ok" en una frase corta.', lang: 'es', mode: 'generate' })
      .then(reply => { setTested('✓ ' + reply.slice(0, 80)); })
      .catch(err => { setTested('Error: ' + err.message); })
      .finally(() => setTesting(false));
  };

  return (
    <div style={{maxWidth:680, display:'flex', flexDirection:'column', gap:14}}>
      <div className="product-card" style={{padding:20}}>
        <div style={{display:'flex', alignItems:'center', gap:10, marginBottom:4}}>
          <Icon name="sparkles" size={18}/>
          <div style={{fontSize:15, fontWeight:600}}>Clave OpenAI</div>
        </div>
        <div style={{fontSize:12, color:'var(--text-muted)', marginBottom:12}}>
          Guardada en <strong>Supabase</strong> — la comparten todos los usuarios. Asegúrate de mantener la contraseña de admin fuerte: cualquiera con acceso al Backoffice como admin puede leer la key.
        </div>
        <div style={{display:'flex', gap:8}}>
          <input
            type="password"
            value={apiKey}
            onChange={e => onChangeKey(e.target.value)}
            placeholder="sk-…"
            style={{flex:1, padding:'8px 12px', border:'1px solid var(--border)', borderRadius:'var(--r-sm)', fontSize:13, fontFamily:'var(--font-mono)', background:'var(--bg-panel)'}}
          />
          <button className="btn btn-outline" onClick={test} disabled={!apiKey || testing} style={{fontSize:12, whiteSpace:'nowrap'}}>
            {testing ? 'Probando…' : 'Probar conexión'}
          </button>
        </div>
        {apiKey && (
          <div style={{marginTop:8, fontSize:12, color:'var(--success)', fontWeight:500}}>✓ API key configurada</div>
        )}
        {tested && (
          <div style={{marginTop:8, fontSize:11, padding:'6px 10px', background:'var(--bg-sunken)', borderRadius:'var(--r-sm)', fontFamily:'var(--font-mono)', color: tested.startsWith('Error') ? 'var(--danger)' : 'var(--success)'}}>
            {tested}
          </div>
        )}
      </div>

      <div className="product-card" style={{padding:20}}>
        <div style={{display:'flex', alignItems:'baseline', justifyContent:'space-between', marginBottom:6, gap:10}}>
          <div style={{fontSize:14, fontWeight:600}}>Tono y estilo · resumen por usuario</div>
          <span style={{fontSize:11, color:'var(--text-muted)'}}>Cada usuario usa su propio tono al pedir IA</span>
        </div>
        <AiStylesAdminOverview appState={appState} setAppState={setAppState} />
      </div>

      <AutoTranslatePanel appState={appState} setAppState={setAppState} />

      <div style={{padding:'12px 14px', background:'color-mix(in oklch, var(--accent) 8%, var(--bg-panel))', borderRadius:'var(--r-sm)', fontSize:11.5, color:'var(--text-muted)', lineHeight:1.6}}>
        <strong style={{color:'var(--text)'}}>Cómo usarlo:</strong> abre cualquier bloque de texto en el Compositor → botón <Icon name="sparkles" size={11}/> IA → describe la idea → el asistente genera el párrafo en el idioma activo, siguiendo el tono fijado para tu usuario.
      </div>
    </div>
  );
}

/* ──────────────── Auto-translate panel ────────────────
   Bulk-fills missing i18n.<lang>.<field> for templates, composed blocks,
   prewritten texts and standalone blocks. Products are intentionally
   excluded (machine names should never be translated). */
function AutoTranslatePanel({ appState, setAppState }) {
  const [langs, setLangs] = React.useState({ fr:true, de:true, en:true, nl:true });
  const [overwrite, setOverwrite] = React.useState(false);
  const [running, setRunning] = React.useState(false);
  const [progress, setProgress] = React.useState('');
  const [result, setResult] = React.useState('');

  // Build the list of items missing translations. Each entry is
  // { kind, idx, field, text } and the panel summary shows totals per kind.
  function gatherWork(state, targetLangs, force) {
    const work = [];
    const push = (kind, idx, field, text, i18n) => {
      if (!text || typeof text !== 'string') return;
      const missing = targetLangs.filter(L => force || !i18n || !i18n[L] || !i18n[L][field]);
      if (missing.length > 0) work.push({ kind, idx, field, text, missing });
    };
    (state.templates || []).forEach((t, i) => {
      push('templates', i, 'name', t.name, t.i18n);
      push('templates', i, 'desc', t.desc, t.i18n);
    });
    (state.composedBlocks || []).forEach((c, i) => {
      push('composedBlocks', i, 'title', c.title, c.i18n);
      push('composedBlocks', i, 'desc', c.desc, c.i18n);
      push('composedBlocks', i, 'introText', c.introText, c.i18n);
    });
    (state.prewrittenTexts || []).forEach((p, i) => {
      push('prewrittenTexts', i, 'name', p.name, p.i18n);
      push('prewrittenTexts', i, 'text', p.text, p.i18n);
    });
    (state.standaloneBlocks || []).forEach((s, i) => {
      push('standaloneBlocks', i, 'title', s.title, s.i18n);
      push('standaloneBlocks', i, 'desc', s.desc, s.i18n);
    });
    return work;
  }

  const targetLangs = ['fr','de','en','nl'].filter(L => langs[L]);
  const pendingWork = gatherWork(appState, targetLangs, overwrite);
  const summary = pendingWork.reduce((acc, w) => {
    acc[w.kind] = (acc[w.kind] || 0) + 1;
    return acc;
  }, {});
  const totalMissing = pendingWork.reduce((n, w) => n + w.missing.length, 0);

  async function run() {
    if (running) return;
    if (!appState.openaiKey) { setResult('Falta la API key de OpenAI más arriba.'); return; }
    if (targetLangs.length === 0) { setResult('Selecciona al menos un idioma.'); return; }
    const work = pendingWork;
    if (work.length === 0) { setResult('No hay nada que traducir — todo está completo.'); return; }
    setRunning(true);
    setResult('');
    // Batch in chunks of ~10 items per request — text bodies can be long, so
    // smaller chunks keep us safely under the model's max_tokens budget.
    const CHUNK = 10;
    const chunks = [];
    for (let i = 0; i < work.length; i += CHUNK) chunks.push(work.slice(i, i + CHUNK));
    const allTranslations = []; // [{ workItem, byLang }]
    try {
      for (let ci = 0; ci < chunks.length; ci++) {
        setProgress('Lote ' + (ci+1) + '/' + chunks.length + '…');
        const items = chunks[ci].map((w, i) => ({ i, text: w.text }));
        const res = await callOpenAITranslateBatch({ items, targetLangs });
        chunks[ci].forEach((w, i) => {
          const byLang = res[String(i)] || {};
          allTranslations.push({ workItem: w, byLang });
        });
      }
      // Apply all translations in one setState pass.
      setAppState(prev => {
        const next = { ...prev };
        const byKind = {};
        allTranslations.forEach(({ workItem, byLang }) => {
          const { kind, idx, field, missing } = workItem;
          if (!next[kind]) return;
          if (!byKind[kind]) byKind[kind] = next[kind].slice();
          const arr = byKind[kind];
          const item = { ...arr[idx] };
          item.i18n = { ...(item.i18n || {}) };
          missing.forEach(L => {
            const v = byLang[L];
            if (typeof v !== 'string' || !v) return;
            item.i18n[L] = { ...(item.i18n[L] || {}), [field]: v };
          });
          arr[idx] = item;
        });
        Object.keys(byKind).forEach(k => { next[k] = byKind[k]; });
        return next;
      });
      setResult('✓ Traducidos ' + work.length + ' campos en ' + targetLangs.length + ' idiomas.');
    } catch (e) {
      setResult('Error: ' + (e.message || String(e)));
    } finally {
      setRunning(false);
      setProgress('');
    }
  }

  return (
    <div className="product-card" style={{padding:20}}>
      <div style={{display:'flex', alignItems:'center', gap:10, marginBottom:4}}>
        <Icon name="sparkles" size={18}/>
        <div style={{fontSize:15, fontWeight:600}}>Auto-traducir nombres</div>
      </div>
      <div style={{fontSize:12, color:'var(--text-muted)', marginBottom:12, lineHeight:1.6}}>
        Rellena las traducciones que falten (FR / DE / EN / NL) en plantillas, bloques compuestos, textos predefinidos y bloques sueltos. <strong>Los nombres de máquinas y marcas se preservan literalmente</strong>. Los productos no se tocan.
      </div>

      <div style={{display:'flex', gap:14, flexWrap:'wrap', marginBottom:12}}>
        {['fr','de','en','nl'].map(L => (
          <label key={L} style={{display:'flex', alignItems:'center', gap:6, fontSize:12, cursor:'pointer'}}>
            <input type="checkbox" checked={!!langs[L]} onChange={e => setLangs(prev => ({...prev, [L]: e.target.checked}))} />
            <span>{(LANG_LABELS && LANG_LABELS[L]) || L.toUpperCase()}</span>
          </label>
        ))}
        <label style={{display:'flex', alignItems:'center', gap:6, fontSize:12, cursor:'pointer', marginLeft:'auto'}}>
          <input type="checkbox" checked={overwrite} onChange={e => setOverwrite(e.target.checked)} />
          <span>Sobrescribir traducciones existentes</span>
        </label>
      </div>

      <div style={{fontSize:11.5, color:'var(--text-muted)', marginBottom:10, padding:'8px 10px', background:'var(--bg-sunken)', borderRadius:'var(--r-sm)'}}>
        Pendiente: <strong style={{color:'var(--text)'}}>{pendingWork.length}</strong> campos / <strong style={{color:'var(--text)'}}>{totalMissing}</strong> traducciones
        {Object.keys(summary).length > 0 && (
          <span style={{marginLeft:8, color:'var(--text-muted)'}}>
            ({Object.entries(summary).map(([k,v]) => k + ':' + v).join(' · ')})
          </span>
        )}
      </div>

      <div style={{display:'flex', gap:8, alignItems:'center'}}>
        <button
          className="btn btn-primary"
          onClick={run}
          disabled={running || pendingWork.length === 0 || !appState.openaiKey}
          style={{fontSize:13}}
        >
          {running ? (progress || 'Traduciendo…') : 'Traducir ahora'}
        </button>
        {result && (
          <span style={{fontSize:12, color: result.startsWith('Error') ? 'var(--danger)' : 'var(--success)', fontWeight:500}}>
            {result}
          </span>
        )}
      </div>
    </div>
  );
}

/* ────────────── Image upload input ──────────────
   Drop-in replacement for a plain URL <input>: shows the URL field plus a
   "Subir" button that opens the file picker, uploads to Supabase Storage,
   and writes the resulting public URL back through onChange. Renders a 60px
   thumbnail when there's a value so the user sees what's loaded. */
function ImageUploadInput({ value, onChange, placeholder, prefix, brand: presetBrand }) {
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState('');
  const [libOpen, setLibOpen] = React.useState(false);
  // Pending file waiting for the user to pick a brand. Cuando el caller no
  // pasa `brand` como prop (contextos sin marca implícita — bloque imagen
  // suelto en composer/compuesto, biblioteca admin), pedimos la marca antes
  // de subir. Si el caller sí pasa brand (editor de producto, marca, hero
  // ya vinculado a una marca), saltamos este paso.
  const [pendingFile, setPendingFile] = React.useState(null);
  const [pickedBrand, setPickedBrand] = React.useState(presetBrand || '');
  const fileRef = React.useRef(null);
  const onPick = () => fileRef.current && fileRef.current.click();
  const appState = (typeof window !== 'undefined' && window.__appState) || {};
  const setAppState = (typeof window !== 'undefined' && window.__setAppState) || (() => {});
  const Lib = (typeof window !== 'undefined' && window.ImageLibraryModal) || null;
  const allBrands = ((typeof window !== 'undefined' && window.BRANDS) || (typeof BRANDS !== 'undefined' ? BRANDS : []) || [])
    .filter(b => b.id !== 'bomedia');

  const doUpload = (file, brandTag) => {
    const upload = (typeof window !== 'undefined' && typeof window.uploadImage === 'function') ? window.uploadImage : null;
    if (!upload) {
      setErr('Subida no disponible (módulo de uploads no cargado)');
      return;
    }
    setBusy(true); setErr('');
    upload(file, { prefix })
      .then(url => {
        onChange(url);
        try {
          if (typeof window.recordUploadedImage === 'function') {
            window.recordUploadedImage({
              url,
              name: file.name || '',
              size: file.size,
              addedAt: Date.now(),
              brand: brandTag || null,
            });
          }
        } catch (e) {}
      })
      .catch(e2 => setErr(e2.message || String(e2)))
      .finally(() => { setBusy(false); setPendingFile(null); });
  };

  const onFile = (e) => {
    const f = e.target.files && e.target.files[0];
    e.target.value = '';
    if (!f) return;
    if (presetBrand) {
      // El caller ha fijado la marca — subir directamente con esa marca.
      doUpload(f, presetBrand);
      return;
    }
    // Pedir marca antes de subir.
    setPendingFile(f);
  };
  const confirmUpload = () => {
    if (!pendingFile) return;
    doUpload(pendingFile, pickedBrand || null);
  };
  const cancelUpload = () => { setPendingFile(null); };

  const showThumb = value && /^(https?:|data:)/i.test(value);
  return (
    <div style={{display:'flex', flexDirection:'column', gap:6}}>
      <div style={{display:'flex', gap:6, flexWrap:'wrap'}}>
        <input className="input" style={{flex:'1 1 200px', minWidth:140}} value={value || ''} onChange={e => onChange(e.target.value)} placeholder={placeholder || 'https://… pegar URL'} />
        {Lib && (
          <button type="button" className="btn btn-outline" style={{fontSize:11, whiteSpace:'nowrap', padding:'6px 10px'}} onClick={() => setLibOpen(true)} disabled={busy} title="Elegir de la biblioteca de imágenes">
            <Icon name="copy" size={11}/> Biblioteca
          </button>
        )}
        <button type="button" className="btn btn-outline" style={{fontSize:11, whiteSpace:'nowrap', padding:'6px 10px'}} onClick={onPick} disabled={busy} title="Subir un archivo nuevo">
          {busy ? 'Subiendo…' : <><Icon name="download" size={11}/> Subir</>}
        </button>
        <input type="file" accept="image/*" ref={fileRef} onChange={onFile} style={{display:'none'}} />
      </div>
      {pendingFile && (
        <div style={{padding:8, border:'1px solid var(--accent)', background:'var(--bg-sunken)', borderRadius:'var(--r-sm)', display:'flex', flexDirection:'column', gap:6}}>
          <div style={{fontSize:11, color:'var(--text-muted)'}}>
            <strong>"{pendingFile.name}"</strong> · selecciona la marca antes de subir:
          </div>
          <div style={{display:'flex', gap:6}}>
            <select className="select" style={{flex:1, fontSize:11}} value={pickedBrand} onChange={e => setPickedBrand(e.target.value)}>
              <option value="">— Sin marca específica —</option>
              {allBrands.map(b => <option key={b.id} value={b.id}>{b.label}</option>)}
            </select>
            <button type="button" className="btn btn-primary" style={{fontSize:11, padding:'6px 10px'}} onClick={confirmUpload}>Subir</button>
            <button type="button" className="btn btn-ghost" style={{fontSize:11, padding:'6px 10px'}} onClick={cancelUpload}>Cancelar</button>
          </div>
        </div>
      )}
      {showThumb && (
        <img src={value} alt="" style={{width:64, height:64, objectFit:'contain', borderRadius:4, border:'1px solid var(--border)', background:'var(--bg-sunken)', padding:4}} onError={e => { e.target.style.display='none'; }} />
      )}
      {err && <div style={{fontSize:11, color:'var(--danger)', lineHeight:1.4}}>{err}</div>}
      {libOpen && Lib && (
        <Lib appState={appState} setAppState={setAppState} onPick={url => { onChange(url); setLibOpen(false); }} onClose={() => setLibOpen(false)} />
      )}
    </div>
  );
}

/* ────────────── Backoffice → Imágenes (admin only) ──────────────
   Lista todas las imágenes que la app conoce: subidas previas + las que
   se referencian indirectamente (productos, marcas, heroes). Permite
   borrar (de la biblioteca, no del WP origen) y subir nuevas. */
function ImageLibraryAdminPanel({ appState, setAppState }) {
  const [filter, setFilter] = React.useState('upload');
  const [brandFilter, setBrandFilter] = React.useState('all');
  const [activeTags, setActiveTags] = React.useState([]);
  const [tagSearch, setTagSearch] = React.useState('');
  const [search, setSearch] = React.useState('');

  const items = React.useMemo(() => _collectKnownImages(appState || {}), [appState]);
  const libCount = items.filter(i => i.source === 'library').length;
  const groups = [
    { id: 'all', label: 'Todas', count: items.length },
    { id: 'upload', label: 'Subidas', count: items.filter(i => i.source === 'upload').length },
    // "Biblioteca" sólo aparece como chip si hay items con source 'library'
    // (vienen del JSON importado). Si el user no ha importado un JSON con
    // imageLibrary, el chip se omite para no ensuciar la UI.
    ...(libCount > 0 ? [{ id: 'library', label: 'Biblioteca', count: libCount }] : []),
    { id: 'product', label: 'Productos', count: items.filter(i => i.source === 'product').length },
    { id: 'brand', label: 'Marcas', count: items.filter(i => i.source === 'brand').length },
    { id: 'hero', label: 'Heroes', count: items.filter(i => i.source === 'hero').length },
  ];
  const allBrands = ((typeof window !== 'undefined' && window.BRANDS) || (appState && appState.brands) || []).filter(b => b.id !== 'bomedia');
  // Recuento por marca (sobre el filtro de fuente activo) para mostrar
  // chips con el número exacto de items por marca.
  const itemsAfterSource = filter === 'all' ? items : items.filter(i => i.source === filter);
  const brandCounts = (() => {
    const counts = { all: itemsAfterSource.length, none: 0 };
    for (const it of itemsAfterSource) {
      if (!it.brand) counts.none++;
      else counts[it.brand] = (counts[it.brand] || 0) + 1;
    }
    return counts;
  })();
  // Items para el tag-filter — pre-filtrados por source + brand para que
  // los tags y sus recuentos reflejen el subconjunto actualmente visible.
  const itemsForTagFilter = items.filter(it =>
    (filter === 'all' || it.source === filter) &&
    (brandFilter === 'all' || (brandFilter === 'none' ? !it.brand : it.brand === brandFilter))
  );
  // AND logic: la imagen debe llevar TODOS los tags activos.
  const matchesTags = (it) => {
    if (activeTags.length === 0) return true;
    const itTags = it.tags || [];
    return activeTags.every(t => itTags.indexOf(t) >= 0);
  };
  const filtered = items.filter(it =>
    (filter === 'all' || it.source === filter) &&
    (brandFilter === 'all' || (brandFilter === 'none' ? !it.brand : it.brand === brandFilter)) &&
    matchesTags(it) &&
    (!search || (it.label || '').toLowerCase().includes(search.toLowerCase()) || (it.url || '').toLowerCase().includes(search.toLowerCase()))
  );

  const removeFromLibrary = (url) => {
    if (!url) return;
    if (!window.confirm('¿Borrar esta imagen de la biblioteca?\n\nSe quitará de los thumbnails. El archivo en WordPress NO se borra (entra a boprint.net/wp-admin → Media para eliminarlo del servidor).')) return;
    setAppState(prev => ({
      ...prev,
      uploadedImages: ((prev && prev.uploadedImages) || []).filter(x => x.url !== url),
    }));
  };

  // Asignar / cambiar la marca de una imagen ya subida. Útil para
  // re-clasificar el legado o corregir un mismatch sin re-subir.
  const setUploadBrand = (url, newBrand) => {
    setAppState(prev => ({
      ...prev,
      uploadedImages: ((prev && prev.uploadedImages) || []).map(x =>
        x.url === url ? Object.assign({}, x, { brand: newBrand || null }) : x
      ),
    }));
  };

  return (
    <div style={{display:'flex', flexDirection:'column', gap:14, maxWidth:1100}}>
      <BulkImageUploader setAppState={setAppState} />

      <div className="product-card" style={{padding:18}}>
        <div style={{display:'flex', flexDirection:'column', gap:10, marginBottom:12}}>
          <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', gap:12, flexWrap:'wrap'}}>
            <div style={{display:'flex', gap:6, flexWrap:'wrap'}}>
              {groups.map(g => (
                <button key={g.id} className={'brand-chip' + (filter === g.id ? ' active' : '')} onClick={() => setFilter(g.id)}>
                  {g.label} <span className="mono" style={{opacity:0.6}}>{g.count}</span>
                </button>
              ))}
            </div>
            <div className="bo-search" style={{minWidth:220}}>
              <Icon name="search" size={14}/>
              <input placeholder="Buscar por nombre o URL…" value={search} onChange={e => setSearch(e.target.value)} />
            </div>
          </div>
          <div style={{display:'flex', alignItems:'center', gap:8, flexWrap:'wrap', borderTop:'1px solid var(--border)', paddingTop:10}}>
            <span style={{fontSize:11, color:'var(--text-muted)', fontWeight:600, letterSpacing:0.3}}>Marca:</span>
            <button className={'brand-chip' + (brandFilter === 'all' ? ' active' : '')} onClick={() => setBrandFilter('all')}>
              Todas <span className="mono" style={{opacity:0.6}}>{brandCounts.all}</span>
            </button>
            {allBrands.map(b => (
              <button key={b.id} className={'brand-chip' + (brandFilter === b.id ? ' active' : '')} onClick={() => setBrandFilter(b.id)} disabled={!brandCounts[b.id]} style={{opacity: brandCounts[b.id] ? 1 : 0.45}}>
                {b.label} <span className="mono" style={{opacity:0.6}}>{brandCounts[b.id] || 0}</span>
              </button>
            ))}
            <button className={'brand-chip' + (brandFilter === 'none' ? ' active' : '')} onClick={() => setBrandFilter('none')} disabled={!brandCounts.none} style={{opacity: brandCounts.none ? 1 : 0.45}}>
              Sin marca <span className="mono" style={{opacity:0.6}}>{brandCounts.none}</span>
            </button>
          </div>
          {/* Barra de tags — sólo se renderiza si los items actuales (tras
              source+brand filter) tienen algún tag. AND logic: la imagen
              debe llevar todos los tags activos. */}
          <div style={{borderTop:'1px solid var(--border)', paddingTop:10}}>
            <ImageTagFilter
              items={itemsForTagFilter}
              activeTags={activeTags}
              setActiveTags={setActiveTags}
              tagSearch={tagSearch}
              setTagSearch={setTagSearch}
            />
          </div>
        </div>

        {filter !== 'upload' && filter !== 'all' && filter !== 'library' && (
          <div style={{padding:'10px 12px', background:'var(--bg-sunken)', borderRadius:'var(--r-sm)', fontSize:11.5, color:'var(--text-muted)', marginBottom:12, lineHeight:1.5}}>
            ℹ️ Estas imágenes vienen del catálogo ({filter}). Para borrarlas, edita el producto/marca/hero correspondiente en su tab y limpia el campo de imagen.
          </div>
        )}
        {filter === 'library' && (
          <div style={{padding:'10px 12px', background:'var(--bg-sunken)', borderRadius:'var(--r-sm)', fontSize:11.5, color:'var(--text-muted)', marginBottom:12, lineHeight:1.5}}>
            ℹ️ Imágenes de <strong>biblioteca</strong> importadas vía JSON. Cada item lleva tags y referencia opcional a un producto. Para modificarlas hay que editar el JSON fuente y re-importar.
          </div>
        )}

        {filtered.length === 0 ? (
          <div style={{padding:'40px 20px', textAlign:'center', color:'var(--text-muted)', fontSize:13}}>
            {items.length === 0 ? 'No hay imágenes todavía. Sube las primeras arriba.' : 'Sin resultados con ese filtro.'}
          </div>
        ) : (
          <div style={{display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(180px, 1fr))', gap:12}}>
            {filtered.map((it, i) => {
              const brandObj = it.brand ? allBrands.find(b => b.id === it.brand) : null;
              return (
                <div key={i} style={{border:'1px solid var(--border)', borderRadius:'var(--r-sm)', overflow:'hidden', background:'var(--bg-panel)', display:'flex', flexDirection:'column'}}>
                  <div style={{aspectRatio:'4/3', display:'grid', placeItems:'center', overflow:'hidden', background:'#fff', position:'relative'}}>
                    <img src={it.url} alt={it.label} style={{maxWidth:'100%', maxHeight:'100%', objectFit:'contain'}} onError={e => { e.target.style.opacity = 0.2; }}/>
                    <span style={{position:'absolute', top:6, right:6, padding:'2px 6px', background:'rgba(0,0,0,0.7)', color:'#fff', borderRadius:4, fontSize:9, fontFamily:'var(--font-mono)'}}>{it.source}</span>
                    {brandObj && (
                      <span style={{position:'absolute', bottom:6, left:6, padding:'2px 7px', background: brandObj.color || '#444', color:'#fff', borderRadius:10, fontSize:9, fontWeight:700}}>{brandObj.label}</span>
                    )}
                  </div>
                  <div style={{padding:'8px 10px', display:'flex', flexDirection:'column', gap:4, flex:1}}>
                    <div style={{fontSize:11, fontWeight:600, color:'var(--text)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>{it.label || '(sin nombre)'}</div>
                    <div style={{fontSize:9, color:'var(--text-muted)', fontFamily:'var(--font-mono)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}} title={it.url}>{it.url}</div>
                    {it.source === 'upload' && (
                      <select className="select" value={it.brand || ''} onChange={e => setUploadBrand(it.url, e.target.value)} style={{fontSize:10, padding:'2px 6px', height:24}} title="Reasignar marca de esta imagen subida">
                        <option value="">Sin marca</option>
                        {allBrands.map(b => <option key={b.id} value={b.id}>{b.label}</option>)}
                      </select>
                    )}
                    <div style={{display:'flex', gap:4, marginTop:'auto', paddingTop:6}}>
                      <button className="btn btn-ghost" style={{fontSize:10, padding:'3px 8px', flex:1}} onClick={() => { navigator.clipboard?.writeText(it.url); }} title="Copiar URL">
                        <Icon name="copy" size={10}/> URL
                      </button>
                      <a className="btn btn-ghost" style={{fontSize:10, padding:'3px 8px', flex:1, textDecoration:'none', display:'inline-flex', alignItems:'center', justifyContent:'center', gap:4}} href={it.url} target="_blank" rel="noopener noreferrer" title="Abrir en pestaña nueva">
                        <Icon name="share" size={10}/> Ver
                      </a>
                      {it.source === 'upload' && (
                        <button className="btn btn-ghost" style={{fontSize:10, padding:'3px 8px', color:'var(--danger)'}} onClick={() => removeFromLibrary(it.url)} title="Quitar de la biblioteca">
                          <Icon name="trash" size={10}/>
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

/* Panel admin de actividad por usuario. Lee `appState.activityLog` (lista
   capped a 1000 que mantiene el helper window.logActivity, definido en
   app-main.jsx). Permite filtrar por usuario, tipo de acción y rango de
   fechas, y muestra los eventos agrupados por día. Solo visible al admin
   — la prop isAdmin se valida en el render de Backoffice. Apr 2026. */
const ACTIVITY_ACTION_LABELS = {
  // Sesión
  login: { icon: '→', label: 'Inicio de sesión', color: '#16a34a' },
  login_failed: { icon: '✗', label: 'Login fallido', color: '#dc2626' },
  logout: { icon: '←', label: 'Cierre de sesión', color: '#94a3b8' },
  // Email composer
  block_add: { icon: '+', label: 'Añadió bloque', color: '#0891b2' },
  email_copy: { icon: '⎘', label: 'Copió HTML del email', color: '#2563eb' },
  email_html_download: { icon: '↓', label: 'Descargó .html', color: '#2563eb' },
  email_pdf_export: { icon: '↓', label: 'Exportó a PDF', color: '#7c3aed' },
  email_doc_export: { icon: '↓', label: 'Exportó a Word', color: '#7c3aed' },
  // Plantillas
  template_create: { icon: '★', label: 'Creó plantilla', color: '#16a34a' },
  template_update: { icon: '✎', label: 'Actualizó plantilla', color: '#0891b2' },
  template_load: { icon: '▶', label: 'Cargó plantilla', color: '#94a3b8' },
  // Catálogo
  product_create: { icon: '+', label: 'Creó producto', color: '#16a34a' },
  product_update: { icon: '✎', label: 'Actualizó producto', color: '#0891b2' },
  brand_create: { icon: '+', label: 'Creó marca', color: '#16a34a' },
  brand_update: { icon: '✎', label: 'Actualizó marca', color: '#0891b2' },
  text_create: { icon: '+', label: 'Creó texto', color: '#16a34a' },
  text_update: { icon: '✎', label: 'Actualizó texto', color: '#0891b2' },
  standalone_create: { icon: '+', label: 'Creó bloque suelto', color: '#16a34a' },
  standalone_update: { icon: '✎', label: 'Actualizó bloque suelto', color: '#0891b2' },
  composed_create: { icon: '+', label: 'Creó bloque compuesto', color: '#16a34a' },
  composed_update: { icon: '✎', label: 'Actualizó bloque compuesto', color: '#0891b2' },
  cta_create: { icon: '+', label: 'Creó CTA', color: '#16a34a' },
  cta_update: { icon: '✎', label: 'Actualizó CTA', color: '#0891b2' },
  user_create: { icon: '+', label: 'Creó usuario', color: '#16a34a' },
  user_update: { icon: '✎', label: 'Actualizó usuario', color: '#0891b2' },
  // Imágenes + IA
  image_upload: { icon: '↑', label: 'Subió imagen', color: '#ea580c' },
  ai_agent_run: { icon: '✨', label: 'Ejecutó IA', color: '#a855f7' },
};

function _activityLabelFor(action) {
  return ACTIVITY_ACTION_LABELS[action] || { icon: '·', label: action, color: '#94a3b8' };
}

/* Format relative time: "ahora", "hace 3 min", "hace 2 h" — para timestamps
   recientes. Para más viejos cae a fecha absoluta. */
function _activityTime(ts, now) {
  const diff = now - ts;
  if (diff < 30 * 1000) return 'ahora';
  if (diff < 60 * 1000) return 'hace ' + Math.floor(diff / 1000) + ' s';
  if (diff < 60 * 60 * 1000) return 'hace ' + Math.floor(diff / 60000) + ' min';
  if (diff < 24 * 60 * 60 * 1000) return 'hace ' + Math.floor(diff / 3600000) + ' h';
  const d = new Date(ts);
  return d.toLocaleString('es-ES', { hour:'2-digit', minute:'2-digit' });
}
function _activityDayLabel(ts) {
  const d = new Date(ts);
  const today = new Date();
  const sameDay = d.getFullYear() === today.getFullYear() && d.getMonth() === today.getMonth() && d.getDate() === today.getDate();
  if (sameDay) return 'Hoy';
  const y = new Date(today); y.setDate(y.getDate() - 1);
  if (d.getFullYear() === y.getFullYear() && d.getMonth() === y.getMonth() && d.getDate() === y.getDate()) return 'Ayer';
  return d.toLocaleDateString('es-ES', { weekday:'long', day:'numeric', month:'long', year: d.getFullYear() !== today.getFullYear() ? 'numeric' : undefined });
}

/* Reconstruye eventos pasados a partir de campos persistidos en appState
   (uploadedImages.addedAt, items.createdBy + createdAt). Los marca con
   `synthetic:true` para que se distingan de eventos en tiempo real, y
   asigna ids estables `synth-<source>-<originalId>` para que re-ejecutar
   el backfill no duplique entradas (set por id en el merge).
   Devuelve un array de eventos ordenados por ts ascendente. */
function _backfillActivityFromHistory(appState) {
  if (!appState) return [];
  const out = [];
  const push = (ts, action, userId, details, idSuffix) => {
    if (!ts) return;
    out.push({
      id: 'synth-' + idSuffix,
      ts: Number(ts),
      userId: userId || null,
      action,
      details: details || {},
      synthetic: true,
    });
  };
  // 1) Subidas de imagen — uploadedImages[].addedAt
  const imgs = Array.isArray(appState.uploadedImages) ? appState.uploadedImages : [];
  imgs.forEach((it, i) => {
    if (!it || !it.addedAt) return;
    push(it.addedAt, 'image_upload', null, {
      url: it.url, name: it.name, size: it.size, brand: it.brand || null,
    }, 'img-' + i + '-' + (it.url || '').slice(-30));
  });
  // 2) Creaciones de items con createdBy + createdAt — distintos kinds.
  const kindMap = [
    { coll: 'templates', kind: 'template' },
    { coll: 'composedBlocks', kind: 'composed' },
    { coll: 'standaloneBlocks', kind: 'standalone' },
    { coll: 'prewrittenTexts', kind: 'text' },
    { coll: 'ctaBlocks', kind: 'cta' },
    { coll: 'products', kind: 'product' },
    { coll: 'brands', kind: 'brand' },
  ];
  kindMap.forEach(({ coll, kind }) => {
    const list = Array.isArray(appState[coll]) ? appState[coll] : [];
    list.forEach(item => {
      if (!item || !item.createdBy || !item.createdAt) return;
      push(item.createdAt, kind + '_create', item.createdBy, {
        collection: coll,
        id: item.id,
        name: item.name || item.title || item.label || item.id,
      }, kind + '-' + item.id);
    });
  });
  // Orden cronológico ascendente para que el merge respete el orden global
  out.sort((a, b) => a.ts - b.ts);
  return out;
}

function ActivityPanel({ appState, setAppState }) {
  const log = Array.isArray(appState?.activityLog) ? appState.activityLog : [];
  const users = (appState && appState.users) || [];
  const userById = React.useMemo(() => {
    const m = {};
    users.forEach(u => { m[u.id] = u; });
    return m;
  }, [users]);

  const [userFilter, setUserFilter] = React.useState('all');
  const [actionFilter, setActionFilter] = React.useState('all');
  const [search, setSearch] = React.useState('');
  const [now, setNow] = React.useState(Date.now());

  // Refresca timestamps relativos cada 30 s — sin esto "hace 3 min" se queda
  // congelado mientras el panel está abierto.
  React.useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(t);
  }, []);

  // Conteos para los chips de filtro (sobre TODO el log, sin aplicar filtros)
  const counts = React.useMemo(() => {
    const c = { all: log.length, byUser: {}, byAction: {} };
    for (const e of log) {
      const u = e.userId || '__none__';
      c.byUser[u] = (c.byUser[u] || 0) + 1;
      c.byAction[e.action] = (c.byAction[e.action] || 0) + 1;
    }
    return c;
  }, [log]);

  // Lista filtrada — más reciente primero. activityLog se guarda en orden
  // cronológico ascendente, así que lo invertimos para presentación.
  const filtered = React.useMemo(() => {
    const sLow = search.trim().toLowerCase();
    const out = [];
    for (let i = log.length - 1; i >= 0; i--) {
      const e = log[i];
      if (userFilter !== 'all') {
        if (userFilter === 'none' ? e.userId : e.userId !== userFilter) continue;
      }
      if (actionFilter !== 'all' && e.action !== actionFilter) continue;
      if (sLow) {
        const haystack = [
          e.action,
          (userById[e.userId] && userById[e.userId].name) || '',
          JSON.stringify(e.details || {}),
        ].join(' ').toLowerCase();
        if (!haystack.includes(sLow)) continue;
      }
      out.push(e);
    }
    return out;
  }, [log, userFilter, actionFilter, search, userById]);

  // Agrupar por día para los headers "Hoy / Ayer / 28 abr 2026".
  const groupedByDay = React.useMemo(() => {
    const out = [];
    let currentDay = null;
    let currentBucket = null;
    for (const e of filtered) {
      const dayKey = new Date(e.ts).toISOString().slice(0, 10);
      if (dayKey !== currentDay) {
        currentDay = dayKey;
        currentBucket = { day: dayKey, label: _activityDayLabel(e.ts), events: [] };
        out.push(currentBucket);
      }
      currentBucket.events.push(e);
    }
    return out;
  }, [filtered]);

  // Acciones únicas presentes en el log (para el dropdown de filtro de acción)
  const actionsInLog = React.useMemo(() => {
    return Array.from(new Set(log.map(e => e.action))).sort();
  }, [log]);

  const clearLog = () => {
    if (!window.confirm('¿Borrar TODO el registro de actividad?\n\nNo afecta a los datos del email composer — solo borra el historial de eventos.')) return;
    setAppState(prev => Object.assign({}, prev, { activityLog: [] }));
  };
  // Reconstruye eventos pasados a partir de campos persistidos en otros
  // sitios del state (uploadedImages.addedAt, items.createdBy/createdAt).
  // Idempotente: usa ids estables `synth-…` así que re-ejecutar solo
  // reemplaza los sintéticos previos, no los duplica.
  const synthCount = React.useMemo(() => _backfillActivityFromHistory(appState).length, [appState]);
  const backfillLog = () => {
    const synthesized = _backfillActivityFromHistory(appState);
    if (synthesized.length === 0) {
      window.alert('No se encontraron eventos pasados que reconstruir. Las imágenes subidas tienen addedAt y los items recientes (creados tras el último fix) tienen createdBy/createdAt — todo lo demás no se guardaba.');
      return;
    }
    if (!window.confirm('Reconstruir ' + synthesized.length + ' eventos sintéticos a partir del historial guardado.\n\nIncluye: subidas de imagen + creaciones de items con autor conocido.\nNO incluye: copias de email, cargas de plantilla, logins, etc. (no se guardaban antes).\n\nLos eventos sintéticos llevan flag `synthetic:true` y se distinguen de los reales. ¿Continuar?')) return;
    setAppState(prev => {
      const existing = Array.isArray(prev.activityLog) ? prev.activityLog : [];
      // Quitamos sintéticos previos (los reemplazamos por el nuevo set,
      // por si hay items nuevos desde el último backfill); conservamos
      // todo evento real (no synthetic).
      const real = existing.filter(e => !e.synthetic);
      // Merge + sort + cap a 1000
      const merged = [...synthesized, ...real].sort((a, b) => a.ts - b.ts);
      return Object.assign({}, prev, { activityLog: merged.slice(-1000) });
    });
  };
  const exportLog = () => {
    try {
      const blob = new Blob([JSON.stringify(log, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'bomedia-activity-' + new Date().toISOString().slice(0, 10) + '.json';
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { alert('No se pudo exportar: ' + e.message); }
  };

  const renderDetails = (details) => {
    if (!details || typeof details !== 'object' || !Object.keys(details).length) return null;
    // Mostrar campos prioritarios primero — el resto en JSON compacto.
    const priority = ['name', 'templateName', 'title', 'prompt', 'type', 'url', 'blockCount', 'lang', 'role'];
    const parts = [];
    for (const k of priority) {
      if (details[k] != null && details[k] !== '') {
        parts.push(<span key={k} style={{marginRight:8}}><span style={{color:'var(--text-subtle)'}}>{k}:</span> {String(details[k]).slice(0, 80)}</span>);
      }
    }
    return parts.length > 0 ? <div style={{fontSize:11, color:'var(--text-muted)', marginTop:2}}>{parts}</div> : null;
  };

  return (
    <div style={{display:'flex', flexDirection:'column', gap:14, maxWidth:1100}}>
      <div className="product-card" style={{padding:18}}>
        <div style={{display:'flex', alignItems:'center', gap:10, marginBottom:10, flexWrap:'wrap'}}>
          <Icon name="eye" size={18}/>
          <div style={{fontSize:14, fontWeight:600, flex:1}}>Registro de actividad · {log.length} eventos</div>
          <button
            className="btn btn-ghost"
            style={{fontSize:11}}
            onClick={backfillLog}
            disabled={synthCount === 0}
            title={synthCount > 0
              ? 'Reconstruye ' + synthCount + ' eventos pasados a partir de los timestamps guardados en uploadedImages y los createdBy/createdAt de items.'
              : 'Sin eventos pasados que reconstruir.'}
          >
            <Icon name="undo" size={11}/> Reconstruir desde historial {synthCount > 0 && <span style={{marginLeft:4, fontSize:10, color:'var(--text-muted)'}}>({synthCount})</span>}
          </button>
          <button className="btn btn-ghost" style={{fontSize:11}} onClick={exportLog} disabled={log.length === 0}>
            <Icon name="download" size={11}/> Exportar JSON
          </button>
          <button className="btn btn-ghost" style={{fontSize:11, color:'var(--danger)'}} onClick={clearLog} disabled={log.length === 0}>
            <Icon name="trash" size={11}/> Borrar todo
          </button>
        </div>
        {log.some(e => e.synthetic) && (
          <div style={{padding:'8px 12px', background:'color-mix(in oklch, var(--accent) 10%, var(--bg-sunken))', borderRadius:'var(--r-sm)', fontSize:11, color:'var(--text-muted)', marginBottom:12, lineHeight:1.5}}>
            ℹ️ Hay eventos <strong>sintéticos</strong> (marcados con borde discontinuo abajo) reconstruidos del historial guardado en otros campos. No son eventos en tiempo real — son aproximaciones de cuándo se subió/creó cada cosa.
          </div>
        )}

        <div style={{display:'flex', flexDirection:'column', gap:10, marginBottom:14}}>
          {/* Búsqueda libre */}
          <div className="bo-search" style={{minWidth:260}}>
            <Icon name="search" size={14}/>
            <input placeholder="Buscar en acción, usuario, detalles…" value={search} onChange={e => setSearch(e.target.value)}/>
          </div>

          {/* Filtro por usuario */}
          <div style={{display:'flex', alignItems:'center', gap:6, flexWrap:'wrap'}}>
            <span style={{fontSize:11, color:'var(--text-muted)', fontWeight:600, letterSpacing:0.3, marginRight:4}}>Usuario:</span>
            <button className={'brand-chip' + (userFilter === 'all' ? ' active' : '')} onClick={() => setUserFilter('all')}>
              Todos <span className="mono" style={{opacity:0.6}}>{counts.all}</span>
            </button>
            {users.map(u => {
              const c = counts.byUser[u.id] || 0;
              return (
                <button key={u.id} className={'brand-chip' + (userFilter === u.id ? ' active' : '')} onClick={() => setUserFilter(u.id)} disabled={!c} style={{opacity: c ? 1 : 0.4}}>
                  {u.name} <span className="mono" style={{opacity:0.6}}>{c}</span>
                </button>
              );
            })}
            {counts.byUser.__none__ > 0 && (
              <button className={'brand-chip' + (userFilter === 'none' ? ' active' : '')} onClick={() => setUserFilter('none')}>
                Sin usuario <span className="mono" style={{opacity:0.6}}>{counts.byUser.__none__}</span>
              </button>
            )}
          </div>

          {/* Filtro por tipo de acción */}
          <div style={{display:'flex', alignItems:'center', gap:6, flexWrap:'wrap'}}>
            <span style={{fontSize:11, color:'var(--text-muted)', fontWeight:600, letterSpacing:0.3, marginRight:4}}>Acción:</span>
            <button className={'brand-chip' + (actionFilter === 'all' ? ' active' : '')} onClick={() => setActionFilter('all')}>
              Todas
            </button>
            {actionsInLog.map(a => {
              const lab = _activityLabelFor(a);
              return (
                <button key={a} className={'brand-chip' + (actionFilter === a ? ' active' : '')} onClick={() => setActionFilter(a)} title={a}>
                  <span style={{color: lab.color, marginRight:4}}>{lab.icon}</span>
                  {lab.label} <span className="mono" style={{opacity:0.6}}>{counts.byAction[a]}</span>
                </button>
              );
            })}
          </div>
        </div>

        {filtered.length === 0 ? (
          <div style={{padding:'40px 20px', textAlign:'center', color:'var(--text-muted)', fontSize:13}}>
            {log.length === 0 ? 'Aún no hay actividad registrada — los eventos aparecerán aquí cuando los usuarios usen la app.' : 'Sin eventos con esos filtros.'}
          </div>
        ) : (
          <div style={{display:'flex', flexDirection:'column', gap:14}}>
            {groupedByDay.map(g => (
              <div key={g.day}>
                <div style={{
                  fontSize:10, fontWeight:700, textTransform:'uppercase', letterSpacing:1,
                  color:'var(--text-muted)', marginBottom:6, paddingBottom:4,
                  borderBottom:'1px solid var(--border)',
                }}>
                  {g.label} · {g.events.length} {g.events.length === 1 ? 'evento' : 'eventos'}
                </div>
                <div style={{display:'flex', flexDirection:'column', gap:2}}>
                  {g.events.map(e => {
                    const u = userById[e.userId];
                    const lab = _activityLabelFor(e.action);
                    return (
                      <div key={e.id} style={{
                        display:'grid', gridTemplateColumns:'auto auto 1fr auto', gap:10,
                        alignItems:'baseline', padding:'6px 10px',
                        borderRadius:'var(--r-sm)', fontSize:12,
                        // Sintéticos = borde discontinuo + opacidad ligera, así
                        // se distinguen visualmente de los eventos en vivo.
                        border: e.synthetic ? '1px dashed var(--border-strong)' : '1px solid transparent',
                        opacity: e.synthetic ? 0.85 : 1,
                      }}>
                        <span style={{
                          width:22, height:22, borderRadius:11, display:'inline-grid', placeItems:'center',
                          background:'color-mix(in oklch, ' + lab.color + ' 14%, transparent)',
                          color: lab.color, fontWeight:700, fontSize:11,
                        }} title={e.action}>{lab.icon}</span>
                        <span style={{fontWeight:600, color:'var(--text)', minWidth:120}}>
                          {u ? u.name : (e.userId || <em style={{color:'var(--text-subtle)'}}>—</em>)}
                          {u && u.role === 'admin' && <span style={{marginLeft:4, fontSize:9, padding:'1px 5px', background:'var(--bg-sunken)', borderRadius:3, fontWeight:600, color:'var(--text-muted)'}}>admin</span>}
                        </span>
                        <span style={{color:'var(--text)'}}>
                          <span style={{color: lab.color, fontWeight:500}}>{lab.label}</span>
                          {renderDetails(e.details)}
                        </span>
                        <span className="mono" style={{fontSize:10, color:'var(--text-subtle)', whiteSpace:'nowrap'}} title={new Date(e.ts).toLocaleString('es-ES')}>
                          {_activityTime(e.ts, now)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}

        {log.length >= 1000 && (
          <div style={{marginTop:12, padding:'8px 12px', background:'color-mix(in oklch, var(--accent) 8%, var(--bg-sunken))', borderRadius:'var(--r-sm)', fontSize:11, color:'var(--text-muted)'}}>
            ℹ️ El registro está en su tope (1000 eventos). Los eventos más antiguos se descartan automáticamente al añadir nuevos.
          </div>
        )}
      </div>
    </div>
  );
}

/* Subida masiva — el admin selecciona N archivos a la vez, fija una marca
   común y suben uno tras otro. Cada subida individual conserva su barra de
   estado (OK / falló) para que se vea qué pasó con cada archivo. */
function BulkImageUploader({ setAppState }) {
  const [files, setFiles] = React.useState([]);
  const [brand, setBrand] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [results, setResults] = React.useState([]); // [{name, url, error}]
  const fileRef = React.useRef(null);
  const allBrands = ((typeof window !== 'undefined' && window.BRANDS) || []).filter(b => b.id !== 'bomedia');

  const addUrlToLib = (url, file, brandTag) => {
    if (!url) return;
    setAppState(prev => {
      const list = ((prev && prev.uploadedImages) || []);
      if (list.some(x => x.url === url)) return prev;
      return { ...prev, uploadedImages: [...list, {
        url, name: file.name || '', size: file.size, addedAt: Date.now(), brand: brandTag || null,
      }] };
    });
  };

  const onPickFiles = (e) => {
    const list = Array.from(e.target.files || []);
    e.target.value = '';
    if (list.length) {
      setFiles(list);
      setResults([]);
    }
  };

  const startUpload = async () => {
    const upload = (typeof window !== 'undefined' && typeof window.uploadImage === 'function') ? window.uploadImage : null;
    if (!upload) {
      setResults([{ name: '(global)', error: 'Subida no disponible (módulo de uploads no cargado)' }]);
      return;
    }
    if (files.length === 0) return;
    setBusy(true);
    const out = files.map(f => ({ name: f.name, url: '', error: '', status: 'pending' }));
    setResults(out.slice());
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      try {
        out[i].status = 'uploading';
        setResults(out.slice());
        const url = await upload(f, { prefix: 'library' });
        out[i].url = url;
        out[i].status = 'done';
        addUrlToLib(url, f, brand);
      } catch (e) {
        out[i].error = e.message || String(e);
        out[i].status = 'error';
      }
      setResults(out.slice());
    }
    setBusy(false);
    setFiles([]);
  };

  const reset = () => { setFiles([]); setResults([]); };

  return (
    <div className="product-card" style={{padding:18}}>
      <div style={{display:'flex', alignItems:'center', gap:10, marginBottom:6}}>
        <Icon name="copy" size={18}/>
        <div style={{fontSize:14, fontWeight:600}}>Subir imágenes (admin · masivo)</div>
      </div>
      <div style={{fontSize:11, color:'var(--text-muted)', marginBottom:10}}>
        Sube uno o varios archivos a boprint.net asignándoles la misma marca. Útil para cargar de golpe muestras de impresión, fotos de feria, etc.
      </div>

      <div style={{display:'grid', gridTemplateColumns:'1fr auto', gap:10, alignItems:'end'}}>
        <div className="field" style={{margin:0}}>
          <label className="field-label">Marca para todas las imágenes</label>
          <select className="select" value={brand} onChange={e => setBrand(e.target.value)} disabled={busy}>
            <option value="">— Sin marca específica —</option>
            {allBrands.map(b => <option key={b.id} value={b.id}>{b.label}</option>)}
          </select>
        </div>
        <button className="btn btn-outline" onClick={() => fileRef.current && fileRef.current.click()} disabled={busy}>
          <Icon name="download" size={12}/> Elegir archivos…
        </button>
        <input type="file" accept="image/*" multiple ref={fileRef} onChange={onPickFiles} style={{display:'none'}} />
      </div>

      {files.length > 0 && (
        <div style={{marginTop:12, padding:10, background:'var(--bg-sunken)', borderRadius:'var(--r-sm)'}}>
          <div style={{fontSize:11, color:'var(--text-muted)', marginBottom:8}}>
            <strong>{files.length}</strong> archivo{files.length === 1 ? '' : 's'} seleccionado{files.length === 1 ? '' : 's'} · marca: <strong>{allBrands.find(b => b.id === brand)?.label || 'Sin marca'}</strong>
          </div>
          <div style={{display:'flex', flexDirection:'column', gap:4, maxHeight:180, overflowY:'auto', marginBottom:8}}>
            {files.map((f, i) => (
              <div key={i} style={{fontSize:11, color:'var(--text-muted)', fontFamily:'var(--font-mono)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>
                · {f.name} <span style={{opacity:0.5}}>({Math.round(f.size/1024)} KB)</span>
              </div>
            ))}
          </div>
          <div style={{display:'flex', gap:6}}>
            <button className="btn btn-primary" onClick={startUpload} disabled={busy}>
              {busy ? 'Subiendo…' : <><Icon name="zap" size={12}/> Subir {files.length} archivo{files.length === 1 ? '' : 's'}</>}
            </button>
            <button className="btn btn-ghost" onClick={reset} disabled={busy}>Cancelar</button>
          </div>
        </div>
      )}

      {results.length > 0 && (
        <div style={{marginTop:12, display:'flex', flexDirection:'column', gap:4}}>
          <div style={{fontSize:11, color:'var(--text-muted)', fontWeight:600}}>Resultados:</div>
          {results.map((r, i) => (
            <div key={i} style={{
              padding:'4px 10px', borderRadius:'var(--r-sm)', fontSize:11,
              background: r.status === 'done' ? 'color-mix(in oklch, var(--success) 12%, var(--bg-sunken))' :
                         r.status === 'error' ? 'color-mix(in oklch, var(--danger) 12%, var(--bg-sunken))' :
                         'var(--bg-sunken)',
              color: r.status === 'error' ? 'var(--danger)' : 'var(--text)',
              fontFamily:'var(--font-mono)',
              display:'flex', justifyContent:'space-between', gap:10,
            }}>
              <span style={{overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>
                {r.status === 'done' ? '✓' : r.status === 'error' ? '✗' : r.status === 'uploading' ? '…' : '·'} {r.name}
              </span>
              <span style={{opacity:0.7}}>{r.error || (r.url ? 'subido' : r.status)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ────────────── Image library modal ──────────────
   Modal that shows a grid of every image URL the app already knows about
   (product photos, brand logos, hero images from blocks/standalones, plus
   the user's prior uploads). User can pick one or upload a new one. The
   chosen URL is returned via `onPick(url)`. */

/* Collect every distinct image URL we can find across the appState. The
   resulting list is unique by URL and roughly grouped by source so the
   modal can show "Subidas" first, then "Productos", etc. Cada item
   incluye también `brand` cuando se puede inferir (producto → su marca,
   logo de marca → la marca, hero → marca del standalone, subidas → la
   que el user marcó al subir) y `tags` cuando vienen en el JSON
   importado (uploadedImages.tags + imageLibrary.tags). Apr 2026: añadido
   soporte de tags + collection `imageLibrary` (imágenes de producto
   suplementarias importadas vía JSON, distinto de products[].img). */
function _collectKnownImages(appState) {
  const items = []
  const seen = new Set()
  const push = (url, source, label, brand, tags) => {
    if (!url || typeof url !== 'string') return
    const u = url.trim()
    if (!u || !/^https?:|^data:/i.test(u)) return
    if (seen.has(u)) return
    seen.add(u)
    items.push({
      url: u,
      source,
      label: label || '',
      brand: brand || null,
      tags: Array.isArray(tags) ? tags.filter(t => typeof t === 'string' && t) : [],
    })
  }
  // Recent uploads first — tags vienen del JSON importado (uploadedImages[i].tags)
  ;(appState.uploadedImages || []).slice().reverse().forEach(it => push(it.url, 'upload', it.name, it.brand, it.tags))
  // imageLibrary — colección suplementaria importada vía JSON. Cada item
  // tiene {id, url, brand, productId, name, tags, alt, type}. Es distinta
  // de products[].img (que es la imagen "principal" del catálogo) — aquí
  // entran fotos extra de producto, muestras, packshots, etc.
  ;(appState.imageLibrary || []).forEach(it => push(it.url, 'library', it.name || it.alt, it.brand, it.tags))
  // Products: img + per-lang i18n images. La marca se hereda del propio
  // producto. Si el producto tiene tags (formato libre en el JSON),
  // también se propagan al thumbnail.
  ;(appState.products || []).forEach(p => {
    push(p.img, 'product', p.name, p.brand, p.tags)
    if (p.i18n) Object.values(p.i18n).forEach(loc => push(loc.img, 'product', p.name, p.brand, p.tags))
  })
  // Brand logos: brand = la propia marca
  ;(appState.brands || []).forEach(b => push(b.logo, 'brand', b.label, b.id, b.tags))
  // Hero images on standalone blocks: brand = la del standalone
  ;(appState.standaloneBlocks || []).forEach(sb => push(sb.config && sb.config.heroImage, 'hero', sb.title, sb.brand, sb.tags))
  return items
}

/* Helper compartido entre ImageLibraryAdminPanel + ImageLibraryModal:
   pintar la barra de tags con búsqueda + chips. La AND logic la aplica
   el caller en su propia filtered list (`it.tags.includes(t)` para
   cada t en activeTags). */
function ImageTagFilter({ items, activeTags, setActiveTags, tagSearch, setTagSearch }) {
  // Recopilar tags únicos de todos los items, filtrar por búsqueda, y
  // ordenar alfabético. ~80 tags es manejable; con search casi nunca se
  // ven más de 15-20 chips a la vez.
  const allTags = React.useMemo(() => {
    const set = new Set()
    for (const it of (items || [])) {
      for (const t of (it.tags || [])) {
        if (t) set.add(t)
      }
    }
    return Array.from(set).sort()
  }, [items])
  const q = (tagSearch || '').trim().toLowerCase()
  const visibleTags = q ? allTags.filter(t => t.toLowerCase().indexOf(q) >= 0) : allTags
  const toggle = (t) => {
    setActiveTags(prev => prev.includes(t) ? prev.filter(x => x !== t) : [...prev, t])
  }
  const clearAll = () => { setActiveTags([]); setTagSearch('') }
  if (allTags.length === 0) return null
  return (
    <div style={{display:'flex', flexDirection:'column', gap:6}}>
      <div style={{display:'flex', gap:6, alignItems:'center'}}>
        <span style={{fontSize:11, color:'var(--text-muted)', fontWeight:600, letterSpacing:0.3, marginRight:4}}>
          Tags{activeTags.length > 0 ? ' · ' + activeTags.length + ' activo' + (activeTags.length === 1 ? '' : 's') : ''}:
        </span>
        <input
          className="input"
          placeholder="Buscar tag…"
          value={tagSearch || ''}
          onChange={e => setTagSearch(e.target.value)}
          style={{flex:1, fontSize:11, padding:'4px 8px'}}
        />
        {(activeTags.length > 0 || tagSearch) && (
          <button
            className="btn btn-ghost"
            style={{fontSize:11, padding:'4px 8px'}}
            onClick={clearAll}
            title="Limpiar tags activos y búsqueda"
          >
            Limpiar
          </button>
        )}
      </div>
      <div style={{
        display:'flex', flexWrap:'wrap', gap:4,
        maxHeight:120, overflowY:'auto',
        padding: visibleTags.length > 0 ? 4 : 0,
      }}>
        {visibleTags.length === 0 ? (
          <div style={{fontSize:11, color:'var(--text-subtle)', fontStyle:'italic', padding:6}}>
            {q ? 'Sin coincidencias para "' + q + '".' : 'Sin tags en estas imágenes.'}
          </div>
        ) : visibleTags.map(t => {
          const active = activeTags.includes(t)
          return (
            <button
              key={t}
              className={'brand-chip' + (active ? ' active' : '')}
              onClick={() => toggle(t)}
              style={{fontSize:10, padding:'2px 8px'}}
              title={active ? 'Quitar tag del filtro' : 'Filtrar por este tag (AND con los demás activos)'}
            >
              {t}
            </button>
          )
        })}
      </div>
    </div>
  )
}

/* Append an upload to appState.uploadedImages. Idempotent (deduped by URL).
   Called from ImageUploadInput once a fetch completes. */
function _registerRecorder(setAppState) {
  window.recordUploadedImage = (item) => {
    if (!item || !item.url || !setAppState) return
    setAppState(prev => {
      const list = Array.isArray(prev.uploadedImages) ? prev.uploadedImages : []
      if (list.some(x => x.url === item.url)) return prev
      // Cap at 200 — older entries get dropped to keep state size sane
      const next = [...list, item].slice(-200)
      return Object.assign({}, prev, { uploadedImages: next })
    })
  }
}

function ImageLibraryModal({ appState, setAppState, onPick, onClose }) {
  React.useEffect(() => { _registerRecorder(setAppState) }, [setAppState])
  const [filter, setFilter] = React.useState('all')
  const [brandFilter, setBrandFilter] = React.useState('all')
  const [activeTags, setActiveTags] = React.useState([])
  const [tagSearch, setTagSearch] = React.useState('')
  const [search, setSearch] = React.useState('')
  const items = React.useMemo(() => _collectKnownImages(appState || {}), [appState])
  const libCount = items.filter(i => i.source === 'library').length
  // AND logic: la imagen debe llevar TODOS los tags activos.
  const matchesTags = (it) => {
    if (activeTags.length === 0) return true
    const itTags = it.tags || []
    return activeTags.every(t => itTags.indexOf(t) >= 0)
  }
  const filtered = items.filter(it =>
    (filter === 'all' || it.source === filter) &&
    (brandFilter === 'all' || (brandFilter === 'none' ? !it.brand : it.brand === brandFilter)) &&
    matchesTags(it) &&
    (!search || (it.label || '').toLowerCase().includes(search.toLowerCase()))
  )
  const groups = [
    { id: 'all', label: 'Todas', count: items.length },
    { id: 'upload', label: 'Subidas', count: items.filter(i => i.source === 'upload').length },
    // Chip "Biblioteca" sólo si hay items con source 'library' (JSON importado)
    ...(libCount > 0 ? [{ id: 'library', label: 'Biblioteca', count: libCount }] : []),
    { id: 'product', label: 'Productos', count: items.filter(i => i.source === 'product').length },
    { id: 'brand', label: 'Marcas', count: items.filter(i => i.source === 'brand').length },
    { id: 'hero', label: 'Heroes', count: items.filter(i => i.source === 'hero').length },
  ]
  const allBrands = ((typeof window !== 'undefined' && window.BRANDS) || (appState && appState.brands) || []).filter(b => b.id !== 'bomedia')
  const itemsAfterSource = filter === 'all' ? items : items.filter(i => i.source === filter)
  const brandCounts = (() => {
    const counts = { all: itemsAfterSource.length, none: 0 }
    for (const it of itemsAfterSource) {
      if (!it.brand) counts.none++
      else counts[it.brand] = (counts[it.brand] || 0) + 1
    }
    return counts
  })()
  // Items para la barra de tags: filtrados por source + brand para que
  // los tags reflejen sólo lo visible y no se ofrezcan tags imposibles.
  const itemsForTagFilter = items.filter(it =>
    (filter === 'all' || it.source === filter) &&
    (brandFilter === 'all' || (brandFilter === 'none' ? !it.brand : it.brand === brandFilter))
  )
  return (
    <>
      <div className="bo-drawer-overlay" onClick={onClose} style={{zIndex:50}}/>
      <div style={{position:'fixed', top:'5%', left:'50%', transform:'translateX(-50%)', width:'min(900px, 92vw)', maxHeight:'90vh', background:'var(--bg-panel)', border:'1px solid var(--border)', borderRadius:'var(--r-md)', display:'flex', flexDirection:'column', zIndex:51, overflow:'hidden'}}>
        <div style={{padding:'14px 18px', borderBottom:'1px solid var(--border)', display:'flex', alignItems:'center', gap:12}}>
          <Icon name="copy" size={18}/>
          <div style={{fontSize:14, fontWeight:600, flex:1}}>Biblioteca de imágenes</div>
          <button className="icon-btn" onClick={onClose}><Icon name="x" size={16}/></button>
        </div>
        <div style={{padding:'10px 18px', borderBottom:'1px solid var(--border)', display:'flex', flexDirection:'column', gap:8}}>
          <div style={{display:'flex', gap:10, alignItems:'center', flexWrap:'wrap'}}>
            <div className="bo-search" style={{flex:'1 1 200px', minWidth:200}}>
              <Icon name="search" size={14}/>
              <input placeholder="Buscar por nombre…" value={search} onChange={e => setSearch(e.target.value)}/>
            </div>
            <div style={{display:'flex', gap:4, flexWrap:'wrap'}}>
              {groups.map(g => (
                <button key={g.id} className={'brand-chip' + (filter === g.id ? ' active' : '')} onClick={() => setFilter(g.id)}>
                  {g.label} <span className="mono" style={{opacity:0.6}}>{g.count}</span>
                </button>
              ))}
            </div>
          </div>
          <div style={{display:'flex', gap:4, alignItems:'center', flexWrap:'wrap'}}>
            <span style={{fontSize:11, color:'var(--text-muted)', fontWeight:600, marginRight:4, letterSpacing:0.3}}>Marca:</span>
            <button className={'brand-chip' + (brandFilter === 'all' ? ' active' : '')} onClick={() => setBrandFilter('all')}>
              Todas <span className="mono" style={{opacity:0.6}}>{brandCounts.all}</span>
            </button>
            {allBrands.map(b => (
              <button key={b.id} className={'brand-chip' + (brandFilter === b.id ? ' active' : '')} onClick={() => setBrandFilter(b.id)} disabled={!brandCounts[b.id]} style={{opacity: brandCounts[b.id] ? 1 : 0.45}}>
                {b.label} <span className="mono" style={{opacity:0.6}}>{brandCounts[b.id] || 0}</span>
              </button>
            ))}
            {brandCounts.none > 0 && (
              <button className={'brand-chip' + (brandFilter === 'none' ? ' active' : '')} onClick={() => setBrandFilter('none')}>
                Sin marca <span className="mono" style={{opacity:0.6}}>{brandCounts.none}</span>
              </button>
            )}
          </div>
          {/* Filtro por tags: chips estilo brand-chip con buscador encima.
              Sólo aparece si los items actuales tienen tags. */}
          <ImageTagFilter
            items={itemsForTagFilter}
            activeTags={activeTags}
            setActiveTags={setActiveTags}
            tagSearch={tagSearch}
            setTagSearch={setTagSearch}
          />
        </div>
        <div style={{padding:14, borderBottom:'1px solid var(--border)'}}>
          <ImageUploadInput value="" onChange={url => { if (url) onPick(url) }} prefix="library" placeholder="Subir nueva imagen — se añadirá a la biblioteca" />
        </div>
        <div style={{padding:14, overflowY:'auto', flex:1}}>
          {filtered.length === 0 && (
            <div style={{padding:'40px 20px', textAlign:'center', color:'var(--text-muted)', fontSize:13}}>
              {items.length === 0 ? 'No hay imágenes todavía. Sube una para empezar.' : 'Sin resultados con ese filtro.'}
            </div>
          )}
          <div style={{display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(140px, 1fr))', gap:10}}>
            {filtered.map((it, i) => {
              const brandObj = it.brand ? allBrands.find(b => b.id === it.brand) : null
              return (
                <button key={i} onClick={() => onPick(it.url)} title={it.url}
                  style={{padding:0, border:'1px solid var(--border)', borderRadius:'var(--r-sm)', background:'var(--bg-sunken)', cursor:'pointer', overflow:'hidden', display:'flex', flexDirection:'column'}}
                >
                  <div style={{aspectRatio:'4/3', display:'grid', placeItems:'center', overflow:'hidden', background:'#fff', position:'relative'}}>
                    <img src={it.url} alt={it.label} style={{maxWidth:'100%', maxHeight:'100%', objectFit:'contain'}} onError={e => { e.target.style.opacity = 0.2; }}/>
                    {brandObj && (
                      <span style={{position:'absolute', bottom:4, left:4, padding:'1px 6px', background: brandObj.color || '#444', color:'#fff', borderRadius:8, fontSize:8, fontWeight:700}}>{brandObj.label}</span>
                    )}
                  </div>
                  <div style={{padding:'6px 8px', fontSize:10, color:'var(--text-muted)', textAlign:'left', whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis'}}>
                    <span style={{fontWeight:600, color:'var(--text)'}}>{it.label || '—'}</span>
                    <span style={{marginLeft:6, padding:'1px 5px', background:'var(--bg-panel)', borderRadius:3, fontSize:9}}>{it.source}</span>
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      </div>
    </>
  )
}

/* ────────────── Backup & restore helpers ──────────────
   Stable, tool-free way to dump the entire appState to a JSON file the user
   can download. Used by both the header "Exportar" button and the Settings
   panel. */
function exportAppStateAsJson(appState) {
  try {
    const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
    const blob = new Blob([JSON.stringify(appState || {}, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'bomedia-backup-' + ts + '.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    return true;
  } catch (e) {
    console.error('Export failed:', e);
    alert('No se pudo exportar: ' + (e.message || e));
    return false;
  }
}

/* Merge an imported state into the existing one without overwriting anything.
   For each known array collection, append items whose `id` doesn't already
   exist. Scalars (passwords, openaiKey…) are left untouched in merge mode. */
function _mergeImportedState(prev, incoming) {
  if (!incoming || typeof incoming !== 'object') return prev;
  const next = Object.assign({}, prev);
  const collections = ['products','brands','prewrittenTexts','templates','standaloneBlocks','composedBlocks','users'];
  collections.forEach(key => {
    if (!Array.isArray(incoming[key])) return;
    const existing = Array.isArray(prev[key]) ? prev[key] : [];
    const ids = new Set(existing.map(x => x && x.id).filter(Boolean));
    const newOnes = incoming[key].filter(x => x && x.id && !ids.has(x.id));
    if (newOnes.length > 0) next[key] = existing.concat(newOnes);
  });
  return next;
}

/* ────────────── Settings tab ──────────────
   Backup (download JSON), Restore (upload JSON in either replace-or-merge
   mode), and Recargar desde la nube (re-pulls Supabase). The old "Forzar
   sync" was a placeholder — the auto-save effect already pushes changes to
   Supabase 1.5s after every edit, so a manual force is rarely useful. */
function SettingsPanel({ appState, setAppState }) {
  const fileRef = React.useRef(null);
  const [importMode, setImportMode] = React.useState('merge');
  const [status, setStatus] = React.useState('');
  const [reloading, setReloading] = React.useState(false);

  const setMsg = (m, isErr) => setStatus({ text: m, err: !!isErr });

  const reloadFromCloud = () => {
    if (typeof loadFromSupabase !== 'function') {
      setMsg('Supabase no está disponible.', true);
      return;
    }
    setReloading(true);
    setMsg('Recargando desde la nube…');
    loadFromSupabase().then(data => {
      if (!data) {
        // 200 OK pero sin datos — la nube está genuinamente vacía. Sigue
        // siendo "alcanzable" para el auto-save: notificamos éxito de
        // conexión aunque no hayamos sobrescrito state.
        setMsg('La nube está vacía. Local mantenido.', true);
        if (typeof window.__onCloudReloadSuccess === 'function') window.__onCloudReloadSuccess(null);
        return;
      }
      let merged = data;
      if (typeof mergeI18nFromDefaults === 'function') merged = mergeI18nFromDefaults(merged);
      if (typeof migrateMboDtf === 'function') merged = migrateMboDtf(merged);
      if (typeof migrateComposedToCompositorBlocks === 'function') merged = migrateComposedToCompositorBlocks(merged);
      if (typeof migrateDividerTypes === 'function') merged = migrateDividerTypes(merged);
      setAppState(merged);
      setMsg('Estado recargado desde Supabase.');
      // Avisar a app-main para que destrabe el auto-save (en caso de que
      // la hydration inicial hubiese fallado y estuviera en local-offline).
      if (typeof window.__onCloudReloadSuccess === 'function') window.__onCloudReloadSuccess(merged);
    }).catch(e => {
      setMsg('Error: ' + (e.message || e), true);
      // Marca explícita de "nube no alcanzable" — el auto-save seguirá
      // bloqueado hasta que un reload tenga éxito.
      if (typeof window.__onCloudReloadFailure === 'function') window.__onCloudReloadFailure();
    }).finally(() => setReloading(false));
  };

  const onPickFile = () => fileRef.current && fileRef.current.click();

  const onFile = (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = '';
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      let parsed;
      try { parsed = JSON.parse(ev.target.result); }
      catch (err) { setMsg('JSON inválido: ' + err.message, true); return; }
      if (!parsed || typeof parsed !== 'object') { setMsg('JSON no es un objeto.', true); return; }
      if (importMode === 'replace') {
        const ok = window.confirm(
          'Reemplazar TODO el estado actual con el JSON importado.\n\n' +
          'Esta acción es destructiva: borrará productos, plantillas, textos, bloques, marcas y usuarios actuales.\n\n' +
          '¿Continuar?'
        );
        if (!ok) return;
        let next = parsed;
        if (typeof mergeI18nFromDefaults === 'function') next = mergeI18nFromDefaults(next);
        if (typeof migrateMboDtf === 'function') next = migrateMboDtf(next);
        setAppState(next);
        setMsg('Estado reemplazado con el JSON importado (' + (file.name || 'archivo') + ').');
      } else {
        // Merge: append new items by id, never overwrite scalar fields like
        // passwords or the OpenAI key.
        const counts = {};
        const collections = ['products','brands','prewrittenTexts','templates','standaloneBlocks','composedBlocks','users'];
        collections.forEach(k => {
          if (!Array.isArray(parsed[k])) return;
          const ids = new Set(((appState && appState[k]) || []).map(x => x && x.id).filter(Boolean));
          counts[k] = parsed[k].filter(x => x && x.id && !ids.has(x.id)).length;
        });
        const total = Object.values(counts).reduce((n, x) => n + x, 0);
        if (total === 0) { setMsg('Nada que añadir — todos los items del JSON ya existen por id.'); return; }
        setAppState(prev => _mergeImportedState(prev, parsed));
        const breakdown = Object.entries(counts).filter(([,v]) => v > 0).map(([k,v]) => v + ' ' + k).join(', ');
        setMsg('Fusionados ' + total + ' items nuevos: ' + breakdown);
      }
    };
    reader.onerror = () => setMsg('No se pudo leer el archivo.', true);
    reader.readAsText(file);
  };

  // Stats summary (helps the user see roughly what's in there)
  const stat = (k) => Array.isArray(appState && appState[k]) ? appState[k].length : 0;

  return (
    <div style={{maxWidth:680, display:'flex', flexDirection:'column', gap:14}}>
      <div className="product-card" style={{padding:20}}>
        <div style={{display:'flex', alignItems:'center', gap:10, marginBottom:4}}>
          <Icon name="download" size={18}/>
          <div style={{fontSize:15, fontWeight:600}}>Exportar JSON</div>
        </div>
        <div style={{fontSize:12, color:'var(--text-muted)', marginBottom:12, lineHeight:1.6}}>
          Descarga el estado completo: <strong>{stat('products')}</strong> productos, <strong>{stat('templates')}</strong> plantillas, <strong>{stat('prewrittenTexts')}</strong> textos, <strong>{stat('standaloneBlocks')}</strong> bloques, <strong>{stat('composedBlocks')}</strong> compuestos, <strong>{stat('brands')}</strong> marcas, <strong>{stat('users')}</strong> usuarios. Sirve como respaldo o para mover datos entre entornos.
        </div>
        <button className="btn btn-primary" style={{fontSize:12}} onClick={() => { exportAppStateAsJson(appState); setMsg('Backup descargado.'); }}>
          <Icon name="download" size={12}/> Descargar backup
        </button>
      </div>

      <div className="product-card" style={{padding:20}}>
        <div style={{display:'flex', alignItems:'center', gap:10, marginBottom:4}}>
          <Icon name="copy" size={18}/>
          <div style={{fontSize:15, fontWeight:600}}>Importar JSON</div>
        </div>
        <div style={{fontSize:12, color:'var(--text-muted)', marginBottom:12, lineHeight:1.6}}>
          Carga un JSON previamente exportado (o creado a mano).
        </div>
        <div style={{display:'flex', gap:14, marginBottom:12, flexWrap:'wrap'}}>
          <label style={{display:'flex', gap:6, alignItems:'flex-start', cursor:'pointer', maxWidth:280}}>
            <input type="radio" checked={importMode === 'merge'} onChange={() => setImportMode('merge')} style={{marginTop:3}} />
            <span>
              <strong style={{fontSize:12.5}}>Fusionar</strong>
              <div style={{fontSize:11, color:'var(--text-muted)', lineHeight:1.5}}>Añade items nuevos por id. No toca lo existente.</div>
            </span>
          </label>
          <label style={{display:'flex', gap:6, alignItems:'flex-start', cursor:'pointer', maxWidth:280}}>
            <input type="radio" checked={importMode === 'replace'} onChange={() => setImportMode('replace')} style={{marginTop:3}} />
            <span>
              <strong style={{fontSize:12.5, color:'var(--danger)'}}>Reemplazar todo</strong>
              <div style={{fontSize:11, color:'var(--text-muted)', lineHeight:1.5}}>Sobrescribe todo el estado. Pide confirmación.</div>
            </span>
          </label>
        </div>
        <input type="file" accept="application/json,.json" ref={fileRef} onChange={onFile} style={{display:'none'}} />
        <button className="btn btn-outline" style={{fontSize:12}} onClick={onPickFile}>
          <Icon name="copy" size={12}/> Elegir archivo JSON…
        </button>
      </div>

      <div className="product-card" style={{padding:20}}>
        <div style={{display:'flex', alignItems:'center', gap:10, marginBottom:4}}>
          <Icon name="database" size={18}/>
          <div style={{fontSize:15, fontWeight:600}}>Sincronización con Supabase</div>
        </div>
        <div style={{fontSize:12, color:'var(--text-muted)', marginBottom:12, lineHeight:1.6}}>
          Los cambios se guardan en la nube automáticamente unos segundos después de cada edición. Usa <em>Recargar desde la nube</em> si has editado los datos desde otro dispositivo y quieres descartar los cambios locales no sincronizados.
        </div>
        <button className="btn btn-outline" style={{fontSize:12}} onClick={reloadFromCloud} disabled={reloading}>
          {reloading ? 'Recargando…' : 'Recargar desde la nube'}
        </button>
      </div>

      {status && status.text && (
        <div style={{padding:'10px 14px', background:'var(--bg-sunken)', borderRadius:'var(--r-sm)', fontSize:12, color: status.err ? 'var(--danger)' : 'var(--success)', fontWeight:500}}>
          {status.text}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { Backoffice, AISettingsPanel, AutoTranslatePanel, SettingsPanel, UsersPanel, UserBOEdit, AiStylesAdminOverview, MyToneIaPanel, MyAccountPanel, ComposedBOEdit, CtaSavedBOEdit, ImageUploadInput, ImageLibraryModal, ImageLibraryAdminPanel, ImageTagFilter, exportAppStateAsJson, blankItemForKind });
