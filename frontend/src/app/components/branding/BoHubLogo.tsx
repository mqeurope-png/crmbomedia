"use client";

/**
 * BoHub CRM logo. Tres variantes:
 *
 *   - `icon`: el isotipo solo (sólo SVG cuadrado).
 *   - `horizontal`: isotipo + wordmark "BoHub CRM" alineados horizontal.
 *     El wordmark se renderiza con `<span>` HTML, NO con `<text>` SVG —
 *     así el browser usa las métricas reales de Inter y no se solapan
 *     los textos como pasaba en PR-A.
 *   - `monochrome`: misma estructura en `currentColor`, para fondos de
 *     color o exportes a PDF.
 *
 * El isotipo se genera geometricamente (3 arcos + 3 nodos + target):
 *   - órbita radius 35 sobre un viewBox 100×100,
 *   - nodos a las 12 / 4 / 8 horarias,
 *   - target central (anillo + disco filled).
 */
import type { CSSProperties } from "react";

export type BoHubLogoVariant = "icon" | "horizontal" | "monochrome";

type Props = {
  variant?: BoHubLogoVariant;
  size?: number;
  className?: string;
  title?: string;
  style?: CSSProperties;
};

const COLOR_PRIMARY = "#2563EB";
const COLOR_LIGHT = "#0EA5E9";
const COLOR_TEAL = "#14B8A6";
const COLOR_DARK = "#0F172A";

const ORBIT_RADIUS = 35;
const NODE_RADIUS = 6.5;
const NODE_GAP_DEG = 18;

function polarToCartesian(angleDeg: number, radius: number) {
  // 0° = 12 en punto, sentido horario. SVG y crece hacia abajo.
  const rad = (angleDeg * Math.PI) / 180;
  return {
    x: 50 + radius * Math.sin(rad),
    y: 50 - radius * Math.cos(rad),
  };
}

function arcPath(fromDeg: number, toDeg: number): string {
  const start = polarToCartesian(fromDeg, ORBIT_RADIUS);
  const end = polarToCartesian(toDeg, ORBIT_RADIUS);
  return `M ${start.x.toFixed(2)} ${start.y.toFixed(2)} A ${ORBIT_RADIUS} ${ORBIT_RADIUS} 0 0 1 ${end.x.toFixed(2)} ${end.y.toFixed(2)}`;
}

const ARCS = [
  { from: 0 + NODE_GAP_DEG, to: 120 - NODE_GAP_DEG, colorKey: "light" },
  { from: 120 + NODE_GAP_DEG, to: 240 - NODE_GAP_DEG, colorKey: "teal" },
  { from: 240 + NODE_GAP_DEG, to: 360 - NODE_GAP_DEG, colorKey: "blue" },
] as const;

const NODES = [
  { deg: 0, colorKey: "dark" },
  { deg: 120, colorKey: "teal" },
  { deg: 240, colorKey: "blue" },
] as const;

function colorFor(
  key: "blue" | "light" | "teal" | "dark",
  monochrome: boolean,
): string {
  if (monochrome) return "currentColor";
  return {
    blue: COLOR_PRIMARY,
    light: COLOR_LIGHT,
    teal: COLOR_TEAL,
    dark: COLOR_DARK,
  }[key];
}

function IsotypeSvg({
  monochrome,
  size,
  title,
}: {
  monochrome: boolean;
  size: number;
  title: string;
}) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 100 100"
      width={size}
      height={size}
      role="img"
      aria-label={title}
      style={{ flexShrink: 0 }}
    >
      <title>{title}</title>
      {ARCS.map((arc, idx) => (
        <path
          key={`arc-${idx}`}
          d={arcPath(arc.from, arc.to)}
          fill="none"
          stroke={colorFor(arc.colorKey, monochrome)}
          strokeWidth={7}
          strokeLinecap="round"
        />
      ))}
      {NODES.map((node, idx) => {
        const p = polarToCartesian(node.deg, ORBIT_RADIUS);
        return (
          <circle
            key={`node-${idx}`}
            cx={p.x}
            cy={p.y}
            r={NODE_RADIUS}
            fill={colorFor(node.colorKey, monochrome)}
          />
        );
      })}
      <circle
        cx={50}
        cy={50}
        r={10}
        fill="none"
        stroke={colorFor("dark", monochrome)}
        strokeWidth={3}
      />
      <circle cx={50} cy={50} r={4.5} fill={colorFor("dark", monochrome)} />
    </svg>
  );
}

export function BoHubLogo({
  variant = "horizontal",
  size = 36,
  className,
  title = "BoHub CRM",
  style,
}: Props) {
  const monochrome = variant === "monochrome";

  if (variant === "icon") {
    return (
      <IsotypeSvg monochrome={false} size={size} title={title} />
    );
  }

  // horizontal + monochrome: flex con icono SVG y wordmark HTML. El
  // wordmark se renderiza con `<span>` para que herede la fuente
  // Inter cargada por next/font (ver layout.tsx) y respete las
  // métricas reales de cada glyph. PR-A intentaba hacerlo con
  // `<text>` SVG, pero la fuente del SVG cae al system default y los
  // anchors x= hardcodeados solapaban "BoHub" y "CRM".
  const ink = monochrome ? "currentColor" : COLOR_DARK;
  const accent = monochrome ? "currentColor" : "#475569";

  return (
    <span
      className={className}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: size * 0.36,
        fontFamily:
          "var(--font-base, 'Inter', system-ui, -apple-system, sans-serif)",
        lineHeight: 1,
        ...style,
      }}
      aria-label={title}
    >
      <IsotypeSvg monochrome={monochrome} size={size} title={title} />
      <span
        style={{
          display: "inline-flex",
          alignItems: "baseline",
          gap: size * 0.24,
          fontSize: size * 0.68,
          letterSpacing: "-0.02em",
          color: ink,
          fontWeight: 800,
        }}
      >
        BoHub
        <span
          style={{
            fontSize: size * 0.48,
            letterSpacing: "0.08em",
            color: accent,
            fontWeight: 500,
          }}
        >
          CRM
        </span>
      </span>
    </span>
  );
}
