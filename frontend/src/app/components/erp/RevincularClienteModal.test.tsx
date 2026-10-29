import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RevincularClienteModal } from "./RevincularClienteModal";

/** «Cambiar cliente» del pedido: buscar en FACTUSOL y revincular, o crear el
 *  cliente comprobando antes el CIF. Datos inventados. */

const searchFactusolCustomers = jest.fn();
const previewRevincularCliente = jest.fn();
const revincularCliente = jest.fn();
const previewClienteNuevo = jest.fn();
const crearClienteNuevo = jest.fn();
jest.mock("../../lib/erpApi", () => ({
  searchFactusolCustomers: (...a: unknown[]) => searchFactusolCustomers(...a),
  previewRevincularCliente: (...a: unknown[]) => previewRevincularCliente(...a),
  revincularCliente: (...a: unknown[]) => revincularCliente(...a),
  previewClienteNuevo: (...a: unknown[]) => previewClienteNuevo(...a),
  crearClienteNuevo: (...a: unknown[]) => crearClienteNuevo(...a),
}));

const NUEVO = {
  codcli: "4321", nombre: "Componentes Ejemplo SL", nif: "B00000001", direccion: "Calle Falsa 1",
  cp: "28000", ciudad: "Madrid", provincia: "Madrid", pais: "724", regime: "nacional",
};

function plan(over: Record<string, unknown> = {}) {
  return {
    cliente: NUEVO,
    actual: { company_id: "c-old", nombre: "Cliente Anterior SA", codcli: "1234" },
    empresa: { company_id: "c-new", nombre: "Componentes Ejemplo SL", crear: false },
    sin_cambios_crm: false, factura: null, documento: null, avisos: [], ...over,
  };
}

beforeEach(() => {
  for (const m of [searchFactusolCustomers, previewRevincularCliente, revincularCliente,
    previewClienteNuevo, crearClienteNuevo]) m.mockReset();
  searchFactusolCustomers.mockResolvedValue([{
    codcli: "4321", nombre: "Componentes Ejemplo SL", nif: "B00000001", pobcli: "Madrid",
    crm_link: null, regime_label: "Nacional",
  }]);
});

function abrir(factura: string | null = null) {
  return render(
    <RevincularClienteModal
      orderId="o-1" orderNumber="BOPRIN-9" factura={factura} clienteActual="Cliente Anterior SA"
      onClose={jest.fn()}
    />,
  );
}

async function buscarYElegir(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Cliente en FACTUSOL"), "componentes");
  await user.click(screen.getByRole("button", { name: "Buscar en FACTUSOL" }));
  await waitFor(() => expect(searchFactusolCustomers).toHaveBeenCalledWith("componentes", "name"));
  await user.click(await screen.findByRole("button", { name: "Elegir" }));
}

describe("RevincularClienteModal", () => {
  it("revincula a un cliente que ya existe: vista previa de qué a qué y confirmación", async () => {
    previewRevincularCliente.mockResolvedValue(plan());
    revincularCliente.mockResolvedValue({
      ok: true, company_id: "c-new", company_name: "Componentes Ejemplo SL", codcli: "4321",
      empresa_creada: false, factura: null, escrito_en_factura: false,
    });
    const user = userEvent.setup();
    abrir();
    await buscarYElegir(user);
    const previa = within(await screen.findByLabelText("Vista previa del cambio"));
    expect(previa.getByText(/^De /)).toHaveTextContent(
      "De Cliente Anterior SA (1234) a Componentes Ejemplo SL (4321 · B00000001 · nacional (con IVA)).",
    );
    const cambiar = screen.getByRole("button", { name: "Cambiar el cliente" });
    expect(cambiar).toBeDisabled();
    await user.click(screen.getByLabelText("Confirmo el cambio de cliente"));
    await user.click(cambiar);
    await waitFor(() => expect(revincularCliente).toHaveBeenCalledWith("o-1", "4321", []));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "El pedido BOPRIN-9 queda con «Componentes Ejemplo SL» (4321).",
    );
  });

  it("con factura emitida avisa antes, enseña campo a campo lo que cambia en ella y lo manda", async () => {
    previewRevincularCliente.mockResolvedValue(plan({
      factura: {
        numero: "2-526090", bloqueo: null, puedes: true, enviada: { at: "2026-10-01", via: "email" },
        avisos: ["Cambiar el destinatario de una factura ya emitida es, en la norma, una rectificativa."],
        cambios: [
          { campo: "CLIFAC", label: "Cliente (código)", antes: "1234", despues: "4321" },
          { campo: "CNOFAC", label: "Nombre", antes: "Cliente Anterior SA", despues: "Componentes Ejemplo SL" },
        ],
      },
    }));
    revincularCliente.mockResolvedValue({
      ok: true, company_id: "c-new", company_name: "Componentes Ejemplo SL", codcli: "4321",
      empresa_creada: false, factura: "2-526090", escrito_en_factura: true,
    });
    const user = userEvent.setup();
    abrir("2-526090");
    expect(screen.getByText(/ya tiene la factura 2-526090 emitida/)).toBeInTheDocument();
    await buscarYElegir(user);
    const previa = within(await screen.findByLabelText("Vista previa del cambio"));
    expect(previa.getByRole("row", { name: /Nombre/ }))
      .toHaveTextContent("NombreCliente Anterior SAComponentes Ejemplo SL");
    expect(previa.getByText(/el PDF que tiene lleva el cliente anterior/)).toBeInTheDocument();
    expect(previa.getByText(/una rectificativa/)).toBeInTheDocument();
    await user.click(screen.getByLabelText("Confirmo el cambio de cliente"));
    await user.click(screen.getByRole("button", { name: "Cambiar el cliente" }));
    await waitFor(() => expect(revincularCliente).toHaveBeenCalledWith(
      "o-1", "4321", ["CLIFAC", "CNOFAC"],
    ));
  });

  it("si la factura no se puede cambiar (IVA, VERI*FACTU) lo dice y no deja confirmar", async () => {
    previewRevincularCliente.mockResolvedValue(plan({
      factura: {
        numero: "2-526090", puedes: true, enviada: null, avisos: [],
        bloqueo: "La factura lleva IVA y el cliente nuevo es de régimen intracomunitario.",
        cambios: [{ campo: "CLIFAC", label: "Cliente (código)", antes: "1234", despues: "4321" }],
      },
    }));
    const user = userEvent.setup();
    abrir("2-526090");
    await buscarYElegir(user);
    expect(await screen.findByText(/No se puede cambiar la factura/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cambiar el cliente" })).toBeNull();
  });

  it("crear: comprueba el CIF ANTES y, si ya existe, ofrece ese y no crea otro", async () => {
    previewClienteNuevo.mockResolvedValue({
      existe: true, crm: [], campos: [],
      factusol: [{ codcli: "4321", nombre: "Componentes Ejemplo SL", nif: "B00000001" }],
    });
    previewRevincularCliente.mockResolvedValue(plan());
    const user = userEvent.setup();
    abrir();
    await user.click(screen.getByRole("button", { name: "Crear el cliente en FACTUSOL" }));
    await user.type(screen.getByLabelText("Razón social"), "Componentes Ejemplo");
    await user.type(screen.getByLabelText("CIF / NIF"), "B00000001");
    await user.click(screen.getByRole("button", { name: "Comprobar CIF" }));
    expect(await screen.findByText(/Ya hay un cliente con ese CIF en FACTUSOL: no se crea otro/))
      .toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Crear cliente" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Usar este" }));
    await waitFor(() => expect(previewRevincularCliente).toHaveBeenCalledWith("o-1", "4321"));
    expect(crearClienteNuevo).not.toHaveBeenCalled();
  });

  it("crear: sin CIF repetido enseña los campos exactos, pide confirmación y luego revincula", async () => {
    previewClienteNuevo.mockResolvedValue({
      existe: false, crm: [], factusol: [], regime: "nacional", regime_label: "Nacional",
      campos: [{ campo: "NOFCLI", valor: "Componentes Nuevos SL" }, { campo: "NIFCLI", valor: "B00000002" }],
    });
    crearClienteNuevo.mockResolvedValue({ codcli: "5000", creado: true });
    previewRevincularCliente.mockResolvedValue(plan({ cliente: { ...NUEVO, codcli: "5000" } }));
    const user = userEvent.setup();
    abrir();
    await user.click(screen.getByRole("button", { name: "Crear el cliente en FACTUSOL" }));
    await user.type(screen.getByLabelText("Razón social"), "Componentes Nuevos SL");
    await user.type(screen.getByLabelText("CIF / NIF"), "B00000002");
    await user.click(screen.getByRole("button", { name: "Comprobar CIF" }));
    const previa = within(await screen.findByLabelText("Cliente que se creará"));
    expect(previa.getByRole("row", { name: /NOFCLI/ })).toHaveTextContent("Componentes Nuevos SL");
    const crear = screen.getByRole("button", { name: "Crear cliente" });
    expect(crear).toBeDisabled();
    await user.click(screen.getByLabelText("Confirmo crear el cliente"));
    await user.click(crear);
    await waitFor(() => expect(crearClienteNuevo).toHaveBeenCalledWith("o-1", expect.objectContaining({
      nombre: "Componentes Nuevos SL", nif: "B00000002",
    })));
    await waitFor(() => expect(previewRevincularCliente).toHaveBeenCalledWith("o-1", "5000"));
  });
});
