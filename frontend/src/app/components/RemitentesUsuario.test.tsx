import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { EmailAlias } from "../lib/emailsApi";
import { getUserSenders, putUserSenders } from "../lib/userAliasesApi";
import { RemitentesUsuario } from "./RemitentesUsuario";

jest.mock("../lib/userAliasesApi", () => ({
  getUserSenders: jest.fn(),
  putUserSenders: jest.fn(),
}));
jest.mock("../lib/errors", () => ({
  extractErrorMessage: (_err: unknown, fallback: string) => fallback,
}));

const mockGet = getUserSenders as jest.MockedFunction<typeof getUserSenders>;
const mockPut = putUserSenders as jest.MockedFunction<typeof putUserSenders>;

function alias(email: string, over: Partial<EmailAlias> = {}): EmailAlias {
  return {
    send_as_email: email,
    display_name: email.split("@")[0],
    is_primary: false,
    is_default: false,
    verification_status: "accepted",
    user_pref_allowed: false,
    user_pref_default: false,
    user_pref_opted_in: null,
    gmail_display_name: email.split("@")[0],
    display_name_override: null,
    resolved_display_name: email.split("@")[0],
    en_gmail: true,
    de_entrada: false,
    menciona_bomedia: false,
    ...over,
  };
}

const MANEL = [
  alias("manel@bomedia.net", {
    user_pref_allowed: true,
    user_pref_default: true,
    resolved_display_name: "Manel · Bomedia",
    menciona_bomedia: true,
  }),
  alias("ventas@streamtec.es", { de_entrada: true }),
  alias("info@mbolasers.com", { en_gmail: false, de_entrada: true }),
];

/** El selector guarda solo, 400 ms después del último cambio. */
async function ultimoGuardado() {
  await waitFor(() => expect(mockPut).toHaveBeenCalled(), { timeout: 2000 });
  const [userId, prefs] = mockPut.mock.calls[mockPut.mock.calls.length - 1];
  return { userId, prefs };
}

describe("RemitentesUsuario — los remitentes del «De» en la ficha de Usuarios", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockGet.mockResolvedValue(MANEL);
    mockPut.mockImplementation(async (_id, prefs) =>
      MANEL.map((a) => {
        const p = prefs.find((x) => x.alias_email === a.send_as_email);
        return p
          ? { ...a, user_pref_allowed: p.is_allowed, user_pref_default: p.is_default }
          : a;
      }),
    );
  });

  it("enseña lo marcado, las marcas de cada dirección y lo que recibe sin poder escribir", async () => {
    render(<RemitentesUsuario userId="u-manel" />);
    expect(await screen.findByText("Manel · Bomedia")).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith("u-manel");
    expect(screen.getByText("nombra Bomedia")).toBeInTheDocument();
    expect(screen.getByText(/1 alias activo/)).toBeInTheDocument();
    // Las dos direcciones de entrada que no son remitentes: se dice.
    expect(
      screen.getByText(/Recibe correo en ventas@streamtec.es, info@mbolasers.com/),
    ).toHaveTextContent("no las tiene como remitente");
    // Abierto: la que Gmail no tiene sale marcada, y las de entrada, como «recibe».
    await userEvent.setup().click(screen.getByRole("button", { name: /1 alias activo/ }));
    const fila = screen.getByText("<info@mbolasers.com>").closest("li") as HTMLElement;
    expect(within(fila).getByText("no verificada en Gmail")).toBeInTheDocument();
    expect(within(fila).getByText("recibe")).toBeInTheDocument();
    expect(screen.getByText(/2 alias en Gmail · 1 fuera de Gmail · 1 marcado/)).toBeInTheDocument();
  });

  it("marcar un remitente verificado lo guarda para ese usuario, con un solo predeterminado", async () => {
    const user = userEvent.setup();
    render(<RemitentesUsuario userId="u-manel" />);
    await user.click(await screen.findByRole("button", { name: /1 alias activo/ }));
    const fila = screen.getByText("<ventas@streamtec.es>").closest("li") as HTMLElement;
    await user.click(within(fila).getByRole("checkbox"));
    const marcado = await ultimoGuardado();
    expect(marcado.userId).toBe("u-manel");
    expect(marcado.prefs.find((p) => p.alias_email === "ventas@streamtec.es")).toMatchObject({
      is_allowed: true,
      is_default: false,
    });
    mockPut.mockClear();
    // La estrella: pasa a ser el predeterminado, y el otro deja de serlo.
    await user.click(
      await screen.findByRole("button", { name: "Marcar ventas@streamtec.es como predeterminado" }),
    );
    const { prefs } = await ultimoGuardado();
    expect(prefs.filter((p) => p.is_default).map((p) => p.alias_email)).toEqual([
      "ventas@streamtec.es",
    ]);
  });

  it("marcar una dirección que Gmail no tiene verificada avisa antes", async () => {
    const user = userEvent.setup();
    const confirmar = jest.spyOn(window, "confirm").mockReturnValue(false);
    render(<RemitentesUsuario userId="u-manel" />);
    await user.click(await screen.findByRole("button", { name: /1 alias activo/ }));
    const fila = screen.getByText("<info@mbolasers.com>").closest("li") as HTMLElement;
    await user.click(within(fila).getByRole("checkbox"));
    expect(confirmar).toHaveBeenCalledWith(expect.stringContaining("Gmail rechazaría el envío"));
    expect(within(fila).getByRole("checkbox")).not.toBeChecked();
    // Si se confirma, se marca y se guarda.
    confirmar.mockReturnValue(true);
    await user.click(within(fila).getByRole("checkbox"));
    const { prefs } = await ultimoGuardado();
    expect(prefs.find((p) => p.alias_email === "info@mbolasers.com")?.is_allowed).toBe(true);
    confirmar.mockRestore();
  });

  it("el nombre visible se cambia con el lápiz, sin tocar Gmail", async () => {
    const user = userEvent.setup();
    render(<RemitentesUsuario userId="u-manel" />);
    await user.click(
      await screen.findByRole("button", { name: "Editar display name de manel@bomedia.net" }),
    );
    const input = screen.getByPlaceholderText("Display name");
    await user.clear(input);
    await user.type(input, "Manel Pons{Enter}");
    const { prefs } = await ultimoGuardado();
    expect(prefs.find((p) => p.alias_email === "manel@bomedia.net")).toMatchObject({
      display_name_override: "Manel Pons",
    });
  });

  it("sin remitentes marcados lo dice de ese usuario", async () => {
    mockGet.mockResolvedValue([alias("manel@bomedia.net"), alias("ventas@streamtec.es")]);
    render(<RemitentesUsuario userId="u-manel" />);
    expect(
      await screen.findByText(/No tiene ningún remitente marcado: su desplegable «De» está vacío/),
    ).toBeInTheDocument();
  });

  it("si Gmail no responde, enseña lo guardado sin dejar cambiarlo", async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValueOnce([
      alias("manel@bomedia.net", { en_gmail: null }),
      alias("ventas@streamtec.es", {
        en_gmail: null,
        user_pref_allowed: true,
        user_pref_default: true,
        resolved_display_name: "Streamtec",
      }),
    ]);
    render(<RemitentesUsuario userId="u-manel" />);
    expect(await screen.findByText(/No se ha podido consultar Gmail/)).toHaveTextContent(
      "no se puede cambiar hasta que responda",
    );
    const guardados = screen.getByRole("list", { name: "Remitentes guardados" });
    expect(guardados).toHaveTextContent("Streamtec<ventas@streamtec.es>predeterminado");
    expect(guardados).not.toHaveTextContent("manel@bomedia.net");
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /alias activo/ })).not.toBeInTheDocument();
    // Reintentar: Gmail ya responde y vuelve el selector.
    await user.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByRole("button", { name: /1 alias activo/ })).toBeInTheDocument();
  });

  it("una elegida que Gmail no tiene no puede ser la predeterminada", async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue([
      alias("manel@bomedia.net", { user_pref_allowed: true, user_pref_default: true }),
      alias("info@mbolasers.com", {
        en_gmail: false,
        user_pref_allowed: true,
        user_pref_opted_in: true,
      }),
    ]);
    render(<RemitentesUsuario userId="u-manel" />);
    const estrella = await screen.findByRole("button", {
      name: "Marcar info@mbolasers.com como predeterminado",
    });
    expect(estrella).toBeDisabled();
    expect(estrella).toHaveAttribute("title", expect.stringContaining("Gmail no la tiene verificada"));
    // Si se quita la que era predeterminada, no pasa a serlo la pendiente.
    await user.click(screen.getByRole("button", { name: "Quitar manel@bomedia.net de los aliases activos" }));
    const { prefs } = await ultimoGuardado();
    expect(prefs.filter((p) => p.is_default)).toEqual([]);
  });

  it("el nombre de una dirección desmarcada también se cambia, desde la lista", async () => {
    const user = userEvent.setup();
    render(<RemitentesUsuario userId="u-manel" />);
    await user.click(await screen.findByRole("button", { name: /1 alias activo/ }));
    await user.click(
      screen.getByRole("button", { name: "Editar display name de ventas@streamtec.es" }),
    );
    const input = screen.getByRole("textbox", { name: "Nombre visible de ventas@streamtec.es" });
    await user.clear(input);
    await user.type(input, "Ventas Streamtec{Enter}");
    const { prefs } = await ultimoGuardado();
    expect(prefs.find((p) => p.alias_email === "ventas@streamtec.es")).toEqual({
      alias_email: "ventas@streamtec.es",
      is_allowed: false,
      is_default: false,
      display_name_override: "Ventas Streamtec",
    });
  });

  it("una recarga que sale antes de guardar no deshace lo guardado", async () => {
    const user = userEvent.setup();
    let devolverViejo: (v: EmailAlias[]) => void = () => {};
    render(<RemitentesUsuario userId="u-manel" />);
    await user.click(await screen.findByRole("button", { name: /1 alias activo/ }));
    // ↻ con Gmail lento…
    mockGet.mockImplementationOnce(
      () => new Promise<EmailAlias[]>((resolve) => (devolverViejo = resolve)),
    );
    await user.click(screen.getByTitle("Recargar lista desde Gmail"));
    // …y mientras, marca ventas@ (se guarda).
    const fila = screen.getByText("<ventas@streamtec.es>").closest("li") as HTMLElement;
    await user.click(within(fila).getByRole("checkbox"));
    await ultimoGuardado();
    await waitFor(() =>
      expect(within(fila).getByRole("checkbox")).toBeChecked(),
    );
    // Llega tarde la recarga, leída antes del guardado: no cuenta.
    devolverViejo(MANEL);
    await new Promise((r) => setTimeout(r, 50));
    expect(within(fila).getByRole("checkbox")).toBeChecked();
    expect(screen.getByText(/2 alias activos/)).toBeInTheDocument();
  });

  it("sin ninguna dirección avisa de que Gmail puede no responder", async () => {
    mockGet.mockResolvedValue([]);
    render(<RemitentesUsuario userId="u-nuevo" />);
    expect(await screen.findByText(/No sale ninguna dirección/)).toHaveTextContent(
      "puede que Gmail no responda",
    );
  });

  it("si no se pueden cargar, lo dice", async () => {
    mockGet.mockRejectedValue(new Error("403"));
    render(<RemitentesUsuario userId="u-manel" />);
    expect(await screen.findByText("No se pudieron cargar los remitentes")).toBeInTheDocument();
  });
});
