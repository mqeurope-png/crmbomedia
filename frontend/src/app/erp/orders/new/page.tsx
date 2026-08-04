"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { PageHeader } from "../../../components/PageHeader";
import {
  CustomerAutocomplete,
  type CustomerChoice,
} from "../../../components/erp/CustomerAutocomplete";
import { listContacts, type Contact } from "../../../lib/api";
import { listCompanies, type Company } from "../../../lib/companiesApi";
import { extractErrorMessage } from "../../../lib/errors";
import {
  createFactusolCustomer,
  createOrder,
  type OrderAddress,
} from "../../../lib/erpApi";

type LineRow = {
  product_sku: string;
  description: string;
  quantity: string;
  unit_price: string;
};

const EMPTY_LINE: LineRow = {
  product_sku: "", description: "", quantity: "1", unit_price: "",
};

const EMPTY_ADDRESS: OrderAddress = {
  address_line: "", city: "", postal_code: "", state: "", country: "España",
};

function num(v: string): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function addressFilled(a: OrderAddress): boolean {
  return Boolean(a.address_line?.trim() || a.city?.trim() || a.postal_code?.trim());
}

/** Alta de pedido manual (Fase D · D-2): encargos por teléfono, muestras y
 *  reparaciones sin ticket Woo. El origen es fijo `manual` y el número lo
 *  genera el backend (`MANUAL-000001`). */
export default function NewManualOrderPage() {
  const router = useRouter();
  const [companyQuery, setCompanyQuery] = useState("");
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companyId, setCompanyId] = useState<string | null>(null);
  const [contactQuery, setContactQuery] = useState("");
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [contactId, setContactId] = useState<string | null>(null);
  const [placedAt, setPlacedAt] = useState(today());
  const [taxId, setTaxId] = useState("");
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState<LineRow[]>([{ ...EMPTY_LINE }]);
  const [pickup, setPickup] = useState(false);
  const [shipping, setShipping] = useState<OrderAddress>({ ...EMPTY_ADDRESS });
  const [billingSame, setBillingSame] = useState(true);
  const [billing, setBilling] = useState<OrderAddress>({ ...EMPTY_ADDRESS });
  const [pendingCrmCompany, setPendingCrmCompany] = useState<Company | null>(null);
  const [creatingCustomer, setCreatingCustomer] = useState(false);
  const [factusolNotice, setFactusolNotice] =
    useState<{ tone: "info" | "error"; text: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Autocomplete de empresas (patrón datalist debounced del CRM).
  useEffect(() => {
    const handle = window.setTimeout(() => {
      listCompanies({ q: companyQuery || undefined, limit: 12 })
        .then((page) => setCompanies(page.items))
        .catch(() => setCompanies([]));
    }, 250);
    return () => window.clearTimeout(handle);
  }, [companyQuery]);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      listContacts({ q: contactQuery || undefined, limit: 12 })
        .then((page) => setContacts(page.items))
        .catch(() => setContacts([]));
    }, 250);
    return () => window.clearTimeout(handle);
  }, [contactQuery]);

  const total = useMemo(
    () => lines.reduce((sum, l) => sum + num(l.quantity) * num(l.unit_price), 0),
    [lines],
  );

  function pickCompany(value: string) {
    setCompanyQuery(value);
    const hit = companies.find((c) => c.name === value);
    setCompanyId(hit?.id ?? null);
    if (!hit) return;
    setTaxId((prev) => prev || hit.tax_id || "");
    // Autocompleta la dirección desde la empresa si aún está vacía.
    setShipping((prev) => (addressFilled(prev) ? prev : {
      address_line: hit.address_line ?? "", city: hit.city ?? "",
      postal_code: hit.postal_code ?? "", state: hit.state ?? "",
      country: hit.country ?? "España",
    }));
  }

  function pickContact(value: string) {
    setContactQuery(value);
    const hit = contacts.find((c) => contactName(c) === value);
    setContactId(hit?.id ?? null);
    // Si el contacto tiene empresa y aún no hay una elegida, la hereda.
    if (hit?.company_id && !companyId) {
      setCompanyId(hit.company_id);
      const comp = companies.find((c) => c.id === hit.company_id);
      if (comp) setCompanyQuery(comp.name);
    }
  }

  /** C-3: elección desde el buscador FACTUSOL/CRM.
   *  - Cliente FACTUSOL ya vinculado → usa la empresa CRM existente.
   *  - Cliente FACTUSOL sin vincular → rellena el formulario con sus datos y
   *    avisa de que se vinculará (el vínculo real necesita empresa CRM, que se
   *    crea desde Contactos/Empresas — aquí solo pre-rellenamos).
   *  - Empresa CRM sin código FACTUSOL → ofrece crearla en FACTUSOL. */
  function onPickCustomer(choice: CustomerChoice) {
    setFactusolNotice(null);
    setPendingCrmCompany(null);
    if (choice.kind === "crm") {
      const c = choice.company;
      applyCompany(c);
      setPendingCrmCompany(c);
      return;
    }
    const cust = choice.customer;
    setTaxId((prev) => prev || cust.nif || "");
    setShipping((prev) => (addressFilled(prev) ? prev : {
      address_line: cust.domcli ?? "", city: cust.pobcli ?? "",
      postal_code: cust.cpocli ?? "", state: cust.procli ?? "",
      country: "España",  // PAICLI es ISO numérico, no sirve de etiqueta
    }));
    if (cust.crm_link?.type === "company") {
      setCompanyId(cust.crm_link.id);
      setCompanyQuery(cust.crm_link.name);
      setFactusolNotice({
        tone: "info",
        text: `Cliente FACTUSOL nº ${cust.codcli} — ya vinculado a «${cust.crm_link.name}».`,
      });
    } else {
      setCompanyQuery(cust.nombre ?? "");
      setFactusolNotice({
        tone: "info",
        text: `Cliente FACTUSOL nº ${cust.codcli} sin empresa en el CRM. Elige o crea la empresa abajo para poder vincularlo.`,
      });
    }
  }

  function applyCompany(c: Company) {
    setCompanyId(c.id);
    setCompanyQuery(c.name);
    setTaxId((prev) => prev || c.tax_id || "");
    setShipping((prev) => (addressFilled(prev) ? prev : {
      address_line: c.address_line ?? "", city: c.city ?? "",
      postal_code: c.postal_code ?? "", state: c.state ?? "",
      country: c.country ?? "España",
    }));
  }

  async function createInFactusol() {
    if (!pendingCrmCompany) return;
    setCreatingCustomer(true);
    setFactusolNotice(null);
    try {
      const r = await createFactusolCustomer({
        crm_type: "company", crm_id: pendingCrmCompany.id,
        nombre: pendingCrmCompany.name,
        nif: pendingCrmCompany.tax_id ?? "",
        direccion: pendingCrmCompany.address_line ?? "",
        ciudad: pendingCrmCompany.city ?? "",
        cp: pendingCrmCompany.postal_code ?? "",
        provincia: pendingCrmCompany.state ?? "",
      });
      setPendingCrmCompany(null);
      setFactusolNotice({
        tone: "info",
        text: r.created
          ? `Creado en FACTUSOL con el nº ${r.factusol_codcli}.`
          : `Ya existía en FACTUSOL (nº ${r.factusol_codcli}) — vinculado.`,
      });
    } catch (e) {
      setFactusolNotice({
        tone: "error",
        text: extractErrorMessage(e, "No se pudo crear en FACTUSOL."),
      });
    } finally {
      setCreatingCustomer(false);
    }
  }

  function updateLine(i: number, key: keyof LineRow, value: string) {
    setLines((rs) => rs.map((r, j) => (j === i ? { ...r, [key]: value } : r)));
  }

  const lineErrors = lines.map((l) =>
    !l.product_sku.trim()
      ? "Indica el SKU."
      : num(l.quantity) <= 0
        ? "La cantidad debe ser > 0."
        : num(l.unit_price) < 0
          ? "El precio no puede ser negativo."
          : null,
  );
  const customerOk = Boolean(companyId || contactId);
  const addressOk = pickup || addressFilled(shipping);
  const valid = customerOk && addressOk && lines.length > 0
    && lineErrors.every((e) => e === null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid) return;
    setSubmitting(true);
    setError(null);
    try {
      const order = await createOrder({
        company_id: companyId,
        contact_id: contactId,
        placed_at: placedAt ? new Date(placedAt).toISOString() : null,
        tax_id: taxId.trim() || null,
        notes: notes.trim() || null,
        pickup_in_store: pickup,
        shipping_address: pickup ? null : shipping,
        billing_address: billingSame ? (pickup ? null : shipping) : billing,
        lines: lines.map((l) => ({
          product_sku: l.product_sku.trim(),
          description: l.description.trim() || l.product_sku.trim(),
          quantity: num(l.quantity),
          unit_price: num(l.unit_price),
        })),
      });
      router.push(`/erp/orders/${order.id}`);
      router.refresh();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo crear el pedido."));
      setSubmitting(false);
    }
  }

  return (
    <main className="shell shell-wide">
      <PageHeader
        title="Nuevo pedido manual"
        eyebrow="ERP"
        description="Encargos por teléfono, muestras y reparaciones sin ticket Woo."
        crumbs={[
          { label: "ERP" },
          { label: "Pedidos", href: "/erp/orders" },
          { label: "Nuevo" },
        ]}
      />

      <form className="erp-manual-form" onSubmit={submit}>
        {error ? <p className="form-error">{error}</p> : null}

        <section className="erp-card">
          <h3>Cliente</h3>
          <p className="muted small">
            Origen: <strong>manual</strong> · el número de pedido se genera solo.
          </p>
          {/* C-3: busca primero en FACTUSOL (fuente contable) y luego en CRM. */}
          <CustomerAutocomplete onPick={onPickCustomer} />
          {factusolNotice ? (
            <p className={factusolNotice.tone === "error" ? "form-error" : "form-info"}
               role="status">
              {factusolNotice.text}
            </p>
          ) : null}
          {pendingCrmCompany ? (
            <p className="form-info" role="status">
              «{pendingCrmCompany.name}» aún no está en FACTUSOL.{" "}
              <button type="button" className="button small"
                      disabled={creatingCustomer}
                      onClick={createInFactusol}>
                {creatingCustomer ? "Creando…" : "Crear en FACTUSOL"}
              </button>
            </p>
          ) : null}
          <div className="form-row">
            <label className="field">
              <span>Empresa</span>
              <input
                type="text" list="erp-new-order-companies" value={companyQuery}
                placeholder="Buscar empresa…"
                onChange={(e) => pickCompany(e.target.value)}
              />
              <datalist id="erp-new-order-companies">
                {companies.map((c) => <option key={c.id} value={c.name} />)}
              </datalist>
            </label>
            <label className="field">
              <span>Contacto</span>
              <input
                type="text" list="erp-new-order-contacts" value={contactQuery}
                placeholder="Buscar contacto…"
                onChange={(e) => pickContact(e.target.value)}
              />
              <datalist id="erp-new-order-contacts">
                {contacts.map((c) => (
                  <option key={c.id} value={contactName(c)} />
                ))}
              </datalist>
            </label>
          </div>
          {!customerOk ? (
            <p className="muted small">
              Elige una empresa o un contacto de la lista.{" "}
              <a href="/contacts/new" target="_blank" rel="noreferrer">
                ¿No existe? Créalo primero en Contactos
              </a>
            </p>
          ) : null}
          <div className="form-row">
            <label className="field">
              <span>Fecha del pedido</span>
              <input type="date" value={placedAt}
                     onChange={(e) => setPlacedAt(e.target.value)} />
            </label>
            <label className="field">
              <span>NIF / CIF</span>
              <input type="text" value={taxId}
                     onChange={(e) => setTaxId(e.target.value)} />
            </label>
          </div>
        </section>

        <section className="erp-card">
          <h3>Líneas</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>SKU</th><th>Descripción</th><th>Cant.</th>
                <th>Precio ud.</th><th>Total</th><th />
              </tr>
            </thead>
            <tbody>
              {lines.map((l, i) => (
                <tr key={i}>
                  <td>
                    <input type="text" value={l.product_sku}
                           aria-label={`SKU línea ${i + 1}`}
                           onChange={(e) => updateLine(i, "product_sku", e.target.value)} />
                  </td>
                  <td>
                    <input type="text" value={l.description}
                           aria-label={`Descripción línea ${i + 1}`}
                           onChange={(e) => updateLine(i, "description", e.target.value)} />
                  </td>
                  <td>
                    <input type="number" min="0" step="1" value={l.quantity}
                           aria-label={`Cantidad línea ${i + 1}`}
                           onChange={(e) => updateLine(i, "quantity", e.target.value)} />
                  </td>
                  <td>
                    <input type="number" min="0" step="0.01" value={l.unit_price}
                           aria-label={`Precio línea ${i + 1}`}
                           onChange={(e) => updateLine(i, "unit_price", e.target.value)} />
                  </td>
                  <td>{(num(l.quantity) * num(l.unit_price)).toFixed(2)}</td>
                  <td>
                    {lines.length > 1 ? (
                      <button type="button" className="button small secondary"
                              aria-label={`Eliminar línea ${i + 1}`}
                              onClick={() => setLines((rs) => rs.filter((_, j) => j !== i))}>
                        ✕
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button type="button" className="button small secondary"
                  onClick={() => setLines((rs) => [...rs, { ...EMPTY_LINE }])}>
            + Añadir línea
          </button>
          <p className="erp-manual-total">
            Total: <strong>{total.toFixed(2)} EUR</strong>
          </p>
        </section>

        <section className="erp-card">
          <h3>Envío</h3>
          <label className="field-toggle">
            <input type="checkbox" checked={pickup}
                   onChange={(e) => setPickup(e.target.checked)} />
            <span>Recogida en tienda</span>
          </label>
          {!pickup ? (
            <AddressFields legend="envío" value={shipping} onChange={setShipping} />
          ) : null}
        </section>

        <section className="erp-card">
          <h3>Facturación</h3>
          <label className="field-toggle">
            <input type="checkbox" checked={billingSame}
                   onChange={(e) => setBillingSame(e.target.checked)} />
            <span>Usar dirección de envío</span>
          </label>
          {!billingSame ? (
            <AddressFields legend="facturación" value={billing} onChange={setBilling} />
          ) : null}
        </section>

        <section className="erp-card">
          <h3>Notas internas</h3>
          <label className="field">
            <span>Notas</span>
            <textarea rows={3} value={notes} aria-label="Notas internas"
                      onChange={(e) => setNotes(e.target.value)} />
          </label>
        </section>

        <div className="form-actions">
          <Link href="/erp/orders" className="button secondary">Cancelar</Link>
          <button type="submit" className="button" disabled={!valid || submitting}>
            {submitting ? "Creando…" : "Crear pedido"}
          </button>
        </div>
      </form>
    </main>
  );
}

function contactName(c: Contact): string {
  return [c.first_name, c.last_name].filter(Boolean).join(" ").trim();
}

function AddressFields({
  legend, value, onChange,
}: {
  legend: string;
  value: OrderAddress;
  onChange: (a: OrderAddress) => void;
}) {
  function set(key: keyof OrderAddress, v: string) {
    onChange({ ...value, [key]: v });
  }
  return (
    <>
      <label className="field">
        <span>Dirección</span>
        <input type="text" value={value.address_line ?? ""}
               aria-label={`Dirección de ${legend}`}
               onChange={(e) => set("address_line", e.target.value)} />
      </label>
      <div className="form-row">
        <label className="field">
          <span>Ciudad</span>
          <input type="text" value={value.city ?? ""}
                 aria-label={`Ciudad de ${legend}`}
                 onChange={(e) => set("city", e.target.value)} />
        </label>
        <label className="field">
          <span>Código postal</span>
          <input type="text" value={value.postal_code ?? ""}
                 aria-label={`Código postal de ${legend}`}
                 onChange={(e) => set("postal_code", e.target.value)} />
        </label>
      </div>
      <div className="form-row">
        <label className="field">
          <span>Provincia</span>
          <input type="text" value={value.state ?? ""}
                 aria-label={`Provincia de ${legend}`}
                 onChange={(e) => set("state", e.target.value)} />
        </label>
        <label className="field">
          <span>País</span>
          <input type="text" value={value.country ?? ""}
                 aria-label={`País de ${legend}`}
                 onChange={(e) => set("country", e.target.value)} />
        </label>
      </div>
    </>
  );
}
