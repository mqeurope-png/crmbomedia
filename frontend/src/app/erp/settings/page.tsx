"use client";

import { useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { extractErrorMessage } from "../../lib/errors";
import {
  getErpSettings,
  updateErpSettings,
  uploadFactusolCompanyLogo,
  type ErpSettings,
  type FactusolCompany,
} from "../../lib/erpApi";

/** ERP-E4 — campos de texto de la identidad fiscal de cada empresa emisora
 *  (alimentan los PDF; los valores iniciales salen de los modelos reales de
 *  FACTUSOL). */
const COMPANY_FIELDS: { key: keyof FactusolCompany & string; label: string }[] = [
  { key: "nombre", label: "Nombre fiscal" },
  { key: "direccion", label: "Domicilio" },
  { key: "cp_poblacion", label: "CP y población" },
  { key: "pais", label: "País" },
  { key: "telefono", label: "Teléfono" },
  { key: "email", label: "Email" },
  { key: "nif", label: "NIF / VAT (tal como debe imprimirse)" },
  { key: "banco", label: "Banco" },
  { key: "iban", label: "IBAN" },
  { key: "bic", label: "BIC / Swift" },
];

const COMPANY_TEXTS: { field: "legal" | "pie" | "intracom"; lang: string; label: string }[] = [
  { field: "legal", lang: "es", label: "Reserva de dominio / texto legal (ES)" },
  { field: "legal", lang: "en", label: "Reserva de dominio / texto legal (EN)" },
  { field: "intracom", lang: "es", label: "Texto intracomunitario (ES)" },
  { field: "intracom", lang: "en", label: "Texto intracomunitario (EN)" },
  { field: "pie", lang: "es", label: "Pie de condiciones (ES)" },
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
      const next = await updateErpSettings(cfg);
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
            </tbody>
          </table>
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
                <label className="field">
                  <span>Logo (PNG/JPG, se sube al elegirlo)</span>
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
                            [serie]: { ...comp, logo: true },
                          },
                        });
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

        <div>
          <button type="button" className="button" onClick={save} disabled={busy}>
            {busy ? "Guardando…" : "Guardar"}
          </button>
        </div>
      </div>
    </main>
  );
}
