import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import OfertasPage from "./page";
import {
  listOfertaPlantillas,
  listOfertas,
  openOfertaPdf,
  updateOfertaPlantilla,
} from "../../lib/erpApi";

/** Pantalla ERP · Ofertas: pestaña «Ofertas» (listado con filtros en el
 *  servidor y el menú «⋯» por fila: Ver / editar, PDF, Enviar por correo,
 *  Convertir en presupuesto; la convertida enlaza a su presupuesto) y pestaña
 *  «Plantillas» (editar, activar / desactivar, nueva). Los modales se prueban
 *  en sus propios tests; aquí basta con ver que se abren con la fila. */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: { children: React.ReactNode; href: string; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title, description, actions }: {
    title: string; description?: string; actions?: React.ReactNode;
  }) => <div><h1>{title}</h1><p>{description}</p>{actions}</div>,
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(() => Promise.resolve({ role: "admin" })),
}));
jest.mock("../../lib/erpApi", () => ({
  // Textos reales de los estados (filtro y pastilla).
  OFERTA_ESTADO_LABELS: jest.requireActual("../../lib/erpApi").OFERTA_ESTADO_LABELS,
  listOfertas: jest.fn(),
  listOfertaPlantillas: jest.fn(),
  openOfertaPdf: jest.fn(),
  updateOfertaPlantilla: jest.fn(),
}));
jest.mock("../../components/erp/OfertaEditorModal", () => ({
  OfertaEditorModal: ({ oferta, marcarPosiciones, onClose, onSaved }: {
    oferta?: { numero: string } | null; marcarPosiciones?: number[];
    onClose: () => void; onSaved: (o: { numero: string }) => void;
  }) => (
    <div>
      EDITOR {oferta?.numero ?? "nueva"} marcadas:{(marcarPosiciones ?? []).join(",") || "—"}
      <button type="button"
              onClick={() => onSaved({ ...(oferta ?? {}), numero: oferta?.numero ?? "OF-2026-0009" })}>
        GUARDAR EDITOR
      </button>
      <button type="button" onClick={onClose}>CERRAR EDITOR</button>
    </div>
  ),
}));
jest.mock("../../components/erp/OfertaEmailModal", () => ({
  OfertaEmailModal: ({ oferta, onClose, onSent }: {
    oferta: { numero: string }; onClose: () => void;
    onSent: (r: { numero: string; to: string[] }) => void;
  }) => (
    <div>
      EMAIL {oferta.numero}
      <button type="button" onClick={() => onSent({ numero: oferta.numero, to: ["ana@acme.com"] })}>
        ENVIAR EMAIL
      </button>
      <button type="button" onClick={onClose}>CERRAR EMAIL</button>
    </div>
  ),
}));
jest.mock("../../components/erp/ConvertOfertaDialog", () => ({
  ConvertOfertaDialog: ({ oferta, onClose, onEditLines }: {
    oferta: { numero: string }; onClose: () => void; onEditLines?: (p: number[]) => void;
  }) => (
    <div>
      CONVERTIR {oferta.numero}
      <button type="button" onClick={() => onEditLines?.([2, 4])}>EDITAR LINEAS</button>
      <button type="button" onClick={onClose}>CERRAR CONVERTIR</button>
    </div>
  ),
}));
jest.mock("../../components/erp/OfertaPlantillaModal", () => ({
  OfertaPlantillaModal: ({ plantilla, onClose, onSaved }: {
    plantilla?: { nombre: string } | null; onClose: () => void;
    onSaved: (p: { nombre: string }) => void;
  }) => (
    <div>
      PLANTILLA MODAL {plantilla?.nombre ?? "nueva"}
      <button type="button" onClick={() => onSaved(plantilla ?? { nombre: "Recién creada" })}>
        GUARDAR PLANTILLA
      </button>
      <button type="button" onClick={onClose}>CERRAR PLANTILLA</button>
    </div>
  ),
}));

const mockList = listOfertas as jest.Mock;
const mockPlantillas = listOfertaPlantillas as jest.Mock;
const mockPdf = openOfertaPdf as jest.Mock;
const mockUpdatePlantilla = updateOfertaPlantilla as jest.Mock;

const OF1 = {
  id: "of1", numero: "OF-2026-0001", anio: 2026, secuencia: 1,
  estado: "enviada", estado_guardado: "enviada", vencida: false,
  contact_id: "c1",
  contacto: { id: "c1", nombre: "Ana Pérez", email: "ana@acme.com", company_id: "e1" },
  company_id: "e1", empresa: { id: "e1", nombre: "Acme SL", factusol_company_id: null },
  plantilla_id: null, serie: 2, emisora: "MQ Europe", idioma: "es", con_iva: true,
  fecha: "2026-10-01", validez_dias: 30, valida_hasta: "2026-10-31",
  referencia: "Rótulos nave", notas: null,
  base: 100, iva_pct: 21, iva: 21, total: 121, version: 2,
  factusol: null, convertida_en: null, enviada_en: "2026-10-01T10:00:00", caducada_en: null,
  num_envios: 1, ultimo_envio: null, pendiente_de_envio: true,
  created_by_user_id: "u1", created_at: null, updated_at: null,
};
const OF2 = {
  ...OF1, id: "of2", numero: "OF-2026-0002", estado: "convertida", estado_guardado: "convertida",
  vencida: true, pendiente_de_envio: false, referencia: null,
  factusol: { serie: 2, codpre: 575, numero: "2-000575", ejercicio: "2026" },
};
const PL1 = {
  id: "p1", nombre: "Kit UV", descripcion: "Arranque impresión UV", activa: true,
  origen_serie: 2, origen_codpre: 500, origen_numero: "2-000500", num_lineas: 3,
  created_at: null, updated_at: null,
};

beforeEach(() => {
  mockList.mockReset();
  mockList.mockResolvedValue([OF1, OF2]);
  mockPlantillas.mockReset();
  mockPlantillas.mockResolvedValue([PL1]);
  mockPdf.mockReset();
  mockPdf.mockResolvedValue(undefined);
  mockUpdatePlantilla.mockReset();
  mockUpdatePlantilla.mockResolvedValue({ ...PL1, activa: false });
});

function row(numero: string) {
  return screen.getByRole("row", { name: `Oferta ${numero}` });
}

describe("ERP · Ofertas", () => {
  it("lista las ofertas (nº, contacto y empresa enlazados, emisora, fechas, estado, total) y el menú «⋯» por fila", async () => {
    const user = userEvent.setup();
    render(<OfertasPage />);
    expect(await screen.findByRole("heading", { name: "Ofertas" })).toBeInTheDocument();
    await screen.findByRole("table", { name: "Ofertas" });
    await waitFor(() => expect(mockList).toHaveBeenCalledWith({ limit: 500 }));

    const r1 = within(row("OF-2026-0001"));
    expect(r1.getByText("OF-2026-0001")).toHaveClass("mono");
    expect(r1.getByRole("link", { name: "Ana Pérez" })).toHaveAttribute("href", "/contacts/c1");
    expect(r1.getByRole("link", { name: "Acme SL" })).toHaveAttribute("href", "/companies/e1");
    expect(r1.getByText("MQ Europe")).toBeInTheDocument();
    expect(r1.getByText("01/10/2026")).toBeInTheDocument();
    expect(r1.getByText("31/10/2026")).toBeInTheDocument();
    expect(r1.getByText("Enviada")).toHaveClass("badge", "info");
    expect(r1.getByText(/121\.00 €/)).toBeInTheDocument();
    expect(r1.getByText("IVA 21 %")).toBeInTheDocument();
    expect(r1.getByText("cambios sin enviar")).toBeInTheDocument();
    expect(r1.getByText("Rótulos nave")).toBeInTheDocument();
    await user.click(r1.getByRole("button", { name: "Acciones OF-2026-0001" }));
    expect(r1.getByRole("button", { name: "Ver / editar" })).toBeInTheDocument();
    expect(r1.getByRole("button", { name: "PDF" })).toBeInTheDocument();
    expect(r1.getByRole("button", { name: "Enviar por correo" })).toBeInTheDocument();
    expect(r1.getByRole("button", { name: "Convertir en presupuesto" })).toBeInTheDocument();

    // La convertida: pastilla verde, enlace al presupuesto y sin convertir /
    // enviar en el menú (solo ver y PDF).
    const r2 = within(row("OF-2026-0002"));
    expect(r2.getByText("Convertida")).toHaveClass("badge", "ok");
    expect(r2.getByRole("link", { name: "Presupuesto 2-000575" })).toHaveAttribute("href", "/erp/proformas");
    expect(r2.getByText("31/10/2026")).toHaveAttribute("title", "La fecha de validez ya pasó.");
    await user.click(r2.getByRole("button", { name: "Acciones OF-2026-0002" }));
    expect(r2.getByRole("button", { name: "Ver" })).toBeInTheDocument();
    expect(r2.queryByRole("button", { name: "Convertir en presupuesto" })).toBeNull();
    expect(r2.queryByRole("button", { name: "Enviar por correo" })).toBeNull();
  });

  it("«Ver / editar», «Enviar por correo» y «Convertir en presupuesto» abren sus modales con la oferta; guardar y enviar recargan la lista", async () => {
    const user = userEvent.setup();
    render(<OfertasPage />);
    await screen.findByRole("table", { name: "Ofertas" });
    const r1 = () => within(row("OF-2026-0001"));

    await user.click(r1().getByRole("button", { name: "Acciones OF-2026-0001" }));
    await user.click(r1().getByRole("button", { name: "Ver / editar" }));
    expect(await screen.findByText(/EDITOR OF-2026-0001 marcadas:—/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "GUARDAR EDITOR" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Oferta OF-2026-0001 guardada.");
    expect(screen.queryByText(/EDITOR/)).toBeNull();
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));

    await user.click(r1().getByRole("button", { name: "Acciones OF-2026-0001" }));
    await user.click(r1().getByRole("button", { name: "Enviar por correo" }));
    expect(await screen.findByText("EMAIL OF-2026-0001")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "ENVIAR EMAIL" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Oferta OF-2026-0001 enviada a ana@acme.com.");
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(3));
    await user.click(screen.getByRole("button", { name: "CERRAR EMAIL" }));
    expect(screen.queryByText(/EMAIL OF/)).toBeNull();

    // Convertir: el diálogo, y «Editar líneas» (422) abre el editor con las
    // posiciones resaltadas.
    await user.click(r1().getByRole("button", { name: "Acciones OF-2026-0001" }));
    await user.click(r1().getByRole("button", { name: "Convertir en presupuesto" }));
    expect(await screen.findByText("CONVERTIR OF-2026-0001")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "EDITAR LINEAS" }));
    expect(await screen.findByText(/EDITOR OF-2026-0001 marcadas:2,4/)).toBeInTheDocument();
    expect(screen.queryByText(/CONVERTIR OF/)).toBeNull();
  });

  it("PDF abre el PDF de la oferta en su idioma; «+ Nueva oferta» abre el editor sin oferta", async () => {
    const user = userEvent.setup();
    render(<OfertasPage />);
    await screen.findByRole("table", { name: "Ofertas" });
    await user.click(within(row("OF-2026-0001")).getByRole("button", { name: "Acciones OF-2026-0001" }));
    await user.click(within(row("OF-2026-0001")).getByRole("button", { name: "PDF" }));
    await waitFor(() => expect(mockPdf).toHaveBeenCalledWith("of1", "es"));

    await user.click(screen.getByRole("button", { name: "+ Nueva oferta" }));
    expect(await screen.findByText(/EDITOR nueva/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "GUARDAR EDITOR" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Oferta OF-2026-0009 creada.");
  });

  it("el filtro de estado y el buscador van al servidor (q con retardo)", async () => {
    const user = userEvent.setup();
    render(<OfertasPage />);
    await screen.findByRole("table", { name: "Ofertas" });
    await user.selectOptions(screen.getByLabelText("Estado"), "enviada");
    await waitFor(() => expect(mockList).toHaveBeenLastCalledWith({ limit: 500, estado: "enviada" }));
    await user.type(screen.getByRole("searchbox", { name: "Buscar oferta" }), "acme");
    await waitFor(() => expect(mockList).toHaveBeenLastCalledWith({ limit: 500, estado: "enviada", q: "acme" }));
    // Cinco teclas → una sola petición con `q`.
    expect(mockList.mock.calls.filter((c) => c[0].q !== undefined)).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Limpiar filtros" }));
    await waitFor(() => expect(mockList).toHaveBeenLastCalledWith({ limit: 500 }));
  });

  it("pestaña Plantillas: lista (nombre, origen, líneas, activa), Editar, Desactivar y Nueva plantilla", async () => {
    const user = userEvent.setup();
    render(<OfertasPage />);
    await screen.findByRole("table", { name: "Ofertas" });
    expect(mockPlantillas).not.toHaveBeenCalled();           // se piden al abrir la pestaña
    await user.click(screen.getByRole("tab", { name: "Plantillas" }));
    await screen.findByRole("table", { name: "Plantillas de oferta" });
    expect(mockPlantillas).toHaveBeenCalledWith(false);
    const fila = screen.getByRole("row", { name: "Plantilla Kit UV" });
    expect(fila).toHaveTextContent("Arranque impresión UV");
    expect(fila).toHaveTextContent("Presupuesto 2-000500");
    expect(within(fila).getByText("3")).toBeInTheDocument();
    expect(within(fila).getByText("Activa")).toHaveClass("badge", "ok");

    await user.click(within(fila).getByRole("button", { name: "Desactivar" }));
    await waitFor(() => expect(mockUpdatePlantilla).toHaveBeenCalledWith("p1", { activa: false }));
    expect(await screen.findByRole("status")).toHaveTextContent("Plantilla «Kit UV» desactivada.");
    await waitFor(() => expect(mockPlantillas).toHaveBeenCalledTimes(2));

    await user.click(within(screen.getByRole("row", { name: "Plantilla Kit UV" })).getByRole("button", { name: "Editar" }));
    expect(await screen.findByText("PLANTILLA MODAL Kit UV")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "CERRAR PLANTILLA" }));

    await user.click(screen.getByRole("button", { name: "+ Nueva plantilla" }));
    expect(await screen.findByText("PLANTILLA MODAL nueva")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "GUARDAR PLANTILLA" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Plantilla «Recién creada» creada.");
    await waitFor(() => expect(mockPlantillas).toHaveBeenCalledTimes(3));
  });
});
