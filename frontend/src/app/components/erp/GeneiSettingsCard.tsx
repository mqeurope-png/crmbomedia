"use client";

import { useEffect, useMemo, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import { getGeneiConfig, saveGeneiConfig, type GeneiConfig } from "../../lib/geneiApi";

type CourierRow = { country: string; couriers: string };

/** Ajustes de Genei (envíos): credenciales cifradas, origen (almacén SAT),
 *  bulto por defecto y couriers preferidos por país. Usa su endpoint propio
 *  (`GET/PUT /genei/config`), aparte de la config de FACTUSOL. La password no
 *  se muestra: en blanco = se conserva la guardada. */
export function GeneiSettingsCard({ canEdit }: { canEdit: boolean }) {
  const [cfg, setCfg] = useState<GeneiConfig | null>(null);
  const [password, setPassword] = useState("");
  const [rows, setRows] = useState<CourierRow[]>([]);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getGeneiConfig()
      .then((c) => {
        setCfg(c);
        setRows(Object.entries(c.preferred_couriers).map(([country, cs]) => ({
          country, couriers: cs.join(", "),
        })));
      })
      .catch((e) => setError(extractErrorMessage(e, "No se pudo cargar la config de Genei.")));
  }, []);

  const touch = () => { setDirty(true); setSaved(false); };
  const set = <K extends keyof GeneiConfig>(key: K, value: GeneiConfig[K]) => {
    setCfg((c) => (c ? { ...c, [key]: value } : c));
    touch();
  };

  const preferred = useMemo(() => {
    const out: Record<string, string[]> = {};
    for (const r of rows) {
      const country = r.country.trim().toUpperCase();
      const couriers = r.couriers.split(",").map((s) => s.trim()).filter(Boolean);
      if (country && couriers.length) out[country] = couriers;
    }
    return out;
  }, [rows]);

  async function save() {
    if (!cfg) return;
    setBusy(true); setError(null);
    try {
      const next = await saveGeneiConfig({
        username: cfg.username || undefined,
        password: password || undefined,
        base_url: cfg.base_url,
        default_address_id: cfg.default_address_id ?? "",
        preferred_couriers: preferred,
        default_package: cfg.default_package,
        origin: cfg.origin,
        is_warehouse: cfg.is_warehouse,
      });
      setCfg(next);
      setPassword("");
      setDirty(false); setSaved(true);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar la config de Genei."));
    } finally {
      setBusy(false);
    }
  }

  if (!cfg) {
    return (
      <section className="erp-settings-section" id="ajuste-genei" aria-labelledby="ajuste-genei-t">
        <header className="erp-settings-head">
          <div className="erp-settings-head-text">
            <h2 className="erp-settings-title" id="ajuste-genei-t">Envíos (Genei)</h2>
            <p className="erp-settings-lead">Credenciales y preferencias del agregador de envíos.</p>
          </div>
        </header>
        <p className="muted small">{error ?? "Cargando…"}</p>
      </section>
    );
  }

  const pkg = cfg.default_package;
  const num = (k: keyof typeof pkg) => (v: string) =>
    set("default_package", { ...pkg, [k]: Number(v) || 0 });

  return (
    <section className="erp-settings-section" id="ajuste-genei" aria-labelledby="ajuste-genei-t">
      <header className="erp-settings-head">
        <div className="erp-settings-head-text">
          <h2 className="erp-settings-title" id="ajuste-genei-t">Envíos (Genei)</h2>
          <p className="erp-settings-lead">
            Credenciales de la cuenta de Genei (cifradas), almacén de origen,
            bulto por defecto y couriers preferidos por país de destino.
            {cfg.configured ? " Credenciales guardadas." : " Sin credenciales aún."}
          </p>
        </div>
        {dirty ? <span className="erp-settings-dirty" role="status">Cambios sin guardar</span> : null}
      </header>

      <fieldset className="erp-genei-cfg" disabled={!canEdit || busy}>
        <div className="form-row">
          <label className="field">
            <span>Email de Genei</span>
            <input value={cfg.username} aria-label="Email de Genei"
                   onChange={(e) => set("username", e.target.value)} />
          </label>
          <label className="field">
            <span>Password {cfg.configured ? "(en blanco = no cambiar)" : ""}</span>
            <input type="password" value={password} aria-label="Password de Genei"
                   onChange={(e) => { setPassword(e.target.value); touch(); }} />
          </label>
        </div>
        <div className="form-row">
          <label className="field">
            <span>URL base de la API</span>
            <input value={cfg.base_url} aria-label="URL base de Genei"
                   onChange={(e) => set("base_url", e.target.value)} />
          </label>
          <label className="field">
            <span>ID de dirección de origen (Genei)</span>
            <input value={cfg.default_address_id ?? ""} aria-label="ID de dirección de origen"
                   onChange={(e) => set("default_address_id", e.target.value)} />
          </label>
        </div>

        <h3 className="erp-settings-sub">Origen (para comparar tarifas)</h3>
        <div className="form-row">
          <label className="field">
            <span>País (ISO2)</span>
            <input value={cfg.origin.iso_country} aria-label="País de origen"
                   onChange={(e) => set("origin", { ...cfg.origin, iso_country: e.target.value.toUpperCase().slice(0, 2) })} />
          </label>
          <label className="field">
            <span>Código postal</span>
            <input value={cfg.origin.postal_code} aria-label="CP de origen"
                   onChange={(e) => set("origin", { ...cfg.origin, postal_code: e.target.value })} />
          </label>
          <label className="field">
            <span>Población</span>
            <input value={cfg.origin.town} aria-label="Población de origen"
                   onChange={(e) => set("origin", { ...cfg.origin, town: e.target.value })} />
          </label>
        </div>

        <h3 className="erp-settings-sub">Bulto por defecto</h3>
        <div className="form-row">
          <label className="field"><span>Peso (kg)</span>
            <input type="number" min={0} step="0.01" value={pkg.weight} aria-label="Peso por defecto"
                   onChange={(e) => num("weight")(e.target.value)} /></label>
          <label className="field"><span>Alto (cm)</span>
            <input type="number" min={0} value={pkg.height} aria-label="Alto por defecto"
                   onChange={(e) => num("height")(e.target.value)} /></label>
          <label className="field"><span>Ancho (cm)</span>
            <input type="number" min={0} value={pkg.width} aria-label="Ancho por defecto"
                   onChange={(e) => num("width")(e.target.value)} /></label>
          <label className="field"><span>Largo (cm)</span>
            <input type="number" min={0} value={pkg.length} aria-label="Largo por defecto"
                   onChange={(e) => num("length")(e.target.value)} /></label>
        </div>

        <h3 className="erp-settings-sub">Couriers preferidos por país</h3>
        <p className="muted small">
          Por país de destino (ISO2), en orden de preferencia. Se propone el
          preferido factible más barato.
        </p>
        <ul className="erp-genei-couriers" aria-label="Couriers preferidos por país">
          {rows.map((r, i) => (
            <li key={i} className="form-row">
              <label className="field"><span>País</span>
                <input value={r.country} aria-label={`País fila ${i + 1}`}
                       onChange={(e) => {
                         const next = [...rows]; next[i] = { ...r, country: e.target.value.toUpperCase().slice(0, 2) };
                         setRows(next); touch();
                       }} /></label>
              <label className="field erp-genei-couriers-list"><span>Couriers (coma)</span>
                <input value={r.couriers} aria-label={`Couriers fila ${i + 1}`}
                       onChange={(e) => {
                         const next = [...rows]; next[i] = { ...r, couriers: e.target.value };
                         setRows(next); touch();
                       }} /></label>
              <button type="button" className="button small secondary" aria-label={`Quitar fila ${i + 1}`}
                      onClick={() => { setRows(rows.filter((_, j) => j !== i)); touch(); }}>×</button>
            </li>
          ))}
        </ul>
        <button type="button" className="button small secondary"
                onClick={() => { setRows([...rows, { country: "", couriers: "" }]); touch(); }}>
          + País
        </button>
      </fieldset>

      <footer className="erp-settings-foot">
        {error ? (
          <span className="erp-settings-status is-error" role="alert">{error}</span>
        ) : saved && !dirty ? (
          <span className="erp-settings-status is-ok" role="status">Guardado.</span>
        ) : !canEdit ? (
          <span className="erp-settings-status">Solo un administrador puede guardar.</span>
        ) : null}
        <button type="button" className="button" aria-label="Guardar cambios · Envíos (Genei)"
                disabled={!canEdit || busy || !dirty} onClick={() => void save()}>
          {busy ? "Guardando…" : "Guardar cambios"}
        </button>
      </footer>
    </section>
  );
}
