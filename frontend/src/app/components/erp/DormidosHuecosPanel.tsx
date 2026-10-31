"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  downloadDormidosHuecosCsv,
  getDormidosHuecos,
  guardarAjustesDormidos,
  saveBlob,
  type DormidosHuecos,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

/** Dormidos y huecos (1/11/2026): el parque mirado de dos maneras.
 *  Dormidos: tienen una máquina y llevan meses sin comprar consumibles ni
 *  recambios de su gama. Huecos: tienen una máquina y nunca han comprado un
 *  consumible de su gama. Las dos listas son para llamar o escribir:
 *  se exportan. */

const EUR = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });

function fecha(iso: string | null): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${Number(d)}/${Number(m)}/${y}`;
}

function Cliente({ c }: { c: { codcli: string; nombre: string | null; empresa: { id: string; nombre: string } | null } }) {
  return (
    <>
      {c.empresa ? (
        <Link href={`/companies/${c.empresa.id}`} prefetch={false}>{c.empresa.nombre}</Link>
      ) : (c.nombre ?? <span className="muted">sin nombre</span>)}{" "}
      <span className="mono muted small">{c.codcli}</span>
    </>
  );
}

export function DormidosHuecosPanel({ puedeEditar }: { puedeEditar: boolean }) {
  const [datos, setDatos] = useState<DormidosHuecos | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [meses, setMeses] = useState("");
  const [gracia, setGracia] = useState("");
  const [gama, setGama] = useState("");

  useEffect(() => {
    getDormidosHuecos().then(setDatos)
      .catch((e) => setError(extractErrorMessage(e, "No se han podido calcular las listas.")));
  }, []);

  async function guardar() {
    setError(null);
    setOcupado(true);
    try {
      setDatos(await guardarAjustesDormidos({
        ...(meses.trim() ? { meses_dormido: Number(meses) } : {}),
        ...(gracia.trim() ? { meses_gracia_hueco: Number(gracia) } : {}),
      }));
      setMeses("");
      setGracia("");
    } catch (e) {
      setError(extractErrorMessage(e, "No se ha podido guardar."));
    } finally {
      setOcupado(false);
    }
  }

  async function exportar(tipo: "dormidos" | "huecos") {
    setError(null);
    try {
      saveBlob(await downloadDormidosHuecosCsv(tipo), `${tipo}.csv`);
    } catch (e) {
      setError(extractErrorMessage(e, "No se ha podido exportar."));
    }
  }

  if (datos === null) {
    return (
      <section className="erp-card" aria-label="Dormidos y huecos">
        {error ? <p className="form-error" role="alert">{error}</p> : <p className="muted">Calculando…</p>}
      </section>
    );
  }
  const dormidos = datos.dormidos.filter((d) => !gama || d.gama === gama);
  const huecos = datos.huecos.filter((h) => !gama || h.gama === gama);
  const aj = datos.ajustes;

  return (
    <section className="erp-card" aria-label="Dormidos y huecos">
      <p className="form-hint">
        Sale del parque (solo las máquinas en servicio) y de lo comprado de la gama de cada
        máquina: el consumible y el recambio cuentan para la gama de su familia. Si una gama sale
        entera como hueco, mira primero cómo está clasificada su familia de consumibles.
      </p>
      {error ? <p className="form-error" role="alert">{error}</p> : null}

      <table className="data-table data-table--responsive" aria-label="Por gama">
        <thead>
          <tr><th>Gama</th><th className="num">Clientes con máquina</th>
            <th className="num">Compran su consumible</th><th className="num">Dormidos</th>
            <th className="num">Huecos</th></tr>
        </thead>
        <tbody>
          {datos.resumen.por_gama.map((g) => (
            <tr key={g.gama}>
              <td data-label="Gama">{g.gama_nombre}</td>
              <td data-label="Clientes con máquina" className="num">{g.clientes}</td>
              <td data-label="Compran su consumible" className="num">{g.con_consumible}</td>
              <td data-label="Dormidos" className="num">{g.dormidos}</td>
              <td data-label="Huecos" className="num">{g.huecos}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {datos.resumen.maquinas_sin_gama > 0 ? (
        <p className="muted small">
          {datos.resumen.maquinas_sin_gama} máquinas en servicio no tienen gama (su familia está
          sin gama): no se pueden cruzar con consumibles.
        </p>
      ) : null}

      <div className="erp-historico-acciones">
        <label className="field">
          <span>Gama</span>
          <select value={gama} onChange={(e) => setGama(e.target.value)}>
            <option value="">Todas</option>
            {datos.resumen.por_gama.map((g) => <option key={g.gama} value={g.gama}>{g.gama_nombre}</option>)}
          </select>
        </label>
        {puedeEditar ? (
          <form className="erp-historico-acciones" onSubmit={(e) => { e.preventDefault(); void guardar(); }}>
            <label className="field">
              <span>Dormido a los (meses)</span>
              <input inputMode="numeric" value={meses} placeholder={String(aj.meses_dormido)}
                     aria-label="Meses sin comprar para ser dormido"
                     onChange={(e) => setMeses(e.target.value)} />
            </label>
            <label className="field">
              <span>Hueco desde (meses con la máquina)</span>
              <input inputMode="numeric" value={gracia} placeholder={String(aj.meses_gracia_hueco)}
                     aria-label="Meses con la máquina para contar como hueco"
                     onChange={(e) => setGracia(e.target.value)} />
            </label>
            <button type="submit" className="button small secondary"
                    disabled={ocupado || (!meses.trim() && !gracia.trim())}>
              Cambiar
            </button>
          </form>
        ) : null}
      </div>

      <h3>
        Dormidos <span className="muted">· {dormidos.length}</span>{" "}
        <button type="button" className="button small secondary" onClick={() => void exportar("dormidos")}>
          Exportar CSV
        </button>
      </h3>
      <p className="form-hint">
        Tienen una máquina y llevan {aj.meses_dormido} meses o más sin comprar consumibles ni
        recambios de su gama. Primero los que más compraban.
      </p>
      {dormidos.length === 0 ? <p className="muted">Ninguno.</p> : (
        <table className="data-table data-table--responsive" aria-label="Dormidos">
          <thead>
            <tr><th>Cliente</th><th>Gama</th><th>Máquinas</th><th>Última compra</th>
              <th className="num">Meses sin comprar</th><th className="num">Compraba (12 meses)</th></tr>
          </thead>
          <tbody>
            {dormidos.map((d) => (
              <tr key={`${d.codcli}-${d.gama}`}>
                <td data-label="Cliente"><Cliente c={d} /></td>
                <td data-label="Gama">{d.gama_nombre}</td>
                <td data-label="Máquinas" className="small">{d.articulos.join(" · ")}</td>
                <td data-label="Última compra">{fecha(d.ultima_compra)}</td>
                <td data-label="Meses sin comprar" className="num">{d.meses_sin_comprar}</td>
                <td data-label="Compraba (12 meses)" className="num">{EUR.format(d.compraba_12_meses)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3>
        Huecos <span className="muted">· {huecos.length}</span>{" "}
        <button type="button" className="button small secondary" onClick={() => void exportar("huecos")}>
          Exportar CSV
        </button>
      </h3>
      <p className="form-hint">
        Tienen una máquina de una gama y nunca han comprado un consumible de esa gama (una máquina
        de menos de {aj.meses_gracia_hueco} meses aún no cuenta). Lo compran en otro sitio.
      </p>
      {huecos.length === 0 ? <p className="muted">Ninguno.</p> : (
        <table className="data-table data-table--responsive" aria-label="Huecos">
          <thead>
            <tr><th>Cliente</th><th>Gama</th><th>Máquinas</th><th>Última máquina</th><th>Recambios</th></tr>
          </thead>
          <tbody>
            {huecos.map((h) => (
              <tr key={`${h.codcli}-${h.gama}`}>
                <td data-label="Cliente"><Cliente c={h} /></td>
                <td data-label="Gama">{h.gama_nombre}</td>
                <td data-label="Máquinas" className="small">{h.articulos.join(" · ")}</td>
                <td data-label="Última máquina">{fecha(h.ultima_maquina)}</td>
                <td data-label="Recambios">{h.solo_recambios ? "Sí, pero ningún consumible" : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
