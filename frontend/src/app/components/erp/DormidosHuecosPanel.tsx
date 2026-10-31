"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  downloadDormidosHuecosCsv,
  getDormidosHuecos,
  guardarAjustesDormidos,
  saveBlob,
  type DormidosHuecos,
  type FiltroDormidos,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

/** Dormidos y huecos (1/11/2026): el parque mirado de dos maneras.
 *  Dormidos: tienen una máquina y llevan meses sin comprar consumibles ni
 *  recambios de su gama. Huecos: tienen una máquina y nunca han comprado un
 *  consumible de su gama. Las listas se parten por gama, sector (ficha de
 *  empresa), antigüedad de la máquina y gasto total, para que salga una lista
 *  corta que se pueda llamar; el CSV exporta lo filtrado. */

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

function numero(v: string): number | undefined {
  const n = Number(v.replace(",", "."));
  return v.trim() && Number.isFinite(n) ? n : undefined;
}

export function DormidosHuecosPanel({ puedeEditar }: { puedeEditar: boolean }) {
  const [datos, setDatos] = useState<DormidosHuecos | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [meses, setMeses] = useState("");
  const [gracia, setGracia] = useState("");
  // El filtro que se está escribiendo y el aplicado (el de la lista y el CSV).
  const [gama, setGama] = useState("");
  const [sector, setSector] = useState("");
  const [mesesMin, setMesesMin] = useState("");
  const [mesesMax, setMesesMax] = useState("");
  const [gastoMin, setGastoMin] = useState("");
  const [aplicado, setAplicado] = useState<FiltroDormidos>({});

  const leer = useCallback(async (f: FiltroDormidos) => {
    setError(null);
    setOcupado(true);
    try {
      setDatos(await getDormidosHuecos(f));
      setAplicado(f);
    } catch (e) {
      setError(extractErrorMessage(e, "No se han podido calcular las listas."));
    } finally {
      setOcupado(false);
    }
  }, []);

  useEffect(() => { void leer({}); }, [leer]);

  function filtro(): FiltroDormidos {
    return {
      gama: gama || undefined, sector: sector || undefined,
      meses_min: numero(mesesMin), meses_max: numero(mesesMax), gasto_min: numero(gastoMin),
    };
  }

  async function guardar() {
    setError(null);
    setOcupado(true);
    try {
      await guardarAjustesDormidos({
        ...(meses.trim() ? { meses_dormido: Number(meses) } : {}),
        ...(gracia.trim() ? { meses_gracia_hueco: Number(gracia) } : {}),
      });
      setMeses("");
      setGracia("");
      await leer(aplicado);
    } catch (e) {
      setError(extractErrorMessage(e, "No se ha podido guardar."));
    } finally {
      setOcupado(false);
    }
  }

  async function exportar(tipo: "dormidos" | "huecos") {
    setError(null);
    try {
      saveBlob(await downloadDormidosHuecosCsv(tipo, aplicado), `${tipo}.csv`);
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
  const { dormidos, huecos } = datos;
  const aj = datos.ajustes;
  const sinGama = datos.resumen.sin_gama ?? [];
  const sectores = datos.resumen.sectores ?? [];
  const hayFiltro = Object.values(aplicado).some((v) => v !== undefined);

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
      {sinGama.length > 0 ? (
        <details>
          <summary className="muted small">
            {sinGama.length} máquinas en servicio no tienen gama (su familia está sin gama): no se
            pueden cruzar con consumibles. Dale gama a su familia en «Familias».
          </summary>
          <ul className="small" aria-label="Máquinas sin gama">
            {sinGama.map((m, i) => (
              <li key={`${m.codcli}-${m.codart}-${i}`}>
                <span className="mono">{m.codart ?? "—"}</span> {m.articulo ?? ""}{" "}
                <span className="muted">· familia <span className="mono">{m.familia ?? "—"}</span>
                  {" "}· cliente <span className="mono">{m.codcli}</span></span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <form className="erp-historico-acciones" aria-label="Partir las listas"
            onSubmit={(e) => { e.preventDefault(); void leer(filtro()); }}>
        <label className="field">
          <span>Gama</span>
          <select value={gama} onChange={(e) => setGama(e.target.value)}>
            <option value="">Todas</option>
            {datos.resumen.por_gama.map((g) => <option key={g.gama} value={g.gama}>{g.gama_nombre}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Sector</span>
          <select value={sector} onChange={(e) => setSector(e.target.value)}>
            <option value="">Todos</option>
            {sectores.map((x) => (
              <option key={x.sector ?? "-"} value={x.sector ?? "-"}>
                {x.sector ?? "Sin sector"} ({x.clientes})
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Máquina de hace (meses), desde</span>
          <input inputMode="numeric" value={mesesMin} aria-label="Meses con la máquina, mínimo"
                 onChange={(e) => setMesesMin(e.target.value)} />
        </label>
        <label className="field">
          <span>hasta</span>
          <input inputMode="numeric" value={mesesMax} aria-label="Meses con la máquina, máximo"
                 onChange={(e) => setMesesMax(e.target.value)} />
        </label>
        <label className="field">
          <span>Gasto total desde (€)</span>
          <input inputMode="decimal" value={gastoMin} aria-label="Gasto total mínimo"
                 onChange={(e) => setGastoMin(e.target.value)} />
        </label>
        <button type="submit" className="button small" disabled={ocupado}>Filtrar</button>
        {hayFiltro ? (
          <button type="button" className="button small secondary" disabled={ocupado}
                  onClick={() => {
                    setGama(""); setSector(""); setMesesMin(""); setMesesMax(""); setGastoMin("");
                    void leer({});
                  }}>
            Quitar filtros
          </button>
        ) : null}
      </form>
      {sectores.length > 0 && sectores.every((x) => x.sector === null) ? (
        <p className="muted small">
          Ningún cliente de estas listas tiene el sector puesto en su ficha de empresa: el filtro
          por sector no parte nada todavía.
        </p>
      ) : null}

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
            <tr><th>Cliente</th><th>Sector</th><th>Gama</th><th>Máquinas</th><th>Última compra</th>
              <th className="num">Meses sin comprar</th><th className="num">Compraba (12 meses)</th>
              <th className="num">Gasto total</th></tr>
          </thead>
          <tbody>
            {dormidos.map((d) => (
              <tr key={`${d.codcli}-${d.gama}`}>
                <td data-label="Cliente"><Cliente c={d} /></td>
                <td data-label="Sector">{d.sector ?? <span className="muted">—</span>}</td>
                <td data-label="Gama">{d.gama_nombre}</td>
                <td data-label="Máquinas" className="small">{d.articulos.join(" · ")}</td>
                <td data-label="Última compra">{fecha(d.ultima_compra)}</td>
                <td data-label="Meses sin comprar" className="num">{d.meses_sin_comprar}</td>
                <td data-label="Compraba (12 meses)" className="num">{EUR.format(d.compraba_12_meses)}</td>
                <td data-label="Gasto total" className="num">{EUR.format(d.gasto_total)}</td>
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
        de menos de {aj.meses_gracia_hueco} meses aún no cuenta). Lo compran en otro sitio. Arriba,
        quien más ha comprado en total. Las máquinas viejas siguen contando hasta que se den de
        baja: filtra por antigüedad para verlas.
      </p>
      {huecos.length === 0 ? <p className="muted">Ninguno.</p> : (
        <table className="data-table data-table--responsive" aria-label="Huecos">
          <thead>
            <tr><th>Cliente</th><th>Sector</th><th>Gama</th><th>Máquinas</th><th>Última máquina</th>
              <th className="num">Meses</th><th>Recambios</th><th className="num">Gasto total</th></tr>
          </thead>
          <tbody>
            {huecos.map((h) => (
              <tr key={`${h.codcli}-${h.gama}`}>
                <td data-label="Cliente"><Cliente c={h} /></td>
                <td data-label="Sector">{h.sector ?? <span className="muted">—</span>}</td>
                <td data-label="Gama">{h.gama_nombre}</td>
                <td data-label="Máquinas" className="small">{h.articulos.join(" · ")}</td>
                <td data-label="Última máquina">{fecha(h.ultima_maquina)}</td>
                <td data-label="Meses" className="num">{h.meses_con_maquina ?? "—"}</td>
                <td data-label="Recambios">{h.solo_recambios ? "Sí, pero ningún consumible" : "—"}</td>
                <td data-label="Gasto total" className="num">{EUR.format(h.gasto_total)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
