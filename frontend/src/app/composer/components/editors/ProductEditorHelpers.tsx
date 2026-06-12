"use client";

/**
 * Product editor helpers — literal ports of `ProductSelect` and
 * `ProductMini` from `bomedia-v4/app-inspector.jsx` lines 701-731.
 *
 * `ProductSelect` is the catalog dropdown with optgroups per brand.
 * `ProductMini` shows a small preview row (thumbnail + brand label +
 * product name) below the selector so the editor reads as a
 * confirmation of the current choice.
 */

import type { ComposerCatalog } from "../../lib/types";
import { Field } from "../InspectorPrimitives";

export interface ProductSelectProps {
  catalog: ComposerCatalog;
  value: string | undefined;
  onChange: (next: string) => void;
  label?: string;
}

export function ProductSelect({
  catalog,
  value,
  onChange,
  label,
}: ProductSelectProps) {
  const brands = catalog.brands.filter((b) => b.id !== "bomedia");
  return (
    <Field label={label ?? "Producto"}>
      <select
        className="select"
        value={value || ""}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="" disabled>
          — Seleccionar —
        </option>
        {brands.map((b) => (
          <optgroup key={b.id} label={b.label}>
            {catalog.products
              .filter((p) => p.brand_id === b.id && p.visible)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} — {p.price ?? ""}
                </option>
              ))}
          </optgroup>
        ))}
      </select>
    </Field>
  );
}

export interface ProductMiniProps {
  catalog: ComposerCatalog;
  productId: string | undefined;
}

export function ProductMini({ catalog, productId }: ProductMiniProps) {
  const p = catalog.products.find((x) => x.id === productId);
  if (!p) {
    return (
      <div
        style={{
          fontSize: 11,
          color: "var(--text-subtle)",
          padding: 8,
          fontStyle: "italic",
        }}
      >
        No seleccionado
      </div>
    );
  }
  const brand = catalog.brands.find((b) => b.id === p.brand_id);
  return (
    <div
      style={{
        display: "flex",
        gap: 8,
        alignItems: "center",
        padding: "6px 0",
      }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={p.img}
        alt=""
        style={{
          width: 36,
          height: 36,
          objectFit: "contain",
          borderRadius: 4,
          background: "var(--bg-sunken)",
          padding: 2,
        }}
      />
      <div style={{ minWidth: 0 }}>
        <div
          style={{
            fontSize: 11,
            fontWeight: 600,
            color: brand?.color,
          }}
        >
          {brand?.label}
        </div>
        <div style={{ fontSize: 12, fontWeight: 500 }}>{p.name}</div>
      </div>
    </div>
  );
}
