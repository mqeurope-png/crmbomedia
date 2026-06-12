/**
 * Email HTML generator — table-based output for email-client compat.
 *
 * Ported literal from `bomedia-v4/src/app-email-gen.jsx`. The shape is
 * preserved one-for-one (escapeHtml, productCardHtml, brandStripHtml,
 * textBlockHtml, productSingleHtml, productPairHtml, productTrioHtml,
 * freebirdHtml, pimpamHeroHtml, pimpamStepsHtml, imageBlockHtml,
 * ctaBlockHtml, dividerBlockHtml, generateFullHtml, v3BlocksToV2Blocks,
 * renderEmailHtml, addUtmParams, renderEmailHtmlWithTracking).
 *
 * The only deviation: the CRM catalog ships product fields as
 * `description` / `brand_id` while the original Composer reads `desc`
 * / `brand`. The `EmailProduct` interface mirrors the runtime shape
 * the v5o functions expect, and `toEmailProduct()` adapts CRM
 * ComposerProduct → EmailProduct at the call boundary so the body of
 * every render fn stays a literal port.
 */

import {
  getHeroDataInLanguage,
  getLocalizedProduct as i18nGetLocalizedProduct,
  getLocalizedText,
  getTextInLanguage,
} from "./i18n";
import { sanitizeHtml } from "./security";
import type {
  Block,
  ComposerAppState,
  ComposerBrand,
  ComposerProduct,
  HeroCtaButton,
  Lang,
  PimpamStep,
} from "./types";

// ───────────────────────────────────────────────────────────────────
// Internal product shape — exactly what the original v5o functions
// expect. We map CRM `ComposerProduct` → this at the boundary so the
// fn bodies stay byte-for-byte equivalent.
// ───────────────────────────────────────────────────────────────────

export interface EmailProduct {
  id: string;
  brand: string;
  name: string;
  desc: string;
  badge: string;
  badgeBg: string;
  badgeColor: string;
  img: string;
  area: string;
  alt: string;
  feat1: string;
  feat2: string;
  price: string;
  link: string;
  accent: string;
  gradient: string;
  i18n?: Record<string, Record<string, unknown>>;
}

export function toEmailProduct(p: ComposerProduct): EmailProduct {
  return {
    id: p.id,
    brand: p.brand_id,
    name: p.name,
    desc: p.description ?? "",
    badge: p.badge ?? "",
    badgeBg: p.badge_bg ?? "",
    badgeColor: p.badge_color ?? "",
    img: p.img,
    area: p.area ?? "-",
    alt: p.alt ?? "-",
    feat1: p.feat1 ?? "",
    feat2: p.feat2 ?? "",
    price: p.price ?? "",
    link: p.link ?? "",
    accent: p.accent ?? "#000",
    gradient: p.gradient ?? "#000",
    i18n: p.i18n,
  };
}

// Map of i18n to the EmailProduct shape so getLocalizedProduct keeps
// working through the adapter.
function getLocalizedEmailProduct(p: EmailProduct, lang: Lang): EmailProduct {
  if (!lang || lang === "es" || !p.i18n || !p.i18n[lang]) return p;
  const ov = p.i18n[lang] as Record<string, unknown>;
  const out: EmailProduct = { ...p };
  for (const key of Object.keys(ov)) {
    const v = ov[key];
    if (v) (out as unknown as Record<string, unknown>)[key] = v;
  }
  return out;
}

// ───────────────────────────────────────────────────────────────────
// Brand shape used by the strip renderer — adapter for CRM brands.
// ───────────────────────────────────────────────────────────────────

export interface EmailBrand {
  id: string;
  label: string;
  color: string;
  divider: string | null;
  logo: string | null;
  logoBg?: string | null;
  logoHeight: string | null;
  logoMaxWidth: string | null;
  url?: string | Record<string, string>;
  urlLabel?: string | Record<string, string>;
}

export function toEmailBrand(b: ComposerBrand): EmailBrand {
  return {
    id: b.id,
    label: b.label,
    color: b.color,
    divider: b.divider,
    logo: b.logo,
    logoBg: null,
    logoHeight: b.logo_height,
    logoMaxWidth: b.logo_max_width,
    url: undefined,
    urlLabel: undefined,
  };
}

// ───────────────────────────────────────────────────────────────────
// Helpers
// ───────────────────────────────────────────────────────────────────

export function escapeHtml(str: unknown): string {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ───────────────────────────────────────────────────────────────────
// Product cards (regular + compact)
// ───────────────────────────────────────────────────────────────────

export function productCardHtml(p: EmailProduct, lang: Lang): string {
  const areaLabel =
    lang === "fr"
      ? "Surface"
      : lang === "de"
        ? "Fläche"
        : lang === "en"
          ? "Area"
          : lang === "nl"
            ? "Oppervlak"
            : "Área";
  const altLabel =
    lang === "fr"
      ? "Haut. max."
      : lang === "de"
        ? "Max. Höhe"
        : lang === "en"
          ? "Max. height"
          : lang === "nl"
            ? "Max. hoogte"
            : "Alt. máx.";
  const eName = escapeHtml(p.name);
  const eDesc = escapeHtml(p.desc);
  const eFeat1 = escapeHtml(p.feat1);
  const eFeat2 = escapeHtml(p.feat2);
  const eBadge = escapeHtml(p.badge);
  const eArea = escapeHtml(p.area);
  const eAlt = escapeHtml(p.alt);
  const ePrice = escapeHtml(p.price);
  const eImg = escapeHtml(p.img || "");
  const eLink = escapeHtml(p.link || "");
  let areaBlock = "";
  if (p.area !== "-") {
    areaBlock =
      '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0">' +
      '<tr><td width="38%" style="font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase">' +
      areaLabel +
      '</td><td style="font-size:11px;font-weight:700;color:#334155">' +
      eArea +
      "</td></tr>" +
      (p.alt !== "-"
        ? '<tr><td style="font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase">' +
          altLabel +
          '</td><td style="font-size:11px;font-weight:700;color:#334155">' +
          eAlt +
          "</td></tr>"
        : "") +
      "</table>";
  }
  const priceExtra =
    p.price !== "Consultar" &&
    p.price !== "Sur demande" &&
    p.price !== "Auf Anfrage" &&
    p.price !== "On request" &&
    p.price !== "Op aanvraag"
      ? ' <span style="font-size:11px;font-weight:500;color:#475569">' +
        (lang === "fr"
          ? "+ TVA"
          : lang === "de"
            ? "+ MwSt"
            : lang === "en"
              ? "+ VAT"
              : lang === "nl"
                ? "+ BTW"
                : "+ IVA") +
        "</span>"
      : "";
  const ctaLabel =
    p.brand === "pimpam"
      ? lang === "fr"
        ? "Détails"
        : lang === "de"
          ? "Details"
          : lang === "en"
            ? "Details"
            : lang === "nl"
              ? "Details"
              : "Detalles"
      : lang === "fr"
        ? "Plus d'infos"
        : lang === "de"
          ? "Mehr Infos"
          : lang === "en"
            ? "More info"
            : lang === "nl"
              ? "Meer info"
              : "Más info";
  const bgExtra = p.brand === "pimpam" ? ";background:#fff7ed" : "";
  return (
    '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1.5px solid ' +
    (p.brand === "pimpam" ? "#fed7aa" : "#e2e8f0") +
    ';border-radius:12px;overflow:hidden;background:#fff">' +
    '<tr><td style="background:#fff;padding:8px 4px 4px;border-bottom:1px solid ' +
    (p.brand === "pimpam" ? "#ffedd5" : "#f1f5f9") +
    ';text-align:center">' +
    '<img src="' +
    eImg +
    '" alt="' +
    eName +
    '" style="display:block;width:100%;max-width:100%;height:auto;border-radius:8px">' +
    "</td></tr>" +
    '<tr><td style="padding:14px' +
    bgExtra +
    '">' +
    '<span style="display:inline-block;font-size:9px;font-weight:800;letter-spacing:1.5px;text-transform:uppercase;padding:3px 9px;border-radius:20px;margin-bottom:8px;background:' +
    p.badgeBg +
    ";color:" +
    p.badgeColor +
    '">' +
    eBadge +
    "</span>" +
    '<p style="font-size:15px;font-weight:900;color:#0f172a;margin:0;line-height:1.3">' +
    eName +
    "</p>" +
    '<p style="font-size:12px;color:#64748b;margin:5px 0 0;line-height:1.5">' +
    eDesc +
    "</p>" +
    areaBlock +
    '<p style="font-size:11px;color:#475569;padding:2px 0;margin:' +
    (p.area === "-" ? "8px" : "0") +
    ' 0 0">✓ ' +
    eFeat1 +
    "</p>" +
    '<p style="font-size:11px;color:#475569;padding:2px 0;margin:0">✓ ' +
    eFeat2 +
    "</p>" +
    '<p style="font-size:16px;font-weight:900;color:' +
    p.accent +
    ';margin:10px 0 0;text-align:center">' +
    ePrice +
    priceExtra +
    "</p>" +
    '<a href="' +
    eLink +
    '" style="display:block;text-align:center;font-size:12px;font-weight:700;text-decoration:none;padding:8px 10px;border-radius:8px;text-transform:uppercase;letter-spacing:0.4px;background:' +
    p.gradient +
    ';color:#fff;margin-top:8px">' +
    ctaLabel +
    " →</a>" +
    "</td></tr></table>"
  );
}

export function productCardCompactHtml(p: EmailProduct, lang: Lang): string {
  const areaLabel =
    lang === "fr"
      ? "Surface"
      : lang === "de"
        ? "Fläche"
        : lang === "en"
          ? "Area"
          : lang === "nl"
            ? "Oppervlak"
            : "Área";
  const altLabel =
    lang === "fr"
      ? "Haut."
      : lang === "de"
        ? "Höhe"
        : lang === "en"
          ? "Height"
          : lang === "nl"
            ? "Hoogte"
            : "Alt";
  const eName = escapeHtml(p.name);
  const eDesc = escapeHtml(p.desc);
  const eFeat1 = escapeHtml(p.feat1);
  const eFeat2 = escapeHtml(p.feat2);
  const eBadge = escapeHtml(p.badge);
  const eArea = escapeHtml(p.area);
  const eAlt = escapeHtml(p.alt);
  const ePrice = escapeHtml(p.price);
  const eImg = escapeHtml(p.img || "");
  const eLink = escapeHtml(p.link || "");
  let areaBlock = "";
  if (p.area !== "-") {
    areaBlock =
      '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:6px 0">' +
      '<tr><td style="font-size:9px;font-weight:700;color:#94a3b8;text-transform:uppercase">' +
      areaLabel +
      ": " +
      eArea +
      "</td></tr>" +
      (p.alt !== "-"
        ? '<tr><td style="font-size:9px;font-weight:700;color:#94a3b8;text-transform:uppercase">' +
          altLabel +
          ": " +
          eAlt +
          "</td></tr>"
        : "") +
      "</table>";
  }
  const priceExtra =
    p.price !== "Consultar" &&
    p.price !== "Sur demande" &&
    p.price !== "Auf Anfrage" &&
    p.price !== "On request" &&
    p.price !== "Op aanvraag"
      ? ' <span style="font-size:9px;font-weight:500;color:#475569">(' +
        (lang === "fr"
          ? "+ TVA"
          : lang === "de"
            ? "+ MwSt"
            : lang === "en"
              ? "+ VAT"
              : lang === "nl"
                ? "+ BTW"
                : "+ IVA") +
        ")</span>"
      : "";
  const ctaLabel =
    p.brand === "pimpam"
      ? lang === "fr"
        ? "Détails"
        : lang === "de"
          ? "Details"
          : lang === "en"
            ? "Details"
            : lang === "nl"
              ? "Details"
              : "Detalles"
      : "Info";
  const bgExtra = p.brand === "pimpam" ? ";background:#fff7ed" : "";
  return (
    '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid ' +
    (p.brand === "pimpam" ? "#fed7aa" : "#e2e8f0") +
    ';border-radius:10px;overflow:hidden;background:#fff">' +
    '<tr><td style="background:#fff;padding:6px 3px 3px;border-bottom:1px solid ' +
    (p.brand === "pimpam" ? "#ffedd5" : "#f1f5f9") +
    ';text-align:center">' +
    '<img src="' +
    eImg +
    '" alt="' +
    eName +
    '" style="display:block;width:100%;max-width:100%;height:auto;border-radius:6px">' +
    "</td></tr>" +
    '<tr><td style="padding:10px' +
    bgExtra +
    '">' +
    '<span style="display:inline-block;font-size:8px;font-weight:800;letter-spacing:1px;text-transform:uppercase;padding:2px 7px;border-radius:16px;margin-bottom:4px;background:' +
    p.badgeBg +
    ";color:" +
    p.badgeColor +
    '">' +
    eBadge +
    "</span>" +
    '<p style="font-size:13px;font-weight:900;color:#0f172a;margin:0;line-height:1.3">' +
    eName +
    "</p>" +
    '<p style="font-size:10px;color:#64748b;margin:3px 0 0;line-height:1.4">' +
    eDesc +
    "</p>" +
    areaBlock +
    '<p style="font-size:10px;color:#475569;padding:1px 0;margin:' +
    (p.area === "-" ? "6px" : "0") +
    ' 0 0">✓ ' +
    eFeat1 +
    "</p>" +
    '<p style="font-size:10px;color:#475569;padding:1px 0;margin:0">✓ ' +
    eFeat2 +
    "</p>" +
    '<p style="font-size:13px;font-weight:900;color:' +
    p.accent +
    ';margin:8px 0 0;text-align:center">' +
    ePrice +
    priceExtra +
    "</p>" +
    '<a href="' +
    eLink +
    '" style="display:block;text-align:center;font-size:10px;font-weight:700;text-decoration:none;padding:7px 8px;border-radius:6px;text-transform:uppercase;letter-spacing:0.3px;background:' +
    p.gradient +
    ';color:#fff;margin-top:6px">' +
    ctaLabel +
    " →</a>" +
    "</td></tr></table>"
  );
}

// ───────────────────────────────────────────────────────────────────
// Brand strip
// ───────────────────────────────────────────────────────────────────

function pickLang(
  value: string | Record<string, string> | undefined,
  lang: Lang,
): string {
  if (!value) return "";
  if (typeof value === "string") return value;
  return value[lang] || value.es || "";
}

export function brandStripHtml(
  key: string,
  lang: Lang,
  brands: EmailBrand[],
): string {
  const b = brands.find((br) => br.id === key);
  if (!b) return "";
  const url = pickLang(b.url, lang);
  const urlLabel = pickLang(b.urlLabel, lang);
  const h = parseInt(b.logoHeight ?? "", 10) || 28;
  const mw = parseInt(b.logoMaxWidth ?? "", 10) || 180;
  const imgTag = b.logo
    ? '<img src="' +
      b.logo +
      '" alt="' +
      b.label +
      '" style="max-height:' +
      h +
      "px;max-width:" +
      mw +
      'px;width:auto;height:auto;display:block">'
    : '<span style="font-size:14px;font-weight:800;color:' +
      b.color +
      '">' +
      b.label +
      "</span>";
  const linkTag =
    '<a href="' +
    url +
    '" style="font-size:12px;font-weight:700;color:' +
    b.color +
    ';text-decoration:none;white-space:nowrap">' +
    urlLabel +
    "</a>";
  if (b.logoBg) {
    return (
      '<tr><td style="padding:16px 8px 8px"><table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:' +
      b.logoBg +
      ';border-radius:10px"><tr>' +
      '<td valign="middle" width="70%" style="padding:16px 24px">' +
      imgTag +
      "</td>" +
      '<td valign="middle" width="30%" align="right" style="padding:16px 24px">' +
      linkTag +
      "</td>" +
      "</tr></table></td></tr>"
    );
  }
  return (
    '<tr><td style="padding:16px 8px 8px"><table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>' +
    '<td valign="middle" width="70%" style="padding:4px 0">' +
    imgTag +
    "</td>" +
    '<td valign="middle" width="30%" align="right" style="padding:4px 0">' +
    linkTag +
    "</td>" +
    '</tr></table><div style="height:1px;background:' +
    (b.divider ?? "#e2e8f0") +
    ';margin-top:8px"></div></td></tr>'
  );
}

// ───────────────────────────────────────────────────────────────────
// Text block (supports plain + rich HTML; sanitizes)
// ───────────────────────────────────────────────────────────────────

export interface TextBlockOpts {
  fontSize?: string | number;
  align?: string;
}

export function textBlockHtml(text: string, opts?: TextBlockOpts): string {
  const fs = (opts && opts.fontSize) || 14;
  const align = (opts && opts.align) || "left";
  const wrapOpen =
    '<tr><td style="padding:16px 8px;font-size:' +
    fs +
    "px;color:#1e293b;line-height:1.65;text-align:" +
    align +
    '">\n';
  const wrapClose = "</td></tr>";
  if (text && /<[a-z][\s\S]*>/i.test(text)) {
    let richHtml = sanitizeHtml(text);
    richHtml = richHtml.replace(
      /<h1[^>]*>/gi,
      '<h1 style="font-size:22px;font-weight:800;color:#0f172a;margin:0 0 12px;font-family:system-ui,sans-serif">',
    );
    richHtml = richHtml.replace(
      /<h2[^>]*>/gi,
      '<h2 style="font-size:18px;font-weight:700;color:#1e293b;margin:0 0 10px;font-family:system-ui,sans-serif">',
    );
    richHtml = richHtml.replace(
      /<h3[^>]*>/gi,
      '<h3 style="font-size:15px;font-weight:700;color:#374151;margin:0 0 8px;font-family:system-ui,sans-serif">',
    );
    richHtml = richHtml.replace(/<p[^>]*>/gi, '<p style="margin:0 0 14px">');
    richHtml = richHtml.replace(
      /<ul[^>]*>/gi,
      '<ul style="margin:0 0 14px;padding-left:20px">',
    );
    richHtml = richHtml.replace(
      /<ol[^>]*>/gi,
      '<ol style="margin:0 0 14px;padding-left:20px">',
    );
    richHtml = richHtml.replace(/<li[^>]*>/gi, '<li style="margin:0 0 4px">');
    richHtml = richHtml.replace(
      /<a /gi,
      '<a style="color:#2563eb;text-decoration:underline" ',
    );
    return wrapOpen + richHtml + wrapClose;
  }
  const lines = String(text || "").split("\n");
  let html = "";
  for (const ln of lines) {
    if (ln.trim()) html += '<p style="margin:0 0 14px">' + escapeHtml(ln) + "</p>\n";
  }
  return wrapOpen + html + wrapClose;
}

// ───────────────────────────────────────────────────────────────────
// Product layouts
// ───────────────────────────────────────────────────────────────────

export function productSingleHtml(p: EmailProduct, lang: Lang): string {
  return (
    '<tr><td style="padding:8px 8px 16px"><table width="320" cellpadding="0" cellspacing="0" border="0" style="margin:0"><tr><td>' +
    productCardHtml(p, lang) +
    "</td></tr></table></td></tr>"
  );
}

export function productPairHtml(
  p1: EmailProduct,
  p2: EmailProduct,
  lang: Lang,
): string {
  return (
    '<tr><td style="padding:8px 8px 16px"><table width="100%" cellpadding="0" cellspacing="0" border="0"><tr class="prod-row">' +
    '<td class="col-half prod-cell" width="50%" valign="top" style="padding:0 5px 0 0">' +
    productCardHtml(p1, lang) +
    "</td>" +
    '<td class="col-half prod-cell" width="50%" valign="top" style="padding:0 0 0 5px">' +
    productCardHtml(p2, lang) +
    "</td>" +
    "</tr></table></td></tr>"
  );
}

export function productTrioHtml(
  p1: EmailProduct,
  p2: EmailProduct,
  p3: EmailProduct,
  lang: Lang,
): string {
  return (
    '<tr><td style="padding:8px 8px 16px"><table width="100%" cellpadding="0" cellspacing="0" border="0"><tr class="prod-row">' +
    '<td class="col-third prod-cell" width="33%" valign="top" style="padding:0 4px 0 0">' +
    productCardCompactHtml(p1, lang) +
    "</td>" +
    '<td class="col-third prod-cell" width="33%" valign="top" style="padding:0 4px">' +
    productCardCompactHtml(p2, lang) +
    "</td>" +
    '<td class="col-third prod-cell" width="33%" valign="top" style="padding:0 0 0 4px">' +
    productCardCompactHtml(p3, lang) +
    "</td>" +
    "</tr></table></td></tr>"
  );
}

// ───────────────────────────────────────────────────────────────────
// Freebird / video
// ───────────────────────────────────────────────────────────────────

export interface FreebirdConfig {
  youtubeUrl?: string;
  thumbnailOverride?: string;
}

export function freebirdHtml(config: FreebirdConfig | null, lang: Lang): string {
  const cfg = config ?? {};
  const youtubeUrl =
    cfg.youtubeUrl || "https://www.youtube.com/watch?v=gp-x_jRBRcE";
  let thumbnailUrl = cfg.thumbnailOverride;
  if (!thumbnailUrl && youtubeUrl) {
    const videoIdMatch = youtubeUrl.match(
      /(?:youtube\.com\/watch\?v=|youtu\.be\/)([^&\n?#]+)/,
    );
    if (videoIdMatch)
      thumbnailUrl =
        "https://img.youtube.com/vi/" + videoIdMatch[1] + "/hqdefault.jpg";
  }
  if (!thumbnailUrl)
    thumbnailUrl =
      "https://artisjet-printers.eu/wp-content/uploads/2025/02/3000-pro-freebirdok.png";
  const videoLabel =
    lang === "fr"
      ? "Voir la vidéo"
      : lang === "de"
        ? "Video ansehen"
        : lang === "en"
          ? "Watch video"
          : lang === "nl"
            ? "Video bekijken"
            : "Ver vídeo";
  return (
    '<tr><td style="padding:8px 8px 16px"><table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-radius:12px;overflow:hidden;background:#0f172a">' +
    '<tr><td style="text-align:center;padding:0">' +
    '<a href="' +
    youtubeUrl +
    '" target="_blank" rel="noopener noreferrer" style="text-decoration:none">' +
    '<img src="' +
    thumbnailUrl +
    '" alt="Video" width="480" style="width:100%;max-width:480px;display:block;margin:0 auto;opacity:0.85"/>' +
    "</a></td></tr>" +
    '<tr><td style="text-align:center;padding:12px 16px;background:#0f172a">' +
    '<a href="' +
    youtubeUrl +
    '" target="_blank" rel="noopener noreferrer" style="color:#93c5fd;font-size:14px;font-weight:700;text-decoration:none;font-family:system-ui,sans-serif">▶ ' +
    videoLabel +
    "</a>" +
    "</td></tr></table></td></tr>"
  );
}

// ───────────────────────────────────────────────────────────────────
// Pimpam hero
// ───────────────────────────────────────────────────────────────────

export interface PimpamHeroConfig {
  i18n?: Partial<Record<Lang, Partial<PimpamHeroConfig>>>;
  heroImage?: string;
  heroImageLink?: string;
  heroTitle?: string;
  heroSubtitle?: string;
  heroBullets?: string[];
  heroCtaText?: string;
  heroCtaUrl?: string;
  heroCtaButtons?: Array<HeroCtaButton & { bg?: string; color?: string }>;
  heroCtaColor?: string;
  heroBgColor?: string;
}

export function pimpamHeroHtml(
  config: PimpamHeroConfig | null,
  lang: Lang | null,
): string {
  const cfg = config ?? {};
  const hi =
    cfg.i18n && lang && lang !== "es" && cfg.i18n[lang] ? cfg.i18n[lang]! : null;
  const imgUrl =
    cfg.heroImage ||
    "https://pimpam-vending.com/wp-content/uploads/2026/01/ChatGPT-Image-22-ene-2026-16_17_36.png";
  const title =
    (hi && hi.heroTitle) ||
    cfg.heroTitle ||
    "Personaliza, imprime y vende… sin operario";
  const subtitle =
    (hi && hi.heroSubtitle) ||
    cfg.heroSubtitle ||
    "Impresión UV-LED directa sobre fundas de móvil en autoservicio completo.";
  const bullets = (hi && hi.heroBullets) ||
    cfg.heroBullets || [
      "Autoservicio 100% — sin personal",
      "Pago con tarjeta, móvil o QR",
      "Funda impresa en HD en 30 segundos",
      "Compatible con +600 modelos de móvil",
    ];
  const imgLink = cfg.heroImageLink || "";
  const ctaText = (hi && hi.heroCtaText) || cfg.heroCtaText || "";
  const ctaUrl = (hi && hi.heroCtaUrl) || cfg.heroCtaUrl || "";
  const bgColor = cfg.heroBgColor || "#fff";

  let isDark = false;
  const _luminance = (r: number, g: number, b: number) =>
    r * 0.299 + g * 0.587 + b * 0.114;
  const bgRaw = String(bgColor || "").trim();
  let m = bgRaw.match(/^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
  if (m) {
    isDark =
      _luminance(parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)) <
      128;
  } else if (
    (m = bgRaw.match(/^#?([0-9a-f])([0-9a-f])([0-9a-f])$/i))
  ) {
    isDark =
      _luminance(
        parseInt(m[1] + m[1], 16),
        parseInt(m[2] + m[2], 16),
        parseInt(m[3] + m[3], 16),
      ) < 128;
  } else if (
    (m = bgRaw.match(
      /^rgba?\s*\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)/i,
    ))
  ) {
    isDark =
      _luminance(parseFloat(m[1]), parseFloat(m[2]), parseFloat(m[3])) < 128;
  } else {
    const darkNames = [
      "black",
      "navy",
      "maroon",
      "darkblue",
      "darkred",
      "darkgreen",
      "darkslategray",
      "midnightblue",
      "indigo",
      "purple",
      "brown",
    ];
    if (darkNames.includes(bgRaw.toLowerCase())) isDark = true;
  }
  const titleColor = isDark ? "#ffffff" : "#0f172a";
  const subColor = isDark ? "#94a3b8" : "#64748b";
  const bulletColor = isDark ? "#cbd5e1" : "#475569";
  const ctaBg = isDark ? cfg.heroCtaColor || "#00d4ff" : "#ea580c";
  const ctaTextColor = isDark ? "#0f172a" : "#fff";

  let imgInner =
    '<img src="' +
    imgUrl +
    '" alt="Hero" width="270" style="display:block;width:100%;max-width:100%;height:auto;border-radius:10px 0 0 10px">';
  if (imgLink) {
    imgInner =
      '<a href="' +
      imgLink +
      '" target="_blank" rel="noopener noreferrer" style="text-decoration:none;display:block">' +
      imgInner +
      "</a>";
  }
  const imgHtml = imgInner;

  let bulletsHtml = "";
  for (const bullet of bullets) {
    bulletsHtml +=
      '<p style="font-size:12px;color:' +
      bulletColor +
      ';margin:0 0 4px;line-height:1.5">✓ ' +
      bullet +
      "</p>";
  }

  let ctaHtml = "";
  let ctaButtons = cfg.heroCtaButtons || [];
  if (ctaButtons.length === 0 && ctaText && ctaUrl)
    ctaButtons = [{ text: ctaText, url: ctaUrl }];
  if (hi && hi.heroCtaButtons && hi.heroCtaButtons.length > 0)
    ctaButtons = hi.heroCtaButtons as NonNullable<PimpamHeroConfig["heroCtaButtons"]>;
  if (ctaButtons.length > 0) {
    let btnCells = "";
    for (let bi = 0; bi < ctaButtons.length; bi += 1) {
      const btn = ctaButtons[bi];
      if (btn.text && btn.url) {
        const btnBg = btn.bg || ctaBg;
        const btnTxtC = btn.color || ctaTextColor;
        if (bi > 0) btnCells += '<td style="width:8px"></td>';
        btnCells +=
          '<td style="background:' +
          btnBg +
          ';border-radius:6px;padding:9px 20px"><a href="' +
          btn.url +
          '" target="_blank" rel="noopener noreferrer" style="color:' +
          btnTxtC +
          ';font-size:13px;font-weight:700;text-decoration:none;font-family:system-ui,sans-serif;white-space:nowrap">' +
          btn.text +
          "</a></td>";
      }
    }
    if (btnCells)
      ctaHtml =
        '<table cellpadding="0" cellspacing="0" border="0" style="margin-top:10px"><tr>' +
        btnCells +
        "</tr></table>";
  }

  return (
    '<tr><td style="padding:12px 8px 16px"><table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:' +
    bgColor +
    ';border-radius:10px;overflow:hidden"><tr>' +
    '<td class="pp-img-cell" width="45%" valign="middle" style="padding:0;font-size:0;line-height:0">' +
    imgHtml +
    "</td>" +
    '<td class="pp-body-cell" valign="middle" style="padding:18px 20px 18px 18px">' +
    '<p style="font-size:17px;font-weight:900;color:' +
    titleColor +
    ';margin:0 0 10px;line-height:1.3">' +
    title +
    "</p>" +
    '<p style="font-size:13px;color:' +
    subColor +
    ';margin:0 0 12px;line-height:1.5">' +
    subtitle +
    "</p>" +
    bulletsHtml +
    ctaHtml +
    "</td></tr></table></td></tr>"
  );
}

// ───────────────────────────────────────────────────────────────────
// Image / CTA / Divider blocks
// ───────────────────────────────────────────────────────────────────

export interface ImageBlockData {
  src?: string;
  imageUrl?: string;
  alt?: string;
  align?: string;
  widthPct?: number;
  link?: string;
}

export function imageBlockHtml(b: ImageBlockData | null): string {
  const src = (b && (b.src || b.imageUrl)) || "";
  if (!src) return "";
  const align = (b && b.align) || "center";
  const widthPct =
    typeof b?.widthPct === "number" && b.widthPct > 0 && b.widthPct <= 100
      ? b.widthPct
      : 100;
  const alt = escapeHtml((b && b.alt) || "");
  let img =
    '<img src="' +
    escapeHtml(src) +
    '" alt="' +
    alt +
    '" style="width:' +
    widthPct +
    '%;max-width:100%;height:auto;border-radius:6px;display:block;margin:0 auto" />';
  if (b && b.link)
    img =
      '<a href="' +
      escapeHtml(b.link) +
      '" target="_blank" style="text-decoration:none;display:block">' +
      img +
      "</a>";
  return '<tr><td align="' + align + '" style="padding:8px 20px">' + img + "</td></tr>";
}

export interface CtaBlockData {
  text?: string;
  url?: string;
  bg?: string;
  color?: string;
  align?: string;
  title?: string;
  subtitle?: string;
  bullets?: string[];
  panelBg?: string;
  panelBorder?: string;
}

export function ctaBlockHtml(input: CtaBlockData | null): string {
  const b = input ?? {};
  const text = escapeHtml(b.text || "Más información");
  const rawUrl = (b.url || "").trim();
  const hasUrl = rawUrl && rawUrl !== "#";
  const bg = b.bg || "#1d4ed8";
  const color = b.color || "#ffffff";
  const align = b.align || "center";
  const title = b.title || "";
  const subtitle = b.subtitle || "";
  const bullets = Array.isArray(b.bullets)
    ? b.bullets.filter((x) => x && String(x).trim())
    : [];
  const panelBg =
    b.panelBg && b.panelBg !== "transparent" ? b.panelBg : "";
  const panelBorder =
    b.panelBorder && b.panelBorder !== "transparent" ? b.panelBorder : "";
  let inner = "";
  if (title)
    inner +=
      '<h3 style="margin:0 0 6px;font-size:16px;font-weight:700;color:#1a1918;line-height:1.3">' +
      escapeHtml(title) +
      "</h3>";
  if (subtitle)
    inner +=
      '<p style="margin:0 0 10px;font-size:13px;color:#475569;line-height:1.5">' +
      escapeHtml(subtitle) +
      "</p>";
  if (bullets.length) {
    inner +=
      '<ul style="margin:0 0 14px;padding:0 0 0 18px;font-size:13px;color:#334155;line-height:1.55">';
    bullets.forEach((bp) => {
      inner += '<li style="margin:0 0 4px">' + escapeHtml(bp) + "</li>";
    });
    inner += "</ul>";
  }
  const buttonSharedStyle =
    "display:inline-block;padding:10px 22px;font-size:13px;font-weight:600;color:" +
    color +
    ";text-decoration:none;font-family:Helvetica,Arial,sans-serif";
  const button = hasUrl
    ? '<a href="' +
      escapeHtml(rawUrl) +
      '" target="_blank" style="' +
      buttonSharedStyle +
      '">' +
      text +
      "</a>"
    : '<!-- TODO: añadir URL al CTA antes de enviar --><span style="' +
      buttonSharedStyle +
      ';cursor:default">' +
      text +
      "</span>";
  inner +=
    '<table cellpadding="0" cellspacing="0" border="0" style="display:inline-table;margin:0 auto"><tr>' +
    '<td style="background:' +
    bg +
    ';border-radius:6px;padding:0">' +
    button +
    "</td></tr></table>";
  const panelStyle =
    (panelBg ? "background:" + panelBg + ";" : "") +
    (panelBorder ? "border:1px solid " + panelBorder + ";" : "") +
    "border-radius:8px;padding:" +
    (panelBg || panelBorder ? "16px 18px" : "0");
  if (panelBg || panelBorder) {
    return (
      '<tr><td style="padding:8px 20px"><div style="' +
      panelStyle +
      ";text-align:" +
      align +
      '">' +
      inner +
      "</div></td></tr>"
    );
  }
  return (
    '<tr><td align="' + align + '" style="padding:8px 20px">' + inner + "</td></tr>"
  );
}

export interface DividerBlockData {
  style?: "line" | "short" | "dots";
  color?: string;
  paddingV?: number;
}

export function dividerBlockHtml(b: DividerBlockData | null): string {
  const d = b ?? {};
  const style = d.style || "line";
  const color = d.color || "#e2e8f0";
  const padV =
    typeof d.paddingV === "number" ? Math.max(8, Math.min(80, d.paddingV)) : 24;
  if (style === "dots") {
    return (
      '<tr><td align="center" style="padding:' +
      padV +
      "px 20px;font-family:Helvetica,Arial,sans-serif;font-size:18px;letter-spacing:8px;color:" +
      color +
      ';line-height:1">·&nbsp;·&nbsp;·</td></tr>'
    );
  }
  if (style === "short") {
    return (
      '<tr><td align="center" style="padding:' +
      padV +
      'px 20px"><div style="display:inline-block;width:80px;height:2px;background:' +
      color +
      ';border-radius:1px;line-height:0;font-size:0">&nbsp;</div></td></tr>'
    );
  }
  return (
    '<tr><td style="padding:' +
    padV +
    'px 20px"><div style="height:1px;background:' +
    color +
    ';line-height:0;font-size:0">&nbsp;</div></td></tr>'
  );
}

// ───────────────────────────────────────────────────────────────────
// Pimpam steps
// ───────────────────────────────────────────────────────────────────

export interface PimpamStepsConfig {
  steps?: PimpamStep[];
  stepsBgColor?: string;
  stepsBorderColor?: string;
}

export function pimpamStepsHtml(
  config: PimpamStepsConfig | null,
  lang: Lang,
): string {
  // `lang` is reserved for future i18n on step labels; the original
  // v5o signature passes it through unused, so the dispatcher calls
  // every renderer uniformly. Touching the var here keeps tooling
  // quiet without dropping it from the signature.
  void lang;
  const cfg = config ?? {};
  const steps: PimpamStep[] = cfg.steps || [
    { n: "1️⃣", t: "Elige diseño", s: "Pantalla táctil" },
    { n: "2️⃣", t: "Personaliza", s: "Texto, colores…" },
    { n: "3️⃣", t: "Paga", s: "Tarjeta / QR" },
    { n: "4️⃣", t: "¡Listo!", s: "Funda en 30s" },
  ];
  const bgColor = cfg.stepsBgColor || "#fff7ed";
  const borderColor = cfg.stepsBorderColor || "#fed7aa";
  let cells = "";
  for (let i = 0; i < steps.length; i += 1) {
    const pad =
      i === 0
        ? "0 4px 0 0"
        : i === steps.length - 1
          ? "0 0 0 4px"
          : "0 4px";
    cells +=
      '<td class="step-cell" width="' +
      100 / steps.length +
      '%" valign="top" style="padding:' +
      pad +
      '">' +
      '<div style="background:' +
      bgColor +
      ";border:1px solid " +
      borderColor +
      ';border-radius:8px;padding:10px;text-align:center">' +
      '<div style="font-size:22px;margin-bottom:4px">' +
      steps[i].n +
      "</div>" +
      '<p style="font-size:10px;font-weight:800;color:#0f172a;margin:0 0 2px">' +
      steps[i].t +
      "</p>" +
      '<p style="font-size:9px;color:#64748b;margin:0">' +
      steps[i].s +
      "</p></div></td>";
  }
  return (
    '<tr><td style="padding:0 8px 16px"><table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>' +
    cells +
    "</tr></table></td></tr>"
  );
}

// ───────────────────────────────────────────────────────────────────
// CSS block (responsive media queries) + full HTML wrapper
// ───────────────────────────────────────────────────────────────────

export const CSS_BLOCK =
  "<style>" +
  "body,table,td,p,a,h1,h2,h3,h4{margin:0;padding:0;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%}" +
  "table{border-collapse:collapse;mso-table-lspace:0;mso-table-rspace:0}" +
  "img{border:0;display:block;line-height:100%;outline:none;text-decoration:none;-ms-interpolation-mode:bicubic;max-width:100%}" +
  'body{font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;background:#ffffff;color:#1e293b}' +
  "@media only screen and (max-width:600px){" +
  ".wrap{width:100%!important}.col-half,.col-third{width:100%!important;display:block!important;padding:0 0 12px 0!important}" +
  ".prod-row{display:block!important;width:100%!important}.prod-cell{display:block!important;width:100%!important;padding:0 0 16px 0!important}" +
  ".prod-cell table{width:100%!important}.pp-img-cell{display:block!important;width:100%!important}" +
  ".pp-body-cell{display:block!important;width:100%!important;padding:18px 16px!important}" +
  ".step-cell{width:50%!important;display:inline-block!important;padding:0 4px 12px!important}" +
  "}" +
  "</style>";

// ───────────────────────────────────────────────────────────────────
// generateFullHtml — main dispatcher. Walks the v2 block list,
// resolves text/hero by language, and emits one wrapped <table>.
// ───────────────────────────────────────────────────────────────────

/** Loose internal block shape the email renderer reads — kept as
 * its own interface (not `extends Block`) so legacy v2 fields with
 * looser types (`layout: string`, free-form `config`, …) don't
 * collide with the narrowed Block typings. Every field that
 * overlaps with a real Block field keeps the same name.
 */
type V2Block = {
  id: string;
  type: string;
  // legacy v2 fields the renderer keys off
  columns?: Array<{ blocks?: V2Block[] }>;
  composedId?: string;
  introText?: string;
  brandStrip?: string;
  includeHero?: boolean;
  includeSteps?: boolean;
  blockType?: string;
  config?: Record<string, unknown>;
  products?: string[] | string;
  layout?: string;
  widthPct?: number;
  blockAlign?: "left" | "center" | "right";
  src?: string;
  link?: string;
  bg?: string;
  panelBg?: string;
  panelBorder?: string;
  subtitle?: string;
  bullets?: string[];
  _richHtmlByLang?: Partial<Record<Lang, string>>;
  // Pull-through fields used by the renderer
  text?: string;
  url?: string;
  align?: string;
  brand?: string;
  product1?: string;
  product2?: string;
  product3?: string;
  fontSize?: number | string;
  _richHtml?: string;
  _sourceType?: string;
  _sourceId?: string;
  i18n?: Record<string, unknown>;
  innerBlocks?: V2Block[];
  heroBgColor?: string;
  // Hero passthrough
  heroTitle?: string;
  heroSubtitle?: string;
  heroBullets?: string[];
  heroCtaButtons?: unknown;
  heroImage?: string;
};

export function generateFullHtml(
  blocks: V2Block[],
  products: EmailProduct[],
  lang: Lang,
  brands: EmailBrand[],
  appState: ComposerAppState,
): string {
  const activeLang: Lang = lang || "es";
  let rows = "";

  const resolveText = (block: V2Block): string => {
    if (block._richHtmlByLang && block._richHtmlByLang[activeLang]) {
      return block._richHtmlByLang[activeLang]!;
    }
    if (block._richHtml != null && activeLang === "es") return block._richHtml;
    if (block._sourceType)
      return getTextInLanguage(block as unknown as Block, activeLang, appState);
    if (block.i18n)
      return (
        getLocalizedText(block as unknown as Block, "text", activeLang) ?? ""
      );
    return block.text || "";
  };

  const resolveHero = (block: V2Block) => {
    if (block._sourceType)
      return getHeroDataInLanguage(
        block as unknown as Block,
        activeLang,
        appState,
      );
    return block;
  };

  const wrapWithWidth = (
    rowsHtml: string,
    widthPct: number,
    align: V2Block["blockAlign"] | undefined,
  ) => {
    const w = typeof widthPct === "number" ? Math.max(30, Math.min(100, widthPct)) : 100;
    const a: "left" | "center" | "right" =
      align === "left" || align === "right" ? align : "center";
    if (w >= 100 && a === "center") return rowsHtml;
    return (
      '<tr><td style="padding:0">' +
      '<table width="100%" cellpadding="0" cellspacing="0" border="0">' +
      '<tr><td align="' +
      a +
      '" style="padding:0">' +
      '<table width="' +
      w +
      '%" style="width:' +
      w +
      '%" cellpadding="0" cellspacing="0" border="0" align="' +
      a +
      '">' +
      "<tbody>" +
      rowsHtml +
      "</tbody>" +
      "</table>" +
      "</td></tr></table>" +
      "</td></tr>"
    );
  };

  function renderBlock(b: V2Block): string {
    let out = "";
    // Section dispatcher
    if (b.type === ("section" as Block["type"])) {
      const cols = Array.isArray(b.columns) ? b.columns : [];
      const colCount = cols.length || 2;
      const colW = Math.floor(600 / colCount);
      out += '<tr><td style="padding:0">';
      out +=
        '<table class="section-row" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto"><tr>';
      cols.forEach((col) => {
        out +=
          '<td class="col-' +
          (colCount === 2 ? "half" : "third") +
          '" valign="top" align="left" width="' +
          colW +
          '" style="vertical-align:top;width:' +
          colW +
          'px;padding:0 6px">';
        out += '<table width="100%" cellpadding="0" cellspacing="0" border="0">';
        (col.blocks || []).forEach((ib) => {
          out += renderBlock(ib);
        });
        out += "</table>";
        out += "</td>";
      });
      out += "</tr></table></td></tr>";
      return out;
    }
    switch (b.type) {
      case "text": {
        const resolvedText = resolveText(b);
        if (resolvedText)
          out += textBlockHtml(resolvedText, {
            fontSize: b.fontSize,
            align: b.align,
          });
        break;
      }
      case "brand_strip": {
        out += brandStripHtml(b.brand || "artisjet", activeLang, brands);
        break;
      }
      case "product_single": {
        const ps = products.find((p) => p.id === b.product1);
        if (ps) out += productSingleHtml(getLocalizedEmailProduct(ps, activeLang), activeLang);
        break;
      }
      case "product_pair": {
        const p1 = products.find((p) => p.id === b.product1);
        const p2 = products.find((p) => p.id === b.product2);
        if (p1 && p2)
          out += productPairHtml(
            getLocalizedEmailProduct(p1, activeLang),
            getLocalizedEmailProduct(p2, activeLang),
            activeLang,
          );
        break;
      }
      case "product_trio": {
        const pt1 = products.find((p) => p.id === b.product1);
        const pt2 = products.find((p) => p.id === b.product2);
        const pt3 = products.find((p) => p.id === b.product3);
        if (pt1 && pt2 && pt3)
          out += productTrioHtml(
            getLocalizedEmailProduct(pt1, activeLang),
            getLocalizedEmailProduct(pt2, activeLang),
            getLocalizedEmailProduct(pt3, activeLang),
            activeLang,
          );
        break;
      }
      case "freebird":
      case "video": {
        const cfg = (b.config as FreebirdConfig | undefined) || (b as unknown as FreebirdConfig);
        out += freebirdHtml(cfg, activeLang);
        break;
      }
      case "image": {
        out += imageBlockHtml(b as unknown as ImageBlockData);
        break;
      }
      case "cta": {
        out += ctaBlockHtml(b as unknown as CtaBlockData);
        break;
      }
      case "divider": {
        const dividerB = b as V2Block & {
          style?: "line" | "short" | "dots";
          color?: string;
          paddingV?: number;
        };
        out += dividerBlockHtml({
          style: dividerB.style ?? "line",
          color: dividerB.color,
          paddingV: dividerB.paddingV ?? 24,
        });
        break;
      }
      case "pimpam_hero": {
        const heroData = resolveHero(b);
        if (heroData === b) {
          out += pimpamHeroHtml(b as PimpamHeroConfig, activeLang);
        } else {
          out += pimpamHeroHtml(heroData as PimpamHeroConfig, null);
        }
        break;
      }
      case "pimpam_steps": {
        const cfg = (b.config as PimpamStepsConfig | undefined) || (b as unknown as PimpamStepsConfig);
        out += pimpamStepsHtml(cfg, activeLang);
        break;
      }
      case "composed": {
        const ibs: V2Block[] = (b.innerBlocks as V2Block[]) || [];
        if (ibs.length === 0) {
          if (b.introText) ibs.push({ id: "_t", type: "text", text: b.introText });
          if (b.brandStrip && b.brandStrip !== "none")
            ibs.push({ id: "_b", type: "brand_strip", brand: b.brandStrip });
          if (b.includeHero) ibs.push({ id: "_h", type: "pimpam_hero" });
          const cProds = b.products || [];
          if (b.blockType === "product_trio" && cProds.length >= 3) {
            ibs.push({
              id: "_pt",
              type: "product_trio",
              product1: cProds[0],
              product2: cProds[1],
              product3: cProds[2],
            });
          } else if (b.blockType === "product_pair" && cProds.length >= 2) {
            ibs.push({
              id: "_pp",
              type: "product_pair",
              product1: cProds[0],
              product2: cProds[1],
            });
          } else if (b.blockType === "product_single" && cProds.length >= 1) {
            ibs.push({ id: "_ps", type: "product_single", product1: cProds[0] });
          }
          if (b.includeSteps) ibs.push({ id: "_s", type: "pimpam_steps" });
        }
        ibs.forEach((ib) => {
          out += renderBlock(ib as V2Block);
        });
        break;
      }
      default:
        break;
    }
    if (b.type !== ("section" as Block["type"])) {
      const w = typeof b.widthPct === "number" ? b.widthPct : 100;
      const a = b.blockAlign || "center";
      if (w < 100 || a !== "center") {
        out = wrapWithWidth(out, w, a);
      }
    }
    return out;
  }

  for (const block of blocks) rows += renderBlock(block);

  return (
    "<html><head>" +
    CSS_BLOCK +
    '</head><body style="font-family:\'Helvetica Neue\',Helvetica,Arial,sans-serif;margin:0;padding:0;background:#ffffff;color:#1e293b">' +
    '<table class="wrap" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto">' +
    rows +
    "</table></body></html>"
  );
}

// ───────────────────────────────────────────────────────────────────
// High-level entry: takes the CRM catalog + Block[] and produces HTML.
// ───────────────────────────────────────────────────────────────────

export function renderEmailHtml(
  blocks: Block[],
  appState: ComposerAppState,
  lang: Lang,
): string {
  const products = appState.products.map(toEmailProduct);
  const brands = appState.brands.map(toEmailBrand);
  return generateFullHtml(blocks as V2Block[], products, lang, brands, appState);
}

// ───────────────────────────────────────────────────────────────────
// UTM tracking
// ───────────────────────────────────────────────────────────────────

function detectCampaignBrand(blocks: Block[], appState: ComposerAppState): string {
  const brandCounts: Record<string, number> = {};
  const products = appState.products;
  const inc = (b: string | undefined) => {
    if (!b) return;
    brandCounts[b] = (brandCounts[b] || 0) + 1;
  };
  blocks.forEach((b) => {
    if (!b) return;
    if (b.brand) inc(b.brand);
    if (b.type && b.type.startsWith("brand_")) inc(b.type.replace("brand_", ""));
    if (b.product1) {
      const p = products.find((x) => x.id === b.product1);
      if (p) inc(p.brand_id);
    }
    if (b.product2) {
      const p = products.find((x) => x.id === b.product2);
      if (p) inc(p.brand_id);
    }
    if (b.product3) {
      const p = products.find((x) => x.id === b.product3);
      if (p) inc(p.brand_id);
    }
  });
  let top = "mix";
  let max = 0;
  for (const k of Object.keys(brandCounts)) {
    if (brandCounts[k] > max) {
      max = brandCounts[k];
      top = k;
    }
  }
  return top;
}

export function generateCampaignName(
  blocks: Block[],
  lang: Lang,
  appState: ComposerAppState,
  customTitle?: string,
): string {
  const now = new Date();
  const yy = String(now.getFullYear());
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  const brand = detectCampaignBrand(blocks, appState);
  const slug = customTitle
    ? "-" +
      String(customTitle)
        .toLowerCase()
        .replace(/[áàäâ]/g, "a")
        .replace(/[éèëê]/g, "e")
        .replace(/[íìïî]/g, "i")
        .replace(/[óòöô]/g, "o")
        .replace(/[úùüû]/g, "u")
        .replace(/[ñ]/g, "n")
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "")
        .slice(0, 40)
    : "";
  return yy + mm + dd + "-" + brand + "-" + (lang || "es") + slug;
}

function slugifyUser(s: string | undefined): string {
  if (!s) return "";
  return String(s)
    .toLowerCase()
    .replace(/[áàäâ]/g, "a")
    .replace(/[éèëê]/g, "e")
    .replace(/[íìïî]/g, "i")
    .replace(/[óòöô]/g, "o")
    .replace(/[úùüû]/g, "u")
    .replace(/[ñ]/g, "n")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 30);
}

export function addUtmParams(
  html: string,
  campaign: string,
  lang: Lang,
  userSlug?: string,
): string {
  if (!html) return "";
  let utmBase =
    "utm_source=email&utm_medium=bomedia&utm_campaign=" +
    encodeURIComponent(campaign) +
    "&utm_term=" +
    encodeURIComponent(lang || "es");
  if (userSlug) utmBase += "&utm_content=" + encodeURIComponent(userSlug);
  return html.replace(
    /<a\s([^>]*?)href="([^"]+)"([^>]*?)>/gi,
    (match: string, pre: string, url: string, post: string) => {
      if (/^(mailto:|tel:|javascript:|data:|#)/i.test(url)) return match;
      if (/[?&]utm_(source|campaign|medium|term|content)=/i.test(url)) return match;
      const separator = url.indexOf("?") >= 0 ? "&" : "?";
      return '<a ' + pre + 'href="' + url + separator + utmBase + '"' + post + ">";
    },
  );
}

export interface CurrentUserLike {
  id?: string;
  name?: string;
}

export function renderEmailHtmlWithTracking(
  blocks: Block[],
  appState: ComposerAppState,
  lang: Lang,
  customTitle?: string,
  currentUser?: CurrentUserLike,
): { html: string; campaign: string; userSlug: string } {
  const html = renderEmailHtml(blocks, appState, lang);
  const campaign = generateCampaignName(blocks, lang, appState, customTitle);
  const userSlug = currentUser
    ? slugifyUser(currentUser.id || currentUser.name)
    : "";
  return { html: addUtmParams(html, campaign, lang, userSlug), campaign, userSlug };
}

// `getLocalizedProduct` re-exported for inspector helpers that need
// the i18n-aware view of a product without going through email gen.
export { i18nGetLocalizedProduct as getLocalizedProduct };
