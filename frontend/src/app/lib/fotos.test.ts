import { FOTO_MAX_BYTES, prepararFoto } from "./fotos";

function archivo(bytes: number, name = "IMG_1.jpg", type = "image/jpeg"): File {
  const f = new File(["x"], name, { type });
  Object.defineProperty(f, "size", { value: bytes });
  return f;
}

afterEach(() => {
  delete (globalThis as { createImageBitmap?: unknown }).createImageBitmap;
  jest.restoreAllMocks();
});

describe("prepararFoto (reducir en el navegador antes de subir)", () => {
  it("una foto pequeña o un PDF van tal cual", async () => {
    const f = archivo(500_000);
    expect(await prepararFoto(f)).toBe(f);
    const pdf = archivo(20 * 1024 * 1024, "doc.pdf", "application/pdf");
    expect(await prepararFoto(pdf)).toBe(pdf);
  });

  it("una foto grande se reduce a JPEG de 2560 px como mucho", async () => {
    const close = jest.fn();
    (globalThis as { createImageBitmap?: unknown }).createImageBitmap =
      jest.fn().mockResolvedValue({ width: 8000, height: 6000, close });
    const drawImage = jest.fn();
    jest.spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue({ drawImage } as unknown as CanvasRenderingContext2D);
    jest.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(function (
      this: HTMLCanvasElement, cb: BlobCallback,
    ) {
      cb(new Blob(["y".repeat(1000)], { type: "image/jpeg" }));
    });
    const out = await prepararFoto(archivo(18 * 1024 * 1024, "IMG_2.HEIC", "image/heic"));
    expect(out.name).toBe("IMG_2.jpg");
    expect(out.type).toBe("image/jpeg");
    expect(drawImage).toHaveBeenCalledWith(expect.anything(), 0, 0, 2560, 1920);
  });

  it("si el navegador no sabe leerla y pasa de 12 MB, lo dice en vez de subirla", async () => {
    (globalThis as { createImageBitmap?: unknown }).createImageBitmap =
      jest.fn().mockRejectedValue(new Error("formato no soportado"));
    await expect(prepararFoto(archivo(FOTO_MAX_BYTES + 1, "IMG_3.HEIC", "image/heic")))
      .rejects.toThrow(/no puede reducirla/);
  });

  it("si no sabe leerla pero cabe, la manda tal cual (el servidor convierte HEIC)", async () => {
    (globalThis as { createImageBitmap?: unknown }).createImageBitmap =
      jest.fn().mockRejectedValue(new Error("formato no soportado"));
    const f = archivo(5 * 1024 * 1024, "IMG_4.heic", "");
    expect(await prepararFoto(f)).toBe(f);
  });
});
