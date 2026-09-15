"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { CompanyLogoThumbnail } from "../../components/erp/CompanyLogoThumbnail";
import { PageHeader } from "../../components/PageHeader";
import { extractErrorMessage } from "../../lib/errors";
import {
  deleteFactusolCompanyLogo,
  getErpNextReferences,
  getErpSettings,
  previewInvoiceEmailTemplate,
  sendInvoiceEmailTemplateTest,
  updateErpSettings,
  uploadFactusolCompanyLogo,
  type ErpNextReferences,
  type ErpSettings,
  type FactusolCompany,
  type InvoiceEmailTemplatePreview,
} from "../../lib/erpApi";
import { getEmailAliases, getMyEmailAliases, type MyAlias } from "../../lib/emailsApi";

/** Idiomas de las plantillas del email de factura (los del PDF, E4). */
const INVOICE_EMAIL_LANGS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "es", label: "Español" },
  { value: "en", label: "English" },
  { value: "de", label: "Deutsch" },
  { value: "fr", label: "Français" },
  { value: "nl", label: "Nederlands" },
];

/** ERP-F5 — tiendas con contrapartida PayPal propia (la clave es la que
 *  guarda el backend; `flux` de Woo se normaliza a `fluxlasers`). */
const PAYPAL_STORES: ReadonlyArray<{ key: string; label: string }> = [
  { key: "artisjet", label: "artisJet" },
  { key: "boprint", label: "boprint" },
  { key: "fluxlasers", label: "fluxlasers" },
];

/** ERP-E4 — campos de texto de la identidad fiscal de cada empresa emisora
 *  (alimentan los PDF; los valores iniciales salen de los modelos reales de
 *  FACTUSOL). */
type CompanyTextKey =
  | "nombre" | "direccion" | "cp_poblacion" | "pais" | "telefono" | "email" | "nif"
  | "idioma_defecto";

const COMPANY_FIELDS: { key: CompanyTextKey; label: string }[] = [
  { key: "nombre", label: "Nombre fiscal" },
  { key: "direccion", label: "Domicilio" },
  { key: "cp_poblacion", label: "CP y población" },
  { key: "pais", label: "País" },
  { key: "telefono", label: "Teléfono" },
  { key: "email", label: "Email" },
  { key: "nif", label: "NIF / VAT (tal como debe imprimirse)" },
  { key: "idioma_defecto", label: "Idioma por defecto (es/en/de/fr/nl)" },
];

const COMPANY_TEXTS: {
  field: "legal" | "pie" | "intracom" | "titulo_albaran_valorado";
  lang: string; label: string;
}[] = [
  { field: "legal", lang: "es", label: "Reserva de dominio / texto legal (ES)" },
  { field: "legal", lang: "en", label: "Reserva de dominio / texto legal (EN)" },
  { field: "intracom", lang: "es", label: "Texto intracomunitario (ES)" },
  { field: "intracom", lang: "en", label: "Texto intracomunitario (EN)" },
  { field: "pie", lang: "es", label: "Pie de condiciones (ES)" },
  { field: "titulo_albaran_valorado", lang: "es",
    label: "Título del albarán valorado (ES) — p. ej. «ALBARÁN DE ENTREGA»" },
];

/** Orígenes de pedido con serie de facturación propia opcional (C-2).
 *  Espejo del enum `OrderSource` del backend. */
const ORDER_SOURCES = [
  { value: "woocommerce", label: "WooCommerce (las 3 tiendas)" },
  { value: "manual", label: "Manual" },
  { value: "factusol_proforma", label: "Proforma FACTUSOL" },
];

/** Prefijo de referencia válido (mismo criterio que el backend). */
const REF_PREFIX_RE = /^[A-Z0-9]{1,6}$/;

/** Lote 2 · PR-2 — cada sección se guarda por separado: el PATCH lleva SOLO
 *  sus campos (el backend acepta cualquier subconjunto). El orden es el de la
 *  pantalla. */
type SectionId =
  | "facturacion" | "tiendas" | "remitentes" | "plantillas" | "series"
  | "abreviaturas" | "sat" | "empresas" | "almacenes" | "contrapartidas"
  | "origenes" | "drive";

const SECTIONS: ReadonlyArray<{ id: SectionId; title: string; keys: (keyof ErpSettings)[] }> = [
  { id: "facturacion", title: "Facturación",
    keys: ["default_invoice_mode", "auto_invoice_max_amount_eur",
           "factusol_default_ejercicio", "factusol_live"] },
  { id: "tiendas", title: "Tiendas y referencias", keys: ["factusol_ref_prefix_by_store"] },
  { id: "remitentes", title: "Remitentes del email de factura",
    keys: ["factusol_store_email_from", "factusol_series_email_from"] },
  { id: "plantillas", title: "Plantillas del email de factura",
    keys: ["factusol_invoice_email_templates"] },
  { id: "series", title: "Series FACTUSOL",
    keys: ["factusol_series_default", "factusol_series_by_source",
           "factusol_estpcl_invoiced", "factusol_estpre_accepted", "factusol_estalb_invoiced",
           "factusol_estfac_cobrada", "factusol_estfac_pendiente",
           "factusol_auto_mark_paid_when_order_paid"] },
  { id: "abreviaturas", title: "Abreviaturas de empresa", keys: ["factusol_series_abbreviations"] },
  { id: "sat", title: "Email del SAT / taller", keys: ["sat_email"] },
  { id: "empresas", title: "Empresas emisoras", keys: ["factusol_companies"] },
  { id: "almacenes", title: "Almacenes de recogida", keys: ["factusol_pickup_warehouses"] },
  { id: "contrapartidas", title: "Contrapartidas de cobro",
    keys: ["contrapartidas", "paypal_contrapartidas_by_store"] },
  { id: "origenes", title: "Orígenes del envío", keys: ["shipping_origins"] },
  { id: "drive", title: "Hoja de seguimiento en Drive",
    keys: ["drive_spreadsheet_id", "drive_service_account_json", "drive_reference_prefer_albaran"] },
];

/** Estado de guardado de una sección: `saved` = el último guardado fue bien
 *  (se enseña «Guardado.» hasta que vuelva a haber cambios). */
type SectionStatus = { busy: boolean; error: string | null; saved: boolean };
const IDLE: SectionStatus = { busy: false, error: null, saved: false };

/** Subconjunto de `obj` con esas claves (solo las definidas). */
function pick<T extends object>(obj: T, keys: (keyof T)[]): Partial<T> {
  const out: Partial<T> = {};
  for (const k of keys) {
    if (obj[k] !== undefined) out[k] = obj[k];
  }
  return out;
}

/** Valor comparable de un campo: `undefined`/`null` iguales; el JSON de la
 *  cuenta de servicio es write-only (nunca vuelve del GET), así que vacío
 *  cuenta como «sin cambios». */
function canon(key: keyof ErpSettings, value: unknown): string {
  if (key === "drive_service_account_json" && (value === "" || value == null)) return "null";
  return JSON.stringify(value ?? null);
}

/** Nombre legible de una serie: «5 (Streamtec)» si se conoce la empresa. */
function serieLabel(cfg: ErpSettings, serie: string): string {
  const s = serie.trim();
  if (!s) return "";
  const name = cfg.factusol_series_names?.[s]
    ?? cfg.factusol_companies?.[s]?.nombre
    ?? cfg.factusol_series_abbreviations?.[s];
  return name ? `${s} (${name})` : s;
}

/** Serie prevista para un origen o tienda: la suya → la de WooCommerce
 *  (tiendas) → la por defecto. Vacío si no hay ninguna. */
function serieFor(cfg: ErpSettings, key: string, isStore: boolean): string {
  const own = (cfg.factusol_series_by_source?.[key] ?? "").trim();
  if (own) return own;
  if (isStore) {
    const woo = (cfg.factusol_series_by_source?.woocommerce ?? "").trim();
    if (woo) return woo;
  }
  return (cfg.factusol_series_default ?? "").trim();
}

/** Primeras líneas del cuerpo de ejemplo, en una sola frase corta. */
function firstLines(text: string, max = 150): string {
  const flat = text.split("\n").map((l) => l.trim()).filter(Boolean).join(" ");
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

const SENDER_SOURCE_LABEL: Record<string, string> = {
  serie: "remitente de la serie por defecto",
  tienda: "remitente de la tienda",
  usuario: "alias por defecto de quien envía",
};

export default function ErpSettingsPage() {
  // `saved` = lo último que devolvió el servidor; `draft` = lo que se está
  // editando. La diferencia por sección es el indicador «sin guardar».
  const [saved, setSaved] = useState<ErpSettings | null>(null);
  const [draft, setDraft] = useState<ErpSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<Partial<Record<SectionId, SectionStatus>>>({});
  const [nextRefs, setNextRefs] = useState<ErpNextReferences | null>(null);
  // ERP-F3 — se bumpea tras subir/quitar un logo para refrescar la miniatura.
  const [logoRefresh, setLogoRefresh] = useState(0);
  // «Enviar factura al cliente» — los «enviar como» del usuario, como
  // sugerencia (datalist) para el remitente por tienda. Best-effort.
  const [aliases, setAliases] = useState<MyAlias[]>([]);

  const refreshNextRefs = useCallback(() => {
    getErpNextReferences().then(setNextRefs).catch(() => setNextRefs(null));
  }, []);

  useEffect(() => {
    getErpSettings()
      .then((cfg) => { setSaved(cfg); setDraft(cfg); })
      .catch((e) => setError(extractErrorMessage(e, "No se pudo cargar la configuración.")));
    refreshNextRefs();
    // Sugerencias de remitente: TODOS los «enviar como» verificados de la
    // cuenta de Gmail (los de tienda, como pedidos@streamtec.es, no son de
    // ningún usuario y no salen en «mis alias»), más los propios del usuario.
    Promise.all([
      getEmailAliases().catch(() => []),
      getMyEmailAliases().catch(() => []),
    ]).then(([all, mine]) => {
      const seen = new Set<string>();
      const merged: MyAlias[] = [];
      for (const a of [...all, ...mine]) {
        const key = a.send_as_email.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        merged.push({
          send_as_email: a.send_as_email,
          display_name: a.display_name,
          is_default: a.is_default,
          resolved_display_name: a.resolved_display_name,
        });
      }
      setAliases(merged);
    }).catch(() => setAliases([]));
  }, [refreshNextRefs]);

  // Solo `can_edit === false` desactiva el guardado (respuestas antiguas sin
  // el campo siguen funcionando).
  const canEdit = draft?.can_edit !== false;

  const dirtyById = useMemo(() => {
    const out: Partial<Record<SectionId, boolean>> = {};
    if (!draft || !saved) return out;
    for (const s of SECTIONS) {
      out[s.id] = s.keys.some((k) => canon(k, draft[k]) !== canon(k, saved[k]));
    }
    return out;
  }, [draft, saved]);
  const anyDirty = SECTIONS.some((s) => dirtyById[s.id]);

  // Aviso nativo del navegador al salir con cambios sin guardar (solo tiene
  // sentido para quien puede guardarlos).
  useEffect(() => {
    if (!anyDirty || !canEdit) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [anyDirty, canEdit]);

  const patch = useCallback((p: Partial<ErpSettings>) => {
    setDraft((d) => (d ? { ...d, ...p } : d));
  }, []);

  const setSectionStatus = (id: SectionId, s: Partial<SectionStatus>) => {
    setStatus((prev) => ({ ...prev, [id]: { ...(prev[id] ?? IDLE), ...s } }));
  };

  async function saveSection(id: SectionId) {
    if (!draft) return;
    const section = SECTIONS.find((s) => s.id === id);
    if (!section) return;
    const payload = pick(draft, section.keys);
    // ERP-F6: el JSON de la cuenta de servicio es write-only. Vacío = no
    // tocar las credenciales guardadas (no se envía el campo).
    if (id === "drive" && !payload.drive_service_account_json?.trim()) {
      delete payload.drive_service_account_json;
    }
    setSectionStatus(id, { busy: true, error: null });
    try {
      const next = await updateErpSettings(payload);
      setSaved(next);
      // Solo se refrescan en el borrador los campos de ESTA sección (ya
      // normalizados por el servidor); el resto de secciones conserva lo que
      // se estuviera editando.
      setDraft((d) => ({
        ...(d ?? next),
        ...pick(next, section.keys),
        ...(id === "drive" ? { drive_service_account_json: "" } : {}),
      }));
      setSectionStatus(id, { busy: false, error: null, saved: true });
      if (id === "tiendas") refreshNextRefs();
    } catch (e) {
      setSectionStatus(id, {
        busy: false, saved: false,
        error: extractErrorMessage(e, "No se pudo guardar. ¿Eres admin?"),
      });
    }
  }

  if (!draft) {
    return <main className="shell"><p className="muted">{error ?? "Cargando…"}</p></main>;
  }
  const cfg = draft;

  const sectionProps = (id: SectionId) => {
    const s = SECTIONS.find((x) => x.id === id) ?? SECTIONS[0];
    const st = status[id] ?? IDLE;
    return {
      id, title: s.title, dirty: !!dirtyById[id], busy: st.busy, error: st.error,
      saved: st.saved, canEdit, onSave: () => saveSection(id),
    };
  };

  const setCompany = (serie: string, comp: FactusolCompany) => patch({
    factusol_companies: { ...(cfg.factusol_companies ?? {}), [serie]: comp },
  });

  const stores = cfg.woocommerce_stores ?? [];
  const nextByStore = new Map((nextRefs?.stores ?? []).map((s) => [s.slug, s] as const));

  return (
    <main className="shell">
      <PageHeader
        title="Configuración ERP"
        eyebrow="ERP"
        description="Cada cambio afecta a todos los pedidos: cada ajuste enseña al lado lo que va a pasar y cada sección se guarda por separado."
        crumbs={[{ label: "ERP" }, { label: "Configuración" }]}
      />
      {error ? <p className="form-error">{error}</p> : null}
      {!canEdit ? (
        <p className="muted" role="note">
          Puedes consultar los ajustes, pero solo un administrador puede guardarlos.
        </p>
      ) : null}
      <nav className="erp-settings-nav" aria-label="Secciones de la configuración">
        {SECTIONS.map((s) => (
          <a
            key={s.id}
            href={`#ajuste-${s.id}`}
            className={dirtyById[s.id] ? "is-dirty" : undefined}
            title={dirtyById[s.id] ? "Cambios sin guardar" : undefined}
          >
            {s.title}{dirtyById[s.id] ? " •" : ""}
          </a>
        ))}
      </nav>
      <div className="erp-settings-form">
        {/* ---------------------------------------------------------------- */}
        <SettingsSection
          {...sectionProps("facturacion")}
          lead="Cuándo se emite la factura de un pedido y en qué ejercicio de FACTUSOL se trabaja."
        >
          <label className="field">
            <span>Modo de facturación por defecto</span>
            <select
              value={cfg.default_invoice_mode}
              aria-label="Modo de facturación"
              onChange={(e) => patch({ default_invoice_mode: e.target.value as ErpSettings["default_invoice_mode"] })}
            >
              <option value="manual">Manual (siempre revisión)</option>
              <option value="auto">Automática al cobrar</option>
              <option value="auto_under_max">Automática bajo importe máximo</option>
            </select>
            <span className="erp-settings-result">
              {cfg.default_invoice_mode === "manual"
                ? "Cada factura se emite a mano tras revisar el pedido."
                : cfg.default_invoice_mode === "auto"
                  ? "La factura se emite sola en cuanto el pedido consta cobrado."
                  : cfg.auto_invoice_max_amount_eur != null
                    ? `Se emite sola al cobrar si el importe no supera ${cfg.auto_invoice_max_amount_eur} €; por encima, revisión manual.`
                    : "Se emite sola al cobrar por debajo del importe máximo: falta indicarlo."}
            </span>
          </label>
          <label className="field">
            <span>Importe máximo para factura automática (€)</span>
            <input
              type="number" inputMode="decimal"
              value={cfg.auto_invoice_max_amount_eur ?? ""}
              aria-label="Importe máximo factura automática"
              onChange={(e) => patch({ auto_invoice_max_amount_eur: e.target.value ? Number(e.target.value) : null })}
            />
          </label>
          <label className="field">
            <span>Ejercicio FACTUSOL por defecto</span>
            <input
              type="text" maxLength={4} placeholder="2026"
              value={cfg.factusol_default_ejercicio ?? ""}
              aria-label="Ejercicio FACTUSOL"
              onChange={(e) => patch({ factusol_default_ejercicio: e.target.value || null })}
            />
            <span className="erp-settings-result">
              {cfg.factusol_default_ejercicio
                ? <>Los documentos se buscan y se crean en el ejercicio <span className="mono">{cfg.factusol_default_ejercicio}</span>.</>
                : "Vacío: se usa el ejercicio configurado en el servidor."}
            </span>
          </label>
          <label className="field erp-check-field">
            <input
              type="checkbox"
              checked={cfg.factusol_live}
              aria-label="FACTUSOL en producción"
              onChange={(e) => patch({ factusol_live: e.target.checked })}
            />
            <span>FACTUSOL en producción (Fase C)</span>
            <span className="muted small">
              Al activar, la ficha del pedido consulta FACTUSOL en vivo para
              detectar si ya existe factura o albarán (y vincularla sola). No
              añade bloqueos a la Cola PEDIDOS.
            </span>
          </label>
        </SettingsSection>

        {/* ---------------------------------------------------------------- */}
        <SettingsSection
          {...sectionProps("tiendas")}
          lead="El prefijo va delante del número de pedido en la referencia que la app Woo→FACTUSOL escribe en los documentos; con él BoHub localiza el pedido web y sus documentos."
          note="Cambiar un prefijo no afecta a los pedidos ya creados."
        >
          {stores.length === 0 ? (
            <p className="muted small">No hay tiendas WooCommerce dadas de alta.</p>
          ) : null}
          {stores.map((store, i) => {
            const typed = (cfg.factusol_ref_prefix_by_store?.[store.slug] ?? "").trim();
            const prefix = store.ref_prefix_metadata || typed || store.derived_ref_prefix || "";
            const next = nextByStore.get(store.slug);
            const valid = !typed || REF_PREFIX_RE.test(typed);
            const ref = prefix && next ? `${prefix}-${String(next.next_number).padStart(6, "0")}` : null;
            return (
              <div className="erp-settings-row" key={store.slug}>
                <span className="erp-settings-row-name">
                  <span className={`erp-settings-dot is-${(i % 3) + 1}`} aria-hidden="true" />
                  {store.label}
                </span>
                {store.ref_prefix_metadata ? (
                  <span
                    className="erp-settings-fixed mono"
                    title="Fijado en la cuenta de la tienda (metadata); manda sobre este ajuste"
                  >
                    {store.ref_prefix_metadata}
                  </span>
                ) : (
                  <input
                    type="text" maxLength={6} className="mono erp-settings-prefix"
                    aria-label={`Prefijo referencia FACTUSOL tienda ${store.label}`}
                    aria-invalid={!valid || undefined}
                    placeholder={store.derived_ref_prefix ?? ""}
                    value={cfg.factusol_ref_prefix_by_store?.[store.slug] ?? ""}
                    onChange={(e) => patch({
                      factusol_ref_prefix_by_store: {
                        ...(cfg.factusol_ref_prefix_by_store ?? {}),
                        [store.slug]: e.target.value.toUpperCase(),
                      },
                    })}
                  />
                )}
                <span className="erp-settings-result" aria-live="polite">
                  {!valid ? (
                    <span className="erp-settings-status is-error">Prefijo: 1–6 letras o dígitos.</span>
                  ) : ref ? (
                    <>
                      Siguiente referencia: <span className="mono">{ref}</span>
                      {store.ref_prefix_metadata
                        ? " · fijado en la cuenta"
                        : !typed ? " · derivado del nº de pedido" : ""}
                      {next && !next.next_number_known ? " · aún sin pedidos de esta tienda" : ""}
                    </>
                  ) : (
                    "Siguiente referencia: —"
                  )}
                </span>
              </div>
            );
          })}
          <p className="muted small">
            La app Woo→FACTUSOL escribe en «Su referencia» el prefijo más el nº
            de pedido con seis cifras (<code>FLE-005789</code> para{" "}
            <code>FLUXLA-5789</code>). Vacío = las 3 primeras letras del nº de
            pedido, que pueden no coincidir con las de la app.
          </p>
        </SettingsSection>

        {/* ---------------------------------------------------------------- */}
        <SettingsSection
          {...sectionProps("remitentes")}
          lead="Desde qué dirección sale la factura al cliente: manda el remitente de la tienda del pedido y, si no tiene, el de la empresa emisora (serie)."
          note="El remitente debe ser un «enviar como» verificado de la cuenta de Gmail; si no, el envío falla."
        >
          <h3 className="erp-settings-sub">Por tienda</h3>
          <datalist id="erp-email-aliases">
            {aliases.map((a) => (
              <option key={a.send_as_email} value={a.send_as_email} />
            ))}
          </datalist>
          {stores.map((store) => {
            const own = (cfg.factusol_store_email_from?.[store.slug] ?? "").trim();
            const serie = serieFor(cfg, store.slug, true);
            const serieAlias = serie ? (cfg.factusol_series_email_from?.[serie] ?? "").trim() : "";
            return (
              <div className="erp-settings-row" key={store.slug}>
                <span className="erp-settings-row-name">{store.label}</span>
                <input
                  type="email"
                  list="erp-email-aliases"
                  className="erp-settings-email"
                  placeholder="(remitente de la serie)"
                  aria-label={`Remitente tienda ${store.label}`}
                  value={cfg.factusol_store_email_from?.[store.slug] ?? ""}
                  onChange={(e) => patch({
                    factusol_store_email_from: {
                      ...(cfg.factusol_store_email_from ?? {}),
                      [store.slug]: e.target.value,
                    },
                  })}
                />
                <span className="erp-settings-result" aria-live="polite">
                  {own
                    ? <>Las facturas de {store.label} saldrán de <span className="mono">{own}</span>.</>
                    : serieAlias
                      ? <>Las facturas de {store.label} saldrán de <span className="mono">{serieAlias}</span> (remitente de la serie {serieLabel(cfg, serie)}).</>
                      : <>Las facturas de {store.label} saldrán del alias por defecto de quien envía.</>}
                </span>
              </div>
            );
          })}
          {stores.length === 0 ? (
            <p className="muted small">No hay tiendas WooCommerce dadas de alta.</p>
          ) : null}
          <h3 className="erp-settings-sub">Por serie (empresa emisora)</h3>
          {Array.from(new Set([
            ...Object.keys(cfg.factusol_companies ?? {}),
            ...Object.keys(cfg.factusol_series_email_from ?? {}),
          ]))
            .sort((a, b) => Number(a) - Number(b))
            .map((serie) => {
              const value = (cfg.factusol_series_email_from?.[serie] ?? "").trim();
              return (
                <div className="erp-settings-row" key={serie}>
                  <span className="erp-settings-row-name">Serie {serieLabel(cfg, serie)}</span>
                  <input
                    type="email"
                    list="erp-email-aliases"
                    className="erp-settings-email"
                    placeholder="(alias por defecto del usuario)"
                    aria-label={`Remitente serie ${serie}`}
                    value={cfg.factusol_series_email_from?.[serie] ?? ""}
                    onChange={(e) => patch({
                      factusol_series_email_from: {
                        ...(cfg.factusol_series_email_from ?? {}),
                        [serie]: e.target.value,
                      },
                    })}
                  />
                  <span className="erp-settings-result" aria-live="polite">
                    {value
                      ? <>Las facturas de la serie {serie} saldrán de <span className="mono">{value}</span> (salvo que la tienda tenga el suyo).</>
                      : <>Las facturas de la serie {serie} saldrán del alias por defecto de quien envía.</>}
                  </span>
                </div>
              );
            })}
        </SettingsSection>

        {/* ---------------------------------------------------------------- */}
        <SettingsSection
          {...sectionProps("plantillas")}
          lead="El texto del correo con el que se envía la factura, en el idioma del cliente; los marcadores se sustituyen por los datos reales al enviar."
        >
          <p className="muted small">
            Marcadores: <code>{"{cliente}"}</code>, <code>{"{numero}"}</code> (nº de
            factura), <code>{"{pedido}"}</code> (nº de pedido, con separador; vacío
            sin pedido) y <code>{"{referencia}"}</code> («su ref.»). Vacío = el
            texto por defecto de ese idioma. El ejemplo usa datos de muestra.
          </p>
          {INVOICE_EMAIL_LANGS.map((l) => {
            const tpl = cfg.factusol_invoice_email_templates?.[l.value]
              ?? { subject: "", body: "" };
            return (
              <TemplateEditor
                key={l.value}
                lang={l.value}
                label={l.label}
                tpl={tpl}
                canTest={canEdit}
                onChange={(next) => patch({
                  factusol_invoice_email_templates: {
                    ...(cfg.factusol_invoice_email_templates ?? {}),
                    [l.value]: { ...tpl, ...next },
                  },
                })}
              />
            );
          })}
        </SettingsSection>

        {/* ---------------------------------------------------------------- */}
        <SettingsSection
          {...sectionProps("series")}
          lead="La serie es la empresa que emite la factura en FACTUSOL; aquí se decide a qué serie va cada origen de pedido y con qué estados se marcan allí los documentos."
        >
          <label className="field">
            <span>Serie por defecto</span>
            <input
              type="text" maxLength={10} placeholder="A"
              value={cfg.factusol_series_default ?? ""}
              aria-label="Serie por defecto"
              onChange={(e) => patch({ factusol_series_default: e.target.value })}
            />
            <span className="erp-settings-result" aria-live="polite">
              {cfg.factusol_series_default?.trim()
                ? <>Las facturas nuevas irán a la serie <span className="mono">{serieLabel(cfg, cfg.factusol_series_default)}</span>.</>
                : "Sin serie por defecto: hay que indicarla al emitir."}
            </span>
            <span className="muted small">
              Solo para pedidos que NO están en FACTUSOL. Si el pedido ya
              existe allí, la factura hereda SU serie (empresa emisora).
            </span>
          </label>
          <table className="data-table data-table--responsive erp-settings-table">
            <thead>
              <tr>
                <th>Origen del pedido</th>
                <th>Serie (vacío = por defecto)</th>
                <th>Resultado</th>
              </tr>
            </thead>
            <tbody>
              {ORDER_SOURCES.map((src) => {
                const serie = serieFor(cfg, src.value, false);
                const own = (cfg.factusol_series_by_source?.[src.value] ?? "").trim();
                return (
                  <tr key={src.value}>
                    <td data-label="Origen">{src.label}</td>
                    <td data-label="Serie">
                      <input
                        type="text" maxLength={10}
                        aria-label={`Serie ${src.label}`}
                        value={cfg.factusol_series_by_source?.[src.value] ?? ""}
                        onChange={(e) => patch({
                          factusol_series_by_source: {
                            ...(cfg.factusol_series_by_source ?? {}),
                            [src.value]: e.target.value,
                          },
                        })}
                      />
                    </td>
                    <td data-label="Resultado" className="erp-settings-result">
                      {serie
                        ? <>→ serie <span className="mono">{serieLabel(cfg, serie)}</span>{own ? "" : " (la por defecto)"}</>
                        : "→ sin serie"}
                      {src.value === "manual" && nextRefs?.manual_next
                        ? <> · siguiente nº <span className="mono">{nextRefs.manual_next}</span></>
                        : null}
                    </td>
                  </tr>
                );
              })}
              {/* ERP-F6-fix3 — serie POR TIENDA Woo (no un único WooCommerce).
                  Solo afecta a la EMPRESA prevista del seguimiento; la emisión
                  sigue heredando del pedido en FACTUSOL. */}
              {stores.map((store) => {
                const serie = serieFor(cfg, store.slug, true);
                const own = (cfg.factusol_series_by_source?.[store.slug] ?? "").trim();
                return (
                  <tr key={store.slug}>
                    <td data-label="Origen">WooCommerce · {store.label}</td>
                    <td data-label="Serie">
                      <input
                        type="text" maxLength={10}
                        aria-label={`Serie tienda ${store.label}`}
                        placeholder="hereda de WooCommerce"
                        value={cfg.factusol_series_by_source?.[store.slug] ?? ""}
                        onChange={(e) => patch({
                          factusol_series_by_source: {
                            ...(cfg.factusol_series_by_source ?? {}),
                            [store.slug]: e.target.value,
                          },
                        })}
                      />
                    </td>
                    <td data-label="Resultado" className="erp-settings-result">
                      {serie
                        ? <>→ serie <span className="mono">{serieLabel(cfg, serie)}</span>{own ? "" : " (heredada)"}</>
                        : "→ sin serie"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="muted small">
            La serie por tienda solo decide la EMPRESA prevista del seguimiento
            mientras no haya factura. Al emitir, la serie la manda siempre el
            pedido en FACTUSOL.
          </p>
          <h3 className="erp-settings-sub">Estados que BoHub escribe en FACTUSOL</h3>
          <label className="field">
            <span>Estado ESTPCL del pedido facturado</span>
            <input
              type="text" maxLength={10} placeholder="2"
              value={cfg.factusol_estpcl_invoiced ?? ""}
              aria-label="Estado ESTPCL del pedido facturado"
              onChange={(e) => patch({ factusol_estpcl_invoiced: e.target.value })}
            />
            <span className="muted small">
              Valor que FACTUSOL usa para marcar el pedido como facturado
              («Enviado»). Por defecto 2. Vacío → el pedido no se marca.
            </span>
          </label>
          <label className="field">
            <span>Estado ESTPRE del presupuesto convertido</span>
            <input
              type="text" maxLength={10} placeholder="1"
              value={cfg.factusol_estpre_accepted ?? ""}
              aria-label="Estado ESTPRE del presupuesto convertido"
              onChange={(e) => patch({ factusol_estpre_accepted: e.target.value })}
            />
            <span className="muted small">
              Al crear un albarán o factura desde un presupuesto, BoHub lo
              marca como «Aceptado» — igual que hace FACTUSOL escritorio al
              convertir. Confirmado: 1. Vacío → no se marca.
            </span>
          </label>
          <label className="field">
            <span>Estado ESTALB del albarán facturado</span>
            <input
              type="text" maxLength={10} placeholder="1"
              value={cfg.factusol_estalb_invoiced ?? ""}
              aria-label="Estado ESTALB del albarán facturado"
              onChange={(e) => patch({ factusol_estalb_invoiced: e.target.value })}
            />
            <span className="muted small">
              Al crear la factura desde un albarán, BoHub lo marca como
              «Facturado» (columna FACT. del escritorio). Confirmado: 1.
              Vacío → no se marca.
            </span>
          </label>
          {/* ERP-F3 — estado de COBRO de las facturas + auto-marcado. */}
          <label className="field">
            <span>Estado ESTFAC de factura cobrada</span>
            <input
              type="text" maxLength={10} placeholder="2"
              value={cfg.factusol_estfac_cobrada ?? ""}
              aria-label="Estado ESTFAC de factura cobrada"
              onChange={(e) => patch({ factusol_estfac_cobrada: e.target.value })}
            />
            <span className="muted small">
              Valor de ESTFAC al marcar una factura como cobrada. Confirmado: 2.
              Vacío → desactiva el marcado de cobro.
            </span>
          </label>
          <label className="field">
            <span>Estado ESTFAC de factura pendiente</span>
            <input
              type="text" maxLength={10} placeholder="0"
              value={cfg.factusol_estfac_pendiente ?? ""}
              aria-label="Estado ESTFAC de factura pendiente"
              onChange={(e) => patch({ factusol_estfac_pendiente: e.target.value })}
            />
            <span className="muted small">
              Valor de ESTFAC al marcar «pendiente de cobro». Confirmado: 0.
            </span>
          </label>
          <label className="field erp-check-field">
            <input
              type="checkbox"
              checked={cfg.factusol_auto_mark_paid_when_order_paid ?? false}
              aria-label="Marcar la factura como cobrada al emitirla si el pedido ya estaba pagado"
              onChange={(e) => patch({ factusol_auto_mark_paid_when_order_paid: e.target.checked })}
            />
            <span>
              Marcar la factura como cobrada al emitirla si el pedido ya estaba
              pagado
            </span>
            <span className="muted small">
              Solo para pedidos que constan pagados en el CRM (web con pago al
              comprar); nunca para manuales o pendientes. Desactivado por
              defecto: es una afirmación contable automática.
            </span>
          </label>
        </SettingsSection>

        {/* ERP-F6-fix3 — abreviaturas de empresa por serie (columna Empresa). */}
        <SettingsSection
          {...sectionProps("abreviaturas")}
          lead="La forma corta de la empresa que se escribe en la columna «Empresa» del seguimiento (BO, MQ, ST…)."
        >
          {Object.entries(cfg.factusol_series_abbreviations ?? {})
            .sort(([a], [b]) => Number(a) - Number(b))
            .map(([serie, abbr]) => (
              <div className="erp-settings-row" key={serie}>
                <span className="erp-settings-row-name">Serie {serieLabel(cfg, serie)}</span>
                <input
                  type="text" maxLength={10} className="erp-settings-short"
                  aria-label={`Abreviatura serie ${serie}`}
                  value={abbr}
                  onChange={(e) => patch({
                    factusol_series_abbreviations: {
                      ...(cfg.factusol_series_abbreviations ?? {}),
                      [serie]: e.target.value,
                    },
                  })}
                />
                <span className="erp-settings-result">
                  {abbr.trim()
                    ? <>En el seguimiento, la serie {serie} se escribe <span className="mono">{abbr.trim()}</span>.</>
                    : `Vacío: la serie ${serie} se quita de la lista al guardar.`}
                </span>
              </div>
            ))}
          <AddAbbreviation
            onAdd={(serie, abbr) => patch({
              factusol_series_abbreviations: {
                ...(cfg.factusol_series_abbreviations ?? {}),
                [serie]: abbr,
              },
            })}
          />
        </SettingsSection>

        {/* ERP — destinatario por defecto del envío del PEDIDO por email. */}
        <SettingsSection
          {...sectionProps("sat")}
          lead="El destinatario que viene precargado en «Enviar por email» desde un pedido (con el albarán adjunto); se puede cambiar al enviar."
        >
          <div className="erp-settings-row">
            <input
              type="email"
              className="erp-settings-email"
              placeholder="taller@bomedia.net"
              aria-label="Email del SAT"
              value={cfg.sat_email ?? ""}
              onChange={(e) => patch({ sat_email: e.target.value })}
            />
            <span className="erp-settings-result" aria-live="polite">
              {cfg.sat_email?.trim()
                ? <>«Enviar por email» saldrá precargado a <span className="mono">{cfg.sat_email.trim()}</span>.</>
                : "Sin destinatario precargado: se escribe al enviar."}
            </span>
          </div>
        </SettingsSection>

        {/* ---------------------------------------------------------------- */}
        <SettingsSection
          {...sectionProps("empresas")}
          lead="La identidad fiscal que imprimen los PDF de presupuestos, pedidos, albaranes y facturas según la serie del documento; los textos legales van por idioma."
          note="Los valores iniciales salen de los modelos reales de FACTUSOL. El logo se sube al elegirlo, sin pasar por «Guardar cambios»."
        >
          {Object.entries(cfg.factusol_companies ?? {})
            .sort(([a], [b]) => Number(a) - Number(b))
            .map(([serie, comp]) => (
              <details key={serie} className="erp-company-block">
                <summary>
                  Serie {serie} — {comp.nombre || "(sin nombre)"}
                  {comp.logo ? " · logo ✓" : " · sin logo"}
                </summary>
                {COMPANY_FIELDS.map((f) => (
                  <label className="field" key={f.key}>
                    <span>{f.label}</span>
                    <input
                      type="text"
                      value={String(comp[f.key] ?? "")}
                      aria-label={`${f.label} (serie ${serie})`}
                      onChange={(e) => setCompany(serie, { ...comp, [f.key]: e.target.value })}
                    />
                  </label>
                ))}
                {COMPANY_TEXTS.map((t) => (
                  <label className="field" key={`${t.field}-${t.lang}`}>
                    <span>{t.label}</span>
                    <textarea
                      rows={2}
                      value={comp[t.field]?.[t.lang] ?? ""}
                      aria-label={`${t.label} (serie ${serie})`}
                      onChange={(e) => setCompany(serie, {
                        ...comp,
                        [t.field]: { ...(comp[t.field] ?? {}), [t.lang]: e.target.value },
                      })}
                    />
                  </label>
                ))}

                {/* E4-fix1 — cuentas bancarias (N por empresa, una por
                    defecto). El operador elige la cuenta al descargar. */}
                <fieldset className="erp-bank-fieldset">
                  <legend>Cuentas bancarias</legend>
                  {(comp.bancos ?? []).map((cuenta, i) => (
                    <div className="erp-bank-row" key={i}>
                      {(["nombre", "domicilio", "iban", "bic"] as const).map((bk) => (
                        <input
                          key={bk}
                          type="text"
                          placeholder={bk}
                          aria-label={`Banco ${i + 1} ${bk} (serie ${serie})`}
                          value={cuenta[bk] ?? ""}
                          onChange={(e) => {
                            const bancos = [...(comp.bancos ?? [])];
                            bancos[i] = { ...bancos[i], [bk]: e.target.value };
                            setCompany(serie, { ...comp, bancos });
                          }}
                        />
                      ))}
                      <label className="erp-bank-default">
                        <input
                          type="radio"
                          name={`bank-default-${serie}`}
                          aria-label={`Cuenta por defecto ${i + 1} (serie ${serie})`}
                          checked={!!cuenta.defecto}
                          onChange={() => {
                            const bancos = (comp.bancos ?? []).map((b, j) => ({
                              ...b, defecto: j === i,
                            }));
                            setCompany(serie, { ...comp, bancos });
                          }}
                        />
                        Por defecto
                      </label>
                      <button
                        type="button"
                        className="button small secondary"
                        onClick={() => setCompany(serie, {
                          ...comp, bancos: (comp.bancos ?? []).filter((_, j) => j !== i),
                        })}
                      >
                        Quitar
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    className="button small secondary"
                    onClick={() => setCompany(serie, {
                      ...comp,
                      bancos: [...(comp.bancos ?? []), {
                        nombre: "", domicilio: "", iban: "", bic: "",
                        defecto: (comp.bancos ?? []).length === 0,
                      }],
                    })}
                  >
                    + Añadir cuenta
                  </button>
                </fieldset>
                <label className="field">
                  <span>Logo (PNG/JPG, se sube al elegirlo)</span>
                  {/* ERP-F3 — miniatura del logo actual + nombre + quitar. El
                      logo NO pasa por el PATCH: se actualiza también en `saved`
                      para que la sección no aparezca como «sin guardar». */}
                  <CompanyLogoThumbnail
                    serie={serie}
                    hasLogo={!!comp.logo}
                    filename={comp.logo_filename}
                    refreshToken={logoRefresh}
                    onRemove={async () => {
                      setError(null);
                      try {
                        await deleteFactusolCompanyLogo(serie);
                        const withLogo = (c: ErpSettings | null) => (c ? {
                          ...c,
                          factusol_companies: {
                            ...(c.factusol_companies ?? {}),
                            [serie]: {
                              ...(c.factusol_companies?.[serie] ?? comp),
                              logo: false, logo_filename: null,
                            },
                          },
                        } : c);
                        setDraft(withLogo);
                        setSaved(withLogo);
                        setLogoRefresh((n) => n + 1);
                      } catch (err) {
                        setError(extractErrorMessage(err, "No se pudo quitar el logo."));
                      }
                    }}
                  />
                  <input
                    type="file"
                    accept="image/png,image/jpeg"
                    aria-label={`Logo serie ${serie}`}
                    onChange={async (e) => {
                      const file = e.target.files?.[0];
                      if (!file) return;
                      setError(null);
                      try {
                        await uploadFactusolCompanyLogo(serie, file);
                        const withLogo = (c: ErpSettings | null) => (c ? {
                          ...c,
                          factusol_companies: {
                            ...(c.factusol_companies ?? {}),
                            [serie]: {
                              ...(c.factusol_companies?.[serie] ?? comp),
                              logo: true, logo_filename: file.name,
                            },
                          },
                        } : c);
                        setDraft(withLogo);
                        setSaved(withLogo);
                        setLogoRefresh((n) => n + 1);
                      } catch (err) {
                        setError(extractErrorMessage(err, "No se pudo subir el logo."));
                      }
                    }}
                  />
                </label>
              </details>
            ))}
        </SettingsSection>

        {/* E4-fix1 — almacenes de recogida del albarán de devolución. */}
        <SettingsSection
          {...sectionProps("almacenes")}
          lead="La dirección de recogida que imprime el albarán de devolución; puedes tener varias y elegir al generar el PDF."
        >
          {(cfg.factusol_pickup_warehouses ?? []).map((w, i) => (
            <div className="erp-bank-row" key={i}>
              <input
                type="text"
                placeholder="Nombre"
                aria-label={`Almacén ${i + 1} nombre`}
                value={w.nombre ?? ""}
                onChange={(e) => {
                  const list = [...(cfg.factusol_pickup_warehouses ?? [])];
                  list[i] = { ...list[i], nombre: e.target.value };
                  patch({ factusol_pickup_warehouses: list });
                }}
              />
              <textarea
                rows={2}
                placeholder="Dirección (varias líneas)"
                aria-label={`Almacén ${i + 1} dirección`}
                value={w.direccion ?? ""}
                onChange={(e) => {
                  const list = [...(cfg.factusol_pickup_warehouses ?? [])];
                  list[i] = { ...list[i], direccion: e.target.value };
                  patch({ factusol_pickup_warehouses: list });
                }}
              />
              <button
                type="button"
                className="button small secondary"
                onClick={() => patch({
                  factusol_pickup_warehouses:
                    (cfg.factusol_pickup_warehouses ?? []).filter((_, j) => j !== i),
                })}
              >
                Quitar
              </button>
            </div>
          ))}
          <button
            type="button"
            className="button small secondary"
            onClick={() => patch({
              factusol_pickup_warehouses: [
                ...(cfg.factusol_pickup_warehouses ?? []),
                { nombre: "", direccion: "" },
              ],
            })}
          >
            + Añadir almacén
          </button>
        </SettingsSection>

        {/* ERP-F5 — contrapartidas de cobro (destino del dinero en FACTUSOL). */}
        <SettingsSection
          {...sectionProps("contrapartidas")}
          lead="A qué cuenta entra el dinero al registrar un cobro en FACTUSOL (el «Apunte de cobro»); no es la forma de pago."
        >
          <p className="muted small">
            FACTUSOL no expone esta tabla, así que el catálogo vive aquí: código
            → descripción. Cada cuenta bancaria de la conciliación se enlaza
            con una de ellas.
          </p>
          {(cfg.contrapartidas ?? []).map((c, i) => (
            <div className="erp-bank-row" key={i}>
              <input
                type="text" inputMode="numeric" placeholder="Código"
                aria-label={`Contrapartida ${i + 1} código`}
                value={c.codigo}
                className="erp-settings-short mono"
                onChange={(e) => {
                  const list = [...(cfg.contrapartidas ?? [])];
                  list[i] = { ...list[i], codigo: e.target.value };
                  patch({ contrapartidas: list });
                }}
              />
              <input
                type="text" placeholder="Descripción (p. ej. Bomedia Sabadell)"
                aria-label={`Contrapartida ${i + 1} descripción`}
                value={c.nombre}
                onChange={(e) => {
                  const list = [...(cfg.contrapartidas ?? [])];
                  list[i] = { ...list[i], nombre: e.target.value };
                  patch({ contrapartidas: list });
                }}
              />
              <button
                type="button"
                className="button small secondary"
                onClick={() => patch({
                  contrapartidas: (cfg.contrapartidas ?? []).filter((_, j) => j !== i),
                })}
              >
                Quitar
              </button>
            </div>
          ))}
          <button
            type="button"
            className="button small secondary"
            onClick={() => patch({
              contrapartidas: [...(cfg.contrapartidas ?? []), { codigo: "", nombre: "" }],
            })}
          >
            + Añadir contrapartida
          </button>

          <h3 className="erp-settings-sub">PayPal por tienda</h3>
          <p className="muted small">
            La contrapartida de un cobro PayPal no viene de ningún extracto: se
            deduce de la tienda del pedido.
          </p>
          <table className="data-table data-table--responsive erp-settings-table">
            <thead>
              <tr><th>Tienda</th><th>Contrapartida</th><th>Resultado</th></tr>
            </thead>
            <tbody>
              {PAYPAL_STORES.map((s) => {
                const code = cfg.paypal_contrapartidas_by_store?.[s.key] ?? "";
                const match = (cfg.contrapartidas ?? []).find((c) => c.codigo === code);
                return (
                  <tr key={s.key}>
                    <td data-label="Tienda">{s.label}</td>
                    <td data-label="Contrapartida">
                      <select
                        aria-label={`Contrapartida PayPal ${s.label}`}
                        value={code}
                        onChange={(e) => patch({
                          paypal_contrapartidas_by_store: {
                            ...(cfg.paypal_contrapartidas_by_store ?? {}),
                            [s.key]: e.target.value,
                          },
                        })}
                      >
                        <option value="">—</option>
                        {(cfg.contrapartidas ?? []).map((c, i) => (
                          <option key={`${c.codigo}-${i}`} value={c.codigo}>
                            {c.codigo} · {c.nombre}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td data-label="Resultado" className="erp-settings-result">
                      {code
                        ? <>Los cobros PayPal de {s.label} entran en <span className="mono">{code}</span>{match ? ` (${match.nombre})` : ""}.</>
                        : `Sin contrapartida: los cobros PayPal de ${s.label} piden elegirla a mano.`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </SettingsSection>

        {/* ERP-F6 — orígenes del envío (OFI-TER-SAT del Excel de seguimiento). */}
        <SettingsSection
          {...sectionProps("origenes")}
          lead="Desde dónde sale la mercancía (la columna OFI-TER-SAT del Excel de seguimiento); se ofrecen en la ficha del pedido."
        >
          {(cfg.shipping_origins ?? []).map((o, i) => (
            <div className="erp-bank-row" key={i}>
              <input
                type="text"
                aria-label={`Origen del envío ${i + 1}`}
                value={o}
                onChange={(e) => {
                  const list = [...(cfg.shipping_origins ?? [])];
                  list[i] = e.target.value;
                  patch({ shipping_origins: list });
                }}
              />
              <button
                type="button" className="button small secondary"
                onClick={() => patch({
                  shipping_origins: (cfg.shipping_origins ?? []).filter((_, j) => j !== i),
                })}
              >
                Quitar
              </button>
            </div>
          ))}
          <button
            type="button" className="button small secondary"
            onClick={() => patch({ shipping_origins: [...(cfg.shipping_origins ?? []), ""] })}
          >
            + Añadir origen
          </button>
        </SettingsSection>

        {/* ERP-F6 — hoja de seguimiento en Drive (cuenta de SERVICIO, no el
            OAuth de Gmail: sus tokens caducan cada 7 días). */}
        <SettingsSection
          {...sectionProps("drive")}
          lead="La hoja de Google Sheets donde BoHub escribe el seguimiento de envíos, con una cuenta de servicio propia."
        >
          <p className="erp-settings-result">
            {cfg.drive_configured
              ? <>Configurada. Comparte la hoja con <span className="mono">{cfg.drive_service_account_email ?? "la cuenta de servicio"}</span> (como editor).</>
              : "Pega el JSON de una cuenta de servicio de Google y el ID de la hoja. Después comparte la hoja con el email de esa cuenta (como editor)."}
          </p>
          <label className="field">
            <span>ID de la hoja (de su URL de Drive)</span>
            <input
              type="text"
              aria-label="ID de la hoja de Drive"
              placeholder="1AbCdEfGhIjKlMnOpQrStUvWxYz…"
              value={cfg.drive_spreadsheet_id ?? ""}
              onChange={(e) => patch({ drive_spreadsheet_id: e.target.value || null })}
            />
          </label>
          <label className="field">
            <span>
              Credenciales de la cuenta de servicio (JSON)
              {cfg.drive_configured ? " — ya guardadas; pega otras para sustituirlas" : ""}
            </span>
            <textarea
              rows={3}
              aria-label="JSON de la cuenta de servicio"
              placeholder='{"type": "service_account", "client_email": "…", "private_key": "…"}'
              value={cfg.drive_service_account_json ?? ""}
              onChange={(e) => patch({ drive_service_account_json: e.target.value })}
            />
            <span className="muted small">
              Se guardan cifradas y no vuelven a mostrarse. Deja el campo vacío
              para no cambiarlas.
            </span>
          </label>
          {/* ERP-F6-fix2 — qué número escribir en «Albarán / Núm Pedido WEb». */}
          <label className="field erp-check-field">
            <input
              type="checkbox"
              aria-label="Preferir el número de albarán en la columna de referencia"
              checked={cfg.drive_reference_prefer_albaran ?? true}
              onChange={(e) => patch({ drive_reference_prefer_albaran: e.target.checked })}
            />
            <span>Escribir el nº de albarán cuando exista (si no, el de pedido web)</span>
            <span className="muted small">
              La columna «Albarán / Núm Pedido WEb» de Bart usa el número de
              albarán en sus filas antiguas. Siempre se escribe el número
              desnudo, nunca la referencia con prefijo.
            </span>
          </label>
        </SettingsSection>
      </div>
    </main>
  );
}

/** Lote 2 · PR-2 — una sección de ajustes: título, una frase de explicación,
 *  el indicador «sin guardar» y su propio «Guardar cambios» (con el motivo si
 *  no se puede guardar) y la línea de resultado dentro de la sección. */
function SettingsSection({
  id, title, lead, note, dirty, busy, error, saved, canEdit, onSave, children,
}: {
  id: SectionId;
  title: string;
  lead: string;
  /** Consecuencia escrita al lado del botón (opcional). */
  note?: string;
  dirty: boolean;
  busy: boolean;
  error: string | null;
  saved: boolean;
  canEdit: boolean;
  onSave: () => void;
  children: ReactNode;
}) {
  const reason = !canEdit ? "Solo un administrador puede guardar." : undefined;
  return (
    <section className="erp-settings-section" id={`ajuste-${id}`} aria-labelledby={`ajuste-${id}-t`}>
      <header className="erp-settings-head">
        <div className="erp-settings-head-text">
          <h2 className="erp-settings-title" id={`ajuste-${id}-t`}>{title}</h2>
          <p className="erp-settings-lead">{lead}</p>
        </div>
        {dirty ? (
          <span className="erp-settings-dirty" role="status">Cambios sin guardar</span>
        ) : null}
      </header>
      {children}
      <footer className="erp-settings-foot">
        {note ? <span className="erp-settings-foot-note">{note}</span> : null}
        {error ? (
          <span className="erp-settings-status is-error" role="alert">{error}</span>
        ) : saved && !dirty ? (
          <span className="erp-settings-status is-ok" role="status">Guardado.</span>
        ) : reason ? (
          <span className="erp-settings-status">{reason}</span>
        ) : null}
        <button
          type="button"
          className="button"
          aria-label={`Guardar cambios · ${title}`}
          title={reason}
          disabled={!canEdit || busy || !dirty}
          onClick={onSave}
        >
          {busy ? "Guardando…" : "Guardar cambios"}
        </button>
      </footer>
    </section>
  );
}

/** Lote 2 · PR-2 — editor de la plantilla de un idioma con el ejemplo en
 *  vivo (datos de muestra, previsualizado por el backend según se escribe),
 *  «Ver ejemplo» (modal con el correo completo) y «Enviarme una prueba». */
function TemplateEditor({
  lang, label, tpl, canTest, onChange,
}: {
  lang: string;
  label: string;
  tpl: { subject: string; body: string };
  /** La prueba sale desde un alias de la organización: solo admin. */
  canTest: boolean;
  onChange: (patch: Partial<{ subject: string; body: string }>) => void;
}) {
  const [example, setExample] = useState<InvoiceEmailTemplatePreview | null>(null);
  const [exampleError, setExampleError] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [testBusy, setTestBusy] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  // Ejemplo en vivo, con retardo para no pedir una previsualización por tecla.
  useEffect(() => {
    let alive = true;
    const timer = setTimeout(() => {
      previewInvoiceEmailTemplate(lang, { subject: tpl.subject, body: tpl.body })
        .then((p) => { if (alive) { setExample(p); setExampleError(null); } })
        .catch((e) => {
          if (alive) setExampleError(extractErrorMessage(e, "No se pudo preparar el ejemplo."));
        });
    }, 350);
    return () => { alive = false; clearTimeout(timer); };
  }, [lang, tpl.subject, tpl.body]);

  async function sendTest() {
    setTestBusy(true);
    setTestResult(null);
    setTestError(null);
    try {
      const r = await sendInvoiceEmailTemplateTest(lang, { subject: tpl.subject, body: tpl.body });
      setTestResult(`Prueba enviada a ${r.to} desde ${r.from_alias} con el asunto «${r.subject}».`);
    } catch (e) {
      setTestError(extractErrorMessage(e, "No se pudo enviar la prueba."));
    } finally {
      setTestBusy(false);
    }
  }

  return (
    <div className="erp-settings-tpl">
      <div className="erp-settings-tpl-head">
        <h3 className="erp-settings-tpl-title">{label}</h3>
        <button
          type="button" className="button small secondary"
          aria-label={`Ver ejemplo ${lang}`}
          onClick={() => setShowModal(true)}
        >
          Ver ejemplo
        </button>
        <button
          type="button" className="button small secondary"
          aria-label={`Enviarme una prueba ${lang}`}
          disabled={!canTest || testBusy}
          title={!canTest ? "Solo un administrador puede enviar pruebas." : undefined}
          onClick={sendTest}
        >
          {testBusy ? "Enviando…" : "Enviarme una prueba"}
        </button>
      </div>
      <input
        type="text"
        aria-label={`Asunto factura ${lang}`}
        placeholder="Asunto"
        value={tpl.subject}
        onChange={(e) => onChange({ subject: e.target.value })}
      />
      <textarea
        aria-label={`Cuerpo factura ${lang}`}
        placeholder="Cuerpo"
        rows={4}
        value={tpl.body}
        onChange={(e) => onChange({ body: e.target.value })}
      />
      <p className="erp-settings-tpl-example" aria-live="polite" data-testid={`ejemplo-${lang}`}>
        {exampleError ? (
          <span className="erp-settings-status is-error">{exampleError}</span>
        ) : example ? (
          <>
            Ejemplo: <strong>{example.subject}</strong> — {firstLines(example.body_text)}
          </>
        ) : (
          "Preparando el ejemplo…"
        )}
      </p>
      {testResult ? (
        <p className="erp-settings-status is-ok" role="status">{testResult}</p>
      ) : null}
      {testError ? (
        <p className="erp-settings-status is-error" role="alert">{testError}</p>
      ) : null}
      {showModal ? (
        <TemplateExampleModal
          lang={lang}
          label={label}
          subject={tpl.subject}
          body={tpl.body}
          onClose={() => setShowModal(false)}
        />
      ) : null}
    </div>
  );
}

/** «Ver ejemplo»: el correo completo (remitente, asunto y cuerpo) con los
 *  datos de muestra, tal como lo enviaría BoHub con lo que hay escrito. */
function TemplateExampleModal({
  lang, label, subject, body, onClose,
}: {
  lang: string;
  label: string;
  subject: string;
  body: string;
  onClose: () => void;
}) {
  const [example, setExample] = useState<InvoiceEmailTemplatePreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    previewInvoiceEmailTemplate(lang, { subject, body })
      .then((p) => { if (alive) setExample(p); })
      .catch((e) => {
        if (alive) setError(extractErrorMessage(e, "No se pudo preparar el ejemplo."));
      });
    return () => { alive = false; };
  }, [lang, subject, body]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label={`Ejemplo del email de factura en ${label}`}>
      <div className="modal-dialog erp-modal">
        <h2>
          Ejemplo del email de factura{" "}
          <span className="muted">{label} · datos de muestra</span>
        </h2>
        {error ? <p className="form-error">{error}</p> : null}
        {!example && !error ? <p className="muted">Preparando…</p> : null}
        {example ? (
          <>
            <dl className="erp-kv-grid">
              <dt>De</dt>
              <dd>
                <span className="mono">{example.from_alias_example || "—"}</span>
                {example.from_alias_example
                  ? ` (${SENDER_SOURCE_LABEL[example.from_alias_source] ?? example.from_alias_source})`
                  : ""}
              </dd>
              <dt>Asunto</dt>
              <dd><strong>{example.subject}</strong></dd>
            </dl>
            <div className="erp-settings-example-body" data-testid="ejemplo-cuerpo">
              {example.body_text}
            </div>
            <p className="muted small">
              Datos de muestra: cliente «{example.sample.cliente}», factura{" "}
              <span className="mono">{example.sample.numero}</span>, pedido{" "}
              <span className="mono">{example.sample.pedido}</span>, referencia{" "}
              <span className="mono">{example.sample.referencia}</span>. Al enviar
              se sustituyen por los del pedido real.
            </p>
          </>
        ) : null}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose}>
            Cerrar
          </button>
        </div>
      </div>
    </div>
  );
}

/** ERP-F6-fix3 — alta de una abreviatura para una serie que aún no la tiene
 *  (p. ej. la 4, Lambert, cuando Bart la confirme). */
function AddAbbreviation({ onAdd }: { onAdd: (serie: string, abbr: string) => void }) {
  const [serie, setSerie] = useState("");
  const [abbr, setAbbr] = useState("");
  return (
    <div className="erp-bank-row">
      <input
        type="text" inputMode="numeric" placeholder="Serie" aria-label="Serie nueva"
        className="erp-settings-short"
        value={serie} onChange={(e) => setSerie(e.target.value)}
      />
      <input
        type="text" maxLength={10} placeholder="Abreviatura" aria-label="Abreviatura nueva"
        value={abbr} onChange={(e) => setAbbr(e.target.value)}
      />
      <button
        type="button" className="button small secondary"
        disabled={!serie.trim() || !abbr.trim()}
        onClick={() => {
          onAdd(serie.trim(), abbr.trim());
          setSerie(""); setAbbr("");
        }}
      >
        + Añadir
      </button>
    </div>
  );
}
