import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  getClienteWebPedido,
  vincularEmpresaPedido,
  type ClienteWebPedido,
} from "../../lib/erpApi";
import { VincularEmpresaPedidoPanel } from "./VincularEmpresaPedidoPanel";

jest.mock("../../lib/erpApi", () => ({
  getClienteWebPedido: jest.fn(),
  vincularEmpresaPedido: jest.fn(),
}));
jest.mock("../../lib/errors", () => ({
  extractErrorMessage: (err: unknown, fallback: string) =>
    err instanceof Error && err.message ? err.message : fallback,
}));
// El buscador real consulta la API: aquí basta con elegir una empresa.
jest.mock("../CompanySearch", () => ({
  CompanySearch: ({ onPick, initialQuery }: {
    onPick: (c: { id: string; name: string }) => void; initialQuery?: string;
  }) => (
    <button type="button" onClick={() => onPick({ id: "c-otra", name: "Otra SL" })}>
      elegir en el buscador ({initialQuery})
    </button>
  ),
}));

const mockGet = getClienteWebPedido as jest.MockedFunction<typeof getClienteWebPedido>;
const mockLink = vincularEmpresaPedido as jest.MockedFunction<typeof vincularEmpresaPedido>;

const BASE: ClienteWebPedido = {
  faltan: ["empresa", "nif", "direccion"],
  faltan_texto: ["sin empresa", "sin NIF", "sin dirección de facturación"],
  nif: null,
  nombre: "Nadine",
  email: "nadine@example.com",
  propuestas: [],
  factusol: { disponible: true, referencia: "BOP-100015", pedido_encontrado: false },
  mensaje: "La web no trae NIF y el pedido no está en FACTUSOL como pedido de cliente (BOP-100015): no hay con qué emparejarlo. Búscala a mano.",
};

describe("VincularEmpresaPedidoPanel — pedido web sin cliente", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockLink.mockResolvedValue({
      id: "o-15", company_id: "c-1", company_name: "X", codcli: null, creada: false,
    });
  });

  it("dice qué falta y, sin con qué emparejar, deja buscarla a mano", async () => {
    const user = userEvent.setup();
    const onLinked = jest.fn();
    mockGet.mockResolvedValue(BASE);
    render(<VincularEmpresaPedidoPanel orderId="o-15" canEdit onLinked={onLinked} />);
    expect(await screen.findByText(/Pedido web sin empresa, sin NIF y sin dirección de facturación/))
      .toBeInTheDocument();
    expect(screen.getByText("La web trae: Nadine · nadine@example.com")).toBeInTheDocument();
    expect(screen.getByText(/no hay con qué emparejarlo\. Búscala a mano\./)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Empresas propuestas" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Buscar la empresa" }));
    await user.click(screen.getByRole("button", { name: /elegir en el buscador/ }));
    await waitFor(() => expect(mockLink).toHaveBeenCalledWith("o-15", {
      company_id: "c-otra", origen: "busqueda",
    }));
    expect(onLinked).toHaveBeenCalled();
  });

  it("propone la empresa por el NIF de la web o por el pedido de FACTUSOL y la vincula", async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue({
      ...BASE,
      faltan: ["empresa"],
      faltan_texto: ["sin empresa"],
      nif: "B17643313",
      mensaje: null,
      propuestas: [
        {
          company_id: null, codcli: "1611", nombre: "COMPONENTS FRANCE SPORT, S.L.",
          nif: "B17643313", origen: "pedido_factusol",
          motivo: "El pedido llegó a FACTUSOL como pedido de cliente BOP-100015, del cliente 1611.",
        },
        {
          company_id: "c-2", codcli: null, nombre: "Components France Sport (CRM)",
          nif: "B17643313", origen: "nif",
          motivo: "El NIF del pedido (B17643313) es el de esta empresa.",
        },
      ],
    });
    render(<VincularEmpresaPedidoPanel orderId="o-15" canEdit onLinked={jest.fn()} />);
    const lista = await screen.findByRole("list", { name: "Empresas propuestas" });
    expect(lista).toHaveTextContent("COMPONENTS FRANCE SPORT, S.L.");
    expect(lista).toHaveTextContent("pedido de cliente en FACTUSOL");
    expect(lista).toHaveTextContent("NIF en BoHub");
    expect(screen.getByText("La web trae: Nadine · nadine@example.com · NIF B17643313"))
      .toBeInTheDocument();
    const [primero, segundo] = screen.getAllByRole("button", { name: "Vincular" });
    await user.click(primero);
    expect(mockLink).toHaveBeenLastCalledWith("o-15", { codcli: "1611", origen: "pedido_factusol" });
    await user.click(segundo);
    expect(mockLink).toHaveBeenLastCalledWith("o-15", { company_id: "c-2", origen: "nif" });
    expect(screen.getByRole("button", { name: "Buscar otra empresa" })).toBeInTheDocument();
  });

  it("si no se puede vincular, lo dice; sin permiso, no ofrece botones", async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue({
      ...BASE,
      propuestas: [{
        company_id: "c-2", codcli: null, nombre: "X SL", nif: "B1", origen: "nif",
        motivo: "El NIF del pedido (B1) es el de esta empresa.",
      }],
    });
    mockLink.mockRejectedValue(new Error("El pedido ya está facturado: su cliente es el de la factura."));
    const { unmount } = render(
      <VincularEmpresaPedidoPanel orderId="o-15" canEdit onLinked={jest.fn()} />,
    );
    await user.click(await screen.findByRole("button", { name: "Vincular" }));
    expect(await screen.findByText(/ya está facturado/)).toBeInTheDocument();
    unmount();

    render(<VincularEmpresaPedidoPanel orderId="o-15" canEdit={false} onLinked={jest.fn()} />);
    expect(await screen.findByText("X SL")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Vincular" })).not.toBeInTheDocument();
    expect(screen.getByText("Vincular la empresa es de quien edita pedidos.")).toBeInTheDocument();
  });
});
