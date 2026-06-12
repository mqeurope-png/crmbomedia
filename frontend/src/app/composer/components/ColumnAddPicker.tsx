"use client";

/**
 * ColumnAddPicker — distilled port of the same-named function in
 * `bomedia-v4/app-compositor.jsx` (line 1324).
 *
 * Opens a dropdown above the column's "+ Añadir a columna X" button.
 * The dropdown is a two-level menu: first picks the kind (text /
 * product / image / cta / divider variants); products and prewritten
 * texts expand to a sub-list of catalog rows.
 *
 * The original supports video standalones + saved CTAs via the
 * Backoffice — those depend on infrastructure not yet wired in the
 * CRM Composer port, so this version covers text / product / image /
 * cta / divider-{line,short,dots}. Adding video + saved CTA expands
 * the same switch when Fase 3 ports the standalone block editor.
 */

import { useEffect, useRef, useState } from "react";

import type { AddBlockSpec, ComposerCatalog } from "../lib/types";
import { Icon } from "./Icon";

type Mode = null | "text" | "product" | "image" | "cta" | "divider";

export interface ColumnAddPickerProps {
  catalog: ComposerCatalog;
  columnLabel: string;
  onPick: (spec: AddBlockSpec) => void;
}

export function ColumnAddPicker({
  catalog,
  columnLabel,
  onPick,
}: ColumnAddPickerProps) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<Mode>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
        setMode(null);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const close = () => {
    setOpen(false);
    setMode(null);
  };
  const pick = (spec: AddBlockSpec) => {
    onPick(spec);
    close();
  };

  const popoverStyle: React.CSSProperties = {
    position: "absolute",
    top: "100%",
    left: 0,
    marginTop: 4,
    background: "var(--bg-panel)",
    border: "1px solid var(--border-strong)",
    borderRadius: "var(--r-sm)",
    boxShadow: "0 10px 30px rgba(0,0,0,0.18)",
    zIndex: 50,
    padding: 6,
    maxHeight: 320,
    overflowY: "auto",
    minWidth: 220,
  };

  const products = catalog.products.filter((p) => p.visible);
  const prewritten = catalog.prewritten_texts.filter((t) => t.visible);

  return (
    <div
      ref={wrapRef}
      onClick={(e) => e.stopPropagation()}
      style={{ position: "relative" }}
    >
      <button
        type="button"
        className="btn btn-ghost"
        style={{
          fontSize: 11,
          justifyContent: "center",
          border: "1px dashed var(--border-strong)",
          background: "var(--bg-panel)",
          width: "100%",
          padding: "8px 10px",
        }}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
          setMode(null);
        }}
        title={`Añadir un bloque a la columna ${columnLabel}`}
      >
        <Icon name="plus" size={11} /> Añadir a columna {columnLabel}
      </button>

      {open && mode === null && (
        <div
          style={{
            ...popoverStyle,
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          <PickerOption
            icon="text"
            label="Texto"
            onClick={() => setMode("text")}
          />
          <PickerOption
            icon="box"
            label="Producto"
            onClick={() => setMode("product")}
          />
          <PickerOption
            icon="copy"
            label="Imagen"
            onClick={() => pick({ type: "image" })}
          />
          <PickerOption
            icon="zap"
            label="Botón CTA"
            onClick={() => pick({ type: "cta" })}
          />
          <div style={{ height: 1, background: "var(--border)", margin: "4px 0" }} />
          <PickerOption
            label="Línea fina"
            onClick={() => pick({ type: "divider_line" })}
            visual={
              <span
                style={{
                  width: 14,
                  height: 1,
                  background: "currentColor",
                  display: "inline-block",
                }}
              />
            }
          />
          <PickerOption
            label="Línea corta"
            onClick={() => pick({ type: "divider_short" })}
            visual={
              <span
                style={{
                  width: 8,
                  height: 2,
                  background: "currentColor",
                  borderRadius: 1,
                  display: "inline-block",
                }}
              />
            }
          />
          <PickerOption
            label="Puntos"
            onClick={() => pick({ type: "divider_dots" })}
            visual={<span style={{ letterSpacing: 2, fontSize: 14 }}>···</span>}
          />
        </div>
      )}

      {open && mode === "product" && (
        <div style={{ ...popoverStyle, minWidth: 260 }}>
          <SubHeading>Elige producto</SubHeading>
          {products.map((p) => (
            <button
              key={p.id}
              type="button"
              className="btn btn-ghost"
              style={{
                fontSize: 11,
                justifyContent: "flex-start",
                width: "100%",
                padding: "6px 8px",
                gap: 6,
              }}
              onClick={() => pick({ type: "product", productId: p.id })}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={p.img}
                alt=""
                style={{
                  width: 24,
                  height: 24,
                  objectFit: "contain",
                  marginRight: 6,
                }}
              />
              <span
                style={{
                  flex: 1,
                  textAlign: "left",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {p.name}
              </span>
              <span
                className="mono"
                style={{ fontSize: 10, color: "var(--text-muted)" }}
              >
                {p.brand_id}
              </span>
            </button>
          ))}
        </div>
      )}

      {open && mode === "text" && (
        <div style={{ ...popoverStyle, minWidth: 280 }}>
          <SubHeading>Texto</SubHeading>
          <button
            type="button"
            className="btn btn-ghost"
            style={{
              fontSize: 11,
              justifyContent: "flex-start",
              width: "100%",
              padding: "6px 8px",
              borderBottom: "1px solid var(--border)",
              gap: 6,
            }}
            onClick={() => pick({ type: "text-blank" })}
          >
            <Icon name="plus" size={11} />
            <strong>Texto en blanco</strong>
          </button>
          <SubHeading style={{ marginTop: 4 }}>Pre-escritos</SubHeading>
          {prewritten.map((t) => (
            <button
              key={t.id}
              type="button"
              className="btn btn-ghost"
              style={{
                fontSize: 11,
                justifyContent: "flex-start",
                width: "100%",
                padding: "6px 8px",
                gap: 6,
              }}
              onClick={() => pick({ type: "text", textId: t.id })}
            >
              <span style={{ marginRight: 6, fontSize: 14 }}>{t.icon || "📝"}</span>
              <span
                style={{
                  flex: 1,
                  textAlign: "left",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {t.name}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function PickerOption({
  icon,
  label,
  visual,
  onClick,
}: {
  icon?: string;
  label: string;
  visual?: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="btn btn-ghost"
      style={{
        fontSize: 12,
        justifyContent: "flex-start",
        padding: "8px 10px",
        gap: 8,
      }}
      onClick={onClick}
    >
      {visual ?? (icon ? <Icon name={icon} size={14} /> : null)}
      {label}
    </button>
  );
}

function SubHeading({
  children,
  style,
}: {
  children: React.ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <div
      style={{
        padding: "4px 6px",
        fontSize: 10,
        color: "var(--text-muted)",
        fontWeight: 600,
        textTransform: "uppercase",
        ...style,
      }}
    >
      {children}
    </div>
  );
}
