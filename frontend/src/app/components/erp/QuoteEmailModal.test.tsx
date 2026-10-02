import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QuoteEmailModal, emailedMark } from "./QuoteEmailModal";
import {
  getEmailSenders,
  getQuoteEmailPreview,
  searchCrmContacts,
  sendQuoteEmail,
  type QuoteEmailPreview,
} from "../../lib/erpApi";

/** Punto A — enviar el presupuesto / proforma por email con el molde del
 *  modal de la factura: previsualización obligatoria, contactos premarcados,
 *  idioma con procedencia, asunto/cuerpo editables, remitente de la serie y
 *  adjunto; el envío es un botón aparte con `confirm: true`. */

jest.mock("../../lib/erpApi", () => ({
  getQuoteEmailPreview: jest.fn(),
  sendQuoteEmail: jest.fn(),
  getEmailSenders: jest.fn(),
  searchCrmContacts: jest.fn(),
}));
const mockSearch = searchCrmContacts as jest.Mock;
const mockPreview = getQuoteEmailPreview as jest.Mock;
const mockSend = sendQuoteEmail as jest.Mock;
const mockSenders = getEmailSenders as jest.Mock;

function preview(over: Partial<QuoteEmailPreview> = {}): QuoteEmailPreview {
  return {
    serie: 2, codpre: 75, numero: "2-000075", to: "marta@maison.example",
    lang: "fr", lang_source: "cliente",
    subject: "Devis 2-000075",
    body_text: "Bonjour Marta Coll,\n\nVeuillez trouver ci-joint le devis 2-000075.",
    from_alias: "info@artisjet-printers.eu", from_alias_source: "serie",
    from_alias_ok: true, from_alias_problem: null,
    attachment_filename: "Presupuesto-2-000075.pdf", variant: null, currency: "EUR",
    company_id: "c1", company_name: "La Maison de la Plaque",
    order_id: null,
    company_contacts: [
      { id: "k1", name: "Marta Coll", email: "marta@maison.example", has_email: true,
        is_order_contact: false, is_primary: true },
      { id: "k2", name: "Jean Dupont", email: "jean@maison.example", has_email: true,
        is_order_contact: false, is_primary: false },
      { id: "k3", name: "Sin Email", email: null, has_email: false,
        is_order_contact: false, is_primary: false },
    ],
    ...over,
  };
}

beforeEach(() => {
  mockPreview.mockReset();
  mockPreview.mockResolvedValue(preview());
  mockSend.mockReset();
  mockSend.mockResolvedValue({
    sent: true, message_id: "m1", thread_id: "t1", to: ["marta@maison.example"],
    lang: "fr", numero: "2-000075", attachment_filename: "Presupuesto-2-000075.pdf",
  });
  mockSenders.mockReset();
  mockSenders.mockResolvedValue({ senders: [], available: true, problem: null });
  mockSearch.mockReset();
  mockSearch.mockResolvedValue([]);
});

describe("QuoteEmailModal", () => {
  it("carga la previsualización: contacto vinculado premarcado, idioma con procedencia, asunto, cuerpo, remitente de la serie y adjunto", async () => {
    render(<QuoteEmailModal codpre="75" serie={2} numero="2-000075" onClose={jest.fn()} />);
    expect(await screen.findByRole("heading", { name: /Enviar presupuesto por email/ })).toBeInTheDocument();
    await waitFor(() => expect(mockPreview).toHaveBeenCalledWith("75", 2, expect.objectContaining({})));
    // El contacto de la cabecera va premarcado en «Para»; el otro no; sin email, deshabilitado.
    expect(await screen.findByLabelText("Enviar a Marta Coll (marta@maison.example)")).toBeChecked();
    expect(screen.getByLabelText("Enviar a Jean Dupont (jean@maison.example)")).not.toBeChecked();
    expect(screen.getByLabelText("Idioma del correo")).toHaveValue("fr");
    expect(screen.getByText(/Idioma del cliente/)).toBeInTheDocument();
    expect(screen.getByLabelText("Asunto")).toHaveValue("Devis 2-000075");
    expect(screen.getByLabelText("Cuerpo del mensaje")).toHaveValue(preview().body_text);
    expect(screen.getByText("Presupuesto-2-000075.pdf")).toBeInTheDocument();
    expect(screen.getByText(/de la empresa emisora de la serie/)).toBeInTheDocument();
    expect(mockSend).not.toHaveBeenCalled();                 // abrir no envía nada
  });

  it("envía con confirmación explícita al contacto premarcado, con el tipo/moneda/banco del PDF, y avisa del resultado", async () => {
    const onSent = jest.fn();
    const user = userEvent.setup();
    render(<QuoteEmailModal codpre="75" serie={2} variant="proforma" currency="USD" bank={1}
                            onClose={jest.fn()} onSent={onSent} />);
    await screen.findByLabelText("Enviar a Marta Coll (marta@maison.example)");
    expect(mockPreview).toHaveBeenCalledWith("75", 2, expect.objectContaining({ variant: "proforma", currency: "USD" }));
    await user.click(screen.getByRole("button", { name: "Enviar proforma" }));
    await waitFor(() => expect(mockSend).toHaveBeenCalledTimes(1));
    expect(mockSend).toHaveBeenCalledWith("75", 2, expect.objectContaining({
      confirm: true, to: ["marta@maison.example"], cc: [], lang: "fr",
      from_alias: "info@artisjet-printers.eu", variant: "proforma", currency: "USD", bank: 1,
    }));
    expect(await screen.findByRole("status")).toHaveTextContent("Proforma enviada a marta@maison.example en FR.");
    expect(onSent).toHaveBeenCalledWith({ to: ["marta@maison.example"], lang: "fr" });
  });

  it("sin destinatarios no se puede enviar; una dirección libre vale", async () => {
    mockPreview.mockResolvedValue(preview({ company_contacts: [], company_id: null, to: "" }));
    const user = userEvent.setup();
    render(<QuoteEmailModal codpre="75" serie={2} onClose={jest.fn()} />);
    const boton = await screen.findByRole("button", { name: "Enviar presupuesto" });
    expect(boton).toBeDisabled();
    expect(screen.getByText(/Elige al menos un destinatario/)).toBeInTheDocument();
    expect(screen.getByText(/Este cliente no está vinculado a una empresa del CRM/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Destinatario"), "libre@ejemplo.com");
    expect(boton).toBeEnabled();
    await user.click(boton);
    await waitFor(() => expect(mockSend).toHaveBeenCalledWith("75", 2, expect.objectContaining({ to: ["libre@ejemplo.com"] })));
  });

  it("cambiar el idioma re-traduce asunto y cuerpo conservando los destinatarios", async () => {
    const user = userEvent.setup();
    render(<QuoteEmailModal codpre="75" serie={2} onClose={jest.fn()} />);
    await screen.findByLabelText("Enviar a Marta Coll (marta@maison.example)");
    mockPreview.mockResolvedValue(preview({ lang: "de", lang_source: "selector", subject: "Angebot 2-000075" }));
    await user.selectOptions(screen.getByLabelText("Idioma del correo"), "de");
    await waitFor(() => expect(mockPreview).toHaveBeenLastCalledWith("75", 2, expect.objectContaining({ lang: "de" })));
    await waitFor(() => expect(screen.getByLabelText("Asunto")).toHaveValue("Angebot 2-000075"));
    expect(screen.getByLabelText("Enviar a Marta Coll (marta@maison.example)")).toBeChecked();
    expect(screen.getByText("Idioma elegido a mano.")).toBeInTheDocument();
  });

  it("si el envío falla lo dice y NO marca como enviado; un remitente no utilizable se avisa antes", async () => {
    mockSend.mockRejectedValue(new Error("Gmail no conectado"));
    const user = userEvent.setup();
    render(<QuoteEmailModal codpre="75" serie={2} onClose={jest.fn()} />);
    await screen.findByLabelText("Enviar a Marta Coll (marta@maison.example)");
    await user.click(screen.getByRole("button", { name: "Enviar presupuesto" }));
    expect(await screen.findByText("Gmail no conectado")).toBeInTheDocument();
    expect(screen.queryByText(/Presupuesto enviado/)).toBeNull();

    mockPreview.mockResolvedValue(preview({ from_alias_ok: false, from_alias_problem: "sin_alias" }));
    render(<QuoteEmailModal codpre="76" serie={1} onClose={jest.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/No hay remitente configurado/);
  });

  it("emailedMark: «Enviada dd/mm» con los destinatarios en el título; null si nunca se envió", () => {
    expect(emailedMark(null, null)).toBeNull();
    const mark = emailedMark("2026-10-02T10:15:00", ["a@x.com", "b@x.com"]);
    expect(mark).toEqual({ label: "Enviada 02/10", title: "Enviada por email el 02/10/2026 a a@x.com, b@x.com" });
  });
});


describe("QuoteEmailModal — buscar cualquier contacto del CRM (remates · punto 3)", () => {
  const eduard = {
    id: "c-eduard", name: "Eduard Riera", email: "eduard@riera.example",
    company_name: "Riera Contijoch SL",
  };

  it("cliente sin vincular: avisa en una línea y el buscador añade el contacto a «Para» con nombre y email", async () => {
    mockPreview.mockResolvedValue(preview({
      company_contacts: [], company_id: null, company_name: null, to: "",
      customer_name: "EDUARD RIERA CONTIJOCH", contacto_id: null,
    }));
    mockSearch.mockResolvedValue([eduard]);
    const user = userEvent.setup();
    render(<QuoteEmailModal codpre="4358" serie={1} onClose={jest.fn()} />);
    expect(await screen.findByText(
      /Este cliente no está vinculado a una empresa del CRM \(«EDUARD RIERA CONTIJOCH»\)/,
    )).toBeInTheDocument();
    await user.type(screen.getByLabelText("Buscar contacto del CRM"), "eduard");
    await waitFor(() => expect(mockSearch).toHaveBeenCalledWith("eduard"));
    const hits = await screen.findByRole("list", { name: "Contactos del CRM encontrados" });
    expect(hits).toHaveTextContent("Eduard Riera");
    expect(hits).toHaveTextContent("Riera Contijoch SL");
    expect(hits).toHaveTextContent("eduard@riera.example");
    await user.click(screen.getByRole("button", { name: "Añadir a Eduard Riera en Para" }));
    const picks = screen.getByRole("list", { name: "Contactos del CRM añadidos" });
    expect(picks).toHaveTextContent("Eduard Riera");
    expect(picks).toHaveTextContent("eduard@riera.example");
    // El saludo pasa a ser el del contacto elegido (la previsualización se rehace).
    await waitFor(() => expect(mockPreview).toHaveBeenLastCalledWith(
      "4358", 1, expect.objectContaining({ contact_id: "c-eduard" }),
    ));
    await waitFor(() => expect(screen.getByRole("button", { name: "Enviar presupuesto" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Enviar presupuesto" }));
    await waitFor(() => expect(mockSend).toHaveBeenCalledWith("4358", 1, expect.objectContaining({
      to: ["eduard@riera.example"], cc: [],
    })));
  });

  it("también con empresa vinculada: un contacto de otra empresa va a CC y se puede quitar", async () => {
    mockSearch.mockResolvedValue([eduard]);
    const user = userEvent.setup();
    render(<QuoteEmailModal codpre="75" serie={2} onClose={jest.fn()} />);
    await screen.findByLabelText("Enviar a Marta Coll (marta@maison.example)");
    expect(screen.queryByText(/no está vinculado/)).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Buscar contacto del CRM"), "riera");
    await user.click(await screen.findByRole("button", { name: "Añadir a Eduard Riera en CC" }));
    await user.click(screen.getByRole("button", { name: "Enviar presupuesto" }));
    await waitFor(() => expect(mockSend).toHaveBeenCalledWith("75", 2, expect.objectContaining({
      to: ["marta@maison.example"], cc: ["eduard@riera.example"],
    })));
  });

  it("quitar un contacto añadido lo saca de los destinatarios", async () => {
    mockSearch.mockResolvedValue([eduard]);
    const user = userEvent.setup();
    render(<QuoteEmailModal codpre="75" serie={2} onClose={jest.fn()} />);
    await screen.findByLabelText("Enviar a Marta Coll (marta@maison.example)");
    await user.type(screen.getByLabelText("Buscar contacto del CRM"), "riera");
    await user.click(await screen.findByRole("button", { name: "Añadir a Eduard Riera en CC" }));
    await user.click(screen.getByRole("button", { name: "Quitar a Eduard Riera" }));
    expect(screen.queryByRole("list", { name: "Contactos del CRM añadidos" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Enviar presupuesto" }));
    await waitFor(() => expect(mockSend).toHaveBeenCalledWith("75", 2, expect.objectContaining({
      to: ["marta@maison.example"], cc: [],
    })));
  });
});


describe("QuoteEmailModal — los contactos del CRM añadidos se ven y son los que se envían", () => {
  const contacto = (i: number) => ({
    id: `c${i}`, name: `ACEVEDO RUIBAL, FAUSTINO ${i}`, email: `f${i}@crm.example`,
    company_name: `Empresa ${i}`,
  });

  it("cuatro contactos añadidos: los cuatro en la lista y en el envío; «Quitar» lo saca de los dos", async () => {
    const user = userEvent.setup();
    mockPreview.mockResolvedValue(preview({
      // Mensaje largo: el caso real en el que el modal llega a su alto máximo.
      body_text: Array.from({ length: 60 }, (_, i) => `Línea ${i}`).join("\n"),
    }));
    render(<QuoteEmailModal codpre="75" serie={2} onClose={jest.fn()} />);
    await screen.findByLabelText("Enviar a Marta Coll (marta@maison.example)");
    for (const i of [1, 2, 3, 4]) {
      mockSearch.mockResolvedValue([contacto(i)]);
      await user.type(screen.getByLabelText("Buscar contacto del CRM"), "acevedo");
      await user.click(await screen.findByRole("button", { name: `Añadir a ${contacto(i).name} en Para` }));
    }
    const lista = screen.getByRole("list", { name: "Contactos del CRM añadidos" });
    expect(lista).toHaveClass("erp-contacts-list", "erp-crm-picks");
    expect(within(lista).getAllByRole("listitem")).toHaveLength(4);
    for (const i of [1, 2, 3, 4]) {
      expect(within(lista).getByText(contacto(i).name)).toBeInTheDocument();
      expect(within(lista).getByText(contacto(i).email)).toBeInTheDocument();
    }
    await user.click(screen.getByRole("button", { name: `Quitar a ${contacto(2).name}` }));
    expect(within(lista).getAllByRole("listitem")).toHaveLength(3);
    expect(within(lista).queryByText(contacto(2).name)).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Enviar presupuesto" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Enviar presupuesto" }));
    await waitFor(() => expect(mockSend).toHaveBeenCalled());
    // Los destinatarios son exactamente los que se ven: el de la empresa y
    // los tres del CRM que quedan, sin el quitado.
    expect(mockSend.mock.calls[0][2].to).toEqual([
      "marta@maison.example", "f1@crm.example", "f3@crm.example", "f4@crm.example",
    ]);
    expect(mockSend.mock.calls[0][2].cc).toEqual([]);
  });
});
