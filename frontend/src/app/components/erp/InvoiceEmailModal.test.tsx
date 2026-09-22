import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { InvoiceEmailModal } from "./InvoiceEmailModal";
import {
  getInvoiceEmailPreview,
  sendInvoiceEmail,
  getEmailSenders,
  type InvoiceEmailPreview,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  getInvoiceEmailPreview: jest.fn(),
  sendInvoiceEmail: jest.fn(),
  getEmailSenders: jest.fn(),
  // PDF_LANGS lo importa el componente desde FactusolDocumentDetailModal, no
  // desde aquí, así que no hace falta mockearlo.
}));
const mockPreview = getInvoiceEmailPreview as jest.Mock;
const mockSend = sendInvoiceEmail as jest.Mock;
const mockSenders = getEmailSenders as jest.Mock;

function preview(over: Partial<InvoiceEmailPreview> = {}): InvoiceEmailPreview {
  return {
    serie: 5,
    codigo: 63,
    numero: "5-000063",
    to: "cliente@ejemplo.fr",
    lang: "fr",
    lang_source: "pais_cliente",
    subject: "Facture 5-000063",
    body_text: "Bonjour,\n\nVeuillez trouver ci-joint votre facture.",
    from_alias: "ventas@bomedia.es",
    from_alias_source: "usuario",
    attachment_filename: "Factura_5-000063.pdf",
    reply_to_message_id: null,
    replies_to_thread: false,
    order_id: "ord-1",
    ...over,
  };
}

beforeEach(() => {
  mockPreview.mockReset();
  mockPreview.mockResolvedValue(preview());
  mockSend.mockReset();
  mockSend.mockResolvedValue({
    sent: true, message_id: "m1", thread_id: "t1",
    to: ["cliente@ejemplo.fr"], lang: "fr", numero: "5-000063",
    attachment_filename: "Factura_5-000063.pdf",
  });
  mockSenders.mockReset();
  mockSenders.mockResolvedValue({ senders: [], available: true, problem: null });
});

describe("InvoiceEmailModal", () => {
  it("carga la previsualización: destinatario, idioma+origen, asunto, cuerpo y adjunto", async () => {
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={jest.fn()} />);
    // destinatario propuesto (editable)
    expect(await screen.findByLabelText("Destinatario")).toHaveValue(
      "cliente@ejemplo.fr",
    );
    // idioma con su procedencia visible
    expect(screen.getByLabelText("Idioma del correo")).toHaveValue("fr");
    expect(screen.getByText(/del país del cliente/i)).toBeInTheDocument();
    // asunto + cuerpo del template
    expect(screen.getByLabelText("Asunto")).toHaveValue("Facture 5-000063");
    expect(screen.getByLabelText("Cuerpo del mensaje")).toHaveValue(
      preview().body_text,
    );
    // adjunto identificado por nombre
    expect(screen.getByText("Factura_5-000063.pdf")).toBeInTheDocument();
  });

  it("el botón de envío es SEPARADO y confirma explícitamente (confirm:true)", async () => {
    const user = userEvent.setup();
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={jest.fn()} />);
    await screen.findByLabelText("Destinatario");
    // Nada se ha enviado con solo abrir la preview (no hay clic-único).
    expect(mockSend).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Enviar factura" }));
    await waitFor(() => expect(mockSend).toHaveBeenCalledTimes(1));
    expect(mockSend).toHaveBeenCalledWith(5, 63, expect.objectContaining({
      confirm: true,
      to: ["cliente@ejemplo.fr"],
      lang: "fr",
      from_alias: "ventas@bomedia.es",
    }));
  });

  it("cambiar el idioma re-traduce asunto/cuerpo conservando el destinatario", async () => {
    const user = userEvent.setup();
    mockPreview.mockImplementation((_s: number, _c: number, lang?: string) =>
      Promise.resolve(lang === "es"
        ? preview({ lang: "es", lang_source: "defecto", subject: "Factura 5-000063",
            body_text: "Hola,\n\nAdjuntamos su factura." })
        : preview()),
    );
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={jest.fn()} />);
    await screen.findByLabelText("Destinatario");
    // el usuario corrige el destinatario ANTES de cambiar idioma
    await user.clear(screen.getByLabelText("Destinatario"));
    await user.type(screen.getByLabelText("Destinatario"), "otro@ejemplo.fr");
    await user.selectOptions(screen.getByLabelText("Idioma del correo"), "es");
    // el asunto/cuerpo se re-traducen al español…
    await waitFor(() =>
      expect(screen.getByLabelText("Asunto")).toHaveValue("Factura 5-000063"));
    expect(screen.getByLabelText("Cuerpo del mensaje")).toHaveValue(
      "Hola,\n\nAdjuntamos su factura.");
    // …pero el destinatario editado NO se pierde
    expect(screen.getByLabelText("Destinatario")).toHaveValue("otro@ejemplo.fr");
  });

  it("tras enviar muestra el éxito y llama onSent", async () => {
    const onSent = jest.fn();
    const user = userEvent.setup();
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={jest.fn()} onSent={onSent} />);
    await screen.findByLabelText("Destinatario");
    await user.click(screen.getByRole("button", { name: "Enviar factura" }));
    expect(await screen.findByText(/Factura enviada a/i)).toBeInTheDocument();
    expect(onSent).toHaveBeenCalledWith({ to: ["cliente@ejemplo.fr"], lang: "fr" });
  });

  it("si el envío falla, enseña el error y NO marca como enviada", async () => {
    mockSend.mockRejectedValue(new Error("Gmail no conectado"));
    const onSent = jest.fn();
    const user = userEvent.setup();
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={jest.fn()} onSent={onSent} />);
    await screen.findByLabelText("Destinatario");
    await user.click(screen.getByRole("button", { name: "Enviar factura" }));
    expect(await screen.findByText(/Gmail no conectado/i)).toBeInTheDocument();
    expect(screen.queryByText(/Factura enviada a/i)).not.toBeInTheDocument();
    expect(onSent).not.toHaveBeenCalled();
    // el botón sigue disponible para reintentar
    expect(screen.getByRole("button", { name: "Enviar factura" })).toBeEnabled();
  });

  it("una dirección inválida deshabilita el envío", async () => {
    const user = userEvent.setup();
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={jest.fn()} />);
    await screen.findByLabelText("Destinatario");
    await user.clear(screen.getByLabelText("Destinatario"));
    await user.type(screen.getByLabelText("Destinatario"), "no-es-un-email");
    expect(screen.getByRole("button", { name: "Enviar factura" })).toBeDisabled();
    expect(screen.getByText(/Revisa las direcciones/i)).toBeInTheDocument();
  });

  it("cancelar llama onClose sin enviar", async () => {
    const onClose = jest.fn();
    const user = userEvent.setup();
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={onClose} />);
    await screen.findByLabelText("Destinatario");
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(onClose).toHaveBeenCalled();
    expect(mockSend).not.toHaveBeenCalled();
  });
});

it("indica que el remitente es el de la TIENDA del pedido cuando así viene", async () => {
  mockPreview.mockResolvedValue(preview({
    from_alias: "tienda@boprint.es", from_alias_source: "tienda", store: "boprint",
  }));
  render(<InvoiceEmailModal serie={5} codigo={63} onClose={() => undefined} />);
  expect(await screen.findByText(/tienda@boprint\.es/)).toBeInTheDocument();
  expect(screen.getByText(/remitente de la tienda boprint/)).toBeInTheDocument();
});

// --- #426: pedido explícito desde la ficha, aviso de remitente, cliente ------

describe("InvoiceEmailModal — ficha de pedido (#426)", () => {
  it("pide la previsualización y envía con el pedido de la ficha (order_id), nunca adivinado", async () => {
    const user = userEvent.setup();
    render(<InvoiceEmailModal serie={5} codigo={63} orderId="ord-escola" onClose={jest.fn()} />);
    await screen.findByLabelText("Destinatario");
    expect(mockPreview).toHaveBeenCalledWith(5, 63, undefined, "ord-escola");
    await user.click(screen.getByRole("button", { name: "Enviar factura" }));
    await waitFor(() => expect(mockSend).toHaveBeenCalledTimes(1));
    expect(mockSend).toHaveBeenCalledWith(5, 63, expect.objectContaining({
      confirm: true, order_id: "ord-escola",
    }));
  });

  it("avisa cuando el remitente propuesto no es un «enviar como» verificado en Gmail", async () => {
    mockPreview.mockResolvedValue(preview({
      from_alias: "pedidos@streamtec.es", from_alias_source: "tienda", store: "fluxlasers",
      from_alias_ok: false, from_alias_problem: "not_in_gmail",
    }));
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={jest.fn()} />);
    await screen.findByLabelText("Destinatario");
    expect(screen.getByRole("alert")).toHaveTextContent(/no es un «enviar como» verificado/);
    expect(screen.getByText(/remitente de la tienda fluxlasers/)).toBeInTheDocument();
  });

  it("avisa cuando el cliente de la factura no coincide con la empresa del pedido", async () => {
    mockPreview.mockResolvedValue(preview({
      customer_mismatch: true, invoice_customer: "NEON LED, S.L.",
    }));
    render(<InvoiceEmailModal serie={5} codigo={63} orderId="ord-escola" onClose={jest.fn()} />);
    await screen.findByLabelText("Destinatario");
    expect(screen.getByRole("alert")).toHaveTextContent(/NEON LED, S.L./);
    expect(screen.getByRole("alert")).toHaveTextContent(/no coincide con la empresa del pedido/);
  });

  it("sin problemas no hay avisos", async () => {
    mockPreview.mockResolvedValue(preview({ from_alias_ok: true, customer_mismatch: false }));
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={jest.fn()} />);
    await screen.findByLabelText("Destinatario");
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

// --- destinatarios = contactos de la empresa --------------------------------

describe("InvoiceEmailModal — contactos de la empresa", () => {
  const contacts = [
    { id: "c1", name: "Ana Compras", email: "ana@cli.com", has_email: true, is_order_contact: true },
    { id: "c2", name: "Beto Admin", email: "beto@cli.com", has_email: true, is_order_contact: false },
    { id: "c3", name: "Ciro Tec", email: null, has_email: false, is_order_contact: false },
  ];

  beforeEach(() => {
    mockPreview.mockResolvedValue(preview({ to: "", company_contacts: contacts }));
  });

  it("lista los contactos; el del pedido va pre-marcado y el sin email deshabilitado", async () => {
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={jest.fn()} />);
    expect(await screen.findByText("Contactos de la empresa")).toBeInTheDocument();
    // El contacto del pedido (Ana) viene marcado por defecto en «Para».
    expect(screen.getByLabelText(/Enviar a Ana Compras/)).toBeChecked();
    // Cada contacto enseña su nombre y su email (fila legible, no fieldset).
    expect(screen.getByText("Ana Compras")).toBeInTheDocument();
    expect(screen.getByText("ana@cli.com")).toBeInTheDocument();
    // El contacto sin email se enseña deshabilitado con aviso.
    expect(screen.getByText("Ciro Tec")).toBeInTheDocument();
    expect(screen.getByText(/sin email/)).toBeInTheDocument();
  });

  it("envía a los contactos marcados repartidos en To y CC", async () => {
    const user = userEvent.setup();
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={jest.fn()} />);
    await screen.findByText("Contactos de la empresa");
    // Marca a Beto (entra en «Para») y lo pasa a CC.
    await user.click(screen.getByLabelText(/Enviar a Beto Admin/));
    const betoGroup = screen.getByRole("group", { name: /Canal de Beto Admin/ });
    await user.click(within(betoGroup).getByRole("button", { name: "CC" }));
    await user.click(screen.getByRole("button", { name: "Enviar factura" }));
    await waitFor(() => expect(mockSend).toHaveBeenCalledTimes(1));
    const payload = mockSend.mock.calls[0][2];
    expect(payload.to).toEqual(["ana@cli.com"]);   // Ana (contacto del pedido)
    expect(payload.cc).toEqual(["beto@cli.com"]);  // Beto movido a CC
  });
});

// --- selector de remitente («Enviar desde») ---------------------------------

describe("InvoiceEmailModal — remitente («Enviar desde»)", () => {
  beforeEach(() => {
    mockSenders.mockResolvedValue({
      senders: [
        { email: "pedidos@streamtec.es", name: "Streamtec", is_primary: false },
        { email: "bart@bomedia.net", name: "Bart", is_primary: true },
      ],
      available: true,
      problem: null,
    });
  });

  it("ofrece los sendAs del Gmail, con el propuesto por defecto, y envía con el elegido", async () => {
    const user = userEvent.setup();
    render(<InvoiceEmailModal serie={5} codigo={63} onClose={jest.fn()} />);
    const select = await screen.findByLabelText("Remitente");
    // Espera a que carguen los sendAs de Gmail.
    await screen.findByRole("option", { name: /bart@bomedia\.net/ });
    // Por defecto, el remitente propuesto por el preview (from_alias).
    expect(select).toHaveValue("ventas@bomedia.es");
    // Cambia el remitente y envía → se usa el elegido como From.
    await user.selectOptions(select, "bart@bomedia.net");
    await user.click(screen.getByRole("button", { name: "Enviar factura" }));
    await waitFor(() => expect(mockSend).toHaveBeenCalledTimes(1));
    expect(mockSend.mock.calls[0][2].from_alias).toBe("bart@bomedia.net");
  });
});
