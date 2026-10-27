import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfirmarPedidoModal } from "./ConfirmarPedidoModal";
import { ApiError } from "../../lib/api";
import { createOrderFromDocumentType, previewOrderFromDocumentType } from "../../lib/erpApi";

/** «Crear pedido» desde un albarán / una factura (Documentos): el modal
 *  enseña ANTES el resumen de lo que se va a crear (empresa, fecha, origen,
 *  conceptos con cantidad/precio/importe y total con IVA) leído del servidor,
 *  y solo «Crear pedido» crea — una vez, aunque se pulse dos veces. Cancelar
 *  no crea nada; un documento ya importado enlaza su pedido; un cliente sin
 *  empresa en el CRM (409) lleva a «Vincular empresa». */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: { children: React.ReactNode; href: string; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));
jest.mock("../../lib/erpApi", () => ({
  previewOrderFromDocumentType: jest.fn(),
  createOrderFromDocumentType: jest.fn(),
}));

const mockPreview = previewOrderFromDocumentType as jest.Mock;
const mockCreate = createOrderFromDocumentType as jest.Mock;

const LINEAS = [
  { product_sku: "DTF-60", description: "Impresora DTF 60 cm", quantity: 2, unit_price: 50,
    discount_pct: 0, line_total: 100, tax_rate: 21 },
  { product_sku: "", description: "Instalación y formación", quantity: 1, unit_price: 30,
    discount_pct: 10, line_total: 27, tax_rate: 21 },
];

function resumen(over = {}) {
  return {
    company_id: "acme", company_name: "Acme SL", fecha: "2026-10-20",
    lineas: LINEAS,
    total: 153.67,
    origen: { doc_type: "albaranes", label: "albarán", serie: 5, codigo: 77,
              numero: "5-000077", referencia: null },
    ...over,
  };
}

function preview(over = {}) {
  return {
    doc_type: "albaranes", serie: 5, codigo: 77, numero: "5-000077",
    fecha: "2026-10-20", total: 153.67, referencia: null,
    estado: "0", estado_label: "Pendiente", forma_pago: "002", forma_pago_nombre: "Transferencia",
    cliente_codigo: "55555", cliente_nombre: "ACME SL",
    company_id: "acme", company_name: "Acme SL", company_linked: true,
    lines: [], order_number: "ALB-5-000077", external_id: "albaranes:5:77",
    already_imported: null,
    resumen: resumen(),
    ...over,
  };
}

/** El pedido que devuelve el alta: misma empresa, mismas líneas, mismo importe. */
const PEDIDO = {
  id: "o-77", order_number: "ALB-5-000077", company_id: "acme",
  lines: LINEAS, total: 153.67,
};

function props(over = {}) {
  return {
    docType: "albaranes" as const,
    serie: 5,
    codigo: 77,
    numero: "5-000077",
    onClose: jest.fn(),
    onCreated: jest.fn(),
    onUnlinked: jest.fn(),
    ...over,
  };
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

beforeEach(() => {
  mockPreview.mockReset();
  mockPreview.mockResolvedValue(preview());
  mockCreate.mockReset();
  mockCreate.mockResolvedValue(PEDIDO);
});

describe("ConfirmarPedidoModal", () => {
  it("al abrir solo LEE el resumen: llama a la vista previa y no crea nada", async () => {
    render(<ConfirmarPedidoModal {...props()} />);
    expect(screen.getByRole("dialog", { name: "Crear pedido desde el albarán 5-000077" }))
      .toBeInTheDocument();
    await screen.findByRole("region", { name: "Resumen del pedido" });
    expect(mockPreview).toHaveBeenCalledTimes(1);
    expect(mockPreview).toHaveBeenCalledWith("albaranes", 5, 77);
    expect(mockCreate).not.toHaveBeenCalled();
    expect(screen.getByText(/Nada se crea hasta pulsar «Crear pedido»/)).toBeInTheDocument();
  });

  it("enseña empresa, fecha, origen, cada concepto con cantidad/precio/importe y el total con IVA", async () => {
    render(<ConfirmarPedidoModal {...props()} />);
    const region = await screen.findByRole("region", { name: "Resumen del pedido" });
    expect(within(region).getByText("Acme SL")).toBeInTheDocument();
    expect(within(region).getByText("20/10/2026")).toBeInTheDocument();
    expect(within(region).getByText("5-000077").closest("dd")).toHaveTextContent("Albarán 5-000077");

    const tabla = within(region).getByRole("table", { name: "Conceptos del pedido" });
    const filas = within(tabla).getAllByRole("row").slice(1);
    expect(filas).toHaveLength(2);
    const celdas = (fila: HTMLElement) =>
      within(fila).getAllByRole("cell").map((td) => td.textContent);
    expect(celdas(filas[0])).toEqual(["DTF-60 · Impresora DTF 60 cm", "2", "50.00 €", "", "100.00 €"]);
    expect(celdas(filas[1])).toEqual(["Instalación y formación", "1", "30.00 €", "10 %", "27.00 €"]);

    expect(within(region).getByText(/Importe \(IVA incl\.\)/)).toHaveTextContent(
      "Importe (IVA incl.): 153.67 €",
    );
    expect(screen.getByRole("button", { name: "Crear pedido" })).toBeEnabled();
  });

  it("la empresa elegida al abrir (cruce CRM de la fila) es la que se ve y la que se usa al crear", async () => {
    const user = userEvent.setup();
    const p = props({ companyId: "crm-acme", companyName: "Acme Ibérica SL" });
    render(<ConfirmarPedidoModal {...p} />);
    const region = await screen.findByRole("region", { name: "Resumen del pedido" });
    expect(within(region).getByText("Acme Ibérica SL")).toBeInTheDocument();
    expect(within(region).queryByText("Acme SL")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Crear pedido" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledWith("albaranes", 5, 77, { company_id: "crm-acme" });
  });

  it("«Cancelar» cierra sin crear nada", async () => {
    const user = userEvent.setup();
    const p = props();
    render(<ConfirmarPedidoModal {...p} />);
    await screen.findByRole("region", { name: "Resumen del pedido" });
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(p.onClose).toHaveBeenCalledTimes(1);
    expect(mockCreate).not.toHaveBeenCalled();
    expect(p.onCreated).not.toHaveBeenCalled();
  });

  it("«Crear pedido» crea UNA sola vez aunque se pulse dos veces seguidas, con la empresa del resumen", async () => {
    const alta = deferred<typeof PEDIDO>();
    mockCreate.mockReturnValueOnce(alta.promise);
    const p = props();
    render(<ConfirmarPedidoModal {...p} />);
    await screen.findByRole("region", { name: "Resumen del pedido" });
    const boton = screen.getByRole("button", { name: "Crear pedido" });
    // Dos pulsaciones antes de que React repinte (mismo lote).
    act(() => {
      boton.click();
      boton.click();
    });
    expect(mockCreate).toHaveBeenCalledTimes(1);
    expect(mockCreate).toHaveBeenCalledWith("albaranes", 5, 77, { company_id: "acme" });
    // Mientras crea, el botón queda bloqueado (y Cancelar también).
    expect(screen.getByRole("button", { name: "Creando…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancelar" })).toBeDisabled();
    await act(async () => { alta.resolve(PEDIDO); });
    await waitFor(() => expect(p.onCreated).toHaveBeenCalledTimes(1));
    expect(p.onCreated).toHaveBeenCalledWith(PEDIDO);
    expect(mockCreate).toHaveBeenCalledTimes(1);
    expect(p.onUnlinked).not.toHaveBeenCalled();
  });

  it("lo que se crea es lo que se ve: misma empresa, mismo documento, mismas líneas e importe", async () => {
    const user = userEvent.setup();
    const p = props();
    render(<ConfirmarPedidoModal {...p} />);
    const region = await screen.findByRole("region", { name: "Resumen del pedido" });
    const vistas = within(within(region).getByRole("table", { name: "Conceptos del pedido" }))
      .getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[0].textContent);
    await user.click(screen.getByRole("button", { name: "Crear pedido" }));
    await waitFor(() => expect(p.onCreated).toHaveBeenCalledTimes(1));
    // El alta va con la empresa y el documento (serie/código del origen) del resumen.
    const r = resumen();
    expect(mockCreate).toHaveBeenCalledWith(
      r.origen.doc_type, r.origen.serie, r.origen.codigo, { company_id: r.company_id },
    );
    // Y el pedido creado que se devuelve coincide con lo enseñado.
    const creado = (p.onCreated as jest.Mock).mock.calls[0][0] as typeof PEDIDO;
    expect(creado.company_id).toBe(r.company_id);
    expect(creado.total).toBe(r.total);
    expect(creado.lines.map((l) => [l.product_sku ? `${l.product_sku} · ` : "", l.description].join("")))
      .toEqual(vistas);
  });

  it("si el documento ya tiene pedido, lo enlaza y no ofrece «Crear pedido»", async () => {
    mockPreview.mockResolvedValue(preview({
      already_imported: { order_id: "o-77", order_number: "ALB-5-000077" },
    }));
    render(<ConfirmarPedidoModal {...props()} />);
    const aviso = await screen.findByRole("status");
    expect(aviso).toHaveTextContent("Este documento ya tiene pedido");
    expect(within(aviso).getByRole("link", { name: "ALB-5-000077" }))
      .toHaveAttribute("href", "/erp/orders/o-77");
    expect(screen.queryByRole("button", { name: "Crear pedido" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancelar" })).toBeEnabled();
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("si el backend dice que el cliente no tiene empresa (409), pide vincularla en vez de mostrar el error", async () => {
    const user = userEvent.setup();
    mockCreate.mockRejectedValue(new ApiError("no vinculado", 409, {
      code: "factusol_customer_unlinked", detail: "no vinculado",
      codcli: "385", cliente_nombre: "JAP EDICIONES S.L.",
    }));
    const p = props();
    render(<ConfirmarPedidoModal {...p} />);
    await screen.findByRole("region", { name: "Resumen del pedido" });
    await user.click(screen.getByRole("button", { name: "Crear pedido" }));
    await waitFor(() => expect(p.onUnlinked).toHaveBeenCalledWith("385", "JAP EDICIONES S.L."));
    expect(p.onCreated).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("cualquier otro fallo del alta se muestra y se puede reintentar", async () => {
    const user = userEvent.setup();
    mockCreate.mockRejectedValueOnce(new Error("FACTUSOL no responde"));
    const p = props();
    render(<ConfirmarPedidoModal {...p} />);
    await screen.findByRole("region", { name: "Resumen del pedido" });
    await user.click(screen.getByRole("button", { name: "Crear pedido" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("FACTUSOL no responde");
    expect(p.onCreated).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Crear pedido" }));
    await waitFor(() => expect(p.onCreated).toHaveBeenCalledWith(PEDIDO));
    expect(mockCreate).toHaveBeenCalledTimes(2);
  });

  it("sin empresa en el CRM no deja crear y ofrece «Vincular empresa»", async () => {
    const user = userEvent.setup();
    mockPreview.mockResolvedValue(preview({
      company_id: null, company_name: null, company_linked: false,
      cliente_codigo: "385", cliente_nombre: "JAP EDICIONES S.L.",
      resumen: resumen({ company_id: null, company_name: null }),
    }));
    const p = props();
    render(<ConfirmarPedidoModal {...p} />);
    await screen.findByRole("region", { name: "Resumen del pedido" });
    expect(screen.getByText("Sin empresa del CRM vinculada")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Crear pedido" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Vincular empresa" }));
    expect(p.onUnlinked).toHaveBeenCalledWith("385", "JAP EDICIONES S.L.");
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("si no se puede leer el documento, avisa y no deja crear", async () => {
    mockPreview.mockRejectedValue(new Error("Documento no encontrado"));
    render(<ConfirmarPedidoModal {...props()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Documento no encontrado");
    expect(screen.getByRole("button", { name: "Crear pedido" })).toBeDisabled();
    expect(mockCreate).not.toHaveBeenCalled();
  });
});
