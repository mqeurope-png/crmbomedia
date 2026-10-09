import { apiDownloadBlob } from "./api";

/** `apiDownloadBlob` lee `Content-Disposition` y devuelve el binario con el
 *  nombre del servidor pegado (un `File`); sin cabecera, el Blob a secas. */

type FetchLike = (input: string, init?: RequestInit) => Promise<unknown>;

function respuesta(disposition: string | null): unknown {
  return {
    ok: true,
    status: 200,
    headers: {
      get: (k: string) => (k.toLowerCase() === "content-disposition" ? disposition : null),
    },
    blob: async () => new Blob(["%PDF-1.4"], { type: "application/pdf" }),
  };
}

const fetchMock = jest.fn<Promise<unknown>, Parameters<FetchLike>>();

beforeEach(() => {
  fetchMock.mockReset();
  (globalThis as unknown as { fetch: FetchLike }).fetch = fetchMock;
});

test("el PDF llega con el nombre que manda el servidor", async () => {
  fetchMock.mockResolvedValue(
    respuesta('attachment; filename="Proforma invoice HUGIN GMBH 2-004365.pdf"'),
  );
  const blob = await apiDownloadBlob("/api/erp/factusol/documents/presupuestos/2/4365/pdf");
  expect(blob).toBeInstanceOf(File);
  expect((blob as File).name).toBe("Proforma invoice HUGIN GMBH 2-004365.pdf");
  expect(blob.type).toBe("application/pdf");
  expect(fetchMock.mock.calls[0][0]).toMatch(/\/pdf$/);
});

test("el ZIP del lote también", async () => {
  fetchMock.mockResolvedValue(respuesta('attachment; filename="facturas_pdf.zip"'));
  const blob = await apiDownloadBlob("/api/erp/factusol/documents/facturas/pdf-zip", {
    method: "POST", body: "{}", headers: { "Content-Type": "application/json" },
  });
  expect((blob as File).name).toBe("facturas_pdf.zip");
  expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
});

test("sin cabecera llega un Blob sin nombre: la pantalla pondrá el suyo", async () => {
  fetchMock.mockResolvedValue(respuesta(null));
  const blob = await apiDownloadBlob("/api/erp/orders/o-1/factusol-pedido-pdf?lang=es");
  expect(blob).toBeInstanceOf(Blob);
  expect(blob).not.toBeInstanceOf(File);
});
