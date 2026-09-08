"use client";

import { useEffect, useState } from "react";
import { downloadFactusolCompanyLogo } from "../../lib/erpApi";

/** ERP-F3 — miniatura del logo actual de una empresa emisora en /erp/settings.
 *  El input de archivo aparece vacío al recargar (comportamiento del
 *  navegador), así que sin esto parecía que no se hubiera guardado. Se
 *  descarga con auth (blob → object URL), no con `<img src>` a pelo. */
export function CompanyLogoThumbnail({
  serie,
  hasLogo,
  filename,
  refreshToken,
  onRemove,
}: {
  serie: number | string;
  hasLogo: boolean;
  filename?: string | null;
  /** Cambia (lo bumpea el padre) tras subir/quitar para forzar la recarga. */
  refreshToken?: number;
  onRemove: () => void;
}) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    if (!hasLogo) {
      setSrc(null);
      return;
    }
    let alive = true;
    let url: string | null = null;
    downloadFactusolCompanyLogo(serie)
      .then((blob) => {
        if (!alive) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch(() => {
        if (alive) setSrc(null);
      });
    return () => {
      alive = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [serie, hasLogo, filename, refreshToken]);

  if (!hasLogo) {
    return <span className="muted small" role="status">Sin logo</span>;
  }
  return (
    <span className="erp-logo-thumb">
      {src ? (
        <img src={src} alt={`Logo de la serie ${serie}`} />
      ) : (
        <span className="muted small">…</span>
      )}
      <span className="muted small">{filename ?? "logo"}</span>
      <button
        type="button"
        className="button small secondary"
        onClick={onRemove}
      >
        Quitar
      </button>
    </span>
  );
}
