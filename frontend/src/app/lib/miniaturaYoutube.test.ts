import {
  MINIATURAS_YOUTUBE,
  anchoDeImagen,
  miniaturaYoutube,
  urlMiniaturaYoutube,
} from "./miniaturaYoutube";

const ID = "dQw4w9WgXcQ";

describe("miniaturaYoutube — siempre 16:9, sin bandas negras", () => {
  it("la cadena es maxresdefault → mqdefault; nunca hqdefault ni sddefault (4:3)", () => {
    expect(MINIATURAS_YOUTUBE).toEqual(["maxresdefault", "mqdefault"]);
    expect(urlMiniaturaYoutube(ID, "mqdefault")).toBe(`https://img.youtube.com/vi/${ID}/mqdefault.jpg`);
  });

  it("maxresdefault si existe de verdad; si es el marcador de 120×90 o no carga, mqdefault", async () => {
    const medir = jest.fn();
    medir.mockResolvedValueOnce(1280);
    expect(await miniaturaYoutube(ID, medir)).toBe(`https://img.youtube.com/vi/${ID}/maxresdefault.jpg`);
    expect(medir).toHaveBeenCalledWith(`https://img.youtube.com/vi/${ID}/maxresdefault.jpg`);
    medir.mockResolvedValueOnce(120);                 // vídeo que no se subió en HD
    expect(await miniaturaYoutube(ID, medir)).toBe(`https://img.youtube.com/vi/${ID}/mqdefault.jpg`);
    medir.mockResolvedValueOnce(0);                   // no carga
    expect(await miniaturaYoutube(ID, medir)).toBe(`https://img.youtube.com/vi/${ID}/mqdefault.jpg`);
  });

  it("mide la imagen cargándola en el navegador: su ancho, o 0 si falla o tarda", async () => {
    const original = window.Image;
    let ultima: { onload?: () => void; onerror?: () => void; naturalWidth: number; src: string } | null = null;
    class ImagenFalsa {
      onload?: () => void;
      onerror?: () => void;
      naturalWidth = 0;
      private _src = "";
      constructor() { ultima = this as unknown as typeof ultima; }
      get src() { return this._src; }
      set src(v: string) { this._src = v; }
    }
    window.Image = ImagenFalsa as unknown as typeof Image;
    try {
      const carga = anchoDeImagen("https://img.youtube.com/vi/x/maxresdefault.jpg");
      ultima!.naturalWidth = 1280;
      ultima!.onload!();
      expect(await carga).toBe(1280);
      const falla = anchoDeImagen("https://img.youtube.com/vi/x/maxresdefault.jpg");
      ultima!.onerror!();
      expect(await falla).toBe(0);
      jest.useFakeTimers();
      const tarda = anchoDeImagen("https://img.youtube.com/vi/x/maxresdefault.jpg", 50);
      jest.advanceTimersByTime(60);
      expect(await tarda).toBe(0);
    } finally {
      jest.useRealTimers();
      window.Image = original;
    }
  });
});
