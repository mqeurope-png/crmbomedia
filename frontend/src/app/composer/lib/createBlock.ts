/**
 * Block factory + source materialisation — literal port of
 * `createBlock` (`bomedia-v4/app-data.jsx` lines 107-149) plus the
 * `addBlock(spec)` body of `app-main.jsx` (lines 900-993) that
 * layers source attribution and standalone config materialisation
 * on top.
 *
 * One-stop entry point: `materialiseBlock(spec, appState)`. The
 * store's `addBlock` calls this with the spec the palette emits
 * (sidebar item / CMDK / ColumnAddPicker) and a snapshot of the
 * catalog so heroes / steps / freebird blocks land in the canvas
 * already populated with the standalone's defaults — exactly the
 * way the original works.
 *
 * Spec types the v5o palette uses (and we honour):
 *   text / text-blank — empty editable text block.
 *   text + textId — prewritten reference.
 *   product / product_single / product_pair / product_trio
 *   brand_artisjet / brand_mbo / brand_pimpam / brand_smartjet / brand_flux
 *   brand_strip + standaloneId — standalone strip block.
 *   composed + composedId — composed catalog block.
 *   pimpam_hero / pimpam_steps / freebird / video + standaloneId
 *   product_hero + standaloneId — materialises from product on insert.
 *   cta / image / saved_cta — palette-blank or _ctaSourceId.
 *   section_2col / section_3col — multi-column container.
 *   divider_line / divider_short / divider_dots — divider variants.
 */

import type {
  AddBlockSpec,
  Block,
  BlockType,
  ComposerAppState,
  HeroCtaButton,
  Lang,
} from "./types";

let blockIdCounter = 0;

export function mkId(): string {
  blockIdCounter += 1;
  return `b${blockIdCounter}-${Date.now().toString(36)}`;
}

// ───────────────────────────────────────────────────────────────────
// createBlock — literal port of app-data.jsx line 107
// ───────────────────────────────────────────────────────────────────

export function createBlock(type: string): Block {
  const id = mkId();
  switch (type) {
    case "text":
      return {
        id,
        type: "text",
        _sourceType: "manual",
        overridesByLang: { es: "Hola,\n\nEscribe aquí tu texto personalizado." },
      };
    case "brand_artisjet":
      return { id, type: "brand_artisjet", brand: "artisjet" };
    case "brand_mbo":
      return { id, type: "brand_mbo", brand: "mbo" };
    case "brand_pimpam":
      return { id, type: "brand_pimpam", brand: "pimpam" };
    case "brand_smartjet":
      return { id, type: "brand_smartjet", brand: "smartjet" };
    case "brand_flux":
      return { id, type: "brand_flux", brand: "flux" };
    case "product_single":
      return { id, type: "product_single", product1: "young" };
    case "product_pair":
      return { id, type: "product_pair", product1: "young", product2: "3000pro" };
    case "product_trio":
      return {
        id,
        type: "product_trio",
        product1: "uv1612g",
        product2: "uv1812",
        product3: "uv2513",
      };
    case "freebird":
      return { id, type: "freebird" };
    case "pimpam_hero":
      return { id, type: "pimpam_hero" };
    case "pimpam_steps":
      return { id, type: "pimpam_steps" };
    case "section_2col":
      return {
        id,
        type: "section",
        layout: "2col",
        columns: [{ blocks: [] }, { blocks: [] }],
      };
    case "section_3col":
      return {
        id,
        type: "section",
        layout: "3col",
        columns: [{ blocks: [] }, { blocks: [] }, { blocks: [] }],
      };
    case "image":
      return {
        id,
        type: "image",
        src: "",
        alt: "",
        link: "",
        align: "center",
        widthPct: 100,
      };
    case "cta":
      return {
        id,
        type: "cta",
        title: "",
        subtitle: "",
        bullets: [],
        text: "Más información",
        url: "",
        bg: "#1d4ed8",
        color: "#ffffff",
        align: "center",
        panelBg: "transparent",
        panelBorder: "transparent",
      };
    case "divider_line":
      return {
        id,
        type: "divider",
        style: "line",
        color: "#e2e8f0",
        paddingV: 24,
      };
    case "divider_short":
      return {
        id,
        type: "divider",
        style: "short",
        color: "#cbd5e1",
        paddingV: 32,
      };
    case "divider_dots":
      return {
        id,
        type: "divider",
        style: "dots",
        color: "#94a3b8",
        paddingV: 28,
      };
    default:
      return { id, type: type as BlockType };
  }
}

// ───────────────────────────────────────────────────────────────────
// materialiseBlock — port of app-main.jsx addBlock body
// ───────────────────────────────────────────────────────────────────

const HERO_CTA_LABELS: Record<Lang, string> = {
  es: "Más información",
  fr: "Plus d'infos",
  de: "Mehr Infos",
  en: "More info",
  nl: "Meer info",
};

/** Take a palette spec and return a fully-populated Block. Handles:
 *  - text / text-blank shortcuts
 *  - direct-add fallbacks for product_pair / product_trio /
 *    brand_strip when no standaloneId is given
 *  - standalone hero / steps / freebird / video — source attribution +
 *    product_hero materialisation from the linked product. */
export function materialiseBlock(
  spec: AddBlockSpec,
  appState: ComposerAppState,
): Block | null {
  // text-blank → empty text block, ready to write into.
  if (spec.type === "text-blank") {
    return {
      id: mkId(),
      type: "text",
      _sourceType: "manual",
      overridesByLang: { es: "" },
    };
  }
  // text + textId → prewritten source attribution.
  if (spec.type === "text" && spec.textId) {
    const src = appState.prewrittenTexts.find((t) => t.id === spec.textId);
    return {
      id: mkId(),
      type: "text",
      textId: spec.textId,
      _sourceType: "prewritten",
      _sourceId: spec.textId,
      // Carry the ES seed so the canvas can show something before
      // the user opens the rich editor.
      overridesByLang: src ? { es: src.text } : undefined,
    };
  }

  // Base block from the factory (catches dividers / sections / blanks).
  const base = createBlock(spec.type);
  const b: Block = { ...base };

  // Direct catalog references (productId / composedId / textId).
  if (spec.productId) b.productId = spec.productId;
  if (spec.textId) b.textId = spec.textId;
  if (spec.composedId) {
    b.composedId = spec.composedId;
    b._sourceType = "composed";
    b._sourceId = spec.composedId;
  }
  if (spec._ctaSourceId) b._ctaSourceId = spec._ctaSourceId;
  if (spec._imgUrl) b.src = spec._imgUrl;

  // Standalone block materialisation. Lines 924-986 of app-main.jsx.
  if (spec.standaloneId) {
    b.standaloneId = spec.standaloneId;
    const sb = appState.standaloneBlocks.find(
      (s) => s.id === spec.standaloneId,
    );
    const cfg = (sb?.config ?? {}) as Record<string, unknown>;

    if (b.type === "product_single") {
      b.product1 = (cfg.defaultProduct as string) || "young";
    } else if (b.type === "product_pair") {
      b.product1 = (cfg.defaultProduct1 as string) || "young";
      b.product2 = (cfg.defaultProduct2 as string) || "3000pro";
    } else if (b.type === "product_trio") {
      b.product1 = (cfg.defaultProduct1 as string) || "uv1612g";
      b.product2 = (cfg.defaultProduct2 as string) || "uv1812";
      b.product3 = (cfg.defaultProduct3 as string) || "uv2513";
    } else if (b.type === "brand_strip") {
      b.brand = (cfg.brand as string) || "artisjet";
    } else if (
      b.type === "pimpam_hero" ||
      b.type === "product_hero" ||
      b.type === "hero" ||
      b.type === "pimpam_steps" ||
      b.type === "video" ||
      b.type === "freebird"
    ) {
      b._sourceType = "standalone";
      b._sourceId = spec.standaloneId;

      // product_hero standalones only store `config.defaultProduct`.
      // Materialise the hero fields from that product so the rest of
      // the pipeline (preview, editor, email-gen) works uniformly.
      if (b.type === "product_hero" && cfg.defaultProduct) {
        const p = appState.products.find(
          (x) => x.id === cfg.defaultProduct,
        );
        if (p) {
          b.heroImage = p.img;
          b.heroTitle = p.name;
          b.heroSubtitle = p.description ?? "";
          b.heroBullets = [p.feat1, p.feat2].filter(
            (x): x is string => Boolean(x),
          );
          const ctaButtons: HeroCtaButton[] = p.link
            ? [
                {
                  text: HERO_CTA_LABELS.es,
                  url: p.link,
                  bg: p.accent ?? "#1d4ed8",
                  color: "#ffffff",
                },
              ]
            : [];
          b.heroCtaButtons = ctaButtons;
          b.heroBgColor = "#ffffff";

          const heroI18n: Partial<
            Record<Lang, Record<string, unknown>>
          > = {};
          for (const l of ["fr", "de", "en", "nl"] as Lang[]) {
            const tr = p.i18n?.[l];
            if (!tr || typeof tr !== "object") continue;
            const entry: Record<string, unknown> = {};
            if (tr.desc) entry.heroSubtitle = tr.desc;
            if (tr.feat1 || tr.feat2) {
              entry.heroBullets = [tr.feat1, tr.feat2].filter(
                (x): x is unknown => Boolean(x),
              );
            }
            if (p.link || tr.link) {
              entry.heroCtaButtons = [
                {
                  text: HERO_CTA_LABELS[l] || HERO_CTA_LABELS.es,
                  url: (tr.link as string) || p.link,
                  bg: p.accent ?? "#1d4ed8",
                  color: "#ffffff",
                },
              ];
            }
            if (Object.keys(entry).length) heroI18n[l] = entry;
          }
          if (Object.keys(heroI18n).length) b.i18n = heroI18n;
        }
        // Normalise to the unified type so all editors/renderers
        // treat it as a regular hero from now on.
        b.type = "pimpam_hero";
      }
    }
  }

  // Direct-add fallbacks when not coming from a standalone — line 988.
  if (!spec.standaloneId) {
    if (b.type === "product_pair" && !b.product1) {
      b.product1 = "young";
      b.product2 = "3000pro";
    }
    if (b.type === "product_trio" && !b.product1) {
      b.product1 = "uv1612g";
      b.product2 = "uv1812";
      b.product3 = "uv2513";
    }
    if (b.type === "brand_strip" && !b.brand) b.brand = "artisjet";
  }

  return b;
}
