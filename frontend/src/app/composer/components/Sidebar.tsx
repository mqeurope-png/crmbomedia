"use client";

/**
 * Composer Sidebar — palette of catalog items the user can drag onto
 * the canvas.
 *
 * Distilled port of `Sidebar` from `bomedia-v4/app-compositor.jsx`.
 * The original ships with five tabs (Bloques / Plantillas / Textos /
 * Productos), brand chip filter, type chip filter, price chip filter,
 * `usePersistentState` for tab/type/price selection, and the
 * `isHiddenForUser` per-user hide mechanism. This Fase-2.1 port covers:
 *
 *   - search box
 *   - brand chip row (derived from the catalog brand list)
 *   - one collapsible section per catalog collection: products,
 *     prewritten texts, composed blocks, standalone blocks
 *   - dnd-kit `useDraggable` on every item so the canvas can pick it
 *     up. The drag data carries `{kind:"palette", spec:{type, params}}`
 *     so the canvas's drop handler knows to call `addBlock`.
 *
 * Type / price filters and the templates tab port in Fase 2.2 along
 * with the inspector + preview surface.
 */

import { useDraggable } from "@dnd-kit/core";
import { Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useCatalog } from "../lib/useCatalog";
import type {
  AddBlockSpec,
  ComposerBrand,
  ComposerComposedBlock,
  ComposerPrewrittenText,
  ComposerProduct,
  ComposerStandaloneBlock,
} from "../lib/types";

const BRAND_FILTER_STORAGE = "composer-sidebar-brand";

interface DraggableSpec {
  kind: "palette";
  itemId: string;
  spec: AddBlockSpec;
  label: string;
}

interface DraggableItemProps {
  id: string;
  payload: DraggableSpec;
  children: React.ReactNode;
}

function DraggableItem({ id, payload, children }: DraggableItemProps) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id,
    data: payload,
  });
  return (
    <div
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      className={`palette-item${isDragging ? " is-dragging" : ""}`}
      title={payload.label}
    >
      {children}
    </div>
  );
}

export function ComposerSidebar() {
  const { catalog, loading, error } = useCatalog();
  const [search, setSearch] = useState("");
  const [brandFilter, setBrandFilter] = useState<string>("all");

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(BRAND_FILTER_STORAGE);
      if (stored) setBrandFilter(stored);
    } catch {
      /* no-op */
    }
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(BRAND_FILTER_STORAGE, brandFilter);
    } catch {
      /* no-op */
    }
  }, [brandFilter]);

  const q = search.trim().toLowerCase();
  const matchesQ = (s: string | null | undefined) =>
    !q || (s ?? "").toLowerCase().includes(q);
  const matchesBrand = (brandId: string | null | undefined) => {
    if (brandFilter === "all") return true;
    if (brandFilter === "mix") return !brandId || brandId === "mix";
    return brandId === brandFilter || brandId === "mix" || !brandId;
  };

  const brands: ComposerBrand[] = useMemo(
    () =>
      (catalog?.brands ?? [])
        .filter((b) => b.visible && b.id !== "bomedia")
        .sort((a, b) => a.sort_order - b.sort_order),
    [catalog],
  );

  const products: ComposerProduct[] = useMemo(
    () =>
      (catalog?.products ?? []).filter(
        (p) => p.visible && matchesBrand(p.brand_id) && matchesQ(p.name),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [catalog, brandFilter, q],
  );

  const texts: ComposerPrewrittenText[] = useMemo(
    () =>
      (catalog?.prewritten_texts ?? []).filter(
        (t) =>
          t.visible &&
          matchesBrand(t.brand_id) &&
          (matchesQ(t.name) || matchesQ(t.text)),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [catalog, brandFilter, q],
  );

  const composed: ComposerComposedBlock[] = useMemo(
    () =>
      (catalog?.composed_blocks ?? []).filter(
        (c) => c.visible && (matchesQ(c.title) || matchesQ(c.description)),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [catalog, brandFilter, q],
  );

  const standalone: ComposerStandaloneBlock[] = useMemo(
    () =>
      (catalog?.standalone_blocks ?? []).filter(
        (s) => s.visible && matchesBrand(s.brand_id) && matchesQ(s.title),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [catalog, brandFilter, q],
  );

  if (error) {
    return (
      <aside className="sidebar">
        <p className="composer-placeholder" role="alert">
          {error}
        </p>
      </aside>
    );
  }

  if (loading || !catalog) {
    return (
      <aside className="sidebar">
        <p>Cargando biblioteca…</p>
      </aside>
    );
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <span className="sidebar-title">Biblioteca</span>
      </div>

      <div className="local-search">
        <Search size={14} aria-hidden />
        <input
          placeholder="Buscar en biblioteca…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div className="brand-chips">
        <button
          type="button"
          className={`brand-chip${brandFilter === "all" ? " active" : ""}`}
          onClick={() => setBrandFilter("all")}
        >
          Todas
        </button>
        <button
          type="button"
          className={`brand-chip${brandFilter === "mix" ? " active" : ""}`}
          onClick={() => setBrandFilter("mix")}
          style={brandFilter === "mix" ? undefined : { color: "#94a3b8" }}
        >
          <span className="brand-chip-dot" style={{ background: "#94a3b8" }} />
          Multi-marca
        </button>
        {brands.map((b) => (
          <button
            key={b.id}
            type="button"
            className={`brand-chip${brandFilter === b.id ? " active" : ""}`}
            onClick={() => setBrandFilter(b.id)}
            style={brandFilter === b.id ? undefined : { color: b.color }}
          >
            <span className="brand-chip-dot" style={{ background: b.color }} />
            {b.label}
          </button>
        ))}
      </div>

      <section className="sidebar-section">
        <h4 className="sidebar-section-title">Productos ({products.length})</h4>
        <div className="palette-grid">
          {products.map((p) => (
            <DraggableItem
              key={`product-${p.id}`}
              id={`palette-product-${p.id}`}
              payload={{
                kind: "palette",
                itemId: p.id,
                label: p.name,
                spec: {
                  type: "product_single",
                  params: { product1: p.id },
                },
              }}
            >
              <img src={p.img} alt={p.name} className="palette-thumb" />
              <span className="palette-label">{p.name}</span>
            </DraggableItem>
          ))}
        </div>
      </section>

      <section className="sidebar-section">
        <h4 className="sidebar-section-title">Textos ({texts.length})</h4>
        <div className="palette-list">
          {texts.map((t) => (
            <DraggableItem
              key={`text-${t.id}`}
              id={`palette-text-${t.id}`}
              payload={{
                kind: "palette",
                itemId: t.id,
                label: t.name,
                spec: {
                  type: "text",
                  params: {
                    _sourceType: "prewritten",
                    _sourceId: t.id,
                    text: t.text,
                  },
                },
              }}
            >
              {t.icon ? <span aria-hidden>{t.icon}</span> : null}
              <span className="palette-label">{t.name}</span>
            </DraggableItem>
          ))}
        </div>
      </section>

      <section className="sidebar-section">
        <h4 className="sidebar-section-title">Compuestos ({composed.length})</h4>
        <div className="palette-list">
          {composed.map((c) => (
            <DraggableItem
              key={`composed-${c.id}`}
              id={`palette-composed-${c.id}`}
              payload={{
                kind: "palette",
                itemId: c.id,
                label: c.title,
                spec: {
                  type: "composed",
                  params: { _sourceType: "composed", _sourceId: c.id },
                },
              }}
            >
              <span className="palette-label">{c.title}</span>
            </DraggableItem>
          ))}
        </div>
      </section>

      <section className="sidebar-section">
        <h4 className="sidebar-section-title">Bloques ({standalone.length})</h4>
        <div className="palette-list">
          {standalone.map((s) => {
            const type = (
              s.block_type === "pimpam_hero"
                ? "pimpam_hero"
                : s.block_type === "pimpam_steps"
                  ? "pimpam_steps"
                  : s.block_type === "freebird"
                    ? "freebird"
                    : s.block_type === "brand_strip"
                      ? "brand_strip"
                      : "cta"
            ) as AddBlockSpec["type"];
            return (
              <DraggableItem
                key={`standalone-${s.id}`}
                id={`palette-standalone-${s.id}`}
                payload={{
                  kind: "palette",
                  itemId: s.id,
                  label: s.title,
                  spec: {
                    type,
                    params: {
                      _sourceType: "standalone",
                      _sourceId: s.id,
                    },
                  },
                }}
              >
                {s.icon ? <span aria-hidden>{s.icon}</span> : null}
                <span className="palette-label">{s.title}</span>
              </DraggableItem>
            );
          })}
        </div>
      </section>
    </aside>
  );
}
