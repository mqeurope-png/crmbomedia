"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { PageHeader } from "../../../components/PageHeader";
import {
  CustomerAutocomplete,
  type CustomerChoice,
} from "../../../components/erp/CustomerAutocomplete";
import {
  DocumentLinesTable,
  emptyDocumentLine,
  lineNum,
  type DocumentLine,
} from "../../../components/erp/DocumentLinesTable";
import { initialPayment, paymentReady, PaymentStep } from "../../../components/erp/PaymentStep";
import { PrimaryActionBar } from "../../../components/erp/PrimaryActionBar";
import { QuotePicker } from "../../../components/erp/QuotePicker";
import { listContacts, type Contact } from "../../../lib/api";
import { getCompany, listCompanies, type Company } from "../../../lib/companiesApi";
import { extractErrorMessage } from "../../../lib/errors";
import {
  createFactusolCustomer,
  createFactusolCustomerAndLink,
  createOrder,
  FACTUSOL_SERIES,
  getFactusolQuote,
  linkFactusolCustomer,
  listFactusolDocuments,
  listFactusolQuotes,
  previewOrderFromFactusol,
  searchFactusolCustomers,
  type FactusolCustomer,
  type FactusolDocument,
  type FactusolOrderDocType,
  type FactusolOrderPreview,
  type FactusolQuote,
  type FactusolRegime,
  type OrderAddress,
  type PaymentIntentInput,
} from "../../../lib/erpApi";

const EMPTY_ADDRESS: OrderAddress = {
  address_line: "", city: "", postal_code: "", state: "", country: "España",
};

/** Descripción de la línea de portes del pedido (la misma etiqueta que el
 *  PDF pone en la línea de portes de los documentos web). */
const PORTES_DESCRIPTION = "Portes";

/** IVA general. Es también lo que asume el backend cuando una línea llega
 *  sin `tax_rate`. */
const IVA_GENERAL = 21;

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function addressFilled(a: OrderAddress): boolean {
  return Boolean(a.address_line?.trim() || a.city?.trim() || a.postal_code?.trim());
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

function eur(n: number): string {
  return `${n.toFixed(2)} €`;
}

/** Lote 2 · PR-2 — IVA por defecto de las líneas según el régimen del cliente
 *  F_CLI (Tarea C): nacional → 21 %; intracomunitario / exportación → 0 %
 *  (exento). Sin ficha leída, el general. Se manda como `tax_rate` en CADA
 *  línea (portes incluidos) para que el total del panel sea exactamente el
 *  `total_amount` que guarda el backend: Σ round(cant × precio, 2) × (1 + IVA). */
function regimeTaxRate(regime: FactusolRegime | null | undefined): number {
  return regime === "intracomunitario" || regime === "exportacion" ? 0 : IVA_GENERAL;
}

/** Alta de pedido manual (Fase D · D-2): encargos por teléfono, muestras y
 *  reparaciones sin ticket Woo. El origen es fijo `manual` y el número lo
 *  genera el backend (`MANUAL-000001`).
 *
 *  Lote 2 · PR-2 (revisión de diseño §10): tres pasos numerados — Empresa,
 *  Líneas, Envío — con el total (IVA incluido) siempre visible en un panel
 *  lateral (barra fija abajo en móvil), el requisito FACTUSOL explicado donde
 *  ocurre («Vincular ahora» junto a la empresa) y el botón desactivado con su
 *  motivo escrito debajo. Nada de lo anterior se quita: importar de FACTUSOL
 *  es un desplegable dentro del paso 1; facturación y notas, uno del paso 3. */
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

/** Dirección de la empresa CRM tal como la usa el formulario. */
function companyAddressOf(c: Company): OrderAddress {
  return {
    address_line: c.address_line ?? "", city: c.city ?? "",
    postal_code: c.postal_code ?? "", state: c.state ?? "",
    country: c.country ?? "España",
  };
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

/** Atajo de la dirección de envío: «La de la empresa» (los campos siguen a la
 *  dirección conocida de la empresa: F_CLI o CRM) u «Otra dirección» (lo que
 *  Bart escriba). Editar un campo pasa solo a «Otra dirección». */
type ShippingMode = "company" | "other";

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
  // Lote 7 · P1: serie (empresa emisora) del pedido MANUAL. Por defecto 5
  // (Streamtec, el default de `resolve_serie`). Solo se envía en el alta
  // manual (con documento FACTUSOL de origen la serie la hereda el documento).
  const [factusolSerie, setFactusolSerie] = useState<number>(5);
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState<DocumentLine[]>([emptyDocumentLine()]);
  // Portes del pedido: se mandan como su propia LÍNEA (`is_shipping`), igual
  // que los pedidos web los llevan aparte de la mercancía.
  const [portes, setPortes] = useState("");
  const [pickup, setPickup] = useState(false);
  const [shipping, setShipping] = useState<OrderAddress>({ ...EMPTY_ADDRESS });
  const [shippingMode, setShippingModeState] = useState<ShippingMode>("company");
  // Espejo del modo para los callbacks asíncronos (vincular, precarga de la
  // URL), que no ven el estado del render en curso.
  const shippingModeRef = useRef<ShippingMode>("company");
  // Última dirección conocida de la empresa elegida (F_CLI manda sobre CRM):
  // es lo que vuelca el atajo «La de la empresa».
  const [companyAddress, setCompanyAddress] = useState<OrderAddress | null>(null);
  // Nombre de envío (dropshipping): destinatario del albarán cuando NO es la
  // empresa cliente. Vacío = se envía a la empresa; la factura va SIEMPRE a
  // los datos fiscales de la empresa.
  const [shippingName, setShippingName] = useState("");
  const [billingSame, setBillingSame] = useState(true);
  const [billing, setBilling] = useState<OrderAddress>({ ...EMPTY_ADDRESS });
  const [pendingCrmCompany, setPendingCrmCompany] = useState<Company | null>(null);
  // Tarea B: CODCLI (F_CLI) de la empresa elegida. El alta manual exige
  // empresa VINCULADA a FACTUSOL — sin CODCLI no se puede crear el pedido
  // (se ofrece «Vincular ahora»). null = sin empresa o sin vincular.
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

  // Fase 5 — «Crear pedido» desde el explorador de documentos: la URL trae
  // ?doc_type=&serie=&codigo= y se precarga el documento FACTUSOL con el mismo
  // flujo de importación (Fase 1/2), sin teclear serie ni número.
  const presetDocType = searchParams?.get("doc_type") ?? null;
  const presetDocSerie = searchParams?.get("serie") ?? null;
  const presetDocCodigo = searchParams?.get("codigo") ?? null;

  // Lote 2 · PR-2: «Importar de FACTUSOL» es un desplegable del paso 1 (abierto
  // si la URL trae un documento o al cargar uno); facturación y notas, otro
  // del paso 3.
  const [importOpen, setImportOpen] = useState(
    presetDocType === "presupuestos" || presetDocType === "pedidos",
  );
  const [moreOpen, setMoreOpen] = useState(false);

  function setShippingMode(mode: ShippingMode) {
    shippingModeRef.current = mode;
    setShippingModeState(mode);
  }

  /** Dirección de la empresa CRM → campos de envío. Con «La de la empresa»
   *  (o sin nada escrito) se vuelca; si Bart ya escribió otra dirección se
   *  respeta, y solo se recuerda para el atajo del selector. */
  function adoptCompanyAddress(c: Company) {
    const addr = companyAddressOf(c);
    setCompanyAddress(addressFilled(addr) ? addr : null);
    setShipping((prev) =>
      (shippingModeRef.current === "company" || !addressFilled(prev) ? addr : prev));
  }

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
        adoptCompanyAddress(c);
        // B) Empresa vinculada: FACTUSOL manda sobre NIF y direcciones.
        setFactusolCustomer(null);
        if (c.factusol_company_id) void prefillFromFactusol(c.factusol_company_id, c.name);
      })
      .catch(() => undefined);
    return () => { alive = false; };
    // prefillFromFactusol / adoptCompanyAddress solo usan setters: estables.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presetCompanyId]);

  useEffect(() => {
    if (presetDocType !== "presupuestos" && presetDocType !== "pedidos") return;
    const serie = Number(presetDocSerie);
    const codigo = Number(presetDocCodigo);
    if (!Number.isInteger(serie) || serie <= 0
        || !Number.isInteger(codigo) || codigo <= 0) return;
    setFacDocType(presetDocType);
    setFacSerie(String(serie));
    setFacCodigo(String(codigo));
    void loadFactusolDocument(presetDocType, serie, codigo);
    // loadFactusolDocument solo usa setters/estado: referencia estable.
  }, [presetDocType, presetDocSerie, presetDocCodigo]);

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

  // --- Total con IVA, como lo calcula el backend ----------------------------
  const regime = factusolCustomer?.regime ?? null;
  const taxRate = regimeTaxRate(regime);
  const articlesBase = useMemo(
    () => round2(lines.reduce(
      (sum, l) => sum + round2(lineNum(l.quantity) * lineNum(l.unit_price)), 0,
    )),
    [lines],
  );
  const portesAmount = lineNum(portes) > 0 ? round2(lineNum(portes)) : 0;
  const base = round2(articlesBase + portesAmount);
  const total = round2(base * (1 + taxRate / 100));
  const ivaAmount = round2(total - base);

  /** C-4: vuelca el desglose de una proforma en las líneas del pedido.
   *  Si la proforma se hizo en el FACTUSOL de escritorio no hay desglose
   *  (F_PRE es mono-línea) y llega una única línea con su texto e importe. */
  /** Carga una proforma existente en el alta.
   *
   *  `mode`:
   *   - `"all"`    → cliente + líneas (como duplicar, pero hacia un pedido).
   *   - `"lines"`  → SOLO los conceptos; el cliente que ya hubiera elegido el
   *     comercial NO se toca (reutilizar los mismos artículos con otro cliente).
   *
   *  Si ya hay líneas escritas se pregunta si añadir o reemplazar. Los portes
   *  de la proforma van a su campo de portes, no a una línea. Las líneas
   *  quedan editables antes de crear el pedido. */
  async function loadQuoteIntoForm(quote: FactusolQuote, mode: "all" | "lines") {
    const codpre = quote.codpre ?? "";
    if (!codpre) return;
    const hasLines = lines.some((l) => l.description.trim() || l.sku.trim());
    let replace = false;
    if (hasLines) {
      replace = window.confirm(
        `Ya hay líneas en el pedido.\n\n`
        + `Aceptar = REEMPLAZARLAS por las de la proforma ${codpre}.\n`
        + `Cancelar = AÑADIRLAS a las que ya hay.`,
      );
    }
    setLoadingQuote(codpre);
    setQuoteNotice(null);
    try {
      const full = await getFactusolQuote(codpre);
      const rows: DocumentLine[] = (full.lines ?? []).map((l) => emptyDocumentLine({
        sku: l.codart ?? "",
        description: l.description,
        quantity: String(l.quantity),
        unit_price: String(l.unit_price),
      }));
      const fallback: DocumentLine[] = [emptyDocumentLine({
        description: full.referencia || `Proforma ${codpre}`,
        unit_price: String(full.base),
      })];
      const next = rows.length > 0 ? rows : fallback;
      setLines((prev) => {
        if (replace) return next;
        const kept = prev.filter((l) => l.description.trim() || l.sku.trim());
        return [...kept, ...next];
      });
      // Los portes de la cabecera van a SU campo (no a una línea): si no, al
      // crear el pedido se duplicarían con la línea de portes que arma `submit`.
      const portes = full.portes ?? quote.portes ?? 0;
      if (portes > 0) setPortes(String(portes));

      const partes: string[] = [];
      if (mode === "all") partes.push(await loadQuoteCustomer(quote, full));
      partes.push(
        `${next.length} línea${next.length === 1 ? "" : "s"} `
        + `${replace ? "reemplazadas" : "añadidas"}`,
      );
      if (portes > 0) partes.push(`portes ${portes} €`);
      setQuoteNotice(
        `Proforma ${quote.numero || codpre}: ${partes.join(" · ")}.`
        + (full.line_source && full.line_source !== "F_LPS" && rows.length === 0
          ? " Se creó en FACTUSOL de escritorio: ha entrado como una línea"
            + " única, revisa el importe."
          : ""),
      );
    } catch (e) {
      setQuoteNotice(extractErrorMessage(e, "No se pudo cargar la proforma."));
    } finally {
      setLoadingQuote(null);
    }
  }

  /** «Cargar todo» — el CLIENTE de la proforma pasa a ser el del pedido.
   *
   *  Se lee su ficha F_CLI por el CODCLI de la cabecera (`CLIPRE`) y se aplica
   *  igual que al elegirlo en el buscador de clientes: nombre fiscal, NIF,
   *  dirección, régimen de IVA y —si ese CODCLI ya está vinculado— la empresa
   *  CRM del PROPIO cliente. Ni se busca en el CRM por nombre ni se sustituye
   *  por otra ficha: es el cliente con el que se va a facturar.
   *
   *  El CODCLI hay que leerlo de la cabecera y no de un `company` anotado: el
   *  buscador de proformas (`/quotes/search`) y el detalle (`/quotes/{codpre}`)
   *  NO cruzan con el CRM — solo lo hace el listado por empresa—, así que desde
   *  el buscador no venía empresa ninguna y «Cargar todo» se quedaba sin
   *  cliente (regresión del #453). Aun con empresa anotada faltaba su CODCLI,
   *  y sin él el alta se queda bloqueada en «falta vincular a FACTUSOL».
   *
   *  Nada de esto mete el pedido en la vía «documento FACTUSOL»: sigue siendo
   *  un pedido MANUAL (sin `factusol_source`, sin paso de pago ni albarán).
   *
   *  Devuelve la frase que le toca en el aviso de la carga. */
  async function loadQuoteCustomer(
    quote: FactusolQuote, full: FactusolQuote,
  ): Promise<string> {
    const codcli = String(quote.clipre ?? full.clipre ?? "").trim();
    const nombre = quote.cliente_nombre || full.cliente_nombre || "sin nombre";
    if (!codcli) return "la proforma no dice de qué cliente es: elígelo a mano";
    try {
      const cust = await fetchFactusolCustomer(codcli);
      if (!cust) {
        return `su cliente FACTUSOL nº ${codcli} («${nombre}») no está en `
          + "FACTUSOL: elige la empresa a mano";
      }
      return setFactusolClient(cust)
        ? `cliente «${cust.crm_link?.name ?? nombre}»`
        : `su cliente FACTUSOL nº ${codcli} («${fiscalName(cust) || nombre}») NO `
          + "está vinculado a ninguna empresa del CRM: créala o vincúlala debajo";
    } catch (e) {
      // Un fallo leyendo el cliente no puede tirar las líneas ya cargadas: por
      // eso va en su propio try y solo cambia el texto del aviso.
      return extractErrorMessage(e, `no se pudo leer su cliente FACTUSOL nº ${codcli}`);
    }
  }

  function pickCompany(value: string) {
    setCompanyQuery(value);
    const hit = companies.find((c) => c.name === value);
    setCompanyId(hit?.id ?? null);
    // Tarea B: empresa vinculada → CODCLI; sin vincular → «vincúlala primero».
    setCompanyCodcli(hit?.factusol_company_id ?? null);
    setPendingCrmCompany(hit && !hit.factusol_company_id ? hit : null);
    // Otra empresa (o ninguna): lo precargado de la anterior ya no vale, y una
    // respuesta tardía de su precarga se descarta.
    prefillSeq.current += 1;
    setFactusolCustomer(null);
    if (!hit) return;
    setTaxId((prev) => prev || hit.tax_id || "");
    adoptCompanyAddress(hit);
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
   *  - Cliente FACTUSOL (vinculado o no) → `setFactusolClient`.
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
    // B) FACTUSOL manda: NIF y dirección del cliente F_CLI al formulario (el
    // hit del buscador ya trae la fila, no hace falta releer).
    setFactusolClient(choice.customer);
  }

  /** Deja un cliente F_CLI como CLIENTE del pedido: sus datos (nombre fiscal,
   *  NIF, dirección, régimen) en el formulario y, si ese CODCLI ya está
   *  vinculado, la empresa CRM del propio cliente — nunca otra ficha. Sin
   *  vínculo no hay empresa a la que ponerle el pedido, y se ofrecen ahí mismo
   *  «crearla» o «vincularla».
   *
   *  Lo usan el buscador de clientes y «Cargar todo» de una proforma.
   *  Devuelve si el cliente tenía empresa CRM. */
  function setFactusolClient(cust: FactusolCustomer): boolean {
    prefillSeq.current += 1;
    setPendingCrmCompany(null);
    setLinkingExisting(false);
    const loaded = applyFactusolCustomer(cust);
    if (cust.crm_link?.type === "company") {
      setCompanyId(cust.crm_link.id);
      setCompanyQuery(cust.crm_link.name);
      setCompanyCodcli(cust.codcli);
      setPendingFactusolCustomer(null);
      setFactusolNotice(loadedNotice(cust, loaded, {
        crmName: cust.crm_link.name,
        prefix: `Cliente FACTUSOL nº ${cust.codcli} — ya vinculado a «${cust.crm_link.name}».`,
      }));
      return true;
    }
    // Sin empresa CRM, el pedido tampoco puede quedarse con la que hubiera
    // elegida antes: sería OTRO cliente distinto del que se acaba de cargar.
    setCompanyId(null);
    setCompanyCodcli(null);
    setCompanyQuery(cust.nombre ?? "");
    setPendingFactusolCustomer(cust);
    setFactusolNotice(loadedNotice(cust, loaded, {
      prefix: `Cliente FACTUSOL nº ${cust.codcli} sin empresa en el CRM. Elige debajo qué hacer.`,
    }));
    return false;
  }

  function applyCompany(c: Company) {
    setCompanyId(c.id);
    setCompanyQuery(c.name);
    setCompanyCodcli(c.factusol_company_id ?? null);
    setTaxId((prev) => prev || c.tax_id || "");
    adoptCompanyAddress(c);
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
      // La dirección de F_CLI ES la de la empresa: el atajo apunta a ella.
      setCompanyAddress(fromFactusol);
      setShippingMode("company");
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

  /** «Vincular ahora» (Lote 2 · PR-2, antes «Crear en FACTUSOL»): da de alta
   *  el cliente F_CLI con los datos de la empresa CRM — o lo vincula si ya
   *  existe con ese NIF — sin salir del formulario. */
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
    setImportOpen(true);
    try {
      const p = await previewOrderFromFactusol(docType, serie, codigo);
      setFacPreview(p);
      setPayment(initialPayment(p.forma_pago, p.forma_pago_nombre));
      const rows: DocumentLine[] = p.lines.map((l) => emptyDocumentLine({
        sku: l.codart ?? "",
        description: l.description || l.codart || "",
        quantity: String(l.quantity),
        unit_price: String(l.unit_price),
      }));
      const fallback: DocumentLine[] = [emptyDocumentLine({
        description: p.referencia || `${label} ${p.numero}`,
        unit_price: String(p.total ?? 0),
      })];
      const next = rows.length > 0 ? rows : fallback;
      setLines((prev) => {
        const kept = prev.filter((l) => l.description.trim() || l.sku.trim());
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

  /** La ficha F_CLI de ese CODCLI (solo lectura de FACTUSOL). `null` si no
   *  aparece: el buscador por CODCLI puede traer varios parecidos, y entonces
   *  solo vale el que casa exactamente. */
  async function fetchFactusolCustomer(codcli: string): Promise<FactusolCustomer | null> {
    const hits = await searchFactusolCustomers(codcli, "codcli");
    return hits.find((h) => h.codcli === codcli)
      ?? (hits.length === 1 ? hits[0] : null);
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
      const cust = await fetchFactusolCustomer(codcli);
      if (seq !== prefillSeq.current) return;
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

  // C-4: el SKU es opcional (servicios, reparaciones, muestras). Lo que
  // identifica la línea es la descripción.
  const lineErrors = lines.map((l) =>
    !l.description.trim()
      ? "Indica la descripción."
      : lineNum(l.quantity) <= 0
        ? "La cantidad debe ser > 0."
        : lineNum(l.unit_price) < 0
          ? "El precio no puede ser negativo."
          : null,
  );
  // Tarea B: el alta MANUAL exige EMPRESA vinculada a FACTUSOL (un contacto
  // solo no basta: la factura y el albarán se hacen al cliente F_CLI). Con
  // un documento FACTUSOL de origen (Fase 1) el cliente viene del documento.
  const companyHasCodcli = Boolean(companyId && companyCodcli);
  const customerOk = facPreview ? Boolean(companyId || contactId) : companyHasCodcli;
  const addressOk = pickup || addressFilled(shipping);
  const anyLineContent = lines.some((l) => l.description.trim() || l.sku.trim());
  const firstLineError = lineErrors.findIndex((e) => e !== null);

  // Lote 2 · PR-2: el botón desactivado siempre lleva su motivo escrito. El
  // primer bloqueo en el orden de los pasos (empresa → líneas → envío → pago).
  const submitReason: string | null = !customerOk
    ? (facPreview
      ? "Elige la empresa o el contacto del pedido."
      : !companyId
        ? "Falta elegir la empresa."
        : "Falta vincular la empresa a FACTUSOL.")
    : facPreview?.already_imported
      ? `Este documento ya es el pedido ${facPreview.already_imported.order_number}: no se puede importar dos veces.`
      : !anyLineContent
        ? "Añade al menos una línea."
        : firstLineError !== -1
          ? `Línea ${firstLineError + 1}: ${lineErrors[firstLineError]}`
          : !addressOk
            ? "Falta la dirección de envío."
            : facPreview && !paymentReady(payment)
              ? "Completa el paso de pago."
              : null;
  const valid = submitReason === null;

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
        // Dropshipping: el destinatario del albarán si no es la empresa.
        shipping_name: pickup ? null : shippingName.trim() || null,
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
        // Lote 7 · P1: serie (empresa emisora) elegida a mano. Solo en el alta
        // MANUAL: con documento FACTUSOL de origen la serie la hereda el
        // documento (no se manda).
        factusol_serie: facPreview ? undefined : factusolSerie,
        // Fase 2: paso de pago (opción B) + albarán en FACTUSOL, solo si el
        // pedido parte de un documento de FACTUSOL.
        payment: facPreview ? payment : undefined,
        create_albaran: facPreview ? true : undefined,
        lines: [
          ...lines.map((l) => ({
            product_sku: l.sku.trim(),
            description: l.description.trim() || l.sku.trim(),
            quantity: lineNum(l.quantity),
            unit_price: lineNum(l.unit_price),
            // El IVA del régimen del cliente: el mismo que suma el panel.
            tax_rate: taxRate,
          })),
          // Portes: su propia línea, marcada como tal (el backend la manda a
          // los portes del documento FACTUSOL, no a una línea de mercancía).
          ...(lineNum(portes) > 0 ? [{
            product_sku: "",
            description: PORTES_DESCRIPTION,
            quantity: 1,
            unit_price: lineNum(portes),
            tax_rate: taxRate,
            is_shipping: true,
          }] : []),
        ],
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

  const ivaLabel = taxRate > 0 ? `IVA ${taxRate} %` : "Exento";
  const regimeNote = regime
    ? (taxRate > 0
      ? `Régimen ${factusolCustomer?.regime_label ?? "nacional"} (ficha FACTUSOL nº ${factusolCustomer?.codcli}): IVA general del ${IVA_GENERAL} %.`
      : `Sin IVA: régimen ${factusolCustomer?.regime_label ?? regime} según la ficha FACTUSOL nº ${factusolCustomer?.codcli}.`)
    : `IVA general del ${IVA_GENERAL} %. Se ajusta al régimen de la ficha FACTUSOL al elegir la empresa.`;
  const barHint = facPreview
    ? "Al crear el pedido, BoHub crea también su albarán en FACTUSOL (sin factura)."
    : "Se guarda en BoHub con nº MANUAL; la factura y el albarán se emiten desde su ficha.";
  const moreSummary = [
    !billingSame ? "otra dirección de facturación" : null,
    notes.trim() ? "con notas" : null,
  ].filter(Boolean).join(" · ");

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

      <form className="erp-manual-form erp-new-order" onSubmit={submit}>
        {error ? <p className="form-error">{error}</p> : null}

        <div className="erp-new-order-main">
          {/* ---- 1 · Empresa ------------------------------------------------ */}
          <section className="erp-card erp-step" aria-labelledby="erp-step-1">
            <h3 id="erp-step-1" className="erp-step-title">
              <span className="erp-step-num">1</span> · Empresa
            </h3>
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
            {companyId ? (
              <p className="erp-company-state">
                {companyCodcli ? (
                  <span className="erp-new-order-pill is-linked">
                    FACTUSOL nº <span className="mono">{companyCodcli}</span>
                  </span>
                ) : (
                  <span className="erp-new-order-pill is-crm-only">Solo CRM</span>
                )}
              </p>
            ) : null}
            {/* Lote 2 · PR-2: el requisito FACTUSOL se explica DONDE ocurre y
                trae su solución, sin salir del formulario. */}
            {pendingCrmCompany ? (
              <div className="erp-inline-notice is-amber" role="status">
                <p>
                  <strong>Esta empresa aún no está en FACTUSOL.</strong> Sin ella
                  no se puede emitir factura. Se puede vincular sin salir de aquí:
                  se crea el cliente con los datos de «{pendingCrmCompany.name}»
                  (o se vincula si ya existe con ese NIF).
                </p>
                <button type="button" className="button small secondary"
                        disabled={creatingCustomer}
                        onClick={createInFactusol}>
                  {creatingCustomer ? "Vinculando…" : "Vincular ahora"}
                </button>
              </div>
            ) : null}
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
            {!customerOk && !pendingCrmCompany ? (
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
              {/* Lote 7 · P1: serie (empresa emisora) con la que sale el
                  albarán —y luego la proforma / factura— del pedido. Solo en el
                  alta MANUAL: con un documento FACTUSOL de origen la serie la
                  hereda el documento. */}
              {!facPreview ? (
                <label className="field">
                  <span>Serie (empresa emisora)</span>
                  <select
                    aria-label="Serie del pedido"
                    value={factusolSerie}
                    onChange={(e) => setFactusolSerie(Number(e.target.value))}
                  >
                    {FACTUSOL_SERIES.map((s) => (
                      <option key={s.value} value={s.value}>
                        {s.value} · {s.label}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
            </div>

            {/* Fase 1 — crear el pedido desde un documento que ya existe en
                FACTUSOL. Lote 2: desplegable dentro del paso 1. */}
            <details className="erp-step-helper" open={importOpen}
                     onToggle={(e) => setImportOpen(e.currentTarget.open)}>
              <summary>
                Importar de FACTUSOL
                <span className="muted"> · presupuesto o pedido de cliente ya existente</span>
              </summary>
              <div className="erp-step-helper-body">
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
                  /* A) El mismo buscador/listado de proformas de la ficha de
                     empresa: sin teclear serie y número. */
                  <QuotePicker
                    companyId={companyId}
                    // Mientras se carga una proforma, sus botones bloqueados —
                    // como en el listado de la empresa. Si no, dos clics
                    // seguidos cargan las líneas (y el cliente) dos veces.
                    busy={facLoading || loadingQuote !== null}
                    pickLabel="Cargar todo"
                    // Los DOS caminos (este buscador y el listado de proformas
                    // de la empresa) usan la MISMA carga y producen un pedido
                    // MANUAL normal: traen el cliente FACTUSOL de la proforma +
                    // sus líneas y siguen el ciclo manual. Lo que NO hacen es
                    // pasar por `loadFactusolDocument`, que además metía el
                    // pedido en la vía «documento FACTUSOL» (`factusol_source`
                    // + paso de pago + albarán): para convertir la proforma
                    // COMO DOCUMENTO está «Convertir en pedido» en Proformas.
                    onPick={(q) => void loadQuoteIntoForm(q, "all")}
                    onPickLines={(q) => void loadQuoteIntoForm(q, "lines")}
                    // El aviso de la carga (qué entró, si el cliente no está
                    // vinculado…) se pinta aquí SOLO cuando no hay listado de
                    // proformas de la empresa que ya lo esté enseñando: si no,
                    // desde el buscador el comercial se quedaba sin feedback —
                    // y con los dos, saldría el mismo mensaje por duplicado.
                    notice={quotes.length === 0 ? quoteNotice : null}
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
              </div>
            </details>
          </section>

          {/* ---- 2 · Líneas ------------------------------------------------- */}
          <section className="erp-card erp-step" aria-labelledby="erp-step-2">
            <h3 id="erp-step-2" className="erp-step-title">
              <span className="erp-step-num">2</span> · Líneas
            </h3>
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
                  <ul className="erp-quote-list" aria-label="Proformas de la empresa">
                    {quotes.slice(0, 5).map((q) => (
                      <li key={q.codpre ?? ""}>
                        <span>
                          nº {q.codpre} · {q.fecha ?? "—"} ·{" "}
                          {q.total.toFixed(2)} € · {q.referencia || "—"}
                        </span>
                        <span className="erp-quote-actions">
                          <button type="button" className="button small secondary"
                                  disabled={loadingQuote !== null}
                                  title="Añade solo los conceptos; el cliente que tengas elegido no cambia."
                                  onClick={() => void loadQuoteIntoForm(q, "lines")}>
                            {loadingQuote === q.codpre ? "Cargando…" : "Solo conceptos"}
                          </button>
                          <button type="button" className="button small"
                                  disabled={loadingQuote !== null}
                                  title="Trae el cliente de la proforma y sus líneas."
                                  onClick={() => void loadQuoteIntoForm(q, "all")}>
                            {loadingQuote === q.codpre ? "Cargando…" : "Cargar todo"}
                          </button>
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}
            {/* Lote 2 · PR-2: la misma tabla de líneas que la proforma. Sin
                catálogo (empresa sin vínculo FACTUSOL) son inputs normales. */}
            <DocumentLinesTable
              lines={lines}
              onChange={setLines}
              articleSearch={companyLinked}
              ariaLabel="Líneas del pedido"
            />
          </section>

          {/* ---- 3 · Envío -------------------------------------------------- */}
          <section className="erp-card erp-step" aria-labelledby="erp-step-3">
            <h3 id="erp-step-3" className="erp-step-title">
              <span className="erp-step-num">3</span> · Envío
            </h3>
            {/* Portes como LÍNEA APARTE, igual que los pedidos web: no se
                mezclan con la mercancía. En FACTUSOL viajan en la banda de
                portes de la cabecera (IPOR1), que es donde los deja la app
                Woo→FACTUSOL y de donde el PDF los pinta como línea de cargo. */}
            <div className="form-row">
              <label className="field erp-field-portes">
                <span>Portes (gastos de envío)</span>
                <input type="number" min="0" step="0.01" value={portes}
                       aria-label="Portes"
                       placeholder="0.00"
                       title="Se añaden como línea de portes del pedido, aparte de la mercancía (como en los pedidos web)."
                       onChange={(e) => setPortes(e.target.value)} />
              </label>
              {!pickup ? (
                /* Lote 2 · PR-2: atajo de la dirección de la empresa. */
                <label className="field erp-field-shipmode">
                  <span>Enviar a</span>
                  <select aria-label="Enviar a" value={shippingMode}
                          onChange={(e) => {
                            const mode = e.target.value as ShippingMode;
                            setShippingMode(mode);
                            if (mode === "company" && companyAddress) setShipping(companyAddress);
                          }}>
                    <option value="company">La de la empresa</option>
                    <option value="other">Otra dirección</option>
                  </select>
                </label>
              ) : null}
            </div>
            {lineNum(portes) > 0 ? (
              <p className="muted small" role="status">
                Portes: <strong>{lineNum(portes).toFixed(2)} EUR</strong> como línea
                aparte. En FACTUSOL van en los portes del documento, como los de
                los pedidos web.
              </p>
            ) : null}
            <label className="field-toggle">
              <input type="checkbox" checked={pickup}
                     onChange={(e) => setPickup(e.target.checked)} />
              <span>Recogida en tienda</span>
            </label>
            {!pickup ? (
              <>
                {shippingMode === "company" && !companyAddress ? (
                  <p className="muted small">
                    {companyId
                      ? "La empresa no tiene dirección guardada: escríbela debajo."
                      : "Elige la empresa y se usará su dirección; o escríbela debajo."}
                  </p>
                ) : null}
                {/* Dropshipping: el albarán FACTUSOL que crea BoHub (y su PDF)
                    va a este nombre + la dirección de envío de abajo; la
                    factura sale siempre a los datos fiscales de la empresa. */}
                <label className="field">
                  <span>Nombre de envío (si no es la empresa)</span>
                  <input type="text" value={shippingName}
                         aria-label="Nombre de envío"
                         placeholder="Destinatario del envío (dropshipping)"
                         maxLength={120}
                         title="Destinatario que sale en el albarán cuando el envío no va a la empresa cliente (dropshipping). En blanco: la empresa. La factura va siempre a la empresa."
                         onChange={(e) => setShippingName(e.target.value)} />
                </label>
                <AddressFields legend="envío" value={shipping}
                               onChange={(a) => {
                                 setShipping(a);
                                 // Ya no es la de la empresa: el selector lo dice.
                                 if (shippingModeRef.current === "company") setShippingMode("other");
                               }} />
                {shippingName.trim() ? (
                  <p className="muted small" role="status">
                    El albarán irá a <strong>{shippingName.trim()}</strong> con esta
                    dirección de envío; la factura, a la empresa.
                  </p>
                ) : null}
              </>
            ) : null}

            {facPreview && !facPreview.already_imported ? (
              /* Fase 2: al crear el pedido se crea su albarán en FACTUSOL (sin
                 factura) y se confirma el pago: sin pago, o pagado (el cobro se
                 registra a mano cuando exista la factura). */
              <div className="erp-step-block">
                <h4 className="erp-step-subtitle">Pago</h4>
                <p className="muted small">
                  Al crear el pedido se creará su <strong>albarán en FACTUSOL</strong>
                  {" "}(sin factura). Confirma el pago:
                </p>
                <PaymentStep value={payment} onChange={setPayment} />
              </div>
            ) : null}

            {/* Facturación y notas: casi siempre «la de envío» y sin notas,
                así que van plegadas — pero el resumen dice si hay algo. */}
            <details className="erp-step-more" open={moreOpen}
                     onToggle={(e) => setMoreOpen(e.currentTarget.open)}>
              <summary>
                Más: facturación y notas
                {moreSummary ? <span className="muted"> · {moreSummary}</span> : null}
              </summary>
              <div className="erp-step-helper-body">
                <label className="field-toggle">
                  <input type="checkbox" checked={billingSame}
                         onChange={(e) => setBillingSame(e.target.checked)} />
                  <span>Usar dirección de envío</span>
                </label>
                {!billingSame ? (
                  <AddressFields legend="facturación" value={billing} onChange={setBilling} />
                ) : null}
                <label className="field">
                  <span>Notas internas</span>
                  <textarea rows={3} value={notes} aria-label="Notas internas"
                            onChange={(e) => setNotes(e.target.value)} />
                </label>
              </div>
            </details>
          </section>
        </div>

        {/* ---- Total siempre visible + acción principal --------------------
            Panel lateral pegajoso en escritorio; en móvil el panel queda al
            final y la barra (PrimaryActionBar) se pega abajo con el total. */}
        <div className="erp-new-order-side">
          <section className="erp-card erp-total-panel" aria-labelledby="erp-total-title">
            <h3 id="erp-total-title" className="erp-step-title">Total del pedido</h3>
            <dl className="erp-total-rows">
              <div>
                <dt>Artículos</dt>
                <dd className="num">{eur(articlesBase)}</dd>
              </div>
              <div>
                <dt>Portes</dt>
                <dd className="num">{eur(portesAmount)}</dd>
              </div>
              <div>
                <dt>{ivaLabel}</dt>
                <dd className="num">{eur(ivaAmount)}</dd>
              </div>
              <div className="is-total">
                <dt>Total</dt>
                <dd className="num">{eur(total)}</dd>
              </div>
            </dl>
            <p className="muted small erp-total-note">{regimeNote}</p>
          </section>

          <PrimaryActionBar className="erp-new-order-actions" label="Crear pedido"
                            hint={barHint}>
            <p className="erp-new-order-bar-total">
              <span>Total (IVA incl.)</span>
              <strong className="num">{eur(total)}</strong>
            </p>
            <button type="submit" className="button"
                    disabled={!valid || submitting}
                    aria-describedby={!valid ? "erp-new-order-reason" : undefined}>
              {submitting ? "Creando…" : "Crear pedido"}
            </button>
            <Link href="/erp/orders" className="button secondary">Cancelar</Link>
            {!valid ? (
              <p id="erp-new-order-reason" className="erp-new-order-reason">
                {submitReason}
              </p>
            ) : null}
          </PrimaryActionBar>
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
