"use client";

import {
  type Dispatch,
  type FormEvent,
  type SetStateAction,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { ApiError } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";
import {
  actualizarReglaWebs,
  borrarReglaWebs,
  buscarEnWebs,
  crearReglaWebs,
  estadoWebs,
  listarReglasWebs,
  reindexarWebs,
  type WebFragmento,
  type WebRegla,
  type WebsEstado,
  type WebsPasada,
  type WebTipo,
} from "../../lib/websApi";

/** Configuración ERP → Base de conocimiento → «Índice de las webs»: lo que se
 *  ha leído de nuestras webs WordPress (páginas, entradas y productos, por su
 *  REST pública) para que el redactor pueda citar un trozo con su enlace. Se
 *  recorre una vez al mes en el trabajador de las webs; «Reindexar ahora» lo
 *  encola (nunca se hace en la petición). Aquí se ve el conteo por web y tipo,
 *  cómo fue el último recorrido, las reglas de qué entra y qué no, y una
 *  búsqueda de prueba con lo mismo que ve el redactor. */

/** Cada cuánto se mira cómo va un recorrido en cola o en marcha. */
export const REFRESCO_MS = 5000;

const EN_MARCHA: ReadonlyArray<WebsPasada["estado"]> = ["en_cola", "corriendo"];

const ESTADO_PASADA: Record<WebsPasada["estado"], string> = {
  en_cola: "en cola (esperando al trabajador de las webs)",
  corriendo: "en marcha",
  ok: "terminado sin errores",
  parcial: "terminado con errores en alguna web",
  error: "con error",
};

const ORIGEN_PASADA: Record<WebsPasada["origen"], string> = {
  programada: "el mensual",
  manual: "a mano («Reindexar ahora»)",
};

const TIPO_TEXTO: Record<WebTipo, string> = { pagina: "Página", entrada: "Entrada", producto: "Producto" };

type TipoRegla = WebRegla["tipo"];

const TIPOS_REGLA: { id: TipoRegla; titulo: string; explica: string; patron: string; ejemplo: string }[] = [
  {
    id: "pagina_excluir_slug",
    titulo: "Páginas y entradas fuera, por slug",
    explica: "Una expresión regular sobre el slug, sin distinguir mayúsculas: lo que case no se indexa. "
      + "Para legales, carrito, pago, cuenta, avisos de «enviado» y páginas de prueba, en todos los "
      + "idiomas de las webs (impressum, agb, zur-kasse…).",
    patron: "Expresión regular",
    ejemplo: "p. ej. ^mi-cuenta$|cookie",
  },
  {
    id: "pagina_excluir_id",
    titulo: "Páginas y entradas fuera, por id",
    explica: "El id de WordPress de una página o entrada concreta que no debe entrar (una landing "
      + "duplicada, una página vieja). Siempre de una web: el mismo número en otra web es otra página. "
      + "En una web con varios idiomas cada traducción tiene su id: se pone el de cada una.",
    patron: "Id de WordPress",
    ejemplo: "p. ej. 4621",
  },
  {
    id: "categoria_fuera",
    titulo: "Categorías de producto fuera",
    explica: "Se compara con el nombre de cada categoría en el idioma POR DEFECTO de la web: si EMPIEZA "
      + "por esto (sin distinguir mayúsculas ni tildes), esa categoría queda fuera, y con ella todas sus "
      + "subcategorías y la misma categoría en todos los idiomas de la web (se aplica por su id, no por "
      + "el nombre traducido). Cualquier categoría de fuera manda aunque el producto tenga otra de "
      + "dentro, y un producto sin categoría también queda fuera. "
      + "Dentro quedan las máquinas y los accesorios, también los de cada modelo.",
    patron: "La categoría empieza por",
    ejemplo: "p. ej. Repuestos",
  },
  {
    id: "producto_excluir_id",
    titulo: "Productos fuera, por id",
    explica: "Un producto concreto que no debe entrar aunque sus categorías sean de dentro. Siempre de "
      + "una web: el mismo número en otra web es otro producto.",
    patron: "Id del producto",
    ejemplo: "p. ej. 3201",
  },
  {
    id: "producto_incluir_id",
    titulo: "Productos dentro, por id (excepción)",
    explica: "Un producto concreto que entra aunque una regla de categoría lo saque (un accesorio "
      + "colgado de una categoría de repuestos, por ejemplo). Siempre de una web.",
    patron: "Id del producto",
    ejemplo: "p. ej. 4320",
  },
];

const POR_ID: ReadonlySet<TipoRegla> = new Set(["pagina_excluir_id", "producto_excluir_id", "producto_incluir_id"]);

function fechaCorta(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("es-ES", { day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit" });
}

function esWeb(url: string | null | undefined): url is string {
  return typeof url === "string" && /^https?:\/\//i.test(url);
}

function numero(n: number): string {
  return n.toLocaleString("es-ES");
}

/** El precio tal cual lo enseña la web, y qué dice del IVA. */
export function precioDeLaWeb(f: Pick<WebFragmento, "precio_web" | "precio_web_texto" | "precio_iva" | "moneda">):
  { importe: string; iva: string } | null {
  let importe = (f.precio_web_texto ?? "").trim();
  if (!importe && typeof f.precio_web === "number" && f.precio_web > 0) {
    try {
      importe = new Intl.NumberFormat("es-ES", { style: "currency", currency: f.moneda || "EUR" })
        .format(f.precio_web);
    } catch {
      importe = `${f.precio_web.toFixed(2).replace(".", ",")} ${f.moneda ?? ""}`.trim();
    }
  }
  if (!importe) return null;
  const iva = f.precio_iva === "sin_iva" ? "sin IVA"
    : f.precio_iva === "con_iva" ? "con IVA"
      : "la web no dice si lleva IVA";
  return { importe, iva };
}

/** Lo mismo que comprueba el backend antes de guardar, para decirlo sin ir y
 *  volver: patrón, id numérico, expresión regular válida y, en las de id, la
 *  web (los ids de WordPress son de cada web). */
function validarRegla(tipo: TipoRegla, patron: string, sitio: string): string | null {
  const p = patron.trim();
  if (!p) return "Falta el patrón.";
  if (POR_ID.has(tipo) && !/^\d+$/.test(p)) return "El id es un número (el de WordPress).";
  if (tipo === "pagina_excluir_slug") {
    try {
      new RegExp(p, "i");
    } catch {
      return "No es una expresión regular válida.";
    }
  }
  if (POR_ID.has(tipo) && !sitio.trim()) return "Una regla por id necesita su web: los ids son de cada web.";
  return null;
}

/** Hora de los datos que se ven («11:05», o «11:05 del 26/10» si no son de hoy). */
function hora(d: Date): string {
  const hh = d.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === new Date().toDateString()) return hh;
  return `${hh} del ${d.toLocaleDateString("es-ES", { day: "2-digit", month: "2-digit" })}`;
}

/** Por qué falló una llamada. Un 401 es la sesión caducada (el aviso general
 *  lo pinta AppShell; aquí se dice junto a lo que no se ha podido hacer). */
type Fallo = { motivo: string; caducada: boolean };

const MOTIVO_SESION = "tu sesión ha caducado (vuelve a entrar)";

function falloDe(err: unknown): Fallo {
  if (err instanceof ApiError && err.status === 401) return { motivo: MOTIVO_SESION, caducada: true };
  // Sin el punto final: el motivo va dentro de una frase.
  const motivo = extractErrorMessage(err, "fallo sin más detalle").trim().replace(/[.\s]+$/, "");
  return { motivo, caducada: false };
}

export function WebsIndiceBloque({ canEdit }: { canEdit: boolean }) {
  const [estado, setEstado] = useState<WebsEstado | null>(null);
  // La hora a la que llegó lo que se ve, y si la última vez que se pidió falló:
  // con un fallo los números se quedan, pero marcados como de esa hora (antes
  // seguían ahí sin más y parecía que estaban al día).
  const [cargadoEn, setCargadoEn] = useState<Date | null>(null);
  const [fallo, setFallo] = useState<Fallo | null>(null);
  const [actualizando, setActualizando] = useState(false);
  // Cada respuesta, buena o mala, programa la siguiente consulta mientras haya
  // un recorrido en marcha. Antes solo la reprogramaba un `estado` nuevo: tras
  // un error el sondeo se paraba sin decir nada.
  const [respuestas, setRespuestas] = useState(0);
  const [reindexando, setReindexando] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);
  const vivo = useRef(true);

  const cargarEstado = useCallback((): Promise<void> => estadoWebs()
    .then((e) => {
      if (!vivo.current) return;
      setEstado(e);
      setCargadoEn(new Date());
      setFallo(null);
    })
    .catch((err) => {
      if (vivo.current) setFallo(falloDe(err));
    })
    .finally(() => {
      if (vivo.current) setRespuestas((n) => n + 1);
    }), []);

  useEffect(() => {
    vivo.current = true;
    void cargarEstado();
    return () => { vivo.current = false; };
  }, [cargarEstado]);

  // Mientras haya un recorrido en cola o en marcha, el estado se vuelve a
  // pedir cada pocos segundos, también tras un fallo (un corte de red, un 502
  // del despliegue). Con la sesión caducada no: no va a ir mejor hasta entrar
  // de nuevo, y se dice.
  const enMarcha = estado?.en_curso != null && EN_MARCHA.includes(estado.en_curso.estado);
  const caducada = fallo?.caducada ?? false;
  useEffect(() => {
    if (!enMarcha || caducada) return undefined;
    const t = window.setTimeout(() => { void cargarEstado(); }, REFRESCO_MS);
    return () => window.clearTimeout(t);
  }, [enMarcha, caducada, respuestas, cargarEstado]);

  async function actualizar() {
    setActualizando(true);
    await cargarEstado();
    if (vivo.current) setActualizando(false);
  }

  async function reindexar() {
    setReindexando(true);
    setAviso(null);
    try {
      const p = await reindexarWebs();
      if (!vivo.current) return;
      if (EN_MARCHA.includes(p.estado)) {
        setEstado((e) => (e ? { ...e, en_curso: p } : e));
      }
      setAviso(p.estado === "en_cola"
        ? "Recorrido en cola: lo hace el trabajador de las webs, web a web (unos minutos). Esta tabla se refresca sola mientras dura."
        : p.estado === "corriendo"
          ? "Ya había un recorrido en marcha: se sigue con ese. Esta tabla se refresca sola mientras dura."
          : `El recorrido ha terminado: ${ESTADO_PASADA[p.estado] ?? p.estado}.`);
      void cargarEstado();
    } catch (err) {
      if (!vivo.current) return;
      const f = falloDe(err);
      setAviso(f.caducada ? `No se ha podido encolar el recorrido: ${f.motivo}.`
        : extractErrorMessage(err, "No se pudo encolar el recorrido."));
    } finally {
      if (vivo.current) setReindexando(false);
    }
  }

  const sitios = Array.from(new Set((estado?.filas ?? []).map((f) => f.sitio))).sort();

  // Lo que pasó la última vez que se pidió el estado, dicho junto al botón.
  const resultado = actualizando ? null
    : fallo && cargadoEn ? `No se ha podido actualizar: ${fallo.motivo}. Lo que ves es de las ${hora(cargadoEn)}.`
      : fallo ? `No se ha podido cargar el estado del índice: ${fallo.motivo}.`
        : cargadoEn ? `Actualizado a las ${hora(cargadoEn)}` : null;

  return (
    <section className="kb-bloque webs-indice" aria-labelledby="kb-webs-indice-t">
      <h3 id="kb-webs-indice-t">Índice de las webs</h3>
      <p className="muted small">
        Las páginas, entradas y productos de nuestras webs WordPress, leídos por su REST pública (sin
        credenciales y sin escribir nada en ellas), para que el redactor cite un trozo con su enlace.
        Se recorren solas una vez al mes; «Reindexar ahora» encola un recorrido. Lo que desaparece de
        una web se marca y deja de usarse, no se borra. Las imágenes van enlazadas a la web. Los
        precios son los que enseña la web, guardados como <strong>precio de la web</strong>: el
        redactor puede decir «en la web está a…», nunca ofertar con él (el de una oferta sale de
        FACTUSOL); los que la web tiene a 0 («a consultar») no se guardan.
      </p>
      {estado === null && !fallo ? <p className="muted small">Cargando el estado del índice…</p> : null}
      {estado ? (
        <div className={`webs-indice-datos${fallo ? " is-desfasado" : ""}`}>
          {fallo && cargadoEn ? (
            <p className="webs-indice-desfasado">
              <strong>Datos de las {hora(cargadoEn)}, sin actualizar:</strong>{" "}
              {fallo.caducada ? "tu sesión ha caducado; vuelve a entrar para verlos al día."
                : enMarcha ? `la última actualización ha fallado; se vuelve a intentar cada ${REFRESCO_MS / 1000} s.`
                  : "la última actualización ha fallado."}
            </p>
          ) : null}
          <TablaConteos estado={estado} />
          <div className="kb-fuentes webs-indice-pasadas">
            {estado.en_curso ? <ResumenPasada titulo="Recorrido en curso" pasada={estado.en_curso} /> : null}
            <ResumenPasada titulo="Último recorrido terminado" pasada={estado.ultima_pasada} />
          </div>
        </div>
      ) : null}
      <div className="kb-acciones">
        <button type="button" className="button small" onClick={() => void reindexar()}
          disabled={!canEdit || reindexando || enMarcha}
          title={enMarcha ? "Ya hay un recorrido en cola o en marcha" : "Encola un recorrido de todas las webs"}>
          {reindexando ? "Encolando…" : "Reindexar ahora"}
        </button>
        <button type="button" className="button small secondary" onClick={() => void actualizar()}
          disabled={actualizando}>
          {actualizando ? "Actualizando…" : "Actualizar el estado"}
        </button>
        {/* Sin role="status": el de abajo es el aviso de «Reindexar ahora». */}
        <span className={`small webs-indice-carga${fallo ? " is-error" : ""}`} aria-live="polite">
          {resultado}
        </span>
        {enMarcha && !caducada ? <span className="muted small">Hay un recorrido {estado?.en_curso?.estado === "en_cola" ? "en cola" : "en marcha"}: el estado se refresca solo.</span> : null}
      </div>
      {aviso ? <p className="muted small" role="status">{aviso}</p> : null}
      <ReglasIndice canEdit={canEdit} sitios={sitios} />
      <PruebaBusqueda sitios={sitios} />
    </section>
  );
}

// --- conteo por web y tipo --------------------------------------------------------

function Celda({ n }: { n: number }) {
  return <td className={`num${n === 0 ? " is-cero" : ""}`}>{numero(n)}</td>;
}

function TablaConteos({ estado }: { estado: WebsEstado }) {
  const filas = [...estado.filas].sort((a, b) => a.dominio.localeCompare(b.dominio));
  const total = estado.total;
  const totalIdos = filas.reduce((s, f) => s + f.idos, 0);
  const idiomas = Array.from(new Set(filas.flatMap((f) => f.idiomas))).sort();
  if (filas.length === 0) {
    return (
      <p className="muted small">
        El índice está vacío: todavía no se ha recorrido ninguna web. «Reindexar ahora» lo llena sin
        esperar al recorrido del mes.
      </p>
    );
  }
  return (
    <div className="webs-indice-tabla-scroll">
      <table className="data-table erp-settings-table kb-tabla webs-indice-tabla" aria-label="Índice de las webs por web y tipo">
        <thead>
          <tr>
            <th scope="col">Web</th>
            <th scope="col" className="webs-indice-num">Páginas</th>
            <th scope="col" className="webs-indice-num">Entradas</th>
            <th scope="col" className="webs-indice-num">Productos</th>
            <th scope="col" className="webs-indice-num">Total</th>
            <th scope="col" className="webs-indice-num"
              title="Estaban en la web y ya no: se marcan, no se borran, y no se usan">Ya no están</th>
            <th scope="col">Idiomas</th>
            <th scope="col">Última lectura</th>
          </tr>
        </thead>
        <tbody>
          {filas.map((f) => (
            <tr key={f.sitio}>
              <th scope="row" className="webs-indice-web">
                <span className="webs-indice-dominio">{f.dominio}</span>
                {f.sitio !== f.dominio ? <span className="webs-indice-sitio">{f.sitio}</span> : null}
              </th>
              <Celda n={f.pagina} />
              <Celda n={f.entrada} />
              <Celda n={f.producto} />
              <td className="num webs-indice-suma">{numero(f.pagina + f.entrada + f.producto)}</td>
              <Celda n={f.idos} />
              <td>
                <span className="webs-indice-idiomas">
                  {f.idiomas.length > 0
                    ? f.idiomas.map((i) => <span key={i} className="webs-indice-idioma">{i.toUpperCase()}</span>)
                    : "—"}
                </span>
              </td>
              <td className="webs-indice-fecha">{fechaCorta(f.ultima)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">Total ({filas.length} web{filas.length === 1 ? "" : "s"})</th>
            <td className="num">{numero(total.pagina)}</td>
            <td className="num">{numero(total.entrada)}</td>
            <td className="num">{numero(total.producto)}</td>
            <td className="num webs-indice-suma">{numero(total.pagina + total.entrada + total.producto)}</td>
            <td className="num">{numero(totalIdos)}</td>
            <td>{idiomas.length} idioma{idiomas.length === 1 ? "" : "s"}</td>
            <td />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

// --- recorridos -------------------------------------------------------------------

function ResumenPasada({ titulo, pasada }: { titulo: string; pasada: WebsPasada | null }) {
  if (!pasada) {
    return (
      <div className="kb-fuente webs-indice-pasada" aria-label={titulo}>
        <h4>{titulo}</h4>
        <div className="muted small">Ninguno todavía.</div>
      </div>
    );
  }
  const webs = Object.entries(pasada.resumen ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const claseEstado = pasada.estado === "ok" ? "is-ok"
    : pasada.estado === "error" || pasada.estado === "parcial" ? "is-error" : "";
  return (
    <div className="kb-fuente webs-indice-pasada" aria-label={titulo}>
      <h4>{titulo}</h4>
      <div>
        <span className={`kb-estado ${claseEstado}`}>{ESTADO_PASADA[pasada.estado] ?? pasada.estado}</span>
        <span className="muted small"> · {ORIGEN_PASADA[pasada.origen] ?? pasada.origen}</span>
      </div>
      <div className="muted small">
        {pasada.inicio ? `Empezó ${fechaCorta(pasada.inicio)}` : `Encolado ${fechaCorta(pasada.creado_en)}`}
        {pasada.fin ? ` · terminó ${fechaCorta(pasada.fin)}` : ""}
      </div>
      {pasada.pendientes.length > 0 ? (
        <div className="muted small">Faltan: {pasada.pendientes.join(", ")}</div>
      ) : null}
      {pasada.error ? <div className="kb-estado is-error webs-indice-error">{pasada.error}</div> : null}
      {webs.length > 0 ? (
        <ul className="webs-indice-pasada-webs">
          {webs.map(([web, r]) => {
            const partes = [
              r.paginas !== undefined ? `${r.paginas} páginas` : null,
              r.entradas !== undefined ? `${r.entradas} entradas` : null,
              r.productos !== undefined ? `${r.productos} productos` : null,
              r.nuevos ? `${r.nuevos} nuevos` : null,
              r.actualizados ? `${r.actualizados} cambiados` : null,
              r.idos ? `${r.idos} ya no están` : null,
            ].filter(Boolean);
            return (
              <li key={web}>
                <strong>{web}</strong>{" "}
                <span className={`kb-estado ${r.estado === "ok" ? "is-ok" : r.estado === "error" ? "is-error" : ""}`}>
                  {r.estado}
                </span>
                {partes.length > 0 ? <span className="muted small"> · {partes.join(" · ")}</span> : null}
                {r.error ? <div className="kb-estado is-error webs-indice-error">{r.error}</div> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

// --- reglas -----------------------------------------------------------------------

function ordenar(reglas: WebRegla[]): WebRegla[] {
  return [...reglas].sort((a, b) => a.posicion - b.posicion || a.id - b.id);
}

/** Las reglas van plegadas: son decenas de filas con sus campos y aligeran
 *  mucho Configuración ERP sin pintar. Se piden al abrir el panel (para decir
 *  cuántas hay en el botón) y la lista se monta la primera vez que se abre; al
 *  ocultarla solo se esconde, para no perder lo escrito sin guardar. */
function ReglasIndice({ canEdit, sitios }: { canEdit: boolean; sitios: string[] }) {
  const [reglas, setReglas] = useState<WebRegla[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [abiertas, setAbiertas] = useState(false);
  const [montadas, setMontadas] = useState(false);

  useEffect(() => {
    let vivo = true;
    listarReglasWebs()
      .then((l) => { if (vivo) { setReglas(ordenar(l)); setError(null); } })
      .catch((e) => { if (vivo) setError(`No se pudieron cargar las reglas: ${falloDe(e).motivo}.`); });
    return () => { vivo = false; };
  }, []);

  function alternar() {
    setMontadas(true);
    setAbiertas((a) => !a);
  }

  return (
    <div className="webs-indice-reglas" role="group" aria-labelledby="kb-webs-reglas-t">
      <h4 id="kb-webs-reglas-t" className="kb-subtitulo">Reglas: qué entra en el índice y qué no</h4>
      <p className="muted small">
        Se aplican en cada recorrido: un cambio cuenta en el siguiente (o al «Reindexar ahora»). Las de
        categoría se comparan con el nombre en el idioma por defecto de cada web y valen después, por su
        id, para todos sus idiomas y todas sus subcategorías. Sin web, una regla de slug o de categoría
        vale para todas; las de id son siempre de una web.
      </p>
      <div className="kb-acciones">
        <button type="button" className="button small secondary" onClick={alternar}
          aria-expanded={abiertas} aria-controls="kb-webs-reglas-lista">
          {abiertas ? "Ocultar las reglas" : `Ver las reglas${reglas ? ` (${reglas.length})` : ""}`}
        </button>
      </div>
      {montadas ? (
        <div id="kb-webs-reglas-lista" className="webs-indice-reglas-lista" hidden={!abiertas}>
          <ListaReglas canEdit={canEdit} sitios={sitios} reglas={reglas} error={error} setReglas={setReglas} />
        </div>
      ) : null}
    </div>
  );
}

function ListaReglas({
  canEdit, sitios, reglas, error, setReglas,
}: {
  canEdit: boolean;
  sitios: string[];
  reglas: WebRegla[] | null;
  error: string | null;
  setReglas: Dispatch<SetStateAction<WebRegla[] | null>>;
}) {
  const [nueva, setNueva] = useState<{ tipo: TipoRegla; patron: string; sitio: string; nota: string }>({
    tipo: "pagina_excluir_slug", patron: "", sitio: "", nota: "",
  });
  const [creando, setCreando] = useState(false);
  const [errorAlta, setErrorAlta] = useState<string | null>(null);

  async function crear() {
    const fallo = validarRegla(nueva.tipo, nueva.patron, nueva.sitio);
    if (fallo) {
      setErrorAlta(fallo);
      return;
    }
    setCreando(true);
    setErrorAlta(null);
    try {
      const r = await crearReglaWebs({
        tipo: nueva.tipo, patron: nueva.patron.trim(), sitio: nueva.sitio.trim() || null,
        nota: nueva.nota.trim() || null, activo: true,
      });
      setReglas((l) => ordenar([...(l ?? []), r]));
      setNueva({ ...nueva, patron: "", nota: "" });
    } catch (e) {
      setErrorAlta(extractErrorMessage(e, "No se pudo añadir la regla."));
    } finally {
      setCreando(false);
    }
  }

  const sustituir = (r: WebRegla) => setReglas((l) => ordenar((l ?? []).map((x) => (x.id === r.id ? r : x))));
  const quitar = (id: number) => setReglas((l) => (l ?? []).filter((x) => x.id !== id));
  const tipoNueva = TIPOS_REGLA.find((t) => t.id === nueva.tipo) ?? TIPOS_REGLA[0];

  return (
    <>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {!error && reglas === null ? <p className="muted small">Cargando las reglas…</p> : null}
      {reglas ? TIPOS_REGLA.map((t) => {
        const delTipo = reglas.filter((r) => r.tipo === t.id);
        return (
          <div key={t.id} className="webs-indice-reglas-grupo" role="group" aria-label={t.titulo}>
            <h5>{t.titulo} <span className="muted small">({delTipo.length})</span></h5>
            <p className="muted small">{t.explica}</p>
            {delTipo.length > 0 ? (
              <table className="data-table data-table--responsive erp-settings-table kb-tabla" aria-label={`Reglas: ${t.titulo}`}>
                <thead>
                  <tr><th>{t.patron}</th><th>Web</th><th>Nota</th><th>Activa</th><th></th></tr>
                </thead>
                <tbody>
                  {delTipo.map((r) => (
                    <FilaRegla key={r.id} regla={r} canEdit={canEdit} onCambio={sustituir} onBorrada={quitar} />
                  ))}
                </tbody>
              </table>
            ) : <p className="muted small">Ninguna.</p>}
          </div>
        );
      }) : null}
      {canEdit && reglas ? (
        <div className="kb-alta">
          <label className="field"><span>Tipo</span>
            <select aria-label="Tipo de la regla nueva" value={nueva.tipo}
              onChange={(e) => setNueva({ ...nueva, tipo: e.target.value as TipoRegla })}>
              {TIPOS_REGLA.map((t) => <option key={t.id} value={t.id}>{t.titulo}</option>)}
            </select>
          </label>
          <label className="field kb-campo-url"><span>{tipoNueva.patron}</span>
            <input type="text" aria-label="Patrón de la regla nueva" placeholder={tipoNueva.ejemplo} maxLength={255}
              value={nueva.patron} onChange={(e) => setNueva({ ...nueva, patron: e.target.value })} />
          </label>
          <label className="field"><span>{POR_ID.has(nueva.tipo) ? "Web (obligatoria)" : "Web (vacía = todas)"}</span>
            <input type="text" aria-label="Web de la regla nueva" list="kb-webs-sitios" maxLength={40}
              value={nueva.sitio} onChange={(e) => setNueva({ ...nueva, sitio: e.target.value })} />
          </label>
          <label className="field"><span>Nota</span>
            <input type="text" aria-label="Nota de la regla nueva" maxLength={255}
              value={nueva.nota} onChange={(e) => setNueva({ ...nueva, nota: e.target.value })} />
          </label>
          <button type="button" className="button small" onClick={() => void crear()}
            disabled={creando || !nueva.patron.trim()}>
            {creando ? "Añadiendo…" : "Añadir regla"}
          </button>
          {errorAlta ? <p className="form-error" role="alert">{errorAlta}</p> : null}
        </div>
      ) : null}
      <datalist id="kb-webs-sitios">{sitios.map((s) => <option key={s} value={s} />)}</datalist>
    </>
  );
}

function FilaRegla({
  regla, canEdit, onCambio, onBorrada,
}: { regla: WebRegla; canEdit: boolean; onCambio: (r: WebRegla) => void; onBorrada: (id: number) => void }) {
  const [patron, setPatron] = useState(regla.patron);
  const [sitio, setSitio] = useState(regla.sitio ?? "");
  const [nota, setNota] = useState(regla.nota ?? "");
  const [ocupada, setOcupada] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nombre = `«${regla.patron}»${regla.sitio ? ` (${regla.sitio})` : ""}`;
  const cambiada = patron.trim() !== regla.patron || sitio.trim() !== (regla.sitio ?? "")
    || nota.trim() !== (regla.nota ?? "");

  /** `campos`: tras «Guardar», el patrón, la web y la nota pasan a ser los del
   *  servidor. (Des)activar no los toca: no se lleva lo que se esté escribiendo
   *  en la fila sin guardar (ni el botón «Guardar»). */
  async function cambiar(cambios: Parameters<typeof actualizarReglaWebs>[1], campos: boolean) {
    setOcupada(true);
    setError(null);
    try {
      const r = await actualizarReglaWebs(regla.id, cambios);
      if (campos) {
        setPatron(r.patron);
        setSitio(r.sitio ?? "");
        setNota(r.nota ?? "");
      }
      onCambio(r);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar la regla."));
    } finally {
      setOcupada(false);
    }
  }

  function guardar() {
    const fallo = validarRegla(regla.tipo, patron, sitio);
    if (fallo) {
      setError(fallo);
      return;
    }
    void cambiar({ patron: patron.trim(), sitio: sitio.trim() || null, nota: nota.trim() || null }, true);
  }

  async function borrar() {
    if (!window.confirm(`¿Borrar la regla ${nombre}? Cuenta en el próximo recorrido.`)) return;
    setOcupada(true);
    setError(null);
    try {
      await borrarReglaWebs(regla.id);
      onBorrada(regla.id);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo borrar la regla."));
      setOcupada(false);
    }
  }

  return (
    <tr className={regla.activo ? undefined : "is-inactivo"}>
      <td data-label="Patrón">
        {canEdit ? (
          <input type="text" aria-label={`Patrón de la regla ${nombre}`} value={patron} disabled={ocupada}
            maxLength={255} onChange={(e) => setPatron(e.target.value)} />
        ) : <code>{regla.patron}</code>}
      </td>
      <td data-label="Web">
        {canEdit ? (
          <input type="text" aria-label={`Web de la regla ${nombre}`} list="kb-webs-sitios" value={sitio}
            placeholder="todas" disabled={ocupada} maxLength={40} onChange={(e) => setSitio(e.target.value)} />
        ) : (regla.sitio ?? <span className="muted small">todas</span>)}
      </td>
      <td data-label="Nota">
        {canEdit ? (
          <input type="text" aria-label={`Nota de la regla ${nombre}`} value={nota} disabled={ocupada}
            maxLength={255} onChange={(e) => setNota(e.target.value)} />
        ) : <span className="muted small">{regla.nota ?? ""}</span>}
      </td>
      <td data-label="Activa">
        <input type="checkbox" aria-label={`Activa la regla ${nombre}`} checked={regla.activo}
          disabled={!canEdit || ocupada} onChange={(e) => void cambiar({ activo: e.target.checked }, false)} />
      </td>
      <td>
        {canEdit ? (
          <span className="kb-acciones">
            {cambiada ? (
              <button type="button" className="button small" disabled={ocupada} onClick={guardar}>Guardar</button>
            ) : null}
            <button type="button" className="button small ghost" aria-label={`Borrar la regla ${nombre}`}
              disabled={ocupada} onClick={() => void borrar()}>Borrar</button>
          </span>
        ) : null}
        {error ? <><br /><span className="form-error small" role="alert">{error}</span></> : null}
      </td>
    </tr>
  );
}

// --- búsqueda de prueba ------------------------------------------------------------

function PruebaBusqueda({ sitios }: { sitios: string[] }) {
  const [q, setQ] = useState("");
  const [idioma, setIdioma] = useState("");
  const [sitio, setSitio] = useState("");
  const [tipo, setTipo] = useState<WebTipo | "">("");
  const [resultados, setResultados] = useState<WebFragmento[] | null>(null);
  const [buscada, setBuscada] = useState("");
  const [buscando, setBuscando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function buscar(e?: FormEvent<HTMLFormElement>) {
    e?.preventDefault();
    const consulta = q.trim();
    if (!consulta) {
      setError("Escribe qué buscar: un modelo, un accesorio, una página…");
      return;
    }
    setBuscando(true);
    setError(null);
    try {
      setResultados(await buscarEnWebs(consulta, {
        idioma: idioma.trim() || null, sitio: sitio || null, tipo: tipo || null,
      }));
      setBuscada(consulta);
    } catch (err) {
      setResultados(null);
      setError(extractErrorMessage(err, "No se pudo buscar en el índice."));
    } finally {
      setBuscando(false);
    }
  }

  return (
    <div className="webs-indice-prueba" role="group" aria-labelledby="kb-webs-prueba-t">
      <h4 id="kb-webs-prueba-t" className="kb-subtitulo">Probar: qué encontraría el redactor</h4>
      <p className="muted small">
        La misma búsqueda con la que el asistente consulta solo el índice al redactar, si la casilla
        «Webs» de la franja está marcada: los trozos que tienen que ver, con su enlace, su imagen y,
        en los productos, el precio que enseña la web.
      </p>
      <form className="kb-alta" onSubmit={(e) => void buscar(e)}>
        <label className="field kb-campo-url"><span>Buscar</span>
          <input type="search" aria-label="Buscar en el índice de las webs" maxLength={200}
            placeholder="p. ej. Beambox II, horno DTF, impresora UV A3" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        <label className="field"><span>Idioma</span>
          <input type="text" aria-label="Idioma de la búsqueda de prueba" maxLength={5} placeholder="todos"
            value={idioma} onChange={(e) => setIdioma(e.target.value)} />
        </label>
        <label className="field"><span>Web</span>
          <select aria-label="Web de la búsqueda de prueba" value={sitio} onChange={(e) => setSitio(e.target.value)}>
            <option value="">todas</option>
            {sitios.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label className="field"><span>Qué</span>
          <select aria-label="Tipo de la búsqueda de prueba" value={tipo} onChange={(e) => setTipo(e.target.value as WebTipo | "")}>
            <option value="">todo</option>
            <option value="producto">productos</option>
            <option value="pagina">páginas</option>
            <option value="entrada">entradas</option>
          </select>
        </label>
        <button type="submit" className="button small" disabled={buscando}>
          {buscando ? "Buscando…" : "Buscar en el índice"}
        </button>
      </form>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {resultados !== null && resultados.length === 0 ? (
        <p className="muted small" role="status">Nada en el índice para «{buscada}».</p>
      ) : null}
      {resultados && resultados.length > 0 ? (
        <ul className="webs-indice-resultados" aria-label="Resultados de la prueba">
          {resultados.map((f) => <ResultadoPrueba key={f.id} f={f} />)}
        </ul>
      ) : null}
    </div>
  );
}

function ResultadoPrueba({ f }: { f: WebFragmento }) {
  const precio = precioDeLaWeb(f);
  return (
    <li className="webs-indice-resultado">
      {esWeb(f.imagen) ? (
        // eslint-disable-next-line @next/next/no-img-element -- imagen de la web, enlazada tal cual
        <img className="webs-indice-resultado-imagen" src={f.imagen} alt="" loading="lazy" referrerPolicy="no-referrer" />
      ) : <span className="webs-indice-resultado-imagen is-vacia" aria-hidden />}
      <div className="webs-indice-resultado-cuerpo">
        <p className="webs-indice-resultado-titulo">
          {esWeb(f.url) ? <a href={f.url} target="_blank" rel="noreferrer">{f.titulo || f.url}</a> : <strong>{f.titulo}</strong>}
        </p>
        <p className="webs-indice-resultado-meta">
          {f.dominio || f.sitio} · {TIPO_TEXTO[f.tipo] ?? f.tipo}{f.idioma ? ` · ${f.idioma.toUpperCase()}` : ""}
          {f.categorias.length > 0 ? ` · ${f.categorias.join(", ")}` : ""}
          {f.galeria.length > 0 ? ` · ${f.galeria.length} ${f.galeria.length === 1 ? "imagen" : "imágenes"} más en la galería` : ""}
        </p>
        {esWeb(f.url) ? <p className="webs-indice-resultado-url">{f.url}</p> : null}
        {f.fragmento ? <p className="webs-indice-resultado-fragmento">{f.fragmento}</p> : null}
        {precio ? (
          <p className="webs-indice-resultado-precio">
            Precio de la web: <strong>{precio.importe}</strong> ({precio.iva})
            <span className="webs-indice-resultado-precio-nota"> · lo que dice la web, no es precio de oferta</span>
          </p>
        ) : null}
      </div>
    </li>
  );
}
