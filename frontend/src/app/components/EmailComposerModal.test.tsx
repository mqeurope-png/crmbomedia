import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EmailComposerModal, cuerpoEscrito } from "./EmailComposerModal";
import {
  createEmailDraft,
  deleteEmailDraft,
  descartarBorradorAsistente,
  type EmailDraft,
  updateEmailDraft,
} from "../lib/emailsApi";

// El editor (TinyMCE) va por `next/dynamic`: aquí es un textarea.
jest.mock("next/dynamic", () => {
  const React = jest.requireActual("react");
  const FakeEditor = React.forwardRef(function FakeEditor(
    props: { value: string; onChange: (html: string) => void },
    ref: React.Ref<{ clearDraft: () => void }>,
  ) {
    React.useImperativeHandle(ref, () => ({ clearDraft: () => undefined }));
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

  it("cuerpoEscrito ignora la firma y los párrafos vacíos del editor", () => {
    expect(cuerpoEscrito("<p></p><p>&nbsp;</p>")).toBe("");
    expect(cuerpoEscrito("<p></p><!--crmbo:signature--><p>&mdash;</p><p>Bart</p><!--/crmbo:signature-->")).toBe("");
    expect(cuerpoEscrito("<p>Hola</p><!--crmbo:signature--><p>Bart</p><!--/crmbo:signature-->")).toBe("<p>Hola</p>");
  });
});
