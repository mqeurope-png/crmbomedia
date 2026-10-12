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
import type { EstructuraCompuesta } from "../../lib/correoCompuesto";
import {
  type AsistenteAlcance,
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
 *  aquí, en el aviso. Un borrador guardado vuelve con su conversación. */
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
    // El interruptor solo viaja si no es «Auto» (lo de siempre).
    const peticion: SobreElEditor | null = formato !== "auto" ? { ...(editor ?? {}), formato } : editor;
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
      const conFormato = formato !== "auto" ? { formato } : {};
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
  partes.push(contacto ? `la ficha de ${contacto.nombre || "el contacto"}${contacto.empresa ? ` (${contacto.empresa})` : ""}` : "sin ficha (destinatario desconocido)");
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
