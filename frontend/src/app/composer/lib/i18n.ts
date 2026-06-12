/**
 * Composer i18n helpers — ported literal from `bomedia-v4`'s
 * `app-i18n.jsx`.
 *
 * Five resolution paths consulted in this order by `getTextInLanguage`:
 *
 *   1. Block-level `_overrides[lang]` (user edits per-lang in the
 *      inspector).
 *   2. `_sourceType === "prewritten"` → look up `prewrittenTexts`
 *      by `_sourceId` and read `i18n[lang].text`.
 *   3. `_sourceType === "composed_inner"` → look up the parent
 *      composed block, then its `innerBlocks[_innerIdx]` (or the
 *      first text block as a fallback), then its `i18n[lang].text`.
 *   4. `_sourceType === "manual"` → block.text + overrides only.
 *   5. Block-level `i18n[lang].text` for free-floating blocks
 *      without any catalog source.
 *
 * The hero resolver follows the same fallback chain but layers
 * `srcConfig` defaults under user edits and finally applies any
 * `_overrides[lang]` object on top.
 *
 * `mergeI18nFromDefaults` patches in catalog-side i18n shells when
 * loading a stored template — it's mostly defensive against drafts
 * saved before a translation landed on the catalog row.
 */

import type {
  Block,
  ComposerAppState,
  ComposerComposedBlock,
  ComposerPrewrittenText,
  ComposerProduct,
  ComposerStandaloneBlock,
  HeroCtaButton,
  Lang,
} from "./types";

export interface HeroData {
  heroTitle: string;
  heroSubtitle: string;
  heroBullets: string[];
  heroCtaText: string;
  heroCtaUrl: string;
  heroCtaButtons: HeroCtaButton[];
  heroImage: string;
  heroImageLink: string;
  heroBgColor: string;
}

export function getLocalizedProduct(
  product: ComposerProduct,
  lang: Lang,
): ComposerProduct {
  if (!lang || lang === "es" || !product.i18n || !product.i18n[lang]) {
    return product;
  }
  const localized: ComposerProduct = { ...product };
  const overrides = product.i18n[lang] as Record<string, unknown>;
  for (const key of Object.keys(overrides)) {
    const v = overrides[key];
    if (v) (localized as unknown as Record<string, unknown>)[key] = v;
  }
  return localized;
}

export function getLocalizedText<
  T extends { i18n?: Partial<Record<Lang, Record<string, unknown>>> },
>(obj: T, field: keyof T & string, lang: Lang): string {
  if (
    !lang ||
    lang === "es" ||
    !obj.i18n ||
    !obj.i18n[lang] ||
    obj.i18n[lang]?.[field] === undefined
  ) {
    return (obj[field] as unknown as string) ?? "";
  }
  return (obj.i18n[lang]?.[field] as string) ?? "";
}

export function isAvailableInLang<T extends { langs?: Lang[] }>(
  item: T,
  lang: Lang,
): boolean {
  if (!lang || !item.langs || item.langs.length === 0) return true;
  return item.langs.indexOf(lang) >= 0;
}

function readOverride(block: Block, lang: Lang): string | null {
  if (!block.overridesByLang) return null;
  const ov = block.overridesByLang[lang];
  if (typeof ov === "string") return ov;
  return null;
}

export function getTextInLanguage(
  block: Block,
  lang: Lang,
  appState: ComposerAppState,
): string {
  const override = readOverride(block, lang);
  if (override !== null) return override;

  if (block._sourceType === "prewritten" && block._sourceId) {
    const src = (appState.prewrittenTexts ?? []).find(
      (t) => t.id === block._sourceId,
    );
    if (src) {
      if (!lang || lang === "es") return src.text;
      const tr = (src.i18n?.[lang]?.text as string | undefined) ?? null;
      if (tr) return tr;
      return src.text;
    }
  }

  if (block._sourceType === "composed_inner" && block._composedSourceId) {
    const srcCb = (appState.composedBlocks ?? []).find(
      (cb) => cb.id === block._composedSourceId,
    );
    if (srcCb) {
      const innerIdx =
        typeof block._innerIdx === "number" ? block._innerIdx : -1;
      let srcIb: Block | null = null;
      if (innerIdx >= 0 && srcCb.innerBlocks && srcCb.innerBlocks[innerIdx]) {
        srcIb = srcCb.innerBlocks[innerIdx];
      } else if (srcCb.innerBlocks) {
        srcIb = srcCb.innerBlocks.find((ib) => ib.type === "text") ?? null;
      }
      if (srcIb) {
        if (!lang || lang === "es") return srcIb.text ?? "";
        const tr = (srcIb.i18n?.[lang]?.text as string | undefined) ?? null;
        if (tr) return tr;
        return srcIb.text ?? "";
      }
      if (!lang || lang === "es") return srcCb.introText ?? srcCb.intro_text ?? "";
      const tr =
        (srcCb.i18n?.[lang]?.introText as string | undefined) ??
        (srcCb.i18n?.[lang]?.intro_text as string | undefined) ??
        null;
      if (tr) return tr;
      return srcCb.introText ?? srcCb.intro_text ?? "";
    }
  }

  if (block._sourceType === "manual") {
    if (block.overridesByLang) {
      const ov = block.overridesByLang[lang];
      if (typeof ov === "string") return ov;
      const fallback = block.overridesByLang.es;
      if (typeof fallback === "string") return fallback;
    }
    return block.text ?? "";
  }

  if (block.i18n) {
    return getLocalizedText(block, "text", lang) ?? block.text ?? "";
  }
  return block.text ?? "";
}

export function getHeroDataInLanguage(
  block: Block,
  lang: Lang,
  appState: ComposerAppState,
): HeroData {
  const result: HeroData = {
    heroTitle: block.heroTitle ?? "",
    heroSubtitle: block.heroSubtitle ?? "",
    heroBullets: block.heroBullets ?? [],
    heroCtaText: block.heroCtaText ?? "",
    heroCtaUrl: block.heroCtaUrl ?? "",
    heroCtaButtons: block.heroCtaButtons ?? [],
    heroImage: block.heroImage ?? "",
    heroImageLink: block.heroImageLink ?? "",
    heroBgColor: block.heroBgColor ?? "#fff",
  };

  type HeroLike = Partial<HeroData> & {
    i18n?: Partial<Record<Lang, Partial<HeroData>>>;
  };
  let srcConfig: HeroLike | null = null;

  if (block._sourceType === "standalone" && block._sourceId) {
    const srcSb: ComposerStandaloneBlock | undefined = (
      appState.standaloneBlocks ?? []
    ).find((sb) => sb.id === block._sourceId);
    if (srcSb && srcSb.config) {
      srcConfig = srcSb.config as HeroLike;
    }
  } else if (block._sourceType === "composed_inner" && block._composedSourceId) {
    const srcCb2: ComposerComposedBlock | undefined = (
      appState.composedBlocks ?? []
    ).find((cb) => cb.id === block._composedSourceId);
    if (srcCb2) {
      const innerIdx2 =
        typeof block._innerIdx === "number" ? block._innerIdx : -1;
      if (
        innerIdx2 >= 0 &&
        srcCb2.innerBlocks &&
        srcCb2.innerBlocks[innerIdx2]
      ) {
        const srcIb2 = srcCb2.innerBlocks[innerIdx2];
        if (srcIb2.type === "pimpam_hero") srcConfig = srcIb2 as unknown as HeroLike;
      }
    }
  }

  if (srcConfig) {
    result.heroTitle = result.heroTitle || (srcConfig.heroTitle ?? "");
    result.heroSubtitle = result.heroSubtitle || (srcConfig.heroSubtitle ?? "");
    result.heroBullets =
      result.heroBullets && result.heroBullets.length > 0
        ? result.heroBullets
        : srcConfig.heroBullets ?? [];
    result.heroCtaText = result.heroCtaText || (srcConfig.heroCtaText ?? "");
    result.heroCtaUrl = result.heroCtaUrl || (srcConfig.heroCtaUrl ?? "");
    result.heroCtaButtons =
      result.heroCtaButtons && result.heroCtaButtons.length > 0
        ? result.heroCtaButtons
        : srcConfig.heroCtaButtons ?? [];
    result.heroImage = result.heroImage || (srcConfig.heroImage ?? "");
    result.heroImageLink =
      result.heroImageLink || (srcConfig.heroImageLink ?? "");
    result.heroBgColor = result.heroBgColor || (srcConfig.heroBgColor ?? "#fff");

    if (lang && lang !== "es" && srcConfig.i18n && srcConfig.i18n[lang]) {
      const hi = srcConfig.i18n[lang] ?? {};
      if (hi.heroTitle) result.heroTitle = hi.heroTitle;
      if (hi.heroSubtitle) result.heroSubtitle = hi.heroSubtitle;
      if (hi.heroBullets) result.heroBullets = hi.heroBullets;
      if (hi.heroCtaText) result.heroCtaText = hi.heroCtaText;
      if (hi.heroCtaButtons) result.heroCtaButtons = hi.heroCtaButtons;
    }
  } else if (block.i18n && lang && lang !== "es" && block.i18n[lang]) {
    const bhi = block.i18n[lang] ?? {};
    const title = bhi.heroTitle as string | undefined;
    const subtitle = bhi.heroSubtitle as string | undefined;
    const bullets = bhi.heroBullets as string[] | undefined;
    const ctaText = bhi.heroCtaText as string | undefined;
    if (title) result.heroTitle = title;
    if (subtitle) result.heroSubtitle = subtitle;
    if (bullets) result.heroBullets = bullets;
    if (ctaText) result.heroCtaText = ctaText;
  }

  // Hero per-lang overrides live under `block.i18n[lang]` (handled
  // above). The v5o canvas never wrote a hero override into
  // `overridesByLang` — that field stays exclusively for plain-text
  // overrides on text blocks.

  return result;
}

interface MergeableDataset {
  products?: ComposerProduct[];
  composedBlocks?: ComposerComposedBlock[];
  prewrittenTexts?: ComposerPrewrittenText[];
  standaloneBlocks?: ComposerStandaloneBlock[];
}

/** Patch i18n from a defaults snapshot onto a loaded dataset. Used
 * when hydrating a template that predates a translation landing on
 * the catalog row — keeps the loaded user edits and only fills in
 * the empty `i18n` shells. */
export function mergeI18nFromDefaults<T extends MergeableDataset>(
  loadedData: T,
  defaults: MergeableDataset,
): T {
  if (loadedData.products && defaults.products) {
    const defaultMap: Record<string, ComposerProduct> = {};
    defaults.products.forEach((dp) => {
      defaultMap[dp.id] = dp;
    });
    loadedData.products = loadedData.products.map((p) => {
      const dp = defaultMap[p.id];
      if (dp && dp.i18n && !p.i18n) p.i18n = dp.i18n;
      return p;
    });
  }

  if (loadedData.composedBlocks && defaults.composedBlocks) {
    const blockMap: Record<string, ComposerComposedBlock> = {};
    defaults.composedBlocks.forEach((db) => {
      blockMap[db.id] = db;
    });
    loadedData.composedBlocks = loadedData.composedBlocks.map((b) => {
      const db = blockMap[b.id];
      if (db && db.i18n && !b.i18n) b.i18n = db.i18n;
      return b;
    });
  }

  if (loadedData.prewrittenTexts && defaults.prewrittenTexts) {
    const textMap: Record<string, ComposerPrewrittenText> = {};
    defaults.prewrittenTexts.forEach((dt) => {
      textMap[dt.id] = dt;
    });
    loadedData.prewrittenTexts = loadedData.prewrittenTexts.map((t) => {
      const dt = textMap[t.id];
      if (dt && dt.i18n && !t.i18n) t.i18n = dt.i18n;
      return t;
    });
  }

  if (loadedData.standaloneBlocks && defaults.standaloneBlocks) {
    const sbMap: Record<string, ComposerStandaloneBlock> = {};
    defaults.standaloneBlocks.forEach((dsb) => {
      sbMap[dsb.id] = dsb;
    });
    loadedData.standaloneBlocks = loadedData.standaloneBlocks.map((sb) => {
      const dsb = sbMap[sb.id];
      const dsbCfg = (dsb?.config ?? {}) as Record<string, unknown>;
      const sbCfg = (sb.config ?? {}) as Record<string, unknown>;
      if (dsbCfg.i18n && !sbCfg.i18n) sbCfg.i18n = dsbCfg.i18n;
      sb.config = sbCfg;
      return sb;
    });
  }

  if (loadedData.composedBlocks && defaults.composedBlocks) {
    const cbMap: Record<string, ComposerComposedBlock> = {};
    defaults.composedBlocks.forEach((dcb) => {
      cbMap[dcb.id] = dcb;
    });
    loadedData.composedBlocks = loadedData.composedBlocks.map((cb) => {
      const dcb = cbMap[cb.id];
      if (dcb && dcb.innerBlocks && cb.innerBlocks) {
        cb.innerBlocks = cb.innerBlocks.map((ib, idx) => {
          const dib = dcb.innerBlocks?.[idx];
          if (dib && dib.i18n && !ib.i18n) ib.i18n = dib.i18n;
          return ib;
        });
      }
      return cb;
    });
  }
  return loadedData;
}
