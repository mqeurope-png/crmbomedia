"use client";

import {
  Ban,
  CalendarClock,
  FolderOpen,
  Paperclip,
  PenLine,
  Save,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { getCurrentUser } from "../lib/api";
import {
  deleteEmailDraftAttachment,
  listEmailDraftAttachments,
  uploadEmailDraftAttachment,
  type EmailDraftAttachment,
} from "../lib/emailDraftAttachmentsApi";
import {
  listEmailSignatures,
  type EmailSignature,
} from "../lib/emailSignaturesApi";
import {
  type CorreoGenerado,
  type EstructuraCompuesta,
  esEstructura,
  estructuraVacia,
  htmlFinalDeVista,
  textoAHtml,
} from "../lib/correoCompuesto";
import {
  createEmailDraft,
  deleteEmailDraft,
  descartarBorradorAsistente,
  getMyEmailAliases,
  renderCorreoCompuesto,
  sendEmail,
  sendEmailDraft,
  type BorradorAsistente,
  type EmailDraft,
  type EmailDraftWrite,
  type EmailMessage,
  type MyAlias,
  updateEmailDraft,
} from "../lib/emailsApi";
import { AsistenteFranja } from "./email/AsistenteFranja";
import { CorreoCompuestoVista } from "./email/CorreoCompuestoVista";
import { extractErrorMessage } from "../lib/errors";
import { Modal } from "./Modal";
import { ModalCloseButton } from "./ModalCloseButton";
import { SaveTemplateModal } from "./email/SaveTemplateModal";
import { ScheduleSendDialog } from "./email/ScheduleSendDialog";
import { useModalBehaviour } from "./useModalBehaviour";
import {
  TemplatePicker,
  type TemplatePickerSelection,
} from "./email/TemplatePicker";

// Gmail's hard cap is 25 MB total per message (body + attachments +
// headers + MIME overhead). We enforce it client-side as a soft cap
// at 25 MB across the attachment binaries; the backend re-checks at
// upload time so a stale tab can't bypass it.
const MAX_ATTACHMENTS_TOTAL_BYTES = 25 * 1024 * 1024;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function splitEmails(raw: string): string[] {
  return raw
    .split(/[,\n;]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

// TinyMCE touches `window` the moment its module loads, so the editor
// must never render on the server. `ssr: false` keeps it in a
// client-only chunk and shows a lightweight placeholder while the
// (~200 KB) editor bundle streams in.
type RichEditorHandle = {
  clearDraft: () => void;
  getSelectionHtml: () => string;
  replaceSelection: (html: string) => string | null;
};
const RichEditor = dynamic(
  () => import("./email/RichEditor").then((m) => m.RichEditor),
  {
    ssr: false,
    loading: () => <div className="re-loading">Cargando editor…</div>,
  },
);

type Props = {
  contactId?: string | null;
  contactEmail?: string | null;
  /** When set, the modal opens in reply mode with the parent
   *  message id passed straight to the backend. */
  replyTo?: { messageId: string; subject?: string | null } | null;
  /** When set, the modal hydrates from the existing draft and
   *  auto-saves under the same id. Send-flow deletes the draft
   *  on success. */
  initialDraft?: EmailDraft | null;
  /** CRM-BANDEJA — «Responder a todos»: Cc prellenado con el resto de
   *  destinatarios del mensaje original. */
  initialCc?: string[] | null;
  /** CRM-BANDEJA — «Reenviar»: subject con prefijo Fwd: y cuerpo con el
   *  mensaje original citado. Mensaje nuevo, sin threading. */
  forwardOf?: { subject: string | null; bodyHtml: string } | null;
  /** «Ayúdame a contestar»: la franja del asistente va DENTRO del compositor,
   *  entre la cabecera y el cuerpo. Siempre está, plegada a una línea; con
   *  `abierto` arranca desplegada con la caja de instrucción. `threadId` es
   *  el hilo al que se responde (sin él, la IA redacta un correo nuevo con el
   *  pasado del destinatario). `borradorId` es el turno que estaba en el editor
   *  cuando se guardó un borrador: la franja recupera su conversación. Al
   *  enviar, el id del turno que está en el editor viaja con el envío para
   *  anotar si salió tal cual o editado. `null` quita la franja (reenviar). */
  asistente?: {
    abierto?: boolean;
    threadId?: string | null;
    /** El hilo dice que la sugerencia de destinatario es un compañero. */
    destinatarioInterno?: boolean;
    borradorId?: string | null;
  } | null;
  /** `dialogo` (por defecto): un diálogo de verdad, centrado, con el velo y
   *  la elevación de los demás modales. `panel`: la tarjeta que llena el panel
   *  derecho de la bandeja, la ficha y Borradores (el padre pone el panel). */
  presentacion?: "dialogo" | "panel";
  onClose: () => void;
  onSent?: (message: EmailMessage) => void;
};

const MERGE_TOKEN_RE = /\{(nombre|empresa|email)\}/;

function hasMergeTokens(text: string): boolean {
  return MERGE_TOKEN_RE.test(text);
}

// HTML comment delimiters around the signature block. We use them to
// find + replace a previously-inserted signature without parsing the
// editor DOM. Avoids accidentally clobbering the operator's own
// content that happens to look like a signature.
const SIG_OPEN = "<!--crmbo:signature-->";
const SIG_CLOSE = "<!--/crmbo:signature-->";
const SIG_BLOCK_RE = new RegExp(
  `${SIG_OPEN}[\\s\\S]*?${SIG_CLOSE}`,
  "g",
);

function buildBodyWithSignature(
  current: string,
  _previousSigId: string,
  next: EmailSignature | null,
): string {
  // Drop any signature block we ourselves inserted earlier. We
  // intentionally don't try to detect raw signatures the operator
  // typed by hand — the markers are how we discriminate.
  const stripped = current.replace(SIG_BLOCK_RE, "").trimEnd();
  if (!next) return stripped;
  const block = `${SIG_OPEN}<p>&mdash;</p>${next.html_content}${SIG_CLOSE}`;
  if (!stripped) return block;
  return `${stripped}<p></p>${block}`;
}

/** El cuerpo sin la firma ni los párrafos vacíos del editor: lo que de verdad
 *  ha escrito alguien (la persona o la IA). */
export function cuerpoEscrito(html: string | null | undefined): string {
  return sinFirma(html)
    .replace(/<p>(?:&nbsp;|\s|&mdash;|—|<br\s*\/?>)*<\/p>/g, "")
    .trim();
}

/** El HTML del editor sin el bloque de firma que puso el compositor: lo que
 *  viaja al asistente como «texto actual» (la firma la vuelve a poner él). */
export function sinFirma(html: string | null | undefined): string {
  return (html || "").replace(SIG_BLOCK_RE, "");
}

export function EmailComposerModal({
  contactId,
  contactEmail,
  replyTo,
  initialDraft,
  initialCc,
  forwardOf,
  asistente,
  presentacion = "dialogo",
  onClose,
  onSent,
}: Props) {
  const [aliases, setAliases] = useState<MyAlias[]>([]);
  const [loadingAliases, setLoadingAliases] = useState(true);
  const [fromAlias, setFromAlias] = useState(initialDraft?.from_alias ?? "");
  const [to, setTo] = useState(
    initialDraft?.to_emails?.join(", ") ?? contactEmail ?? "",
  );
  const [cc, setCc] = useState(
    initialDraft?.cc_emails?.join(", ") ?? initialCc?.join(", ") ?? "",
  );
  const [bcc, setBcc] = useState(initialDraft?.bcc_emails?.join(", ") ?? "");
  // Cc y Cco solo ocupan fila si se usan: si vienen con contenido, abiertos.
  const [mostrarCc, setMostrarCc] = useState(() => cc.trim().length > 0);
  const [mostrarCco, setMostrarCco] = useState(() => bcc.trim().length > 0);
  const [mostrarOpciones, setMostrarOpciones] = useState(false);
  const [subject, setSubject] = useState(
    initialDraft?.subject ??
      (forwardOf
        ? forwardOf.subject
          ? forwardOf.subject.toLowerCase().startsWith("fwd:")
            ? forwardOf.subject
            : `Fwd: ${forwardOf.subject}`
          : "Fwd:"
        : replyTo?.subject
          ? replyTo.subject.toLowerCase().startsWith("re:")
            ? replyTo.subject
            : `Re: ${replyTo.subject}`
          : ""),
  );
  const [bodyHtml, setBodyHtml] = useState(
    initialDraft?.body_html ?? forwardOf?.bodyHtml ?? "",
  );
  // Un correo compuesto (fichas de producto, tira de marca, vídeo, botones):
  // su estructura va en la vista previa en lugar del editor; el HTML lo genera
  // BoHub. null = el correo normal de siempre. `versionVista` sube cuando la
  // estructura cambia por fuera de la vista (un turno de la IA, un bloque
  // movido) y la vista se vuelve a generar.
  const [estructura, setEstructura] = useState<EstructuraCompuesta | null>(() => {
    const guardada = initialDraft?.estructura;
    return esEstructura(guardada) && !estructuraVacia(guardada) ? guardada : null;
  });
  const [versionVista, setVersionVista] = useState(0);
  const [draftId, setDraftId] = useState<string | null>(
    initialDraft?.id ?? null,
  );
  const [draftSavedAt, setDraftSavedAt] = useState<Date | null>(
    initialDraft ? new Date(initialDraft.updated_at) : null,
  );
  const dirtyRef = useRef(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [showPicker, setShowPicker] = useState(false);
  const [showSaveModal, setShowSaveModal] = useState(false);
  const [showScheduleDialog, setShowScheduleDialog] = useState(false);
  // La pregunta de cierre: guardar en Borradores, descartar o seguir editando.
  const [preguntaCierre, setPreguntaCierre] = useState<{ conConversacion: boolean } | null>(null);
  const [cerrando, setCerrando] = useState(false);
  const [signatures, setSignatures] = useState<EmailSignature[]>([]);
  const [activeSignatureId, setActiveSignatureId] = useState<string>(
    initialDraft?.signature_id ?? "",
  );
  const [includeUnsubscribe, setIncludeUnsubscribe] = useState(
    initialDraft?.include_unsubscribe ?? false,
  );
  const [attachments, setAttachments] = useState<EmailDraftAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  // El turno del asistente que está en el editor: viaja con el envío para
  // anotar si salió tal cual o editado, se guarda con el borrador y, si se
  // descarta, se descarta.
  const asistenteIdRef = useRef<string | null>(asistente?.borradorId ?? null);
  // Lo que la franja tiene entre manos: cuenta como contenido al cerrar.
  const franjaEstadoRef = useRef({ instruccion: "", turnos: 0 });
  // El asunto que puso la IA (para no pisar uno escrito a mano) y el de partida.
  const asuntoIaRef = useRef<string | null>(null);
  const asuntoInicialRef = useRef(subject);
  const subjectRef = useRef(subject);
  const bodyHtmlRef = useRef(bodyHtml);
  const estructuraRef = useRef(estructura);
  // El último correo compuesto generado por la vista (para el autoguardado).
  const generadoRef = useRef<CorreoGenerado | null>(null);
  const draftIdRef = useRef<string | null>(initialDraft?.id ?? null);
  useEffect(() => {
    subjectRef.current = subject;
    bodyHtmlRef.current = bodyHtml;
    estructuraRef.current = estructura;
    draftIdRef.current = draftId;
  });
  const fileInputRef = useRef<HTMLInputElement>(null);
  const editorRef = useRef<RichEditorHandle | null>(null);

  // ¿Hay algo que perder? Cuerpo escrito (sin la firma), asunto cambiado, una
  // instrucción a medias en la franja o una conversación con la IA.
  const hayContenido = useCallback((): boolean => {
    if (!estructuraVacia(estructuraRef.current)) return true;
    if (cuerpoEscrito(bodyHtmlRef.current).length > 0) return true;
    if ((subjectRef.current || "").trim() !== asuntoInicialRef.current.trim()) return true;
    const franja = franjaEstadoRef.current;
    return franja.instruccion.trim().length > 0 || franja.turnos > 0;
  }, []);
  // Cerrar sin nada que guardar: una conversación sin cuerpo (no había con qué
  // redactar) queda descartada; nada más que hacer.
  const cerrarDirecto = useCallback(() => {
    if (asistenteIdRef.current) {
      void descartarBorradorAsistente(asistenteIdRef.current).catch(() => undefined);
      asistenteIdRef.current = null;
    }
    onClose();
  }, [onClose]);
  // Esc (solo si el composer es el modal de arriba: no con «Programar
  // envío», «Cargar plantilla»… abiertos encima) y foco de vuelta al cerrar.
  // El fondo NO cierra: un clic fuera no puede destruir un borrador que costó
  // dinero. Esc, el ✕ y «Cancelar» preguntan si hay algo escrito.
  const {
    overlayRef: rootRef,
    overlayProps,
    requestClose,
  } = useModalBehaviour({
    onClose: cerrarDirecto,
    disabled: submitting || cerrando,
    confirmClose: () => {
      if (hayContenido()) {
        setPreguntaCierre({ conConversacion: franjaEstadoRef.current.turnos > 0 });
        return false;
      }
      return true;
    },
    closeOnOverlayClick: false,
  });

  const firmaActiva = useCallback(
    (): EmailSignature | null => signatures.find((s) => s.id === activeSignatureId) ?? null,
    [signatures, activeSignatureId],
  );
  // Lo que había en el editor antes del asistente (para volver a ello al
  // empezar de nuevo o descartar) y lo último que puso la IA (para saber si la
  // persona escribió o editó algo a mano, que no se pisa sin preguntar). Con
  // un correo compuesto, lo mismo con su estructura.
  const cuerpoAntesRef = useRef<string | null>(null);
  const estructuraAntesRef = useRef<EstructuraCompuesta | null>(null);
  const cuerpoIaRef = useRef<string | null>(null);
  const estructuraIaRef = useRef<string | null>(null);
  const escritoAMano = useCallback((): boolean => {
    if (estructuraRef.current !== null) {
      return JSON.stringify(estructuraRef.current) !== estructuraIaRef.current;
    }
    const actual = bodyHtmlRef.current || "";
    return cuerpoEscrito(actual).length > 0 && actual !== cuerpoIaRef.current;
  }, []);
  const guardarAntes = useCallback(() => {
    if (cuerpoAntesRef.current !== null) return;
    cuerpoAntesRef.current = bodyHtmlRef.current || "";
    estructuraAntesRef.current = estructuraRef.current;
  }, []);
  // Pone (o quita, con null) un correo compuesto en el compositor.
  const ponerEstructura = useCallback((e: EstructuraCompuesta | null) => {
    estructuraRef.current = e;
    setEstructura(e);
    if (e) setVersionVista((v) => v + 1);
    else generadoRef.current = null;
  }, []);
  // Llega un turno del asistente: su cuerpo sustituye al del editor, con la
  // firma activa detrás (la IA no firma; la firma la pone el compositor). Un
  // cuerpo vacío (no había con qué redactar) deja solo la firma. Si en el
  // editor hay algo escrito a mano que la IA no ha corregido, se pregunta
  // antes de pisarlo; una corrección de lo que había (`sobre_editor`) entra
  // sin preguntar si el editor sigue como cuando se pidió (si cambió mientras
  // la IA trabajaba, o es un turno anterior que se vuelve a elegir, se
  // pregunta), y una corrección VACÍA (la guardia la vació) no toca el editor.
  // Un turno sobre una selección va donde está la selección, si es LA MISMA
  // sobre la que se reescribió, y el resto no se toca; si no, y el turno trae
  // el cuerpo entero, va entero; si no, no se puede poner (false). El asunto
  // de la IA solo pisa el que está vacío o el que puso ella misma.
  const aplicarBorrador = useCallback(
    (b: BorradorAsistente, opciones?: { editorCambiado?: boolean }): boolean => {
      if (b.modo === "compuesto") {
        // Un correo compuesto va a la vista previa. Sin bloques (la guardia o
        // el interruptor lo dejaron vacío) no se toca nada: el aviso lo explica.
        if (estructuraVacia(b.estructura)) return false;
        const sinPreguntar = b.sobre_editor && !opciones?.editorCambiado;
        if (!sinPreguntar && escritoAMano() && !window.confirm(
          "Ya hay un correo escrito o retocado en el editor. ¿Reemplazarlo con el de la IA?",
        )) {
          return false;
        }
        guardarAntes();
        const nueva = b.estructura as EstructuraCompuesta;
        estructuraIaRef.current = JSON.stringify(nueva);
        ponerEstructura(nueva);
        if (b.asunto) {
          const asunto = b.asunto;
          setSubject((prev) => (!prev.trim() || prev === asuntoIaRef.current ? asunto : prev));
          asuntoIaRef.current = asunto;
        }
        dirtyRef.current = true;
        return true;
      }
      if (b.seleccion && estructuraRef.current === null) {
        // Un fragmento vacío (la guardia lo vació): el editor no se toca, el aviso lo explica.
        if (!b.seleccion.fragmento_html) return false;
        // Solo sobre la MISMA selección (si no se guardó, por larga, la que haya).
        const viva = editorRef.current?.getSelectionHtml() ?? "";
        const original = b.seleccion.original_html;
        const misma = Boolean(viva) && (original == null || viva === original);
        const html = misma
          ? (editorRef.current?.replaceSelection(b.seleccion.fragmento_html) ?? null)
          : null;
        if (html !== null) {
          guardarAntes();
          cuerpoIaRef.current = html;
          setBodyHtml(html);
          dirtyRef.current = true;
          return true;
        }
      }
      if (b.fragmento) return false;
      if (b.sobre_editor && !cuerpoEscrito(b.html)) return false;
      const sinPreguntar = b.sobre_editor && !b.seleccion && !opciones?.editorCambiado;
      if (!sinPreguntar && escritoAMano() && !window.confirm(
        "Ya hay texto en el editor. ¿Reemplazarlo con el borrador de la IA?",
      )) {
        return false;
      }
      guardarAntes();
      const siguiente = buildBodyWithSignature(b.html || "", "", firmaActiva());
      cuerpoIaRef.current = siguiente;
      setBodyHtml(siguiente);
      // Un correo normal: si había un compuesto en la vista previa, se va.
      if (estructuraRef.current !== null) ponerEstructura(null);
      if (b.asunto) {
        const asunto = b.asunto;
        setSubject((prev) => (!prev.trim() || prev === asuntoIaRef.current ? asunto : prev));
        asuntoIaRef.current = asunto;
      }
      dirtyRef.current = true;
      return true;
    },
    [firmaActiva, escritoAMano, guardarAntes, ponerEstructura],
  );
  // Lo que la franja manda al asistente como «texto actual»: el cuerpo sin la
  // firma, y la selección del editor si la hay (con un compuesto, nada: va su
  // estructura).
  const cuerpoParaAsistente = useCallback(
    () => (estructuraRef.current !== null ? { html: "", seleccion: "" } : {
      html: sinFirma(bodyHtmlRef.current),
      seleccion: editorRef.current?.getSelectionHtml() ?? "",
    }),
    [],
  );
  const estructuraParaAsistente = useCallback(() => estructuraRef.current, []);
  const hayTexto = cuerpoEscrito(bodyHtml).length > 0 || !estructuraVacia(estructura);
  const vaciarBorrador = useCallback(() => {
    // Vuelve a lo que había antes del asistente (o solo la firma).
    const antes = cuerpoAntesRef.current;
    const siguiente = antes !== null ? antes : buildBodyWithSignature("", "", firmaActiva());
    const estructuraAntes = antes !== null ? estructuraAntesRef.current : null;
    cuerpoAntesRef.current = null;
    estructuraAntesRef.current = null;
    cuerpoIaRef.current = null;
    estructuraIaRef.current = null;
    ponerEstructura(estructuraAntes);
    setBodyHtml(siguiente);
    if (asuntoIaRef.current) {
      const inicial = asuntoInicialRef.current;
      setSubject((prev) => (prev === asuntoIaRef.current ? inicial : prev));
      asuntoIaRef.current = null;
    }
    dirtyRef.current = true;
  }, [firmaActiva, ponerEstructura]);

  // «Pasar a texto»: el compuesto sale de la vista previa y su texto plano
  // (sin fichas, vídeo ni botones) va al editor normal, con la firma.
  const pasarATexto = useCallback((texto: string) => {
    if (!window.confirm(
      "El correo pasa a texto normal en el editor: se quitan las fichas, la tira de marca, "
      + "el vídeo y los botones. ¿Seguir?",
    )) {
      return;
    }
    const siguiente = buildBodyWithSignature(textoAHtml(texto), "", firmaActiva());
    cuerpoIaRef.current = siguiente;
    ponerEstructura(null);
    setBodyHtml(siguiente);
    dirtyRef.current = true;
  }, [firmaActiva, ponerEstructura]);

  // Lo que se guarda en Borradores: una foto del compositor, incluido el
  // turno del asistente que está en el editor (la conversación vuelve al
  // reabrir) y el hilo al que se responde.
  const threadId = asistente?.threadId ?? initialDraft?.thread_id ?? null;
  const payloadBorrador = useCallback((): EmailDraftWrite => ({
    thread_id: threadId,
    contact_id: contactId ?? null,
    from_alias: fromAlias || null,
    subject: subject || null,
    // Un compuesto: su estructura y el último correo generado (lo que enseña
    // la lista de Borradores); al reabrir vuelve a la vista previa.
    ...(estructura !== null
      ? {
        body_html: generadoRef.current ? htmlFinalDeVista(generadoRef.current.html) : null,
        body_text: generadoRef.current?.texto ?? null,
        estructura,
      }
      : { body_html: bodyHtml || null }),
    to_emails: splitEmails(to),
    cc_emails: cc.trim() ? splitEmails(cc) : null,
    bcc_emails: bcc.trim() ? splitEmails(bcc) : null,
    in_reply_to_message_id: replyTo?.messageId ?? null,
    signature_id: activeSignatureId || null,
    include_unsubscribe: includeUnsubscribe,
    asistente_borrador_id: asistenteIdRef.current,
  }), [
    threadId, contactId, fromAlias, subject, bodyHtml, estructura, to, cc, bcc,
    replyTo?.messageId, activeSignatureId, includeUnsubscribe,
  ]);

  // Auto-save every 5s when the operator has changed something.
  // The dirty flag is a ref so typing doesn't re-render the
  // effect dependencies; we read the latest values inside the
  // timer.
  useEffect(() => {
    const timer = window.setInterval(async () => {
      if (!dirtyRef.current) return;
      const payload = payloadBorrador();
      try {
        if (draftId) {
          const fresh = await updateEmailDraft(draftId, payload);
          setDraftSavedAt(new Date(fresh.updated_at));
        } else {
          const fresh = await createEmailDraft(payload);
          setDraftId(fresh.id);
          setDraftSavedAt(new Date(fresh.updated_at));
        }
        dirtyRef.current = false;
      } catch {
        /* keep dirty; retry on next tick */
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [payloadBorrador, draftId]);

  // draftKey isolates the autosave entries per conversation. Replies
  // carry the parent gmail message id (one per thread by construction);
  // fresh composes use the contact id, falling back to "new" when
  // composing from /emails. Without enough uniqueness here a draft
  // from contact A's send leaks into contact B's next compose because
  // TinyMCE keys off the same {path}{query} on /contacts/:id.
  const draftKey = replyTo
    ? `reply-${replyTo.messageId}`
    : `compose-${contactId ?? "new"}`;

  useEffect(() => {
    getMyEmailAliases()
      .then((items) => {
        setAliases(items);
        const def = items.find((a) => a.is_default) ?? items[0];
        if (def) setFromAlias(def.send_as_email);
      })
      .catch(() => setAliases([]))
      .finally(() => setLoadingAliases(false));
  }, []);

  // Load signatures + auto-insert the default at mount. Guarded with
  // `inserted` so a re-render (the body state mutating) doesn't keep
  // re-inserting the signature on every keystroke. Sin predeterminada pero con
  // una sola firma, esa: que el borrador salga firmado con quien lo pide.
  useEffect(() => {
    let inserted = false;
    listEmailSignatures()
      .then((rows) => {
        setSignatures(rows);
        const def = rows.find((s) => s.is_default) ?? (rows.length === 1 ? rows[0] : undefined);
        if (def && !inserted) {
          inserted = true;
          setActiveSignatureId(def.id);
          setBodyHtml((prev) => buildBodyWithSignature(prev, "", def));
        }
      })
      .catch(() => {
        /* signatures are optional; never block the modal */
      });
  }, []);

  // Sprint Email v2.5 — A. Pull existing attachments for a hydrated
  // draft. New composes start with an empty list; the row gets
  // created the first time the operator drops a file.
  useEffect(() => {
    if (!initialDraft?.id) return;
    listEmailDraftAttachments(initialDraft.id)
      .then(setAttachments)
      .catch(() => setAttachments([]));
  }, [initialDraft?.id]);

  // Seed the unsubscribe toggle from the operator's stored default.
  // Reads /api/auth/me's `email_include_unsubscribe_default`, which is
  // the same hydrated user the rest of the app uses — no extra round
  // trip on every send.
  useEffect(() => {
    getCurrentUser()
      .then((u) => {
        if (u.email_include_unsubscribe_default) setIncludeUnsubscribe(true);
      })
      .catch(() => {
        /* falls back to the unchecked default */
      });
  }, []);

  // The draft row is needed before we can attach a binary (the
  // attachment FK points at email_drafts.id). Auto-save normally
  // creates it on the 5 s tick; uploading a file forces it now so
  // the operator doesn't lose the binary while typing.
  async function ensureDraft(): Promise<string> {
    if (draftIdRef.current) return draftIdRef.current;
    const created = await createEmailDraft(payloadBorrador());
    setDraftId(created.id);
    setDraftSavedAt(new Date(created.updated_at));
    draftIdRef.current = created.id;
    dirtyRef.current = false;
    return created.id;
  }

  async function handleFilesPicked(files: FileList | File[]) {
    const list = Array.from(files);
    if (list.length === 0) return;
    const currentTotal = attachments.reduce(
      (sum, a) => sum + a.size_bytes,
      0,
    );
    const incomingTotal = list.reduce((sum, f) => sum + f.size, 0);
    if (currentTotal + incomingTotal > MAX_ATTACHMENTS_TOTAL_BYTES) {
      setError(
        `Gmail no acepta más de 25 MB de adjuntos por mensaje. Llevas ${formatBytes(
          currentTotal,
        )} y estás añadiendo ${formatBytes(incomingTotal)}.`,
      );
      return;
    }
    setError(null);
    setUploading(true);
    try {
      const id = await ensureDraft();
      for (const file of list) {
        const created = await uploadEmailDraftAttachment(id, file);
        setAttachments((prev) => [...prev, created]);
      }
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo subir el adjunto."));
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function handleRemoveAttachment(attachmentId: string) {
    if (!draftId) return;
    try {
      await deleteEmailDraftAttachment(draftId, attachmentId);
      setAttachments((prev) => prev.filter((a) => a.id !== attachmentId));
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar el adjunto."));
    }
  }

  function applyTemplate(selection: TemplatePickerSelection) {
    // QoL: si el operador ya tiene contenido en el editor, pedimos
    // confirmación antes de sobreescribirlo. Comparamos contra el
    // signature de "editor recién inicializado" (TinyMCE emite `<p></p>`
    // o cadena vacía) para no preguntar si solo hay placeholder.
    const stripped = (bodyHtml || "")
      .replace(/<p>(?:&nbsp;|\s)*<\/p>/g, "")
      .trim();
    if (
      (stripped.length > 0 || estructura !== null) &&
      !window.confirm(
        "Ya hay contenido en el editor. ¿Reemplazar con la plantilla seleccionada?",
      )
    ) {
      return;
    }
    setShowPicker(false);
    // Una plantilla es un correo normal: un compuesto en la vista previa se va.
    ponerEstructura(null);
    setBodyHtml(selection.body_html);
    dirtyRef.current = true;
    if (selection.subject) {
      setSubject((prev) => prev || selection.subject || "");
    }
  }

  async function handleSubmit(
    event: React.FormEvent,
    scheduledFor: string | null = null,
  ) {
    event.preventDefault();
    if (submitting) return;
    if (!fromAlias) {
      setError(
        "Conecta Google con permisos Gmail desde /account antes de enviar.",
      );
      return;
    }
    const toList = splitEmails(to);
    if (toList.length === 0) {
      setError("Añade al menos un destinatario en el campo Para.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      // Subject-change ⇒ new thread. Gmail's send API also requires
      // the Subject to match for chaining; if the operator typed a
      // different subject we honour that intent by stripping the
      // in_reply_to id so the server doesn't try to thread.
      const normalise = (s: string) =>
        s.replace(/^re:\s*/i, "").trim().toLowerCase();
      const subjectChanged =
        replyTo != null &&
        normalise(subject) !== normalise(replyTo.subject ?? "");
      const replyMessageId = subjectChanged
        ? null
        : (replyTo?.messageId ?? null);

      // Un correo compuesto sale con el correo que genera BoHub a partir de su
      // estructura (el mismo de la vista previa, con la firma) y su texto
      // plano; uno normal, con el HTML del editor, como siempre.
      let cuerpoHtml: string | null = bodyHtml.trim() || null;
      let cuerpoTexto: string | null = null;
      if (estructuraRef.current !== null) {
        const final = await renderCorreoCompuesto({
          estructura: estructuraRef.current, asunto: subject,
          firma_html: firmaActiva()?.html_content ?? null,
        });
        cuerpoHtml = final.html;
        cuerpoTexto = final.texto;
      }
      let message: EmailMessage;
      // Sprint Email v2.5 — A. Cuando hay adjuntos, el binario vive
      // en email_draft_attachments y solo lo materializa el send
      // route a través de `send_draft`. Por eso forzamos primero un
      // flush del estado actual al draft (PUT) y luego llamamos al
      // endpoint que lee los attachments + send. Sin adjuntos
      // seguimos por el atajo `sendEmail` para no introducir latency
      // al flujo histórico.
      if (attachments.length > 0) {
        const id = await ensureDraft();
        const payload: EmailDraftWrite = {
          ...payloadBorrador(),
          from_alias: fromAlias,
          subject,
          body_html: cuerpoHtml,
          body_text: cuerpoTexto,
          to_emails: toList,
          in_reply_to_message_id: replyMessageId,
          scheduled_for: scheduledFor,
        };
        await updateEmailDraft(id, payload);
        // Con adjuntos el envío sale del borrador guardado; el id del
        // borrador de la IA viaja igual para anotar su resultado.
        message = await sendEmailDraft(id, {
          asistente_borrador_id: asistenteIdRef.current,
        });
        // sendEmailDraft borra la row en éxito → no llamar deleteEmailDraft.
        setDraftId(null);
        draftIdRef.current = null;
      } else {
        message = await sendEmail({
          from_alias: fromAlias,
          to: toList,
          cc: cc.trim() ? splitEmails(cc) : null,
          bcc: bcc.trim() ? splitEmails(bcc) : null,
          subject,
          body_html: cuerpoHtml,
          body_text: cuerpoTexto,
          contact_id: contactId ?? null,
          in_reply_to_message_id: replyMessageId,
          include_unsubscribe: includeUnsubscribe,
          scheduled_for: scheduledFor,
          // «Ayúdame a contestar»: solo para anotar el resultado del
          // borrador (tal cual / editado). El envío es el de siempre.
          asistente_borrador_id: asistenteIdRef.current,
        });
        // v2.4d — the draft row only mirrored the in-flight compose;
        // once the message is actually sent (or scheduled), the
        // operator doesn't expect to see it under Borradores anymore.
        if (draftId) {
          await deleteEmailDraft(draftId).catch(() => undefined);
          setDraftId(null);
          draftIdRef.current = null;
        }
      }
      // El envío resolvió el borrador de la IA: ya no hay nada que descartar.
      asistenteIdRef.current = null;
      // Wipe the autosave entry for this conversation BEFORE handing
      // control back to the parent so a quick "compose another"
      // doesn't restore the just-sent body. We deliberately do NOT
      // clear on Cancel — the operator may want to come back to the
      // half-written reply later.
      editorRef.current?.clearDraft();
      onSent?.(message);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo enviar el email."));
      setSubmitting(false);
    }
  }

  // «Guardar en Borradores»: una foto del compositor ahora mismo, con el
  // turno del asistente; la conversación sigue viva y vuelve al reabrir.
  async function guardarYCerrar() {
    setCerrando(true);
    setError(null);
    try {
      const payload = payloadBorrador();
      if (draftIdRef.current) await updateEmailDraft(draftIdRef.current, payload);
      else await createEmailDraft(payload);
      dirtyRef.current = false;
      setPreguntaCierre(null);
      asistenteIdRef.current = null;
      onClose();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo guardar el borrador."));
      setCerrando(false);
    }
  }

  // «Descartar»: fuera el borrador guardado, la conversación con la IA y el
  // autoguardado del editor.
  async function descartarYCerrar() {
    setCerrando(true);
    if (draftIdRef.current) {
      await deleteEmailDraft(draftIdRef.current).catch(() => undefined);
      draftIdRef.current = null;
    }
    if (asistenteIdRef.current) {
      void descartarBorradorAsistente(asistenteIdRef.current).catch(() => undefined);
      asistenteIdRef.current = null;
    }
    editorRef.current?.clearDraft();
    setPreguntaCierre(null);
    onClose();
  }

  async function handleDiscard() {
    if (!confirm("¿Descartar el borrador? Se perderá el contenido.")) return;
    await descartarYCerrar();
  }

  const hasMerge =
    hasMergeTokens(estructura !== null ? JSON.stringify(estructura) : bodyHtml)
    || hasMergeTokens(subject);
  const firmaHtml = firmaActiva()?.html_content ?? null;
  const dialogo = presentacion === "dialogo";
  const ocupado = submitting || cerrando;

  const tarjeta = (
    <div
      className={dialogo ? "modal-dialog email-compose-modal" : "modal modal-wide email-compose-modal"}
      role="dialog"
      aria-modal="true"
      aria-labelledby="composer-title"
    >
      <header className="composer-header">
        <h2 id="composer-title">
          {asistente?.abierto
            ? (replyTo ? "Responder con ayuda de la IA" : "Nuevo email con ayuda de la IA")
            : replyTo
              ? "Responder"
              : "Nuevo email"}
        </h2>
        <ComposerDraftStatus
          savedAt={draftSavedAt}
          hasDraft={draftId !== null}
        />
        <ModalCloseButton onClose={requestClose} disabled={ocupado} placement="header" />
      </header>
      {error ? <p className="form-error">{error}</p> : null}
      {!loadingAliases && aliases.length === 0 ? (
        <p className="form-warning">
          No has marcado ningún alias en{" "}
          <Link href="/account">/account</Link>. Marca al menos uno para
          enviar emails desde el CRM.
        </p>
      ) : null}
      <form onSubmit={handleSubmit}>
        {aliases.length === 1 ? (
          <p className="muted small email-compose-de">
            Enviando desde:{" "}
            <strong>
              {aliases[0].resolved_display_name
                ? `${aliases[0].resolved_display_name} <${aliases[0].send_as_email}>`
                : aliases[0].send_as_email}
            </strong>
          </p>
        ) : (
          <label className="field">
            De
            <select
              value={fromAlias}
              onChange={(e) => {
                setFromAlias(e.target.value);
                dirtyRef.current = true;
              }}
              disabled={loadingAliases || aliases.length === 0}
            >
              {aliases.map((a) => (
                <option key={a.send_as_email} value={a.send_as_email}>
                  {a.resolved_display_name
                    ? `${a.resolved_display_name} <${a.send_as_email}>`
                    : a.send_as_email}
                  {a.is_default ? " (por defecto)" : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="email-compose-para">
          <label className="field email-compose-para-campo">
            Para
            <input
              type="text"
              value={to}
              onChange={(e) => {
                setTo(e.target.value);
                dirtyRef.current = true;
              }}
              placeholder="email@dominio.com, otro@dominio.com"
            />
          </label>
          {!mostrarCc || !mostrarCco ? (
            <span className="email-compose-cc-toggles">
              {!mostrarCc ? (
                <button type="button" onClick={() => setMostrarCc(true)} title="Añadir Cc">Cc</button>
              ) : null}
              {!mostrarCco ? (
                <button type="button" onClick={() => setMostrarCco(true)} title="Añadir Cco">Cco</button>
              ) : null}
            </span>
          ) : null}
        </div>
        {mostrarCc ? (
          <label className="field">
            Cc
            <input
              type="text"
              value={cc}
              onChange={(e) => {
                setCc(e.target.value);
                dirtyRef.current = true;
              }}
              placeholder="opcional"
            />
          </label>
        ) : null}
        {mostrarCco ? (
          <label className="field">
            Cco
            <input
              type="text"
              value={bcc}
              onChange={(e) => {
                setBcc(e.target.value);
                dirtyRef.current = true;
              }}
              placeholder="opcional"
            />
          </label>
        ) : null}
        <div className="email-compose-asunto-fila">
          <label className="field email-compose-asunto">
            Asunto
            <input
              type="text"
              value={subject}
              onChange={(e) => {
                setSubject(e.target.value);
                dirtyRef.current = true;
              }}
              maxLength={500}
            />
          </label>
          <div className="email-compose-iconos" role="group" aria-label="Herramientas del correo">
            <button
              type="button"
              className="icon-button"
              aria-label="Cargar plantilla"
              title="Cargar plantilla"
              onClick={() => setShowPicker(true)}
            >
              <FolderOpen size={15} aria-hidden />
            </button>
            <button
              type="button"
              className="icon-button"
              aria-label="Guardar como plantilla"
              onClick={() => setShowSaveModal(true)}
              disabled={!bodyHtml.trim() || estructura !== null}
              title={
                estructura !== null
                  ? "Las plantillas son de correo normal: pasa el correo a texto para guardarlo."
                  : bodyHtml.trim()
                    ? "Guardar como plantilla"
                    : "Escribe algo antes de guardar la plantilla."
              }
            >
              <Save size={15} aria-hidden />
            </button>
            <button
              type="button"
              className="icon-button"
              aria-label="Adjuntar archivo"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              title={uploading ? "Subiendo…" : "Adjuntar archivo (máx. 25 MB en total)"}
            >
              <Paperclip size={15} aria-hidden />
            </button>
            <button
              type="button"
              className={`icon-button${mostrarOpciones ? " is-activo" : ""}`}
              aria-label="Más opciones"
              aria-expanded={mostrarOpciones}
              title="Firma y opción de baja"
              onClick={() => setMostrarOpciones((v) => !v)}
            >
              <SlidersHorizontal size={15} aria-hidden />
            </button>
          </div>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files) void handleFilesPicked(e.target.files);
          }}
        />
        {mostrarOpciones ? (
          <div className="email-compose-opciones">
            <label className="email-compose-signature">
              <span className="email-compose-signature-label">
                <PenLine size={12} aria-hidden /> Firma
              </span>
              <select
                aria-label="Firma"
                value={activeSignatureId}
                onChange={(e) => {
                  const nextId = e.target.value;
                  const next =
                    signatures.find((s) => s.id === nextId) ?? null;
                  setActiveSignatureId(nextId);
                  setBodyHtml((prev) =>
                    buildBodyWithSignature(prev, activeSignatureId, next),
                  );
                  dirtyRef.current = true;
                }}
                disabled={signatures.length === 0}
                title={
                  signatures.length === 0
                    ? "Crea una firma en /account/firmas"
                    : undefined
                }
              >
                <option value="">Sin firma</option>
                {signatures.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                    {s.is_default ? " (predeterminada)" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label
              className="email-compose-unsubscribe"
              title={
                includeUnsubscribe
                  ? "El email incluirá el enlace y la cabecera List-Unsubscribe."
                  : "Recomendado para mailings / newsletters. Para 1-a-1, déjalo apagado."
              }
            >
              <input
                type="checkbox"
                checked={includeUnsubscribe}
                onChange={(e) => {
                  setIncludeUnsubscribe(e.target.checked);
                  dirtyRef.current = true;
                }}
              />
              <span className="email-compose-unsubscribe-label">
                <Ban size={12} aria-hidden /> Incluir opción de baja
              </span>
            </label>
          </div>
        ) : null}

        {asistente !== null ? (
          <AsistenteFranja
            threadId={asistente?.threadId ?? null}
            contactId={contactId ?? null}
            destinatario={to}
            destinatarioInterno={asistente?.destinatarioInterno}
            abiertaInicial={Boolean(asistente?.abierto)}
            bloqueada={ocupado}
            borradorInicialId={asistente?.borradorId ?? null}
            hayTexto={hayTexto}
            obtenerCuerpo={cuerpoParaAsistente}
            obtenerEstructura={estructuraParaAsistente}
            onBorrador={aplicarBorrador}
            onVaciar={vaciarBorrador}
            onBorradorActual={(id) => {
              asistenteIdRef.current = id;
              dirtyRef.current = true;
            }}
            onEstado={(estado) => {
              franjaEstadoRef.current = estado;
            }}
          />
        ) : null}

        <div
          className={`field email-compose-body${dragging ? " dragging" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            if (e.dataTransfer.files.length > 0) {
              void handleFilesPicked(e.dataTransfer.files);
            }
          }}
        >
          {estructura !== null ? (
            <CorreoCompuestoVista
              estructura={estructura}
              version={versionVista}
              asunto={subject}
              firmaHtml={firmaHtml}
              bloqueada={ocupado}
              onEditar={(e) => {
                estructuraRef.current = e;
                setEstructura(e);
                dirtyRef.current = true;
              }}
              onCambiar={(e) => {
                ponerEstructura(e);
                dirtyRef.current = true;
              }}
              onPasarATexto={pasarATexto}
              onGenerado={(correo) => {
                generadoRef.current = correo;
              }}
            />
          ) : (
            <label>
              Cuerpo
              <RichEditor
                ref={editorRef}
                value={bodyHtml}
                onChange={(html) => {
                  setBodyHtml(html);
                  dirtyRef.current = true;
                }}
                placeholder="Escribe tu email. Usa {nombre}, {empresa}, {email} para personalizar."
                minHeight={376}
                fill
                draftKey={draftKey}
              />
            </label>
          )}
          {dragging ? (
            <div className="email-compose-dropzone" aria-hidden>
              Suelta para adjuntar
            </div>
          ) : null}
        </div>

        {attachments.length > 0 ? (
          <div className="email-compose-attachments">
            <p className="muted small">
              Adjuntos ({attachments.length}) ·{" "}
              {formatBytes(
                attachments.reduce((s, a) => s + a.size_bytes, 0),
              )}{" "}
              de 25 MB
            </p>
            <ul className="email-compose-attachments-list">
              {attachments.map((a) => (
                <li key={a.id}>
                  <Paperclip size={11} aria-hidden />
                  <span className="email-compose-attachment-name">
                    {a.filename}
                  </span>
                  <span className="muted small">
                    {formatBytes(a.size_bytes)}
                  </span>
                  <button
                    type="button"
                    className="icon-button"
                    onClick={() => handleRemoveAttachment(a.id)}
                    title="Quitar adjunto"
                    aria-label={`Quitar ${a.filename}`}
                  >
                    <X size={12} aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {hasMerge ? (
          <p className="email-merge-hint">
            <Sparkles size={12} aria-hidden /> El email contiene{" "}
            <code>{"{nombre}"}</code>/<code>{"{empresa}"}</code>/
            <code>{"{email}"}</code>. Se reemplazarán al enviar con los
            datos del contacto destinatario.
          </p>
        ) : null}

        <div className="actions">
          <button
            type="button"
            className="button secondary"
            onClick={requestClose}
            disabled={ocupado}
          >
            Cancelar
          </button>
          {draftId ? (
            <button
              type="button"
              className="button secondary"
              onClick={handleDiscard}
              disabled={ocupado}
            >
              <Trash2 size={11} aria-hidden /> Descartar borrador
            </button>
          ) : null}
          <button
            type="button"
            className="button secondary"
            onClick={() => setShowScheduleDialog(true)}
            disabled={ocupado || !fromAlias}
          >
            <CalendarClock size={11} aria-hidden /> Programar envío
          </button>
          <button
            type="submit"
            className="button"
            disabled={ocupado || !fromAlias}
          >
            {submitting ? "Enviando…" : "Enviar ahora"}
          </button>
        </div>
      </form>
    </div>
  );

  const anidados = (
    <>
      {showPicker ? (
        <TemplatePicker
          onSelect={applyTemplate}
          onClose={() => setShowPicker(false)}
        />
      ) : null}
      {showSaveModal ? (
        <SaveTemplateModal
          bodyHtml={bodyHtml}
          subject={subject}
          onClose={() => setShowSaveModal(false)}
          onSaved={() => {
            /* no-op: SaveTemplateModal closes itself */
          }}
        />
      ) : null}
      <ScheduleSendDialog
        open={showScheduleDialog}
        onClose={() => setShowScheduleDialog(false)}
        onSchedule={(iso) => {
          setShowScheduleDialog(false);
          // Fake a form-submit event to reuse the same validation
          // path; the second arg routes the call through the
          // backend's pending-message branch.
          void handleSubmit(
            { preventDefault: () => undefined } as unknown as React.FormEvent,
            iso,
          );
        }}
      />
      <CerrarDialogo
        abierto={preguntaCierre !== null}
        ocupado={cerrando}
        conConversacion={preguntaCierre?.conConversacion ?? false}
        onGuardar={() => void guardarYCerrar()}
        onDescartar={() => void descartarYCerrar()}
        onSeguir={() => setPreguntaCierre(null)}
      />
    </>
  );

  // Como diálogo: la capa común de los modales (velo, elevación) y la tarjeta
  // común. Como panel: la tarjeta que llena el panel derecho que pone el padre.
  if (dialogo) {
    return (
      <div className="modal-overlay email-compose-capa" role="presentation" {...overlayProps}>
        {tarjeta}
        {anidados}
      </div>
    );
  }
  return (
    <div className="modal-backdrop" role="presentation" ref={rootRef}>
      {tarjeta}
      {anidados}
    </div>
  );
}

/** Al cerrar con algo escrito: guardar en Borradores (lo que menos daño
 *  hace, y por eso es lo primero), descartar o seguir editando. Esc o el ✕ de
 *  esta pregunta es «seguir editando». */
function CerrarDialogo({
  abierto, ocupado, conConversacion, onGuardar, onDescartar, onSeguir,
}: {
  abierto: boolean;
  ocupado: boolean;
  conConversacion: boolean;
  onGuardar: () => void;
  onDescartar: () => void;
  onSeguir: () => void;
}) {
  return (
    <Modal open={abierto} onClose={onSeguir} title="Tienes un borrador sin enviar" size="small">
      <p>
        ¿Guardarlo en Borradores, descartarlo o seguir editando?
        {conConversacion ? " Si lo guardas, la conversación con la IA se guarda con él." : ""}
      </p>
      <div className="modal-actions">
        <button type="button" className="button" onClick={onGuardar} disabled={ocupado} autoFocus>
          {ocupado ? "Guardando…" : "Guardar en Borradores"}
        </button>
        <button type="button" className="button secondary" onClick={onSeguir} disabled={ocupado}>
          Seguir editando
        </button>
        <button type="button" className="button secondary is-danger" onClick={onDescartar} disabled={ocupado}>
          Descartar
        </button>
      </div>
    </Modal>
  );
}

/** "Guardado hace Xs" indicator in the composer header. Re-renders
 *  every 15 s so the relative timestamp stays current without the
 *  parent paying a full composer re-render. */
function ComposerDraftStatus({
  savedAt,
  hasDraft,
}: {
  savedAt: Date | null;
  hasDraft: boolean;
}) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!savedAt) return;
    const timer = window.setInterval(() => setTick((n) => n + 1), 15000);
    return () => window.clearInterval(timer);
  }, [savedAt]);

  if (!hasDraft || !savedAt) return null;
  const secs = Math.max(0, Math.floor((Date.now() - savedAt.getTime()) / 1000));
  let label: string;
  if (secs < 10) label = "Guardado";
  else if (secs < 60) label = `Guardado hace ${secs}s`;
  else if (secs < 3600) {
    label = `Guardado hace ${Math.floor(secs / 60)} min`;
  } else {
    label = `Guardado hace ${Math.floor(secs / 3600)} h`;
  }
  return <span className="composer-draft-status">{label}</span>;
}
