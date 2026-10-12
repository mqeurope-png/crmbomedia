import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConvertOfertaDialog } from "./ConvertOfertaDialog";
import { ApiError } from "../../lib/api";
import { convertOferta, getOferta, getOfertaConvertStatus } from "../../lib/erpApi";

/** «Convertir en presupuesto»: confirmación explícita; 409 `company_not_linked`
 *  ofrece el flujo de #502 (ficha de la empresa para vincular / crear el
 *  cliente, o la del contacto si no tiene empresa); 422 `lineas_sin_articulo`
 *  enseña las posiciones y «Editar líneas»; en éxito sondea el job y enseña el
 *  nº del presupuesto con enlace a Proformas. */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: { children: React.ReactNode; href: string; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));
jest.mock("../../lib/erpApi", () => ({
  convertOferta: jest.fn(),
  getOferta: jest.fn(),
  getOfertaConvertStatus: jest.fn(),
}));

const mockConvert = convertOferta as jest.Mock;
const mockGet = getOferta as jest.Mock;
const mockStatus = getOfertaConvertStatus as jest.Mock;

const LINEA = {
  id: "l1", posicion: 1, codart: "A1", sku: "SKU-1", descripcion: "Placa",
  cantidad: 1, precio: 100, descuento_pct: 0, iva_pct: 21, total: 100, text_only: false,
};
const OFERTA = {
  id: "of1", numero: "OF-2026-0001", anio: 2026, secuencia: 1,
  estado: "enviada" as const, estado_guardado: "enviada" as const, vencida: false,
  contact_id: "c1",
  contacto: { id: "c1", nombre: "Ana Pérez", email: "ana@acme.com", company_id: "e1" },
  company_id: "e1", empresa: { id: "e1", nombre: "Acme SL", factusol_company_id: null },
  plantilla_id: null, serie: 2, emisora: "MQ Europe", idioma: "es" as const, con_iva: true,
  fecha: "2026-10-01", validez_dias: 30, valida_hasta: "2026-10-31",
  referencia: null, notas: null,
  base: 100, iva_pct: 21, iva: 21, total: 121, version: 1,
  factusol: null, convertida_en: null, enviada_en: null, caducada_en: null,
  num_envios: 1, ultimo_envio: null, pendiente_de_envio: false,
  created_by_user_id: "u1", created_at: null, updated_at: null,
};

beforeEach(() => {
  mockConvert.mockReset();
  mockGet.mockReset();
  mockStatus.mockReset();
  mockGet.mockResolvedValue({ ...OFERTA, lineas: [LINEA, LINEA, LINEA] });
});

function base(over = {}) {
  return { oferta: OFERTA, onClose: jest.fn(), onConverted: jest.fn(), ...over };
}

describe("ConvertOfertaDialog", () => {
  it("pide confirmación con la serie, el nº de líneas y el aviso; el 409 company_not_linked con empresa enlaza a /companies/{id} con su nombre", async () => {
    mockConvert.mockRejectedValue(new ApiError("no vinculada", 409, {
      code: "company_not_linked",
      detail: "«Acme SL» no está vinculada a un cliente de FACTUSOL. Vincúlala (o créalo) antes de convertir la oferta.",
      company_id: "e1", contact_id: "c1", company_name: "Acme SL",
    }));
    const user = userEvent.setup();
    render(<ConvertOfertaDialog {...base()} />);
    const dialog = screen.getByRole("dialog", { name: "Convertir oferta en presupuesto" });
    expect(dialog).toHaveTextContent("Convertir la oferta OF-2026-0001 en presupuesto");
    expect(dialog).toHaveTextContent("en la serie 2 · MQ Europe");
    await waitFor(() => expect(dialog).toHaveTextContent("con 3 líneas"));
    expect(dialog).toHaveTextContent("solo si el cliente ha aceptado");
    expect(mockConvert).not.toHaveBeenCalled();                      // nada sin el clic

    await user.click(screen.getByRole("button", { name: "Convertir en presupuesto" }));
    await waitFor(() => expect(mockConvert).toHaveBeenCalledWith("of1"));
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent("«Acme SL» no está vinculada a un cliente de FACTUSOL");
    expect(alerta).toHaveTextContent("La empresa Acme SL no está vinculada");
    expect(screen.getByRole("link", { name: "Vincular o crear el cliente en FACTUSOL" }))
      .toHaveAttribute("href", "/companies/e1");
    expect(screen.getByRole("button", { name: "Convertir en presupuesto" })).toBeDisabled();
  });

  it("409 company_not_linked SIN empresa (company_id null) manda a la ficha del contacto", async () => {
    mockConvert.mockRejectedValue(new ApiError("sin empresa", 409, {
      code: "company_not_linked",
      detail: "El contacto no tiene empresa.",
      company_id: null, contact_id: "c1", company_name: null,
    }));
    const user = userEvent.setup();
    render(<ConvertOfertaDialog {...base({ oferta: { ...OFERTA, company_id: null, empresa: null } })} />);
    await user.click(screen.getByRole("button", { name: "Convertir en presupuesto" }));
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent("El contacto no tiene empresa: asígnale una");
    expect(screen.getByRole("link", { name: "Ir a la ficha del contacto" }))
      .toHaveAttribute("href", "/contacts/c1");
    expect(screen.queryByRole("link", { name: "Vincular o crear el cliente en FACTUSOL" })).toBeNull();
  });

  it("422 lineas_sin_articulo enseña las posiciones y «Editar líneas» avisa al padre (o cierra si no escucha)", async () => {
    mockConvert.mockRejectedValue(new ApiError("sin artículo", 422, {
      code: "lineas_sin_articulo",
      detail: "Hay líneas sin artículo de FACTUSOL (2, 4): elige el artículo de cada una antes de convertir.",
      posiciones: [2, 4],
    }));
    const onEditLines = jest.fn();
    const user = userEvent.setup();
    const { unmount } = render(<ConvertOfertaDialog {...base({ onEditLines })} />);
    await user.click(screen.getByRole("button", { name: "Convertir en presupuesto" }));
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent("Líneas sin artículo: 2, 4");
    await user.click(screen.getByRole("button", { name: "Editar líneas" }));
    expect(onEditLines).toHaveBeenCalledWith([2, 4]);
    unmount();

    const onClose = jest.fn();
    render(<ConvertOfertaDialog {...base({ onClose })} />);
    await user.click(screen.getByRole("button", { name: "Convertir en presupuesto" }));
    await user.click(await screen.findByRole("button", { name: "Editar líneas" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("éxito: encola, sondea hasta «finished», enseña el presupuesto con enlace a Proformas y llama a onConverted con la oferta recargada", async () => {
    mockConvert.mockResolvedValue({ job_id: "job-1", oferta_id: "of1", numero: "OF-2026-0001" });
    mockStatus.mockResolvedValue({
      status: "finished",
      result: {
        oferta_id: "of1", numero: "OF-2026-0001", codpre: 575, serie: 2, ejercicio: "2026",
        factusol_numero: "2-000575", lines: 3, total: 121,
      },
    });
    const convertida = {
      ...OFERTA, estado: "convertida", estado_guardado: "convertida",
      factusol: { serie: 2, codpre: 575, numero: "2-000575", ejercicio: "2026" }, lineas: [LINEA],
    };
    mockGet
      .mockResolvedValueOnce({ ...OFERTA, lineas: [LINEA, LINEA, LINEA] })   // nº de líneas al abrir
      .mockResolvedValue(convertida);                                       // tras convertir
    const onConverted = jest.fn();
    const user = userEvent.setup();
    render(<ConvertOfertaDialog {...base({ onConverted })} />);
    await user.click(screen.getByRole("button", { name: "Convertir en presupuesto" }));
    await waitFor(() => expect(mockStatus).toHaveBeenCalledWith("job-1"));
    const hecho = await screen.findByRole("status");
    expect(hecho).toHaveTextContent("Presupuesto 2-000575 creado en FACTUSOL (3 líneas, 121.00 €).");
    expect(screen.getByRole("link", { name: "Ver en Proformas" })).toHaveAttribute("href", "/erp/proformas");
    await waitFor(() => expect(onConverted).toHaveBeenCalledWith(expect.objectContaining({
      estado: "convertida", factusol: expect.objectContaining({ numero: "2-000575" }),
    })));
    expect(screen.queryByRole("button", { name: "Convertir en presupuesto" })).toBeNull();
  });

  it("si el job falla, lo dice y se puede reintentar; nada queda «convirtiendo» sin desenlace", async () => {
    mockConvert.mockResolvedValue({ job_id: "job-f", oferta_id: "of1", numero: "OF-2026-0001" });
    mockStatus.mockResolvedValue({ status: "failed", error: "FACTUSOL rechazó el presupuesto." });
    const user = userEvent.setup();
    render(<ConvertOfertaDialog {...base()} />);
    await user.click(screen.getByRole("button", { name: "Convertir en presupuesto" }));
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent("No se pudo convertir la oferta OF-2026-0001: FACTUSOL rechazó el presupuesto.");
    expect(screen.getByRole("button", { name: "Convertir en presupuesto" })).toBeEnabled();
    expect(screen.queryByText(/Convirtiendo/)).toBeNull();
  });
});
