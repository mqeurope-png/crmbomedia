import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FichaDocumentoPage from "./page";

/** Ficha de un documento de cualquier año. Figurantes. */

jest.mock("next/navigation", () => ({
  useParams: () => ({ tipo: "factura", ejercicio: "2019", serie: "1", codigo: "190001" }),
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a>,
}));
jest.mock("../../../../../../../components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <header><h1>{title}</h1>{actions}</header>
  ),
}));
jest.mock("../../../../../../../lib/erpApi", () => ({
  getFichaDocumento: jest.fn(),
  downloadFactusolDocumentPdf: jest.fn(),
  saveBlob: jest.fn(),
}));

function api() {
  return jest.requireMock("../../../../../../../lib/erpApi") as Record<string, jest.Mock>;
}

const FICHA = {
  fuente: "volcado", cargado_at: "2026-10-30T05:21:00Z", tipo: "factura", tipo_label: "Factura",
  doc_type: "facturas", ejercicio: "2019", serie: "1", codigo: 190001, numero: "1-190001",
  fecha: "2019-05-06", fecha_original: null,
  cliente: { codcli: "4101", nombre: "Talleres Figurante S.L.",
             empresa: { id: "emp-1", nombre: "Talleres Figurante" } },
  referencia: null, total: 1210, suma_lineas: 1000, estado: "2", estado_label: "Cobrada",
  pedido: null,
  lineas: [
    { posicion: 1, codart: "1712", descripcion: "LASER 1390 ANTIGUO",
      articulo: "Láser Figurante 1390 (catálogo 2019)", familia: "LAS", clase: "maquina",
      gama: "laser", cantidad: 1, precio: 1000, total: 1000, es_nota: false },
    { posicion: 2, codart: null, descripcion: "Portes incluidos.", articulo: null, familia: null,
      clase: null, gama: null, cantidad: 0, precio: 0, total: 0, es_nota: true },
  ],
};

beforeEach(() => {
  api().getFichaDocumento.mockResolvedValue(FICHA);
  api().downloadFactusolDocumentPdf.mockResolvedValue(new Blob(["x"]));
});

test("enseña cabecera, líneas con el nombre de su año y las notas aparte", async () => {
  render(<FichaDocumentoPage />);
  expect(await screen.findByRole("heading", { name: "Factura 1-190001" })).toBeInTheDocument();
  expect(api().getFichaDocumento).toHaveBeenCalledWith("factura", "2019", "1", "190001");
  expect(screen.getByRole("link", { name: "Talleres Figurante" })).toHaveAttribute("href", "/companies/emp-1");
  expect(screen.getByText("Cobrada")).toBeInTheDocument();
  expect(screen.getByText("Sin pedido de BoHub")).toBeInTheDocument();
  const tabla = screen.getByRole("table");
  expect(within(tabla).getByText("Láser Figurante 1390 (catálogo 2019)")).toBeInTheDocument();
  expect(within(tabla).queryByText("Portes incluidos.")).not.toBeInTheDocument();
  expect(screen.getByText(/Notas del documento/)).toBeInTheDocument();
  expect(screen.getByText("Portes incluidos.")).toBeInTheDocument();
});

test("el PDF se pide con su ejercicio", async () => {
  const user = userEvent.setup();
  render(<FichaDocumentoPage />);
  await user.click(await screen.findByRole("button", { name: "PDF" }));
  expect(api().downloadFactusolDocumentPdf).toHaveBeenCalledWith("facturas", 1, 190001, "es",
    { ejercicio: "2019" });
  expect(api().saveBlob).toHaveBeenCalled();
});

test("sin total guardado dice que es la suma de líneas", async () => {
  api().getFichaDocumento.mockResolvedValue({ ...FICHA, total: null,
    pedido: { id: "o-1", order_number: "FIG-1" } });
  render(<FichaDocumentoPage />);
  expect(await screen.findByText(/suma de líneas, sin impuestos/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "FIG-1" })).toHaveAttribute("href", "/erp/orders/o-1");
});
