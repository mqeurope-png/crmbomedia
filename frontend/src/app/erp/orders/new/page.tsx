"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { PageHeader } from "../../../components/PageHeader";
import { ArticleAutocompleteInput } from "../../../components/erp/ArticleAutocompleteInput";
import {
  CustomerAutocomplete,
  type CustomerChoice,
} from "../../../components/erp/CustomerAutocomplete";
import { initialPayment, paymentReady, PaymentStep } from "../../../components/erp/PaymentStep";
import { QuotePicker } from "../../../components/erp/QuotePicker";
import { listContacts, type Contact } from "../../../lib/api";
import { getCompany, listCompanies, type Company } from "../../../lib/companiesApi";
import { extractErrorMessage } from "../../../lib/errors";
import {
  createFactusolCustomer,
  createFactusolCustomerAndLink,
  createOrder,
  getFactusolQuote,
  linkFactusolCustomer,
  listFactusolDocuments,
  listFactusolQuotes,
  previewOrderFromFactusol,
  searchFactusolCustomers,
  type FactusolArticle,
  type FactusolCustomer,
  type FactusolDocument,
  type FactusolOrderDocType,
  type FactusolOrderPreview,
  type FactusolQuote,
  type OrderAddress,
  type PaymentIntentInput,
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
const DOC_TYPE_LABEL: Record<FactusolOrderDocType, string> = {
  presupuestos: "presupuesto",
  pedidos: "pedido de cliente",
};

/** ISO2 (lo que devuelve el backend para PAICLI) → etiqueta del formulario. */
const COUNTRY_NAMES_ES: Record<string, string> = {
  ES: "España", PT: "Portugal", FR: "Francia", IT: "Italia", DE: "Alemania",
  GB: "Reino Unido", NL: "Países Bajos", BE: "Bélgica", US: "Estados Unidos",
  AD: "Andorra", CH: "Suiza", AT: "Austria", IE: "Irlanda", MX: "México",
};

function countryName(iso2: string | null | undefined): string {
  return iso2 ? (COUNTRY_NAMES_ES[iso2] ?? iso2) : "España";
}

/** Dirección de F_CLI en una línea legible para el aviso de precarga. */
function formatAddress(a: OrderAddress): string {
  const town = [a.postal_code?.trim(), a.city?.trim()].filter(Boolean).join(" ");
  const parts = [
    a.address_line?.trim(),
    town + (a.state?.trim() && a.state.trim() !== a.city?.trim() ? ` (${a.state.trim()})` : ""),
    a.country?.trim(),
  ].filter(Boolean);
  return parts.join(", ");
}

/** Nombre fiscal del cliente F_CLI (NOFCLI; el comercial si el fiscal está vacío). */
function fiscalName(cust: FactusolCustomer): string {
  return (cust.nofcli ?? "").trim() || (cust.nombre ?? "").trim();
}

/** Lo que la precarga ha escrito DE VERDAD en el formulario. */
type FactusolLoaded = { nif: string | null; address: OrderAddress | null };

/** Aviso de precarga que no miente: enumera exactamente lo volcado (NIF y/o
 *  dirección), avisa de lo que FACTUSOL no tiene, y enseña el nombre fiscal
 *  si no coincide con el de la empresa CRM. `prefix` es lo que pasó antes
 *  («Creado en FACTUSOL con el nº …»), para no perderlo. */
function loadedNotice(
  cust: FactusolCustomer, loaded: FactusolLoaded,
  opts: { crmName?: string | null; prefix?: string } = {},
): { tone: "info" | "error"; text: string } {
  const codcli = cust.codcli ?? "";
  const parts: string[] = [];
  if (loaded.nif) parts.push(`NIF ${loaded.nif}`);
  if (loaded.address) parts.push(`dirección ${formatAddress(loaded.address)}`);
  const fiscal = fiscalName(cust);
  const crm = (opts.crmName ?? "").trim();
  const nameNote = fiscal && crm && fiscal.toLowerCase() !== crm.toLowerCase()
    ? ` Nombre fiscal en FACTUSOL: «${fiscal}» (empresa del CRM: «${crm}»).`
    : "";
  const prefix = opts.prefix ? `${opts.prefix} ` : "";
  if (parts.length === 0) {
    return {
      tone: "info",
      text: `${prefix}El cliente FACTUSOL nº ${codcli} no tiene NIF ni dirección: `
        + "no se ha cargado nada en el pedido (se mantienen los datos del formulario)."
        + nameNote,
    };
  }
  const missing = loaded.nif ? "" : " Sin NIF en FACTUSOL: se mantiene el del formulario.";
  return {
    tone: "info",
    text: `${prefix}Datos del cliente FACTUSOL nº ${codcli} cargados en el pedido: `
      + `${parts.join(" · ")}.${missing}${nameNote}`,
  };
}

export default function NewManualOrderPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
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
  // Tarea B: CODCLI (F_CLI) de la empresa elegida. El alta manual exige
  // empresa VINCULADA a FACTUSOL — sin CODCLI no se puede crear el pedido
  // (se ofrece «Crear en FACTUSOL»). null = sin empresa o sin vincular.
  const [companyCodcli, setCompanyCodcli] = useState<string | null>(null);
  // Cliente F_CLI cuyos datos se han volcado al formulario (nombre fiscal
  // visible, solo lectura). null = nada precargado desde FACTUSOL.
  const [factusolCustomer, setFactusolCustomer] = useState<FactusolCustomer | null>(null);
  // Nº de secuencia de la precarga en vuelo: una respuesta que llegue después
  // de elegir OTRA empresa se descarta (no pisa lo último elegido).
  const prefillSeq = useRef(0);
  // C-3-fix2: cliente FACTUSOL elegido que aún NO tiene empresa en el CRM.
  const [pendingFactusolCustomer, setPendingFactusolCustomer] =
    useState<FactusolCustomer | null>(null);
  const [creatingCrmCompany, setCreatingCrmCompany] = useState(false);
  const [linkingExisting, setLinkingExisting] = useState(false);
  const [linkCompanyQuery, setLinkCompanyQuery] = useState("");
  const [linkCompanies, setLinkCompanies] = useState<Company[]>([]);
  const [linkCompanyId, setLinkCompanyId] = useState<string | null>(null);
  const [linking, setLinking] = useState(false);
  // Cambiarlo remonta el buscador para descartar resultados obsoletos.
  const [customerSearchKey, setCustomerSearchKey] = useState(0);
  const [creatingCustomer, setCreatingCustomer] = useState(false);
  const [factusolNotice, setFactusolNotice] =
    useState<{ tone: "info" | "error"; text: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // C-4: proformas del cliente elegido, para volcarlas al pedido.
  const [quotes, setQuotes] = useState<FactusolQuote[]>([]);
  const [quotesOpen, setQuotesOpen] = useState(false);
  // C-4-fix2: ¿la empresa elegida tiene vínculo FACTUSOL? Decide si el
  // autocomplete de artículos tiene catálogo contra el que buscar.
  const [companyLinked, setCompanyLinked] = useState(false);
  const [loadingQuote, setLoadingQuote] = useState<string | null>(null);
  const [quoteNotice, setQuoteNotice] = useState<string | null>(null);
  // Fase 1 — importar un presupuesto / pedido de cliente de FACTUSOL por nº
  // (solo lectura allí): precarga cliente, líneas, fecha y forma de pago.
  const [facDocType, setFacDocType] = useState<FactusolOrderDocType>("presupuestos");
  const [facSerie, setFacSerie] = useState("1");
  const [facCodigo, setFacCodigo] = useState("");
  const [facLoading, setFacLoading] = useState(false);
  const [facPreview, setFacPreview] = useState<FactusolOrderPreview | null>(null);
  const [facNotice, setFacNotice] =
    useState<{ tone: "info" | "error"; text: string } | null>(null);
  // Fase 2 — paso de confirmación de pago (opción B) del pedido creado desde
  // un documento FACTUSOL; por defecto «sin pago» con la forma del documento.
  const [payment, setPayment] = useState<PaymentIntentInput>(initialPayment());
  // Fase 1 — «Nuevo pedido» desde la ficha de empresa: ?company_id= precarga.
  const presetCompanyId = searchParams?.get("company_id") ?? null;

  useEffect(() => {
    if (!presetCompanyId) return;
    let alive = true;
    getCompany(presetCompanyId)
      .then((c) => {
        if (!alive) return;
        setCompanyId(c.id);
        setCompanyQuery(c.name);
        setCompanyCodcli(c.factusol_company_id ?? null);
        setPendingCrmCompany(c.factusol_company_id ? null : c);
        setTaxId((prev) => prev || c.tax_id || "");
        setShipping((prev) => (addressFilled(prev) ? prev : {
          address_line: c.address_line ?? "", city: c.city ?? "",
          postal_code: c.postal_code ?? "", state: c.state ?? "",
          country: c.country ?? "España",
        }));
        // B) Empresa vinculada: FACTUSOL manda sobre NIF y direcciones.
        setFactusolCustomer(null);
        if (c.factusol_company_id) void prefillFromFactusol(c.factusol_company_id, c.name);
      })
      .catch(() => undefined);
    return () => { alive = false; };
    // prefillFromFactusol solo usa setters: estable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presetCompanyId]);

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

  // C-3-fix2: empresas CRM candidatas a vincular (solo las que aún no tienen
  // código FACTUSOL — las demás ya están vinculadas a otro cliente).
  useEffect(() => {
    if (!linkingExisting) return;
    const handle = window.setTimeout(() => {
      listCompanies({ q: linkCompanyQuery || undefined, limit: 20 })
        .then((page) =>
          setLinkCompanies(page.items.filter((c) => !c.factusol_company_id)))
        .catch(() => setLinkCompanies([]));
    }, 250);
    return () => window.clearTimeout(handle);
  }, [linkingExisting, linkCompanyQuery]);

  useEffect(() => {
    const hit = linkCompanies.find((c) => c.name === linkCompanyQuery);
    setLinkCompanyId(hit?.id ?? null);
  }, [linkCompanyQuery, linkCompanies]);

  // C-4: proformas recientes de la empresa elegida (solo si está vinculada a
  // FACTUSOL; si no, el endpoint devuelve `unlinked` y no se muestra nada).
  useEffect(() => {
    if (!companyId) {
      setQuotes([]);
      setCompanyLinked(false);
      return;
    }
    let alive = true;
    listFactusolQuotes({ company_id: companyId, days_back: 180 })
      .then((r) => {
        if (!alive) return;
        setQuotes(r.unlinked ? [] : r.items);
        // C-4-fix2: el mismo `unlinked` decide si hay catálogo que buscar. Sin
        // CODCLI no hay contexto FACTUSOL, así que el autocomplete se apaga.
        setCompanyLinked(!r.unlinked);
      })
      .catch(() => {
        if (!alive) return;
        setQuotes([]);
        setCompanyLinked(false);
      });
    return () => { alive = false; };
  }, [companyId]);

  const articleSearchEnabled = companyLinked;

  /** C-4-fix2: rellena la línea desde el catálogo F_ART. El precio se deja en
   *  blanco si FACTUSOL no tiene precio de venta — nunca se fuerza «0.00». */
  function applyArticle(i: number, a: FactusolArticle) {
    setLines((rs) => rs.map((r, j) => (j === i ? {
      ...r,
      product_sku: a.sku ?? a.codart ?? "",
      description: a.descripcion ?? a.sku ?? "",
      unit_price: a.precio_venta ? String(a.precio_venta) : r.unit_price,
    } : r)));
  }

  const total = useMemo(
    () => lines.reduce((sum, l) => sum + num(l.quantity) * num(l.unit_price), 0),
    [lines],
  );

  /** C-4: vuelca el desglose de una proforma en las líneas del pedido.
   *  Si la proforma se hizo en el FACTUSOL de escritorio no hay desglose
   *  (F_PRE es mono-línea) y llega una única línea con su texto e importe. */
  async function loadQuoteLines(codpre: string) {
    setLoadingQuote(codpre);
    setQuoteNotice(null);
    try {
      const quote = await getFactusolQuote(codpre);
      const rows: LineRow[] = (quote.lines ?? []).map((l) => ({
        product_sku: l.codart ?? "",
        description: l.description,
        quantity: String(l.quantity),
        unit_price: String(l.unit_price),
      }));
      const fallback: LineRow[] = [{
        product_sku: "",
        description: quote.referencia || `Proforma ${codpre}`,
        quantity: "1",
        unit_price: String(quote.base),
      }];
      const next = rows.length > 0 ? rows : fallback;
      // Se AÑADEN a lo que ya haya, descartando las filas vacías del inicio.
      setLines((prev) => {
        const kept = prev.filter((l) => l.description.trim() || l.product_sku.trim());
        return [...kept, ...next];
      });
      setQuoteNotice(
        quote.line_source === "cache"
          ? `Cargadas ${next.length} líneas de la proforma ${codpre}.`
          : `La proforma ${codpre} se creó en FACTUSOL de escritorio: se ha `
            + "cargado como una línea única, revisa el importe.",
      );
    } catch (e) {
      setQuoteNotice(extractErrorMessage(e, "No se pudo cargar la proforma."));
    } finally {
      setLoadingQuote(null);
    }
  }

  function pickCompany(value: string) {
    setCompanyQuery(value);
    const hit = companies.find((c) => c.name === value);
    setCompanyId(hit?.id ?? null);
    // Tarea B: empresa vinculada → CODCLI; sin vincular → «créala primero».
    setCompanyCodcli(hit?.factusol_company_id ?? null);
    setPendingCrmCompany(hit && !hit.factusol_company_id ? hit : null);
    // Otra empresa (o ninguna): lo precargado de la anterior ya no vale, y una
    // respuesta tardía de su precarga se descarta.
    prefillSeq.current += 1;
    setFactusolCustomer(null);
    if (!hit) return;
    setTaxId((prev) => prev || hit.tax_id || "");
    // Autocompleta la dirección desde la empresa si aún está vacía.
    setShipping((prev) => (addressFilled(prev) ? prev : {
      address_line: hit.address_line ?? "", city: hit.city ?? "",
      postal_code: hit.postal_code ?? "", state: hit.state ?? "",
      country: hit.country ?? "España",
    }));
    // B) Empresa vinculada: FACTUSOL manda sobre NIF y direcciones.
    if (hit.factusol_company_id) void prefillFromFactusol(hit.factusol_company_id, hit.name);
  }

  function pickContact(value: string) {
    setContactQuery(value);
    const hit = contacts.find((c) => contactName(c) === value);
    setContactId(hit?.id ?? null);
    // Si el contacto tiene empresa y aún no hay una elegida, la hereda.
    if (hit?.company_id && !companyId) {
      setCompanyId(hit.company_id);
      const comp = companies.find((c) => c.id === hit.company_id);
      if (comp) {
        setCompanyQuery(comp.name);
        setCompanyCodcli(comp.factusol_company_id ?? null);
        setPendingCrmCompany(comp.factusol_company_id ? null : comp);
      }
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
    setPendingFactusolCustomer(null);
    setLinkingExisting(false);
    if (choice.kind === "crm") {
      const c = choice.company;
      applyCompany(c);
      setPendingCrmCompany(c.factusol_company_id ? null : c);
      return;
    }
    const cust = choice.customer;
    // B) FACTUSOL manda: NIF y dirección del cliente F_CLI al formulario (el
    // hit del buscador ya trae la fila, no hace falta releer).
    prefillSeq.current += 1;
    const loaded = applyFactusolCustomer(cust);
    if (cust.crm_link?.type === "company") {
      setCompanyId(cust.crm_link.id);
      setCompanyQuery(cust.crm_link.name);
      setCompanyCodcli(cust.codcli);
      setFactusolNotice(loadedNotice(cust, loaded, {
        crmName: cust.crm_link.name,
        prefix: `Cliente FACTUSOL nº ${cust.codcli} — ya vinculado a «${cust.crm_link.name}».`,
      }));
    } else {
      setCompanyQuery(cust.nombre ?? "");
      setPendingFactusolCustomer(cust);
      setFactusolNotice(loadedNotice(cust, loaded, {
        prefix: `Cliente FACTUSOL nº ${cust.codcli} sin empresa en el CRM. Elige debajo qué hacer.`,
      }));
    }
  }

  function applyCompany(c: Company) {
    setCompanyId(c.id);
    setCompanyQuery(c.name);
    setCompanyCodcli(c.factusol_company_id ?? null);
    setTaxId((prev) => prev || c.tax_id || "");
    setShipping((prev) => (addressFilled(prev) ? prev : {
      address_line: c.address_line ?? "", city: c.city ?? "",
      postal_code: c.postal_code ?? "", state: c.state ?? "",
      country: c.country ?? "España",
    }));
    // B) Empresa vinculada: FACTUSOL manda sobre NIF y direcciones.
    prefillSeq.current += 1;
    setFactusolCustomer(null);
    if (c.factusol_company_id) void prefillFromFactusol(c.factusol_company_id, c.name);
  }

  /** Vuelca en los campos VISIBLES del formulario los datos del cliente F_CLI
   *  (FACTUSOL manda sobre lo que hubiera: del CRM o tecleado). El NIF solo
   *  si FACTUSOL lo tiene; la dirección solo si tiene alguna (no se borra lo
   *  tecleado con vacíos). Devuelve qué se ha escrito de verdad. */
  function applyFactusolCustomer(cust: FactusolCustomer): FactusolLoaded {
    const nif = (cust.nif ?? "").trim() || null;
    if (nif) setTaxId(nif);
    const fromFactusol: OrderAddress = {
      address_line: cust.domcli ?? "", city: cust.pobcli ?? "",
      postal_code: cust.cpocli ?? "", state: cust.procli ?? "",
      country: countryName(cust.pais_iso2),
    };
    const hasAddress = addressFilled(fromFactusol);
    if (hasAddress) {
      setShipping(fromFactusol);
      setBilling(fromFactusol);
    }
    setFactusolCustomer(cust);
    return { nif, address: hasAddress ? fromFactusol : null };
  }

  /** C-3-fix2: crea la empresa CRM con los datos que vienen de F_CLI y la
   *  vincula al cliente FACTUSOL. Es la acción que faltaba: antes el aviso
   *  decía «elige o crea la empresa abajo» pero no había ningún «abajo». */
  async function createCrmFromFactusol() {
    const cust = pendingFactusolCustomer;
    if (!cust?.codcli) return;
    setCreatingCrmCompany(true);
    setFactusolNotice(null);
    try {
      // Una sola llamada: el backend crea y vincula en la misma transacción,
      // así un fallo no puede dejar la empresa creada sin vínculo (C-3-fix3).
      const name = cust.nombre ?? cust.nofcli ?? "";
      const r = await createFactusolCustomerAndLink({
        factusol_codcli: cust.codcli,
        factusol_customer_data: {
          nombre: name,
          nif: cust.nif ?? "",
          direccion: cust.domcli ?? "",
          ciudad: cust.pobcli ?? "",
          cp: cust.cpocli ?? "",
          provincia: cust.procli ?? "",
          telefono: cust.telcli?.trim() || undefined,
          email: cust.emacli ?? undefined,
          // F1-fix2: el país REAL del cliente (PAICLI); el backend lo
          // normaliza a ISO2 en vez de asumir España.
          pais: cust.paicli ?? undefined,
        },
      });
      setCompanyId(r.company_id);
      setCompanyQuery(name);
      setCompanyCodcli(cust.codcli);
      setPendingCrmCompany(null);
      setPendingFactusolCustomer(null);
      // Limpia el buscador: la próxima búsqueda debe ver el cliente ya «En CRM».
      setCustomerSearchKey((k) => k + 1);
      setFactusolNotice({
        tone: "info",
        text: `Empresa CRM «${name}» creada y vinculada a FACTUSOL nº ${cust.codcli}.`,
      });
    } catch (e) {
      setFactusolNotice({
        tone: "error",
        text: extractErrorMessage(e, "No se pudo crear la empresa CRM."),
      });
    } finally {
      setCreatingCrmCompany(false);
    }
  }

  /** C-3-fix2: vincula el cliente FACTUSOL a una empresa CRM que ya existe. */
  async function linkToExistingCompany() {
    const cust = pendingFactusolCustomer;
    if (!cust?.codcli || !linkCompanyId) return;
    setLinking(true);
    setFactusolNotice(null);
    try {
      await linkFactusolCustomer({
        crm_type: "company", crm_id: linkCompanyId,
        factusol_codcli: cust.codcli,
      });
      const comp = linkCompanies.find((c) => c.id === linkCompanyId);
      if (comp) applyCompany(comp);
      // Recién vinculada: el CODCLI es el del cliente FACTUSOL elegido, cuyos
      // datos ya están en el formulario (los volcó el buscador).
      setCompanyCodcli(cust.codcli);
      setFactusolCustomer(cust);
      setPendingCrmCompany(null);
      setPendingFactusolCustomer(null);
      setLinkingExisting(false);
      setCustomerSearchKey((k) => k + 1);
      setFactusolNotice({
        tone: "info",
        text: `Empresa «${comp?.name ?? ""}» vinculada a FACTUSOL nº ${cust.codcli}.`,
      });
    } catch (e) {
      setFactusolNotice({
        tone: "error",
        text: extractErrorMessage(e, "No se pudo vincular."),
      });
    } finally {
      setLinking(false);
    }
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
      // Tarea B: ya vinculada → se puede crear el pedido; y se precargan
      // los datos del cliente FACTUSOL (NIF y dirección), como en #392. El
      // aviso de la precarga conserva este texto delante.
      setCompanyCodcli(r.factusol_codcli);
      const created = r.created
        ? `Creado en FACTUSOL con el nº ${r.factusol_codcli}.`
        : `Ya existía en FACTUSOL (nº ${r.factusol_codcli}) — vinculado.`;
      setFactusolNotice({ tone: "info", text: created });
      void prefillFromFactusol(r.factusol_codcli, pendingCrmCompany.name, created);
    } catch (e) {
      setFactusolNotice({
        tone: "error",
        text: extractErrorMessage(e, "No se pudo crear en FACTUSOL."),
      });
    } finally {
      setCreatingCustomer(false);
    }
  }

  /** Fase 1: lee el documento de FACTUSOL y vuelca cliente, líneas, fecha y
   *  notas al formulario. Bart revisa y pulsa «Crear pedido»: el pedido queda
   *  con origen FACTUSOL (nunca Woo). No se escribe nada en FACTUSOL. */
  function loadFromInputs() {
    const serie = Number(facSerie);
    const codigo = Number(facCodigo);
    if (!Number.isInteger(serie) || serie <= 0 || !Number.isInteger(codigo) || codigo <= 0) {
      setFacNotice({ tone: "error", text: "Indica la serie y el número del documento." });
      return;
    }
    void loadFactusolDocument(facDocType, serie, codigo);
  }

  async function loadFactusolDocument(
    docType: FactusolOrderDocType, serie: number, codigo: number,
  ) {
    const label = DOC_TYPE_LABEL[docType];
    setFacLoading(true);
    setFacNotice(null);
    try {
      const p = await previewOrderFromFactusol(docType, serie, codigo);
      setFacPreview(p);
      setPayment(initialPayment(p.forma_pago, p.forma_pago_nombre));
      const rows: LineRow[] = p.lines.map((l) => ({
        product_sku: l.codart ?? "",
        description: l.description || l.codart || "",
        quantity: String(l.quantity),
        unit_price: String(l.unit_price),
      }));
      const fallback: LineRow[] = [{
        product_sku: "",
        description: p.referencia || `${label} ${p.numero}`,
        quantity: "1",
        unit_price: String(p.total ?? 0),
      }];
      const next = rows.length > 0 ? rows : fallback;
      setLines((prev) => {
        const kept = prev.filter((l) => l.description.trim() || l.product_sku.trim());
        return [...kept, ...next];
      });
      if (p.fecha) setPlacedAt(p.fecha);
      setNotes((prev) => prev || (
        `Creado desde el ${label} FACTUSOL ${p.numero}`
        + (p.referencia ? ` · ref. ${p.referencia}` : "")
      ));
      if (p.company_id && p.company_name) {
        setCompanyId(p.company_id);
        setCompanyQuery(p.company_name);
      }
      const parts = [
        `${label} ${p.numero} cargado: ${next.length} línea(s)`,
        p.total != null ? `${p.total.toFixed(2)} €` : null,
        p.forma_pago_nombre ? `forma de pago ${p.forma_pago_nombre}` : null,
        p.company_linked
          ? `cliente «${p.company_name}»`
          : `cliente FACTUSOL «${p.cliente_nombre ?? "?"}» (nº ${p.cliente_codigo ?? "?"}) sin vincular: búscalo arriba para vincularlo o elige la empresa`,
      ].filter(Boolean);
      setFacNotice(p.already_imported
        ? { tone: "error",
            text: `Este ${label} ya se importó como el pedido ${p.already_imported.order_number}.` }
        : { tone: "info", text: `${parts.join(" · ")}.` });
    } catch (e) {
      setFacPreview(null);
      setFacNotice({
        tone: "error",
        text: extractErrorMessage(e, "No se pudo leer el documento de FACTUSOL."),
      });
    } finally {
      setFacLoading(false);
    }
  }

  /** B) Empresa vinculada a FACTUSOL → NIF, dirección y nombre fiscal del
   *  cliente F_CLI (solo lectura de FACTUSOL; sus datos mandan sobre lo que
   *  tuviera el formulario, del CRM o tecleado). El aviso dice exactamente
   *  qué se ha volcado; si no se encuentra el cliente o falla la lectura, lo
   *  dice y el formulario se queda como estaba. Una respuesta que llegue
   *  después de elegir otra empresa se ignora. */
  async function prefillFromFactusol(codcli: string, crmName?: string, prefix?: string) {
    const seq = ++prefillSeq.current;
    try {
      const hits = await searchFactusolCustomers(codcli, "codcli");
      if (seq !== prefillSeq.current) return;
      const cust = hits.find((h) => h.codcli === codcli)
        ?? (hits.length === 1 ? hits[0] : null);
      if (!cust) {
        setFactusolCustomer(null);
        setFactusolNotice({
          tone: "error",
          text: `${prefix ? `${prefix} ` : ""}No se encontró el cliente FACTUSOL nº ${codcli}: `
            + "no se ha cargado nada en el pedido.",
        });
        return;
      }
      const loaded = applyFactusolCustomer(cust);
      setFactusolNotice(loadedNotice(cust, loaded, { crmName, prefix }));
    } catch (e) {
      if (seq !== prefillSeq.current) return;
      setFactusolCustomer(null);
      setFactusolNotice({
        tone: "error",
        text: `${prefix ? `${prefix} ` : ""}${extractErrorMessage(
          e, `No se pudo leer el cliente FACTUSOL nº ${codcli}`,
        )}: no se ha cargado nada en el pedido (se mantienen los datos del CRM).`,
      });
    }
  }

  function updateLine(i: number, key: keyof LineRow, value: string) {
    setLines((rs) => rs.map((r, j) => (j === i ? { ...r, [key]: value } : r)));
  }

  // C-4: el SKU es opcional (servicios, reparaciones, muestras). Lo que
  // identifica la línea es la descripción.
  const lineErrors = lines.map((l) =>
    !l.description.trim()
      ? "Indica la descripción."
      : num(l.quantity) <= 0
        ? "La cantidad debe ser > 0."
        : num(l.unit_price) < 0
          ? "El precio no puede ser negativo."
          : null,
  );
  // Tarea B: el alta MANUAL exige EMPRESA vinculada a FACTUSOL (un contacto
  // solo no basta: la factura y el albarán se hacen al cliente F_CLI). Con
  // un documento FACTUSOL de origen (Fase 1) el cliente viene del documento.
  const companyHasCodcli = Boolean(companyId && companyCodcli);
  const customerOk = facPreview ? Boolean(companyId || contactId) : companyHasCodcli;
  const addressOk = pickup || addressFilled(shipping);
  const valid = customerOk && addressOk && lines.length > 0
    && lineErrors.every((e) => e === null) && !facPreview?.already_imported
    && (!facPreview || paymentReady(payment));

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
        // Fase 1: si el alta partió de un documento FACTUSOL, el pedido lo lleva
        // como origen (nunca `woocommerce`) con su nº y forma de pago.
        factusol_source: facPreview ? {
          doc_type: facPreview.doc_type,
          serie: facPreview.serie,
          codigo: facPreview.codigo,
          referencia: facPreview.referencia,
          forma_pago: payment.forma_pago ?? facPreview.forma_pago,
          forma_pago_nombre: payment.forma_pago_nombre ?? facPreview.forma_pago_nombre,
        } : undefined,
        // Fase 2: paso de pago (opción B) + albarán en FACTUSOL, solo si el
        // pedido parte de un documento de FACTUSOL.
        payment: facPreview ? payment : undefined,
        create_albaran: facPreview ? true : undefined,
        lines: lines.map((l) => ({
          product_sku: l.product_sku.trim(),
          description: l.description.trim() || l.product_sku.trim(),
          quantity: num(l.quantity),
          unit_price: num(l.unit_price),
        })),
      });
      // La ficha hace polling del job del albarán y enseña su nº al terminar.
      const albaranJob = order.albaran_job_id
        ? `?albaran_job=${encodeURIComponent(order.albaran_job_id)}`
        : "";
      router.push(`/erp/orders/${order.id}${albaranJob}`);
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

        {/* Fase 1 — crear el pedido desde un documento que ya existe en FACTUSOL. */}
        <section className="erp-card">
          <h3>Importar de FACTUSOL</h3>
          <p className="muted small">
            Crea el pedido a partir de un presupuesto o de un pedido de cliente
            que ya existe en FACTUSOL. Solo se lee: no se escribe nada allí.
          </p>
          <div className="form-row">
            <label className="field">
              <span>Documento</span>
              <select
                aria-label="Tipo de documento FACTUSOL"
                value={facDocType}
                onChange={(e) => setFacDocType(e.target.value as FactusolOrderDocType)}
              >
                <option value="presupuestos">Presupuesto / proforma</option>
                <option value="pedidos">Pedido de cliente</option>
              </select>
            </label>
            {facDocType === "pedidos" ? (
              <>
                <label className="field">
                  <span>Serie</span>
                  <input type="number" min="1" max="9" value={facSerie}
                         aria-label="Serie del documento FACTUSOL"
                         onChange={(e) => setFacSerie(e.target.value)} />
                </label>
                <label className="field">
                  <span>Número</span>
                  <input type="number" min="1" value={facCodigo}
                         aria-label="Número del documento FACTUSOL"
                         onChange={(e) => setFacCodigo(e.target.value)} />
                </label>
                <button type="button" className="button small"
                        disabled={facLoading || !facCodigo}
                        onClick={loadFromInputs}>
                  {facLoading ? "Leyendo…" : "Cargar documento"}
                </button>
              </>
            ) : null}
          </div>
          {facDocType === "presupuestos" ? (
            /* A) El mismo buscador/listado de proformas de la ficha de empresa:
               sin teclear serie y número. */
            <QuotePicker
              companyId={companyId}
              busy={facLoading}
              onPick={(q) => {
                if (q.codpre) void loadFactusolDocument("presupuestos", 1, Number(q.codpre));
              }}
            />
          ) : (
            <PedidoClientePicker
              busy={facLoading}
              onPick={(d) => {
                const serie = d.serie ?? 0;
                const codigo = Number(d.codigo);
                setFacSerie(String(serie || ""));
                setFacCodigo(Number.isInteger(codigo) && codigo > 0 ? String(codigo) : "");
                if (serie > 0 && Number.isInteger(codigo) && codigo > 0) {
                  void loadFactusolDocument("pedidos", serie, codigo);
                }
              }}
            />
          )}
          {facNotice ? (
            <p className={facNotice.tone === "error" ? "form-error" : "form-info"}
               role="status">
              {facNotice.text}
            </p>
          ) : null}
          {facPreview?.already_imported ? (
            <p className="small">
              <Link href={`/erp/orders/${facPreview.already_imported.order_id}`}>
                Abrir el pedido {facPreview.already_imported.order_number}
              </Link>
            </p>
          ) : null}
          {facPreview && !facPreview.already_imported ? (
            /* Fase 2: al crear el pedido se crea su albarán en FACTUSOL (sin
               factura) y se confirma el pago: sin pago, o pagado (el cobro se
               registra cuando exista la factura). */
            <>
              <p className="muted small">
                Al crear el pedido se creará su <strong>albarán en FACTUSOL</strong>
                {" "}(sin factura). Confirma el pago:
              </p>
              <PaymentStep value={payment} onChange={setPayment} />
            </>
          ) : null}
        </section>

        <section className="erp-card">
          <h3>Cliente</h3>
          <p className="muted small">
            Origen:{" "}
            <strong>
              {facPreview
                ? `${DOC_TYPE_LABEL[facPreview.doc_type]} FACTUSOL ${facPreview.numero}`
                : "manual"}
            </strong>
            {facPreview
              ? ` · nº de pedido ${facPreview.order_number}.`
              : " · el número de pedido se genera solo."}
          </p>
          {/* C-3: busca primero en FACTUSOL (fuente contable) y luego en CRM. */}
          <CustomerAutocomplete key={customerSearchKey} onPick={onPickCustomer} />
          {factusolNotice ? (
            <p className={factusolNotice.tone === "error" ? "form-error" : "form-info"}
               role="status">
              {factusolNotice.text}
            </p>
          ) : null}
          {/* C-3-fix2: cliente FACTUSOL sin empresa CRM → 2 acciones reales. */}
          {pendingFactusolCustomer ? (
            <div className="erp-factusol-actions">
              <button type="button" className="button small"
                      disabled={creatingCrmCompany}
                      onClick={createCrmFromFactusol}>
                {creatingCrmCompany
                  ? "Creando…"
                  : "Crear empresa CRM con estos datos y vincular"}
              </button>
              <button type="button" className="button small secondary"
                      onClick={() => setLinkingExisting((v) => !v)}>
                Vincular a empresa CRM existente…
              </button>
            </div>
          ) : null}

          {pendingFactusolCustomer && linkingExisting ? (
            <div className="erp-link-existing">
              <label className="field">
                <span>Empresa CRM a vincular</span>
                <input
                  type="text" list="erp-link-companies" value={linkCompanyQuery}
                  placeholder="Buscar empresa sin FACTUSOL…"
                  aria-label="Empresa CRM a vincular"
                  onChange={(e) => setLinkCompanyQuery(e.target.value)}
                />
                <datalist id="erp-link-companies">
                  {linkCompanies.map((c) => <option key={c.id} value={c.name} />)}
                </datalist>
              </label>
              <div className="erp-exc-actions">
                <button type="button" className="button small"
                        disabled={!linkCompanyId || linking}
                        onClick={linkToExistingCompany}>
                  {linking ? "Vinculando…" : "Vincular"}
                </button>
                <button type="button" className="button small secondary"
                        onClick={() => setLinkingExisting(false)}>
                  Cancelar
                </button>
              </div>
            </div>
          ) : null}

          {pendingCrmCompany ? (
            <p className="form-info" role="status">
              «{pendingCrmCompany.name}» aún no existe en FACTUSOL: créala primero
              (el pedido se factura y se le crea el albarán a ese cliente).{" "}
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
          {factusolCustomer ? (
            /* Lo que se ha cargado de FACTUSOL, a la vista: el nombre fiscal
               del cliente F_CLI (los documentos van a este nombre). La
               «Empresa» de arriba sigue siendo la del CRM, a la que queda el
               pedido. */
            <div className="form-row">
              <label className="field">
                <span>Nombre fiscal (FACTUSOL nº {factusolCustomer.codcli})</span>
                <input type="text" readOnly value={fiscalName(factusolCustomer)}
                       aria-label="Nombre fiscal FACTUSOL"
                       title="Nombre fiscal del cliente en FACTUSOL (solo lectura). Las facturas y albaranes van a este nombre." />
              </label>
              <label className="field">
                <span>NIF en FACTUSOL</span>
                <input type="text" readOnly value={factusolCustomer.nif ?? ""}
                       aria-label="NIF FACTUSOL"
                       placeholder="(sin NIF en FACTUSOL)" />
              </label>
            </div>
          ) : null}
          {!customerOk ? (
            <p className="muted small" role="note">
              {facPreview
                ? "Elige una empresa o un contacto de la lista."
                : !companyId
                  ? "Elige una empresa de la lista (obligatoria: el pedido se factura y se le crea el albarán en FACTUSOL). Un contacto solo no basta."
                  : "La empresa tiene que estar vinculada a un cliente de FACTUSOL antes de crear el pedido."}{" "}
              <a href="/companies/new" target="_blank" rel="noreferrer">
                ¿No existe? Créala primero en Empresas
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
          {/* C-4: si el cliente tiene proformas en FACTUSOL, se pueden volcar
              al pedido en vez de reteclear el presupuesto. */}
          {quotes.length > 0 ? (
            <div className="erp-quotes-inline">
              <button type="button" className="button small secondary"
                      aria-expanded={quotesOpen}
                      onClick={() => setQuotesOpen((v) => !v)}>
                {quotesOpen ? "▾" : "▸"} Proformas FACTUSOL disponibles ({quotes.length})
              </button>
              {quoteNotice ? (
                <p className="form-info" role="status">{quoteNotice}</p>
              ) : null}
              {quotesOpen ? (
                <ul className="erp-quote-list">
                  {quotes.slice(0, 5).map((q) => (
                    <li key={q.codpre ?? ""}>
                      <span>
                        nº {q.codpre} · {q.fecha ?? "—"} ·{" "}
                        {q.total.toFixed(2)} € · {q.referencia || "—"}
                      </span>
                      <button type="button" className="button small"
                              disabled={loadingQuote !== null}
                              onClick={() => loadQuoteLines(q.codpre ?? "")}>
                        {loadingQuote === q.codpre ? "Cargando…" : "Cargar líneas al pedido"}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
          <table className="data-table">
            <thead>
              <tr>
                <th>SKU (opcional)</th><th>Descripción</th><th>Cant.</th>
                <th>Precio ud.</th><th>Total</th><th />
              </tr>
            </thead>
            <tbody>
              {lines.map((l, i) => (
                <tr key={i}>
                  <td>
                    <ArticleAutocompleteInput
                      value={l.product_sku}
                      enabled={articleSearchEnabled}
                      ariaLabel={`SKU línea ${i + 1}`}
                      onChange={(v) => updateLine(i, "product_sku", v)}
                      onPick={(a) => applyArticle(i, a)}
                    />
                  </td>
                  <td>
                    <ArticleAutocompleteInput
                      value={l.description}
                      enabled={articleSearchEnabled}
                      ariaLabel={`Descripción línea ${i + 1}`}
                      onChange={(v) => updateLine(i, "description", v)}
                      onPick={(a) => applyArticle(i, a)}
                    />
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

/** Buscador de pedidos de cliente (F_PCL) por nº, referencia o cliente, sobre
 *  el explorador de documentos de E3 (solo lectura). No hay un listado por
 *  empresa como el de proformas, así que serie+número siguen disponibles. */
function PedidoClientePicker({
  onPick, busy,
}: {
  onPick: (d: FactusolDocument) => void;
  busy?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<FactusolDocument[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const q = query.trim();
    if (!q) { setItems([]); return; }
    let alive = true;
    const handle = window.setTimeout(() => {
      setLoading(true);
      listFactusolDocuments("pedidos", { q, limit: 10 })
        .then((r) => { if (alive) setItems(r.items); })
        .catch(() => { if (alive) setItems([]); })
        .finally(() => { if (alive) setLoading(false); });
    }, 300);
    return () => { alive = false; window.clearTimeout(handle); };
  }, [query]);

  return (
    <div className="erp-quote-picker">
      <label className="field">
        <span>O buscar pedido de cliente</span>
        <input type="text" value={query} aria-label="Buscar pedido de cliente"
               placeholder="nº, referencia o cliente…"
               onChange={(e) => setQuery(e.target.value)} />
      </label>
      {loading ? <p className="muted small" role="status">Buscando pedidos…</p> : null}
      {!loading && items.length > 0 ? (
        <ul className="erp-quote-list">
          {items.map((d) => (
            <li key={`${d.serie}-${d.codigo}`}>
              <span>
                {d.numero} · {d.fecha ?? "—"} · {d.cliente_nombre ?? "—"}
                {d.total != null ? ` · ${d.total.toFixed(2)} €` : ""}
                {d.referencia ? ` · ${d.referencia}` : ""}
              </span>
              <button type="button" className="button small" disabled={busy}
                      onClick={() => onPick(d)}>
                Cargar en el pedido
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
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
