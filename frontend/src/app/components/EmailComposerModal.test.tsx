import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EmailComposerModal, cuerpoEscrito } from "./EmailComposerModal";
import {
  type BorradorAsistente,
  createEmailDraft,
  deleteEmailDraft,
  descartarBorradorAsistente,
  type EmailDraft,
  generarBorradorAsistente,
  turnoAsistente,
  updateEmailDraft,
} from "../lib/emailsApi";

// El editor (TinyMCE) va por `next/dynamic`: aquí es un textarea. La
// «selección» del editor se simula con `globalThis.__seleccion`.
type ConSeleccion = { __seleccion?: string };
jest.mock("next/dynamic", () => {
  const React = jest.requireActual("react");
  const FakeEditor = React.forwardRef(function FakeEditor(
    props: { value: string; onChange: (html: string) => void },
    ref: React.Ref<{ clearDraft: () => void; getSelectionHtml: () => string; replaceSelection: (h: string) => string | null }>,
  ) {
    const { value, onChange } = props;
    React.useImperativeHandle(ref, () => ({
      clearDraft: () => undefined,
      getSelectionHtml: () => (globalThis as { __seleccion?: string }).__seleccion ?? "",
      replaceSelection: (html: string) => {
        const sel = (globalThis as { __seleccion?: string }).__seleccion ?? "";
        if (!sel) return null;
        const nuevo = value.replace(sel, html);
        onChange(nuevo);
        return nuevo;
      },
    }), [value, onChange]);
    return React.createElement("textarea", {
      "aria-label": "Cuerpo", value: props.value,
      onChange: (e: { target: { value: string } }) => props.onChange(e.target.value),
    });
  });
  return { __esModule: true, default: () => FakeEditor };
});
jest.mock("../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ id: "u-1", email_include_unsubscribe_default: false })),
  getStoredToken: jest.fn(() => null),
}));
jest.mock("../lib/emailSignaturesApi", () => ({
  listEmailSignatures: jest.fn(() => Promise.resolve([])),
}));
jest.mock("../lib/emailDraftAttachmentsApi", () => ({
  listEmailDraftAttachments: jest.fn(() => Promise.resolve([])),
  uploadEmailDraftAttachment: jest.fn(),
  deleteEmailDraftAttachment: jest.fn(),
}));
jest.mock("../lib/emailsApi", () => ({
  createEmailDraft: jest.fn(),
  updateEmailDraft: jest.fn(),
  deleteEmailDraft: jest.fn(() => Promise.resolve()),
  descartarBorradorAsistente: jest.fn(() => Promise.resolve({})),
  getMyEmailAliases: jest.fn(() => Promise.resolve([
    { send_as_email: "info@bomedia.net", display_name: "Bomedia", is_default: true,
      resolved_display_name: "Bomedia" },
  ])),
  sendEmail: jest.fn(),
  sendEmailDraft: jest.fn(),
  conversacionAsistente: jest.fn(() => Promise.resolve({ turnos: [], actual: null })),
  esDestinatarioInterno: jest.fn(() => Promise.resolve({ direccion: "", interno: false })),
  generarBorradorAsistente: jest.fn(),
  generarBorradorNuevo: jest.fn(),
  turnoAsistente: jest.fn(),
}));
jest.mock("./email/TemplatePicker", () => ({ TemplatePicker: () => null }));
jest.mock("./email/SaveTemplateModal", () => ({ SaveTemplateModal: () => null }));
jest.mock("./email/ScheduleSendDialog", () => ({ ScheduleSendDialog: () => null }));

const mockCreate = createEmailDraft as jest.Mock;
const mockUpdate = updateEmailDraft as jest.Mock;
const mockDelete = deleteEmailDraft as jest.Mock;
const mockDescartar = descartarBorradorAsistente as jest.Mock;
const mockGenerar = generarBorradorAsistente as jest.Mock;
const mockTurno = turnoAsistente as jest.Mock;

function turno(over: Partial<BorradorAsistente> = {}): BorradorAsistente {
  return {
    id: "b-9", thread_id: "t-1", message_id: null, contact_id: "c-1", usuario_id: "u-1",
    idioma: "es", modelo: "claude-haiku-5-5", tokens_entrada: 300, tokens_salida: 80,
    tokens_cache_lectura: 1000, tokens_cache_escritura: 0, coste_usd: 0.0003,
    destinatario: "cliente@ejemplo.com", remitente: "info@bomedia.net", asunto: null,
    destinatario_interno: false, sin_hilo: false, texto: "Hola", html: "<p>Hola</p>",
    sobre_editor: true, seleccion: null, fragmento: false, aviso: null, instruccion: "más formal",
    conversacion_id: "b-9", turno: 0, padre_id: null, pendiente: [], enlaces: [], contexto: {},
    alcance: "contacto", mensajes_hilo: 1, mensajes_historico: 0, estado: "generado",
    email_message_id: null, creado: null, resuelto_en: null, ...over,
  };
}

function borradorGuardado(over: Partial<EmailDraft> = {}): EmailDraft {
  return {
    id: "d-1", user_id: "u-1", thread_id: "t-1", contact_id: "c-1", from_alias: "info@bomedia.net",
    from_name: null, subject: "Re: Máquinas", body_html: "<p>Hola, te paso las máquinas.</p>",
    body_text: null, to_emails: ["cliente@ejemplo.com"], cc_emails: null, bcc_emails: null,
    in_reply_to_message_id: "m-1", signature_id: null, include_unsubscribe: false,
    scheduled_for: null, asistente_borrador_id: "b-7", created_at: "2026-10-11T10:00:00Z",
    updated_at: "2026-10-11T10:05:00Z", ...over,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockCreate.mockResolvedValue({ id: "d-nuevo", updated_at: "2026-10-11T12:00:00Z" });
  mockUpdate.mockResolvedValue({ id: "d-1", updated_at: "2026-10-11T12:00:00Z" });
});

describe("EmailComposerModal — que no destruya trabajo", () => {
  it("en el hilo es un diálogo con velo (la capa y la tarjeta comunes) y el fondo no cierra", async () => {
    const onClose = jest.fn();
    const { container } = render(
      <EmailComposerModal replyTo={{ messageId: "m-1", subject: "Máquinas" }} asistente={null} onClose={onClose} />,
    );
    await screen.findByLabelText("Cuerpo");
    const capa = container.querySelector(".modal-overlay") as HTMLElement;
    expect(capa).not.toBeNull();
    expect(container.querySelector(".modal-dialog.email-compose-modal")).not.toBeNull();
    fireEvent.mouseDown(capa);
    expect(onClose).not.toHaveBeenCalled();
    // En blanco, Esc cierra sin preguntar.
    await userEvent.setup().keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Tienes un borrador sin enviar")).not.toBeInTheDocument();
  });

  it("en el panel derecho es la tarjeta de siempre", async () => {
    const { container } = render(<EmailComposerModal presentacion="panel" asistente={null} onClose={jest.fn()} />);
    await screen.findByLabelText("Cuerpo");
    expect(container.querySelector(".modal-overlay")).toBeNull();
    expect(container.querySelector(".modal-backdrop > .email-compose-modal")).not.toBeNull();
  });

  it("Esc con algo escrito pregunta; «Guardar en Borradores» guarda la foto y cierra", async () => {
    const onClose = jest.fn();
    render(<EmailComposerModal contactEmail="cliente@ejemplo.com" asistente={null} onClose={onClose} />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Cuerpo"), "Hola, te paso el presupuesto.");
    await user.keyboard("{Escape}");
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText("Tienes un borrador sin enviar")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Guardar en Borradores" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledTimes(1);
    const payload = mockCreate.mock.calls[0][0];
    expect(payload.body_html).toBe("Hola, te paso el presupuesto.");
    expect(payload.to_emails).toEqual(["cliente@ejemplo.com"]);
    expect(payload.asistente_borrador_id).toBeNull();
    expect(mockDelete).not.toHaveBeenCalled();
  });

  it("«Cancelar» y el ✕ preguntan igual; «Seguir editando» vuelve sin cerrar", async () => {
    const onClose = jest.fn();
    render(<EmailComposerModal asistente={null} onClose={onClose} />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Cuerpo"), "Texto a medias");
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(screen.getByText("Tienes un borrador sin enviar")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Seguir editando" }));
    expect(screen.queryByText("Tienes un borrador sin enviar")).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Cerrar" }));
    expect(screen.getByText("Tienes un borrador sin enviar")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("«Descartar» borra el borrador guardado, descarta la conversación de la IA y cierra", async () => {
    const onClose = jest.fn();
    render(
      <EmailComposerModal
        initialDraft={borradorGuardado()} presentacion="panel"
        asistente={{ threadId: "t-1", borradorId: "b-7", abierto: false }} onClose={onClose}
      />,
    );
    const user = userEvent.setup();
    await screen.findByLabelText("Cuerpo");
    await user.keyboard("{Escape}");
    expect(screen.getByText("Tienes un borrador sin enviar")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Descartar" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(mockDelete).toHaveBeenCalledWith("d-1");
    expect(mockDescartar).toHaveBeenCalledWith("b-7");
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("un borrador guardado reabierto guarda el turno de la IA con la foto", async () => {
    const onClose = jest.fn();
    render(
      <EmailComposerModal
        initialDraft={borradorGuardado()} presentacion="panel"
        asistente={{ threadId: "t-1", borradorId: "b-7", abierto: false }} onClose={onClose}
      />,
    );
    const user = userEvent.setup();
    await screen.findByLabelText("Cuerpo");
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Guardar en Borradores" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledTimes(1);
    expect(mockUpdate.mock.calls[0][0]).toBe("d-1");
    expect(mockUpdate.mock.calls[0][1]).toMatchObject({
      thread_id: "t-1", asistente_borrador_id: "b-7", body_html: "<p>Hola, te paso las máquinas.</p>",
    });
    expect(mockDescartar).not.toHaveBeenCalled();
  });

  it("Cc y Cco solo ocupan fila si se usan; con contenido vienen abiertos", async () => {
    const { unmount } = render(<EmailComposerModal asistente={null} onClose={jest.fn()} />);
    const user = userEvent.setup();
    await screen.findByLabelText("Cuerpo");
    expect(screen.queryByLabelText("Cc")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Cco")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cc" }));
    expect(screen.getByLabelText("Cc")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cc" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cco" }));
    expect(screen.getByLabelText("Cco")).toBeInTheDocument();
    // Las herramientas van a iconos en la línea del asunto.
    expect(screen.getByRole("group", { name: "Herramientas del correo" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cargar plantilla" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Firma")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Más opciones" }));
    expect(screen.getByLabelText("Firma")).toBeInTheDocument();
    // Un borrador con Cc viene con la fila abierta.
    unmount();
    render(<EmailComposerModal initialDraft={borradorGuardado({ id: "d-2", cc_emails: ["jefe@ejemplo.com"] })} presentacion="panel" asistente={null} onClose={jest.fn()} />);
    expect(await screen.findByLabelText("Cc")).toHaveValue("jefe@ejemplo.com");
  });

  it("una corrección de lo que hay en el editor entra sin preguntar; sobre una selección, va donde estaba y el resto no se toca", async () => {
    const cuerpo = "<p>Hola Marny,</p><p>Te paso los precios.</p><p>Un saludo</p>";
    render(
      <EmailComposerModal
        initialDraft={borradorGuardado({ body_html: cuerpo, asistente_borrador_id: null })} presentacion="panel"
        asistente={{ threadId: "t-1", abierto: true }} onClose={jest.fn()}
      />,
    );
    const user = userEvent.setup();
    const editor = await screen.findByLabelText("Cuerpo");
    expect(editor).toHaveValue(cuerpo);
    // Hay texto: la franja está en modo corrección y manda el texto del editor.
    expect(screen.getByRole("button", { name: /Aplicar/ })).toBeInTheDocument();
    window.confirm = jest.fn(() => false);
    mockGenerar.mockResolvedValueOnce(turno({ html: "<p>Estimada Marny,</p><p>Le paso los precios.</p>" }));
    await user.type(screen.getByLabelText("Qué quieres decirle"), "más formal");
    await user.click(screen.getByRole("button", { name: /Aplicar/ }));
    await waitFor(() => expect(mockGenerar).toHaveBeenCalledTimes(1));
    expect(mockGenerar.mock.calls[0][1]).toMatchObject({ texto_actual_html: cuerpo, seleccion_html: null });
    await waitFor(() => expect(editor).toHaveValue("<p>Estimada Marny,</p><p>Le paso los precios.</p>"));
    expect(window.confirm).not.toHaveBeenCalled();
    // Con una selección en el editor, el fragmento reescrito va donde estaba la selección.
    (globalThis as ConSeleccion).__seleccion = "<p>Le paso los precios.</p>";
    try {
      // Ya hay conversación: la corrección es un turno más, con el texto del editor.
      mockTurno.mockResolvedValueOnce(turno({
        id: "b-10", turno: 1, padre_id: "b-9", instruccion: "desarróllala",
        html: "<p>Estimada Marny,</p><p>Le adjunto los precios de las máquinas de DTF.</p>",
        seleccion: {
          original: "Le paso los precios.", original_html: "<p>Le paso los precios.</p>",
          fragmento_html: "<p>Le adjunto los precios de las máquinas de DTF.</p>", empalmado: true,
        },
      }));
      // La franja se plegó al llegar el borrador: se despliega para corregir.
      await user.click(screen.getByRole("button", { name: /Ayúdame a contestar/ }));
      await user.type(screen.getByLabelText("Qué quieres decirle"), "desarróllala");
      expect(screen.getByRole("note")).toHaveTextContent("Se aplicará solo a la selección: «Le paso los precios.»");
      await user.click(screen.getByRole("button", { name: /Aplicar/ }));
      await waitFor(() => expect(mockTurno).toHaveBeenCalledWith("b-9", "desarróllala", {
        texto_actual_html: "<p>Estimada Marny,</p><p>Le paso los precios.</p>",
        seleccion_html: "<p>Le paso los precios.</p>",
      }));
      await waitFor(() => expect(editor).toHaveValue(
        "<p>Estimada Marny,</p><p>Le adjunto los precios de las máquinas de DTF.</p>"));
    } finally {
      delete (globalThis as ConSeleccion).__seleccion;
    }
  });

  it("una corrección vacía no toca el editor; con otra selección o con el editor cambiado, pregunta antes de pisar", async () => {
    const cuerpo = "<p>Hola Marny,</p><p>Te paso los precios.</p><p>Un saludo</p>";
    render(
      <EmailComposerModal
        initialDraft={borradorGuardado({ body_html: cuerpo, asistente_borrador_id: null })} presentacion="panel"
        asistente={{ threadId: "t-1", abierto: true }} onClose={jest.fn()}
      />,
    );
    const user = userEvent.setup();
    const editor = await screen.findByLabelText("Cuerpo");
    window.confirm = jest.fn(() => false);
    // 1. La guardia vació la corrección: el texto escrito a mano se queda.
    mockGenerar.mockResolvedValueOnce(turno({
      html: "", texto: "", aviso: "La IA no ha redactado un correo (habla de Marny en tercera persona).",
    }));
    await user.type(screen.getByLabelText("Qué quieres decirle"), "más formal");
    await user.click(screen.getByRole("button", { name: /Aplicar/ }));
    expect(await screen.findByText(/habla de Marny en tercera persona/)).toBeInTheDocument();
    expect(editor).toHaveValue(cuerpo);
    expect(window.confirm).not.toHaveBeenCalled();
    // 2. El turno se reescribió sobre OTRA selección: no va a la que hay ahora;
    // el cuerpo entero pisaría lo escrito a mano, así que pregunta (y se dice que no).
    (globalThis as ConSeleccion).__seleccion = "<p>Un saludo</p>";
    try {
      mockTurno.mockResolvedValueOnce(turno({
        id: "b-10", turno: 1, padre_id: "b-9", instruccion: "desarróllala",
        html: "<p>Hola Marny,</p><p>Le adjunto los precios.</p><p>Un saludo</p>",
        seleccion: {
          original: "Te paso los precios.", original_html: "<p>Te paso los precios.</p>",
          fragmento_html: "<p>Le adjunto los precios.</p>", empalmado: true,
        },
      }));
      await user.type(screen.getByLabelText("Qué quieres decirle"), "desarróllala");
      await user.click(screen.getByRole("button", { name: /Aplicar/ }));
      await waitFor(() => expect(mockTurno).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(window.confirm).toHaveBeenCalledTimes(1));
      expect(editor).toHaveValue(cuerpo);
    } finally {
      delete (globalThis as ConSeleccion).__seleccion;
    }
    // 3. La persona sigue escribiendo mientras la IA corrige: no se pisa sin preguntar.
    let soltar: (b: BorradorAsistente) => void = () => undefined;
    mockTurno.mockReturnValueOnce(new Promise<BorradorAsistente>((r) => { soltar = r; }));
    await user.type(screen.getByLabelText("Qué quieres decirle"), "más corto");
    await user.click(screen.getByRole("button", { name: /Aplicar/ }));
    await waitFor(() => expect(mockTurno).toHaveBeenCalledTimes(2));
    fireEvent.change(editor, { target: { value: `${cuerpo}<p>Posdata escrita mientras tanto.</p>` } });
    soltar(turno({ id: "b-11", turno: 2, padre_id: "b-10", instruccion: "más corto", html: "<p>Hola Marny, precios adjuntos.</p>" }));
    await waitFor(() => expect(window.confirm).toHaveBeenCalledTimes(2));
    expect(editor).toHaveValue(`${cuerpo}<p>Posdata escrita mientras tanto.</p>`);
  });

  it("cuerpoEscrito ignora la firma y los párrafos vacíos del editor", () => {
    expect(cuerpoEscrito("<p></p><p>&nbsp;</p>")).toBe("");
    expect(cuerpoEscrito("<p></p><!--crmbo:signature--><p>&mdash;</p><p>Bart</p><!--/crmbo:signature-->")).toBe("");
    expect(cuerpoEscrito("<p>Hola</p><!--crmbo:signature--><p>Bart</p><!--/crmbo:signature-->")).toBe("<p>Hola</p>");
  });
});
