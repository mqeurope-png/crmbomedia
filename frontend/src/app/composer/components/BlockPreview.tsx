"use client";

/**
 * Visual block previews — ported from `bomedia-v4/app-compositor.jsx`.
 *
 * Each function returns the inline preview shown inside a `BlockCard`
 * on the canvas. The Composer original renders product thumbnails,
 * brand logos, dividers, CTA buttons and hero panels straight in the
 * canvas; the Fase-2.1 BlockCard was reduced to a textual label,
 * which made the canvas read as a plain list. Restoring the visual
 * preview makes the editor look like the original.
 *
 * Ported one-to-one (no rewrites):
 *   - `MiniProduct` (lines 550-608): product card with image / badge /
 *     name / desc / price.
 *   - `BrandStripPreview` (lines 611-632): brand logo + localized url.
 *   - The switch tail of `BlockCard` (lines 1010-1320): divider /
 *     image / cta / brand strip / product_single / product_pair /
 *     product_trio / composed / pimpam_hero / pimpam_steps / freebird.
 *
 * Text blocks render via the inline rich-HTML preview path of the
 * original `InlineTextBlock` (read-only mode); the full inline editor
 * + AI popover land in 2.2 with the Inspector.
 */

import { getLocalizedProduct, getLocalizedText } from "../lib/i18n";
import { sanitizeHtml } from "../lib/security";
import type {
  Block,
  ComposerAppState,
  ComposerBrand,
  ComposerProduct,
  Lang,
} from "../lib/types";

interface MiniProductProps {
  p: ComposerProduct | null | undefined;
  lang: Lang;
  compact?: boolean;
}

export function MiniProduct({ p, lang, compact }: MiniProductProps) {
  if (!p) {
    return (
      <div
        style={{
          border: "1px dashed var(--border-strong)",
          borderRadius: "var(--r-sm)",
          padding: 12,
          textAlign: "center",
          fontSize: 11,
          color: "var(--text-subtle)",
        }}
      >
        Producto no seleccionado
      </div>
    );
  }
  const lp = getLocalizedProduct(p, lang);
  const imgBoxH = compact ? 70 : 100;
  return (
    <div
      style={{
        border:
          "1px solid " +
          (lp.brand_id === "pimpam" ? "#fed7aa" : "var(--border)"),
        borderRadius: "var(--r-sm)",
        background: lp.brand_id === "pimpam" ? "#fff7ed" : "var(--bg-panel)",
        padding: compact ? 8 : 10,
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          height: imgBoxH,
          marginBottom: 6,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={lp.img}
          alt=""
          style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }}
        />
      </div>
      {lp.badge && (
        <span
          style={{
            display: "inline-block",
            fontSize: 8,
            fontWeight: 800,
            letterSpacing: 1,
            textTransform: "uppercase",
            padding: "2px 6px",
            borderRadius: 10,
            background: lp.badge_bg || "#f1f5f9",
            color: lp.badge_color || "#475569",
            marginBottom: 4,
          }}
        >
          {lp.badge}
        </span>
      )}
      <div
        style={{
          fontWeight: 800,
          fontSize: compact ? 11 : 12,
          color: "var(--text)",
        }}
      >
        {lp.name}
      </div>
      {!compact && (
        <div
          style={{
            fontSize: 10,
            color: "var(--text-muted)",
            marginTop: 3,
            lineHeight: 1.4,
          }}
        >
          {lp.description}
        </div>
      )}
      <div
        style={{
          fontWeight: 800,
          fontSize: compact ? 11 : 13,
          color: lp.accent || "var(--text)",
          marginTop: 6,
          textAlign: "center",
        }}
      >
        {lp.price}
      </div>
    </div>
  );
}

interface BrandStripPreviewProps {
  brandId: string;
  lang: Lang;
  brands: ComposerBrand[];
}

export function BrandStripPreview({
  brandId,
  lang,
  brands,
}: BrandStripPreviewProps) {
  const b = brands.find((x) => x.id === brandId);
  if (!b) {
    return (
      <div style={{ padding: 12, fontSize: 12, color: "var(--text-subtle)" }}>
        Marca no encontrada: {brandId}
      </div>
    );
  }
  // url + urlLabel were folded into i18n_json during the seed; pull
  // them out so the strip can show the per-lang link the original
  // shows.
  const i18nWithUrls = b.i18n as {
    url?: Record<string, string> | string;
    urlLabel?: Record<string, string> | string;
  };
  const urlRaw = i18nWithUrls?.url;
  const urlLabelRaw = i18nWithUrls?.urlLabel;
  const url =
    typeof urlRaw === "object" && urlRaw !== null
      ? urlRaw[lang] || urlRaw.es || ""
      : urlRaw || "";
  const urlLabel =
    typeof urlLabelRaw === "object" && urlLabelRaw !== null
      ? urlLabelRaw[lang] || urlLabelRaw.es || ""
      : urlLabelRaw || "";
  const logoHeight = parseInt(b.logo_height || "", 10) || 22;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "12px 4px",
        borderBottom: `1px solid ${b.divider || "var(--border)"}`,
      }}
    >
      {b.logo ? (
        /* eslint-disable-next-line @next/next/no-img-element */
        <img
          src={b.logo}
          alt={b.label}
          style={{
            maxHeight: logoHeight + "px",
            maxWidth: 180,
            width: "auto",
            height: "auto",
          }}
        />
      ) : (
        <strong style={{ color: b.color, fontSize: 14 }}>{b.label}</strong>
      )}
      {url ? (
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          style={{
            marginLeft: "auto",
            fontSize: 12,
            fontWeight: 700,
            color: b.color,
            textDecoration: "none",
            whiteSpace: "nowrap",
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {urlLabel}
        </a>
      ) : null}
    </div>
  );
}

interface InlineTextPreviewProps {
  block: Block;
  lang: Lang;
  appState: ComposerAppState;
}

export function InlineTextPreview({
  block,
  lang,
  appState,
}: InlineTextPreviewProps) {
  // Resolution chain (matches the original `getTextInLanguage` chain):
  //  1. `_overrides[lang]` if set
  //  2. prewritten text body via `_sourceId`
  //  3. block.text
  const sourceText = block._sourceId
    ? appState.prewrittenTexts.find((t) => t.id === block._sourceId)
    : null;
  const localized = sourceText
    ? getLocalizedText(sourceText, "text", lang)
    : block.text || "";
  const override =
    block._overrides && typeof block._overrides[lang] === "string"
      ? (block._overrides[lang] as string)
      : "";
  const plainSeed = override || localized || "";
  const richByLang = (block as Block & { _richHtmlByLang?: Partial<Record<Lang, string>> })
    ._richHtmlByLang;
  const legacyRich =
    lang === "es" && typeof block._richHtml === "string"
      ? block._richHtml
      : null;
  const storedRich = richByLang?.[lang] ?? legacyRich;
  const richHtml =
    storedRich != null
      ? storedRich
      : plainSeed
        ? "<p>" +
          String(plainSeed)
            .split("\n")
            .filter(Boolean)
            .join("</p><p>") +
          "</p>"
        : "";
  const fontSize = block.fontSize || "14";
  const sanitized = sanitizeHtml(richHtml || "");
  return (
    <div className="block-text">
      <div
        className="block-text-rich"
        style={{
          padding: "8px 0",
          minHeight: 60,
          textAlign: (block.align as "left" | "center" | "right") || "left",
          fontSize: fontSize + "px",
        }}
        dangerouslySetInnerHTML={{
          __html:
            sanitized ||
            '<span style="color:var(--text-subtle); font-style:italic">Texto vacío</span>',
        }}
      />
    </div>
  );
}

interface BlockPreviewProps {
  block: Block;
  lang: Lang;
  appState: ComposerAppState;
}

/** Switch over `block.type` and emit the visual preview for the
 * matched kind. Falls back to a small "type label" pill for the
 * types that don't have a dedicated renderer yet. */
export function BlockPreview({ block, lang, appState }: BlockPreviewProps) {
  switch (block.type) {
    case "text":
    case "text_from_library":
      return <InlineTextPreview block={block} lang={lang} appState={appState} />;

    case "brand_strip":
      return (
        <BrandStripPreview
          brandId={block.brand || "artisjet"}
          lang={lang}
          brands={appState.brands}
        />
      );

    case "product_single": {
      const p = appState.products.find((x) => x.id === block.product1);
      return (
        <div style={{ maxWidth: 320, margin: "0 auto" }}>
          <MiniProduct p={p ?? null} lang={lang} />
        </div>
      );
    }

    case "product_pair": {
      const p1 = appState.products.find((x) => x.id === block.product1);
      const p2 = appState.products.find((x) => x.id === block.product2);
      return (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 8,
            alignItems: "start",
          }}
        >
          <MiniProduct p={p1 ?? null} lang={lang} />
          <MiniProduct p={p2 ?? null} lang={lang} />
        </div>
      );
    }

    case "product_trio": {
      const p1 = appState.products.find((x) => x.id === block.product1);
      const p2 = appState.products.find((x) => x.id === block.product2);
      const p3 = appState.products.find((x) => x.id === block.product3);
      return (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr 1fr",
            gap: 6,
            alignItems: "start",
          }}
        >
          <MiniProduct p={p1 ?? null} lang={lang} compact />
          <MiniProduct p={p2 ?? null} lang={lang} compact />
          <MiniProduct p={p3 ?? null} lang={lang} compact />
        </div>
      );
    }

    case "image": {
      const src = block.imageUrl || "";
      return (
        <div
          style={{
            padding: 12,
            textAlign:
              (block.align as "left" | "center" | "right") || "center",
          }}
        >
          {src ? (
            /* eslint-disable-next-line @next/next/no-img-element */
            <img
              src={src}
              alt={block.alt || ""}
              style={{
                maxWidth: "100%",
                maxHeight: 200,
                borderRadius: 6,
                display: "inline-block",
              }}
            />
          ) : (
            <div
              style={{
                padding: "30px 20px",
                background: "var(--bg-sunken)",
                border: "1px dashed var(--border-strong)",
                borderRadius: 6,
                color: "var(--text-muted)",
                fontSize: 12,
              }}
            >
              Imagen sin URL — selecciona una en el inspector.
            </div>
          )}
        </div>
      );
    }

    case "cta": {
      return (
        <div style={{ padding: 12 }}>
          <div
            style={{
              textAlign:
                (block.align as "left" | "center" | "right") || "center",
            }}
          >
            {block.text && (
              <div>
                <span
                  style={{
                    display: "inline-block",
                    padding: "10px 22px",
                    fontSize: 13,
                    fontWeight: 600,
                    color: block.textColor || "#fff",
                    background: block.bgColor || "#1d4ed8",
                    borderRadius: 6,
                    textDecoration: "none",
                  }}
                >
                  {block.text}
                </span>
              </div>
            )}
            {!block.url && (
              <div
                style={{
                  marginTop: 6,
                  fontSize: 11,
                  color: "var(--text-muted)",
                }}
              >
                Sin URL — añádela en el inspector.
              </div>
            )}
          </div>
        </div>
      );
    }

    case "divider_line":
    case "divider_short":
    case "divider_dots": {
      const style: "line" | "short" | "dots" =
        block.type === "divider_short"
          ? "short"
          : block.type === "divider_dots"
            ? "dots"
            : "line";
      const color = block.bgColor || "#e2e8f0";
      const padV = 24;
      if (style === "dots")
        return (
          <div
            style={{
              padding: padV + "px 20px",
              textAlign: "center",
              letterSpacing: 8,
              fontSize: 18,
              color,
              lineHeight: 1,
              fontFamily: "Helvetica,Arial,sans-serif",
            }}
          >
            ·&nbsp;·&nbsp;·
          </div>
        );
      if (style === "short")
        return (
          <div style={{ padding: padV + "px 20px", textAlign: "center" }}>
            <div
              style={{
                display: "inline-block",
                width: 80,
                height: 2,
                background: color,
                borderRadius: 1,
              }}
            />
          </div>
        );
      return (
        <div style={{ padding: padV + "px 20px" }}>
          <div style={{ height: 1, background: color }} />
        </div>
      );
    }

    case "pimpam_hero": {
      const title = block.heroTitle || "Hero sin título";
      const subtitle = block.heroSubtitle || "";
      const bullets = block.heroBullets || [];
      return (
        <div
          style={{
            padding: 12,
            background: block.heroBgColor || "var(--bg-sunken)",
            borderRadius: 8,
            display: "flex",
            gap: 12,
          }}
        >
          {block.heroImage ? (
            /* eslint-disable-next-line @next/next/no-img-element */
            <img
              src={block.heroImage}
              alt=""
              style={{
                width: 100,
                height: 80,
                objectFit: "cover",
                borderRadius: 6,
                flexShrink: 0,
              }}
            />
          ) : null}
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontWeight: 800, fontSize: 14 }}>{title}</div>
            {subtitle && (
              <div
                style={{
                  fontSize: 12,
                  color: "var(--text-muted)",
                  marginTop: 4,
                }}
              >
                {subtitle}
              </div>
            )}
            {bullets.length > 0 && (
              <ul
                style={{
                  margin: "8px 0 0",
                  padding: "0 0 0 18px",
                  fontSize: 11,
                  color: "var(--text-muted)",
                }}
              >
                {bullets.slice(0, 3).map((b, i) => (
                  <li key={i}>{b}</li>
                ))}
              </ul>
            )}
          </div>
        </div>
      );
    }

    case "pimpam_steps": {
      const steps = block.steps || [
        { n: "1", t: "Elige diseño", s: "Pantalla táctil" },
        { n: "2", t: "Personaliza", s: "Texto, colores…" },
        { n: "3", t: "Paga", s: "Tarjeta / QR" },
        { n: "4", t: "¡Listo!", s: "Funda en 30s" },
      ];
      return (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: `repeat(${steps.length}, 1fr)`,
            gap: 6,
          }}
        >
          {steps.map((s, i) => (
            <div
              key={i}
              style={{
                background: block.stepsBgColor || "#fff7ed",
                border: `1px solid ${block.stepsBorderColor || "#fed7aa"}`,
                borderRadius: 6,
                padding: 8,
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: 18 }}>{s.n}</div>
              <div style={{ fontSize: 11, fontWeight: 800 }}>{s.t}</div>
              <div style={{ fontSize: 9, color: "#64748b" }}>{s.s}</div>
            </div>
          ))}
        </div>
      );
    }

    case "freebird":
    case "video": {
      const url = block.youtubeUrl || "";
      const thumb =
        block.thumbnailOverride ||
        (url.match(/(?:v=|youtu\.be\/)([^&\n?#]+)/)?.[1]
          ? `https://img.youtube.com/vi/${url.match(/(?:v=|youtu\.be\/)([^&\n?#]+)/)?.[1]}/hqdefault.jpg`
          : "");
      return (
        <div
          style={{
            background: "#0f172a",
            borderRadius: 8,
            overflow: "hidden",
            padding: 8,
            textAlign: "center",
          }}
        >
          {thumb ? (
            /* eslint-disable-next-line @next/next/no-img-element */
            <img
              src={thumb}
              alt="Vídeo"
              style={{
                maxWidth: 320,
                width: "100%",
                opacity: 0.85,
                display: "block",
                margin: "0 auto",
              }}
            />
          ) : (
            <div style={{ color: "#94a3b8", fontSize: 12 }}>Sin URL de vídeo</div>
          )}
          <div
            style={{
              color: "#93c5fd",
              fontSize: 12,
              fontWeight: 700,
              padding: "6px 0",
            }}
          >
            ▶ Ver vídeo
          </div>
        </div>
      );
    }

    case "composed": {
      const composed = appState.composedBlocks.find(
        (c) => c.id === block._sourceId,
      );
      const title = composed?.title ?? "Bloque compuesto";
      const intro = composed?.introText ?? composed?.intro_text ?? "";
      const productIds = composed?.products ?? [];
      const products = productIds
        .map((pid) => appState.products.find((p) => p.id === pid))
        .filter(Boolean) as ComposerProduct[];
      return (
        <div
          style={{
            border: "1px solid var(--border)",
            borderRadius: 8,
            padding: 10,
            background: "var(--bg-panel)",
          }}
        >
          <div style={{ fontWeight: 800, fontSize: 12 }}>{title}</div>
          {intro && (
            <div
              style={{
                fontSize: 11,
                color: "var(--text-muted)",
                marginTop: 4,
              }}
            >
              {intro}
            </div>
          )}
          {products.length > 0 && (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: `repeat(${Math.min(products.length, 3)}, 1fr)`,
                gap: 6,
                marginTop: 8,
              }}
            >
              {products.slice(0, 3).map((p) => (
                <MiniProduct key={p.id} p={p} lang={lang} compact />
              ))}
            </div>
          )}
        </div>
      );
    }

    case "section_2col":
    case "section_3col": {
      const cols = block.type === "section_3col" ? 3 : 2;
      const inner = block.innerBlocks ?? [];
      return (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: `repeat(${cols}, 1fr)`,
            gap: 8,
            border: "1px dashed var(--border-strong)",
            borderRadius: 8,
            padding: 8,
          }}
        >
          {Array.from({ length: cols }).map((_, ci) => {
            const child = inner[ci];
            return (
              <div
                key={ci}
                style={{
                  background: "var(--bg-sunken)",
                  borderRadius: 6,
                  padding: 8,
                  minHeight: 60,
                  fontSize: 11,
                  color: "var(--text-muted)",
                  textAlign: "center",
                }}
              >
                {child ? (
                  <BlockPreview
                    block={child}
                    lang={lang}
                    appState={appState}
                  />
                ) : (
                  `Columna ${ci + 1}`
                )}
              </div>
            );
          })}
        </div>
      );
    }

    default:
      return null;
  }
}
