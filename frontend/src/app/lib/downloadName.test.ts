import {
  downloadName,
  filenameFromContentDisposition,
  withServerFilename,
} from "./downloadName";

/** Lo que mandan hoy los cuatro puntos de descarga (#531) y el ZIP del lote:
 *  `attachment; filename="…"`, con espacios dentro del nombre. */

describe("filenameFromContentDisposition", () => {
  test("filename entre comillas, con espacios: el formato de #531", () => {
    expect(filenameFromContentDisposition(
      'attachment; filename="Factura CDCOPIADVD SLU 2-004364.pdf"',
    )).toBe("Factura CDCOPIADVD SLU 2-004364.pdf");
    expect(filenameFromContentDisposition(
      'attachment; filename="Bon de livraison DOCUMENT MATERIEL SA 5-260068.pdf"',
    )).toBe("Bon de livraison DOCUMENT MATERIEL SA 5-260068.pdf");
  });

  test("el ZIP del lote y un token sin comillas", () => {
    expect(filenameFromContentDisposition('attachment; filename="facturas_pdf.zip"'))
      .toBe("facturas_pdf.zip");
    expect(filenameFromContentDisposition("attachment; filename=audit_logs.csv"))
      .toBe("audit_logs.csv");
  });

  test("filename*=UTF-8'' manda sobre filename, aunque vaya detrás, y se decodifica", () => {
    // Es lo que ya hace el backend con las etiquetas y fotos (`_inline_disposition`).
    expect(filenameFromContentDisposition(
      "inline; filename=\"Lieferschein MANUFAKTUR F_R 2-000080.pdf\"; "
      + "filename*=UTF-8''Lieferschein%20MANUFAKTUR%20F%C3%9CR%20GESTALTUNG%202-000080.pdf",
    )).toBe("Lieferschein MANUFAKTUR FÜR GESTALTUNG 2-000080.pdf");
    // Con idioma en medio (RFC 5987: charset'lang'valor).
    expect(filenameFromContentDisposition("attachment; filename*=utf-8'de'Rechnung%20X.pdf"))
      .toBe("Rechnung X.pdf");
  });

  test("un filename* que no se puede decodificar cae al filename corriente", () => {
    expect(filenameFromContentDisposition(
      "attachment; filename*=UTF-8''%E0%A4%A; filename=\"respaldo.pdf\"",
    )).toBe("respaldo.pdf");
    // Charset que no es UTF-8: no se intenta adivinar, también cae.
    expect(filenameFromContentDisposition(
      "attachment; filename*=iso-8859-1''Albar%E1n.pdf; filename=\"Albaran.pdf\"",
    )).toBe("Albaran.pdf");
  });

  test("comillas escapadas dentro del nombre", () => {
    expect(filenameFromContentDisposition('attachment; filename="Pedido \\"X\\" 1.pdf"'))
      .toBe('Pedido "X" 1.pdf');
  });

  test("solo el nombre base: sin rutas", () => {
    expect(filenameFromContentDisposition('attachment; filename="../../x.pdf"')).toBe("x.pdf");
    expect(filenameFromContentDisposition("attachment; filename*=UTF-8''a%2Fb%2Fc.pdf"))
      .toBe("c.pdf");
  });

  test("sin cabecera, sin nombre o vacío: null (la pantalla pone el respaldo)", () => {
    expect(filenameFromContentDisposition(null)).toBeNull();
    expect(filenameFromContentDisposition(undefined)).toBeNull();
    expect(filenameFromContentDisposition("")).toBeNull();
    expect(filenameFromContentDisposition("attachment")).toBeNull();
    expect(filenameFromContentDisposition('attachment; filename=""')).toBeNull();
    expect(filenameFromContentDisposition('attachment; filename="   "')).toBeNull();
    // `xfilename=` no es `filename=`.
    expect(filenameFromContentDisposition("attachment; xfilename=a.pdf")).toBeNull();
  });
});

describe("withServerFilename / downloadName", () => {
  test("con nombre en la cabecera llega un File con ese nombre y el mismo tipo", () => {
    const blob = new Blob(["%PDF-1.4"], { type: "application/pdf" });
    const out = withServerFilename(blob, 'attachment; filename="Rechnung HUGIN GMBH 2-004365.pdf"');
    expect(out).toBeInstanceOf(File);
    expect((out as File).name).toBe("Rechnung HUGIN GMBH 2-004365.pdf");
    expect(out.type).toBe("application/pdf");
    expect(out.size).toBe(blob.size);
    expect(downloadName(out, "Factura_2-004365.pdf")).toBe("Rechnung HUGIN GMBH 2-004365.pdf");
  });

  test("sin nombre en la cabecera llega el mismo Blob y manda el respaldo", () => {
    const blob = new Blob(["x"]);
    expect(withServerFilename(blob, null)).toBe(blob);
    expect(withServerFilename(blob, "attachment")).toBe(blob);
    expect(downloadName(blob, "Factura_2-004365.pdf")).toBe("Factura_2-004365.pdf");
    // Un File con el nombre vacío tampoco vale.
    expect(downloadName(new File(["x"], "  "), "respaldo.pdf")).toBe("respaldo.pdf");
  });
});
