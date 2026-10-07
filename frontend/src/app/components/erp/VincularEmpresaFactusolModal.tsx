"use client";

import { useEffect, useState } from "react";
import type { Company } from "../../lib/companiesApi";
import {
  alreadyLinkedHolder,
  createFactusolCustomerAndLink,
  linkFactusolCustomer,
  searchFactusolCustomers,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { CompanySearch } from "../CompanySearch";
import { ModalCloseButton } from "../ModalCloseButton";
import { useModalBehaviour } from "../useModalBehaviour";

/** Empresa CRM que queda vinculada al cliente FACTUSOL. */
export type LinkedCompany = { id: string; name: string };

type Datos = {
  nombre: string;
  nif: string;
  direccion: string;
  cp: string;
  ciudad: string;
  provincia: string;
  pais: string;
  email: string;
  telefono: string;
};

const VACIO: Datos = {
  nombre: "", nif: "", direccion: "", cp: "", ciudad: "", provincia: "", pais: "",
  email: "", telefono: "",
};

/** «Vincular empresa»: el cliente FACTUSOL (CODCLI) ↔ una empresa del CRM.
 *  Distinto de «Vincular a pedido» (documento ↔ pedido de BoHub).
 *
 *  Se busca y enlaza una empresa que ya exista, o se crea aquí mismo con los
 *  datos del cliente FACTUSOL ya puestos (nombre, CIF, dirección…), en una sola
 *  transacción. El vínculo queda guardado en la empresa: los siguientes
 *  documentos de ese cliente ya no lo piden. No escribe en FACTUSOL. */
export function VincularEmpresaFactusolModal({
  codcli,
  clienteNombre,
  contexto,
  onClose,
  onLinked,
}: {
  codcli: string;
  clienteNombre?: string | null;
  /** Para qué se vincula (p. ej. «Crear el pedido del albarán 2-200038»). */
  contexto?: string;
  onClose: () => void;
  onLinked: (company: LinkedCompany) => void;
}) {
  const [datos, setDatos] = useState<Datos>({ ...VACIO, nombre: clienteNombre ?? "" });
  const [leyendo, setLeyendo] = useState(true);
  const [busy, setBusy] = useState(false);
  const { overlayProps, requestClose } = useModalBehaviour({ onClose, disabled: busy });
  const [error, setError] = useState<string | null>(null);

  // Los datos del cliente en FACTUSOL (F_CLI), para crear la empresa con ellos.
  useEffect(() => {
    let alive = true;
    searchFactusolCustomers(codcli, "codcli")
      .then((hits) => {
        if (!alive) return;
        const c = hits.find((h) => String(h.codcli ?? "") === String(codcli)) ?? hits[0];
        if (!c) return;
        setDatos({
          nombre: c.nombre ?? c.nofcli ?? clienteNombre ?? "",
          nif: c.nif ?? c.nifcli ?? "",
          direccion: c.domcli ?? "",
          cp: c.cpocli ?? "",
          ciudad: c.pobcli ?? "",
          provincia: c.procli ?? "",
          pais: c.pais_iso2 ?? c.paicli ?? "",
          email: c.emacli ?? "",
          telefono: c.telcli?.trim() ?? "",
        });
      })
      .catch(() => { /* sin datos: se puede crear con el nombre del documento */ })
      .finally(() => { if (alive) setLeyendo(false); });
    return () => { alive = false; };
  }, [codcli, clienteNombre]);

  function campo(key: keyof Datos, label: string) {
    return (
      <label className="field">
        <span>{label}</span>
        <input
          // Mientras llega F_CLI no se edita: su respuesta rellenaría encima.
          aria-label={label} value={datos[key]} disabled={busy || leyendo}
          onChange={(e) => setDatos((d) => ({ ...d, [key]: e.target.value }))}
        />
      </label>
    );
  }

  async function crear() {
    if (!datos.nombre.trim()) {
      setError("Pon al menos el nombre de la empresa.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await createFactusolCustomerAndLink({
        factusol_codcli: codcli,
        factusol_customer_data: {
          nombre: datos.nombre.trim(),
          nif: datos.nif.trim(),
          direccion: datos.direccion.trim(),
          ciudad: datos.ciudad.trim(),
          cp: datos.cp.trim(),
          provincia: datos.provincia.trim(),
          telefono: datos.telefono.trim() || undefined,
          email: datos.email.trim() || undefined,
          pais: datos.pais.trim() || undefined,
        },
      });
      onLinked({ id: res.company_id, name: datos.nombre.trim() });
    } catch (e) {
      const holder = alreadyLinkedHolder(e);
      setError(holder
        ? `El cliente FACTUSOL ${codcli} ya está vinculado a «${holder.company_name ?? "otra empresa"}». Recarga la lista.`
        : extractErrorMessage(e, "No se pudo crear la empresa."));
    } finally {
      setBusy(false);
    }
  }

  async function usar(company: Company) {
    const actual = company.factusol_company_id ? String(company.factusol_company_id) : "";
    if (actual && actual !== String(codcli)) {
      setError(`«${company.name}» ya está vinculada al cliente FACTUSOL ${actual}. `
        + "Elige otra empresa o crea una nueva.");
      return;
    }
    if (actual === String(codcli)) {
      onLinked({ id: company.id, name: company.name });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await linkFactusolCustomer({ crm_type: "company", crm_id: company.id, factusol_codcli: codcli });
      onLinked({ id: company.id, name: company.name });
    } catch (e) {
      const holder = alreadyLinkedHolder(e);
      setError(holder
        ? `El cliente FACTUSOL ${codcli} ya está vinculado a «${holder.company_name ?? "otra empresa"}». Recarga la lista.`
        : extractErrorMessage(e, "No se pudo vincular la empresa."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="Vincular empresa" {...overlayProps}>
      <div className="modal-dialog erp-modal">
        <div className="modal-header">
          <h2>Vincular empresa</h2>
          <ModalCloseButton onClose={requestClose} disabled={busy} placement="header" />
        </div>
        <div className="modal-body">
          <p>
            El cliente FACTUSOL <strong>{codcli}</strong>
            {clienteNombre ? <> ({clienteNombre})</> : null} no está vinculado a ninguna
            empresa del CRM.{contexto ? <> Al vincularlo, se sigue con: {contexto}.</> : null}
          </p>
          <p className="muted small">
            El vínculo queda guardado: los siguientes documentos de este cliente ya no
            lo pedirán. No se escribe nada en FACTUSOL.
          </p>

          <section aria-label="Empresa existente">
            <h3>Elegir una empresa que ya existe</h3>
            <CompanySearch
              label="Buscar empresa del CRM"
              initialQuery={clienteNombre ?? ""}
              onPick={(c) => { void usar(c); }}
            />
          </section>

          <section aria-label="Crear empresa">
            <h3>… o crearla con los datos de FACTUSOL</h3>
            {leyendo ? <p className="muted small">Leyendo el cliente en FACTUSOL…</p> : null}
            {campo("nombre", "Nombre")}
            {campo("nif", "CIF / NIF")}
            {campo("direccion", "Dirección")}
            {campo("cp", "CP")}
            {campo("ciudad", "Población")}
            {campo("provincia", "Provincia")}
            {campo("pais", "País (código)")}
            {datos.email ? (
              <p className="muted small">Email en FACTUSOL: {datos.email}</p>
            ) : null}
            <button type="button" className="button" disabled={busy || leyendo}
                    onClick={() => void crear()}>
              {busy ? "Guardando…" : "Crear empresa y vincular"}
            </button>
          </section>

          {error ? <p className="form-error" role="alert">{error}</p> : null}
        </div>
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
            Cancelar
          </button>
        </div>
      </div>
    </div>
  );
}
