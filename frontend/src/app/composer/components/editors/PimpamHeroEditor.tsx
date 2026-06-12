"use client";

/**
 * PimpamHeroEditor — literal port of `function PimpamHeroEditor`
 * from `bomedia-v4/app-inspector.jsx` lines 780-932.
 *
 * Reads block fields with a 3-step fallback (the original `val` /
 * `valArr` helpers):
 *   1. block[key] — user-edited value
 *   2. standalone source `config.i18n[lang]` — translation override
 *   3. standalone source `config` — default
 *
 * Edits land on `block.*` via `onUpdate(id, {...block, [key]: v})` —
 * the original does NOT have per-language tabs in this editor.
 * Language switching is the TopBar lang pill; `val()` reading the
 * `lang` prop is what makes the form react to it.
 *
 * CTA fallback chain (matches v5o bug-fix Apr 2026): if
 * `heroCtaButtons` is empty on block + i18n + config, try the legacy
 * `heroCtaText` + `heroCtaUrl` pair from each level and synthesise a
 * single CTA. Without this, hero blocks defaulted from standalones
 * showed no CTA buttons in the editor.
 */

import { useComposerStore } from "../../lib/store";
import type {
  Block,
  ComposerCatalog,
  HeroCtaButton,
  Lang,
} from "../../lib/types";
import { Icon } from "../Icon";
import { Field, Section } from "../InspectorPrimitives";

export interface PimpamHeroEditorProps {
  block: Block;
  lang: Lang;
  catalog: ComposerCatalog;
}

interface LegacyCtaSource {
  heroCtaText?: string;
  heroCtaUrl?: string;
  heroCtaColor?: string;
}

function legacyCta(src: LegacyCtaSource | null): HeroCtaButton[] | null {
  if (!src) return null;
  const t = src.heroCtaText;
  const u = src.heroCtaUrl;
  if (t && u) {
    return [
      {
        text: t,
        url: u,
        bg: src.heroCtaColor || "#ea580c",
        color: "#ffffff",
      },
    ];
  }
  return null;
}

export function PimpamHeroEditor({
  block,
  lang,
  catalog,
}: PimpamHeroEditorProps) {
  const updateBlock = useComposerStore((s) => s.updateBlock);

  const sbSource = (() => {
    const id = block._sourceId || block.standaloneId;
    if (!id) return null;
    return catalog.standalone_blocks.find((s) => s.id === id) ?? null;
  })();
  const sbConf = (sbSource?.config ?? {}) as Record<string, unknown>;
  const sbI18n = (() => {
    const blob = sbConf.i18n as Record<string, Record<string, unknown>> | undefined;
    if (!blob || !lang) return {};
    return (blob[lang] ?? {}) as Record<string, unknown>;
  })();

  const val = (key: string): string => {
    const v = (block as unknown as Record<string, unknown>)[key];
    if (v !== undefined && v !== null && v !== "") return String(v);
    if (sbI18n[key] !== undefined && sbI18n[key] !== null && sbI18n[key] !== "") {
      return String(sbI18n[key]);
    }
    return (sbConf[key] as string) || "";
  };
  const valArr = <T,>(key: string): T[] => {
    const v = (block as unknown as Record<string, unknown>)[key];
    if (Array.isArray(v) && v.length) return v as T[];
    if (Array.isArray(sbI18n[key]) && (sbI18n[key] as unknown[]).length > 0) {
      return sbI18n[key] as T[];
    }
    return Array.isArray(sbConf[key]) ? (sbConf[key] as T[]) : [];
  };
  const set = (key: string, v: unknown) =>
    updateBlock(block.id, { [key]: v } as Partial<Block>);

  const bullets = valArr<string>("heroBullets");
  const ctaButtons: HeroCtaButton[] = (() => {
    if (Array.isArray(block.heroCtaButtons) && block.heroCtaButtons.length > 0) {
      return block.heroCtaButtons;
    }
    if (Array.isArray(sbI18n.heroCtaButtons) && (sbI18n.heroCtaButtons as unknown[]).length > 0) {
      return sbI18n.heroCtaButtons as HeroCtaButton[];
    }
    const legacyBlock = legacyCta(block as LegacyCtaSource);
    if (legacyBlock) return legacyBlock;
    const legacyI18n = legacyCta(sbI18n as LegacyCtaSource);
    if (legacyI18n) return legacyI18n;
    if (Array.isArray(sbConf.heroCtaButtons) && (sbConf.heroCtaButtons as unknown[]).length > 0) {
      return sbConf.heroCtaButtons as HeroCtaButton[];
    }
    const legacyConf = legacyCta(sbConf as LegacyCtaSource);
    if (legacyConf) return legacyConf;
    return [];
  })();

  return (
    <>
      <Section title="Hero — contenido">
        <Field label="Título">
          <input
            className="input"
            value={val("heroTitle")}
            onChange={(e) => set("heroTitle", e.target.value)}
          />
        </Field>
        <Field label="Subtítulo">
          <textarea
            className="textarea"
            rows={3}
            value={val("heroSubtitle")}
            onChange={(e) => set("heroSubtitle", e.target.value)}
          />
        </Field>
        <Field label="Imagen del hero">
          <input
            className="input mono"
            style={{ fontSize: 11 }}
            value={val("heroImage")}
            onChange={(e) => set("heroImage", e.target.value)}
            placeholder="https://…"
          />
        </Field>
        {val("heroImage") && (
          <div
            style={{
              marginTop: 6,
              borderRadius: "var(--r-sm)",
              overflow: "hidden",
              maxHeight: 120,
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={val("heroImage")}
              alt=""
              style={{ width: "100%", objectFit: "cover" }}
            />
          </div>
        )}
        <Field label="Enlace de imagen">
          <input
            className="input mono"
            style={{ fontSize: 11 }}
            placeholder="https://…"
            value={val("heroImageLink")}
            onChange={(e) => set("heroImageLink", e.target.value)}
          />
        </Field>
      </Section>

      <Section title="Bullets">
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {bullets.map((b, i) => (
            <div
              key={i}
              style={{ display: "flex", gap: 4, alignItems: "center" }}
            >
              <span
                style={{
                  fontSize: 11,
                  color: "var(--text-subtle)",
                  width: 16,
                  textAlign: "center",
                }}
              >
                ✓
              </span>
              <input
                className="input"
                style={{ flex: 1, fontSize: 12 }}
                value={b}
                onChange={(e) => {
                  const next = [...bullets];
                  next[i] = e.target.value;
                  set("heroBullets", next);
                }}
              />
              <button
                type="button"
                className="icon-btn"
                style={{ width: 20, height: 20 }}
                onClick={() =>
                  set("heroBullets", bullets.filter((_, j) => j !== i))
                }
                title="Borrar bullet"
              >
                <Icon name="x" size={10} />
              </button>
            </div>
          ))}
          <button
            type="button"
            className="btn btn-ghost"
            style={{ fontSize: 11, justifyContent: "center" }}
            onClick={() => set("heroBullets", [...bullets, ""])}
          >
            <Icon name="plus" size={10} /> Añadir bullet
          </button>
        </div>
      </Section>

      <Section title="CTAs (botones)">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {ctaButtons.map((c, i) => (
            <div
              key={i}
              style={{
                padding: 8,
                border: "1px solid var(--border)",
                borderRadius: "var(--r-sm)",
                background: "var(--bg-sunken)",
              }}
            >
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 6,
                }}
              >
                <Field label="Texto">
                  <input
                    className="input"
                    style={{ fontSize: 12 }}
                    value={c.text || ""}
                    onChange={(e) => {
                      const next = [...ctaButtons];
                      next[i] = { ...next[i], text: e.target.value };
                      set("heroCtaButtons", next);
                    }}
                  />
                </Field>
                <Field label="URL">
                  <input
                    className="input mono"
                    style={{ fontSize: 11 }}
                    value={c.url || ""}
                    onChange={(e) => {
                      const next = [...ctaButtons];
                      next[i] = { ...next[i], url: e.target.value };
                      set("heroCtaButtons", next);
                    }}
                  />
                </Field>
              </div>
              <div
                style={{
                  display: "flex",
                  gap: 6,
                  marginTop: 6,
                  alignItems: "center",
                }}
              >
                <Field label="Fondo">
                  <input
                    type="color"
                    value={c.bg || "#ea580c"}
                    style={{
                      width: 28,
                      height: 22,
                      border: "none",
                      padding: 0,
                      cursor: "pointer",
                    }}
                    onChange={(e) => {
                      const next = [...ctaButtons];
                      next[i] = { ...next[i], bg: e.target.value };
                      set("heroCtaButtons", next);
                    }}
                  />
                </Field>
                <Field label="Texto">
                  <input
                    type="color"
                    value={c.color || "#ffffff"}
                    style={{
                      width: 28,
                      height: 22,
                      border: "none",
                      padding: 0,
                      cursor: "pointer",
                    }}
                    onChange={(e) => {
                      const next = [...ctaButtons];
                      next[i] = { ...next[i], color: e.target.value };
                      set("heroCtaButtons", next);
                    }}
                  />
                </Field>
                <button
                  type="button"
                  className="icon-btn"
                  style={{ marginLeft: "auto", width: 20, height: 20 }}
                  onClick={() =>
                    set(
                      "heroCtaButtons",
                      ctaButtons.filter((_, j) => j !== i),
                    )
                  }
                  title="Borrar CTA"
                >
                  <Icon name="trash" size={10} />
                </button>
              </div>
            </div>
          ))}
          <button
            type="button"
            className="btn btn-ghost"
            style={{ fontSize: 11, justifyContent: "center" }}
            onClick={() =>
              set("heroCtaButtons", [
                ...ctaButtons,
                { text: "", url: "", bg: "#ea580c", color: "#ffffff" },
              ])
            }
          >
            <Icon name="plus" size={10} /> Añadir CTA
          </button>
        </div>
      </Section>

      <Section title="Estilo" defaultOpen={false}>
        <Field label="Color de fondo">
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input
              type="color"
              value={val("heroBgColor") || "#ffffff"}
              style={{
                width: 32,
                height: 24,
                border: "1px solid var(--border)",
                borderRadius: 4,
                padding: 0,
                cursor: "pointer",
              }}
              onChange={(e) => set("heroBgColor", e.target.value)}
            />
            <input
              className="input mono"
              style={{ fontSize: 11, flex: 1 }}
              value={val("heroBgColor") || "#ffffff"}
              onChange={(e) => set("heroBgColor", e.target.value)}
            />
          </div>
        </Field>
      </Section>
    </>
  );
}
