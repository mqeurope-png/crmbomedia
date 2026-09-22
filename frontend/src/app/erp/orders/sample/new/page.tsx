"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { PageHeader } from "../../../../components/PageHeader";
import {
  DocumentLinesTable,
  emptyDocumentLine,
  lineNum,
  type DocumentLine,
} from "../../../../components/erp/DocumentLinesTable";
import { getCurrentUser, type User } from "../../../../lib/api";
import { Cap, can } from "../../../../lib/capabilities";
import { createSampleOrder, type OrderLinePayload } from "../../../../lib/erpApi";
import { extractErrorMessage } from "../../../../lib/errors";

/** Alta de MUESTRA / envío NO FACTURABLE.
 *
 *  Formulario propio y deliberadamente corto: a quién se le manda, qué va
 *  dentro y por qué. NO pide empresa vinculada a FACTUSOL, ni serie, ni NIF —
 *  este pedido no se factura y no toca FACTUSOL. Al guardarlo entra directo a
 *  la Cola SAT: lo único que queda es prepararlo y enviarlo. */
export default function NuevaMuestraPage() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [recipientCompany, setRecipientCompany] = useState("");
  const [recipient, setRecipient] = useState("");
  const [recipientEmail, setRecipientEmail] = useState("");
  const [recipientPhone, setRecipientPhone] = useState("");
  const [addressLine, setAddressLine] = useState("");
  const [city, setCity] = useState("");
  const [postalCode, setPostalCode] = useState("");
  const [state, setState] = useState("");
  const [country, setCountry] = useState("España");
  const [reason, setReason] = useState("");
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState<DocumentLine[]>([emptyDocumentLine()]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCurrentUser().then(setUser).catch(() => undefined);
  }, []);

  const allowed = can(user, Cap.SAMPLES_CREATE);
  const filledLines = lines.filter(
    (l) => l.sku.trim() !== "" || l.description.trim() !== "",
  );
  const ready =
    recipient.trim() !== "" && addressLine.trim() !== "" && filledLines.length > 0;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!ready || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const payload: OrderLinePayload[] = filledLines.map((l) => ({
        product_sku: l.sku.trim(),
        description: l.description.trim(),
        quantity: lineNum(l.quantity) || 1,
        // Una muestra no se cobra: el precio es informativo y por defecto 0.
        unit_price: lineNum(l.unit_price),
        tax_rate: 0,
      }));
      const order = await createSampleOrder({
        recipient_name: recipient.trim(),
        recipient_company: recipientCompany.trim() || null,
        recipient_email: recipientEmail.trim() || null,
        recipient_phone: recipientPhone.trim() || null,
        shipping_address: {
          address_line: addressLine.trim() || null,
          city: city.trim() || null,
          postal_code: postalCode.trim() || null,
          state: state.trim() || null,
          country: country.trim() || null,
        },
        reason: reason.trim() || null,
        notes: notes.trim() || null,
        lines: payload,
      });
      router.push(`/erp/orders/${order.id}`);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo crear la muestra."));
      setSubmitting(false);
    }
  }

  if (user && !allowed) {
    return (
      <main className="shell">
        <PageHeader title="Nuevo envío / muestra" eyebrow="ERP · Pedidos" />
        <p className="form-error" role="alert">
          No tienes permiso para crear muestras. Pueden hacerlo Comercial,
          ERP Pedidos, ERP Taller (SAT) y Administración.
        </p>
        <Link href="/erp/orders" className="button secondary">Volver a la bandeja</Link>
      </main>
    );
  }

  return (
    <main className="shell">
      <PageHeader
        title="Nuevo envío / muestra"
        eyebrow="ERP · Pedidos"
        description={
          "Para mandar una muestra o un envío de cortesía (una pieza olvidada, "
          + "material de prueba). No se factura: no lleva empresa, ni albarán, "
          + "ni factura, ni cobro. Al guardarlo entra en la Cola SAT para "
          + "prepararlo y enviarlo."
        }
      />

      <form onSubmit={submit} className="erp-form">
        <section className="card">
          <h2>Destinatario</h2>
          <p className="muted small">
            A quién y a dónde se manda. No hace falta que sea un cliente: puede
            ser un prospecto.
          </p>
          <label className="field">
            <span>Empresa</span>
            <input value={recipientCompany} maxLength={200}
                   placeholder="Empresa destinataria (opcional)"
                   onChange={(e) => setRecipientCompany(e.target.value)} />
          </label>
          <label className="field">
            <span>Persona de contacto *</span>
            <input value={recipient} required maxLength={120}
                   placeholder="A quién va dirigido"
                   onChange={(e) => setRecipient(e.target.value)} />
          </label>
          <div className="erp-form-row">
            <label className="field">
              <span>Email</span>
              <input value={recipientEmail} type="email" maxLength={255}
                     onChange={(e) => setRecipientEmail(e.target.value)} />
            </label>
            <label className="field">
              <span>Teléfono</span>
              <input value={recipientPhone} maxLength={40}
                     onChange={(e) => setRecipientPhone(e.target.value)} />
            </label>
          </div>
          <label className="field">
            <span>Dirección *</span>
            <input value={addressLine} required maxLength={500}
                   placeholder="Calle, número, piso"
                   onChange={(e) => setAddressLine(e.target.value)} />
          </label>
          <div className="erp-form-row">
            <label className="field">
              <span>Código postal</span>
              <input value={postalCode} maxLength={20}
                     onChange={(e) => setPostalCode(e.target.value)} />
            </label>
            <label className="field">
              <span>Población</span>
              <input value={city} maxLength={200}
                     onChange={(e) => setCity(e.target.value)} />
            </label>
            <label className="field">
              <span>Provincia</span>
              <input value={state} maxLength={200}
                     onChange={(e) => setState(e.target.value)} />
            </label>
            <label className="field">
              <span>País</span>
              <input value={country} maxLength={120}
                     onChange={(e) => setCountry(e.target.value)} />
            </label>
          </div>
        </section>

        <section className="card">
          <h2>Qué se envía</h2>
          <p className="muted small">
            Sirve para que el taller sepa qué preparar. El precio es opcional
            (por defecto 0): una muestra no se cobra.
          </p>
          <DocumentLinesTable
            lines={lines}
            onChange={setLines}
            articleSearch={false}
            ariaLabel="Líneas de la muestra"
            skuPlaceholder="SKU (opcional)"
            descriptionPlaceholder="Qué se manda"
          />
        </section>

        <section className="card">
          <h2>Motivo</h2>
          <label className="field">
            <span>Por qué se manda</span>
            <input value={reason} maxLength={500}
                   placeholder="Muestra · pieza olvidada del pedido BOP-1234 · material de prueba"
                   onChange={(e) => setReason(e.target.value)} />
          </label>
          <label className="field">
            <span>Notas internas</span>
            <textarea value={notes} rows={3}
                      onChange={(e) => setNotes(e.target.value)} />
          </label>
        </section>

        {error ? <p className="form-error" role="alert">{error}</p> : null}

        <div className="modal-actions">
          <Link href="/erp/orders" className="button secondary">Cancelar</Link>
          <button type="submit" className="button" disabled={!ready || submitting}>
            {submitting ? "Creando…" : "Crear envío / muestra"}
          </button>
        </div>
      </form>
    </main>
  );
}
