"use client";

import {
  ChevronDown,
  ChevronRight,
  Globe,
  LifeBuoy,
  RotateCcw,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { getCurrentUser } from "../../lib/api";
import {
  ALCANCES_ASISTENTE,
  ALCANCES_SIN_HILO,
  etiquetaAlcance,
  guardarAlcance,
  leerAlcance,
} from "../../lib/asistenteAlcance";
import type { EstructuraCompuesta } from "../../lib/correoCompuesto";
import {
  type AsistenteAlcance,
  type BomediaSustituida,
  type BorradorAsistente,
  type FormatoAsistente,
  type SobreElEditor,
  type SoporteElegido,
  conversacionAsistente,
  descartarBorradorAsistente,
  esDestinatarioInterno,
  generarBorradorAsistente,
  generarBorradorNuevo,
  turnoAsistente,
} from "../../lib/emailsApi";
import { extractErrorMessage } from "../../lib/errors";
import type { SoporteResultado } from "../../lib/soporteApi";
import type { WebFragmento } from "../../lib/websApi";
import { Modal } from "../Modal";
import { SoporteBusqueda } from "../soporte/SoporteBusqueda";
import { WebsBusqueda } from "../webs/WebsBusqueda";

/** «Ayúdame a contestar» dentro del compositor: una franja propia entre la
 *  cabecera del correo y el cuerpo, que empuja el contenido hacia abajo en vez
 *  de taparlo. De arriba abajo: la caja de instrucción con el selector de
 *  alcance y el botón de redactar; la conversación (lo dicho hasta ahora, con
 *  vuelta atrás); el aviso de la IA; «Lo que ha usado», plegado; y descartar.
 *  En cuanto hay borrador se pliega sola a una línea («1 borrador · 0,0164 $»)
 *  para que el cuerpo tenga el sitio: se despliega al pulsarla. Lo que llega al
 *  editor es siempre un correo: lo que la IA quiera decirle a la persona va
 *  aquí, en el aviso. Un borrador guardado vuelve con su conversación.
 *  «Buscar en soporte» y «Buscar en las webs» abren su buscador: lo elegido
 *  queda a la vista como material (con su ✕) y viaja con cada petición. */
export type AsistenteFranjaProps = {
  /** Con hilo se responde; sin él es un correo nuevo (la instrucción es obligatoria). */
  threadId: string | null;
  contactId: string | null;
  /** La dirección de «Para» del compositor (la primera si hay varias). */
  destinatario: string;
  /** El hilo dice que la sugerencia de «Responder» es un compañero. */
  destinatarioInterno?: boolean;
  abiertaInicial?: boolean;
  /** El compositor está enviando: nada se toca. */
  bloqueada?: boolean;
  /** Un borrador guardado que arrastra su conversación: el turno que estaba en
   *  el editor al guardar. La franja la recupera (turnos y coste). */
  borradorInicialId?: string | null;
  /** Hay texto escrito en el cuerpo (sin contar la firma): la caja va en modo
   *  corrección («Aplicar»), haya conversación o no, y la IA corrige ESE texto. */
  hayTexto?: boolean;
  /** Lo que hay ahora mismo en el editor (sin la firma) y su selección, si la
   *  hay: viaja con cada petición para que la IA corrija eso y no el borrador. */
  obtenerCuerpo?: () => { html: string; seleccion: string };
  /** El correo compuesto que hay en la vista previa (con lo retocado), o null:
   *  si lo hay, la IA corrige ESE (con operaciones sobre sus bloques). */
  obtenerEstructura?: () => EstructuraCompuesta | null;
  /** Llega un turno: el compositor pone su cuerpo (y su asunto) en el editor.
   *  `false` si no pudo (un fragmento para una selección que ya no está). */
  onBorrador: (
    borrador: BorradorAsistente,
    /** `editorCambiado`: el texto del editor ya no es el que se mandó a corregir
     *  (la persona siguió escribiendo, o es un turno anterior): el compositor
     *  pregunta antes de pisarlo. */
    opciones?: { editorCambiado?: boolean },
  ) => boolean | void;
  /** La conversación se vacía: el editor vuelve a como estaba antes del asistente. */
  onVaciar: () => void;
  /** El turno que está en el editor (para anotar el resultado al enviar), o null. */
  onBorradorActual: (id: string | null) => void;
  /** Lo que la franja tiene entre manos: el compositor lo mira antes de cerrar. */
  onEstado?: (estado: { instruccion: string; turnos: number }) => void;
};

/** El interruptor de la franja: qué correo pide la persona. «Auto» es lo de
 *  siempre (texto, salvo que la instrucción pida presentar productos). */
const FORMATOS: { id: FormatoAsistente; label: string; titulo: string }[] = [
  { id: "auto", label: "Auto", titulo: "La IA elige: con fichas si pides presentar productos («mándale la gama DTF»); si no, texto" },
  { id: "normal", label: "Texto", titulo: "Un correo de texto, como siempre" },
  { id: "compuesto", label: "Con fichas", titulo: "Un correo con fichas de producto, tira de marca, vídeo y botones; el HTML lo genera BoHub" },
];

function primera(direcciones: string): string {
  return direcciones.split(/[,\n;]/).map((s) => s.trim()).filter(Boolean)[0] ?? "";
}

function usd(n: number): string {
  return `${n.toFixed(4)} $`;
}

/** Lo que la persona ha elegido DE FUERA para el borrador, con «Buscar en
 *  soporte» y «Buscar en las webs». Viaja en cada petición (redactar, corregir,
 *  cambiar el alcance) y se queda elegido hasta que se quita con su ✕: la IA
 *  lo tiene delante en las correcciones siguientes. Un artículo de soporte y
 *  «no hay procedimiento» se excluyen (el último gana). */
export type MaterialElegido = {
  soporte: (SoporteElegido & { titulo: string; url: string; fuente_nombre: string }) | null;
  /** La consulta que se buscó en el soporte sin encontrar nada. */
  sinResultados: string | null;
  webs: { id: string; titulo: string; url: string }[];
};

export const SIN_MATERIAL: MaterialElegido = { soporte: null, sinResultados: null, webs: [] };
/** Las páginas de las webs que admite el redactor en una petición. */
export const MAX_WEBS = 4;
/** Lo que admite el backend en «busqué y no hay» (`SoporteSinResultados`). */
const MAX_CONSULTA = 300;

/** Los campos de la petición, solo los que llevan algo. Sin material, null:
 *  la petición es exactamente la de siempre (ni campos vacíos). */
export function camposMaterial(
  m: MaterialElegido,
): Pick<SobreElEditor, "soporte" | "soporte_sin_resultados" | "webs"> | null {
  const campos: Pick<SobreElEditor, "soporte" | "soporte_sin_resultados" | "webs"> = {};
  if (m.soporte) {
    campos.soporte = { fuente: m.soporte.fuente, articulo_id: m.soporte.articulo_id, idioma: m.soporte.idioma };
  } else if (m.sinResultados) {
    campos.soporte_sin_resultados = { consulta: m.sinResultados };
  }
  if (m.webs.length > 0) campos.webs = m.webs.map((w) => w.id);
  return Object.keys(campos).length > 0 ? campos : null;
}

/** El texto de un fragmento de HTML, recortado, para enseñar qué hay seleccionado. */
function resumenDe(html: string, max = 70): string {
  const texto = (new DOMParser().parseFromString(html, "text/html").body.textContent ?? "")
    .replace(/\s+/g, " ").trim();
  return texto.length > max ? `${texto.slice(0, max - 1).trimEnd()}…` : texto;
}

/** Un aviso en ámbar: a dos líneas con un «más» si es largo. Está bien escrito,
 *  pero no puede comerse el sitio del cuerpo. */
export function AvisoPlegable({ titulo, texto }: { titulo: string; texto: string }) {
  const [abierto, setAbierto] = useState(false);
  const largo = texto.length > 140;
  return (
    <div className="asistente-franja-aviso" role="status">
      <p className={`asistente-franja-aviso-texto${largo && !abierto ? " is-plegado" : ""}`}>
        <strong>{titulo}</strong> {texto}
      </p>
      {largo ? (
        <button
          type="button"
          className="asistente-franja-aviso-mas"
          aria-expanded={abierto}
          onClick={() => setAbierto((v) => !v)}
        >
          {abierto ? "menos" : "más"}
        </button>
      ) : null}
    </div>
  );
}

/** La guardia de la marca cambió «Bomedia» por la empresa del correo. Va sin
 *  plegar y con cada frase antes y ahora: cambiar un nombre puede reescribir
 *  un hecho («ya trabajaste con Bomedia en 2019»), y quien revisa tiene que
 *  enterarse y poder releer esa frase, no solo saber que se tocó algo. */
export function AvisoBomedia({ empresa, cambios }: BomediaSustituida) {
  const una = cambios.length === 1;
  return (
    <div className="asistente-franja-aviso asistente-franja-aviso-bomedia" role="alert" aria-label="Bomedia sustituida">
      <div className="asistente-franja-aviso-texto">
        <p className="asistente-franja-bomedia-titulo">
          <strong>
            He cambiado «Bomedia» por «{empresa}» en {una ? "esta frase" : `estas ${cambios.length} frases`}.
          </strong>{" "}
          {una ? "Reléela" : "Reléelas"} antes de enviar: si cuenta{una ? "" : "n"} un hecho (con quién trabajó, quién
          le facturó), el nombre nuevo puede no ser cierto; reescríbe{una ? "la" : "las"} como toque.
        </p>
        <ul className="asistente-franja-bomedia-lista">
          {cambios.map((c, i) => (
            <li key={`${c.campo}-${i}`}>
              {c.campo === "asunto" ? <span className="asistente-franja-bomedia-campo">En el asunto propuesto</span> : null}
              <span className="asistente-franja-bomedia-antes">Antes: «{c.antes}»</span>
              <span className="asistente-franja-bomedia-ahora">Ahora: «{c.despues}»</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function AsistenteFranja({
  threadId,
  contactId,
  destinatario,
  destinatarioInterno = false,
  abiertaInicial = false,
  bloqueada = false,
  borradorInicialId = null,
  hayTexto = false,
  obtenerCuerpo,
  obtenerEstructura,
  onBorrador,
  onVaciar,
  onBorradorActual,
  onEstado,
}: AsistenteFranjaProps) {
  const modo: "respuesta" | "nuevo" = threadId ? "respuesta" : "nuevo";
  const [abierta, setAbierta] = useState(abiertaInicial);
  const [instruccion, setInstruccion] = useState("");
  // Lo que hay seleccionado en el editor cuando la persona viene a la caja:
  // la corrección se aplicará solo a eso, y se dice.
  const [seleccion, setSeleccion] = useState("");
  const [alcance, setAlcance] = useState<AsistenteAlcance>("contacto");
  const [formato, setFormato] = useState<FormatoAsistente>("auto");
  const [usuarioId, setUsuarioId] = useState<string | null>(null);
  const [turnos, setTurnos] = useState<BorradorAsistente[]>([]);
  const [actual, setActual] = useState(0);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Lo que dice la API de la dirección de «Para» (null = todavía no se ha preguntado).
  const [internoApi, setInternoApi] = useState<boolean | null>(null);
  // Lo elegido de fuera (soporte, webs) y qué buscador está abierto.
  const [material, setMaterial] = useState<MaterialElegido>(SIN_MATERIAL);
  const [buscador, setBuscador] = useState<"soporte" | "webs" | null>(null);
  const cajaRef = useRef<HTMLTextAreaElement>(null);
  const onBorradorActualRef = useRef(onBorradorActual);
  const onEstadoRef = useRef(onEstado);
  useEffect(() => {
    onBorradorActualRef.current = onBorradorActual;
    onEstadoRef.current = onEstado;
  });

  // Qué histórico mira la IA: se recuerda por usuario (en este navegador).
  useEffect(() => {
    getCurrentUser()
      .then((u) => {
        setUsuarioId(u.id);
        const guardado = leerAlcance(u.id);
        if (guardado) setAlcance(guardado);
      })
      .catch(() => undefined);
  }, []);

  // Un borrador guardado con conversación: la franja vuelve con sus turnos y
  // su coste. El editor ya tiene el cuerpo (viene del borrador): no se repone.
  useEffect(() => {
    if (!borradorInicialId) return;
    let vivo = true;
    conversacionAsistente(borradorInicialId)
      .then(({ turnos: lista, actual: id }) => {
        if (!vivo || lista.length === 0) return;
        const idx = Math.max(0, lista.findIndex((t) => t.id === id));
        setTurnos(lista);
        setActual(idx);
        onBorradorActualRef.current(lista[idx].id);
      })
      .catch(() => undefined);
    return () => { vivo = false; };
  }, [borradorInicialId]);

  // El compositor sabe si hay algo que perder al cerrar (una instrucción a
  // medias, una conversación).
  useEffect(() => {
    onEstadoRef.current?.({ instruccion, turnos: turnos.length });
  }, [instruccion, turnos.length]);

  // «Vas a escribir a un compañero»: se recalcula cada vez que cambia «Para»,
  // con borrador o sin él (antes solo salía con el primer borrador).
  useEffect(() => {
    const para = primera(destinatario);
    if (!para.includes("@")) {
      const t = window.setTimeout(() => setInternoApi(null), 0);
      return () => window.clearTimeout(t);
    }
    const t = window.setTimeout(() => {
      esDestinatarioInterno(para)
        .then((r) => setInternoApi(r.interno))
        .catch(() => undefined);
    }, 350);
    return () => window.clearTimeout(t);
  }, [destinatario]);

  // Sin hilo, «solo este hilo» no significa nada.
  const alcanceEfectivo: AsistenteAlcance =
    modo === "nuevo" && !ALCANCES_SIN_HILO.includes(alcance) ? "contacto" : alcance;
  const opciones = modo === "nuevo"
    ? ALCANCES_ASISTENTE.filter((a) => ALCANCES_SIN_HILO.includes(a.id))
    : ALCANCES_ASISTENTE;
  const borrador = turnos[actual] ?? null;
  const hayConversacion = turnos.length > 0;
  // El estado de la caja lo decide si hay texto en el cuerpo: con texto (de la
  // IA, a mano, o un borrador reabierto) se corrige; sin él, se redacta.
  const modoCorreccion = hayConversacion || hayTexto;
  const interno = internoApi !== null
    ? internoApi
    : destinatarioInterno || Boolean(borrador?.destinatario_interno);
  const costeTotal = turnos.reduce((s, t) => s + (t.coste_usd || 0), 0);
  // El idioma del correo, si la franja lo sabe (lo dice el borrador): los
  // buscadores buscan en ese. Sin borrador, en todos.
  const idiomaCorreo = borrador?.idioma ?? turnos[0]?.idioma ?? null;
  const hayMaterial = camposMaterial(material) !== null;

  function usarSoporte(r: SoporteResultado) {
    setMaterial((m) => ({
      ...m,
      soporte: {
        fuente: r.fuente, articulo_id: r.articulo_id, idioma: r.idioma ?? null,
        titulo: r.titulo, url: r.url, fuente_nombre: r.fuente_nombre,
      },
      sinResultados: null,
    }));
    setBuscador(null);
  }

  function sinProcedimiento(consulta: string) {
    const q = consulta.trim().slice(0, MAX_CONSULTA);
    if (!q) return;
    setMaterial((m) => ({ ...m, soporte: null, sinResultados: q }));
    setBuscador(null);
  }

  function anadirWeb(f: WebFragmento) {
    setMaterial((m) => (m.webs.some((w) => w.id === f.id) || m.webs.length >= MAX_WEBS
      ? m
      : { ...m, webs: [...m.webs, { id: f.id, titulo: f.titulo || f.url, url: f.url }] }));
  }

  function refrescarSeleccion() {
    const sel = hayTexto ? (obtenerCuerpo?.().seleccion ?? "") : "";
    setSeleccion(sel ? resumenDe(sel) : "");
  }

  function poner(b: BorradorAsistente, opciones?: { editorCambiado?: boolean }): boolean {
    const ok = onBorrador(b, opciones);
    if (ok === false && b.fragmento && b.seleccion?.fragmento_html) {
      // Solo un fragmento sin sitio no se puede poner; un cuerpo vacío (la
      // guardia) ya lo explica el aviso, y «no reemplazar» es una decisión.
      setError(
        "El editor ya no tiene la selección sobre la que se reescribió este fragmento: "
        + "selecciona otra vez ese texto y pulsa el turno en la lista para ponerlo.",
      );
    }
    return ok !== false;
  }

  function aplicar(b: BorradorAsistente, lista: BorradorAsistente[], idx: number,
                   opciones?: { editorCambiado?: boolean }) {
    setTurnos(lista);
    setActual(idx);
    onBorradorActual(b.id);
    const puesto = poner(b, opciones);
    // Con borrador, la franja se pliega sola: lo que hay que leer antes de
    // enviar es el correo, y es lo que más sitio necesita.
    if (b.texto && puesto) setAbierta(false);
  }

  async function redactar() {
    if (ocupado || bloqueada) return;
    const texto = instruccion.trim();
    const para = primera(destinatario);
    if (modo === "nuevo" && !para) {
      setError("Pon un destinatario en «Para»: es lo que dispara el expediente del cliente.");
      return;
    }
    if (modoCorreccion && !texto) {
      setError("Escribe qué quieres cambiar («más corto», «más formal», «en inglés», «quita el precio»…).");
      return;
    }
    if (modo === "nuevo" && !texto) {
      setError("Dime qué quieres decirle: en un correo nuevo no hay nada de lo que deducir la intención.");
      return;
    }
    // Lo que hay ahora en el compositor: un correo compuesto (su estructura,
    // con lo retocado en la vista previa) o el texto del editor (y solo la
    // selección si la hay). La IA corrige ESO.
    const compuesto = obtenerEstructura?.() ?? null;
    const fotoCompuesto = compuesto && compuesto.bloques.length > 0 ? JSON.stringify(compuesto) : null;
    const cuerpo = hayTexto && fotoCompuesto === null ? obtenerCuerpo?.() : undefined;
    const editor: SobreElEditor | null = fotoCompuesto !== null
      ? { estructura_actual: compuesto }
      : cuerpo?.html
        ? { texto_actual_html: cuerpo.html, seleccion_html: cuerpo.seleccion || null }
        : null;
    // El interruptor solo viaja si no es «Auto» (lo de siempre), y lo elegido
    // de fuera solo si hay algo: sin nada, la petición es la de siempre.
    const conFormato: SobreElEditor | null = formato !== "auto" ? { ...(editor ?? {}), formato } : editor;
    const deFuera = camposMaterial(material);
    const peticion: SobreElEditor | null = deFuera ? { ...(conFormato ?? {}), ...deFuera } : conFormato;
    // Si mientras la IA trabaja la persona sigue escribiendo, lo que vuelve ya
    // no es una corrección de lo que hay: el compositor pregunta antes de pisarlo.
    const cambiado = () => ({
      editorCambiado: fotoCompuesto !== null
        ? JSON.stringify(obtenerEstructura?.() ?? null) !== fotoCompuesto
        : editor !== null && (obtenerCuerpo?.().html ?? "") !== editor.texto_actual_html,
    });
    setOcupado(true);
    setError(null);
    try {
      if (hayConversacion && borrador) {
        // Una corrección: un turno más, con el contexto ya montado. Si se
        // corrige un turno anterior, los posteriores se dejan atrás.
        const nuevo = peticion
          ? await turnoAsistente(borrador.id, texto, peticion)
          : await turnoAsistente(borrador.id, texto);
        const lista = [...turnos.slice(0, actual + 1), nuevo];
        aplicar(nuevo, lista, lista.length - 1, cambiado());
      } else if (modo === "nuevo") {
        const nuevo = await generarBorradorNuevo({
          destinatario: para, instruccion: texto, alcance: alcanceEfectivo,
          contact_id: contactId, ...(peticion ?? {}),
        });
        aplicar(nuevo, [nuevo], 0, cambiado());
      } else {
        const nuevo = await generarBorradorAsistente(threadId as string, {
          alcance: alcanceEfectivo, instruccion: texto || null, destinatario: para || null,
          ...(peticion ?? {}),
        });
        aplicar(nuevo, [nuevo], 0, cambiado());
      }
      setInstruccion("");
      setSeleccion("");
    } catch (err) {
      setError(extractErrorMessage(err, "La IA no ha podido redactar el borrador."));
    } finally {
      setOcupado(false);
    }
  }

  async function cambiarAlcance(nuevo: AsistenteAlcance) {
    setAlcance(nuevo);
    guardarAlcance(usuarioId, nuevo);
    const raiz = turnos[0];
    if (!raiz || ocupado || bloqueada) return;
    // Cambiar el alcance rearma el contexto: es un borrador nuevo, con la
    // instrucción con la que empezó la conversación (la anterior queda regenerada).
    setOcupado(true);
    setError(null);
    try {
      const para = primera(destinatario);
      // El borrador rehecho lleva lo mismo que lleva la franja: el formato y
      // lo elegido de fuera (si hay algo).
      const conFormato = { ...(formato !== "auto" ? { formato } : {}), ...(camposMaterial(material) ?? {}) };
      const b = modo === "nuevo"
        ? await generarBorradorNuevo({
          destinatario: para, instruccion: raiz.instruccion ?? "", alcance: nuevo,
          contact_id: contactId, regenerar_de: raiz.id, ...conFormato,
        })
        : await generarBorradorAsistente(threadId as string, {
          alcance: nuevo, instruccion: raiz.instruccion, destinatario: para || null,
          regenerar_de: raiz.id, ...conFormato,
        });
      aplicar(b, [b], 0);
    } catch (err) {
      setError(extractErrorMessage(err, "La IA no ha podido redactar el borrador."));
    } finally {
      setOcupado(false);
    }
  }

  function irA(idx: number) {
    const b = turnos[idx];
    if (!b || ocupado || bloqueada) return;
    setActual(idx);
    onBorradorActual(b.id);
    setError(null);
    // Un turno anterior se calculó sobre otro texto: si hay algo escrito a
    // mano, el compositor pregunta antes de pisarlo.
    poner(b, { editorCambiado: true });
  }

  function vaciar(plegar: boolean) {
    const ultimo = turnos[turnos.length - 1];
    if (ultimo) void descartarBorradorAsistente(ultimo.id).catch(() => undefined);
    setTurnos([]);
    setActual(0);
    setInstruccion("");
    setError(null);
    onBorradorActual(null);
    onVaciar();
    if (plegar) setAbierta(false);
    else {
      setAbierta(true);
      cajaRef.current?.focus();
    }
  }

  const resumen = hayConversacion
    ? `${turnos.length} ${turnos.length === 1 ? "borrador" : "turnos"} · ${usd(costeTotal)}`
    : hayTexto
      ? "la IA corrige lo que hay en el editor; tú lo revisas y lo envías"
      : modo === "respuesta"
        ? "la IA redacta la respuesta; tú la revisas y la envías"
        : "la IA redacta el correo; tú lo revisas y lo envías";
  const enVistaPrevia = Boolean(obtenerEstructura?.()?.bloques.length);
  const placeholder = seleccion
    ? "Qué cambiar en la selección: «más corto», «más formal», «en inglés», «quita el precio»…"
    : enVistaPrevia
      ? "Corrige el correo: «quita el vídeo», «deja solo dos máquinas», «añade un botón a la tienda», «pásalo a texto»…"
      : modoCorreccion
      ? "Corrige el texto: «más corto», «más formal», «en inglés», «no menciones el precio», «añade que el plazo son tres semanas»…"
      : modo === "respuesta"
        ? "¿Qué quieres decirle? Si lo dejas vacío, la IA contesta al último mensaje del cliente."
        : "¿Qué quieres decirle? Por ejemplo: «han pasado tres semanas desde el presupuesto, escríbele un seguimiento»";

  return (
    <section className={`asistente-franja${abierta ? " is-abierta" : ""}`} aria-label="Asistente de redacción">
      <button
        type="button"
        className="asistente-franja-cabecera"
        aria-expanded={abierta}
        onClick={() => setAbierta((v) => !v)}
        title={abierta ? "Plegar el asistente" : (hayConversacion ? "Desplegar para corregir el borrador" : "Desplegar el asistente")}
      >
        <Sparkles size={13} aria-hidden />
        <strong>Ayúdame a {modo === "respuesta" ? "contestar" : "redactar"}</strong>
        <span className="muted small asistente-franja-resumen">{resumen}</span>
        {abierta ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}
      </button>
      {borrador?.bomedia && borrador.bomedia.cambios.length > 0 ? (
        // FUERA de lo que se pliega: con borrador la franja se pliega sola, y
        // este aviso no puede quedar escondido ni detrás de un «más».
        <AvisoBomedia empresa={borrador.bomedia.empresa} cambios={borrador.bomedia.cambios} />
      ) : null}
      {abierta ? (
        <div className="asistente-franja-cuerpo">
          {interno ? (
            <AvisoPlegable
              titulo="Vas a escribir a un compañero"
              texto="(una dirección del grupo): el tono y el contenido no son los de un correo a cliente."
            />
          ) : null}
          <div className="asistente-franja-caja">
            <textarea
              ref={cajaRef}
              aria-label="Qué quieres decirle"
              placeholder={placeholder}
              value={instruccion}
              rows={2}
              disabled={ocupado || bloqueada}
              onFocus={refrescarSeleccion}
              onChange={(e) => {
                setInstruccion(e.target.value);
                refrescarSeleccion();
              }}
              onKeyDown={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                  e.preventDefault();
                  void redactar();
                }
              }}
            />
            {seleccion ? (
              <p className="muted small asistente-franja-seleccion" role="note">
                Se aplicará solo a la selección: «{seleccion}». El resto no se toca.
              </p>
            ) : null}
            <div className="asistente-franja-controles">
              {/* Buscar fuera es una acción explícita, ANTES de redactar: lo
                  encontrado se ve, se elige y viaja con la petición (nunca se
                  busca dentro de la generación, que tiene su tiempo contado). */}
              <span className="asistente-franja-buscar">
                <button
                  type="button"
                  className="button secondary small"
                  onClick={() => setBuscador("soporte")}
                  disabled={ocupado || bloqueada}
                  title="Busca el procedimiento en el soporte del fabricante: el borrador lo citará con su enlace"
                >
                  <LifeBuoy size={11} aria-hidden /> Buscar en soporte
                </button>
                <button
                  type="button"
                  className="button secondary small"
                  onClick={() => setBuscador("webs")}
                  disabled={ocupado || bloqueada}
                  title="Busca en nuestras webs páginas y productos para enlazar en el borrador"
                >
                  <Globe size={11} aria-hidden /> Buscar en las webs
                </button>
              </span>
              <span className="asistente-franja-formato" role="radiogroup" aria-label="Formato del correo">
                {FORMATOS.map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    role="radio"
                    aria-checked={formato === f.id}
                    className={formato === f.id ? "is-activo" : undefined}
                    title={f.titulo}
                    disabled={ocupado || bloqueada}
                    onClick={() => setFormato(f.id)}
                  >
                    {f.label}
                  </button>
                ))}
              </span>
              <select
                className="email-toolbar-alcance"
                aria-label="Qué histórico mira la IA"
                title="Qué histórico mira la IA al redactar. Se recuerda para ti. Cambiarlo rearma el contexto: es un borrador nuevo."
                value={alcanceEfectivo}
                disabled={ocupado || bloqueada}
                onChange={(e) => void cambiarAlcance(e.target.value as AsistenteAlcance)}
              >
                {opciones.map((a) => (
                  <option key={a.id} value={a.id}>{a.label}</option>
                ))}
              </select>
              <button
                type="button"
                className="button small"
                onClick={() => void redactar()}
                disabled={ocupado || bloqueada}
                title={modoCorreccion ? "Reescribe el texto del editor con esa corrección (Ctrl+Intro)" : "Pide el borrador a la IA (Ctrl+Intro)"}
              >
                <Sparkles size={12} aria-hidden /> {ocupado ? "Redactando…" : modoCorreccion ? "Aplicar" : "Redactar"}
              </button>
            </div>
            {hayMaterial ? (
              <MaterialChips
                material={material}
                bloqueado={ocupado || bloqueada}
                onQuitarSoporte={() => setMaterial((m) => ({ ...m, soporte: null }))}
                onQuitarSinResultados={() => setMaterial((m) => ({ ...m, sinResultados: null }))}
                onQuitarWeb={(id) => setMaterial((m) => ({ ...m, webs: m.webs.filter((w) => w.id !== id) }))}
              />
            ) : null}
          </div>
          {error ? <p className="form-error asistente-franja-error" role="alert">{error}</p> : null}
          {hayConversacion ? (
            <div className="asistente-franja-conversacion">
              <ol className="asistente-franja-turnos" aria-label="Lo dicho hasta ahora">
                {turnos.map((t, i) => (
                  <li key={t.id} className={i === actual ? "is-actual" : undefined}>
                    <button
                      type="button"
                      className="asistente-franja-turno"
                      aria-current={i === actual ? "true" : undefined}
                      onClick={() => irA(i)}
                      disabled={ocupado || bloqueada}
                      title={i === actual ? "Es el borrador que está en el editor" : "Volver a este borrador"}
                    >
                      {t.turno === 0
                        ? (t.instruccion ? `«${t.instruccion}»` : "Borrador inicial, deducido del hilo")
                        : `«${t.instruccion ?? ""}»`}
                      {t.seleccion ? (
                        <span className="muted small"> — sobre la selección «{resumenDe(t.seleccion.original, 40)}»</span>
                      ) : t.sobre_editor && t.turno === 0 ? (
                        <span className="muted small"> — sobre tu texto</span>
                      ) : null}
                      {t.modo === "compuesto" && t.texto ? <span className="muted small"> — con fichas</span> : null}
                      {!t.texto ? <span className="muted small"> — sin cuerpo</span> : null}
                    </button>
                  </li>
                ))}
              </ol>
              {turnos.length > 1 ? (
                <div className="asistente-franja-navegacion">
                  <button type="button" className="button secondary small" onClick={() => irA(actual - 1)} disabled={actual === 0 || ocupado || bloqueada}>
                    Volver al borrador anterior
                  </button>
                  <button type="button" className="button secondary small" onClick={() => irA(actual + 1)} disabled={actual >= turnos.length - 1 || ocupado || bloqueada}>
                    Siguiente
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}
          {borrador?.aviso ? (
            // Con cuerpo (ante la duda se queda en el editor) es un aviso para
            // revisarlo; sin cuerpo, la IA no redactó nada.
            <AvisoPlegable
              titulo={borrador.texto ? "Revisa antes de enviar:" : "La IA no ha redactado el correo:"}
              texto={borrador.aviso}
            />
          ) : null}
          {borrador && borrador.pendiente.length > 0 ? (
            <AvisoPlegable titulo="No ha podido responder con los datos:" texto={borrador.pendiente.join(" · ")} />
          ) : null}
          {borrador ? <LoQueHaUsado borrador={borrador} /> : null}
          {hayConversacion ? (
            <div className="asistente-franja-acciones">
              <button type="button" className="button secondary small" onClick={() => vaciar(false)} disabled={ocupado || bloqueada} title="Vacía la conversación y el editor para pedir otro borrador">
                <RotateCcw size={11} aria-hidden /> Empezar de nuevo
              </button>
              <button type="button" className="button secondary small" onClick={() => vaciar(true)} disabled={ocupado || bloqueada} title="Deja la conversación como descartada y vacía el editor">
                <Trash2 size={11} aria-hidden /> Descartar el borrador de la IA
              </button>
              <span className="muted small">
                {borrador ? `${borrador.modelo} · ` : ""}esta conversación: {usd(costeTotal)}
              </span>
            </div>
          ) : null}
        </div>
      ) : null}
      {buscador !== null && typeof document !== "undefined" ? createPortal(
        // Fuera del compositor (que es un <form>): un Intro en el buscador no
        // puede enviar el correo, ni un envío del buscador subir hasta el
        // formulario del correo por el árbol de React.
        <div className="asistente-fuentes-capa" onSubmit={(e) => e.stopPropagation()}>
          <Modal
            open
            onClose={() => setBuscador(null)}
            title={buscador === "soporte" ? "Buscar en soporte" : "Buscar en nuestras webs"}
          >
            {buscador === "soporte" ? (
              <SoporteBusqueda
                idioma={idiomaCorreo}
                consultaInicial={material.sinResultados ?? undefined}
                onUsar={usarSoporte}
                onSinResultados={sinProcedimiento}
              />
            ) : (
              <WebsBusqueda
                idioma={idiomaCorreo}
                elegidos={material.webs.map((w) => w.id)}
                maximo={MAX_WEBS}
                onAnadir={anadirWeb}
              />
            )}
          </Modal>
        </div>,
        document.body,
      ) : null}
    </section>
  );
}

/** Lo elegido de fuera, a la vista y con su ✕: es lo que viaja con la petición. */
function MaterialChips({
  material, bloqueado, onQuitarSoporte, onQuitarSinResultados, onQuitarWeb,
}: {
  material: MaterialElegido;
  bloqueado: boolean;
  onQuitarSoporte: () => void;
  onQuitarSinResultados: () => void;
  onQuitarWeb: (id: string) => void;
}) {
  return (
    <div className="asistente-franja-material">
      <ul className="asistente-material-chips" aria-label="Material elegido para el borrador">
        {material.soporte ? (
          <li className="asistente-material-chip">
            <span>Soporte: {material.soporte.titulo || material.soporte.articulo_id}</span>
            <button type="button" aria-label="Quitar el artículo de soporte" title="Quitar" disabled={bloqueado} onClick={onQuitarSoporte}>✕</button>
          </li>
        ) : null}
        {material.sinResultados ? (
          <li className="asistente-material-chip is-sin-resultados">
            <span>Sin procedimiento: {material.sinResultados}</span>
            <button type="button" aria-label="Quitar «sin procedimiento»" title="Quitar" disabled={bloqueado} onClick={onQuitarSinResultados}>✕</button>
          </li>
        ) : null}
        {material.webs.map((w) => (
          <li key={w.id} className="asistente-material-chip">
            <span>Web: {w.titulo}</span>
            <button type="button" aria-label={`Quitar la página «${w.titulo}»`} title="Quitar" disabled={bloqueado} onClick={() => onQuitarWeb(w.id)}>✕</button>
          </li>
        ))}
      </ul>
      <span className="muted small">Va con cada petición hasta que lo quites.</span>
    </div>
  );
}

type MaterialUsado = {
  soporte: { titulo: string; url: string | null; fuente: string } | null;
  webs: { id: string; titulo: string; url: string | null }[];
  sinResultados: string | null;
};

function esObjeto(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Solo enlaces web: lo apuntado en el contexto nunca acaba en un `href` raro. */
function urlWeb(v: unknown): string | null {
  return typeof v === "string" && /^https?:\/\//i.test(v) ? v : null;
}

/** Lo de fuera que entró en el turno, tal como lo apunta el backend en el
 *  contexto (`material`: `soporte`, `webs`, `sin_resultados`). Tolerante: un
 *  borrador de antes no lo trae, y cualquier forma rara se ignora. */
export function materialUsado(ctx: Record<string, unknown>): MaterialUsado {
  const m = esObjeto(ctx["material"]) ? ctx["material"] : ctx;
  const s = m["soporte"];
  const soporte = esObjeto(s) && (s["titulo"] || s["url"])
    ? {
      titulo: String(s["titulo"] ?? "") || String(s["url"] ?? ""),
      url: urlWeb(s["url"]),
      fuente: String(s["fuente_nombre"] ?? s["fuente"] ?? ""),
    }
    : null;
  const webs = Array.isArray(m["webs"])
    ? m["webs"].filter(esObjeto).map((w, i) => ({
      id: String(w["id"] ?? i),
      titulo: String(w["titulo"] ?? "") || String(w["url"] ?? ""),
      url: urlWeb(w["url"]),
    })).filter((w) => w.titulo)
    : [];
  const sr = m["sin_resultados"];
  const sinResultados = esObjeto(sr) && typeof sr["consulta"] === "string" && sr["consulta"].trim()
    ? sr["consulta"].trim()
    : null;
  return { soporte, webs, sinResultados };
}

/** Plegado: lo que entró en el contexto (hilo, ficha, correos viejos con fecha,
 *  asunto y motivo, empresa, tokens, lo dejado fuera) y los enlaces que podía usar. */
export function LoQueHaUsado({ borrador }: { borrador: BorradorAsistente }) {
  const ctx = borrador.contexto ?? {};
  const num = (k: string): number | null => (typeof ctx[k] === "number" ? (ctx[k] as number) : null);
  const contacto = ctx["contacto"] as { nombre?: string; empresa?: string | null } | null | undefined;
  const historico = Array.isArray(ctx["historico"]) ? (ctx["historico"] as Record<string, unknown>[]) : [];
  const empresa = Array.isArray(ctx["empresa"]) ? (ctx["empresa"] as Record<string, unknown>[]) : [];
  const recortes = Array.isArray(ctx["recortes"]) ? (ctx["recortes"] as string[]) : [];
  const catalogo = (ctx["catalogo"] ?? null) as {
    maquinas?: { nombre?: string; marca?: string; precio_texto?: string | null; orientativo?: boolean;
      precio_publico?: boolean; eleccion?: string }[];
    consumibles?: { nombre?: string; precio_texto?: string | null; orientativo?: boolean }[];
    sin_maquina?: string[];
  } | null;
  const maquinas = catalogo?.maquinas ?? [];
  const consumibles = catalogo?.consumibles ?? [];
  const nombradas = maquinas.filter((m) => m.eleccion === "hilo").length;
  const pedidas = maquinas.filter((m) => m.eleccion === "instruccion").length;
  const porPalabras = maquinas.filter((m) => m.eleccion === "consulta").length;
  const posibles = maquinas.filter(
    (m) => m.eleccion === "hilo_posible" || m.eleccion === "instruccion_posible").length;
  const tokens = num("tokens_estimados");
  const presupuesto = num("presupuesto_tokens");
  const partes: string[] = [];
  const hilo = num("mensajes_hilo");
  if (ctx["sin_hilo"] === true) partes.push("sin hilo (correo nuevo)");
  else if (hilo !== null) partes.push(`${hilo} mensaje${hilo === 1 ? "" : "s"} del hilo`);
  // La consulta de su último lead (el formulario o la nota de Agile): cuenta,
  // con sus correos, para elegir las máquinas del catálogo.
  const consultaLead = ctx["consulta_lead"] as { fuente?: string; fecha?: string | null } | null | undefined;
  if (consultaLead) {
    const de = consultaLead.fuente === "web_form" ? "su formulario"
      : consultaLead.fuente === "agilecrm" ? "su nota de Agile" : "su lead";
    const instante = typeof consultaLead.fecha === "string" ? new Date(consultaLead.fecha) : null;
    // El día de la oficina, no el UTC: un formulario de las 00:30 es de ese día.
    const dia = instante && !Number.isNaN(instante.getTime())
      ? new Intl.DateTimeFormat("es-ES", { day: "2-digit", month: "2-digit", year: "numeric",
        timeZone: "Europe/Madrid" }).format(instante)
      : "";
    partes.push(`la consulta de ${de}${dia ? ` (${dia})` : ""}`);
  }
  partes.push(contacto ? `la ficha de ${contacto.nombre || "el contacto"}${contacto.empresa ? ` (${contacto.empresa})` : ""}` : "sin ficha (destinatario desconocido)");
  // La oferta que va adjunta (el modal de enviar una oferta): número, líneas
  // y total, tal como la IA los leyó.
  const oferta = ctx["oferta"] as { numero?: string; lineas?: number; total?: number } | null | undefined;
  if (oferta && oferta.numero) {
    const n = typeof oferta.lineas === "number" ? oferta.lineas : 0;
    const total = typeof oferta.total === "number"
      ? `${new Intl.NumberFormat("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(oferta.total)} €`
      : null;
    partes.push(`Oferta ${oferta.numero} adjunta (${n} línea${n === 1 ? "" : "s"}${total ? `, total ${total}` : ""})`);
  }
  // El resto de la ficha que entró: notas internas, campos personalizados,
  // etiquetas y teléfonos.
  const deLaFicha = (
    [
      ["notas", "nota interna", "notas internas"],
      ["campos_personalizados", "campo personalizado", "campos personalizados"],
      ["etiquetas", "etiqueta", "etiquetas"],
      ["telefonos", "teléfono", "teléfonos"],
    ] as const
  ).flatMap(([clave, una, varias]) => {
    const n = num(clave);
    return n ? [`${n} ${n === 1 ? una : varias}`] : [];
  });
  if (deLaFicha.length > 0) partes.push(deLaFicha.join(", "));
  const pedidos = num("pedidos");
  if (pedidos) partes.push(`${pedidos} pedido${pedidos === 1 ? "" : "s"}`);
  if (typeof ctx["modelo_flux"] === "string" && ctx["modelo_flux"]) partes.push(`máquina FLUX ${ctx["modelo_flux"]}`);
  if (historico.length > 0) partes.push(`${historico.length} correo${historico.length === 1 ? "" : "s"} anterior${historico.length === 1 ? "" : "es"} del contacto`);
  if (empresa.length > 0) partes.push(`${empresa.length} asunto${empresa.length === 1 ? "" : "s"} de otras personas de la empresa`);
  if (maquinas.length > 0 || consumibles.length > 0) {
    const trozos: string[] = [];
    if (maquinas.length > 0) {
      const origen: string[] = [];
      if (nombradas > 0) origen.push(`${nombradas} que nombra el cliente`);
      if (pedidas > 0) origen.push(`${pedidas} que pide la instrucción`);
      if (porPalabras > 0) origen.push(`${porPalabras} por las palabras de la consulta`);
      if (posibles > 0) origen.push(`${posibles} que puede que nombren`);
      trozos.push(`${maquinas.length} máquina${maquinas.length === 1 ? "" : "s"} del catálogo${origen.length > 0 ? ` (${origen.join(", ")})` : ""}`);
    }
    if (consumibles.length > 0) trozos.push(`${consumibles.length} consumible${consumibles.length === 1 ? "" : "s"} de la tienda`);
    partes.push(trozos.join(" y "));
  } else if (catalogo) {
    partes.push("sin productos del catálogo para este caso (ningún precio)");
  }
  // Lo de fuera que eligió la persona para este turno (soporte, nuestras webs).
  const deFuera = materialUsado(ctx);
  if (deFuera.soporte) {
    partes.push(`el artículo de soporte «${deFuera.soporte.titulo}»${deFuera.soporte.fuente ? ` (${deFuera.soporte.fuente})` : ""}`);
  }
  if (deFuera.sinResultados) {
    partes.push(`la búsqueda «${deFuera.sinResultados}» en el soporte, sin procedimiento`);
  }
  if (deFuera.webs.length > 0) {
    partes.push(`${deFuera.webs.length} página${deFuera.webs.length === 1 ? "" : "s"} de nuestras webs`);
  }
  if (tokens !== null) partes.push(`~${tokens.toLocaleString("es-ES")} tokens de entrada${presupuesto ? ` de ${presupuesto.toLocaleString("es-ES")}` : ""}`);
  if (recortes.length > 0) partes.push(`${recortes.length} elemento${recortes.length === 1 ? "" : "s"} fuera por tamaño`);
  if (borrador.turno > 0) {
    // La caché de prompts es de cada modelo y dura 5 minutos: la primera
    // corrección con un modelo distinto del primer borrador escribe la suya y
    // las siguientes, si llegan a tiempo, la leen.
    const leidos = borrador.tokens_cache_lectura;
    const escritos = borrador.tokens_cache_escritura;
    const cache = leidos > 0
      ? `${leidos.toLocaleString("es-ES")} tokens leídos de la caché`
      : escritos > 0
        ? `${escritos.toLocaleString("es-ES")} tokens guardados en la caché de ${borrador.modelo}: la caché es de cada modelo, y la leen las correcciones siguientes que lleguen antes de 5 minutos`
        : "sin caché en este turno";
    partes.push(`turno ${borrador.turno}: el contexto no se volvió a montar (${cache})`);
  }
  return (
    <details className="asistente-panel-detalle">
      <summary>Lo que ha usado (alcance: {etiquetaAlcance(borrador.alcance)})</summary>
      <p className="muted small">{partes.join(" · ")}.</p>
      {historico.length > 0 ? (
        <ul className="asistente-panel-lista">
          {historico.map((h, i) => (
            <li key={`h-${i}`}>
              {String(h["fecha"] ?? "").slice(0, 10)} · {String(h["asunto"] ?? "(sin asunto)")}
              {h["motivo"] ? <span className="muted small"> — {String(h["motivo"])}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {empresa.length > 0 ? (
        <ul className="asistente-panel-lista">
          {empresa.map((e, i) => (
            <li key={`e-${i}`}>
              {String(e["fecha"] ?? "").slice(0, 10)} · {String(e["asunto"] ?? "(sin asunto)")}
              <span className="muted small"> — lo escribió {String(e["quien"] ?? "otra persona de la empresa")}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {maquinas.length > 0 || consumibles.length > 0 ? (
        <ul className="asistente-panel-lista" aria-label="Productos del catálogo que entraron">
          {[...maquinas, ...consumibles].map((p, i) => (
            <li key={`p-${i}`}>
              {String(p.nombre ?? "")}{"marca" in p && p.marca ? ` (${String(p.marca)})` : ""}
              <span className="muted small"> — {p.precio_texto ? `${p.precio_texto}${p.orientativo ? " · orientativo" : ""}` : ("precio_publico" in p && p.precio_publico === false ? "sin precio público (a medida)" : "sin precio publicado")}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {deFuera.soporte || deFuera.sinResultados || deFuera.webs.length > 0 ? (
        <ul className="asistente-panel-lista" aria-label="Material de fuera que entró">
          {deFuera.soporte ? (
            <li>
              Soporte:{" "}
              {deFuera.soporte.url
                ? <a href={deFuera.soporte.url} target="_blank" rel="noreferrer">{deFuera.soporte.titulo}</a>
                : deFuera.soporte.titulo}
              {deFuera.soporte.fuente ? <span className="muted small"> — {deFuera.soporte.fuente}</span> : null}
            </li>
          ) : null}
          {deFuera.sinResultados ? (
            <li>
              Sin procedimiento en el soporte para «{deFuera.sinResultados}»
              <span className="muted small"> — el borrador lo dice, sin pasos</span>
            </li>
          ) : null}
          {deFuera.webs.map((w) => (
            <li key={`w-${w.id}`}>
              Web:{" "}
              {w.url ? <a href={w.url} target="_blank" rel="noreferrer">{w.titulo}</a> : w.titulo}
            </li>
          ))}
        </ul>
      ) : null}
      {borrador.enlaces.length > 0 ? (
        <ul className="asistente-panel-lista">
          {borrador.enlaces.map((e) => (
            <li key={e.url}>
              <a href={e.url} target="_blank" rel="noreferrer">{e.url}</a>
              <span className="muted small"> — {e.etiqueta}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted small">Sin enlaces disponibles para este caso.</p>
      )}
    </details>
  );
}
