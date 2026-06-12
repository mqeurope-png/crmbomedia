"use client";

import { PageHeader } from "../../components/PageHeader";

export default function ComposerBackofficePage() {
  return (
    <>
      <PageHeader
        title="Backoffice"
        eyebrow="Composer"
        description="Gestión del catálogo: marcas, productos, textos, bloques."
      />
      <div className="composer-placeholder">
        <h2>El backoffice llega en Fase 3</h2>
        <p>
          La interfaz CRUD para el catálogo se construye en Fase 3. De
          momento el seed inicial corre desde el script
          <code> scripts/seed_composer_catalog.py</code> y los cambios
          puntuales se hacen por SQL.
        </p>
      </div>
    </>
  );
}
