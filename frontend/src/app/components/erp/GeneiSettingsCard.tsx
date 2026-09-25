"use client";

import { useEffect, useMemo, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  getGeneiConfig, saveGeneiConfig, testGeneiConnection,
  type GeneiAuthStatus, type GeneiConfig,
} from "../../lib/geneiApi";

type CourierRow = { country: string; couriers: string };

function fmtDate(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("es-ES", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

/** Estado de la conexión con Genei. La sesión se renueva SOLA con las
 *  credenciales guardadas: la password solo hay que tocarla si Genei la
 *  rechaza de verdad (estado «error»). */
function GeneiAuthLine({ auth }: { auth?: GeneiAuthStatus }) {
  if (!auth || auth.state === "unknown") {
    return (
      <p className="muted small" role="status">
        Conexión: sin comprobar todavía. La sesión se abre y se renueva sola con
        las credenciales guardadas.
      </p>
    );
  }
  if (auth.state === "error") {
    return (
      <p className="form-error small" role="status">
        ⚠ {auth.last_error ?? "Genei ha rechazado las credenciales guardadas."}
        {auth.last_error_at ? ` (${fmtDate(auth.last_error_at)})` : ""}
      </p>
    );
  }
  return (
    <p className="form-success small" role="status">
      ✓ Conectado. La sesión se renueva sola
      {auth.token_valid_until ? ` (la actual vale hasta el ${fmtDate(auth.token_valid_until)})` : ""};
      no hace falta volver a meter la password.
    </p>
  );
}

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
  const [testing, setTesting] = useState(false);
  const [testMsg, setTestMsg] = useState<string | null>(null);

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
        webhook_base_url: cfg.webhook_base_url,
        tracking_poll_enabled: cfg.tracking_poll_enabled,
        // Mínimo 10 min (no martillear a Genei); vacío → 30.
        tracking_poll_minutes: Math.max(10, cfg.tracking_poll_minutes || 30),
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

  async function probar() {
    setTesting(true); setTestMsg(null);
    try {
      const r = await testGeneiConnection();
      setCfg((c) => (c ? { ...c, auth: r.auth } : c));
      setTestMsg(r.ok ? "Conexión correcta." : (r.detail ?? "Genei ha rechazado la conexión."));
    } catch (e) {
      setTestMsg(extractErrorMessage(e, "No se pudo probar la conexión con Genei."));
    } finally {
      setTesting(false);
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
        <div className="erp-genei-auth">
          <GeneiAuthLine auth={cfg.auth} />
          {cfg.configured ? (
            <div className="erp-genei-auth-actions">
              <button type="button" className="button small secondary"
                      disabled={testing || dirty}
                      title={dirty ? "Guarda los cambios antes de probar" : undefined}
                      onClick={() => void probar()}>
                {testing ? "Probando…" : "Probar conexión"}
              </button>
              {testMsg ? <span className="muted small" role="status">{testMsg}</span> : null}
            </div>
          ) : null}
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
        <div className="form-row">
          <label className="field">
            <span>URL pública del backend (webhook de estados)</span>
            <input value={cfg.webhook_base_url ?? ""} aria-label="URL pública del backend para el webhook"
                   placeholder="https://api.bohub.example"
                   onChange={(e) => set("webhook_base_url", e.target.value)} />
          </label>
          <p className="muted small" role="note">
            Genei avisa aquí de cada cambio de estado (recogido/entregado/incidencia).
            {cfg.webhook_configured
              ? " ✓ Webhook operativo (secreto generado y cifrado)."
              : " Al guardar una URL con las credenciales puestas se genera el secreto."}
          </p>
        </div>

        <h3 className="erp-settings-sub">Estado real del envío (transportista)</h3>
        <p className="muted small">
          BoHub lee de Genei los escaneos del propio transportista («Pendiente de
          entrada en red», «En reparto», «Entregado»…) de los envíos en curso y los
          enseña en «Enviados», la ficha y la hoja. El aviso de Genei solo trae su
          estado general, así que se consulta cada cierto tiempo. Solo lee de Genei.
        </p>
        <div className="form-row">
          <label className="field form-check">
            <input type="checkbox" checked={cfg.tracking_poll_enabled ?? true}
                   aria-label="Consultar el tracking del transportista automáticamente"
                   onChange={(e) => set("tracking_poll_enabled", e.target.checked)} />
            <span>Consultar automáticamente</span>
          </label>
          <label className="field">
            <span>Cada (minutos, mínimo 10)</span>
            <input type="number" min={10} step={5} value={cfg.tracking_poll_minutes || ""}
                   aria-label="Minutos entre consultas del tracking"
                   onChange={(e) => set("tracking_poll_minutes", Number(e.target.value) || 0)} />
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
