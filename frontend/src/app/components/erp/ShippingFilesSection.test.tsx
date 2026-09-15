import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ShippingFilesSection } from "./ShippingFilesSection";
import {
  createOrderAlbaran,
  downloadOrderFactusolAlbaranPdf,
  fetchAlbaranFromWoo,
  getQuoteJobStatus,
  listShippingFiles,
  openShippingFile,
  saveBlob,
  uploadShippingFile,
  type ShipmentFile,
  type ShippingFileUploadResult,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  listShippingFiles: jest.fn(),
  uploadShippingFile: jest.fn(),
  fetchAlbaranFromWoo: jest.fn(),
  openShippingFile: jest.fn(),
  // Fase 2: «PDF del albarán (FACTUSOL)».
  downloadOrderFactusolAlbaranPdf: jest.fn(),
  saveBlob: jest.fn(),
  // Rediseño de flujo: el albarán FACTUSOL se crea desde este panel.
  createOrderAlbaran: jest.fn(),
  getQuoteJobStatus: jest.fn(),
}));
const mockList = listShippingFiles as jest.Mock;
const mockUpload = uploadShippingFile as jest.Mock;
const mockFetchWoo = fetchAlbaranFromWoo as jest.Mock;
const mockOpen = openShippingFile as jest.Mock;

function file(kind: "albaran" | "etiqueta"): ShipmentFile {
  return {
    id: `f-${kind}`, kind, source: "manual_upload", filename: `${kind}.pdf`,
    mime_type: "application/pdf", size_bytes: 10, uploaded_by_user_id: null,
    uploaded_at: null, download_url: `/api/erp/orders/o1/shipping-files/f-${kind}/download`,
  };
}

beforeEach(() => {
  mockList.mockReset();
  mockUpload.mockReset();
  mockFetchWoo.mockReset();
  mockOpen.mockReset();
  (createOrderAlbaran as jest.Mock).mockReset();
  (getQuoteJobStatus as jest.Mock).mockReset();
  mockList.mockResolvedValue([]);
  window.history.replaceState({}, "", "/erp/orders/o1");
});

describe("ShippingFilesSection", () => {
  it("pedido NO Woo sin ficheros: solo botones de subida (sin Descargar de Woo)", async () => {
    render(<ShippingFilesSection orderId="o1" isWooOrder={false} />);
    expect(await screen.findByRole("button", { name: "Subir albarán" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Subir etiqueta" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Descargar albarán de Woo/ }),
    ).not.toBeInTheDocument();
  });

  it("pedido Woo sin albarán: ofrece Descargar de Woo", async () => {
    render(<ShippingFilesSection orderId="o1" isWooOrder />);
    expect(
      await screen.findByRole("button", { name: /Descargar albarán de Woo/ }),
    ).toBeInTheDocument();
  });

  it("con albarán presente: Ver albarán + Reemplazar, y abre el PDF al pulsar", async () => {
    mockList.mockResolvedValue([file("albaran")]);
    const user = userEvent.setup();
    render(<ShippingFilesSection orderId="o1" isWooOrder />);
    const ver = await screen.findByRole("button", { name: "Ver albarán" });
    expect(screen.getByRole("button", { name: "Reemplazar albarán" })).toBeInTheDocument();
    await user.click(ver);
    expect(mockOpen).toHaveBeenCalledWith(expect.objectContaining({ kind: "albaran" }));
  });

  it("con etiqueta presente: Ver etiqueta", async () => {
    mockList.mockResolvedValue([file("etiqueta")]);
    render(<ShippingFilesSection orderId="o1" isWooOrder={false} />);
    expect(await screen.findByRole("button", { name: "Ver etiqueta" })).toBeInTheDocument();
  });

  it("con albarán FACTUSOL: «PDF del albarán (FACTUSOL)» descarga el PDF y «Subir albarán» sigue disponible", async () => {
    const blob = new Blob(["%PDF-"], { type: "application/pdf" });
    (downloadOrderFactusolAlbaranPdf as jest.Mock).mockResolvedValue(blob);
    const user = userEvent.setup();
    render(
      <ShippingFilesSection orderId="o1" isWooOrder={false}
                            factusolAlbaranNumber="1-100327" pdfLang="en" />,
    );
    expect(await screen.findByText("Albarán FACTUSOL 1-100327")).toBeInTheDocument();
    const pdf = screen.getByRole("button", { name: "PDF del albarán (FACTUSOL)" });
    // Conviven: subir un albarán externo / SAT sigue siendo posible.
    expect(screen.getByRole("button", { name: "Subir albarán" })).toBeInTheDocument();
    await user.click(pdf);
    await waitFor(() => expect(downloadOrderFactusolAlbaranPdf).toHaveBeenCalledWith("o1", "en"));
    expect(saveBlob).toHaveBeenCalledWith(blob, "Albaran_1-100327.pdf");
  });

  it("con albarán FACTUSOL y fichero subido: PDF de FACTUSOL + Ver/Reemplazar", async () => {
    mockList.mockResolvedValue([file("albaran")]);
    render(
      <ShippingFilesSection orderId="o1" isWooOrder={false} factusolAlbaranNumber="5-500008" />,
    );
    expect(await screen.findByRole("button", { name: "Ver albarán" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "PDF del albarán (FACTUSOL)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reemplazar albarán" })).toBeInTheDocument();
  });

  it("sin albarán FACTUSOL no hay botón de PDF: solo «Subir albarán» como hasta ahora", async () => {
    render(<ShippingFilesSection orderId="o1" isWooOrder={false} factusolAlbaranNumber={null} />);
    expect(await screen.findByRole("button", { name: "Subir albarán" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "PDF del albarán (FACTUSOL)" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Albarán FACTUSOL/)).not.toBeInTheDocument();
  });

  it("descargar de Woo llama al endpoint y refresca", async () => {
    mockFetchWoo.mockResolvedValue({ file: file("albaran"), already_present: false });
    const user = userEvent.setup();
    render(<ShippingFilesSection orderId="o1" isWooOrder />);
    await user.click(await screen.findByRole("button", { name: /Descargar albarán de Woo/ }));
    await waitFor(() => expect(mockFetchWoo).toHaveBeenCalledWith("o1"));
    // Refresca la lista tras descargar.
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
  });

  // --- albarán FACTUSOL: el ÚNICO sitio de la ficha donde se crea ---

  it("sin albarán FACTUSOL y con permiso: «Crear albarán en FACTUSOL» encola, hace polling y avisa con el nº", async () => {
    (createOrderAlbaran as jest.Mock).mockResolvedValue({ job_id: "job-1", order_id: "o1", status: "queued" });
    (getQuoteJobStatus as jest.Mock).mockResolvedValue({
      status: "finished", result: { numero: "5-500009", status: "created" },
    });
    const onCreated = jest.fn();
    const user = userEvent.setup();
    render(
      <ShippingFilesSection orderId="o1" isWooOrder={false} orderSource="manual"
                            canCreateAlbaran onAlbaranCreated={onCreated} />,
    );
    expect(await screen.findByText("Sin albarán en FACTUSOL")).toBeInTheDocument();
    const btn = screen.getByRole("button", { name: "Crear albarán en FACTUSOL" });
    expect(btn).toHaveAttribute("title", expect.stringMatching(/pedido manual/));
    await user.click(btn);
    await waitFor(() => expect(createOrderAlbaran).toHaveBeenCalledWith("o1"));
    await waitFor(() => expect(getQuoteJobStatus).toHaveBeenCalledWith("job-1"));
    expect(await screen.findByText("Albarán FACTUSOL 5-500009 creado.")).toBeInTheDocument();
    expect(onCreated).toHaveBeenCalledWith({ numero: "5-500009", error: null });
  });

  it("si el job falla lo dice a la ficha (error) en vez de quedarse colgado", async () => {
    window.history.replaceState({}, "", "/erp/orders/o1?albaran_job=job-9");
    (getQuoteJobStatus as jest.Mock).mockResolvedValue({ status: "failed", error: "esquema no cuadra" });
    const onCreated = jest.fn();
    render(<ShippingFilesSection orderId="o1" isWooOrder={false} onAlbaranCreated={onCreated} />);
    await waitFor(() => expect(getQuoteJobStatus).toHaveBeenCalledWith("job-9"));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({
      numero: null, error: expect.stringMatching(/no se creó en FACTUSOL: esquema no cuadra/),
    }));
  });

  it("pedido web: el albarán lo crea WooCommerce, nunca se ofrece crearlo (ni por señal externa)", async () => {
    render(<ShippingFilesSection orderId="o1" isWooOrder canCreateAlbaran createSignal={1} />);
    expect(await screen.findByText(/lo crea WooCommerce/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Crear albarán en FACTUSOL" })).not.toBeInTheDocument();
    expect(createOrderAlbaran).not.toHaveBeenCalled();
  });

  it("sin permiso de edición no hay botón de crear; la señal externa (email al SAT / siguiente paso) sí lo crea", async () => {
    (createOrderAlbaran as jest.Mock).mockResolvedValue({ job_id: "job-2", order_id: "o1", status: "queued" });
    (getQuoteJobStatus as jest.Mock).mockResolvedValue({ status: "queued" });
    const { rerender } = render(
      <ShippingFilesSection orderId="o1" isWooOrder={false} orderSource="factusol_proforma" />,
    );
    expect(await screen.findByText("Sin albarán en FACTUSOL")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Crear albarán en FACTUSOL" })).not.toBeInTheDocument();
    rerender(
      <ShippingFilesSection orderId="o1" isWooOrder={false} orderSource="factusol_proforma"
                            createSignal={1} />,
    );
    await waitFor(() => expect(createOrderAlbaran).toHaveBeenCalledWith("o1"));
    expect(await screen.findByText("Creando el albarán en FACTUSOL…")).toBeInTheDocument();
    expect(screen.getByText(/Albarán encolado en FACTUSOL/)).toBeInTheDocument();
  });

  // --- Lote 2 C: subir la etiqueta ES «Crear envío» ---

  function subida(over: Partial<ShippingFileUploadResult> = {}): ShippingFileUploadResult {
    return {
      file: file("etiqueta"), transition_applied: true,
      transport_status: "label_created", transition_reason: null, ...over,
    };
  }
  const pdf = () => new File(["%PDF-"], "gls.pdf", { type: "application/pdf" });

  it("subir la etiqueta: llama al endpoint, refresca, dice que el transporte pasó a «Etiqueta creada» y lo comunica a la ficha", async () => {
    mockUpload.mockResolvedValue(subida());
    const onUploaded = jest.fn();
    const user = userEvent.setup();
    render(<ShippingFilesSection orderId="o1" isWooOrder={false} onUploaded={onUploaded} />);
    const fichero = pdf();
    await user.upload(await screen.findByLabelText("Subir etiqueta"), fichero);
    await waitFor(() => expect(mockUpload).toHaveBeenCalledWith("o1", "etiqueta", fichero));
    expect(await screen.findByText("Etiqueta subida: el transporte pasa a «Etiqueta creada»."))
      .toBeInTheDocument();
    expect(onUploaded).toHaveBeenCalledWith(expect.objectContaining({
      kind: "etiqueta", transition_applied: true, transport_status: "label_created",
    }));
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
  });

  it("etiqueta subida sin el pedido embalado: el fichero queda y se avisa de por qué el transporte no cambia", async () => {
    mockUpload.mockResolvedValue(subida({
      transition_applied: false, transport_status: "not_shipped",
      transition_reason: "[packed_before_label] crear envío exige preparación embalada; actual: 'preparing'",
    }));
    const onUploaded = jest.fn();
    const user = userEvent.setup();
    render(<ShippingFilesSection orderId="o1" isWooOrder={false} onUploaded={onUploaded} />);
    await user.upload(await screen.findByLabelText("Subir etiqueta"), pdf());
    const aviso = await screen.findByText(/el transporte sigue en «Sin enviar»/);
    expect(aviso).toHaveTextContent(/exige preparación embalada/);
    expect(aviso).toHaveClass("form-warning");
    expect(screen.queryByText(/pasa a «Etiqueta creada»/)).toBeNull();
    // La ficha recarga igualmente (el fichero está guardado).
    expect(onUploaded).toHaveBeenCalledWith(expect.objectContaining({
      kind: "etiqueta", transition_applied: false,
    }));
  });

  it("reemplazar la etiqueta con el envío ya creado: sin aviso de transporte", async () => {
    mockList.mockResolvedValue([file("etiqueta")]);
    mockUpload.mockResolvedValue(subida({ transition_applied: false, transport_status: "label_created" }));
    const user = userEvent.setup();
    render(<ShippingFilesSection orderId="o1" isWooOrder={false} />);
    await user.upload(await screen.findByLabelText("Reemplazar etiqueta"), pdf());
    expect(await screen.findByText("Etiqueta subida.")).toBeInTheDocument();
    expect(screen.queryByText(/sigue en «Sin enviar»/)).toBeNull();
  });

  it("subir el albarán nunca habla de transporte (no lo mueve), pero sí avisa a la ficha", async () => {
    mockUpload.mockResolvedValue(subida({
      file: file("albaran"), transition_applied: false, transport_status: "not_shipped",
    }));
    const onUploaded = jest.fn();
    const user = userEvent.setup();
    render(<ShippingFilesSection orderId="o1" isWooOrder={false} onUploaded={onUploaded} />);
    await user.upload(await screen.findByLabelText("Subir albarán"), pdf());
    await waitFor(() => expect(onUploaded).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "albaran" }),
    ));
    expect(screen.queryByText(/Etiqueta subida/)).toBeNull();
    expect(screen.queryByText(/transporte/)).toBeNull();
  });

  it("la señal «Subir etiqueta» (siguiente paso de la ficha) abre el selector de fichero de la ETIQUETA", async () => {
    const click = jest.spyOn(HTMLElement.prototype, "click");
    try {
      const { rerender } = render(<ShippingFilesSection orderId="o1" isWooOrder={false} />);
      await screen.findByRole("button", { name: "Subir etiqueta" });
      expect(click).not.toHaveBeenCalled();
      rerender(<ShippingFilesSection orderId="o1" isWooOrder={false} openEtiquetaSignal={1} />);
      await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
      const input = click.mock.contexts[0] as HTMLInputElement;
      expect(input.type).toBe("file");
      expect(input).toHaveAttribute("aria-label", "Subir etiqueta");
    } finally {
      click.mockRestore();
    }
  });
});
