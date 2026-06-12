"use client";

/**
 * Sidebar — literal port of `function Sidebar` from
 * `bomedia-v4/app-compositor.jsx` (lines 79-547).
 *
 * Same JSX shape, same class names, same data flow. Adapted for
 * the CRM:
 *   - reads catalog from `useCatalog()` instead of `window.PRODUCTS`
 *     / `window.PREWRITTEN_TEXTS` etc. (the original Composer
 *     publishes its catalog onto `window`; here the catalog comes
 *     from the typed `/api/composer/catalog` endpoint).
 *   - `isHiddenForUser` is dropped — the backend filters those
 *     server-side via `composer_user_hidden_items`.
 *   - `addBlock` is the store action (zustand) instead of the v5o
 *     `onAddBlock` prop. The drag→drop flow still works via
 *     `useDraggable` from `@dnd-kit/core` for items that the user
 *     drags onto the canvas; click also adds them.
 *
 * NO renamed CSS classes. NO refactor. Whatever the original
 * markup says is what we emit.
 */

import { useEffect, useState } from "react";

import { useComposerStore } from "../lib/store";
import { useCatalog } from "../lib/useCatalog";
import type {
  AddBlockSpec,
  ComposerBrand,
  ComposerComposedBlock,
  ComposerPrewrittenText,
  ComposerProduct,
  ComposerStandaloneBlock,
} from "../lib/types";
import { Icon } from "./Icon";

const SIDEBAR_TAB_STORAGE = "composer-sidebar-tab";
const SIDEBAR_TYPE_STORAGE = "composer-sidebar-type";
const SIDEBAR_PRICE_STORAGE = "composer-sidebar-price";

type TabId = "library" | "templates" | "texts";
type TypeFilterId = "all" | "productos" | "compuestos" | "hero" | "video" | "steps" | "cta" | "image" | "divider";
type PriceFilterId = "all" | "low" | "mid" | "high" | "consultar";

const TYPE_FILTERS: ReadonlyArray<{ id: TypeFilterId; label: string }> = [
  { id: "all", label: "Todos" },
  { id: "productos", label: "Productos" },
  { id: "compuestos", label: "Compuestos" },
  { id: "hero", label: "Hero" },
  { id: "video", label: "Vídeo" },
  { id: "steps", label: "Pasos" },
  { id: "cta", label: "CTA" },
  { id: "image", label: "Imagen" },
  { id: "divider", label: "Divisor" },
];

const PRICE_FILTERS: ReadonlyArray<{ id: PriceFilterId; label: string }> = [
  { id: "all", label: "Todos" },
  { id: "low", label: "< 5k" },
  { id: "mid", label: "5-15k" },
  { id: "high", label: "> 15k" },
  { id: "consultar", label: "Consultar" },
];

function priceToNumber(price: string | null | undefined): number {
  if (!price) return 0;
  const cleaned = price.replace(/[^\d.,]/g, "").replace(",", ".");
  const n = parseFloat(cleaned);
  return Number.isFinite(n) ? n : 0;
}

function priceBucket(price: string | null | undefined): PriceFilterId {
  if (!price) return "consultar";
  const consultar = /consultar|on request|sur demande|auf anfrage|op aanvraag/i;
  if (consultar.test(price)) return "consultar";
  const n = priceToNumber(price);
  if (n === 0) return "consultar";
  if (n < 5000) return "low";
  if (n < 15000) return "mid";
  return "high";
}

function standaloneTypeKey(blockType: string | null | undefined): TypeFilterId {
  const t = (blockType || "").toLowerCase();
  if (t.includes("hero")) return "hero";
  if (t.includes("video") || t.includes("freebird")) return "video";
  if (t.includes("step")) return "steps";
  if (t.includes("cta")) return "cta";
  if (t.includes("image")) return "image";
  if (t.includes("divider")) return "divider";
  return "all";
}

function usePersistentString<T extends string>(
  key: string,
  initial: T,
): [T, (next: T) => void] {
  const [value, setValue] = useState<T>(initial);
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(key);
      if (stored) setValue(stored as T);
    } catch {
      /* no-op */
    }
  }, [key]);
  useEffect(() => {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      /* no-op */
    }
  }, [key, value]);
  return [value, setValue];
}

/** Catalog row that the v5o sidebar reads — bridges CRM snake_case
 * to the original's camelCase shorthand inside the JSX so the port
 * stays line-for-line. */
interface ProductLike {
  id: string;
  brand: string;
  name: string;
  area: string;
  price: string;
  badge: string;
  badgeBg: string;
  badgeColor: string;
  img: string;
}

function adaptProduct(p: ComposerProduct): ProductLike {
  return {
    id: p.id,
    brand: p.brand_id,
    name: p.name,
    area: p.area ?? "-",
    price: p.price ?? "-",
    badge: p.badge ?? "",
    badgeBg: p.badge_bg ?? "var(--bg-sunken)",
    badgeColor: p.badge_color ?? "var(--text-muted)",
    img: p.img,
  };
}

interface DraggablePaletteItemProps {
  id: string;
  label: string;
  spec: AddBlockSpec;
  onAddBlock: (spec: AddBlockSpec) => void;
  children: React.ReactNode;
}

/** Click-only palette item.
 *
 * The original `bomedia-v4` composer uses `draggable` + a click
 * handler; the drag itself is a visual affordance and the actual
 * insertion always happens via the click. We follow that — the
 * earlier dnd-kit wrapper here intercepted pointer events through
 * the page-level `DndContext` and silently killed the click in
 * some browsers when the sensor's activation distance triggered
 * before pointerup. Reverting to plain `onClick` makes the sidebar
 * behave like the original. Sortable drag inside the canvas still
 * uses dnd-kit (`useSortable` on `BlockCard`). */
function DraggablePaletteButton({
  id,
  label,
  spec,
  onAddBlock,
  children,
}: DraggablePaletteItemProps) {
  return (
    <button
      type="button"
      data-palette-id={id}
      className="lib-item"
      onClick={() => onAddBlock(spec)}
      title={label}
    >
      {children}
    </button>
  );
}

export interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
  brandFilter: string;
  setBrandFilter: (next: string) => void;
}

export function Sidebar({
  collapsed,
  onToggle,
  brandFilter,
  setBrandFilter,
}: SidebarProps) {
  const { catalog, loading, error } = useCatalog();
  const addBlock = useComposerStore((s) => s.addBlock);
  const [tab, setTab] = usePersistentString<TabId>(SIDEBAR_TAB_STORAGE, "library");
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = usePersistentString<TypeFilterId>(
    SIDEBAR_TYPE_STORAGE,
    "all",
  );
  const [priceFilter, setPriceFilter] = usePersistentString<PriceFilterId>(
    SIDEBAR_PRICE_STORAGE,
    "all",
  );

  // ────────────────────────────────────────────────────────────────
  // Collapsed rail (matches lines 85-99 of the original)
  // ────────────────────────────────────────────────────────────────
  if (collapsed) {
    return (
      <aside className="cmp-sidebar">
        <div className="sidebar-rail">
          <button
            type="button"
            className="rail-btn active"
            onClick={onToggle}
            title="Expandir"
          >
            <Icon name="sidebar" />
          </button>
          <button type="button" className="rail-btn" title="Biblioteca">
            <Icon name="layers" />
          </button>
          <button type="button" className="rail-btn" title="Plantillas">
            <Icon name="template" />
          </button>
          <button type="button" className="rail-btn" title="Productos">
            <Icon name="box" />
          </button>
          <button type="button" className="rail-btn" title="Textos">
            <Icon name="text" />
          </button>
        </div>
      </aside>
    );
  }

  // ────────────────────────────────────────────────────────────────
  // Expanded mode
  // ────────────────────────────────────────────────────────────────
  if (loading || !catalog) {
    return (
      <aside className="cmp-sidebar">
        <p style={{ padding: 16, color: "var(--text-muted)" }}>
          {error ?? "Cargando biblioteca…"}
        </p>
      </aside>
    );
  }

  const q = search.trim().toLowerCase();
  const matchesQ = (s: string | null | undefined) =>
    !q || (s ?? "").toLowerCase().includes(q);
  const matchesBrand = (b: string | null | undefined) => {
    if (brandFilter === "all") return true;
    if (brandFilter === "mix") return !b || b === "mix";
    return b === brandFilter || b === "mix" || !b;
  };

  const showProducts = typeFilter === "all" || typeFilter === "productos";
  const showCompuestos = typeFilter === "all" || typeFilter === "compuestos";
  const showStandaloneType = (sbType: string | null | undefined) => {
    if (typeFilter === "all") return true;
    return standaloneTypeKey(sbType) === typeFilter;
  };

  const BRANDS: ComposerBrand[] = catalog.brands;

  const filteredProducts: ProductLike[] = showProducts
    ? catalog.products
        .filter(
          (p) =>
            p.visible &&
            matchesBrand(p.brand_id) &&
            matchesQ(p.name) &&
            (priceFilter === "all" || priceBucket(p.price) === priceFilter),
        )
        .map(adaptProduct)
    : [];

  const filteredTexts: ComposerPrewrittenText[] = catalog.prewritten_texts.filter(
    (t) =>
      t.visible &&
      matchesBrand(t.brand_id) &&
      (matchesQ(t.name) || matchesQ(t.text)),
  );

  const filteredStandalone: ComposerStandaloneBlock[] =
    catalog.standalone_blocks.filter(
      (b) =>
        b.visible &&
        matchesBrand(b.brand_id) &&
        matchesQ(b.title) &&
        showStandaloneType(b.block_type),
    );

  const composedBrand = (c: ComposerComposedBlock): string => {
    if (c.brand_strip && c.brand_strip !== "none") return c.brand_strip;
    const firstPid = (c.products ?? [])[0];
    if (firstPid) {
      const p = catalog.products.find((x) => x.id === firstPid);
      if (p) return p.brand_id;
    }
    return "mix";
  };

  const filteredComposed: ComposerComposedBlock[] = showCompuestos
    ? catalog.composed_blocks.filter(
        (c) =>
          c.visible &&
          matchesBrand(composedBrand(c)) &&
          (matchesQ(c.title) || matchesQ(c.description)),
      )
    : [];

  const onLibraryTab = tab === "library";
  const showPriceRow =
    onLibraryTab && (typeFilter === "all" || typeFilter === "productos");

  const totalLibrary =
    filteredProducts.length + filteredComposed.length + filteredStandalone.length;
  const noResults =
    (onLibraryTab && totalLibrary === 0) ||
    (tab === "texts" && filteredTexts.length === 0);

  const resetFilters = () => {
    setSearch("");
    setBrandFilter("all");
    setTypeFilter("all");
    setPriceFilter("all");
  };

  return (
      <aside className="cmp-sidebar">
        <div className="sidebar-header">
          <span className="sidebar-title">Biblioteca</span>
          <button
            type="button"
            className="icon-btn"
            onClick={onToggle}
            title="Colapsar"
            style={{ width: 24, height: 24 }}
          >
            <Icon name="sidebar" size={14} />
          </button>
        </div>

        <div className="local-search">
          <Icon name="search" size={14} />
          <input
            placeholder="Buscar en biblioteca…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        <div className="brand-chips">
          <button
            type="button"
            className={"brand-chip" + (brandFilter === "all" ? " active" : "")}
            onClick={() => setBrandFilter("all")}
          >
            Todas
          </button>
          <button
            type="button"
            className={"brand-chip" + (brandFilter === "mix" ? " active" : "")}
            onClick={() => setBrandFilter("mix")}
            style={brandFilter === "mix" ? undefined : { color: "#94a3b8" }}
          >
            <span
              className="brand-chip-dot"
              style={{ background: "#94a3b8" }}
            />
            Multi-marca
          </button>
          {BRANDS.filter((b) => b.id !== "bomedia").map((b) => (
            <button
              key={b.id}
              type="button"
              className={"brand-chip" + (brandFilter === b.id ? " active" : "")}
              onClick={() => setBrandFilter(b.id)}
              style={brandFilter === b.id ? undefined : { color: b.color }}
            >
              <span
                className="brand-chip-dot"
                style={{ background: b.color }}
              />
              {b.label}
            </button>
          ))}
        </div>

        {onLibraryTab && (
          <>
            <div className="filter-row">
              <span className="filter-row-label">Tipo</span>
              {TYPE_FILTERS.map((f) => (
                <button
                  key={f.id}
                  type="button"
                  className={"filter-chip" + (typeFilter === f.id ? " active" : "")}
                  onClick={() => setTypeFilter(f.id)}
                >
                  {f.label}
                </button>
              ))}
            </div>
            {showPriceRow && (
              <div className="filter-row">
                <span className="filter-row-label">Precio</span>
                {PRICE_FILTERS.map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    className={
                      "filter-chip" + (priceFilter === f.id ? " active" : "")
                    }
                    onClick={() => setPriceFilter(f.id)}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
            )}
          </>
        )}

        <div className="nav-tabs">
          {(
            [
              { id: "library", label: "Bloques" },
              { id: "templates", label: "Plantillas" },
              { id: "texts", label: "Textos" },
            ] as const
          ).map((t) => (
            <button
              key={t.id}
              type="button"
              className={"nav-tab" + (tab === t.id ? " active" : "")}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="sidebar-body scroll">
          {onLibraryTab && (
            <>
              {/* Layout section — five built-in items (2col / 3col / 3
                  divider variants). Always shown. */}
              <div className="group">
                <div className="group-header">
                  Layout <span className="count mono">5</span>
                </div>
                <DraggablePaletteButton
                  id="palette-layout-2col"
                  label="2 columnas"
                  spec={{ type: "section_2col" }}
                  onAddBlock={(spec) => addBlock(spec)}
                >
                  <div className="lib-icon mix">
                    <Icon name="grid" size={14} />
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div className="lib-title">2 columnas</div>
                    <div className="lib-sub">
                      Sección con dos columnas iguales (50/50). Stack en
                      móvil.
                    </div>
                    <div className="lib-meta">
                      <span
                        className="lib-badge"
                        style={{
                          background:
                            "color-mix(in oklch, var(--accent) 12%, transparent)",
                          color: "var(--accent-ink)",
                          fontWeight: 600,
                        }}
                      >
                        layout
                      </span>
                    </div>
                  </div>
                  <span className="lib-add">
                    <Icon name="plus" size={14} />
                  </span>
                </DraggablePaletteButton>
                <DraggablePaletteButton
                  id="palette-layout-3col"
                  label="3 columnas"
                  spec={{ type: "section_3col" }}
                  onAddBlock={(spec) => addBlock(spec)}
                >
                  <div className="lib-icon mix">
                    <Icon name="grid" size={14} />
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div className="lib-title">3 columnas</div>
                    <div className="lib-sub">
                      Sección con tres columnas iguales (33/33/33). Stack
                      en móvil.
                    </div>
                    <div className="lib-meta">
                      <span
                        className="lib-badge"
                        style={{
                          background:
                            "color-mix(in oklch, var(--accent) 12%, transparent)",
                          color: "var(--accent-ink)",
                          fontWeight: 600,
                        }}
                      >
                        layout
                      </span>
                    </div>
                  </div>
                  <span className="lib-add">
                    <Icon name="plus" size={14} />
                  </span>
                </DraggablePaletteButton>
                <DraggablePaletteButton
                  id="palette-divider-line"
                  label="Línea fina"
                  spec={{ type: "divider_line" }}
                  onAddBlock={(spec) => addBlock(spec)}
                >
                  <div
                    className="lib-icon mix"
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <div style={{ width: 18, height: 1, background: "currentColor" }} />
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div className="lib-title">Línea fina</div>
                    <div className="lib-sub">
                      Línea horizontal sutil, full width. Ideal entre
                      secciones.
                    </div>
                    <div className="lib-meta">
                      <span
                        className="lib-badge"
                        style={{
                          background: "var(--bg-sunken)",
                          color: "var(--text-muted)",
                        }}
                      >
                        divisor
                      </span>
                    </div>
                  </div>
                  <span className="lib-add">
                    <Icon name="plus" size={14} />
                  </span>
                </DraggablePaletteButton>
                <DraggablePaletteButton
                  id="palette-divider-short"
                  label="Línea corta centrada"
                  spec={{ type: "divider_short" }}
                  onAddBlock={(spec) => addBlock(spec)}
                >
                  <div
                    className="lib-icon mix"
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <div
                      style={{
                        width: 10,
                        height: 2,
                        background: "currentColor",
                        borderRadius: 1,
                      }}
                    />
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div className="lib-title">Línea corta centrada</div>
                    <div className="lib-sub">
                      Línea corta de 80px centrada. Separador elegante de
                      capítulos.
                    </div>
                    <div className="lib-meta">
                      <span
                        className="lib-badge"
                        style={{
                          background: "var(--bg-sunken)",
                          color: "var(--text-muted)",
                        }}
                      >
                        divisor
                      </span>
                    </div>
                  </div>
                  <span className="lib-add">
                    <Icon name="plus" size={14} />
                  </span>
                </DraggablePaletteButton>
                <DraggablePaletteButton
                  id="palette-divider-dots"
                  label="Puntos ornamentales"
                  spec={{ type: "divider_dots" }}
                  onAddBlock={(spec) => addBlock(spec)}
                >
                  <div
                    className="lib-icon mix"
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      letterSpacing: 2,
                      fontSize: 14,
                    }}
                  >
                    ···
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div className="lib-title">Puntos ornamentales</div>
                    <div className="lib-sub">
                      Tres puntos centrados con espaciado. Separador
                      refinado.
                    </div>
                    <div className="lib-meta">
                      <span
                        className="lib-badge"
                        style={{
                          background: "var(--bg-sunken)",
                          color: "var(--text-muted)",
                        }}
                      >
                        divisor
                      </span>
                    </div>
                  </div>
                  <span className="lib-add">
                    <Icon name="plus" size={14} />
                  </span>
                </DraggablePaletteButton>
              </div>

              {filteredProducts.length > 0 && (
                <div className="group">
                  <div className="group-header">
                    Productos{" "}
                    <span className="count mono">{filteredProducts.length}</span>
                  </div>
                  {filteredProducts.map((p) => {
                    const brand = BRANDS.find((b) => b.id === p.brand);
                    return (
                      <DraggablePaletteButton
                        key={p.id}
                        id={`palette-product-${p.id}`}
                        label={p.name}
                        spec={{
                          type: "product_single",
                          params: { product1: p.id },
                        }}
                        onAddBlock={(spec) => addBlock(spec)}
                      >
                        <div className={`lib-icon ${p.brand}`}>
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            src={p.img}
                            alt=""
                            style={{
                              width: 28,
                              height: 28,
                              objectFit: "contain",
                            }}
                          />
                        </div>
                        <div style={{ minWidth: 0 }}>
                          <div className="lib-title">{p.name}</div>
                          <div className="lib-sub">
                            {p.area} ·{" "}
                            <span className="mono">{p.price}</span>
                          </div>
                          <div className="lib-meta">
                            {brand && (
                              <span
                                className="lib-brand-tag"
                                style={{ color: brand.color }}
                              >
                                {brand.label}
                              </span>
                            )}
                            {p.badge && (
                              <span
                                className="lib-badge"
                                style={{
                                  background: p.badgeBg,
                                  color: p.badgeColor,
                                }}
                              >
                                {p.badge}
                              </span>
                            )}
                          </div>
                        </div>
                        <span className="lib-add">
                          <Icon name="plus" size={14} />
                        </span>
                      </DraggablePaletteButton>
                    );
                  })}
                </div>
              )}

              {filteredComposed.length > 0 && (
                <div className="group">
                  <div className="group-header">
                    Compuestos{" "}
                    <span className="count mono">{filteredComposed.length}</span>
                  </div>
                  {filteredComposed.map((c) => {
                    const brandId = composedBrand(c);
                    const brand = BRANDS.find((b) => b.id === brandId);
                    return (
                      <DraggablePaletteButton
                        key={c.id}
                        id={`palette-composed-${c.id}`}
                        label={c.title}
                        spec={{
                          type: "composed",
                          params: {
                            _sourceType: "composed",
                            _sourceId: c.id,
                          },
                        }}
                        onAddBlock={(spec) => addBlock(spec)}
                      >
                        <div className={`lib-icon ${brandId || "mix"}`}>
                          <Icon name="layers" size={14} />
                        </div>
                        <div style={{ minWidth: 0 }}>
                          <div className="lib-title">
                            {c.color_tag && (
                              <span
                                className={`lib-color-tag ${c.color_tag}`}
                              />
                            )}
                            {c.title}
                          </div>
                          <div className="lib-sub">{c.description}</div>
                          <div className="lib-meta">
                            {brand && (
                              <span
                                className="lib-brand-tag"
                                style={{ color: brand.color }}
                              >
                                {brand.label}
                              </span>
                            )}
                            {c.price_range && c.price_range !== "-" && (
                              <span
                                className="lib-badge"
                                style={{
                                  background: "var(--bg-sunken)",
                                  color: "var(--text-muted)",
                                  fontFamily: "var(--font-mono)",
                                }}
                              >
                                {c.price_range}
                              </span>
                            )}
                          </div>
                        </div>
                        <span className="lib-add">
                          <Icon name="plus" size={14} />
                        </span>
                      </DraggablePaletteButton>
                    );
                  })}
                </div>
              )}

              {filteredStandalone.length > 0 && (
                <div className="group">
                  <div className="group-header">
                    Composiciones{" "}
                    <span className="count mono">{filteredStandalone.length}</span>
                  </div>
                  {filteredStandalone.map((b) => {
                    const brand = BRANDS.find((x) => x.id === b.brand_id);
                    const type = standaloneSpecType(b.block_type);
                    return (
                      <DraggablePaletteButton
                        key={b.id}
                        id={`palette-standalone-${b.id}`}
                        label={b.title}
                        spec={{
                          type,
                          params: {
                            _sourceType: "standalone",
                            _sourceId: b.id,
                          },
                        }}
                        onAddBlock={(spec) => addBlock(spec)}
                      >
                        <div className={`lib-icon ${b.brand_id ?? "mix"}`}>
                          {b.icon ?? "•"}
                        </div>
                        <div style={{ minWidth: 0 }}>
                          <div className="lib-title">{b.title}</div>
                          <div className="lib-sub serif">{b.section}</div>
                          <div className="lib-meta">
                            {brand && b.brand_id !== "mix" && (
                              <span
                                className="lib-brand-tag"
                                style={{ color: brand.color }}
                              >
                                {brand.label}
                              </span>
                            )}
                          </div>
                        </div>
                        <span className="lib-add">
                          <Icon name="plus" size={14} />
                        </span>
                      </DraggablePaletteButton>
                    );
                  })}
                </div>
              )}
            </>
          )}

          {tab === "templates" && (
            <TemplatesGroup search={q} brandFilter={brandFilter} />
          )}

          {tab === "texts" && (
            <div className="group">
              <div className="group-header">
                Texto{" "}
                <span className="count mono">{filteredTexts.length + 1}</span>
              </div>
              <DraggablePaletteButton
                id="palette-text-blank"
                label="Texto en blanco"
                spec={{ type: "text", params: { text: "" } }}
                onAddBlock={(spec) => addBlock(spec)}
              >
                <div className="lib-icon mix">
                  <Icon name="text" size={14} />
                </div>
                <div style={{ minWidth: 0 }}>
                  <div className="lib-title">Texto en blanco</div>
                  <div className="lib-sub">
                    Bloque vacío para escribir desde cero
                  </div>
                  <div className="lib-meta">
                    <span
                      className="lib-badge"
                      style={{
                        background:
                          "color-mix(in oklch, var(--accent) 12%, transparent)",
                        color: "var(--accent-ink)",
                        fontWeight: 600,
                      }}
                    >
                      nuevo
                    </span>
                  </div>
                </div>
                <span className="lib-add">
                  <Icon name="plus" size={14} />
                </span>
              </DraggablePaletteButton>
              {filteredTexts.map((t) => {
                const brand = BRANDS.find((b) => b.id === t.brand_id);
                return (
                  <DraggablePaletteButton
                    key={t.id}
                    id={`palette-text-${t.id}`}
                    label={t.name}
                    spec={{
                      type: "text",
                      params: {
                        _sourceType: "prewritten",
                        _sourceId: t.id,
                        text: t.text,
                      },
                    }}
                    onAddBlock={(spec) => addBlock(spec)}
                  >
                    <div className={`lib-icon ${t.brand_id ?? "mix"}`}>
                      {t.icon ?? "•"}
                    </div>
                    <div style={{ minWidth: 0 }}>
                      <div className="lib-title">{t.name}</div>
                      <div className="lib-sub">
                        {(t.text ?? "").slice(0, 60)}…
                      </div>
                      <div className="lib-meta">
                        {brand && t.brand_id !== "mix" && (
                          <span
                            className="lib-brand-tag"
                            style={{ color: brand.color }}
                          >
                            {brand.label}
                          </span>
                        )}
                      </div>
                    </div>
                    <span className="lib-add">
                      <Icon name="plus" size={14} />
                    </span>
                  </DraggablePaletteButton>
                );
              })}
            </div>
          )}

          {noResults && (
            <div
              style={{
                padding: "24px 16px",
                textAlign: "center",
                color: "var(--text-muted)",
                fontSize: 12,
              }}
            >
              <div className="serif" style={{ fontSize: 14, marginBottom: 6 }}>
                Sin resultados
              </div>
              <div style={{ fontSize: 11, marginBottom: 10 }}>
                Prueba a quitar algún filtro o cambiar el término.
              </div>
              <button
                type="button"
                className="btn btn-ghost"
                style={{ fontSize: 11 }}
                onClick={resetFilters}
              >
                <Icon name="x" size={11} /> Limpiar filtros
              </button>
            </div>
          )}
        </div>
      </aside>
  );
}

function standaloneSpecType(blockType: string): AddBlockSpec["type"] {
  switch (blockType) {
    case "pimpam_hero":
      return "pimpam_hero";
    case "pimpam_steps":
      return "pimpam_steps";
    case "freebird":
      return "freebird";
    case "brand_strip":
      return "brand_strip";
    case "cta":
      return "cta";
    case "image":
      return "image";
    case "video":
      return "video";
    default:
      return "cta";
  }
}

/** Templates list — pulls live from `/api/composer/templates` once
 * (still through the existing list endpoint; cached via useState
 * since the sidebar only mounts once per session). */
function TemplatesGroup({
  search,
  brandFilter,
}: {
  search: string;
  brandFilter: string;
}) {
  const [templates, setTemplates] = useState<
    Array<{
      id: string;
      name: string;
      description: string | null;
      brand_id: string | null;
      color_class: string | null;
      blocks: string[];
      compositor_blocks: unknown[] | null;
    }>
  >([]);
  useEffect(() => {
    void import("../lib/composerApi").then(({ listTemplates }) => {
      void listTemplates().then((rows) =>
        setTemplates(
          rows.map((r) => ({
            id: r.id,
            name: r.name,
            description: r.description,
            brand_id: r.brand_id,
            color_class: r.color_class,
            blocks: r.blocks,
            compositor_blocks: r.compositor_blocks,
          })),
        ),
      );
    });
  }, []);
  const { catalog } = useCatalog();
  const BRANDS: ComposerBrand[] = catalog?.brands ?? [];

  const filtered = templates.filter(
    (t) =>
      (brandFilter === "all" ||
        (brandFilter === "mix" ? !t.brand_id : t.brand_id === brandFilter)) &&
      (!search ||
        t.name.toLowerCase().includes(search) ||
        (t.description ?? "").toLowerCase().includes(search)),
  );

  return (
    <div className="group">
      <div className="group-header">
        Plantillas <span className="count mono">{filtered.length}</span>
      </div>
      {filtered.map((t) => {
        const brand = BRANDS.find((b) => b.id === t.brand_id);
        const count = t.compositor_blocks?.length ?? t.blocks.length;
        return (
          <button
            key={t.id}
            type="button"
            className="lib-item"
            onClick={() => {
              // Template loading is wired in 2.2 via the
              // `loadTemplate` action; for now we just emit an
              // event the canvas listens for.
              window.dispatchEvent(
                new CustomEvent("composer:load-template", {
                  detail: { templateId: t.id },
                }),
              );
            }}
          >
            <div className={`lib-icon ${t.brand_id ?? "mix"}`}>
              <Icon name="template" size={14} />
            </div>
            <div style={{ minWidth: 0 }}>
              <div className="lib-title">
                {t.color_class && (
                  <span className={`lib-color-tag ${t.color_class}`} />
                )}
                {t.name}
              </div>
              <div className="lib-sub">{t.description}</div>
              <div className="lib-meta">
                {brand && (
                  <span
                    className="lib-brand-tag"
                    style={{ color: brand.color }}
                  >
                    {brand.label}
                  </span>
                )}
                <span
                  className="lib-badge"
                  style={{
                    background: "var(--bg-sunken)",
                    color: "var(--text-muted)",
                  }}
                >
                  {count} bloques
                </span>
              </div>
            </div>
            <span className="lib-add">
              <Icon name="plus" size={14} />
            </span>
          </button>
        );
      })}
    </div>
  );
}
