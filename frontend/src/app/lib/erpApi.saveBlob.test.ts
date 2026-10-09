import { saveBlob } from "./erpApi";

/** `saveBlob` guarda con el nombre del servidor cuando viene pegado al
 *  binario, y con el de respaldo de la pantalla cuando no. */

const descargas: string[] = [];

beforeAll(() => {
  // jsdom no implementa ni las URL de objeto ni la navegación del <a>.
  URL.createObjectURL = jest.fn(() => "blob:bohub/x");
  URL.revokeObjectURL = jest.fn();
  HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
    descargas.push(this.download);
  };
});

beforeEach(() => {
  descargas.length = 0;
});

test("el nombre del servidor manda sobre el que construye la pantalla", () => {
  const pdf = new File(["%PDF-1.4"], "Rechnung HUGIN GMBH 2-004365.pdf", {
    type: "application/pdf",
  });
  saveBlob(pdf, "Factura_2-004365.pdf");
  expect(descargas).toEqual(["Rechnung HUGIN GMBH 2-004365.pdf"]);
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:bohub/x");
});

test("sin nombre del servidor se usa el respaldo: ninguna descarga sin nombre", () => {
  saveBlob(new Blob(["%PDF-1.4"], { type: "application/pdf" }), "Factura_2-004365.pdf");
  expect(descargas).toEqual(["Factura_2-004365.pdf"]);
});
