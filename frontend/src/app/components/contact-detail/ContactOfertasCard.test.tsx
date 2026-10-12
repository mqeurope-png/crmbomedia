import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ContactOfertasCard } from "./ContactOfertasCard";
import { listOfertas, type Oferta } from "../../lib/erpApi";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string }
    & Record<string, unknown>) => <a href={href} {...rest}>{children}</a>,
}));
jest.mock("../../lib/erpApi", () => ({
  // Constante real: la pastilla de estado saca de ahí la palabra.
  OFERTA_ESTADO_LABELS: jest.requireActual("../../lib/erpApi").OFERTA_ESTADO_LABELS,
  listOfertas: jest.fn(),
}));
// El editor se prueba aparte; aquí basta con ver que se abre con el contacto
// fijo y que, al guardar, la lista se vuelve a pedir.
jest.mock("../erp/OfertaEditorModal", () => ({
  OfertaEditorModal: ({ contactId, contactName, oferta, onClose, onSaved }: {
    contactId?: string | null; contactName?: string | null; oferta?: unknown;
    onClose: () => void; onSaved: (o: unknown) => void;
  }) => (
    <div role="dialog" aria-label="EDITOR OFERTA">
      EDITOR {contactId} {contactName} nueva:{oferta == null ? "sí" : "no"}
      <button type="button" onClick={() => onSaved({ id: "of-nueva" })}>GUARDAR OFERTA</button>
      <button type="button" onClick={onClose}>CERRAR OFERTA</button>
    </div>
  ),
}));

const mockList = listOfertas as jest.Mock;

function oferta(over: Partial<Oferta> = {}): Oferta {
  return {
    id: "of1", numero: "OF-2026-0001", anio: 2026, secuencia: 1,
    estado: "enviada", estado_guardado: "enviada", vencida: false,
    contact_id: "c1", contacto: { id: "c1", nombre: "Ana López", email: "ana@x.es", company_id: null },
    company_id: null, empresa: null, plantilla_id: null,
    serie: 1, emisora: "Bomedia", idioma: "es", con_iva: true,
    fecha: "2026-10-01", validez_dias: 30, valida_hasta: "2026-10-31",
    referencia: "Kit UV", notas: null,
    base: 1000, iva_pct: 21, iva: 210, total: 1210, version: 1,
    factusol: null, convertida_en: null, enviada_en: "2026-10-01T10:00:00Z", caducada_en: null,
    num_envios: 1, ultimo_envio: null, pendiente_de_envio: false,
    created_by_user_id: null, created_at: null, updated_at: null,
    ...over,
  };
}

beforeEach(() => mockList.mockReset());

describe("ContactOfertasCard (Resumen de la ficha)", () => {
  it("lista las ofertas del contacto con nº, estado, total, fecha y validez, y «Ver todas» filtra por el contacto", async () => {
    mockList.mockResolvedValue([
      oferta(),
      oferta({ id: "of2", numero: "OF-2026-0002", estado: "borrador", estado_guardado: "borrador",
               fecha: "2026-09-15", valida_hasta: "2026-10-15", total: 300.5, referencia: null }),
      oferta({ id: "of3", numero: "OF-2026-0003", estado: "caducada", estado_guardado: "enviada",
               vencida: true, total: 99, fecha: "2026-08-01", valida_hasta: "2026-08-31",
               referencia: "Rótulos" }),
    ]);
    render(<ContactOfertasCard contactId="c1" />);
    const lista = within(await screen.findByRole("list", { name: "Ofertas del contacto" }));
    expect(mockList).toHaveBeenCalledWith({ contact_id: "c1" });
    expect(lista.getAllByRole("listitem")).toHaveLength(3);
    expect(lista.getByText("OF-2026-0001")).toBeInTheDocument();
    expect(lista.getByText("Enviada")).toHaveClass("badge", "info");
    expect(lista.getByText("1210.00 €")).toBeInTheDocument();
    expect(lista.getByText("01/10/2026 · válida hasta 31/10/2026 · Kit UV")).toBeInTheDocument();
    // Sin referencia: solo fechas.
    expect(lista.getByText("15/09/2026 · válida hasta 15/10/2026")).toBeInTheDocument();
    expect(lista.getByText("Borrador")).toBeInTheDocument();
    expect(lista.getByText("Caducada")).toHaveClass("warn");
    expect(screen.getByRole("link", { name: /Ver todas/ }))
      .toHaveAttribute("href", "/erp/ofertas?contact_id=c1");
    // Sin la capacidad: ni rastro del botón.
    expect(screen.queryByRole("button", { name: "+ Nueva oferta" })).toBeNull();
  });

  it("sin ofertas lo dice en una línea, sin «Ver todas»; con Cap.PROFORMAS sale «+ Nueva oferta»", async () => {
    mockList.mockResolvedValue([]);
    render(<ContactOfertasCard contactId="c1" canCreate />);
    expect(await screen.findByText("El contacto no tiene ofertas todavía.")).toBeInTheDocument();
    expect(screen.getByText("Ofertas")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Ver todas/ })).toBeNull();
    expect(screen.getByRole("button", { name: "+ Nueva oferta" })).toBeInTheDocument();
  });

  it("«+ Nueva oferta» abre el editor con el contacto fijo y, al guardar, recarga la lista", async () => {
    mockList.mockResolvedValueOnce([]).mockResolvedValueOnce([oferta()]);
    const user = userEvent.setup();
    render(<ContactOfertasCard contactId="c1" contactName="Ana López" canCreate />);
    await screen.findByText("El contacto no tiene ofertas todavía.");
    await user.click(screen.getByRole("button", { name: "+ Nueva oferta" }));
    const editor = screen.getByRole("dialog", { name: "EDITOR OFERTA" });
    expect(editor).toHaveTextContent("EDITOR c1 Ana López nueva:sí");
    await user.click(within(editor).getByRole("button", { name: "GUARDAR OFERTA" }));
    expect(screen.queryByRole("dialog", { name: "EDITOR OFERTA" })).toBeNull();
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("OF-2026-0001")).toBeInTheDocument();
  });

  it("cerrar el editor sin guardar no vuelve a pedir la lista", async () => {
    mockList.mockResolvedValue([]);
    const user = userEvent.setup();
    render(<ContactOfertasCard contactId="c1" canCreate />);
    await screen.findByText("El contacto no tiene ofertas todavía.");
    await user.click(screen.getByRole("button", { name: "+ Nueva oferta" }));
    await user.click(screen.getByRole("button", { name: "CERRAR OFERTA" }));
    expect(screen.queryByRole("dialog", { name: "EDITOR OFERTA" })).toBeNull();
    expect(mockList).toHaveBeenCalledTimes(1);
  });

  it("si la carga falla enseña el error en el recuadro", async () => {
    mockList.mockRejectedValue(new Error("Sin red"));
    render(<ContactOfertasCard contactId="c1" />);
    expect(await screen.findByText("Sin red")).toBeInTheDocument();
  });
});
