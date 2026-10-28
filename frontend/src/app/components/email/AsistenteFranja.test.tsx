import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { BorradorAsistente } from "../../lib/emailsApi";
import { AsistenteFranja } from "./AsistenteFranja";

jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ id: "u-1" })),
}));
jest.mock("../../lib/emailsApi", () => ({
  generarBorradorAsistente: jest.fn(),
  generarBorradorNuevo: jest.fn(),
  turnoAsistente: jest.fn(),
  descartarBorradorAsistente: jest.fn(() => Promise.resolve({})),
  conversacionAsistente: jest.fn(() => Promise.resolve({ turnos: [], actual: null })),
  esDestinatarioInterno: jest.fn(() => Promise.resolve({ direccion: "", interno: false })),
}));

const usuarios = jest.requireMock("../../lib/api") as { getCurrentUser: jest.Mock };

/** Las casillas de partida: «Webs» y «Soporte» marcadas, en cada petición. */
const TODAS = { webs: true, soporte: true };

const api = jest.requireMock("../../lib/emailsApi") as {
  generarBorradorAsistente: jest.Mock;
  generarBorradorNuevo: jest.Mock;
  turnoAsistente: jest.Mock;
  descartarBorradorAsistente: jest.Mock;
  conversacionAsistente: jest.Mock;
  esDestinatarioInterno: jest.Mock;
};

function borrador(over: Partial<BorradorAsistente> = {}): BorradorAsistente {
  return {
    id: "b-0", thread_id: "t-1", message_id: null, contact_id: "c-1", usuario_id: "u-1",
    idioma: "de", modelo: "claude-sonnet-5-5", tokens_entrada: 1200, tokens_salida: 250,
    tokens_cache_lectura: 0, tokens_cache_escritura: 1100, coste_usd: 0.0049,
    destinatario: "klaus@elbarquito.net", remitente: "info@bomedia.net",
    asunto: "Re: Metallplatten", destinatario_interno: false, sin_hilo: false,
    texto: "Hallo Klaus,\n\nvielen Dank.", html: "<p>Hallo Klaus,</p><p>vielen Dank.</p>",
    sobre_editor: false, seleccion: null, fragmento: false,
    aviso: null, instruccion: null, conversacion_id: "b-0", turno: 0, padre_id: null,
    pendiente: [], enlaces: [], contexto: { mensajes_hilo: 2, contacto: { nombre: "Klaus" } },
    alcance: "contacto", mensajes_hilo: 2, mensajes_historico: 0, estado: "generado",
    email_message_id: null, creado: null, resuelto_en: null, ...over,
  };
}

function montar(over: Partial<React.ComponentProps<typeof AsistenteFranja>> = {}) {
  const onBorrador = jest.fn();
  const onVaciar = jest.fn();
  const onBorradorActual = jest.fn();
  const utils = render(
    <AsistenteFranja
      threadId="t-1" contactId="c-1" destinatario="klaus@elbarquito.net" abiertaInicial
      onBorrador={onBorrador} onVaciar={onVaciar} onBorradorActual={onBorradorActual}
      {...over}
    />,
  );
  return { ...utils, onBorrador, onVaciar, onBorradorActual };
}

/** Con borrador la franja se pliega sola: para corregir hay que desplegarla. */
async function desplegar(user: ReturnType<typeof userEvent.setup>) {
  const cabecera = screen.getByRole("button", { name: /Ayúdame a/ });
  if (cabecera.getAttribute("aria-expanded") === "false") await user.click(cabecera);
}

beforeEach(() => {
  jest.clearAllMocks();
  api.conversacionAsistente.mockResolvedValue({ turnos: [], actual: null });
  api.esDestinatarioInterno.mockResolvedValue({ direccion: "", interno: false });
  window.localStorage.clear();
});

describe("AsistenteFranja — lo primero es la caja", () => {
  it("respondiendo, «Redactar» con la caja vacía pide el borrador deducido del hilo, lo pone en el editor y se pliega", async () => {
    api.generarBorradorAsistente.mockResolvedValueOnce(borrador());
    const { onBorrador, onBorradorActual } = montar();
    const user = userEvent.setup();
    expect(screen.getByLabelText("Qué quieres decirle")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Redactar/ }));
    await waitFor(() => expect(onBorrador).toHaveBeenCalledTimes(1));
    expect(api.generarBorradorAsistente).toHaveBeenCalledWith("t-1", {
      alcance: "contacto", instruccion: null, destinatario: "klaus@elbarquito.net", fuentes: TODAS,
    });
    expect(onBorradorActual).toHaveBeenCalledWith("b-0");
    // Con borrador, la franja se pliega sola a una línea con lo esencial.
    const cabecera = screen.getByRole("button", { name: /Ayúdame a contestar/ });
    expect(cabecera).toHaveAttribute("aria-expanded", "false");
    expect(cabecera).toHaveTextContent("1 borrador · 0.0049 $");
    expect(screen.queryByLabelText("Qué quieres decirle")).not.toBeInTheDocument();
    // Desplegada: el botón pasa a «Aplicar» y la caja pide correcciones.
    await desplegar(user);
    expect(screen.getByText("Borrador inicial, deducido del hilo")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Aplicar/ })).toBeInTheDocument();
  });

  it("con instrucción, la instrucción viaja y manda", async () => {
    api.generarBorradorAsistente.mockResolvedValueOnce(
      borrador({ instruccion: "pregúntale si recibió el presupuesto" }),
    );
    montar();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Qué quieres decirle"), "pregúntale si recibió el presupuesto");
    await user.keyboard("{Control>}{Enter}{/Control}");
    await waitFor(() => expect(api.generarBorradorAsistente).toHaveBeenCalledTimes(1));
    expect(api.generarBorradorAsistente.mock.calls[0][1].instruccion).toBe(
      "pregúntale si recibió el presupuesto",
    );
    await desplegar(user);
    expect(await screen.findByText("«pregúntale si recibió el presupuesto»")).toBeInTheDocument();
  });

  it("sin hilo, sin instrucción no se llama al modelo y la franja lo dice; «Solo este hilo» no existe", async () => {
    montar({ threadId: null, destinatario: "nuevo@cliente.io" });
    const user = userEvent.setup();
    const selector = screen.getByLabelText("Qué histórico mira la IA") as HTMLSelectElement;
    expect(Array.from(selector.options).map((o) => o.textContent)).toEqual([
      "Todo lo de este contacto", "Todo lo de la empresa",
    ]);
    await user.click(screen.getByRole("button", { name: /Redactar/ }));
    expect(screen.getByRole("alert")).toHaveTextContent("Dime qué quieres decirle");
    expect(api.generarBorradorNuevo).not.toHaveBeenCalled();
    // Con instrucción sí: con el destinatario de «Para».
    api.generarBorradorNuevo.mockResolvedValueOnce(
      borrador({ thread_id: null, sin_hilo: true, asunto: "Seguimiento del presupuesto",
                 instruccion: "escríbele un seguimiento" }),
    );
    await user.type(screen.getByLabelText("Qué quieres decirle"), "escríbele un seguimiento");
    await user.click(screen.getByRole("button", { name: /Redactar/ }));
    await waitFor(() => expect(api.generarBorradorNuevo).toHaveBeenCalledWith({
      destinatario: "nuevo@cliente.io", instruccion: "escríbele un seguimiento",
      alcance: "contacto", contact_id: "c-1", fuentes: TODAS,
    }));
  });
});

describe("AsistenteFranja — la conversación", () => {
  it("una corrección va por turno, se apila y se puede volver al borrador anterior", async () => {
    api.generarBorradorAsistente.mockResolvedValueOnce(borrador());
    api.turnoAsistente.mockResolvedValueOnce(borrador({
      id: "b-1", turno: 1, padre_id: "b-0", instruccion: "más corto",
      texto: "Hallo Klaus, kurz.", html: "<p>Hallo Klaus, kurz.</p>", modelo: "claude-haiku-5-5",
      tokens_cache_lectura: 1100, coste_usd: 0.0003,
    }));
    const { onBorrador, onBorradorActual } = montar();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Redactar/ }));
    await waitFor(() => expect(onBorrador).toHaveBeenCalledTimes(1));
    await desplegar(user);
    await user.type(screen.getByLabelText("Qué quieres decirle"), "más corto");
    await user.click(screen.getByRole("button", { name: /Aplicar/ }));
    await waitFor(() => expect(api.turnoAsistente).toHaveBeenCalledWith("b-0", "más corto", { fuentes: TODAS }));
    await waitFor(() => expect(onBorrador).toHaveBeenCalledTimes(2));
    expect(onBorrador.mock.calls[1][0].id).toBe("b-1");
    expect(onBorradorActual).toHaveBeenLastCalledWith("b-1");
    await desplegar(user);
    const lista = screen.getByRole("list", { name: "Lo dicho hasta ahora" });
    expect(lista).toHaveTextContent("Borrador inicial, deducido del hilo");
    expect(lista).toHaveTextContent("«más corto»");
    // Volver atrás: el editor vuelve al primero y el envío anotaría ese id.
    await user.click(screen.getByRole("button", { name: "Volver al borrador anterior" }));
    expect(onBorrador).toHaveBeenCalledTimes(3);
    expect(onBorrador.mock.calls[2][0].id).toBe("b-0");
    expect(onBorradorActual).toHaveBeenLastCalledWith("b-0");
    expect(screen.getByRole("button", { name: "Volver al borrador anterior" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Siguiente" })).toBeEnabled();
    // Sin texto no hay corrección que aplicar.
    await user.click(screen.getByRole("button", { name: /Aplicar/ }));
    expect(screen.getByRole("alert")).toHaveTextContent("Escribe qué quieres cambiar");
    expect(api.turnoAsistente).toHaveBeenCalledTimes(1);
  });

  it("lo que la IA le dice a la persona va en la franja, nunca al cuerpo; sin cuerpo no se pliega", async () => {
    api.generarBorradorAsistente.mockResolvedValueOnce(borrador({
      texto: "", html: "",
      aviso: "El último mensaje del hilo es nuestro: no hay nada nuevo del cliente que contestar.",
    }));
    const { onBorrador } = montar();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Redactar/ }));
    await waitFor(() => expect(onBorrador).toHaveBeenCalledTimes(1));
    expect(onBorrador.mock.calls[0][0].html).toBe("");
    expect(screen.getByRole("button", { name: /Ayúdame a contestar/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("status")).toHaveTextContent(
      "La IA no ha redactado el correo: El último mensaje del hilo es nuestro",
    );
    expect(screen.getByRole("list", { name: "Lo dicho hasta ahora" })).toHaveTextContent("sin cuerpo");
  });

  it("«Descartar el borrador de la IA» descarta la conversación, vacía el editor y pliega la franja", async () => {
    api.generarBorradorAsistente.mockResolvedValueOnce(borrador());
    const { onVaciar, onBorradorActual } = montar();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Redactar/ }));
    await desplegar(user);
    await screen.findByText("Borrador inicial, deducido del hilo");
    await user.click(screen.getByRole("button", { name: "Descartar el borrador de la IA" }));
    expect(api.descartarBorradorAsistente).toHaveBeenCalledWith("b-0");
    expect(onVaciar).toHaveBeenCalledTimes(1);
    expect(onBorradorActual).toHaveBeenLastCalledWith(null);
    expect(screen.queryByLabelText("Qué quieres decirle")).not.toBeInTheDocument();
  });

  it("un borrador guardado vuelve con su conversación (turnos y coste) sin tocar el editor", async () => {
    api.conversacionAsistente.mockResolvedValueOnce({
      turnos: [borrador(), borrador({ id: "b-1", turno: 1, padre_id: "b-0", instruccion: "más corto",
        coste_usd: 0.0003 })],
      actual: "b-1",
    });
    const { onBorrador, onBorradorActual } = montar({ abiertaInicial: false, borradorInicialId: "b-1" });
    await waitFor(() => expect(onBorradorActual).toHaveBeenCalledWith("b-1"));
    expect(api.conversacionAsistente).toHaveBeenCalledWith("b-1");
    expect(onBorrador).not.toHaveBeenCalled();          // el editor ya tiene el cuerpo del borrador
    const cabecera = screen.getByRole("button", { name: /Ayúdame a contestar/ });
    expect(cabecera).toHaveTextContent("2 turnos · 0.0052 $");
    await userEvent.setup().click(cabecera);
    expect(screen.getByRole("list", { name: "Lo dicho hasta ahora" })).toHaveTextContent("«más corto»");
    expect(screen.getByRole("button", { name: /Aplicar/ })).toBeInTheDocument();
  });
});

describe("AsistenteFranja — corrige lo que hay en el editor", () => {
  it("con texto en el cuerpo la caja va en modo corrección y manda el texto del editor, nunca «¿Qué quieres decirle?»", async () => {
    const obtenerCuerpo = jest.fn(() => ({ html: "<p>hola klaus, te paso lo de las máquinas</p>", seleccion: "" }));
    api.generarBorradorAsistente.mockResolvedValueOnce(borrador({
      instruccion: "ponlo más formal", sobre_editor: true,
      html: "<p>Estimado Klaus,</p>", texto: "Estimado Klaus,",
    }));
    const { onBorrador } = montar({ hayTexto: true, obtenerCuerpo });
    const user = userEvent.setup();
    expect(screen.getByRole("button", { name: /Ayúdame a contestar/ })).toHaveTextContent(
      "la IA corrige lo que hay en el editor");
    const caja = screen.getByLabelText("Qué quieres decirle") as HTMLTextAreaElement;
    expect(caja.placeholder).toMatch(/^Corrige el texto/);
    expect(caja.placeholder).not.toContain("¿Qué quieres decirle?");
    expect(screen.queryByRole("button", { name: /Redactar/ })).not.toBeInTheDocument();
    // Sin decir qué cambiar no hay nada que aplicar.
    await user.click(screen.getByRole("button", { name: /Aplicar/ }));
    expect(screen.getByRole("alert")).toHaveTextContent("Escribe qué quieres cambiar");
    expect(api.generarBorradorAsistente).not.toHaveBeenCalled();
    await user.type(caja, "ponlo más formal");
    await user.click(screen.getByRole("button", { name: /Aplicar/ }));
    await waitFor(() => expect(api.generarBorradorAsistente).toHaveBeenCalledWith("t-1", {
      alcance: "contacto", instruccion: "ponlo más formal", destinatario: "klaus@elbarquito.net",
      texto_actual_html: "<p>hola klaus, te paso lo de las máquinas</p>", seleccion_html: null,
      fuentes: TODAS,
    }));
    await waitFor(() => expect(onBorrador).toHaveBeenCalledTimes(1));
    expect(onBorrador.mock.calls[0][0].sobre_editor).toBe(true);
    // El editor no cambió mientras la IA trabajaba: el compositor la pone sin preguntar.
    expect(onBorrador.mock.calls[0][1]).toEqual({ editorCambiado: false });
    await desplegar(user);
    expect(screen.getByRole("list", { name: "Lo dicho hasta ahora" })).toHaveTextContent("sobre tu texto");
  });

  it("si el editor cambia mientras la IA trabaja, se lo dice al compositor (que pregunta antes de pisarlo)", async () => {
    let soltar: (b: BorradorAsistente) => void = () => undefined;
    api.generarBorradorAsistente.mockReturnValueOnce(new Promise<BorradorAsistente>((r) => { soltar = r; }));
    const cuerpo = { html: "<p>hola klaus</p>" };
    const { onBorrador } = montar({ hayTexto: true, obtenerCuerpo: () => ({ html: cuerpo.html, seleccion: "" }) });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Qué quieres decirle"), "más formal");
    await user.click(screen.getByRole("button", { name: /Aplicar/ }));
    await waitFor(() => expect(api.generarBorradorAsistente).toHaveBeenCalledTimes(1));
    cuerpo.html = "<p>hola klaus, y una cosa más que escribí mientras tanto</p>";
    soltar(borrador({ sobre_editor: true }));
    await waitFor(() => expect(onBorrador).toHaveBeenCalledTimes(1));
    expect(onBorrador.mock.calls[0][1]).toEqual({ editorCambiado: true });
  });

  it("un fragmento vacío (la guardia lo vació) no es «selección perdida»: queda el aviso y el editor no se toca", async () => {
    api.generarBorradorAsistente.mockResolvedValueOnce(borrador({
      sobre_editor: true, fragmento: true, html: "", texto: "",
      aviso: "La IA no ha redactado un correo (habla de Marny en tercera persona).",
      seleccion: { original: "Te paso los precios.", original_html: "<p>Te paso los precios.</p>", fragmento_html: "", empalmado: false },
    }));
    montar({
      hayTexto: true, onBorrador: jest.fn(() => false),
      obtenerCuerpo: () => ({ html: "<p>Te paso los precios.</p>", seleccion: "<p>Te paso los precios.</p>" }),
    });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Qué quieres decirle"), "desarróllala");
    await user.click(screen.getByRole("button", { name: /Aplicar/ }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "La IA no ha redactado el correo: La IA no ha redactado un correo (habla de Marny");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("ante la duda el cuerpo se queda: el aviso pide revisarlo, no dice que no hay correo", async () => {
    api.generarBorradorAsistente.mockResolvedValueOnce(borrador({
      aviso: "Revisa el cuerpo antes de enviar: menciona «al cliente». Lo he dejado en el editor por si es un correo válido.",
    }));
    montar();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Redactar/ }));
    await desplegar(user);
    expect(await screen.findByRole("status")).toHaveTextContent("Revisa antes de enviar: Revisa el cuerpo");
    expect(screen.getByRole("status")).not.toHaveTextContent("La IA no ha redactado el correo");
  });

  it("sin hilo, con un correo a mano, «Aplicar» manda el texto como correo nuevo", async () => {
    api.generarBorradorNuevo.mockResolvedValueOnce(borrador({ thread_id: null, sin_hilo: true, sobre_editor: true }));
    montar({
      threadId: null, destinatario: "nuevo@cliente.io", hayTexto: true,
      obtenerCuerpo: () => ({ html: "<p>hola, te escribo por el presupuesto</p>", seleccion: "" }),
    });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Qué quieres decirle"), "más formal");
    await user.click(screen.getByRole("button", { name: /Aplicar/ }));
    await waitFor(() => expect(api.generarBorradorNuevo).toHaveBeenCalledWith({
      destinatario: "nuevo@cliente.io", instruccion: "más formal", alcance: "contacto", contact_id: "c-1",
      texto_actual_html: "<p>hola, te escribo por el presupuesto</p>", seleccion_html: null,
      fuentes: TODAS,
    }));
  });

  it("con una selección lo dice, la corrección va solo a ella y una corrección con conversación parte del editor", async () => {
    api.generarBorradorAsistente.mockResolvedValueOnce(borrador());
    const seleccion = { current: "" };
    const obtenerCuerpo = () => ({
      html: "<p>Hola Marny,</p><p>Te paso los precios.</p><p>Un saludo</p>", seleccion: seleccion.current,
    });
    const { rerender, onBorrador } = montar({ obtenerCuerpo });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Redactar/ }));
    await waitFor(() => expect(onBorrador).toHaveBeenCalledTimes(1));
    // Ya hay texto en el editor (el borrador): la persona selecciona una frase.
    seleccion.current = "<p>Te paso los precios.</p>";
    rerender(
      <AsistenteFranja threadId="t-1" contactId="c-1" destinatario="klaus@elbarquito.net" abiertaInicial
        hayTexto obtenerCuerpo={obtenerCuerpo}
        onBorrador={onBorrador} onVaciar={jest.fn()} onBorradorActual={jest.fn()} />,
    );
    await desplegar(user);
    const caja = screen.getByLabelText("Qué quieres decirle") as HTMLTextAreaElement;
    await user.click(caja);
    expect(screen.getByRole("note")).toHaveTextContent("Se aplicará solo a la selección: «Te paso los precios.»");
    expect(caja.placeholder).toMatch(/^Qué cambiar en la selección/);
    api.turnoAsistente.mockResolvedValueOnce(borrador({
      id: "b-1", turno: 1, padre_id: "b-0", instruccion: "desarróllala", sobre_editor: true,
      seleccion: { original: "Te paso los precios.", fragmento_html: "<p>Te adjunto los precios.</p>", empalmado: true },
      html: "<p>Hola Marny,</p><p>Te adjunto los precios.</p><p>Un saludo</p>",
    }));
    await user.type(caja, "desarróllala");
    await user.click(screen.getByRole("button", { name: /Aplicar/ }));
    await waitFor(() => expect(api.turnoAsistente).toHaveBeenCalledWith("b-0", "desarróllala", {
      texto_actual_html: "<p>Hola Marny,</p><p>Te paso los precios.</p><p>Un saludo</p>",
      seleccion_html: "<p>Te paso los precios.</p>", fuentes: TODAS,
    }));
    await waitFor(() => expect(onBorrador).toHaveBeenCalledTimes(2));
    await desplegar(user);
    expect(screen.getByRole("list", { name: "Lo dicho hasta ahora" })).toHaveTextContent(
      "«desarróllala» — sobre la selección «Te paso los precios.»");
  });

  it("si el compositor no puede poner el fragmento (la selección ya no está), la franja lo dice y no se pliega", async () => {
    api.generarBorradorAsistente.mockResolvedValueOnce(borrador({
      sobre_editor: true, fragmento: true, html: "<p>Te adjunto los precios.</p>",
      seleccion: { original: "Te paso los precios.", fragmento_html: "<p>Te adjunto los precios.</p>", empalmado: false },
    }));
    montar({
      hayTexto: true, onBorrador: jest.fn(() => false),
      obtenerCuerpo: () => ({ html: "<p>Te paso los precios.</p>", seleccion: "<p>Te paso los precios.</p>" }),
    });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Qué quieres decirle"), "desarróllala");
    await user.click(screen.getByRole("button", { name: /Aplicar/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("El editor ya no tiene la selección");
    expect(screen.getByRole("button", { name: /Ayúdame a contestar/ })).toHaveAttribute("aria-expanded", "true");
  });
});

describe("AsistenteFranja — plegado y avisos", () => {
  it("plegada es una sola línea; la cabecera la despliega", async () => {
    montar({ abiertaInicial: false });
    expect(screen.queryByLabelText("Qué quieres decirle")).not.toBeInTheDocument();
    const cabecera = screen.getByRole("button", { name: /Ayúdame a contestar/ });
    expect(cabecera).toHaveAttribute("aria-expanded", "false");
    await userEvent.setup().click(cabecera);
    expect(screen.getByLabelText("Qué quieres decirle")).toBeInTheDocument();
    expect(cabecera).toHaveAttribute("aria-expanded", "true");
  });

  it("si el destinatario es un compañero lo dice antes de redactar, y sin hilo se llama «redactar»", () => {
    api.esDestinatarioInterno.mockResolvedValue({ direccion: "klaus@elbarquito.net", interno: true });
    montar({ destinatarioInterno: true });
    expect(screen.getByRole("status")).toHaveTextContent("Vas a escribir a un compañero");
    montar({ threadId: null, abiertaInicial: false });
    expect(screen.getByRole("button", { name: /Ayúdame a redactar/ })).toBeInTheDocument();
  });

  it("el aviso de compañero se recalcula al cambiar «Para», con borrador o sin él", async () => {
    api.esDestinatarioInterno.mockImplementation((direccion: string) =>
      Promise.resolve({ direccion, interno: direccion.endsWith("@bomedia.net") }));
    const { rerender } = montar();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    rerender(
      <AsistenteFranja threadId="t-1" contactId="c-1" destinatario="manel@bomedia.net" abiertaInicial
        onBorrador={jest.fn()} onVaciar={jest.fn()} onBorradorActual={jest.fn()} />,
    );
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Vas a escribir a un compañero"));
    expect(api.generarBorradorAsistente).not.toHaveBeenCalled();
    // Y al revés: de compañero a cliente, el aviso se va.
    rerender(
      <AsistenteFranja threadId="t-1" contactId="c-1" destinatario="klaus@elbarquito.net" abiertaInicial
        destinatarioInterno
        onBorrador={jest.fn()} onVaciar={jest.fn()} onBorradorActual={jest.fn()} />,
    );
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });

  it("un aviso largo va a dos líneas con un «más»", async () => {
    api.generarBorradorAsistente.mockResolvedValueOnce(borrador({
      texto: "", html: "",
      aviso: "He revisado el hilo y el último mensaje es nuestro, así que no hay nada nuevo del "
        + "cliente que contestar ahora mismo. Dime qué quieres decirle (un seguimiento, una "
        + "pregunta, un aviso) y lo redacto con todo el histórico delante.",
    }));
    montar();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Redactar/ }));
    const mas = await screen.findByRole("button", { name: "más" });
    expect(mas).toHaveAttribute("aria-expanded", "false");
    await user.click(mas);
    expect(screen.getByRole("button", { name: "menos" })).toHaveAttribute("aria-expanded", "true");
  });

  it("«Bomedia» sustituida: el aviso dice qué frase se tocó y se ve aunque la franja se pliegue", async () => {
    api.generarBorradorAsistente.mockResolvedValueOnce(borrador({
      idioma: "es",
      texto: "Hola Klaus,\n\nYa trabajaste con Streamtec en 2019.",
      html: "<p>Hola Klaus,</p><p>Ya trabajaste con Streamtec en 2019.</p>",
      bomedia: {
        empresa: "Streamtec",
        cambios: [
          { campo: "asunto", antes: "Re: Tintas de Bomedia", despues: "Re: Tintas de Streamtec" },
          { campo: "cuerpo", antes: "Ya trabajaste con Bomedia en 2019.", despues: "Ya trabajaste con Streamtec en 2019." },
        ],
      },
    }));
    montar();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Redactar/ }));
    const aviso = await screen.findByRole("alert", { name: "Bomedia sustituida" });
    // Con cuerpo la franja se pliega sola… y el aviso sigue a la vista.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Ayúdame a/ })).toHaveAttribute("aria-expanded", "false"));
    expect(aviso).toBeVisible();
    expect(aviso).toHaveTextContent("He cambiado «Bomedia» por «Streamtec» en estas 2 frases.");
    expect(aviso).toHaveTextContent("Antes: «Ya trabajaste con Bomedia en 2019.»");
    expect(aviso).toHaveTextContent("Ahora: «Ya trabajaste con Streamtec en 2019.»");
    expect(aviso).toHaveTextContent("En el asunto propuesto");
    // Sin «más»: no se pliega.
    expect(screen.queryByRole("button", { name: "más" })).not.toBeInTheDocument();
  });

  it("sin sustitución no hay aviso de Bomedia", async () => {
    api.generarBorradorAsistente.mockResolvedValueOnce(borrador({ bomedia: null }));
    montar();
    await userEvent.setup().click(screen.getByRole("button", { name: /Redactar/ }));
    await waitFor(() => expect(api.generarBorradorAsistente).toHaveBeenCalled());
    expect(screen.queryByRole("alert", { name: "Bomedia sustituida" })).not.toBeInTheDocument();
  });

  it("el alcance elegido se recuerda por usuario", async () => {
    window.localStorage.setItem("bohub.asistente.alcance.u-1", "empresa");
    montar();
    await waitFor(() =>
      expect((screen.getByLabelText("Qué histórico mira la IA") as HTMLSelectElement).value).toBe("empresa"));
    await userEvent.setup().selectOptions(screen.getByLabelText("Qué histórico mira la IA"), "hilo");
    expect(window.localStorage.getItem("bohub.asistente.alcance.u-1")).toBe("hilo");
    // Sin conversación, cambiar el alcance no llama a nada.
    expect(api.generarBorradorAsistente).not.toHaveBeenCalled();
  });
});

describe("AsistenteFranja — lo que consulta solo (Webs y Soporte)", () => {
  /** Hasta que no se sabe quién es el usuario no hay nada que leer ni guardar. */
  async function usuarioCargado() {
    await waitFor(() => expect(usuarios.getCurrentUser).toHaveBeenCalled());
    await act(async () => { await Promise.resolve(); });
  }

  it("las casillas «Webs» y «Soporte» están, marcadas de partida, y los botones de buscar ya no", async () => {
    montar();
    await usuarioCargado();
    const grupo = screen.getByRole("group", { name: "Qué consulta el asistente" });
    const webs = within(grupo).getByRole("checkbox", { name: "Webs" });
    const soporte = within(grupo).getByRole("checkbox", { name: "Soporte" });
    expect(webs).toBeChecked();
    expect(soporte).toBeChecked();
    expect(webs.closest("label")).toHaveAttribute("title", "Consulta el índice de nuestras webs al redactar");
    expect(soporte.closest("label")).toHaveAttribute("title", "Consulta el soporte de los fabricantes al redactar");
    expect(screen.queryByRole("button", { name: /Buscar en soporte/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Buscar en las webs/ })).not.toBeInTheDocument();
    // El interruptor del formato se queda: es otra cosa (qué correo se pide).
    expect(screen.getByRole("radiogroup", { name: "Formato del correo" })).toBeInTheDocument();
  });

  it("lo desmarcado viaja en la petición siguiente: redactar, corregir y cambiar el alcance", async () => {
    api.generarBorradorAsistente.mockResolvedValueOnce(borrador());
    montar();
    await usuarioCargado();
    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: "Soporte" }));
    await user.click(screen.getByRole("button", { name: /Redactar/ }));
    await waitFor(() => expect(api.generarBorradorAsistente).toHaveBeenCalledWith("t-1", {
      alcance: "contacto", instruccion: null, destinatario: "klaus@elbarquito.net",
      fuentes: { webs: true, soporte: false },
    }));
    // Una corrección lleva las casillas de AHORA (siempre con su tercer argumento).
    await desplegar(user);
    await user.click(screen.getByRole("checkbox", { name: "Webs" }));
    api.turnoAsistente.mockResolvedValueOnce(borrador({ id: "b-1", turno: 1, padre_id: "b-0", instruccion: "más corto" }));
    await user.type(screen.getByLabelText("Qué quieres decirle"), "más corto");
    await user.click(screen.getByRole("button", { name: /Aplicar/ }));
    await waitFor(() => expect(api.turnoAsistente).toHaveBeenCalledWith("b-0", "más corto", {
      fuentes: { webs: false, soporte: false },
    }));
    // Cambiar el alcance rehace el borrador con lo que lleva la franja: el
    // formato y las casillas.
    await desplegar(user);
    await user.click(screen.getByRole("checkbox", { name: "Soporte" }));
    await user.click(screen.getByRole("radio", { name: "Con fichas" }));
    api.generarBorradorAsistente.mockResolvedValueOnce(borrador({ id: "b-2", alcance: "empresa" }));
    await user.selectOptions(screen.getByLabelText("Qué histórico mira la IA"), "empresa");
    await waitFor(() => expect(api.generarBorradorAsistente).toHaveBeenCalledTimes(2));
    expect(api.generarBorradorAsistente.mock.calls[1]).toEqual(["t-1", {
      alcance: "empresa", instruccion: null, destinatario: "klaus@elbarquito.net", regenerar_de: "b-0",
      formato: "compuesto", fuentes: { webs: false, soporte: true },
    }]);
  });

  it("en un correo nuevo también viajan", async () => {
    api.generarBorradorNuevo.mockResolvedValueOnce(borrador({ thread_id: null, sin_hilo: true }));
    montar({ threadId: null, destinatario: "nuevo@cliente.io" });
    await usuarioCargado();
    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: "Webs" }));
    await user.type(screen.getByLabelText("Qué quieres decirle"), "contéstale lo del tubo");
    await user.click(screen.getByRole("button", { name: /Redactar/ }));
    await waitFor(() => expect(api.generarBorradorNuevo).toHaveBeenCalledWith({
      destinatario: "nuevo@cliente.io", instruccion: "contéstale lo del tubo", alcance: "contacto",
      contact_id: "c-1", fuentes: { webs: false, soporte: true },
    }));
  });

  it("se recuerdan por usuario de un correo a otro; otro usuario empieza con las de partida", async () => {
    const primero = montar();
    await usuarioCargado();
    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: "Soporte" }));
    expect(JSON.parse(window.localStorage.getItem("bohub.asistente.fuentes.u-1") ?? "null"))
      .toEqual({ webs: true, soporte: false });
    // Sin conversación, tocar una casilla no llama a nada: vale para la petición siguiente.
    expect(api.generarBorradorAsistente).not.toHaveBeenCalled();
    primero.unmount();
    // Otro correo, el mismo usuario: como lo dejó.
    const segundo = montar();
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Soporte" })).not.toBeChecked());
    expect(screen.getByRole("checkbox", { name: "Webs" })).toBeChecked();
    segundo.unmount();
    // Otro usuario en el mismo navegador: las de partida.
    usuarios.getCurrentUser.mockResolvedValueOnce({ id: "u-2" });
    montar();
    await waitFor(() => expect(usuarios.getCurrentUser).toHaveBeenCalledTimes(3));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByRole("checkbox", { name: "Webs" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Soporte" })).toBeChecked();
  });

  it("«Lo que ha usado» dice qué consultó solo, con sus enlaces", async () => {
    api.generarBorradorAsistente.mockResolvedValueOnce(borrador({
      contexto: {
        mensajes_hilo: 2, contacto: { nombre: "Klaus" },
        material: {
          automatico: true, fuentes: TODAS,
          consulta: { tecnica: true, marca: "flux", soporte_en: "beamo laser tube", soporte: "beamo tubo láser",
            webs: "beamo", origen: "ia", modelo: "claude-haiku-5-5", error: null, plan_ms: 800, coste_usd: 0.0003 },
          soporte: { ref: "S1", fuente: "flux", fuente_nombre: "FLUX · centro de ayuda", articulo_id: "4410",
            titulo: "Cambiar el tubo láser del beamo", url: "https://support.flux3dp.com/hc/de/articles/4410",
            idioma: "de", recortado: false, parece_orden: false, usado: true },
          webs: [{ ref: "W1", id: "w-1", titulo: "beamo", url: "https://www.fluxlasers.eu/de/produkt/beamo/",
            sitio: "fluxlasers", dominio: "fluxlasers.eu", tipo: "producto", idioma: "de",
            precio_web_texto: null, precio_iva: null, usado: false }],
          buscado_en_soporte: ["beamo laser tube"], ms: 1300, material_usado: ["S1"],
        },
      },
    }));
    montar();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Redactar/ }));
    await desplegar(user);
    expect(screen.getByText(/Lo que ha usado/)).toHaveTextContent("· consultó soporte y webs");
    const trajo = screen.getByRole("list", { name: "Lo que trajo solo de webs y soporte" });
    expect(within(trajo).getByRole("link", { name: "Cambiar el tubo láser del beamo" })).toHaveAttribute(
      "href", "https://support.flux3dp.com/hc/de/articles/4410");
    expect(trajo).toHaveTextContent("FLUX · centro de ayuda · usado en el borrador");
    expect(trajo).toHaveTextContent("Web: beamo — fluxlasers.eu · producto · no lo usó");
    expect(screen.queryByRole("list", { name: "Material de fuera que entró" })).not.toBeInTheDocument();
  });

  it("un borrador sin nada de fuera no dice nada de fuera", async () => {
    api.generarBorradorAsistente.mockResolvedValueOnce(borrador());
    montar();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Redactar/ }));
    await desplegar(user);
    expect(screen.getByText(/Lo que ha usado/)).not.toHaveTextContent("consultó");
    expect(screen.queryByRole("list", { name: "Lo que trajo solo de webs y soporte" })).not.toBeInTheDocument();
  });
});
