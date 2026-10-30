"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { PageHeader } from "../../../../../../../components/PageHeader";
import {
  downloadFactusolDocumentPdf,
  getFichaDocumento,
  saveBlob,
  type FichaDocumento,
} from "../../../../../../../lib/erpApi";
import { extractErrorMessage } from "../../../../../../../lib/errors";

/** Ficha de un documento de FACTUSOL (factura, albarán o proforma) de
 *  cualquier año: cabecera, líneas (las de nota aparte), estado, pedido de
 *  BoHub y PDF. Solo lectura: cobrar, mención o vincular siguen en su sitio. */

const NF = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const NQ = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 3 });

function euros(n: number | null | undefined): string {
  return n == null ? "—" : `${NF.format(n)} €`;
}

function fecha(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("es-ES");
}

export default function FichaDocumentoPage() {
  const p = useParams<{ tipo: string; ejercicio: string; serie: string; codigo: string }>();
  const [doc, setDoc] = useState<FichaDocumento | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [descargando, setDescargando] = useState(false);

  useEffect(() => {
    let vivo = true;
    setDoc(null);
    setError(null);
    getFichaDocumento(p.tipo, p.ejercicio, p.serie, p.codigo)
      .then((d) => { if (vivo) setDoc(d); })
      .catch((err) => { if (vivo) setError(extractErrorMessage(err, "No se ha podido abrir el documento.")); });
    return () => { vivo = false; };
  }, [p.tipo, p.ejercicio, p.serie, p.codigo]);

  async function pdf() {
    if (!doc) return;
    setDescargando(true);
    try {
      const blob = await downloadFactusolDocumentPdf(
        doc.doc_type, Number(doc.serie), doc.codigo, "es", { ejercicio: doc.ejercicio });
      saveBlob(blob, `${doc.tipo_label}_${doc.numero}.pdf`);
    } catch (err) {
      setError(extractErrorMessage(err,
        "No se ha podido generar el PDF: FACTUSOL tiene que tener abierto ese ejercicio."));
    } finally {
      setDescargando(false);
    }
  }

  const producto = doc?.lineas.filter((l) => !l.es_nota) ?? [];
  const notas = doc?.lineas.filter((l) => l.es_nota) ?? [];

  return (
    <main className="shell shell-wide erp-flow">
      <PageHeader
        title={doc ? `${doc.tipo_label} ${doc.numero}` : "Documento"}
        eyebrow="ERP"
        description={doc ? `Ejercicio ${doc.ejercicio}` : undefined}
        crumbs={[{ label: "ERP", href: "/erp" }, { label: "Documento" }]}
        actions={doc ? (
          <button type="button" className="button small secondary" disabled={descargando}
                  onClick={() => void pdf()}>
            {descargando ? "Generando…" : "PDF"}
          </button>
        ) : undefined}
      />
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {!doc && !error ? <p className="muted">Cargando…</p> : null}
      {doc ? (
        <>
          <section className="erp-card">
            <dl className="erp-ficha-documento">
              <div><dt>Documento</dt><dd>{doc.tipo_label} <span className="mono">{doc.numero}</span></dd></div>
              <div><dt>Fecha</dt><dd>{doc.fecha ? fecha(doc.fecha)
                : <span title="La fecha que tiene FACTUSOL no es válida">fecha no válida ({doc.fecha_original ?? "—"})</span>}</dd></div>
              <div><dt>Ejercicio</dt><dd>{doc.ejercicio}</dd></div>
              <div>
                <dt>Cliente</dt>
                <dd>
                  {doc.cliente.empresa ? (
                    <Link href={`/companies/${doc.cliente.empresa.id}`} prefetch={false}>
                      {doc.cliente.empresa.nombre}
                    </Link>
                  ) : (doc.cliente.nombre ?? "—")}
                  {doc.cliente.codcli ? <span className="mono muted"> {doc.cliente.codcli}</span> : null}
                  {!doc.cliente.empresa ? <> <span className="badge warn">Sin empresa vinculada</span></> : null}
                </dd>
              </div>
              <div>
                <dt>Total</dt>
                <dd>{doc.total != null ? euros(doc.total) : (
                  <span title="Volcado antes de guardar el total: es la suma de las líneas">
                    {euros(doc.suma_lineas)} <span className="muted">(suma de líneas, sin impuestos)</span>
                  </span>
                )}</dd>
              </div>
              {doc.tipo === "factura" ? (
                <div><dt>Cobro</dt><dd>{doc.estado_label ?? "—"}
                  {doc.estado_en_vivo ? <span className="muted"> · en vivo</span>
                    : doc.cargado_at ? <span className="muted"> · a fecha de la última carga</span>
                    : null}</dd></div>
              ) : doc.estado_label ? (
                <div><dt>Estado</dt><dd>{doc.estado_label}</dd></div>
              ) : null}
              <div>
                <dt>Pedido de BoHub</dt>
                <dd>{doc.pedido ? (
                  <Link href={`/erp/orders/${doc.pedido.id}`} prefetch={false} className="mono">
                    {doc.pedido.order_number}
                  </Link>
                ) : <span className="muted">Sin pedido de BoHub</span>}</dd>
              </div>
              {doc.referencia ? <div><dt>Referencia</dt><dd>{doc.referencia}</dd></div> : null}
            </dl>
            {doc.fuente === "vivo" ? (
              <p className="form-hint">Leído en vivo de FACTUSOL: aún no está en la carga nocturna.</p>
            ) : null}
          </section>

          <section className="erp-card">
            <h3>Líneas</h3>
            {producto.length === 0 ? <p className="muted">Sin líneas de producto.</p> : (
              <table className="data-table data-table--responsive">
                <thead>
                  <tr>
                    <th>Artículo</th><th>Descripción</th>
                    <th className="num">Cantidad</th><th className="num">Precio</th><th className="num">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {producto.map((l, i) => (
                    <tr key={`p-${l.posicion ?? i}-${i}`}>
                      <td data-label="Artículo">
                        <span className="mono">{l.codart}</span>
                        {l.articulo ? <div className="muted">{l.articulo}</div> : null}
                        {l.viene_de ? <div className="muted">Viene de {l.viene_de}</div> : null}
                      </td>
                      <td data-label="Descripción">{l.descripcion ?? "—"}</td>
                      <td data-label="Cantidad" className="num">{l.cantidad == null ? "—" : NQ.format(l.cantidad)}</td>
                      <td data-label="Precio" className="num">{euros(l.precio)}</td>
                      <td data-label="Total" className="num">{euros(l.total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {notas.length > 0 ? (
              <div className="erp-ficha-notas">
                <h4>Notas del documento <span className="muted">(sin artículo: no cuentan como venta)</span></h4>
                <ul>
                  {notas.map((l, i) => (
                    <li key={`n-${l.posicion ?? i}-${i}`}>
                      {l.descripcion ?? "—"}
                      {l.total ? <span className="muted"> · {euros(l.total)}</span> : null}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </section>
        </>
      ) : null}
    </main>
  );
}
