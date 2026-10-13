import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { INSTRUCCION_POR_DEFECTO, OfertaEmailModal } from "./OfertaEmailModal";
import { generarBorradorNuevo } from "../../lib/emailsApi";
import {
  getEmailSenders,
  getOfertaEmailPreview,
  searchCrmContacts,
  sendOfertaEmail,
  type Oferta,
  type OfertaEmailPreview,
  type OfertaEmailResult,
} from "../../lib/erpApi";

/** Enviar una oferta por email: previsualización obligatoria, contacto de la
 *  oferta premarcado, «Redactar con la IA» con la oferta en el contexto, y el
 *  envío solo con la casilla marcada y `confirm: true`. No toca FACTUSOL. */

jest.mock("../../lib/erpApi", () => ({
  getOfertaEmailPreview: jest.fn(),
  sendOfertaEmail: jest.fn(),
  getEmailSenders: jest.fn(),
  searchCrmContacts: jest.fn(),
}));
jest.mock("../../lib/emailsApi", () => ({
  generarBorradorNuevo: jest.fn(),
}));

const mockPreview = getOfertaEmailPreview as jest.Mock;
const mockSend = sendOfertaEmail as jest.Mock;
const mockSenders = getEmailSenders as jest.Mock;
const mockSearch = searchCrmContacts as jest.Mock;
const mockIa = generarBorradorNuevo as jest.Mock;

function oferta(over: Partial<Oferta> = {}): Oferta {
  return {
    id: "of-1", numero: "OF-2026-0001", anio: 2026, secuencia: 1,
    estado: "borrador", estado_guardado: "borrador", vencida: false,
    contact_id: "k1",
    contacto: { id: "k1", nombre: "Marta Coll", email: "marta@maison.example", company_id: "c1" },
    company_id: "c1",
    empresa: { id: "c1", nombre: "La Maison de la Plaque", factusol_company_id: null },
    plantilla_id: null, serie: 2, emisora: "MQ Europe", idioma: "fr", con_iva: true,
    fecha: "2026-10-12", validez_dias: 30, valida_hasta: "2026-11-11",
    referencia: null, notas: null, base: 1090, iva_pct: 21, iva: 228.9, total: 1318.9,
    version: 2, factusol: null, convertida_en: null, enviada_en: null, caducada_en: null,
    num_envios: 1, ultimo_envio: null, pendiente_de_envio: true,
    created_by_user_id: null, created_at: null, updated_at: null,
    ...over,
  };
}

function preview(over: Partial<OfertaEmailPreview> = {}): OfertaEmailPreview {
  return {
    oferta_id: "of-1", numero: "OF-2026-0001", to: "marta@maison.example",
    lang: "fr", lang_source: "oferta",
    subject: "Offre OF-2026-0001",
    body_text: "Bonjour Marta Coll,\n\nVeuillez trouver ci-joint notre offre OF-2026-0001.",
    from_alias: "info@mqeurope.example", from_alias_source: "serie",
    from_alias_ok: true, from_alias_problem: null,
    attachment_filename: "Offre-OF-2026-0001.pdf",
    company_id: "c1", company_name: "La Maison de la Plaque", contact_id: "k1",
    company_contacts: [
      { id: "k1", name: "Marta Coll", email: "marta@maison.example", has_email: true, is_primary: true },
      { id: "k2", name: "Jean Dupont", email: "jean@maison.example", has_email: true, is_primary: false },
      { id: "k3", name: "Sin Email", email: null, has_email: false, is_primary: false },
    ],
    contacto_id: "k1", markers: {}, version: 2, pendiente_de_envio: true,
    ...over,
  };
}

function resultado(over: Partial<OfertaEmailResult> = {}): OfertaEmailResult {
  return {
    sent: true, message_id: "m1", thread_id: "t1", to: ["marta@maison.example"], cc: [], bcc: [],
    lang: "fr", numero: "OF-2026-0001", attachment_filename: "Offre-OF-2026-0001.pdf",
    estado: "enviada", enviada_en: "2026-10-12T10:00:00+00:00",
    etapas_movidas: ["Propuesta enviada"],
    ...over,
  };
}

const borradorIa = {
  id: "b-1", asunto: "Votre offre OF-2026-0001 — plaques et accessoires",
  texto: "Bonjour Marta,\n\nvous trouverez ci-joint notre offre…",
  html: "<p>Bonjour Marta,</p><p>vous trouverez ci-joint notre offre…</p>",
  aviso: null, pendiente: [], enlaces: [], contexto: {},
};

beforeEach(() => {
  mockPreview.mockReset();
  mockPreview.mockResolvedValue(preview());
  mockSend.mockReset();
  mockSend.mockResolvedValue(resultado());
  mockSenders.mockReset();
  mockSenders.mockResolvedValue({ senders: [], available: true, problem: null });
  mockSearch.mockReset();
  mockSearch.mockResolvedValue([]);
  mockIa.mockReset();
  mockIa.mockResolvedValue(borradorIa);
});

describe("OfertaEmailModal", () => {
  it("carga la previsualización: contacto de la oferta premarcado, idioma, asunto, cuerpo, adjunto con versión y aviso de versión sin enviar", async () => {
    render(<OfertaEmailModal oferta={oferta()} onClose={jest.fn()} onSent={jest.fn()} />);
    expect(await screen.findByRole("heading", { name: /Enviar oferta por email/ })).toBeInTheDocument();
    await waitFor(() => expect(mockPreview).toHaveBeenCalledWith("of-1", undefined));
    expect(await screen.findByLabelText("Enviar a Marta Coll (marta@maison.example)")).toBeChecked();
    expect(screen.getByLabelText("Enviar a Jean Dupont (jean@maison.example)")).not.toBeChecked();
    expect(screen.getByLabelText("Idioma del correo")).toHaveValue("fr");
    expect(screen.getByText(/Idioma de la oferta/)).toBeInTheDocument();
    expect(screen.getByLabelText("Asunto")).toHaveValue("Offre OF-2026-0001");
    expect(screen.getByLabelText("Cuerpo del mensaje")).toHaveValue(preview().body_text);
    expect(screen.getByText(/Se adjunta el PDF de la oferta OF-2026-0001 \(versión 2\)/)).toBeInTheDocument();
    expect(screen.getByText("Offre-OF-2026-0001.pdf")).toBeInTheDocument();
    expect(screen.getByText(/Esta versión aún no se ha enviado/)).toBeInTheDocument();
    expect(screen.getByText(/de la empresa emisora de la oferta/)).toBeInTheDocument();
    expect(screen.getByLabelText("Instrucción para la IA")).toHaveValue(INSTRUCCION_POR_DEFECTO);
    // Abrir no envía nada ni llama a la IA.
    expect(mockSend).not.toHaveBeenCalled();
    expect(mockIa).not.toHaveBeenCalled();
  });

  it("sin «versión pendiente» no sale el aviso", async () => {
    mockPreview.mockResolvedValue(preview({ pendiente_de_envio: false, version: 1 }));
    render(<OfertaEmailModal oferta={oferta({ pendiente_de_envio: false, version: 1 })}
                             onClose={jest.fn()} onSent={jest.fn()} />);
    await screen.findByLabelText("Enviar a Marta Coll (marta@maison.example)");
    expect(screen.queryByText(/Esta versión aún no se ha enviado/)).not.toBeInTheDocument();
    expect(screen.getByText(/\(versión 1\)/)).toBeInTheDocument();
  });

  it("«Redactar con la IA» pide el correo nuevo con la oferta y el contacto, y vuelca asunto y cuerpo", async () => {
    const user = userEvent.setup();
    render(<OfertaEmailModal oferta={oferta()} onClose={jest.fn()} onSent={jest.fn()} />);
    await screen.findByLabelText("Enviar a Marta Coll (marta@maison.example)");
    await user.click(screen.getByRole("button", { name: "Redactar con la IA" }));
    await waitFor(() => expect(mockIa).toHaveBeenCalledTimes(1));
    expect(mockIa).toHaveBeenCalledWith({
      destinatario: "marta@maison.example",
      instruccion: INSTRUCCION_POR_DEFECTO,
      alcance: "contacto",
      contact_id: "k1",
      oferta_id: "of-1",
      formato: "normal",
    });
    await waitFor(() => expect(screen.getByLabelText("Asunto")).toHaveValue(borradorIa.asunto));
    expect(screen.getByLabelText("Cuerpo del mensaje")).toHaveValue(borradorIa.texto);
    // Redactar no envía.
    expect(mockSend).not.toHaveBeenCalled();
  });

  it("la instrucción se puede cambiar y el aviso de la IA se enseña", async () => {
    mockIa.mockResolvedValue({ ...borradorIa, aviso: "No tengo el plazo de entrega: revísalo." });
    const user = userEvent.setup();
    render(<OfertaEmailModal oferta={oferta()} onClose={jest.fn()} onSent={jest.fn()} />);
    await screen.findByLabelText("Enviar a Marta Coll (marta@maison.example)");
    const caja = screen.getByLabelText("Instrucción para la IA");
    await user.clear(caja);
    await user.type(caja, "Dile que el plazo son tres semanas");
    await user.click(screen.getByRole("button", { name: "Redactar con la IA" }));
    await waitFor(() => expect(mockIa).toHaveBeenCalledWith(expect.objectContaining({
      instruccion: "Dile que el plazo son tres semanas", oferta_id: "of-1",
    })));
    expect(await screen.findByText(/No tengo el plazo de entrega/)).toBeInTheDocument();
  });

  it("si la guardia cambió «Bomedia», el modal enseña qué frase se tocó, antes y ahora", async () => {
    mockIa.mockResolvedValue({
      ...borradorIa,
      bomedia: {
        empresa: "MQ Europe",
        cambios: [{ campo: "cuerpo", antes: "Vous avez déjà travaillé avec Bomedia.",
                    despues: "Vous avez déjà travaillé avec MQ Europe." }],
      },
    });
    const user = userEvent.setup();
    render(<OfertaEmailModal oferta={oferta()} onClose={jest.fn()} onSent={jest.fn()} />);
    await screen.findByLabelText("Enviar a Marta Coll (marta@maison.example)");
    await user.click(screen.getByRole("button", { name: "Redactar con la IA" }));
    const aviso = await screen.findByRole("alert", { name: "Bomedia sustituida" });
    expect(aviso).toHaveTextContent("He cambiado «Bomedia» por «MQ Europe» en esta frase.");
    expect(aviso).toHaveTextContent("Antes: «Vous avez déjà travaillé avec Bomedia.»");
    expect(aviso).toHaveTextContent("Ahora: «Vous avez déjà travaillé avec MQ Europe.»");
  });

  it("si la IA falla lo dice y deja el texto de la plantilla", async () => {
    mockIa.mockRejectedValue(new Error("Sin crédito de IA"));
    const user = userEvent.setup();
    render(<OfertaEmailModal oferta={oferta()} onClose={jest.fn()} onSent={jest.fn()} />);
    await screen.findByLabelText("Enviar a Marta Coll (marta@maison.example)");
    await user.click(screen.getByRole("button", { name: "Redactar con la IA" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Sin crédito de IA");
    expect(screen.getByLabelText("Asunto")).toHaveValue("Offre OF-2026-0001");
  });

  it("sin marcar la casilla no se envía; marcada, envía con confirm: true, el HTML de la IA, y avisa de la etapa", async () => {
    const onSent = jest.fn();
    const user = userEvent.setup();
    render(<OfertaEmailModal oferta={oferta()} onClose={jest.fn()} onSent={onSent} />);
    await screen.findByLabelText("Enviar a Marta Coll (marta@maison.example)");
    const enviar = screen.getByRole("button", { name: "Enviar oferta" });
    expect(enviar).toBeDisabled();
    await user.click(enviar);
    expect(mockSend).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Redactar con la IA" }));
    await waitFor(() => expect(screen.getByLabelText("Asunto")).toHaveValue(borradorIa.asunto));
    await user.click(screen.getByLabelText("Confirmo el envío"));
    expect(enviar).toBeEnabled();
    await user.click(enviar);
    await waitFor(() => expect(mockSend).toHaveBeenCalledTimes(1));
    expect(mockSend).toHaveBeenCalledWith("of-1", {
      confirm: true,
      to: ["marta@maison.example"],
      cc: [],
      bcc: [],
      subject: borradorIa.asunto,
      body_text: borradorIa.texto,
      body_html: borradorIa.html,
      lang: "fr",
      from_alias: "info@mqeurope.example",
    });
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Oferta OF-2026-0001 enviada a marta@maison.example en FR. El contacto pasa a Propuesta enviada.",
    );
    expect(onSent).toHaveBeenCalledWith(resultado());
  });

  it("si la persona retoca el cuerpo de la IA, va solo el texto (sin body_html); sin etapas movidas no lo menciona", async () => {
    mockSend.mockResolvedValue(resultado({ etapas_movidas: [] }));
    const user = userEvent.setup();
    render(<OfertaEmailModal oferta={oferta()} onClose={jest.fn()} onSent={jest.fn()} />);
    await screen.findByLabelText("Enviar a Marta Coll (marta@maison.example)");
    await user.click(screen.getByRole("button", { name: "Redactar con la IA" }));
    await waitFor(() => expect(screen.getByLabelText("Cuerpo del mensaje")).toHaveValue(borradorIa.texto));
    await user.type(screen.getByLabelText("Cuerpo del mensaje"), " Merci.");
    await user.click(screen.getByLabelText("Confirmo el envío"));
    await user.click(screen.getByRole("button", { name: "Enviar oferta" }));
    await waitFor(() => expect(mockSend).toHaveBeenCalledTimes(1));
    expect(mockSend.mock.calls[0][1]).toEqual(expect.objectContaining({
      confirm: true, body_html: null, body_text: `${borradorIa.texto} Merci.`,
    }));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Oferta OF-2026-0001 enviada a marta@maison.example en FR.");
    expect(status).not.toHaveTextContent("Propuesta enviada");
  });

  it("cambiar el idioma recarga la previsualización con lang conservando los destinatarios", async () => {
    const user = userEvent.setup();
    render(<OfertaEmailModal oferta={oferta()} onClose={jest.fn()} onSent={jest.fn()} />);
    await screen.findByLabelText("Enviar a Marta Coll (marta@maison.example)");
    mockPreview.mockResolvedValue(preview({ lang: "de", lang_source: "selector", subject: "Angebot OF-2026-0001" }));
    await user.selectOptions(screen.getByLabelText("Idioma del correo"), "de");
    await waitFor(() => expect(mockPreview).toHaveBeenLastCalledWith("of-1", "de"));
    await waitFor(() => expect(screen.getByLabelText("Asunto")).toHaveValue("Angebot OF-2026-0001"));
    expect(screen.getByLabelText("Enviar a Marta Coll (marta@maison.example)")).toBeChecked();
    expect(screen.getByText("Idioma elegido a mano.")).toBeInTheDocument();
  });

  it("sin destinatarios no se puede enviar ni redactar; una dirección libre vale", async () => {
    mockPreview.mockResolvedValue(preview({ company_contacts: [], company_id: null, company_name: null, to: "" }));
    const user = userEvent.setup();
    render(<OfertaEmailModal oferta={oferta({ company_id: null, empresa: null })}
                             onClose={jest.fn()} onSent={jest.fn()} />);
    const enviar = await screen.findByRole("button", { name: "Enviar oferta" });
    expect(enviar).toBeDisabled();
    expect(screen.getByText(/Elige al menos un destinatario/)).toBeInTheDocument();
    expect(screen.getByText(/no tiene empresa en el CRM/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Redactar con la IA" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Elige primero un destinatario/);
    expect(mockIa).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText("Destinatario"), "libre@ejemplo.com");
    await user.click(screen.getByLabelText("Confirmo el envío"));
    expect(enviar).toBeEnabled();
    await user.click(enviar);
    await waitFor(() => expect(mockSend).toHaveBeenCalledWith("of-1", expect.objectContaining({
      confirm: true, to: ["libre@ejemplo.com"],
    })));
  });

  it("el buscador del CRM añade un contacto de otra empresa a CC", async () => {
    mockSearch.mockResolvedValue([{
      id: "c-eduard", name: "Eduard Riera", email: "eduard@riera.example", company_name: "Riera SL",
    }]);
    const user = userEvent.setup();
    render(<OfertaEmailModal oferta={oferta()} onClose={jest.fn()} onSent={jest.fn()} />);
    await screen.findByLabelText("Enviar a Marta Coll (marta@maison.example)");
    await user.type(screen.getByLabelText("Buscar contacto del CRM"), "riera");
    await user.click(await screen.findByRole("button", { name: "Añadir a Eduard Riera en CC" }));
    expect(screen.getByRole("list", { name: "Contactos del CRM añadidos" })).toHaveTextContent("eduard@riera.example");
    await user.click(screen.getByLabelText("Confirmo el envío"));
    await user.click(screen.getByRole("button", { name: "Enviar oferta" }));
    await waitFor(() => expect(mockSend).toHaveBeenCalledWith("of-1", expect.objectContaining({
      to: ["marta@maison.example"], cc: ["eduard@riera.example"],
    })));
  });

  it("si el envío falla lo dice y NO la da por enviada; un remitente no utilizable se avisa antes", async () => {
    mockSend.mockRejectedValue(new Error("Gmail no conectado"));
    const user = userEvent.setup();
    render(<OfertaEmailModal oferta={oferta()} onClose={jest.fn()} onSent={jest.fn()} />);
    await screen.findByLabelText("Enviar a Marta Coll (marta@maison.example)");
    await user.click(screen.getByLabelText("Confirmo el envío"));
    await user.click(screen.getByRole("button", { name: "Enviar oferta" }));
    expect(await screen.findByText("Gmail no conectado")).toBeInTheDocument();
    expect(screen.queryByText(/Oferta OF-2026-0001 enviada/)).toBeNull();

    mockPreview.mockResolvedValue(preview({ from_alias_ok: false, from_alias_problem: "sin_alias" }));
    render(<OfertaEmailModal oferta={oferta({ id: "of-2" })} onClose={jest.fn()} onSent={jest.fn()} />);
    expect(await screen.findByText(/No hay remitente configurado/)).toBeInTheDocument();
  });

  it("una oferta ya convertida: la previsualización falla y el modal lo enseña sin formulario", async () => {
    mockPreview.mockRejectedValue(new Error("La oferta OF-2026-0001 ya está convertida; envía el presupuesto de FACTUSOL."));
    render(<OfertaEmailModal oferta={oferta({ estado: "convertida" })} onClose={jest.fn()} onSent={jest.fn()} />);
    expect(await screen.findByText(/ya está convertida/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Enviar oferta" })).not.toBeInTheDocument();
  });
});
