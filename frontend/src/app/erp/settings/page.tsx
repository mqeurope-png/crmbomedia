"use client";

import { useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { extractErrorMessage } from "../../lib/errors";
import { getErpSettings, updateErpSettings, type ErpSettings } from "../../lib/erpApi";

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
            Mientras esté desactivado, los pedidos con SKU sin mapear o empresa
            sin vincular a FACTUSOL muestran un aviso pero <strong>se pueden
            aprobar</strong>. Al activarlo, esos avisos pasan a ser bloqueos.
          </span>
        </label>
        <div>
          <button type="button" className="button" onClick={save} disabled={busy}>
            {busy ? "Guardando…" : "Guardar"}
          </button>
        </div>
      </div>
    </main>
  );
}
