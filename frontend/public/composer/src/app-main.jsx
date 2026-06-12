/* ───────────── MAIN APP ───────────── */

const TWEAK_DEFAULS = /*EDITMODE-BEGIN*/{
  "theme": "default",
  "titleStyle": "serif",
  "density": "comfortable"
}/*EDITMODE-END*/;

function App() {
  const [tweaks, setTweaks] = React.useState(TWEAK_DEFAULS);
  const [tweaksOpen, setTweaksOpen] = React.useState(false);
  const [mode, setMode] = React.useState('compositor');
  const [lang, setLang] = React.useState('es');
  const lastLangUserRef = React.useRef(null);
  // Note: the useEffects that restore/persist user.lastLang are declared
  // further down — they need currentUserId + appState which are defined
  // later in the component body.
  const [sidebarCollapsed, setSidebarCollapsed] = React.useState(false);
  const [previewHidden, setPreviewHidden] = React.useState(false);
  const [rightMode, setRightMode] = React.useState('preview');
  const [previewTab, setPreviewTab] = React.useState('visual');
  const [device, setDevice] = React.useState('desktop');
  const [brandFilter, setBrandFilter] = React.useState(() => {
    try { const v = localStorage.getItem('bomedia-ui-sidebar-brand'); return v ? JSON.parse(v) : 'all'; } catch (e) { return 'all'; }
  });
  React.useEffect(() => {
    try { localStorage.setItem('bomedia-ui-sidebar-brand', JSON.stringify(brandFilter)); } catch (e) {}
  }, [brandFilter]);
  const [selectedId, setSelectedId] = React.useState(null);
  const [cmdkOpen, setCmdkOpen] = React.useState(false);
  const [previewModalOpen, setPreviewModalOpen] = React.useState(false);
  const [editingTemplateId, setEditingTemplateId] = React.useState(null);
  const [rightPanelWidth, setRightPanelWidth] = React.useState(() => {
    try {
      const v = parseInt(localStorage.getItem('bomedia-ui-right-panel-w'), 10);
      if (Number.isFinite(v) && v >= 360 && v <= 1100) return v;
    } catch (e) {}
    return 560;
  });
  React.useEffect(() => {
    try { localStorage.setItem('bomedia-ui-right-panel-w', String(rightPanelWidth)); } catch (e) {}
  }, [rightPanelWidth]);

  // Drag-to-resize the right panel: pointer events on the .right-panel-resizer
  // handle. Width is bounded between 360 (under that the email iframe can't
  // breathe) and 1100 (above that the canvas becomes unreadable).
  const startResize = React.useCallback((e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = rightPanelWidth;
    const move = (ev) => {
      const next = Math.max(360, Math.min(1100, startW + (startX - ev.clientX)));
      setRightPanelWidth(next);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      document.body.classList.remove('resizing-panel');
    };
    document.body.classList.add('resizing-panel');
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }, [rightPanelWidth]);
  const [currentUserId, setCurrentUserId] = React.useState(() => {
    try { return sessionStorage.getItem('bomedia_session_user') || null; } catch (e) { return null; }
  });
  const [loginValue, setLoginValue] = React.useState('');
  const [loginUserId, setLoginUserId] = React.useState('admin');
  const [loginError, setLoginError] = React.useState(false);
  // Pantalla de bienvenida que se muestra antes del modal de login. El user
  // pulsa "Entrar" → showLanding=false → aparece el modal. Tras un logout
  // se vuelve a mostrar el landing.
  const [showLanding, setShowLanding] = React.useState(true);
  const [syncStatus, setSyncStatus] = React.useState('loading'); // 'loading' | 'cloud' | 'local' | 'local-offline' | ''

  // ─── Guards contra el bug crítico de "auto-save pisa Supabase con defaults" ───
  // 1) `_initialFromDefaults`: true si el state inicial vino de getDefaultState()
  //    (localStorage estaba vacío). En ese caso NUNCA debemos pushear sin
  //    haber confirmado primero que la nube también está vacía.
  // 2) `hydratedRef`: true sólo después de que loadFromSupabase() resuelva
  //    (success o catch). El auto-save NO dispara hasta entonces.
  // 3) `cloudReachableRef`: null=desconocido, true=responde y se leyó,
  //    false=error de red/auth. Si false, el auto-save NUNCA pushea —
  //    la nube podría tener datos que no pudimos ver.
  const _initialFromDefaults = React.useRef(false);
  const hydratedRef = React.useRef(false);
  const cloudReachableRef = React.useRef(null);

  // ─── appState (real data) — start from localStorage+defaults, then hydrate from Supabase ───
  const [appState, setAppState] = React.useState(() => {
    const stored = getStorageData();
    if (stored) {
      const defaults = getDefaultState();
      if (!stored.brands) stored.brands = defaults.brands;
      else {
        const existingIds = new Set(stored.brands.map(b => b.id));
        defaults.brands.forEach(db => {
          if (!existingIds.has(db.id)) stored.brands.push(db);
        });
      }
      // One-shot migration: split DTF items off into the mbo_dtf brand
      // (idempotent — checks for the keyword each time).
      if (typeof migrateMboDtf === 'function') Object.assign(stored, migrateMboDtf(stored));
      // Repara i18n.{lang}.link en productos que se han "perdido"
      // (igualados al base) o que faltan. Idempotente.
      if (typeof repairProductLinks === 'function') Object.assign(stored, repairProductLinks(stored));
      // Migra los bloques compuestos legacy (introText + brandStrip + ...)
      // a la nueva forma `compositorBlocks` (lista plana de bloques v3).
      // Idempotente — si ya hay compositorBlocks no toca nada.
      if (typeof migrateComposedToCompositorBlocks === 'function') Object.assign(stored, migrateComposedToCompositorBlocks(stored));
      // Normaliza divider_line/short/dots (tipo literal del factory antiguo
      // del BO) al shape canónico {type:'divider', style}. Sin esto los
      // divisores guardados antes del fix Apr 2026 no aparecían en el canvas
      // al cargar plantillas viejas (sí en el preview gracias al bridge).
      if (typeof migrateDividerTypes === 'function') Object.assign(stored, migrateDividerTypes(stored));
      if (!stored.standaloneBlocks) stored.standaloneBlocks = defaults.standaloneBlocks;
      if (!stored.templates) stored.templates = defaults.templates;
      // Backwards-compat: if there's no users[] yet, create a single admin
      // and migrate the legacy boPasswordHash into it.
      if (!Array.isArray(stored.users) || stored.users.length === 0) {
        stored.users = [{
          id: 'admin',
          name: 'Admin',
          role: 'admin',
          passwordHash: stored.boPasswordHash || (typeof DEFAULT_BO_HASH !== 'undefined' ? DEFAULT_BO_HASH : 'a1bfe0bf4fa8f02f1969c64276b15f55e455b3dd9f50f11a22fb8c284a9c2f48'),
          hiddenItems: {},
          aiStyles: {},
        }];
      }
      // Ensure each user has a valid hiddenItems + aiStyles object
      stored.users = stored.users.map(u => Object.assign({ hiddenItems: {}, aiStyles: {} }, u));
      if (typeof stored.openaiKey !== 'string') stored.openaiKey = '';
      return mergeI18nFromDefaults(stored);
    }
    // localStorage vacío → arrancamos con defaults. Marcamos este ref para
    // que el auto-save sepa que NO debe pushear sin haber confirmado antes
    // que la nube también está vacía (capa 1 de defensa).
    _initialFromDefaults.current = true;
    return getDefaultState();
  });

  // Persist current user across sessions (sessionStorage = per-tab)
  React.useEffect(() => {
    try {
      if (currentUserId) sessionStorage.setItem('bomedia_session_user', currentUserId);
      else sessionStorage.removeItem('bomedia_session_user');
    } catch (e) {}
  }, [currentUserId]);

  // Resolve current user from appState.users
  const currentUser = (appState.users || []).find(u => u.id === currentUserId) || null;
  const isAdmin = currentUser?.role === 'admin';

  // If the stored session user no longer exists in appState (e.g. admin
  // deleted them), drop the session.
  React.useEffect(() => {
    if (currentUserId && !currentUser && (appState.users || []).length > 0) {
      setCurrentUserId(null);
    }
  }, [currentUserId, currentUser, appState.users]);

  // When the active user changes, restore their saved language. When the
  // language changes while a user is logged in, persist it on their record
  // so it survives logout/reload.
  React.useEffect(() => {
    if (lastLangUserRef.current === currentUserId) return;
    lastLangUserRef.current = currentUserId;
    if (currentUser && currentUser.lastLang && ['es','fr','de','en','nl'].includes(currentUser.lastLang)) {
      setLang(currentUser.lastLang);
    }
  }, [currentUserId, currentUser]);
  React.useEffect(() => {
    if (!currentUserId) return;
    setAppState(prev => {
      const users = prev.users || [];
      const i = users.findIndex(x => x.id === currentUserId);
      if (i < 0) return prev;
      if (users[i].lastLang === lang) return prev;
      const next = users.slice();
      next[i] = { ...next[i], lastLang: lang };
      return { ...prev, users: next };
    });
  }, [lang, currentUserId]);

  // ─── Draft blocks (v3 shape) — persisted per user in localStorage ───
  // Each user has their own draft slot keyed by user id, so Sara's canvas
  // doesn't overwrite Bart's.
  const [blocks, setBlocks] = React.useState(() => {
    const initialUserId = (() => {
      try { return sessionStorage.getItem('bomedia_session_user') || null; } catch (e) { return null; }
    })();
    const draft = getDraftBlocks(initialUserId);
    if (draft && draft.length > 0) return draft;
    return [];
  });

  // When the user changes (login / logout / switch), load their personal
  // draft so they see what they left when they last used the app. We set
  // `inLoadRef` so the save effect below skips the very next run — without
  // that, the save effect would fire with the OUTGOING user's blocks but
  // the INCOMING user's id and overwrite the new user's draft slot.
  const lastUserRef = React.useRef(currentUserId);
  const inLoadRef = React.useRef(false);
  React.useEffect(() => {
    if (lastUserRef.current === currentUserId) return;
    lastUserRef.current = currentUserId;
    inLoadRef.current = true;
    const userDraft = getDraftBlocks(currentUserId);
    setBlocks(userDraft && userDraft.length > 0 ? userDraft : []);
  }, [currentUserId]);

  // Save the draft to the CURRENT user's slot whenever blocks change.
  // Skips the first fire after a user switch (see inLoadRef above).
  React.useEffect(() => {
    if (inLoadRef.current) { inLoadRef.current = false; return; }
    saveDraftBlocks(blocks, currentUserId);
  }, [blocks, currentUserId]);

  // ─── Email title (asunto) — visible/editable arriba del canvas, persistido
  // por usuario en localStorage como los drafts.
  const _emailTitleKey = (uid) => 'bomedia_email_title_' + (uid || 'anon');
  const [emailTitle, setEmailTitle] = React.useState(() => {
    try {
      const initialUserId = sessionStorage.getItem('bomedia_session_user') || null;
      return localStorage.getItem(_emailTitleKey(initialUserId)) || '';
    } catch (e) { return ''; }
  });
  // Cargar el título del usuario activo cuando cambia el currentUserId
  React.useEffect(() => {
    try { setEmailTitle(localStorage.getItem(_emailTitleKey(currentUserId)) || ''); } catch (e) {}
  }, [currentUserId]);
  // Guardar el título cada vez que cambia
  React.useEffect(() => {
    try { localStorage.setItem(_emailTitleKey(currentUserId), emailTitle || ''); } catch (e) {}
  }, [emailTitle, currentUserId]);

  // Expose setAppState globally so deeply-nested popovers (e.g. the image
  // library picker inside section columns) can record uploads without
  // threading the setter through every layer of props. Also expose the
  // current appState so library widgets can browse uploads/products/brands
  // without prop-drilling.
  React.useEffect(() => { window.__setAppState = setAppState; }, [setAppState]);
  // Atajo "aplicar a todos los bloques" del BlockWidthControl. Setea
  // widthPct + blockAlign en cada bloque top-level del canvas (no entra
  // dentro de columnas de sección — esas tienen su propio layout).
  React.useEffect(() => {
    window.__applyBlockSizeToAll = (patch) => {
      if (!patch || typeof patch !== 'object') return;
      setBlocks(prev => prev.map(b =>
        b.type === 'section' ? b : { ...b, ...patch }
      ));
    };
    return () => { delete window.__applyBlockSizeToAll; };
  }, []);
  React.useEffect(() => { window.__appState = appState; }, [appState]);
  // Expose expandTemplate so the AI agent's load_template tool can use the
  // app's canonical template-expansion logic instead of reimplementing it.
  React.useEffect(() => { window.expandTemplate = expandTemplate; });
  // Registrar recordUploadedImage SIEMPRE (no solo cuando ImageLibraryModal
  // está montado) — antes las subidas hechas desde ImageUploadInput sin
  // abrir la biblioteca primero no quedaban guardadas en appState.uploadedImages
  // y desaparecían al recargar.
  React.useEffect(() => {
    window.recordUploadedImage = (item) => {
      if (!item || !item.url) return;
      let isNewUrl = false;
      setAppState(prev => {
        const list = Array.isArray(prev.uploadedImages) ? prev.uploadedImages : [];
        if (list.some(x => x.url === item.url)) return prev;
        isNewUrl = true;
        const next = [...list, item].slice(-200);
        return Object.assign({}, prev, { uploadedImages: next });
      });
      // Log solo la primera vez que vemos esa URL (así "abrir imagen ya
      // existente" no genera ruido). El timeout 0 espera al commit del
      // setAppState para no logear si la URL ya estaba.
      setTimeout(() => {
        if (isNewUrl && typeof window.logActivity === 'function') {
          window.logActivity('image_upload', {
            url: item.url, name: item.name, size: item.size, brand: item.brand || null,
          });
        }
      }, 0);
    };
    return () => { delete window.recordUploadedImage; };
  }, [setAppState]);

  // ─── Registro de actividad por usuario ─────────────────────────
  // Logger universal: cualquier módulo que tenga una acción de usuario
  // relevante llama a window.logActivity('action_id', {detalles}). Lo
  // empujamos a appState.activityLog (capped a 1000 entradas — FIFO) y
  // el auto-save de Supabase lo persiste con el resto del estado. El
  // panel "Actividad" del backoffice (admin-only) lo presenta.
  // Apr 2026.
  const currentUserIdRef = React.useRef(currentUserId);
  React.useEffect(() => { currentUserIdRef.current = currentUserId; }, [currentUserId]);
  React.useEffect(() => {
    window.logActivity = (action, details) => {
      if (!action) return;
      const uid = currentUserIdRef.current;
      // Permitimos eventos sin user (ej. login fallido) — quedan con userId:null.
      const entry = {
        id: 'act-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 6),
        ts: Date.now(),
        userId: uid || null,
        action,
        details: (details && typeof details === 'object') ? details : {},
      };
      setAppState(prev => {
        const list = Array.isArray(prev.activityLog) ? prev.activityLog : [];
        // Cap a 1000 — el más viejo se descarta al superar el límite
        const next = list.length >= 1000 ? [...list.slice(-999), entry] : [...list, entry];
        return Object.assign({}, prev, { activityLog: next });
      });
    };
    return () => { delete window.logActivity; };
  }, [setAppState]);

  // Keep the v3-compat globals in sync with the live appState.
  // Components (Sidebar, BlockCard, Inspector, etc.) read from window.PRODUCTS /
  // window.BRANDS / … directly, so when the user edits anything from Backoffice
  // we must republish the arrays so next render sees the fresh data.
  React.useEffect(() => {
    window.PRODUCTS = appState.products || [];
    window.BRANDS = appState.brands || [];
    window.PREWRITTEN_TEXTS = appState.prewrittenTexts || [];
    window.TEMPLATES = appState.templates || [];
    window.COMPOSED_BLOCKS = appState.composedBlocks || [];
    window.STANDALONE_BLOCKS = (appState.standaloneBlocks || []).map(sb => Object.assign({}, sb, {
      type: sb.blockType,
    }));
    window.OPENAI_KEY = appState.openaiKey || '';
  }, [appState]);

  // Publish current user's AI tone prompts so callOpenAI() picks them up
  // automatically. When the user logs out, fall back to defaults.
  React.useEffect(() => {
    window.AI_STYLES = (currentUser && currentUser.aiStyles) || {};
  }, [currentUser]);

  // One-shot migration: if the user had a key in sessionStorage from before
  // we moved it to Supabase, copy it into appState the first time.
  const migratedKeyRef = React.useRef(false);
  React.useEffect(() => {
    if (migratedKeyRef.current) return;
    if (syncStatus === 'loading') return;
    if (appState.openaiKey) { migratedKeyRef.current = true; return; }
    let legacy = '';
    try { legacy = sessionStorage.getItem('bomedia_openai_key') || ''; } catch (e) {}
    if (legacy) {
      migratedKeyRef.current = true;
      setAppState(prev => ({ ...prev, openaiKey: legacy }));
    }
  }, [appState, syncStatus]);

  // Auto-save appState to localStorage + Supabase (debounced).
  // 4 capas de defensa contra el bug "los defaults pisan el catálogo":
  //   a) Gate por syncStatus === 'loading' (legacy — sigue valiendo)
  //   b) Gate por hydratedRef.current === true (NO pushear hasta que
  //      loadFromSupabase haya resuelto, incluso con error)
  //   c) Gate por cloudReachableRef.current === true (si hubo error de
  //      red/auth, NO pushear — la nube podría tener datos que no vimos)
  //   d) Gate por window.isPristineDefaults(state) === false (cinturón
  //      final: si el state es indistinguible de getDefaultState() pelado,
  //      NUNCA pushear, sería destruir el catálogo de otros usuarios)
  const saveTimer = React.useRef(null);
  React.useEffect(() => {
    if (syncStatus === 'loading') return;
    if (!hydratedRef.current) return;
    saveStorageData(appState); // local SIEMPRE es seguro
    clearTimeout(saveTimer.current);
    // Decisión sobre push a la nube
    let shouldPushToCloud = true;
    if (cloudReachableRef.current !== true) {
      // Hydration falló o no se ha completado — la nube podría tener datos
      // que no leímos. NO pushear.
      shouldPushToCloud = false;
    } else if (typeof window.isPristineDefaults === 'function' && window.isPristineDefaults(appState)) {
      // El state es indistinguible de getDefaultState(). Pushear pisaría
      // el catálogo de la nube con datos pelados.
      shouldPushToCloud = false;
      console.warn('[autosave] State indistinguible de defaults — push a Supabase suprimido para evitar wipe.');
    }
    if (shouldPushToCloud) {
      saveTimer.current = setTimeout(() => { saveToSupabase(appState); }, 1500);
    }
    return () => clearTimeout(saveTimer.current);
  }, [appState, syncStatus]);

  // Initial Supabase hydration
  const loadedRef = React.useRef(false);
  React.useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    loadFromSupabase().then(cloudData => {
      // .then sólo se ejecuta si la nube respondió (200 OK con o sin data).
      // Marcamos como alcanzable — el auto-save puede empezar a pushear
      // a partir de aquí (siempre que no sea pristine).
      cloudReachableRef.current = true;
      if (cloudData && cloudData.products) {
        const defaults = getDefaultState();
        if (!cloudData.brands) cloudData.brands = defaults.brands;
        // Forward-compat: if defaults add new brand ids that the cloud data
        // doesn't have yet (e.g. mbo_dtf split from mbo), inject them so the
        // user can use them without re-resetting their data.
        else {
          const existingIds = new Set(cloudData.brands.map(b => b.id));
          defaults.brands.forEach(db => {
            if (!existingIds.has(db.id)) cloudData.brands.push(db);
          });
        }
        // One-shot migration: re-tag any item whose name/title/desc mentions
        // "DTF" from the legacy `mbo` brand to the new `mbo_dtf`. Idempotent
        // — re-running it on already-migrated data is a no-op.
        cloudData = migrateMboDtf(cloudData);
        if (typeof repairProductLinks === 'function') cloudData = repairProductLinks(cloudData);
        if (typeof migrateComposedToCompositorBlocks === 'function') cloudData = migrateComposedToCompositorBlocks(cloudData);
        if (typeof migrateDividerTypes === 'function') cloudData = migrateDividerTypes(cloudData);
        if (!cloudData.standaloneBlocks) cloudData.standaloneBlocks = defaults.standaloneBlocks;
        if (!cloudData.templates) cloudData.templates = defaults.templates;
        // Multi-user migration: pre-existing Supabase rows have no users[].
        // Promote the legacy boPasswordHash into a default admin so the app
        // remains usable with the same credentials.
        if (!Array.isArray(cloudData.users) || cloudData.users.length === 0) {
          cloudData.users = [{
            id: 'admin',
            name: 'Admin',
            role: 'admin',
            passwordHash: cloudData.boPasswordHash || (typeof DEFAULT_BO_HASH !== 'undefined' ? DEFAULT_BO_HASH : 'a1bfe0bf4fa8f02f1969c64276b15f55e455b3dd9f50f11a22fb8c284a9c2f48'),
            hiddenItems: {},
            aiStyles: {},
          }];
        }
        cloudData.users = cloudData.users.map(u => Object.assign({ hiddenItems: {}, aiStyles: {} }, u));
        if (typeof cloudData.openaiKey !== 'string') cloudData.openaiKey = '';
        cloudData = mergeI18nFromDefaults(cloudData);
        setAppState(cloudData);
        saveStorageData(cloudData);
        setSyncStatus('cloud');
        setTimeout(() => setSyncStatus(''), 3000);
      } else {
        // Nube respondió pero está vacía (fresh install genuino). Sólo
        // pusheamos local si tenemos datos reales — NUNCA defaults.
        setSyncStatus('local');
        setTimeout(() => setSyncStatus(''), 3000);
        if (!_initialFromDefaults.current) {
          saveToSupabase(appState);
        } else {
          console.warn('[hydration] Supabase vacío + localStorage vacío. Trabajando sólo con defaults — NO se pushea hasta que el user añada contenido real.');
        }
      }
    }).catch(err => {
      // Error real de red/auth/HTTP. NO sabemos qué hay en la nube — quizá
      // 42 productos que no pudimos leer. Marcar como NO alcanzable y dejar
      // que el user trabaje en local. El auto-save NUNCA pusheará bajo
      // esta condición (capa c) hasta que el user pulse "Recargar desde
      // la nube" en SettingsPanel y la próxima carga tenga éxito.
      console.error('[hydration] loadFromSupabase falló:', err);
      cloudReachableRef.current = false;
      setSyncStatus('local-offline');
      // No auto-clear: que el badge "Sin nube" quede visible como señal
      // permanente hasta que el user fuerce un reload exitoso.
    }).finally(() => {
      // Marcar hydratedRef AL FINAL para destrabar el auto-save effect.
      // Tanto en éxito como en error, el state ya está estable y se puede
      // permitir guardado local. El push a nube sigue gated por
      // cloudReachableRef.
      hydratedRef.current = true;
    });
  }, []);

  // Permitir reintentar la hydration desde la nube tras un error — la
  // función la consume SettingsPanel ("Recargar desde la nube"). Si la
  // recarga tiene éxito, restablece cloudReachableRef para destrabar el
  // auto-save. Si vuelve a fallar, se queda en local-offline.
  React.useEffect(() => {
    window.__onCloudReloadSuccess = (data) => {
      cloudReachableRef.current = true;
      // Si la primera hydration había fallado y ahora la manual tiene
      // éxito, los datos importados sustituyen al state actual via el
      // setAppState que ya hace el caller.
      setSyncStatus('cloud');
      setTimeout(() => setSyncStatus(''), 3000);
    };
    window.__onCloudReloadFailure = () => {
      cloudReachableRef.current = false;
      setSyncStatus('local-offline');
    };
    return () => {
      delete window.__onCloudReloadSuccess;
      delete window.__onCloudReloadFailure;
    };
  }, []);

  // Apply theme
  React.useEffect(() => {
    const themeMap = { default: '', warm: 'warm', cool: 'cool', dark: 'dark' };
    document.documentElement.dataset.theme = themeMap[tweaks.theme] || '';
  }, [tweaks.theme]);

  // Keyboard shortcut
  React.useEffect(() => {
    const h = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); setCmdkOpen(v => !v); }
      if (e.key === 'Escape') { setCmdkOpen(false); setLoginPrompt(false); }
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, []);

  // Tweaks edit-mode protocol
  React.useEffect(() => {
    const handler = (e) => {
      if (e.data?.type === '__activate_edit_mode') setTweaksOpen(true);
      if (e.data?.type === '__deactivate_edit_mode') setTweaksOpen(false);
    };
    window.addEventListener('message', handler);
    try { window.parent.postMessage({ type: '__edit_mode_available' }, '*'); } catch(err) {}
    return () => window.removeEventListener('message', handler);
  }, []);

  const updateTweak = (key, val) => {
    const next = { ...tweaks, [key]: val };
    setTweaks(next);
    try { window.parent.postMessage({ type: '__edit_mode_set_keys', edits: { [key]: val } }, '*'); } catch(err) {}
  };

  // ─── Template expansion ───
  // Supports two template schemas:
  //  - Legacy:  tpl.blocks = ['text-001', 'block-002', ...]  (refs to existing texts/composed)
  //  - Inline:  tpl.compositorBlocks = [ { type:'text', text:'…', i18n:{…} }, { type:'product_pair', product1, product2 }, … ]
  const expandTemplate = (tplId) => {
    const tpl = (appState.templates || []).find(t => t.id === tplId);
    if (!tpl) return [];
    const expanded = [];

    // Inline blocks (newer Supabase schema) — pass through verbatim, normalising
    // text blocks to v3's overridesByLang shape so i18n + body survive.
    if (Array.isArray(tpl.compositorBlocks) && tpl.compositorBlocks.length > 0) {
      for (const cb of tpl.compositorBlocks) {
        if (!cb || !cb.type) continue;
        if (cb.type === 'text') {
          // Aceptamos las tres formas en las que puede venir un texto:
          // (1) overridesByLang (forma moderna que escribe el editor de
          //     plantillas/compuestos del backoffice + el composer);
          // (2) text + i18n (schema viejo de Supabase / plantillas guardadas
          //     antes del refactor); (3) ambas mezcladas.
          // Antes solo leíamos cb.text → si la plantilla había sido editada
          // en el editor nuevo del BO (que escribe overridesByLang y limpia
          // text/i18n), al cargar la plantilla en el composer aparecía el
          // texto vacío. Bug Apr 2026.
          let overridesByLang = null;
          if (cb.overridesByLang && typeof cb.overridesByLang === 'object') {
            overridesByLang = Object.assign({}, cb.overridesByLang);
          } else if (cb.text != null || cb.i18n) {
            overridesByLang = { es: cb.text || '' };
            if (cb.i18n) {
              for (const [l, v] of Object.entries(cb.i18n)) {
                if (v && v.text) overridesByLang[l] = v.text;
              }
            }
          } else {
            overridesByLang = { es: '' };
          }
          // Mantener textId si la plantilla referenciaba un texto pre-escrito.
          const textBlock = { type: 'text', overridesByLang };
          if (cb.textId) textBlock.textId = cb.textId;
          if (cb._richHtml != null) textBlock._richHtml = cb._richHtml;
          if (cb._richHtmlByLang) textBlock._richHtmlByLang = cb._richHtmlByLang;
          expanded.push(textBlock);
        } else {
          // Pass through other block kinds (product_pair, product_trio, brand_strip, pimpam_hero, etc.)
          expanded.push(Object.assign({}, cb));
        }
      }
      return expanded;
    }

    // Legacy ref-based template
    for (const ref of (tpl.blocks || [])) {
      // prewritten text?
      if ((appState.prewrittenTexts || []).some(t => t.id === ref)) {
        expanded.push({ type: 'text', textId: ref });
        continue;
      }
      // composed block? expand its innards
      const cb = (appState.composedBlocks || []).find(c => c.id === ref);
      if (cb) {
        if (cb.introText) expanded.push({ type: 'text', overrideText: cb.introText });
        if (cb.brandStrip && cb.brandStrip !== 'none') expanded.push({ type: 'brandstrip', brands: [cb.brandStrip] });
        const prods = cb.products || [];
        for (const pid of prods) expanded.push({ type: 'product', productId: pid });
        if (cb.includeHero) {
          const heroSb = (appState.standaloneBlocks || []).find(s => s.blockType === 'pimpam_hero');
          expanded.push({ type: 'hero', standaloneId: heroSb?.id });
        }
        if (cb.includeSteps) {
          const stepsSb = (appState.standaloneBlocks || []).find(s => s.blockType === 'pimpam_steps');
          expanded.push({ type: 'pimpam_steps', standaloneId: stepsSb?.id });
        }
      }
    }
    return expanded;
  };

  // mkId combines a counter with a base36 timestamp + random suffix to avoid
  // collisions with IDs already present in the draft loaded from localStorage.
  const nextId = React.useRef(0);
  const mkId = () => {
    const c = ++nextId.current;
    return 'b' + c + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 6);
  };

  // Convert the current v3 blocks array back into a compositorBlocks-shaped
  // array suitable for storing inside a template.
  const blocksToCompositorBlocks = (v3Blocks) => {
    const out = [];
    const texts = appState.prewrittenTexts || [];
    // Campos de presentación que cualquier bloque puede llevar y que SÍ
    // queremos persistir en la plantilla. Antes la versión rebuild-from-
    // scratch del case `text` y `composed` (y la falta de cases `image`/
    // `cta`/`divider`/`section`) los descartaba — al recargar la plantilla
    // el formato (rich HTML, ancho, alineación, fontSize, link de imagen,
    // estilo de divider, columnas de sección…) se evaporaba.
    const carryMeta = (src, dst) => {
      // widthPct y blockAlign — afectan al wrapper exterior del bloque
      if (typeof src.widthPct === 'number') dst.widthPct = src.widthPct;
      if (src.blockAlign) dst.blockAlign = src.blockAlign;
      return dst;
    };

    for (const b of (v3Blocks || [])) {
      switch (b.type) {
        case 'text': {
          // 1. Reference to an existing prewritten text
          let textEs = '', i18n = {};
          if (b.textId) {
            const src = texts.find(t => t.id === b.textId);
            if (src) {
              textEs = src.text || '';
              if (src.i18n) {
                for (const [l, v] of Object.entries(src.i18n)) {
                  if (v && v.text) i18n[l] = { text: v.text };
                }
              }
            }
          }
          // 2. Per-language overrides (overridesByLang)
          if (b.overridesByLang) {
            if (b.overridesByLang.es != null) textEs = b.overridesByLang.es;
            for (const [l, v] of Object.entries(b.overridesByLang)) {
              if (l !== 'es' && v != null) i18n[l] = { text: v };
            }
          }
          // 3. Single-shot override
          if (b.overrideText) textEs = b.overrideText;
          // Conservar formato/tipografía/anclaje/textId/rich HTML — todos
          // estos campos los lee el composer/preview al recargar la
          // plantilla. Sin esto el contenido bold/colored, los anchos
          // personalizados y la referencia al pre-escrito desaparecían
          // tras "Guardar como plantilla". Bug fix Apr 2026.
          const tBlock = Object.assign({ type: 'text', text: textEs }, Object.keys(i18n).length ? { i18n } : {});
          if (b.textId) tBlock.textId = b.textId;
          if (b.overridesByLang) tBlock.overridesByLang = Object.assign({}, b.overridesByLang);
          if (b._richHtml != null) tBlock._richHtml = b._richHtml;
          if (b._richHtmlByLang) tBlock._richHtmlByLang = Object.assign({}, b._richHtmlByLang);
          if (b.fontSize) tBlock.fontSize = b.fontSize;
          if (b.align) tBlock.align = b.align;
          out.push(carryMeta(b, tBlock));
          break;
        }
        case 'product':
          if (b.productId) out.push(carryMeta(b, { type: 'product_single', product1: b.productId }));
          break;
        case 'product_single':
        case 'product_pair':
        case 'product_trio':
          out.push(Object.assign({}, b, { id: undefined }));
          break;
        case 'brand_strip':
        case 'brand_artisjet':
        case 'brand_mbo':
        case 'brand_pimpam':
        case 'brand_smartjet':
        case 'brand_flux':
          out.push(carryMeta(b, { type: 'brand_strip', brand: b.brand || b.type.replace('brand_', '') }));
          break;
        case 'brandstrip': {
          const enabled = b.brands || [];
          for (const bid of enabled) out.push(carryMeta(b, { type: 'brand_strip', brand: bid }));
          break;
        }
        case 'pimpam_hero':
        case 'pimpam_steps':
        case 'freebird':
        case 'video':
          out.push(Object.assign({}, b, { id: undefined }));
          break;
        // Image / CTA / divider / section: pasan tal cual (manteniendo
        // todos sus campos, solo limpiamos id que es interno del canvas).
        // Antes estos tipos NO tenían case y caían a `default: break`,
        // borrándose de la plantilla al guardar. Bug fix Apr 2026.
        case 'image':
        case 'cta':
        case 'divider':
        case 'divider_line':
        case 'divider_short':
        case 'divider_dots':
          out.push(Object.assign({}, b, { id: undefined }));
          break;
        case 'section': {
          // Recursión por columnas — cada columna tiene su propio array
          // de blocks que también pasamos por blocksToCompositorBlocks
          // para que sus hijos reciban el mismo trato (preserva todos
          // los campos, no solo type+brand).
          const cols = (b.columns || []).map(col => ({
            blocks: blocksToCompositorBlocks(col.blocks || []),
          }));
          out.push(Object.assign({}, b, { id: undefined, columns: cols }));
          break;
        }
        case 'composed':
          // Keep as-is — when this template is loaded, the composed block resolves via its id.
          // Carry width/align so the composed unit respects user-set sizing.
          if (b.composedId) out.push(carryMeta(b, { type: 'composed', composedId: b.composedId }));
          break;
        // header / footer / hero (v3-only stubs) are skipped: they don't have
        // a stable representation in the template schema.
        default: break;
      }
    }
    return out.filter(b => b && b.type);
  };

  // Load a template into the canvas (used by "Editar plantilla" in BO).
  const loadTemplateIntoCanvas = (tplId) => {
    const tpl = (appState.templates || []).find(t => t.id === tplId);
    if (!tpl) return;
    const exp = expandTemplate(tplId);
    setBlocks(exp.map(e => ({ ...e, id: mkId() })));
    setEditingTemplateId(tplId);
    setSelectedId(null);
    setMode('compositor');
    // Auto-populate the email title with the template's name (in active
    // language). The user can still rename it manually afterwards.
    if (typeof window.getLocalizedText === 'function') {
      setEmailTitle(window.getLocalizedText(tpl, 'name', lang) || tpl.name || '');
    } else {
      setEmailTitle(tpl.name || '');
    }
    if (typeof window.logActivity === 'function') {
      window.logActivity('template_load', { templateId: tplId, templateName: tpl.name, blockCount: exp.length });
    }
  };

  // Save the current canvas into an existing template (overwrite its blocks).
  const saveCurrentToTemplate = (tplId) => {
    if (!tplId) return;
    const compBlocks = blocksToCompositorBlocks(blocks);
    setAppState(prev => ({
      ...prev,
      templates: (prev.templates || []).map(t => t.id === tplId
        ? { ...t, compositorBlocks: compBlocks, blocks: [] }
        : t),
    }));
    if (typeof window.logActivity === 'function') {
      const tpl = (appState.templates || []).find(t => t.id === tplId);
      window.logActivity('template_update', { templateId: tplId, templateName: tpl?.name, blockCount: compBlocks.length });
    }
  };

  // Save the current canvas as a brand new template.
  const saveCurrentAsNewTemplate = (name, opts) => {
    const compBlocks = blocksToCompositorBlocks(blocks);
    const id = 'tpl-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 6);
    const tpl = {
      id,
      name: name || 'Plantilla sin título',
      desc: (opts && opts.desc) || '',
      brand: (opts && opts.brand) || 'mix',
      colorClass: (opts && opts.colorClass) || 'gray',
      compositorBlocks: compBlocks,
      blocks: [],
      visible: true,
      // Stamp ownership for commercial creators so the BO can show a "Tuyo"
      // badge. Admin-created templates are visible to everyone (no stamp).
      ...(currentUser && currentUser.role !== 'admin' ? { createdBy: currentUser.id, createdAt: Date.now() } : {}),
    };
    setAppState(prev => ({
      ...prev,
      templates: [...(prev.templates || []), tpl],
    }));
    setEditingTemplateId(id);
    // Misma política que en el "+ Nuevo" del Backoffice: cuando un
    // comercial crea contenido nuevo, lo ocultamos del resto de
    // comerciales por defecto. El admin lo puede ver de todas formas
    // y los demás pueden hacerlo visible para sí mismos desde BO.
    if (currentUser && currentUser.role !== 'admin') {
      setTimeout(() => autoHideForOthers('templates', id), 0);
    }
    if (typeof window.logActivity === 'function') {
      window.logActivity('template_create', { templateId: id, templateName: tpl.name, blockCount: compBlocks.length });
    }
    return tpl;
  };
  // When the user clicks the "+" insert-zone between two blocks the canvas
  // sets this index — the next addBlock() call splices at that position
  // (after-index = insertAfter, so the new block lands right below) and the
  // index resets back to null.
  const [insertAfter, setInsertAfter] = React.useState(null);

  // Helper that splices `arr` so the new items land right after `insertAfter`
  // (or appends if it's null/out of range). Resets the insertion target.
  // Defensive: only treat insertAfter as valid if it's a non-NaN integer in
  // range. Anything else (null, undefined, SyntheticEvent leak, NaN) → append.
  const placeBlocks = (prev, items) => {
    const ia = insertAfter;
    const valid = typeof ia === 'number' && Number.isFinite(ia) && ia >= 0 && ia < prev.length;
    if (!valid) return [...prev, ...items];
    const out = prev.slice();
    out.splice(ia + 1, 0, ...items);
    return out;
  };

  // Target for the next inner-block insertion (when the user clicks "+ Añadir"
  // inside a section column). Cleared after one add. null means the block
  // goes at top level of the canvas (default).
  const [innerTarget, setInnerTarget] = React.useState(null);

  const addBlock = (spec, opts) => {
    // `into` lets the caller route the new block into a section column instead
    // of placing it at the top of the canvas. Falls back to innerTarget state
    // (set by the column "+ Añadir" button).
    const into = (opts && opts.into) || innerTarget;
    if (spec.templateId) {
      const exp = expandTemplate(spec.templateId);
      setBlocks(prev => placeBlocks(prev, exp.map(e => ({ ...e, id: mkId() }))));
      // Si el canvas estaba vacío (carga "fresh" de plantilla, no append),
      // autocompletamos el título con el nombre de la plantilla en el
      // idioma activo. No machacamos un título que el user ya hubiera
      // escrito.
      if (blocks.length === 0 && !emailTitle) {
        const tpl = (appState.templates || []).find(t => t.id === spec.templateId);
        if (tpl) {
          const tplName = (typeof window.getLocalizedText === 'function')
            ? window.getLocalizedText(tpl, 'name', lang) || tpl.name
            : tpl.name;
          setEmailTitle(tplName || '');
        }
      }
      setInsertAfter(null);
      setInnerTarget(null);
      return;
    }
    // Multi-column section containers — built via createBlock so they get a
    // properly initialized `columns` array.
    if (spec.type === 'section_2col' || spec.type === 'section_3col') {
      const sec = createBlock(spec.type);
      sec.id = mkId();
      setBlocks(prev => placeBlocks(prev, [sec]));
      setInsertAfter(null);
      setInnerTarget(null);
      return;
    }
    // Divisores — tres variantes con createBlock
    if (spec.type === 'divider_line' || spec.type === 'divider_short' || spec.type === 'divider_dots') {
      const dv = createBlock(spec.type);
      dv.id = mkId();
      if (into) {
        setBlocks(prev => _addToSection(prev, into.sectionId, into.columnIdx, dv));
      } else {
        setBlocks(prev => placeBlocks(prev, [dv]));
      }
      setInsertAfter(null);
      setInnerTarget(null);
      return;
    }
    // Image / CTA blocks: built via createBlock so the defaults populate.
    // For images coming from the library picker, the preselected URL travels
    // in spec._imgUrl and gets set as src here. For CTAs picked from the
    // saved library, spec._ctaSourceId carries the source id; we copy the
    // saved fields into the new block (no live link — independent copy).
    if (spec.type === 'image' || spec.type === 'cta') {
      const ib = createBlock(spec.type);
      ib.id = mkId();
      if (spec.type === 'image' && spec._imgUrl) ib.src = spec._imgUrl;
      if (spec.type === 'cta' && spec._ctaSourceId) {
        const src = (appState.ctaBlocks || []).find(c => c.id === spec._ctaSourceId);
        if (src) {
          ['title','subtitle','bullets','text','url','bg','color','align','panelBg','panelBorder'].forEach(k => {
            if (src[k] !== undefined) ib[k] = Array.isArray(src[k]) ? src[k].slice() : src[k];
          });
          ib._ctaSourceId = src.id;
        }
      }
      if (into) {
        setBlocks(prev => _addToSection(prev, into.sectionId, into.columnIdx, ib));
      } else {
        setBlocks(prev => placeBlocks(prev, [ib]));
      }
      setInsertAfter(null);
      setInnerTarget(null);
      return;
    }
    // "text-blank" → empty text block, ready to write into. Selected on add
    // so the inline editor opens immediately.
    if (spec.type === 'text-blank') {
      const newId = mkId();
      const newBlock = { id: newId, type: 'text', overridesByLang: { es: '' } };
      if (into) {
        setBlocks(prev => _addToSection(prev, into.sectionId, into.columnIdx, newBlock));
      } else {
        setBlocks(prev => placeBlocks(prev, [newBlock]));
      }
      setTimeout(() => { setSelectedId(newId); setRightMode('edit'); }, 0);
      setInsertAfter(null);
      setInnerTarget(null);
      return;
    }
    const b = { id: mkId(), type: spec.type };
    if (spec.productId) b.productId = spec.productId;
    if (spec.textId) b.textId = spec.textId;
    if (spec.composedId) b.composedId = spec.composedId;
    if (spec.standaloneId) {
      b.standaloneId = spec.standaloneId;
      const sb = (appState.standaloneBlocks || []).find(s => s.id === spec.standaloneId);
      const cfg = sb?.config || {};
      if (b.type === 'product_single') {
        b.product1 = cfg.defaultProduct || 'young';
      } else if (b.type === 'product_pair') {
        b.product1 = cfg.defaultProduct1 || 'young';
        b.product2 = cfg.defaultProduct2 || '3000pro';
      } else if (b.type === 'product_trio') {
        b.product1 = cfg.defaultProduct1 || 'uv1612g';
        b.product2 = cfg.defaultProduct2 || 'uv1812';
        b.product3 = cfg.defaultProduct3 || 'uv2513';
      } else if (b.type === 'brand_strip') {
        b.brand = cfg.brand || 'artisjet';
      } else if (
        b.type === 'pimpam_hero' || b.type === 'product_hero' || b.type === 'hero' ||
        b.type === 'pimpam_steps' || b.type === 'video' || b.type === 'freebird'
      ) {
        b._sourceType = 'standalone';
        b._sourceId = spec.standaloneId;
        // product_hero standalones use a different schema — they only store
        // `config.defaultProduct`. Materialize the hero fields from that
        // product so the rest of the pipeline (preview, editor, email-gen)
        // can work uniformly.
        if (b.type === 'product_hero' && cfg.defaultProduct) {
          const products = appState.products || [];
          const p = products.find(x => x.id === cfg.defaultProduct);
          if (p) {
            // Base (Spanish) fields — the email-gen reads these as the default
            b.heroImage = p.img;
            b.heroTitle = p.name;
            b.heroSubtitle = p.desc;
            b.heroBullets = [p.feat1, p.feat2].filter(Boolean);
            const ctaLabels = { es:'Más información', fr:"Plus d'infos", de:'Mehr Infos', en:'More info', nl:'Meer info' };
            b.heroCtaButtons = (p.link ? [{ text: ctaLabels.es, url: p.link, bg: p.accent || '#1d4ed8', color: '#ffffff' }] : []);
            b.heroBgColor = '#ffffff';
            // Build per-lang i18n from the product's own translations so
            // switching language in the canvas reflects in the hero too.
            const heroI18n = {};
            for (const l of ['fr','de','en','nl']) {
              const tr = (p.i18n && p.i18n[l]) || null;
              if (!tr) continue;
              const entry = {};
              if (tr.desc)  entry.heroSubtitle = tr.desc;
              if (tr.feat1 || tr.feat2) entry.heroBullets = [tr.feat1, tr.feat2].filter(Boolean);
              if (p.link || tr.link) {
                entry.heroCtaButtons = [{
                  text: ctaLabels[l] || ctaLabels.es,
                  url: tr.link || p.link,
                  bg: p.accent || '#1d4ed8',
                  color: '#ffffff',
                }];
              }
              if (Object.keys(entry).length) heroI18n[l] = entry;
            }
            if (Object.keys(heroI18n).length) b.i18n = heroI18n;
          }
          // Normalise to the unified type so all editors/renderers treat
          // it as a regular hero from now on.
          b.type = 'pimpam_hero';
        }
      }
    }
    // Direct-add fallbacks when not coming from a standalone
    if (!spec.standaloneId) {
      if (b.type === 'product_pair' && !b.product1) { b.product1 = 'young'; b.product2 = '3000pro'; }
      if (b.type === 'product_trio' && !b.product1) { b.product1 = 'uv1612g'; b.product2 = 'uv1812'; b.product3 = 'uv2513'; }
      if (b.type === 'brand_strip' && !b.brand) b.brand = 'artisjet';
    }
    if (into) {
      setBlocks(prev => _addToSection(prev, into.sectionId, into.columnIdx, b));
    } else {
      setBlocks(prev => placeBlocks(prev, [b]));
    }
    setInsertAfter(null);
    setInnerTarget(null);
    // Log la adición — un evento por llamada a addBlock (no por bloque,
    // porque un template-load añade muchos a la vez y queremos ver "cargó
    // plantilla X" no 12 entradas "block_add"). Solo registramos lo que
    // describe la acción del user.
    if (typeof window.logActivity === 'function') {
      const summary = spec.templateId ? null  // template_load se logea aparte
        : { type: spec.type, productId: spec.productId, textId: spec.textId,
            standaloneId: spec.standaloneId, composedId: spec.composedId };
      if (summary) window.logActivity('block_add', summary);
    }
  };

  // Section-aware mutation helpers. They operate at top level first, and if
  // the target id isn't there, recurse into section columns. Used by
  // updateBlock / deleteBlock / duplicateBlock so the same APIs work for
  // both standalone blocks and inner ones.
  const _mapBlocks = (blocks, fn) => blocks.map(x => {
    const r = fn(x);
    if (r !== x) return r;
    if (x.type === 'section' && Array.isArray(x.columns)) {
      const cols = x.columns.map(col => ({
        ...col,
        blocks: _mapBlocks(col.blocks || [], fn),
      }));
      return { ...x, columns: cols };
    }
    return x;
  });
  const _filterBlocks = (blocks, pred) => blocks.filter(pred).map(x => {
    if (x.type === 'section' && Array.isArray(x.columns)) {
      const cols = x.columns.map(col => ({
        ...col,
        blocks: _filterBlocks(col.blocks || [], pred),
      }));
      return { ...x, columns: cols };
    }
    return x;
  });
  const _addToSection = (blocks, sectionId, columnIdx, newBlock) => blocks.map(x => {
    if (x.id !== sectionId) return x;
    if (x.type !== 'section' || !Array.isArray(x.columns)) return x;
    const cols = x.columns.slice();
    const col = cols[columnIdx] || { blocks: [] };
    cols[columnIdx] = { ...col, blocks: [...(col.blocks || []), newBlock] };
    return { ...x, columns: cols };
  });

  const updateBlock = (id, b) => setBlocks(prev => _mapBlocks(prev, x => x.id === id ? b : x));
  const deleteBlock = (id) => setBlocks(prev => _filterBlocks(prev, x => x.id !== id));

  // "Desagrupar" — replaces a composed block with its constituent child blocks
  // (intro text + brand strip + products + optional hero/steps), so each piece
  // can be edited / removed independently. Each child gets a fresh id.
  // Same expansion logic email-gen uses at render time, but materialised as
  // editable canvas blocks. The composed block's `i18n.{lang}.introText` is
  // copied into the new text block's `overridesByLang` so translations survive.
  const ungroupComposedBlock = (id) => {
    // Find the actual composed block instance in the canvas (top-level OR
    // inside a section column) so we can resolve its source via composedId.
    const findCanvasBlock = (list) => {
      for (const x of list) {
        if (x.id === id) return x;
        if (x.type === 'section' && Array.isArray(x.columns)) {
          for (const col of x.columns) {
            const inner = (col.blocks || []).find(ib => ib.id === id);
            if (inner) return inner;
          }
        }
      }
      return null;
    };
    const canvasBlock = findCanvasBlock(blocks);
    if (!canvasBlock || canvasBlock.type !== 'composed') return;
    const sourceId = canvasBlock.composedId;
    const source = (appState.composedBlocks || []).find(c => c.id === sourceId);
    if (!source) return;

    // Build child blocks from the source.
    const children = [];

    // Preferred path: source.compositorBlocks is the new flat list of v3
    // blocks. Just clone each child and assign a fresh canvas id. Already
    // contains text/brand_strip/products/image/cta/divider/video etc.
    if (Array.isArray(source.compositorBlocks) && source.compositorBlocks.length > 0) {
      for (const c of source.compositorBlocks) {
        if (!c || !c.type) continue;
        children.push(Object.assign({}, c, { id: mkId() }));
      }
    } else {
      // Legacy path: derive from introText + brandStrip + blockType + products.
      // Same expansion email-gen used to do for legacy composed blocks.
      if (source.introText) {
        const overridesByLang = { es: source.introText };
        if (source.i18n) {
          for (const [l, v] of Object.entries(source.i18n)) {
            if (v && v.introText) overridesByLang[l] = v.introText;
          }
        }
        children.push({ id: mkId(), type: 'text', overridesByLang });
      }
      if (source.brandStrip && source.brandStrip !== 'none') {
        children.push({ id: mkId(), type: 'brand_strip', brand: source.brandStrip });
      }
      const prods = source.products || [];
      if (source.blockType === 'product_trio' && prods.length >= 3) {
        children.push({ id: mkId(), type: 'product_trio', product1: prods[0], product2: prods[1], product3: prods[2] });
      } else if (source.blockType === 'product_pair' && prods.length >= 2) {
        children.push({ id: mkId(), type: 'product_pair', product1: prods[0], product2: prods[1] });
      } else if (source.blockType === 'product_single' && prods.length >= 1) {
        children.push({ id: mkId(), type: 'product_single', product1: prods[0] });
      } else {
        for (const pid of prods) children.push({ id: mkId(), type: 'product_single', product1: pid });
      }
      // includeHero / includeSteps son legacy y se han desactivado en v5 —
      // los heros/pasos ahora se añaden manualmente como bloques sueltos.
      // No los desplegamos aquí para evitar que aparezca un hero genérico
      // que el usuario no eligió explícitamente.
    }
    if (children.length === 0) return;

    // Replace the composed block with its expanded children — at top level OR
    // inside a section column.
    setBlocks(prev => {
      const ti = prev.findIndex(x => x.id === id);
      if (ti >= 0) {
        return [...prev.slice(0, ti), ...children, ...prev.slice(ti + 1)];
      }
      return prev.map(x => {
        if (x.type !== 'section' || !Array.isArray(x.columns)) return x;
        return {
          ...x,
          columns: x.columns.map(col => {
            const ii = (col.blocks || []).findIndex(ib => ib.id === id);
            if (ii < 0) return col;
            return {
              ...col,
              blocks: [...(col.blocks || []).slice(0, ii), ...children, ...(col.blocks || []).slice(ii + 1)],
            };
          }),
        };
      });
    });
    setSelectedId(null);
  };

  const duplicateBlock = (id) => setBlocks(prev => {
    // Top-level duplicate first
    const ti = prev.findIndex(x => x.id === id);
    if (ti >= 0) {
      const copy = { ...prev[ti], id: mkId() };
      return [...prev.slice(0, ti+1), copy, ...prev.slice(ti+1)];
    }
    // Recurse into sections
    return prev.map(x => {
      if (x.type !== 'section' || !Array.isArray(x.columns)) return x;
      return {
        ...x,
        columns: x.columns.map(col => {
          const ii = (col.blocks || []).findIndex(ib => ib.id === id);
          if (ii < 0) return col;
          const copy = { ...col.blocks[ii], id: mkId() };
          return { ...col, blocks: [...col.blocks.slice(0, ii+1), copy, ...col.blocks.slice(ii+1)] };
        }),
      };
    });
  });
  // Mueve un bloque arriba/abajo. Funciona en top-level y, si no se
  // encuentra ahí, recursea por las columnas de cualquier sección. Antes
  // los bloques dentro de una columna no podían reordenarse — los botones
  // ↑↓ estaban escondidos vía `!isInner`. Apr 2026 audit fix.
  const moveBlock = (id, dir) => setBlocks(prev => {
    // 1) Top-level: swap con vecino directo
    const i = prev.findIndex(x => x.id === id);
    if (i >= 0) {
      const j = i + dir;
      if (j < 0 || j >= prev.length) return prev;
      const arr = [...prev];
      [arr[i], arr[j]] = [arr[j], arr[i]];
      return arr;
    }
    // 2) Buscar en columnas de secciones — el swap es DENTRO de la columna,
    // no atraviesa columnas (el up/down dentro de una columna mantiene al
    // bloque en su columna; cruzar requiere drag-drop o eliminar+reañadir).
    let touched = false;
    const next = prev.map(x => {
      if (touched) return x;
      if (x.type !== 'section' || !Array.isArray(x.columns)) return x;
      const cols = x.columns.map(col => {
        if (touched) return col;
        const blocks = col.blocks || [];
        const ii = blocks.findIndex(ib => ib.id === id);
        if (ii < 0) return col;
        const jj = ii + dir;
        if (jj < 0 || jj >= blocks.length) { touched = true; return col; }
        const arr = blocks.slice();
        [arr[ii], arr[jj]] = [arr[jj], arr[ii]];
        touched = true;
        return { ...col, blocks: arr };
      });
      return touched ? { ...x, columns: cols } : x;
    });
    return touched ? next : prev;
  });

  // ─── Undo / Redo for the canvas blocks array ───
  // Two stacks of previous block states. Every time `blocks` changes (except
  // during user-switch loads or the undo/redo itself), the OLD state gets
  // pushed to the past stack so Ctrl+Z can restore it. Cap at 50 entries to
  // bound memory.
  const undoPastRef = React.useRef([]);
  const undoFutureRef = React.useRef([]);
  const undoPrevRef = React.useRef(blocks);
  const undoSkipRef = React.useRef(true); // skip the very first effect run (initial mount)

  React.useEffect(() => {
    if (undoSkipRef.current) {
      undoSkipRef.current = false;
      undoPrevRef.current = blocks;
      return;
    }
    if (undoPrevRef.current === blocks) return;
    undoPastRef.current.push(undoPrevRef.current);
    if (undoPastRef.current.length > 50) undoPastRef.current.shift();
    undoFutureRef.current = []; // any new edit clears the redo stack
    undoPrevRef.current = blocks;
  }, [blocks]);

  // Reset history when the active user changes — Sara shouldn't be able to
  // undo back into Bart's draft. The user-switch effect (above) already sets
  // inLoadRef so its setBlocks call doesn't fire saveDraft; we mirror that
  // for the undo history.
  React.useEffect(() => {
    undoPastRef.current = [];
    undoFutureRef.current = [];
    undoSkipRef.current = true;
  }, [currentUserId]);

  const undo = () => {
    setBlocks(curr => {
      if (undoPastRef.current.length === 0) return curr;
      const prev = undoPastRef.current.pop();
      undoFutureRef.current.push(curr);
      undoSkipRef.current = true;
      return prev;
    });
  };
  const redo = () => {
    setBlocks(curr => {
      if (undoFutureRef.current.length === 0) return curr;
      const next = undoFutureRef.current.pop();
      undoPastRef.current.push(curr);
      undoSkipRef.current = true;
      return next;
    });
  };

  // Keyboard shortcuts: Ctrl/Cmd+Z = undo, Ctrl/Cmd+Shift+Z or Ctrl+Y = redo.
  // Skip when the user is typing in an input/textarea/contenteditable so the
  // browser's native text undo still works inside text fields.
  React.useEffect(() => {
    const isEditableTarget = (el) => {
      if (!el) return false;
      const tag = (el.tagName || '').toUpperCase();
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
      if (el.isContentEditable) return true;
      return false;
    };
    const onKey = (e) => {
      const cmd = e.metaKey || e.ctrlKey;
      if (!cmd) return;
      const key = e.key.toLowerCase();
      if (isEditableTarget(e.target)) return;
      if (key === 'z' && !e.shiftKey) { e.preventDefault(); undo(); }
      else if (key === 'z' && e.shiftKey) { e.preventDefault(); redo(); }
      else if (key === 'y') { e.preventDefault(); redo(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // HTML5 drag-drop reorder: splice the source block out and insert it
  // immediately before (position='before') or after (position='after') the
  // target. No-op when source === target.
  const reorderBlocks = (sourceId, targetId, position) => {
    if (!sourceId || !targetId || sourceId === targetId) return;
    setBlocks(prev => {
      const si = prev.findIndex(b => b.id === sourceId);
      const ti = prev.findIndex(b => b.id === targetId);
      if (si < 0 || ti < 0) return prev;
      const arr = prev.slice();
      const [moved] = arr.splice(si, 1);
      // After removal the target index may have shifted by 1
      const adjustedTi = ti > si ? ti - 1 : ti;
      const insertAt = position === 'after' ? adjustedTi + 1 : adjustedTi;
      arr.splice(insertAt, 0, moved);
      return arr;
    });
  };

  const goBackoffice = () => setMode('backoffice');
  const submitLogin = () => {
    const user = (appState.users || []).find(u => u.id === loginUserId);
    if (!user) { setLoginError(true); return; }
    checkPassword(loginValue, user.passwordHash, DEFAULT_BO_HASH).then(({ match }) => {
      if (match) {
        setCurrentUserId(user.id);
        setLoginValue('');
        setLoginError(false);
        // El logActivity necesita currentUserIdRef ya actualizado — pero el
        // ref se sincroniza en el siguiente effect tras setCurrentUserId.
        // Usamos setTimeout(0) para correr el log tras el commit.
        setTimeout(() => {
          if (typeof window.logActivity === 'function') {
            window.logActivity('login', { userId: user.id, role: user.role });
          }
        }, 0);
      } else {
        setLoginError(true);
        if (typeof window.logActivity === 'function') {
          // Log con userId que intentó loguearse aunque la pwd fallara
          window.logActivity('login_failed', { attemptedUserId: loginUserId });
        }
      }
    });
  };
  const logout = () => {
    if (typeof window.logActivity === 'function') {
      window.logActivity('logout', {});
    }
    setCurrentUserId(null);
    setMode('compositor');
    setLoginValue('');
    setLoginError(false);
    setShowLanding(true); // Volver a la portada al cerrar sesión
  };

  // Helpers for the visibility model.
  const isItemHidden = (collection, id) => {
    if (!currentUser) return false;
    return Array.isArray(currentUser.hiddenItems?.[collection]) && currentUser.hiddenItems[collection].includes(id);
  };
  const setItemHiddenForCurrentUser = (collection, id, hidden) => {
    if (!currentUser) return;
    setAppState(prev => ({
      ...prev,
      users: (prev.users || []).map(u => {
        if (u.id !== currentUser.id) return u;
        const list = Array.isArray(u.hiddenItems?.[collection]) ? u.hiddenItems[collection] : [];
        const next = hidden ? Array.from(new Set([...list, id])) : list.filter(x => x !== id);
        return { ...u, hiddenItems: { ...u.hiddenItems, [collection]: next } };
      }),
    }));
  };
  // When a non-admin user creates an item, hide it from all OTHER users by
  // default. Admin-created items are visible to everyone.
  const autoHideForOthers = (collection, id) => {
    if (!currentUser || currentUser.role === 'admin') return;
    setAppState(prev => ({
      ...prev,
      users: (prev.users || []).map(u => {
        if (u.id === currentUser.id) return u;
        const list = Array.isArray(u.hiddenItems?.[collection]) ? u.hiddenItems[collection] : [];
        return { ...u, hiddenItems: { ...u.hiddenItems, [collection]: Array.from(new Set([...list, id])) } };
      }),
    }));
  };

  // Generate the live email HTML once per render; Preview consumes it.
  // Includes UTM tracking on every external link so opens/clicks can be
  // attributed to the campaign + commercial in GA/Plausible/etc.
  // utm_source=email, utm_medium=bomedia, utm_campaign=YYYYMMDD-brand-lang-title,
  // utm_term=lang, utm_content=<commercial-id>.
  const emailHtml = React.useMemo(() => {
    if (typeof renderEmailHtmlWithTracking === 'function') {
      return renderEmailHtmlWithTracking(blocks, appState, lang, emailTitle, currentUser).html;
    }
    return renderEmailHtml(blocks, appState, lang);
  }, [blocks, appState, lang, emailTitle, currentUser]);

  const blockCount = blocks.length;
  const syncLabel = syncStatus === 'loading' ? 'Cargando…'
    : syncStatus === 'cloud' ? 'Sincronizado (nube)'
    : syncStatus === 'local' ? 'Local (sin nube)'
    : syncStatus === 'local-offline' ? '⚠ Nube no disponible — solo local'
    : 'Sincronizado';

  return (
    <div className="app-shell" style={{ '--right-panel-w': rightPanelWidth + 'px' }}>
      <header className="topbar">
        <div className="topbar-brand">
          {BOMEDIA_LOGO_URL ? (
            <img src={BOMEDIA_LOGO_URL} alt="Bomedia" className="topbar-logo" style={{objectFit:'cover'}}/>
          ) : (
            <div className="topbar-logo" style={{background:'linear-gradient(135deg, #8b5cf6 0%, #ec4899 60%, #3b82f6 100%)', color:'#fff'}}>B</div>
          )}
          <div>
            <div className="topbar-title">bomedia<span className="topbar-title-sub">email composer</span></div>
          </div>
        </div>

        <div className="topbar-crumbs">
          <button
            className={'topbar-crumb' + (mode === 'compositor' ? ' active' : '')}
            onClick={() => setMode('compositor')}
          ><Icon name="layers" size={14} /> Compositor</button>
          <span className="topbar-sep">/</span>
          <button
            className={'topbar-crumb' + (mode === 'backoffice' ? ' active' : '')}
            onClick={goBackoffice}
          >
            <Icon name="database" size={14} />
            Backoffice
          </button>
        </div>

        <button className="topbar-search" onClick={() => setCmdkOpen(true)}>
          <Icon name="search" size={14} />
          <span>Buscar bloques, productos…</span>
          <span className="topbar-search-kbd">⌘K</span>
        </button>

        <div className="topbar-actions">
          {/* CRM-embed only: send the user back to the main CRM
              dashboard. Hidden if the embed isn't running inside the
              CRM (window.__COMPOSER_AUTH_BYPASSED__ is the flag set
              by the index.html bootstrap). */}
          {typeof window !== 'undefined' && window.__COMPOSER_AUTH_BYPASSED__ && (
            <button
              className="icon-btn"
              onClick={() => { window.location.href = '/'; }}
              title="Volver al CRM"
              style={{display:'inline-flex', alignItems:'center', gap:6, padding:'4px 10px', fontSize:12}}
            >
              <Icon name="chevron" size={14} style={{transform:'rotate(180deg)'}} /> CRM
            </button>
          )}
          <div className="lang-pill">
            {LANGS.map(l => (
              <button key={l} className={lang === l ? 'active' : ''} onClick={() => setLang(l)}>{l.toUpperCase()}</button>
            ))}
          </div>
          {mode === 'compositor' && (
            <>
              <button className={'icon-btn' + (previewHidden ? '' : ' active')} onClick={() => setPreviewHidden(v => !v)} title="Preview">
                <Icon name="eye" size={16} />
              </button>
              <button className="icon-btn" title="Copiar HTML — pégalo en Gmail/Outlook con formato (con UTM tracking)" onClick={() => {
                // emailHtml ya incluye UTM tracking (utm_source/medium/
                // campaign/term/content) inyectado en useMemo arriba.
                if (typeof copyHtmlAsRich === 'function') copyHtmlAsRich(emailHtml);
                else navigator.clipboard.writeText(emailHtml).catch(() => {});
              }}>
                <Icon name="code" size={16} />
              </button>
              <button className="icon-btn" title="Compartir"><Icon name="share" size={16} /></button>
            </>
          )}
          {currentUser && (
            <div className="topbar-user" title={currentUser.role === 'admin' ? 'Administrador' : 'Comercial'}>
              <span className={'topbar-user-dot ' + (currentUser.role === 'admin' ? 'admin' : 'commercial')} />
              <span className="topbar-user-name">{currentUser.name}</span>
              <button className="icon-btn" onClick={logout} title="Cerrar sesión" style={{width:24, height:24}}>
                <Icon name="x" size={13} />
              </button>
            </div>
          )}
        </div>
      </header>

      {mode === 'compositor' ? (
        <div className={'main' + (sidebarCollapsed ? ' sidebar-collapsed' : '') + (previewHidden ? ' preview-hidden' : '')}>
          <Sidebar
            collapsed={sidebarCollapsed}
            onToggle={() => setSidebarCollapsed(v => !v)}
            blocks={blocks}
            onAddBlock={addBlock}
            brandFilter={brandFilter}
            setBrandFilter={setBrandFilter}
            lang={lang}
            currentUser={currentUser}
          />
          <Canvas
            blocks={blocks}
            onUpdate={updateBlock}
            onDelete={deleteBlock}
            onMove={moveBlock}
            onReorder={reorderBlocks}
            onDuplicate={duplicateBlock}
            onUngroup={ungroupComposedBlock}
            selectedId={selectedId}
            setSelectedId={(id) => { setSelectedId(id); if (id) setRightMode('edit'); }}
            onOpenPalette={(idx) => {
              // Only accept numeric indices. Without this, calling
              // `onClick={onOpenPalette}` (no parens) would pass the React
              // SyntheticEvent here, which then becomes a NaN insertAfter
              // and makes splice(NaN+1, 0, ...) insert at index 0 — i.e.
              // the bottom "Añadir bloque" button silently inserts at the
              // top of the email instead of the bottom. Bug fix Apr 2026.
              if (typeof idx === 'number' && idx >= 0) setInsertAfter(idx);
              else setInsertAfter(null);
              setCmdkOpen(true);
            }}
            onOpenInnerPalette={(sectionId, columnIdx) => { setInnerTarget({ sectionId, columnIdx }); setCmdkOpen(true); }}
            onAddBlock={(spec, idx) => {
              if (typeof idx === 'number' && idx >= 0) setInsertAfter(idx);
              addBlock(spec);
            }}
            onAddBlockToColumn={(sectionId, columnIdx, spec) => { addBlock(spec, { into: { sectionId, columnIdx } }); }}
            onClearBlocks={() => { setBlocks([]); setSelectedId(null); setEditingTemplateId(null); }}
            onExpandPreview={() => setPreviewModalOpen(true)}
            editingTemplate={editingTemplateId ? (appState.templates || []).find(t => t.id === editingTemplateId) : null}
            onExitTemplateEdit={() => setEditingTemplateId(null)}
            onSaveCurrentTemplate={() => saveCurrentToTemplate(editingTemplateId)}
            onSaveAsTemplate={(name, opts) => saveCurrentAsNewTemplate(name, opts)}
            lang={lang}
            variant={tweaks.titleStyle}
            emailHtml={emailHtml}
            onUndo={undo}
            onRedo={redo}
            appState={appState}
            onSetBlocks={setBlocks}
            onSetLang={setLang}
            emailTitle={emailTitle}
            onEmailTitleChange={setEmailTitle}
          />
          {!previewHidden && (
            <div className="right-panel" style={{display:'flex', flexDirection:'column', minHeight:0, background:'var(--bg-sunken)', borderLeft:'1px solid var(--border)'}}>
              <div
                className="right-panel-resizer"
                onPointerDown={startResize}
                onDoubleClick={() => setRightPanelWidth(560)}
                title="Arrastra para redimensionar · doble click para resetear"
              />
              <div className="right-mode">
                <button
                  className={'right-mode-btn' + (rightMode === 'preview' ? ' active' : '')}
                  onClick={() => setRightMode('preview')}
                >
                  <Icon name="eye" size={12} /> Preview
                </button>
                <button
                  className={'right-mode-btn' + (rightMode === 'edit' ? ' active' : '')}
                  onClick={() => { if (!selectedId && blocks[0]) setSelectedId(blocks[0].id); setRightMode('edit'); }}
                  disabled={!selectedId && blocks.length === 0}
                  style={{opacity: (!selectedId && blocks.length === 0) ? 0.4 : 1}}
                >
                  <Icon name="settings" size={12} /> Editar
                  {selectedId && <span style={{marginLeft:6, fontSize:10, fontFamily:'var(--font-mono)', color:'var(--text-subtle)'}}>1</span>}
                </button>
              </div>

              {rightMode === 'edit' && selectedId ? (
                <Inspector
                  block={(() => {
                    // Search top-level then inside section columns
                    const top = blocks.find(b => b.id === selectedId);
                    if (top) return top;
                    for (const x of blocks) {
                      if (x.type === 'section' && Array.isArray(x.columns)) {
                        for (const col of x.columns) {
                          const inner = (col.blocks || []).find(ib => ib.id === selectedId);
                          if (inner) return inner;
                        }
                      }
                    }
                    return null;
                  })()}
                  onUpdate={updateBlock}
                  onClose={() => { setSelectedId(null); setRightMode('preview'); }}
                  onDelete={deleteBlock}
                  onDuplicate={duplicateBlock}
                  lang={lang}
                  setLang={setLang}
                  onOpenBackoffice={isAdmin ? goBackoffice : null}
                  appState={appState}
                />
              ) : rightMode === 'edit' ? (
                <div style={{padding:40, textAlign:'center', color:'var(--text-muted)', fontSize:13, flex:1, display:'flex', flexDirection:'column', justifyContent:'center', alignItems:'center', gap:10}}>
                  <Icon name="sparkles" size={24} />
                  <div className="serif" style={{fontSize:18}}>Selecciona un bloque</div>
                  <div style={{fontSize:12}}>Haz click sobre cualquier bloque del canvas para editarlo</div>
                </div>
              ) : (
                <PreviewPanel
                  blocks={blocks}
                  device={device}
                  setDevice={setDevice}
                  tab={previewTab}
                  setTab={setPreviewTab}
                  lang={lang}
                  emailHtml={emailHtml}
                  onExpand={() => setPreviewModalOpen(true)}
                  embedded
                />
              )}
            </div>
          )}
        </div>
      ) : (
        <Backoffice
          appState={appState}
          setAppState={setAppState}
          brandFilter={brandFilter}
          setBrandFilter={setBrandFilter}
          onLoadTemplateInCompositor={(tplId) => loadTemplateIntoCanvas(tplId)}
          currentUser={currentUser}
          lang={lang}
          isItemHidden={isItemHidden}
          setItemHiddenForCurrentUser={setItemHiddenForCurrentUser}
          autoHideForOthers={autoHideForOthers}
        />
      )}

      <div className="footer">
        <span className="status"><span className="status-dot" /> {syncLabel}</span>
        <span className="dot" />
        <span>Supabase · {syncStatus === 'cloud' ? 'cloud' : 'local'}</span>
        <span className="dot" />
        <span>{blockCount} bloques</span>
        <span className="dot" />
        <span>{(appState.products || []).length} productos · {(appState.templates || []).length} plantillas</span>
        <div style={{marginLeft:'auto', display:'flex', gap:16, alignItems:'center'}}>
          <span>v4.5 · By Edu & Claude Code</span>
          <span className="dot" />
          <span>⌘K para comandos</span>
        </div>
      </div>

      {previewModalOpen && (
        <EmailPreviewModal
          html={emailHtml}
          lang={lang}
          onClose={() => setPreviewModalOpen(false)}
        />
      )}

      {cmdkOpen && (
        <CommandPalette
          appState={appState}
          currentUser={currentUser}
          onClose={() => setCmdkOpen(false)}
          onPick={(item) => {
            if (item.type === 'template') addBlock({ type: 'template', templateId: item.id });
            else if (item.type === 'product') addBlock({ type: 'product', productId: item.id });
            else if (item.type === 'text') addBlock({ type: 'text', textId: item.id });
            else if (item.type === 'composed') addBlock({ type: 'composed', composedId: item.id });
            else addBlock({ type: item.type, standaloneId: item.id });
          }}
        />
      )}

      {!currentUser && syncStatus !== 'loading' && showLanding && (
        <LandingScreen onEnter={() => setShowLanding(false)} />
      )}

      {!currentUser && syncStatus !== 'loading' && !showLanding && (
        <div className="modal-overlay login-overlay" onClick={e => e.stopPropagation()}>
          <div className="modal login-modal" onClick={e => e.stopPropagation()}>
            <div style={{display:'flex', alignItems:'center', gap:10, marginBottom:6}}>
              {BOMEDIA_LOGO_URL ? (
                <img src={BOMEDIA_LOGO_URL} alt="Bomedia" className="topbar-logo" style={{width:32, height:32, objectFit:'cover'}}/>
              ) : (
                <div className="topbar-logo" style={{width:32, height:32, background:'linear-gradient(135deg, #8b5cf6 0%, #ec4899 60%, #3b82f6 100%)', color:'#fff'}}>B</div>
              )}
              <div>
                <h2 style={{fontSize:16, margin:0}}>bomedia <span className="serif" style={{color:'var(--text-muted)'}}>email composer</span></h2>
                <div style={{fontSize:11, color:'var(--text-muted)', marginTop:2}}>Selecciona usuario y contraseña para continuar.</div>
              </div>
            </div>
            <div className="field" style={{marginTop:12}}>
              <label className="field-label">Usuario</label>
              <select
                className="select"
                value={loginUserId}
                onChange={e => { setLoginUserId(e.target.value); setLoginError(false); }}
              >
                {(appState.users || []).map(u => (
                  <option key={u.id} value={u.id}>{u.name} ({u.role})</option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="field-label">Contraseña</label>
              <input
                type="password"
                className="input"
                value={loginValue}
                onChange={e => { setLoginValue(e.target.value); setLoginError(false); }}
                onKeyDown={e => e.key === 'Enter' && submitLogin()}
                placeholder="••••••••"
                autoFocus
                style={loginError ? {borderColor:'var(--danger)'} : {}}
              />
              {loginError && <div style={{fontSize:12, color:'var(--danger)', marginTop:6}}>Usuario o contraseña incorrectos</div>}
            </div>
            <div className="modal-actions" style={{display:'flex', flexDirection:'column', gap:6}}>
              <button className="btn btn-primary" onClick={submitLogin} style={{width:'100%', justifyContent:'center'}}>
                <Icon name="zap" size={13}/> Entrar
              </button>
              <button className="btn btn-ghost" onClick={() => setShowLanding(true)} style={{width:'100%', justifyContent:'center', fontSize:12}}>
                ← Volver a la portada
              </button>
            </div>
          </div>
        </div>
      )}

      {tweaksOpen ? (
        <DraggableTweaks
          tweaks={tweaks}
          updateTweak={updateTweak}
          onClose={() => setTweaksOpen(false)}
        />
      ) : null}
    </div>
  );
}

/* URLs del branding de Bomedia (subidas a boprint.net WP Media). El logo
   se usa en topbar + login modal; el banner se usa como hero del landing.
   Si están vacíos, se renderizan fallbacks CSS-gradient. */
const BOMEDIA_LOGO_URL = 'https://boprint.net/wp-content/uploads/2026/04/logo-app.jpg';
const BOMEDIA_BANNER_URL = 'https://boprint.net/wp-content/uploads/2026/04/banner-app.jpg';

/* Pantalla de bienvenida. Se muestra antes del login. El user pulsa
   "Entrar" → se cierra y aparece el modal de credenciales. */
function LandingScreen({ onEnter }) {
  const [hover, setHover] = React.useState(false);
  return (
    <div style={{
      position:'fixed', inset:0, zIndex:1000,
      display:'flex', flexDirection:'column',
      background:'linear-gradient(135deg, #f7f3ff 0%, #fef0f7 40%, #eff5ff 80%, #f7f3ff 100%)',
      overflow:'auto',
    }}>
      <div style={{flex:1, display:'flex', alignItems:'center', justifyContent:'center', padding:'30px 20px'}}>
        <div style={{textAlign:'center', maxWidth:1100, width:'100%'}}>
          {BOMEDIA_BANNER_URL ? (
            <img
              src={BOMEDIA_BANNER_URL}
              alt="Bomedia Email Composer"
              style={{
                width:'100%', maxWidth:1000, height:'auto',
                display:'block', margin:'0 auto 32px',
                borderRadius:18,
                boxShadow:'0 30px 80px rgba(139,92,246,0.18), 0 8px 20px rgba(26,25,24,0.08)',
              }}
            />
          ) : (
            <>
              {BOMEDIA_LOGO_URL ? (
                <img src={BOMEDIA_LOGO_URL} alt="Bomedia" style={{width:140, height:140, marginBottom:32, borderRadius:32, boxShadow:'0 30px 60px rgba(139,92,246,0.25)'}}/>
              ) : (
                <div style={{
                  width:140, height:140, margin:'0 auto 32px',
                  background:'linear-gradient(135deg, #8b5cf6 0%, #ec4899 50%, #3b82f6 100%)',
                  borderRadius:32,
                  display:'grid', placeItems:'center',
                  fontSize:88, fontWeight:900, color:'#fff',
                  boxShadow:'0 30px 60px rgba(139,92,246,0.35)',
                }}>B</div>
              )}
              <h1 style={{
                fontSize:'clamp(40px, 6vw, 64px)', fontWeight:800, lineHeight:1.05,
                margin:'0 0 12px', letterSpacing:'-0.025em',
                background:'linear-gradient(135deg, #1a1918 0%, #1a1918 35%, #8b5cf6 65%, #ec4899 100%)',
                WebkitBackgroundClip:'text', WebkitTextFillColor:'transparent',
                backgroundClip:'text',
              }}>
                Bomedia<br/>Email Composer
              </h1>
              <p style={{fontSize:'clamp(16px, 2vw, 20px)', color:'#4a4742', margin:'24px 0 6px', fontWeight:500}}>
                Crea emails comerciales en minutos
              </p>
              <p style={{fontSize:14, color:'#8a8780', margin:'0 0 44px', lineHeight:1.5, maxWidth:520, marginLeft:'auto', marginRight:'auto'}}>
                Plantillas, productos, idiomas e IA en una sola herramienta.
              </p>
            </>
          )}
          <button
            onClick={onEnter}
            onMouseEnter={() => setHover(true)}
            onMouseLeave={() => setHover(false)}
            style={{
              padding:'14px 44px', fontSize:16, fontWeight:600,
              background:'linear-gradient(135deg, #8b5cf6 0%, #ec4899 100%)',
              color:'#fff', border:'none', borderRadius:12,
              cursor:'pointer',
              boxShadow: hover ? '0 18px 36px rgba(139,92,246,0.45)' : '0 12px 24px rgba(139,92,246,0.3)',
              transform: hover ? 'translateY(-1px)' : 'translateY(0)',
              transition:'all 0.18s ease',
              display:'inline-flex', alignItems:'center', gap:8,
            }}
          >
            Entrar →
          </button>
          <div style={{marginTop:14, fontSize:11, color:'#a8a59c'}}>
            Acceso restringido al equipo Bomedia
          </div>
        </div>
      </div>
      <div style={{padding:'20px 30px', textAlign:'center', fontSize:11, color:'#9c9a91', letterSpacing:'0.02em'}}>
        Bomedia Composer V4.5 · By Edu & Claude Code
      </div>
    </div>
  );
}

function DraggableTweaks({ tweaks, updateTweak, onClose }) {
  const [pos, setPos] = React.useState(() => {
    try {
      const s = localStorage.getItem('tweaks-pos');
      if (s) return JSON.parse(s);
    } catch(e) {}
    return { x: window.innerWidth - 280, y: window.innerHeight - 340 };
  });
  const [minimized, setMinimized] = React.useState(() => {
    try { return localStorage.getItem('tweaks-min') === '1'; } catch(e) { return false; }
  });

  React.useEffect(() => {
    try { localStorage.setItem('tweaks-pos', JSON.stringify(pos)); } catch(e) {}
  }, [pos]);
  React.useEffect(() => {
    try { localStorage.setItem('tweaks-min', minimized ? '1' : '0'); } catch(e) {}
  }, [minimized]);

  const onMouseDown = (e) => {
    if (e.target.closest('.tweaks-head-btn')) return;
    const startX = e.clientX, startY = e.clientY;
    const origX = pos.x, origY = pos.y;
    const move = (ev) => {
      const nx = Math.max(8, Math.min(window.innerWidth - 80, origX + ev.clientX - startX));
      const ny = Math.max(8, Math.min(window.innerHeight - 50, origY + ev.clientY - startY));
      setPos({ x: nx, y: ny });
    };
    const up = () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  };

  return (
    <div
      className={'tweaks' + (minimized ? ' minimized' : '')}
      style={{ left: pos.x, top: pos.y, bottom: 'auto', right: 'auto' }}
    >
      <div className="tweaks-head" onMouseDown={onMouseDown}>
        <Icon name="dots" size={14} />
        <span className="tweaks-head-title">Tweaks</span>
        <button className="tweaks-head-btn" onClick={() => setMinimized(m => !m)} title={minimized ? 'Expandir' : 'Minimizar'}>
          <Icon name={minimized ? 'arrowUp' : 'arrowDown'} size={12} />
        </button>
        <button className="tweaks-head-btn" onClick={onClose} title="Cerrar">
          <Icon name="x" size={12} />
        </button>
      </div>
      <div className="tweaks-content">
        <div className="tweaks-group">
          <div className="tweaks-label">Tema de la app</div>
          <div className="tweaks-row">
            {['default','warm','cool','dark'].map(t => (
              <button key={t} className={'tweaks-opt' + (tweaks.theme === t ? ' active' : '')} onClick={() => updateTweak('theme', t)}>
                {t === 'default' ? 'Slate' : t[0].toUpperCase()+t.slice(1)}
              </button>
            ))}
          </div>
        </div>
        <div className="tweaks-group">
          <div className="tweaks-label">Título del canvas</div>
          <div className="tweaks-row two">
            <button className={'tweaks-opt' + (tweaks.titleStyle === 'serif' ? ' active' : '')} onClick={() => updateTweak('titleStyle', 'serif')}>
              <span className="serif" style={{fontSize:14}}>Serif</span>
            </button>
            <button className={'tweaks-opt' + (tweaks.titleStyle === 'sans' ? ' active' : '')} onClick={() => updateTweak('titleStyle', 'sans')}>
              Sans
            </button>
          </div>
        </div>
        <div style={{fontSize:10, color:'var(--text-subtle)', fontFamily:'var(--font-mono)', marginTop:10, paddingTop:10, borderTop:'1px solid var(--border)'}}>
          Arrastra desde la cabecera
        </div>
      </div>
    </div>
  );
}

// Error boundary so a runtime crash during render doesn't leave a blank page
class AppErrorBoundary extends React.Component {
  constructor(p) { super(p); this.state = { err: null }; }
  static getDerivedStateFromError(err) { return { err }; }
  componentDidCatch(err, info) { console.error('App crashed:', err, info); }
  render() {
    if (this.state.err) {
      return (
        <div style={{padding:20, fontFamily:'monospace', fontSize:12, color:'#b91c1c', background:'#fef2f2', minHeight:'100vh'}}>
          <h2 style={{marginBottom:12}}>Error al renderizar la app</h2>
          <pre style={{whiteSpace:'pre-wrap'}}>{String(this.state.err?.stack || this.state.err)}</pre>
          <p style={{marginTop:16, color:'#64748b'}}>Revisa la consola del navegador (F12) para más detalles.</p>
        </div>
      );
    }
    return this.props.children;
  }
}

// Sanity log — surfaces missing globals before React mounts
(function sanityCheck() {
  const needs = ['getDefaultState','createBlock','LANGS','getStorageData','saveStorageData',
    'getDraftBlocks','saveDraftBlocks','loadFromSupabase','saveToSupabase','copyHtmlAsRich',
    'renderEmailHtml','mergeI18nFromDefaults','checkPassword','DEFAULT_BO_HASH',
    'getOpenaiKey','setOpenaiKey','getAiStyles','saveAiStyle','callOpenAI',
    'Sidebar','Canvas','PreviewPanel','CommandPalette','EmailPreviewModal','Inspector','Backoffice','Icon'];
  const missing = needs.filter(n => typeof window[n] === 'undefined');
  if (missing.length) {
    console.error('bomedia: missing globals →', missing);
    const pre = document.createElement('pre');
    pre.style.cssText = 'padding:20px;font-family:monospace;color:#b91c1c;background:#fef2f2;white-space:pre-wrap';
    pre.textContent = 'bomedia v3: estos símbolos no están definidos en window:\n  ' + missing.join('\n  ') +
      '\n\nProbable causa: un script no se cargó. Si abriste el archivo con doble-click (file://),' +
      '\nsírvelo con un servidor local: `python -m http.server 8080` en la carpeta bomedia-v3,' +
      '\ny abre http://localhost:8080/';
    document.getElementById('root').appendChild(pre);
  }
})();

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<AppErrorBoundary><App /></AppErrorBoundary>);
