import { render, screen, waitFor } from "@testing-library/react";
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
}));

const api = jest.requireMock("../../lib/emailsApi") as {
  generarBorradorAsistente: jest.Mock;
  generarBorradorNuevo: jest.Mock;
  turnoAsistente: jest.Mock;
  descartarBorradorAsistente: jest.Mock;
};

function borrador(over: Partial<BorradorAsistente> = {}): BorradorAsistente {
  return {
    id: "b-0", thread_id: "t-1", message_id: null, contact_id: "c-1", usuario_id: "u-1",
    idioma: "de", modelo: "claude-sonnet-5-5", tokens_entrada: 1200, tokens_salida: 250,
    tokens_cache_lectura: 0, tokens_cache_escritura: 1100, coste_usd: 0.0049,
    destinatario: "klaus@elbarquito.net", remitente: "info@bomedia.net",
    asunto: "Re: Metallplatten", destinatario_interno: false, sin_hilo: false,
    texto: "Hallo Klaus,\n\nvielen Dank.", html: "<p>Hallo Klaus,</p><p>vielen Dank.</p>",
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

beforeEach(() => {
  jest.clearAllMocks();
  window.localStorage.clear();
});

describe("AsistenteFranja — lo primero es la caja", () => {
  it("respondiendo, «Redactar» con la caja vacía pide el borrador deducido del hilo y lo pone en el editor", async () => {
    api.generarBorradorAsistente.mockResolvedValueOnce(borrador());
    const { onBorrador, onBorradorActual } = montar();
    const user = userEvent.setup();
    expect(screen.getByLabelText("Qué quieres decirle")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Redactar/ }));
    await waitFor(() => expect(onBorrador).toHaveBeenCalledTimes(1));
    expect(api.generarBorradorAsistente).toHaveBeenCalledWith("t-1", {
      alcance: "contacto", instruccion: null, destinatario: "klaus@elbarquito.net",
    });
    expect(onBorradorActual).toHaveBeenCalledWith("b-0");
    expect(screen.getByText("Borrador inicial, deducido del hilo")).toBeInTheDocument();
    // Con borrador, el botón pasa a «Aplicar» y la caja pide correcciones.
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
      alcance: "contacto", contact_id: "c-1",
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
    await user.type(screen.getByLabelText("Qué quieres decirle"), "más corto");
    await user.click(screen.getByRole("button", { name: /Aplicar/ }));
    await waitFor(() => expect(api.turnoAsistente).toHaveBeenCalledWith("b-0", "más corto"));
    await waitFor(() => expect(onBorrador).toHaveBeenCalledTimes(2));
    expect(onBorrador.mock.calls[1][0].id).toBe("b-1");
    expect(onBorradorActual).toHaveBeenLastCalledWith("b-1");
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

  it("lo que la IA le dice a la persona va en la franja, nunca al cuerpo", async () => {
    api.generarBorradorAsistente.mockResolvedValueOnce(borrador({
      texto: "", html: "",
      aviso: "El último mensaje del hilo es nuestro: no hay nada nuevo del cliente que contestar.",
    }));
    const { onBorrador } = montar();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Redactar/ }));
    await waitFor(() => expect(onBorrador).toHaveBeenCalledTimes(1));
    expect(onBorrador.mock.calls[0][0].html).toBe("");
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
    await screen.findByText("Borrador inicial, deducido del hilo");
    await user.click(screen.getByRole("button", { name: "Descartar el borrador de la IA" }));
    expect(api.descartarBorradorAsistente).toHaveBeenCalledWith("b-0");
    expect(onVaciar).toHaveBeenCalledTimes(1);
    expect(onBorradorActual).toHaveBeenLastCalledWith(null);
    expect(screen.queryByLabelText("Qué quieres decirle")).not.toBeInTheDocument();
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
    montar({ destinatarioInterno: true });
    expect(screen.getByRole("status")).toHaveTextContent("Vas a escribir a un compañero");
    montar({ threadId: null, abiertaInicial: false });
    expect(screen.getByRole("button", { name: /Ayúdame a redactar/ })).toBeInTheDocument();
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
