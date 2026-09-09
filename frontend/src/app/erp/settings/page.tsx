"use client";

import { useEffect, useState } from "react";
import { CompanyLogoThumbnail } from "../../components/erp/CompanyLogoThumbnail";
import { PageHeader } from "../../components/PageHeader";
import { extractErrorMessage } from "../../lib/errors";
import {
  deleteFactusolCompanyLogo,
  getErpSettings,
  updateErpSettings,
  uploadFactusolCompanyLogo,
  type ErpSettings,
  type FactusolCompany,
} from "../../lib/erpApi";

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
const COMPANY_TEXT_KEYS = [
  "nombre", "direccion", "cp_poblacion", "pais", "telefono", "email", "nif",
  "idioma_defecto",
] as const;
type CompanyTextKey = (typeof COMPANY_TEXT_KEYS)[number];

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

export default function ErpSettingsPage() {
  const [cfg, setCfg] = useState<ErpSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  // ERP-F3 — se bumpea tras subir/quitar un logo para refrescar la miniatura.
  const [logoRefresh, setLogoRefresh] = useState(0);

  useEffect(() => {
    getErpSettings()
      .then(setCfg)
      .catch((e) => setError(extractErrorMessage(e, "No se pudo cargar la configuración.")));
  }, []);

  async function save() {
    if (!cfg) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      // ERP-F6: el JSON de la cuenta de servicio es write-only. Vacío = no
      // tocar las credenciales guardadas (no se envía el campo).
      const payload = { ...cfg };
      if (!payload.drive_service_account_json?.trim()) {
        delete payload.drive_service_account_json;
      }
      const next = await updateErpSettings(payload);
      setCfg(next);
      setSaved(true);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar. ¿Eres admin?"));
    } finally {
      setBusy(false);
    }
  }

  if (!cfg) {
    return <main className="shell"><p className="muted">{error ?? "Cargando…"}</p></main>;
  }

  return (
    <main className="shell">
      <PageHeader
        title="Configuración ERP"
        eyebrow="ERP"
        description="Facturación, transportista por defecto y ejercicio FACTUSOL. Solo admin puede guardar."
        crumbs={[{ label: "ERP" }, { label: "Configuración" }]}
      />
      {error ? <p className="form-error">{error}</p> : null}
      {saved ? <p className="form-success">Guardado.</p> : null}
      <div className="erp-settings-form">
        <label className="field">
          <span>Modo de facturación por defecto</span>
          <select
            value={cfg.default_invoice_mode}
            aria-label="Modo de facturación"
            onChange={(e) => setCfg({ ...cfg, default_invoice_mode: e.target.value as ErpSettings["default_invoice_mode"] })}
          >
            <option value="manual">Manual (siempre revisión)</option>
            <option value="auto">Automática al cobrar</option>
            <option value="auto_under_max">Automática bajo importe máximo</option>
          </select>
        </label>
        <label className="field">
          <span>Importe máximo para factura automática (€)</span>
          <input
            type="number" inputMode="decimal"
            value={cfg.auto_invoice_max_amount_eur ?? ""}
            aria-label="Importe máximo factura automática"
            onChange={(e) => setCfg({ ...cfg, auto_invoice_max_amount_eur: e.target.value ? Number(e.target.value) : null })}
          />
        </label>
        <label className="field">
          <span>Ejercicio FACTUSOL por defecto</span>
          <input
            type="text" maxLength={4} placeholder="2026"
            value={cfg.factusol_default_ejercicio ?? ""}
            aria-label="Ejercicio FACTUSOL"
            onChange={(e) => setCfg({ ...cfg, factusol_default_ejercicio: e.target.value || null })}
          />
        </label>
        <label className="field">
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input
              type="checkbox"
              checked={cfg.factusol_live}
              aria-label="FACTUSOL en producción"
              onChange={(e) => setCfg({ ...cfg, factusol_live: e.target.checked })}
            />
            FACTUSOL en producción (Fase C)
          </span>
          <span className="muted small">
            Al activar, la ficha del pedido consulta FACTUSOL en vivo para
            detectar si ya existe factura o albarán (y vincularla sola). No
            añade bloqueos a la Cola PEDIDOS.
          </span>
        </label>

        <fieldset className="erp-series-fieldset">
          <legend>Serie de facturación</legend>
          <label className="field">
            <span>Serie por defecto</span>
            <input
              type="text" maxLength={10} placeholder="A"
              value={cfg.factusol_series_default ?? ""}
              aria-label="Serie por defecto"
              onChange={(e) => setCfg({ ...cfg, factusol_series_default: e.target.value })}
            />
            <span className="muted small">
              Solo para pedidos que NO están en FACTUSOL. Si el pedido ya
              existe allí, la factura hereda SU serie (empresa emisora).
            </span>
          </label>
          <label className="field">
            <span>Estado ESTPCL del pedido facturado</span>
            <input
              type="text" maxLength={10} placeholder="2"
              value={cfg.factusol_estpcl_invoiced ?? ""}
              aria-label="Estado ESTPCL del pedido facturado"
              onChange={(e) => setCfg({
                ...cfg, factusol_estpcl_invoiced: e.target.value,
              })}
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
              onChange={(e) => setCfg({
                ...cfg, factusol_estpre_accepted: e.target.value,
              })}
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
              onChange={(e) => setCfg({
                ...cfg, factusol_estalb_invoiced: e.target.value,
              })}
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
              onChange={(e) => setCfg({
                ...cfg, factusol_estfac_cobrada: e.target.value,
              })}
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
              onChange={(e) => setCfg({
                ...cfg, factusol_estfac_pendiente: e.target.value,
              })}
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
              onChange={(e) => setCfg({
                ...cfg,
                factusol_auto_mark_paid_when_order_paid: e.target.checked,
              })}
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
          <table className="data-table">
            <thead>
              <tr><th>Origen del pedido</th><th>Serie (vacío = por defecto)</th></tr>
            </thead>
            <tbody>
              {ORDER_SOURCES.map((src) => (
                <tr key={src.value}>
                  <td>{src.label}</td>
                  <td>
                    <input
                      type="text" maxLength={10}
                      aria-label={`Serie ${src.label}`}
                      value={cfg.factusol_series_by_source?.[src.value] ?? ""}
                      onChange={(e) => setCfg({
                        ...cfg,
                        factusol_series_by_source: {
                          ...(cfg.factusol_series_by_source ?? {}),
                          [src.value]: e.target.value,
                        },
                      })}
                    />
                  </td>
                </tr>
              ))}
              {/* ERP-F6-fix3 — serie POR TIENDA Woo (no un único WooCommerce).
                  Solo afecta a la EMPRESA prevista del seguimiento; la emisión
                  sigue heredando del pedido en FACTUSOL. */}
              {(cfg.woocommerce_stores ?? []).map((store) => (
                <tr key={store.slug}>
                  <td>WooCommerce · {store.label}</td>
                  <td>
                    <input
                      type="text" maxLength={10}
                      aria-label={`Serie tienda ${store.label}`}
                      placeholder="hereda de WooCommerce"
                      value={cfg.factusol_series_by_source?.[store.slug] ?? ""}
                      onChange={(e) => setCfg({
                        ...cfg,
                        factusol_series_by_source: {
                          ...(cfg.factusol_series_by_source ?? {}),
                          [store.slug]: e.target.value,
                        },
                      })}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">
            La serie por tienda solo decide la EMPRESA prevista del seguimiento
            mientras no haya factura. Al emitir, la serie la manda siempre el
            pedido en FACTUSOL.
          </p>
        </fieldset>

        {/* ERP-F6-fix3 — abreviaturas de empresa por serie (columna Empresa). */}
        <fieldset className="erp-series-fieldset">
          <legend>Abreviaturas de empresa (seguimiento)</legend>
          <p className="muted small">
            La forma corta que se escribe en la columna «Empresa» del
            seguimiento (BO, MQ, ST…). Añade la de una serie nueva escribiendo
            su número y su abreviatura.
          </p>
          {Object.entries(cfg.factusol_series_abbreviations ?? {})
            .sort(([a], [b]) => Number(a) - Number(b))
            .map(([serie, abbr]) => (
              <div className="erp-bank-row" key={serie}>
                <span style={{ flex: "0 0 90px" }}>Serie {serie}</span>
                <input
                  type="text" maxLength={10}
                  aria-label={`Abreviatura serie ${serie}`}
                  value={abbr}
                  onChange={(e) => setCfg({
                    ...cfg,
                    factusol_series_abbreviations: {
                      ...(cfg.factusol_series_abbreviations ?? {}),
                      [serie]: e.target.value,
                    },
                  })}
                />
              </div>
            ))}
          <AddAbbreviation
            onAdd={(serie, abbr) => setCfg({
              ...cfg,
              factusol_series_abbreviations: {
                ...(cfg.factusol_series_abbreviations ?? {}),
                [serie]: abbr,
              },
            })}
          />
        </fieldset>

        {/* ERP — remitente (alias de envío) del email de factura por serie. */}
        <fieldset className="erp-series-fieldset">
          <legend>Remitente del email de factura (por serie)</legend>
          <p className="muted small">
            Desde qué dirección sale la factura por email según la SERIE = empresa
            emisora. Serie 2 (MQ Europe / artisJet) →
            {" "}info@artisjet-printers.eu; serie 5 (Streamtec) →
            {" "}pedidos@streamtec.es. Déjalo vacío para usar el alias por
            defecto del usuario que envía. El alias debe ser un «enviar como»
            válido de la cuenta de Gmail que envía; si no, el envío falla.
          </p>
          {Array.from(new Set([
            ...Object.keys(cfg.factusol_companies ?? {}),
            ...Object.keys(cfg.factusol_series_email_from ?? {}),
          ]))
            .sort((a, b) => Number(a) - Number(b))
            .map((serie) => (
              <div className="erp-bank-row" key={serie}>
                <span style={{ flex: "0 0 90px" }}>Serie {serie}</span>
                <input
                  type="email"
                  style={{ flex: 1 }}
                  placeholder="(alias por defecto del usuario)"
                  aria-label={`Remitente serie ${serie}`}
                  value={cfg.factusol_series_email_from?.[serie] ?? ""}
                  onChange={(e) => setCfg({
                    ...cfg,
                    factusol_series_email_from: {
                      ...(cfg.factusol_series_email_from ?? {}),
                      [serie]: e.target.value,
                    },
                  })}
                />
              </div>
            ))}
        </fieldset>

        <fieldset className="erp-series-fieldset">
          <legend>Empresas emisoras (PDF de documentos)</legend>
          <p className="muted small">
            Identidad fiscal que imprimen los PDF de presupuestos, pedidos,
            albaranes y facturas, según la SERIE del documento. Los valores
            iniciales salen de los modelos reales de FACTUSOL; corrígelos aquí
            sin necesidad de despliegue. Los textos legales van por idioma.
          </p>
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
                      onChange={(e) => setCfg({
                        ...cfg,
                        factusol_companies: {
                          ...(cfg.factusol_companies ?? {}),
                          [serie]: { ...comp, [f.key]: e.target.value },
                        },
                      })}
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
                      onChange={(e) => setCfg({
                        ...cfg,
                        factusol_companies: {
                          ...(cfg.factusol_companies ?? {}),
                          [serie]: {
                            ...comp,
                            [t.field]: {
                              ...(comp[t.field] ?? {}),
                              [t.lang]: e.target.value,
                            },
                          },
                        },
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
                            setCfg({
                              ...cfg,
                              factusol_companies: {
                                ...(cfg.factusol_companies ?? {}),
                                [serie]: { ...comp, bancos },
                              },
                            });
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
                            setCfg({
                              ...cfg,
                              factusol_companies: {
                                ...(cfg.factusol_companies ?? {}),
                                [serie]: { ...comp, bancos },
                              },
                            });
                          }}
                        />
                        Por defecto
                      </label>
                      <button
                        type="button"
                        className="button small secondary"
                        onClick={() => {
                          const bancos = (comp.bancos ?? []).filter(
                            (_, j) => j !== i,
                          );
                          setCfg({
                            ...cfg,
                            factusol_companies: {
                              ...(cfg.factusol_companies ?? {}),
                              [serie]: { ...comp, bancos },
                            },
                          });
                        }}
                      >
                        Quitar
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    className="button small secondary"
                    onClick={() => {
                      const bancos = [...(comp.bancos ?? []), {
                        nombre: "", domicilio: "", iban: "", bic: "",
                        defecto: (comp.bancos ?? []).length === 0,
                      }];
                      setCfg({
                        ...cfg,
                        factusol_companies: {
                          ...(cfg.factusol_companies ?? {}),
                          [serie]: { ...comp, bancos },
                        },
                      });
                    }}
                  >
                    + Añadir cuenta
                  </button>
                </fieldset>
                <label className="field">
                  <span>Logo (PNG/JPG, se sube al elegirlo)</span>
                  {/* ERP-F3 — miniatura del logo actual + nombre + quitar. */}
                  <CompanyLogoThumbnail
                    serie={serie}
                    hasLogo={!!comp.logo}
                    filename={comp.logo_filename}
                    refreshToken={logoRefresh}
                    onRemove={async () => {
                      setError(null);
                      try {
                        await deleteFactusolCompanyLogo(serie);
                        setCfg({
                          ...cfg,
                          factusol_companies: {
                            ...(cfg.factusol_companies ?? {}),
                            [serie]: { ...comp, logo: false, logo_filename: null },
                          },
                        });
                        setLogoRefresh((n) => n + 1);
                      } catch (err) {
                        setError(extractErrorMessage(
                          err, "No se pudo quitar el logo.",
                        ));
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
                        setCfg({
                          ...cfg,
                          factusol_companies: {
                            ...(cfg.factusol_companies ?? {}),
                            [serie]: {
                              ...comp, logo: true, logo_filename: file.name,
                            },
                          },
                        });
                        setLogoRefresh((n) => n + 1);
                      } catch (err) {
                        setError(extractErrorMessage(
                          err, "No se pudo subir el logo.",
                        ));
                      }
                    }}
                  />
                </label>
              </details>
            ))}
        </fieldset>

        {/* E4-fix1 — almacenes de recogida del albarán de devolución. */}
        <fieldset className="erp-series-fieldset">
          <legend>Almacenes de recogida (albarán de devolución)</legend>
          <p className="muted small">
            Dirección de recogida que imprime el albarán de devolución. El
            valor inicial sale del modelo A-321; puedes tener varios.
          </p>
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
                  setCfg({ ...cfg, factusol_pickup_warehouses: list });
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
                  setCfg({ ...cfg, factusol_pickup_warehouses: list });
                }}
              />
              <button
                type="button"
                className="button small secondary"
                onClick={() => setCfg({
                  ...cfg,
                  factusol_pickup_warehouses:
                    (cfg.factusol_pickup_warehouses ?? []).filter(
                      (_, j) => j !== i,
                    ),
                })}
              >
                Quitar
              </button>
            </div>
          ))}
          <button
            type="button"
            className="button small secondary"
            onClick={() => setCfg({
              ...cfg,
              factusol_pickup_warehouses: [
                ...(cfg.factusol_pickup_warehouses ?? []),
                { nombre: "", direccion: "" },
              ],
            })}
          >
            + Añadir almacén
          </button>
        </fieldset>

        {/* ERP-F5 — contrapartidas de cobro (destino del dinero en FACTUSOL). */}
        <fieldset className="erp-series-fieldset">
          <legend>Contrapartidas de cobro (FACTUSOL)</legend>
          <p className="muted small">
            Destino donde entra el dinero al registrar un cobro (el «Apunte de
            cobro» de FACTUSOL). No es la forma de pago. FACTUSOL no expone
            esta tabla, así que el catálogo vive aquí: código → descripción.
            Cada cuenta bancaria de la conciliación se enlaza con una de ellas.
          </p>
          {(cfg.contrapartidas ?? []).map((c, i) => (
            <div className="erp-bank-row" key={i}>
              <input
                type="text" inputMode="numeric" placeholder="Código"
                aria-label={`Contrapartida ${i + 1} código`}
                value={c.codigo}
                style={{ flex: "0 0 90px", minWidth: 70 }}
                onChange={(e) => {
                  const list = [...(cfg.contrapartidas ?? [])];
                  list[i] = { ...list[i], codigo: e.target.value };
                  setCfg({ ...cfg, contrapartidas: list });
                }}
              />
              <input
                type="text" placeholder="Descripción (p. ej. Bomedia Sabadell)"
                aria-label={`Contrapartida ${i + 1} descripción`}
                value={c.nombre}
                onChange={(e) => {
                  const list = [...(cfg.contrapartidas ?? [])];
                  list[i] = { ...list[i], nombre: e.target.value };
                  setCfg({ ...cfg, contrapartidas: list });
                }}
              />
              <button
                type="button"
                className="button small secondary"
                onClick={() => setCfg({
                  ...cfg,
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
            onClick={() => setCfg({
              ...cfg,
              contrapartidas: [...(cfg.contrapartidas ?? []), { codigo: "", nombre: "" }],
            })}
          >
            + Añadir contrapartida
          </button>

          <h4>PayPal por tienda</h4>
          <p className="muted small">
            La contrapartida de un cobro PayPal no viene de ningún extracto: se
            deduce de la tienda del pedido.
          </p>
          <table className="data-table">
            <thead>
              <tr><th>Tienda</th><th>Contrapartida</th></tr>
            </thead>
            <tbody>
              {PAYPAL_STORES.map((s) => (
                <tr key={s.key}>
                  <td>{s.label}</td>
                  <td>
                    <select
                      aria-label={`Contrapartida PayPal ${s.label}`}
                      value={cfg.paypal_contrapartidas_by_store?.[s.key] ?? ""}
                      onChange={(e) => setCfg({
                        ...cfg,
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
                </tr>
              ))}
            </tbody>
          </table>
        </fieldset>

        {/* ERP-F6 — orígenes del envío (OFI-TER-SAT del Excel de seguimiento). */}
        <fieldset className="erp-series-fieldset">
          <legend>Orígenes del envío (seguimiento)</legend>
          <p className="muted small">
            La columna OFI-TER-SAT del Excel: desde dónde sale la mercancía.
            Añade los que uses; se ofrecen en la ficha del pedido.
          </p>
          {(cfg.shipping_origins ?? []).map((o, i) => (
            <div className="erp-bank-row" key={i}>
              <input
                type="text"
                aria-label={`Origen del envío ${i + 1}`}
                value={o}
                onChange={(e) => {
                  const list = [...(cfg.shipping_origins ?? [])];
                  list[i] = e.target.value;
                  setCfg({ ...cfg, shipping_origins: list });
                }}
              />
              <button
                type="button" className="button small secondary"
                onClick={() => setCfg({
                  ...cfg,
                  shipping_origins: (cfg.shipping_origins ?? []).filter((_, j) => j !== i),
                })}
              >
                Quitar
              </button>
            </div>
          ))}
          <button
            type="button" className="button small secondary"
            onClick={() => setCfg({
              ...cfg, shipping_origins: [...(cfg.shipping_origins ?? []), ""],
            })}
          >
            + Añadir origen
          </button>
        </fieldset>

        {/* ERP-F6 — hoja de seguimiento en Drive (cuenta de SERVICIO, no el
            OAuth de Gmail: sus tokens caducan cada 7 días). */}
        <fieldset className="erp-series-fieldset">
          <legend>Hoja de seguimiento en Drive</legend>
          <p className="muted small">
            {cfg.drive_configured
              ? `Configurada. Comparte la hoja con ${cfg.drive_service_account_email ?? "la cuenta de servicio"} (como editor).`
              : "Pega el JSON de una cuenta de servicio de Google y el ID de la hoja. Después comparte la hoja con el email de esa cuenta (como editor)."}
          </p>
          <label className="field">
            <span>ID de la hoja (de su URL de Drive)</span>
            <input
              type="text"
              aria-label="ID de la hoja de Drive"
              placeholder="1AbCdEfGhIjKlMnOpQrStUvWxYz…"
              value={cfg.drive_spreadsheet_id ?? ""}
              onChange={(e) => setCfg({ ...cfg, drive_spreadsheet_id: e.target.value || null })}
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
              onChange={(e) => setCfg({ ...cfg, drive_service_account_json: e.target.value })}
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
              onChange={(e) => setCfg({
                ...cfg, drive_reference_prefer_albaran: e.target.checked,
              })}
            />
            <span>Escribir el nº de albarán cuando exista (si no, el de pedido web)</span>
            <span className="muted small">
              La columna «Albarán / Núm Pedido WEb» de Bart usa el número de
              albarán en sus filas antiguas. Siempre se escribe el número
              desnudo, nunca la referencia con prefijo.
            </span>
          </label>
        </fieldset>

        <div>
          <button type="button" className="button" onClick={save} disabled={busy}>
            {busy ? "Guardando…" : "Guardar"}
          </button>
        </div>
      </div>
    </main>
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
        style={{ flex: "0 0 90px" }}
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
