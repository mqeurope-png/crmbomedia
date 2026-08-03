"use client";

import { useState } from "react";
import type { WooStoreCreate } from "../../lib/erpApi";

/** Formulario para dar de alta una tienda WooCommerce. Al enviar, el
 *  contenedor prueba conexión antes de persistir (o solo persiste si el
 *  usuario acepta el fallo). El campo secret nunca se muestra en UI. */
export function WooStoreForm({
  onSubmit,
  onCancel,
  busy,
}: {
  onSubmit: (data: WooStoreCreate) => void;
  onCancel: () => void;
  busy?: boolean;
}) {
  const [accountId, setAccountId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [ck, setCk] = useState("");
  const [cs, setCs] = useState("");

  const canSubmit = accountId && displayName && baseUrl && ck && cs;

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="Añadir tienda WooCommerce">
      <div className="modal-dialog">
        <h2>Añadir tienda WooCommerce</h2>
        <label className="field">
          <span>Slug de tienda (id interno)</span>
          <input
            type="text" placeholder="boprint" value={accountId}
            aria-label="Slug de tienda"
            onChange={(e) => setAccountId(e.target.value.toLowerCase().trim())}
          />
        </label>
        <label className="field">
          <span>Nombre visible</span>
          <input
            type="text" placeholder="boprint.net" value={displayName}
            aria-label="Nombre visible"
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </label>
        <label className="field">
          <span>URL base</span>
          <input
            type="url" placeholder="https://boprint.net" value={baseUrl}
            aria-label="URL base" onChange={(e) => setBaseUrl(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Consumer Key</span>
          <input
            type="password" value={ck} aria-label="Consumer Key"
            onChange={(e) => setCk(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Consumer Secret</span>
          <input
            type="password" value={cs} aria-label="Consumer Secret"
            onChange={(e) => setCs(e.target.value)}
          />
        </label>
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onCancel} disabled={busy}>
            Cancelar
          </button>
          <button
            type="button" className="button"
            disabled={!canSubmit || busy}
            onClick={() => onSubmit({
              account_id: accountId, display_name: displayName,
              base_url: baseUrl, consumer_key: ck, consumer_secret: cs,
            })}
          >
            {busy ? "Guardando…" : "Guardar tienda"}
          </button>
        </div>
      </div>
    </div>
  );
}
