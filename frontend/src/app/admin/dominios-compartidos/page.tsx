"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import { apiFetch } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";

/** Administración → Dominios compartidos (1/11/2026). La lista de dominios
 *  de correo que NUNCA vinculan un contacto a una empresa: los proveedores de
 *  correo (gmail.com) y los que comparten muchas organizaciones (xtec.cat, las
 *  consejerías de educación). Es un dato: añadir uno no requiere tocar código.
 *  Añadirlo no despega a quien ya está pegado: eso lo hace el script del
 *  despegue, con su prueba antes. */

export type DominioCompartido = {
  dominio: string;
  tipo: "correo" | "compartido";
  nota: string | null;
  por: string | null;
  at: string | null;
  contactos: number | null;
};

const TIPOS: Record<DominioCompartido["tipo"], string> = {
  compartido: "Compartido (organización)",
  correo: "Proveedor de correo",
};

const NF = new Intl.NumberFormat("es-ES");

const leer = () => apiFetch<DominioCompartido[]>("/api/admin/dominios-compartidos");

export default function DominiosCompartidosPage() {
  const [filas, setFilas] = useState<DominioCompartido[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dominio, setDominio] = useState("");
  const [tipo, setTipo] = useState<DominioCompartido["tipo"]>("compartido");
  const [nota, setNota] = useState("");
  const [guardando, setGuardando] = useState(false);
  const [verCorreo, setVerCorreo] = useState(false);

  const cargar = useCallback(async () => {
    try {
      setFilas(await leer());
      setError(null);
    } catch (e) {
      setError(extractErrorMessage(e, "No se ha podido cargar la lista."));
    }
  }, []);

  useEffect(() => {
    leer()
      .then((f) => setFilas(f))
      .catch((e) => setError(extractErrorMessage(e, "No se ha podido cargar la lista.")))
      .finally(() => setCargando(false));
  }, []);

  async function anadir(ev: React.FormEvent<HTMLFormElement>) {
    ev.preventDefault();
    if (!dominio.trim()) return;
    setGuardando(true);
    try {
      await apiFetch("/api/admin/dominios-compartidos", {
        method: "POST",
        body: JSON.stringify({ dominio: dominio.trim(), tipo, nota: nota.trim() || null }),
      });
      setDominio("");
      setNota("");
      await cargar();
    } catch (e) {
      setError(extractErrorMessage(e, "No se ha podido añadir."));
    } finally {
      setGuardando(false);
    }
  }

  async function quitar(d: DominioCompartido) {
    if (!window.confirm(`¿Quitar ${d.dominio} de la lista? Los contactos con ese dominio `
      + "volverán a poder vincularse a una empresa por el dominio.")) return;
    try {
      await apiFetch(`/api/admin/dominios-compartidos/${encodeURIComponent(d.dominio)}`,
        { method: "DELETE" });
      await cargar();
    } catch (e) {
      setError(extractErrorMessage(e, "No se ha podido quitar."));
    }
  }

  const compartidos = filas.filter((f) => f.tipo === "compartido");
  const correo = filas.filter((f) => f.tipo === "correo");
  const visibles = verCorreo ? [...compartidos, ...correo] : compartidos;

  return (
    <div className="page">
      <PageHeader
        title="Dominios compartidos"
        description={"Dominios de correo que nunca vinculan un contacto a una empresa: los usa "
          + "gente de muchos sitios distintos (xtec.cat no identifica un instituto, como "
          + "gmail.com no identifica una empresa). Añadir uno no despega a quien ya está "
          + "vinculado: eso se hace con el script del despegue, que enseña antes lo que hará."}
      />
      {error ? <ErrorState title="Error" message={error} /> : null}

      <div className="form-card">
        <h3>Añadir un dominio</h3>
        <form onSubmit={(e) => void anadir(e)} className="form-grid">
          <label>
            Dominio
            <input type="text" value={dominio} onChange={(e) => setDominio(e.target.value)}
                   placeholder="educa.ejemplo.es" required maxLength={255} />
          </label>
          <label>
            Tipo
            <select value={tipo}
                    onChange={(e) => setTipo(e.target.value as DominioCompartido["tipo"])}>
              <option value="compartido">{TIPOS.compartido}</option>
              <option value="correo">{TIPOS.correo}</option>
            </select>
          </label>
          <label>
            Nota (opcional)
            <input type="text" value={nota} onChange={(e) => setNota(e.target.value)}
                   placeholder="Consejería de educación" maxLength={255} />
          </label>
          <button type="submit" className="button" disabled={guardando}>
            {guardando ? "Añadiendo…" : "Añadir"}
          </button>
        </form>
        <p className="muted small">
          Un subdominio cuenta como su dominio: con xtec.cat en la lista, alumnes.xtec.cat
          tampoco empareja.
        </p>
      </div>

      <div className="form-card">
        <h3>
          En la lista: {compartidos.length} compartidos · {correo.length} proveedores de correo
        </h3>
        <label className="small"
               style={{ display: "inline-flex", alignItems: "center", gap: 8, width: "auto" }}>
          <input type="checkbox" checked={verCorreo} style={{ width: "auto" }}
                 onChange={(e) => setVerCorreo(e.target.checked)} />
          Ver también los proveedores de correo
        </label>
        {cargando ? (
          <p className="muted">Cargando…</p>
        ) : visibles.length === 0 ? (
          <p className="muted">La lista está vacía.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Dominio</th>
                <th>Tipo</th>
                <th>Contactos con ese correo</th>
                <th>Nota</th>
                <th>Añadido por</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {visibles.map((d) => (
                <tr key={d.dominio}>
                  <td><code>{d.dominio}</code></td>
                  <td>{TIPOS[d.tipo] ?? d.tipo}</td>
                  <td>{d.contactos === null ? "—" : NF.format(d.contactos)}</td>
                  <td>{d.nota || <span className="muted">—</span>}</td>
                  <td>{d.por || <span className="muted">—</span>}</td>
                  <td>
                    <button type="button" className="button secondary small"
                            onClick={() => void quitar(d)}>
                      Quitar
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
