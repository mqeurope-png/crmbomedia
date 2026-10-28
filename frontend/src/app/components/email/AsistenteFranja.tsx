"use client";

import {
  ChevronDown,
  ChevronRight,
  RotateCcw,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { getCurrentUser } from "../../lib/api";
import {
  ALCANCES_ASISTENTE,
  ALCANCES_SIN_HILO,
  etiquetaAlcance,
  guardarAlcance,
  leerAlcance,
} from "../../lib/asistenteAlcance";
import {
  type FuentesAsistente,
  FUENTES_DE_PARTIDA,
  guardarFuentes,
  leerFuentes,
} from "../../lib/asistenteFuentes";
import type { EstructuraCompuesta } from "../../lib/correoCompuesto";
import {
  type AsistenteAlcance,
  type BomediaSustituida,
  type BorradorAsistente,
  type FormatoAsistente,
  type SobreElEditor,
  conversacionAsistente,
  descartarBorradorAsistente,
  esDestinatarioInterno,
  generarBorradorAsistente,
  generarBorradorNuevo,
  turnoAsistente,
} from "../../lib/emailsApi";
import { extractErrorMessage } from "../../lib/errors";

/** «Ayúdame a contestar» dentro del compositor: una franja propia entre la
 *  cabecera del correo y el cuerpo, que empuja el contenido hacia abajo en vez
 *  de taparlo. De arriba abajo: la caja de instrucción con el selector de
 *  alcance y el botón de redactar; la conversación (lo dicho hasta ahora, con
 *  vuelta atrás); el aviso de la IA; «Lo que ha usado», plegado; y descartar.
 *  En cuanto hay borrador se pliega sola a una línea («1 borrador · 0,0164 $»)
 *  para que el cuerpo tenga el sitio: se despliega al pulsarla. Lo que llega al
 *  editor es siempre un correo: lo que la IA quiera decirle a la persona va
 *  aquí, en el aviso. Un borrador guardado vuelve con su conversación.
 *  Las casillas «Webs» y «Soporte» (marcadas de partida, recordadas por
 *  usuario) dicen qué consulta el asistente SOLO al redactar, además del
 *  catálogo: el índice de nuestras webs y el soporte de los fabricantes, con
 *  lo que pregunta el cliente. Viajan en cada petición; nadie busca ni elige
 *  nada. Lo que trajo, si lo usó y lo que no se pudo consultar se ve en «Lo
 *  que ha usado»; lo que no respondió, también en el aviso. */
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

/** Las casillas de la franja: qué consulta el asistente solo al redactar. */
const FUENTES: { id: keyof FuentesAsistente; label: string; titulo: string }[] = [
  { id: "webs", label: "Webs", titulo: "Consulta el índice de nuestras webs al redactar" },
  { id: "soporte", label: "Soporte", titulo: "Consulta el soporte de los fabricantes al redactar" },
];

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
  // Qué consulta el asistente solo al redactar (nuestras webs, el soporte).
  // Una casilla tocada antes de saber quién es el usuario no se pisa con lo
  // que tenía guardado: se queda como la dejó y se le guarda en cuanto se
  // sabe quién es. Las referencias son lo de ahora mismo, sin esperar a
  // pintar: lo que se ve y quién es.
  const [fuentes, setFuentes] = useState<FuentesAsistente>(FUENTES_DE_PARTIDA);
  const fuentesRef = useRef<FuentesAsistente>(FUENTES_DE_PARTIDA);
  const fuentesTocadas = useRef(new Set<keyof FuentesAsistente>());
  const [usuarioId, setUsuarioId] = useState<string | null>(null);
  const usuarioRef = useRef<string | null>(null);
  const [turnos, setTurnos] = useState<BorradorAsistente[]>([]);
  const [actual, setActual] = useState(0);
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Lo que dice la API de la dirección de «Para» (null = todavía no se ha preguntado).
  const [internoApi, setInternoApi] = useState<boolean | null>(null);
  const cajaRef = useRef<HTMLTextAreaElement>(null);
  const onBorradorActualRef = useRef(onBorradorActual);
  const onEstadoRef = useRef(onEstado);
  useEffect(() => {
    onBorradorActualRef.current = onBorradorActual;
    onEstadoRef.current = onEstado;
  });

  // Qué histórico mira la IA y qué consulta: se recuerda por usuario (en este
  // navegador).
  useEffect(() => {
    getCurrentUser()
      .then((u) => {
        usuarioRef.current = u.id;
        setUsuarioId(u.id);
        const guardado = leerAlcance(u.id);
        if (guardado) setAlcance(guardado);
        // Lo guardado, salvo las casillas que ya ha tocado: esas, como las
        // dejó, y se le guardan (antes no se sabía para quién).
        const tocadas = fuentesTocadas.current;
        const guardadas = leerFuentes(u.id);
        const finales: FuentesAsistente = {
          webs: tocadas.has("webs") ? fuentesRef.current.webs : guardadas.webs,
          soporte: tocadas.has("soporte") ? fuentesRef.current.soporte : guardadas.soporte,
        };
        fuentesRef.current = finales;
        setFuentes(finales);
        if (tocadas.size > 0) guardarFuentes(u.id, finales);
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

  // Una casilla vale para la petición siguiente (no rehace el borrador) y se
  // recuerda para este usuario.
  function cambiarFuente(id: keyof FuentesAsistente, marcada: boolean) {
    const nuevas = { ...fuentesRef.current, [id]: marcada };
    fuentesTocadas.current.add(id);
    fuentesRef.current = nuevas;
    setFuentes(nuevas);
    // Sin usuario todavía no se guarda nada: lo guarda la carga, al llegar.
    guardarFuentes(usuarioRef.current, nuevas);
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
    // El interruptor solo viaja si no es «Auto» (lo de siempre); las casillas,
    // siempre: son lo que el asistente consulta solo en este turno.
    const peticion: SobreElEditor = {
      ...(editor ?? {}), ...(formato !== "auto" ? { formato } : {}), fuentes,
    };
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
        const nuevo = await turnoAsistente(borrador.id, texto, peticion);
        const lista = [...turnos.slice(0, actual + 1), nuevo];
        aplicar(nuevo, lista, lista.length - 1, cambiado());
      } else if (modo === "nuevo") {
        const nuevo = await generarBorradorNuevo({
          destinatario: para, instruccion: texto, alcance: alcanceEfectivo,
          contact_id: contactId, ...peticion,
        });
        aplicar(nuevo, [nuevo], 0, cambiado());
      } else {
        const nuevo = await generarBorradorAsistente(threadId as string, {
          alcance: alcanceEfectivo, instruccion: texto || null, destinatario: para || null,
          ...peticion,
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
      // El borrador rehecho lleva lo mismo que lleva la franja: el formato (si
      // no es «Auto») y las casillas de lo que consulta.
      const extra: SobreElEditor = { ...(formato !== "auto" ? { formato } : {}), fuentes };
      const b = modo === "nuevo"
        ? await generarBorradorNuevo({
          destinatario: para, instruccion: raiz.instruccion ?? "", alcance: nuevo,
          contact_id: contactId, regenerar_de: raiz.id, ...extra,
        })
        : await generarBorradorAsistente(threadId as string, {
          alcance: nuevo, instruccion: raiz.instruccion, destinatario: para || null,
          regenerar_de: raiz.id, ...extra,
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
              {/* Qué consulta el asistente solo al redactar, además del
                  catálogo: van en cada petición y lo que trae se ve en «Lo
                  que ha usado». No rehacen el borrador: valen para la
                  petición siguiente. */}
              <span className="asistente-franja-fuentes" role="group" aria-label="Qué consulta el asistente">
                {FUENTES.map((f) => (
                  <label key={f.id} title={f.titulo}>
                    <input
                      type="checkbox"
                      checked={fuentes[f.id]}
                      disabled={ocupado || bloqueada}
                      onChange={(e) => cambiarFuente(f.id, e.target.checked)}
                    />
                    {f.label}
                  </label>
                ))}
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
    </section>
  );
}

/** Un artículo del soporte de un fabricante que entró en el turno. `usado`:
 *  si el borrador lo usó (null si el modelo no lo dijo o es de antes). */
type ArticuloUsado = { titulo: string; url: string | null; fuente: string; usado: boolean | null };

/** Una página de nuestras webs que entró en el turno. `precio`: el que enseña
 *  la web, si lo hay (nunca es el de una oferta). `ficha`: es un producto del
 *  índice y la IA la puede poner como ficha en un correo compuesto; `pedida`:
 *  quien envía pidió ese producto (por su enlace o por su nombre). */
type PaginaUsada = {
  id: string;
  titulo: string;
  url: string | null;
  dominio: string;
  tipo: string;
  precio: string | null;
  usado: boolean | null;
  ficha: boolean;
  pedida: boolean;
};

/** Por qué un enlace del catálogo no es un producto del índice: una página
 *  del índice («Contacto»), un producto que ya no está, o nada del índice
 *  (puede ser un producto recién publicado que aún no se ha leído). */
type MotivoEnlace = "pagina" | "ido" | "fuera";

/** Una ficha del correo compuesto que sale del catálogo del compositor.
 *  `precioPublico` / `orientativo`: lo que dice el correo en el sitio del
 *  precio («a medida», la nota de orientativo). `enlaceNoProducto`: su enlace,
 *  de una web indexada, no lleva a una página de producto (se avisa; no se
 *  corrige), y por qué. */
type FichaCatalogo = {
  nombre: string;
  enlace: string | null;
  marca: string;
  precio: string | null;
  precioPublico: boolean;
  orientativo: boolean;
  enlaceNoProducto: MotivoEnlace | null;
};

/** Una ficha montada desde una página del índice de las webs. `precio` ya
 *  lleva su IVA si es el de la web. `precioOrigen`: la web, quien envía (lo
 *  dijo en sus instrucciones) o ninguno («a consultar»); null si el contexto
 *  trae un precio sin decir de dónde sale. `imagenes`: las de su galería
 *  (con la principal). */
type FichaIndice = {
  nombre: string;
  enlace: string | null;
  dominio: string;
  precio: string | null;
  precioOrigen: "web" | "usuario" | "consultar" | null;
  idioma: string;
  sinIdiomaPedido: boolean;
  imagenes: number;
};

type FichasDelCorreo = { catalogo: FichaCatalogo[]; indice: FichaIndice[] };

/** Cómo preparó la búsqueda el asistente (el plan). Lo que buscó de verdad va
 *  aparte (`buscadoEnSoporte`, `buscadoEnWebs`): el plan se calcula aunque una
 *  casilla vaya desmarcada o la fuente no llegue a consultarse. */
type ConsultaUsada = {
  tecnica: boolean;
  /** Lo que buscaría en las webs; vacío si el correo no pregunta por ningún producto. */
  webs: string;
  /** La preparó la IA o, si no respondió a tiempo, las palabras del correo. */
  origen: "ia" | "palabras" | null;
  /** La IA falló y la preparó por palabras. */
  fallo: boolean;
  /** Una corrección: reutiliza lo que trajo el primer borrador (y busca solo
   *  lo que ese no miró o no pudo mirar). */
  reutilizada: boolean;
};

type MaterialUsado = {
  /** Lo trajo el asistente solo (las casillas «Webs» y «Soporte»). Sin él, un
   *  borrador de antes: lo eligió la persona con los botones que había. */
  automatico: boolean;
  /** Las casillas con las que se redactó el turno. */
  fuentes: { webs: boolean; soporte: boolean } | null;
  consulta: ConsultaUsada | null;
  soporte: ArticuloUsado | null;
  /** Los otros artículos que salieron: solo título y enlace (no se citan). */
  otrosSoporte: ArticuloUsado[];
  webs: PaginaUsada[];
  /** La búsqueda en el soporte que no encontró ningún procedimiento. */
  sinResultados: string | null;
  /** Lo que no respondió (o no a tiempo), y por qué. */
  noConsultado: { fuente: string; motivo: string }[];
  /** La marca de la máquina, si no tiene centro de ayuda en la lista blanca. */
  sinCentro: string | null;
  /** No hay ningún centro de ayuda activo con búsqueda. */
  sinFuentes: boolean;
  /** Lo que se buscó en el soporte de verdad (en inglés y, si nada, en el idioma del correo). */
  buscadoEnSoporte: string[];
  /** Lo que se buscó en las webs de verdad. */
  buscadoEnWebs: string | null;
  /** La búsqueda en las webs que no encontró nada. */
  websSinResultados: string | null;
  /** Lo que tardó la consulta entera (el plan incluido), en ms. */
  ms: number | null;
  /** Lo que tardó cada fuente, en ms: solo lo hay si se buscó en este turno
   *  (una corrección que reutiliza lo del primer borrador no lo trae). */
  soporteMs: number | null;
  websMs: number | null;
  /** Usó el artículo sin poner su enlace: se añadió al final. */
  enlaceAnadido: boolean;
  /** El borrador trae pasos que no salen de ningún artículo. */
  pasosSinProcedimiento: boolean;
};

function esObjeto(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Solo enlaces web: lo apuntado en el contexto nunca acaba en un `href` raro. */
function urlWeb(v: unknown): string | null {
  return typeof v === "string" && /^https?:\/\//i.test(v) ? v : null;
}

function cadena(v: unknown): string {
  return typeof v === "string" ? v.trim() : "";
}

function usadoDe(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

const TIPO_WEB: Record<string, string> = { pagina: "página", entrada: "entrada", producto: "producto" };

function articuloDe(s: unknown): ArticuloUsado | null {
  if (!esObjeto(s) || !(cadena(s["titulo"]) || cadena(s["url"]))) return null;
  return {
    titulo: cadena(s["titulo"]) || cadena(s["url"]),
    url: urlWeb(s["url"]),
    fuente: cadena(s["fuente_nombre"]) || cadena(s["fuente"]),
    usado: usadoDe(s["usado"]),
  };
}

/** El precio de la web con su IVA: «1.990,00 € (sin IVA)». Si el texto ya
 *  lo dice («489,30 € (+ IVA)»), tal cual: no se repite. */
function conIva(texto: string, iva: unknown): string {
  if (/\b(iva|vat|mwst|tva|btw)\b/i.test(texto)) return texto;
  const sufijo = iva === "sin_iva" ? " (sin IVA)" : iva === "con_iva" ? " (con IVA)" : "";
  return `${texto}${sufijo}`;
}

/** «1.990,00 € (sin IVA)»: lo que dice la web, si da precio. */
function precioWebDe(w: Record<string, unknown>): string | null {
  const texto = cadena(w["precio_web_texto"]);
  return texto ? conIva(texto, w["precio_iva"]) : null;
}

/** Los idiomas de una ficha, como los dice el aviso del borrador. */
const NOMBRE_IDIOMA: Record<string, string> = {
  es: "castellano", en: "inglés", de: "alemán", fr: "francés", nl: "neerlandés",
  pt: "portugués", it: "italiano", ca: "catalán",
};

function nombreIdioma(codigo: string): string {
  return NOMBRE_IDIOMA[codigo.toLowerCase().slice(0, 2)] ?? (codigo || "otro idioma");
}

/** Las fichas que lleva el correo compuesto, con su procedencia, tal como las
 *  apunta el backend en el contexto (`fichas`): las del catálogo (con su marca,
 *  su precio y si su enlace no es un producto de nuestras webs) y las montadas
 *  desde el índice de las webs (con su dominio, de dónde sale su precio, su
 *  idioma y su galería). Al cliente una ficha es una ficha; a quien revisa,
 *  no. Tolerante: sin `fichas` (un correo de texto, un borrador de antes) no
 *  hay nada, y una ficha sin nombre o de procedencia rara se ignora. */
export function fichasDelCorreo(ctx: Record<string, unknown>): FichasDelCorreo {
  const fichas: FichasDelCorreo = { catalogo: [], indice: [] };
  const lista = Array.isArray(ctx["fichas"]) ? ctx["fichas"].filter(esObjeto) : [];
  for (const f of lista) {
    const nombre = cadena(f["nombre"]) || cadena(f["origen_id"]);
    if (!nombre) continue;
    const enlace = urlWeb(f["enlace"]);
    const precio = cadena(f["precio_texto"]) || null;
    if (f["procedencia"] === "catalogo") {
      fichas.catalogo.push({
        nombre, enlace, marca: cadena(f["marca"]), precio,
        precioPublico: f["precio_publico"] !== false,
        orientativo: f["orientativo"] === true,
        enlaceNoProducto: f["enlace_no_producto"] === true ? motivoEnlace(f["enlace_motivo"]) : null,
      });
    } else if (f["procedencia"] === "indice") {
      // Sin importe, «a consultar» (nunca uno inventado); con importe, de
      // donde diga el backend.
      const o = f["precio_origen"];
      const precioOrigen = !precio ? "consultar" : o === "web" || o === "usuario" ? o : null;
      const galeria = Array.isArray(f["galeria"]) ? f["galeria"].map(urlWeb).filter(Boolean) : [];
      fichas.indice.push({
        nombre, enlace,
        dominio: cadena(f["dominio"]) || cadena(f["sitio"]),
        precio: precio && precioOrigen === "web" ? conIva(precio, f["precio_iva"]) : precio,
        precioOrigen,
        idioma: cadena(f["idioma"]),
        sinIdiomaPedido: f["sin_idioma_pedido"] === true,
        imagenes: new Set(galeria).size,
      });
    }
  }
  return fichas;
}

function motivoEnlace(v: unknown): MotivoEnlace {
  return v === "ido" || v === "fuera" ? v : "pagina";
}

/** Lo que se dice de un enlace del catálogo que no es un producto del índice. */
function avisoEnlace(motivo: MotivoEnlace): string {
  if (motivo === "fuera") {
    return "el enlace no está en el índice de las webs: si el producto es nuevo, aún no se ha leído; si no, revísalo en el compositor";
  }
  if (motivo === "ido") return "el enlace es el de un producto que ya no está en la web: revísalo en el compositor";
  return "el enlace no lleva a una página de producto de nuestras webs: revísalo en el compositor";
}

/** Los enlaces del catálogo que, siendo de una web indexada, no llevan a una
 *  página de producto, con el porqué (los apunta el backend con «Webs»
 *  marcada en el turno). */
function enlacesNoProducto(ctx: Record<string, unknown>): Map<string, MotivoEnlace> {
  const lista = Array.isArray(ctx["enlaces_no_producto"]) ? ctx["enlaces_no_producto"].filter(esObjeto) : [];
  return new Map(lista.filter((e) => cadena(e["enlace"])).map((e) => [cadena(e["enlace"]), motivoEnlace(e["motivo"])]));
}

/** El precio de una ficha del catálogo como lo dice el correo. */
function precioDeCatalogo(f: FichaCatalogo): string {
  if (f.precio) return `${f.precio}${f.orientativo ? " · orientativo" : ""}`;
  return f.precioPublico ? "sin precio publicado" : "sin precio público (a medida)";
}

function consultaDe(c: unknown): ConsultaUsada | null {
  if (!esObjeto(c)) return null;
  const origen = c["origen"] === "ia" || c["origen"] === "palabras" ? c["origen"] : null;
  return {
    tecnica: c["tecnica"] === true,
    webs: cadena(c["webs"]),
    origen,
    fallo: Boolean(cadena(c["error"])),
    reutilizada: c["reutilizado"] === true,
  };
}

/** Lo de fuera que entró en el turno, tal como lo apunta el backend en el
 *  contexto (`material`): lo que el asistente consultó solo (`automatico`:
 *  la consulta, el artículo, los otros artículos, las páginas, lo que no
 *  encontró o no pudo consultar) o, en un borrador de antes, lo que eligió la
 *  persona (`soporte`, `webs`, `sin_resultados`). Tolerante: un borrador sin
 *  material no trae nada, y cualquier forma rara se ignora. */
export function materialUsado(ctx: Record<string, unknown>): MaterialUsado {
  const m = esObjeto(ctx["material"]) ? ctx["material"] : ctx;
  const automatico = m["automatico"] === true;
  const f = m["fuentes"];
  const webs = Array.isArray(m["webs"])
    ? m["webs"].filter(esObjeto).map((w, i) => ({
      id: String(w["id"] ?? i),
      titulo: cadena(w["titulo"]) || cadena(w["url"]),
      url: urlWeb(w["url"]),
      dominio: cadena(w["dominio"]) || cadena(w["sitio"]),
      tipo: TIPO_WEB[cadena(w["tipo"])] ?? cadena(w["tipo"]),
      precio: precioWebDe(w),
      usado: usadoDe(w["usado"]),
      ficha: w["ficha"] === true,
      pedida: w["ficha"] === true && w["pedida"] === true,
    })).filter((w) => w.titulo)
    : [];
  const sr = m["sin_resultados"];
  const sinResultados = esObjeto(sr) ? cadena(sr["consulta"]) || null : null;
  return {
    automatico,
    fuentes: automatico && esObjeto(f) ? { webs: f["webs"] === true, soporte: f["soporte"] === true } : null,
    consulta: automatico ? consultaDe(m["consulta"]) : null,
    soporte: articuloDe(m["soporte"]),
    otrosSoporte: Array.isArray(m["otros_soporte"])
      ? m["otros_soporte"].map(articuloDe).filter((a): a is ArticuloUsado => a !== null)
      : [],
    webs,
    sinResultados,
    noConsultado: Array.isArray(m["no_consultado"])
      ? m["no_consultado"].filter(esObjeto)
        .map((n) => ({ fuente: cadena(n["fuente"]), motivo: cadena(n["motivo"]) }))
        .filter((n) => n.fuente)
      : [],
    sinCentro: cadena(m["sin_centro"]) || null,
    sinFuentes: m["sin_fuentes"] === true,
    buscadoEnSoporte: Array.isArray(m["buscado_en_soporte"])
      ? m["buscado_en_soporte"].map(cadena).filter(Boolean)
      : [],
    buscadoEnWebs: cadena(m["buscado_en_webs"]) || null,
    websSinResultados: cadena(m["webs_sin_resultados"]) || null,
    ms: typeof m["ms"] === "number" ? m["ms"] : null,
    soporteMs: typeof m["soporte_ms"] === "number" ? m["soporte_ms"] : null,
    websMs: typeof m["webs_ms"] === "number" ? m["webs_ms"] : null,
    enlaceAnadido: m["enlace_anadido"] === true,
    pasosSinProcedimiento: m["pasos_sin_procedimiento"] === true,
  };
}

/** Un correo técnico con «Soporte» marcada en el que no se buscó nada y no
 *  consta por qué (ni sin centro de ayuda, ni un fallo): se dice igual, que
 *  el soporte no se ha consultado. Nunca se da por buscado. */
function soporteSinBuscar(d: MaterialUsado): boolean {
  return Boolean(d.fuentes?.soporte && d.consulta?.tecnica)
    && d.buscadoEnSoporte.length === 0 && d.soporte === null && d.otrosSoporte.length === 0
    && d.sinResultados === null && d.sinCentro === null && !d.sinFuentes
    && !d.noConsultado.some((n) => n.fuente === "soporte");
}

/** Lo de fuera en la frase de «Lo que ha usado». */
function partesDeFuera(d: MaterialUsado): string[] {
  const partes: string[] = [];
  const plural = (n: number) => (n === 1 ? "" : "s");
  if (!d.automatico) {
    // Un borrador de antes: lo eligió la persona con los botones que había.
    if (d.soporte) partes.push(`el artículo de soporte «${d.soporte.titulo}»${d.soporte.fuente ? ` (${d.soporte.fuente})` : ""}`);
    if (d.sinResultados) partes.push(`la búsqueda «${d.sinResultados}» en el soporte, sin procedimiento`);
    if (d.webs.length > 0) partes.push(`${d.webs.length} página${plural(d.webs.length)} de nuestras webs`);
    return partes;
  }
  if (d.soporte) {
    partes.push(`consultó el soporte${d.soporte.fuente ? ` (${d.soporte.fuente})` : ""}: el artículo «${d.soporte.titulo}»`);
  } else if (d.sinResultados) {
    partes.push(`sin procedimiento en el soporte para «${d.sinResultados}»`);
  }
  if (d.noConsultado.some((n) => n.fuente === "soporte")) partes.push("no se pudo consultar el soporte");
  if (d.sinCentro) partes.push(`${d.sinCentro} no tiene centro de ayuda en la lista`);
  if (soporteSinBuscar(d)) partes.push("no se consultó el soporte");
  if (d.webs.length > 0) {
    partes.push(`${d.webs.length} página${plural(d.webs.length)} de nuestras webs`);
  } else if (d.websSinResultados) {
    partes.push(`webs: nada para «${d.websSinResultados}»`);
  }
  if (d.noConsultado.some((n) => n.fuente === "webs")) partes.push("no se pudieron consultar las webs");
  return partes;
}

/** Lo que dice el título plegado si el asistente consultó algo solo. */
function pistaDeFuera(d: MaterialUsado): string {
  if (!d.automatico) return "";
  const soporte = d.soporte !== null || d.otrosSoporte.length > 0 || d.sinResultados !== null;
  const webs = d.webs.length > 0 || d.websSinResultados !== null;
  return soporte && webs ? "consultó soporte y webs"
    : soporte ? "consultó el soporte"
      : webs ? "consultó las webs" : "";
}

/** «Buscó «…» en el soporte · «…» en las webs — la preparó la IA, en 1,2 s».
 *  Solo lo que se buscó de verdad (lo apunta el backend al buscar): nunca lo
 *  que preparó el plan ni las casillas de ahora, que no dicen que se buscara
 *  (una casilla desmarcada en el primer borrador, una marca sin centro de
 *  ayuda, una fuente que no respondió). */
function busquedaDe(d: MaterialUsado): string {
  const c = d.consulta;
  if (!c) return "";
  const soporte = d.buscadoEnSoporte.length > 0 ? `«${d.buscadoEnSoporte.join(" / ")}» en el soporte` : "";
  const webs = d.buscadoEnWebs ? `«${d.buscadoEnWebs}» en las webs` : "";
  if (!soporte && !webs) return "";
  if (c.reutilizada) {
    // Una corrección reutiliza lo que buscó el primer borrador; lo que ese no
    // miró (su casilla iba desmarcada) o no pudo mirar lo busca ahora, con la
    // búsqueda que se preparó entonces. Lo buscado ahora trae su tiempo, o el
    // fallo de las webs (lo del primer borrador no se arrastra).
    const soporteAhora = Boolean(soporte) && d.soporteMs !== null;
    const websAhora = Boolean(webs)
      && (d.websMs !== null || d.noConsultado.some((n) => n.fuente === "webs"));
    const ahora = [soporteAhora ? soporte : "", websAhora ? webs : ""].filter(Boolean);
    const antes = [soporteAhora ? "" : soporte, websAhora ? "" : webs].filter(Boolean);
    const grupos: string[] = [];
    if (ahora.length > 0) {
      grupos.push(`${ahora.join(" · ")} — en esta corrección, con la búsqueda del primer borrador`);
    }
    if (antes.length > 0) {
      grupos.push(`${antes.join(" · ")} — la del primer borrador: esta corrección no ha vuelto a buscar`);
    }
    return `Buscó ${grupos.join("; ")}`;
  }
  const quien = c.origen === "ia"
    ? "la preparó la IA"
    : c.origen === "palabras"
      ? `con las palabras del correo${c.fallo ? " (la IA no pudo prepararla)" : ""}`
      : "";
  const tiempo = d.ms !== null
    ? `, en ${(d.ms / 1000).toLocaleString("es-ES", { maximumFractionDigits: 1 })} s`
    : "";
  return `Buscó ${[soporte, webs].filter(Boolean).join(" · ")}${quien ? ` — ${quien}` : ""}${tiempo}`;
}

function usoDe(usado: boolean | null): string {
  return usado === null ? "" : usado ? "usado en el borrador" : "no lo usó";
}

function Enlace({ titulo, url }: { titulo: string; url: string | null }) {
  return url ? <a href={url} target="_blank" rel="noreferrer">{titulo}</a> : <>{titulo}</>;
}

/** « — trozo · trozo», en pequeño; nada si no hay trozos. */
function detalle(...trozos: string[]) {
  const texto = trozos.filter(Boolean).join(" · ");
  return texto ? <span className="muted small"> — {texto}</span> : null;
}

/** Lo que hay que mirar en una línea del panel: resaltado, no en pequeño. */
function avisoDe(texto: string) {
  return <span className="asistente-panel-aviso"> — {texto}</span>;
}

/** «2 fichas: 1 del catálogo y 1 del índice». */
function resumenFichas({ catalogo, indice }: FichasDelCorreo): string {
  const n = catalogo.length + indice.length;
  const fichas = `${n} ficha${n === 1 ? "" : "s"}`;
  if (catalogo.length > 0 && indice.length > 0) {
    return `${fichas}: ${catalogo.length} del catálogo y ${indice.length} del índice`;
  }
  return `${fichas} del ${catalogo.length > 0 ? "catálogo" : "índice"}`;
}

/** El precio de una ficha del índice, diciendo siempre de dónde sale: el de
 *  la web (nunca el de una oferta), el que dijo quien envía o «a consultar». */
function precioDeFicha(f: FichaIndice): string {
  if (f.precioOrigen === "consultar") return "precio a consultar (la web no da precio)";
  if (f.precioOrigen === "usuario") {
    return `precio que has indicado tú: ${f.precio} (no sale de la web ni del catálogo)`;
  }
  return f.precioOrigen === "web" ? `precio de la web: ${f.precio}` : `precio: ${f.precio}`;
}

/** Las fichas que lleva el correo, separadas por procedencia y con su enlace:
 *  las del catálogo (precio de venta, revisado) y las montadas desde el
 *  índice de las webs (su precio, su idioma y su galería, para revisarlas
 *  antes de enviar). */
function FichasEnElCorreo({ fichas }: { fichas: FichasDelCorreo }) {
  return (
    <>
      {fichas.catalogo.length > 0 ? (
        <ul className="asistente-panel-lista" aria-label="Fichas del catálogo en el correo">
          {fichas.catalogo.map((f, i) => (
            <li key={`fc-${i}`}>
              Ficha del catálogo: <Enlace titulo={f.nombre} url={f.enlace} />
              {detalle(f.marca, precioDeCatalogo(f))}
              {f.enlaceNoProducto ? avisoDe(avisoEnlace(f.enlaceNoProducto)) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {fichas.indice.length > 0 ? (
        <ul className="asistente-panel-lista" aria-label="Fichas del índice de las webs en el correo">
          {fichas.indice.map((f, i) => (
            <li key={`fi-${i}`}>
              Ficha del índice de {f.dominio || "las webs"}: <Enlace titulo={f.nombre} url={f.enlace} />
              {detalle(
                precioDeFicha(f),
                f.sinIdiomaPedido
                  ? `en ${nombreIdioma(f.idioma)}: no está en el índice en el idioma del correo`
                  : "",
                f.imagenes > 1 ? `${f.imagenes} imágenes en la galería` : "",
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </>
  );
}

/** Lo que el asistente trajo solo de nuestras webs y del soporte, pieza a
 *  pieza: su enlace (solo web), de dónde sale y si el borrador lo usó; lo que
 *  no encontró, no hacía falta o no se pudo consultar; y qué buscó. */
function LoQueTrajo({ d }: { d: MaterialUsado }) {
  const falloSoporte = d.noConsultado.some((n) => n.fuente === "soporte");
  const algoDelSoporte = d.soporte !== null || d.otrosSoporte.length > 0 || d.sinResultados !== null
    || d.sinCentro !== null || d.sinFuentes || falloSoporte;
  const falloWebs = d.noConsultado.some((n) => n.fuente === "webs");
  // Marcada, pero no hacía falta: el correo no es técnico, o no pregunta por
  // ningún producto.
  const soporteSinConsultar = Boolean(d.fuentes?.soporte && d.consulta && !d.consulta.tecnica && !algoDelSoporte);
  const websSinConsultar = Boolean(d.fuentes?.webs && d.consulta && !d.consulta.webs
    && d.webs.length === 0 && !d.websSinResultados && !falloWebs);
  const soporteSinBuscarlo = soporteSinBuscar(d);
  const busqueda = busquedaDe(d);
  if (!algoDelSoporte && !d.pasosSinProcedimiento && !soporteSinConsultar && !soporteSinBuscarlo
      && d.webs.length === 0
      && !d.websSinResultados && !websSinConsultar && d.noConsultado.length === 0 && !busqueda) {
    return null;
  }
  return (
    <ul className="asistente-panel-lista" aria-label="Lo que trajo solo de webs y soporte">
      {d.soporte ? (
        <li>
          Soporte: <Enlace titulo={d.soporte.titulo} url={d.soporte.url} />
          {detalle(d.soporte.fuente, usoDe(d.soporte.usado),
            d.enlaceAnadido ? "su enlace se ha añadido al final del correo" : "")}
        </li>
      ) : null}
      {d.otrosSoporte.map((a, i) => (
        <li key={`s-${i}`}>
          Otro artículo del soporte: <Enlace titulo={a.titulo} url={a.url} />
          {detalle(a.fuente, "solo enlazable", usoDe(a.usado))}
        </li>
      ))}
      {d.sinResultados ? (
        <li>
          Sin procedimiento en el soporte para «{d.sinResultados}»
          {detalle(d.pasosSinProcedimiento ? "pero el borrador trae pasos: revísalo" : "el borrador lo dice, sin pasos")}
        </li>
      ) : d.pasosSinProcedimiento ? (
        <li>
          El borrador trae pasos que no salen de ningún artículo del soporte{detalle("revísalo")}
        </li>
      ) : null}
      {d.sinCentro ? (
        <li>Soporte: {d.sinCentro} no tiene centro de ayuda en la lista{detalle("no se ha consultado")}</li>
      ) : null}
      {d.sinFuentes ? (
        <li>Soporte: no hay ningún centro de ayuda activo en la lista{detalle("no se ha consultado")}</li>
      ) : null}
      {soporteSinConsultar ? (
        <li>Soporte: no se ha consultado{detalle("el correo no es técnico")}</li>
      ) : null}
      {soporteSinBuscarlo ? (
        <li>Soporte: no se ha consultado{detalle("no hay ningún procedimiento del fabricante que citar")}</li>
      ) : null}
      {d.webs.map((w) => (
        <li key={`w-${w.id}`}>
          Web: <Enlace titulo={w.titulo} url={w.url} />
          {detalle(w.dominio, w.tipo, w.precio ? `precio de la web: ${w.precio}` : "",
            w.ficha ? "se puede poner como ficha" : "", w.pedida ? "pedida por quien envía" : "",
            usoDe(w.usado))}
        </li>
      ))}
      {d.websSinResultados ? (
        <li>Webs: nada en el índice para «{d.websSinResultados}»</li>
      ) : null}
      {websSinConsultar ? (
        <li>Webs: no se han consultado{detalle("el correo no pregunta por ningún producto")}</li>
      ) : null}
      {d.noConsultado.map((n, i) => (
        <li key={`n-${i}`}>
          {n.fuente === "webs" ? "No se han podido consultar nuestras webs" : "No se ha podido consultar el soporte"}
          {detalle(n.motivo)}
        </li>
      ))}
      {busqueda ? <li className="muted small">{busqueda}</li> : null}
    </ul>
  );
}

/** Plegado: lo que entró en el contexto (hilo, ficha, correos viejos con fecha,
 *  asunto y motivo, empresa, tokens, lo dejado fuera), las fichas del correo
 *  compuesto (del catálogo o del índice de las webs, con su enlace y de dónde
 *  sale su precio), lo que consultó solo en nuestras webs y en el soporte (y
 *  si lo usó), y los enlaces que podía usar. */
export function LoQueHaUsado({ borrador }: { borrador: BorradorAsistente }) {
  const ctx = borrador.contexto ?? {};
  const num = (k: string): number | null => (typeof ctx[k] === "number" ? (ctx[k] as number) : null);
  const contacto = ctx["contacto"] as { nombre?: string; empresa?: string | null } | null | undefined;
  const historico = Array.isArray(ctx["historico"]) ? (ctx["historico"] as Record<string, unknown>[]) : [];
  const empresa = Array.isArray(ctx["empresa"]) ? (ctx["empresa"] as Record<string, unknown>[]) : [];
  const recortes = Array.isArray(ctx["recortes"]) ? (ctx["recortes"] as string[]) : [];
  const catalogo = (ctx["catalogo"] ?? null) as {
    maquinas?: { nombre?: string; marca?: string; precio_texto?: string | null; orientativo?: boolean;
      precio_publico?: boolean; eleccion?: string; enlace?: string | null }[];
    consumibles?: { nombre?: string; precio_texto?: string | null; orientativo?: boolean;
      enlace?: string | null }[];
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
  // Lo de fuera: lo que el asistente consultó solo para este turno (las
  // casillas) o, en un borrador de antes, lo que eligió la persona.
  const deFuera = materialUsado(ctx);
  partes.push(...partesDeFuera(deFuera));
  const pista = pistaDeFuera(deFuera);
  // Las fichas que lleva el correo compuesto: cuáles del catálogo y cuáles
  // montadas desde el índice de las webs. Las del índice se dicen también
  // plegado: hay que revisar su precio y su descripción antes de enviar.
  const fichas = fichasDelCorreo(ctx);
  if (fichas.catalogo.length + fichas.indice.length > 0) partes.push(resumenFichas(fichas));
  const delIndice = fichas.indice.length;
  const noProducto = enlacesNoProducto(ctx);
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
      <summary>
        Lo que ha usado (alcance: {etiquetaAlcance(borrador.alcance)}){pista ? ` · ${pista}` : ""}
        {delIndice > 0 ? ` · ${delIndice} ficha${delIndice === 1 ? "" : "s"} del índice` : ""}
      </summary>
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
              <Enlace titulo={String(p.nombre ?? "")} url={urlWeb(p.enlace)} />
              {"marca" in p && p.marca ? ` (${String(p.marca)})` : ""}
              <span className="muted small"> — {p.precio_texto ? `${p.precio_texto}${p.orientativo ? " · orientativo" : ""}` : ("precio_publico" in p && p.precio_publico === false ? "sin precio público (a medida)" : "sin precio publicado")}</span>
              {p.enlace && noProducto.has(p.enlace)
                ? avisoDe(noProducto.get(p.enlace) === "fuera"
                  ? "el enlace no está en el índice de las webs"
                  : "el enlace no es una página de producto de nuestras webs")
                : null}
            </li>
          ))}
        </ul>
      ) : null}
      <FichasEnElCorreo fichas={fichas} />
      {deFuera.automatico ? <LoQueTrajo d={deFuera} /> : null}
      {!deFuera.automatico && (deFuera.soporte || deFuera.sinResultados || deFuera.webs.length > 0) ? (
        // Un borrador de antes: lo que la persona eligió con los botones que había.
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
        <ul className="asistente-panel-lista" aria-label="Enlaces que podía usar">
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
