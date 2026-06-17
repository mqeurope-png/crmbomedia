"use client";

/**
 * BoHub CRM logo, reconstructed geométricamente desde la guía de
 * estilo. Tres componentes visuales en uno:
 *
 *   - una órbita circular con 3 arcos separados (cada arco un color de
 *     la paleta primaria),
 *   - 3 nodos posicionados a las 12, 4 y 8 en punto,
 *   - un símbolo "target" en el centro (anillo + disco filled).
 *
 * Variants:
 *   - `icon`: el isotipo solo (cuadrado), favicons y sidebar collapsed.
 *   - `horizontal`: isotipo + "BoHub" + "CRM" alineados horizontal,
 *     para el sidebar expanded y el header del login.
 *   - `monochrome`: misma estructura en negro/slate dark, para fondos
 *     coloreados o contextos sin color (emails, PDFs).
 *
 * El SVG se renderiza inline (no externo) para que herede `color:` de
 * CSS y se vea nítido a cualquier zoom sin pedir un asset extra.
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

// Paleta — alineada con los CSS vars de styles.css. Mantener
// sincronizado si Bart afina los hex.
const COLOR_PRIMARY = "#2563EB"; // azul nodo 8 + arco upper-left
const COLOR_LIGHT = "#0EA5E9"; // azul cielo arco upper-right
const COLOR_TEAL = "#14B8A6"; // teal nodo 4 + arco bottom
const COLOR_DARK = "#0F172A"; // slate dark nodo 12 + target centro

// Geometría — viewBox 0 0 100 100, center (50,50). Las funciones
// `nodeAt` y `arcPoint` se usan para mantener la simetría exacta.
const ORBIT_RADIUS = 35;
const NODE_RADIUS = 6.5;
const NODE_GAP_DEG = 18; // separación arco↔nodo a cada lado

function polarToCartesian(angleDeg: number, radius: number) {
  // 0° = 12 en punto, sentido horario. SVG y crece hacia abajo, así
  // que la fórmula clásica clock es: x = cx + r·sin(θ), y = cy − r·cos(θ).
  const rad = (angleDeg * Math.PI) / 180;
  return {
    x: 50 + radius * Math.sin(rad),
    y: 50 - radius * Math.cos(rad),
  };
}

function arcPath(fromDeg: number, toDeg: number): string {
  const start = polarToCartesian(fromDeg, ORBIT_RADIUS);
  const end = polarToCartesian(toDeg, ORBIT_RADIUS);
  // largeArcFlag=0 (cada arco < 180°), sweepFlag=1 (sentido horario).
  return `M ${start.x.toFixed(2)} ${start.y.toFixed(2)} A ${ORBIT_RADIUS} ${ORBIT_RADIUS} 0 0 1 ${end.x.toFixed(2)} ${end.y.toFixed(2)}`;
}

// 3 arcos + 3 nodos. Los ángulos están medidos desde las 12 horarias.
const ARCS = [
  { from: 0 + NODE_GAP_DEG, to: 120 - NODE_GAP_DEG, colorKey: "light" }, // 12 → 4
  { from: 120 + NODE_GAP_DEG, to: 240 - NODE_GAP_DEG, colorKey: "teal" }, // 4 → 8
  { from: 240 + NODE_GAP_DEG, to: 360 - NODE_GAP_DEG, colorKey: "blue" }, // 8 → 12
] as const;

const NODES = [
  { deg: 0, colorKey: "dark" }, // 12 en punto
  { deg: 120, colorKey: "teal" }, // 4 en punto
  { deg: 240, colorKey: "blue" }, // 8 en punto
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

function IsotypeSvg({ monochrome }: { monochrome: boolean }) {
  return (
    <>
      {/* Arcos de la órbita. stroke-linecap=round redondea las
          puntas para que los gaps no queden "cortados". */}
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
      {/* Nodos */}
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
      {/* Target central: anillo + disco filled + tiny inner dot.
          La diana resume la idea de "centralizar leads" en un foco. */}
      <circle
        cx={50}
        cy={50}
        r={10}
        fill="none"
        stroke={colorFor("dark", monochrome)}
        strokeWidth={3}
      />
      <circle cx={50} cy={50} r={4.5} fill={colorFor("dark", monochrome)} />
    </>
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
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 100 100"
        width={size}
        height={size}
        className={className}
        role="img"
        aria-label={title}
        style={style}
      >
        <title>{title}</title>
        <IsotypeSvg monochrome={false} />
      </svg>
    );
  }

  // horizontal + monochrome — mismo lockup, sólo cambia la paleta.
  // viewBox 0 0 320 100: isotipo de 100×100 + wordmark a la derecha.
  const wordmarkInk = monochrome ? "currentColor" : COLOR_DARK;
  const wordmarkAccent = monochrome ? "currentColor" : "#475569";
  // Altura calculada vía aspect-ratio para que el caller pase solo
  // `size` (= altura) y obtenga el ancho correcto.
  const aspect = 320 / 100;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 320 100"
      width={size * aspect}
      height={size}
      className={className}
      role="img"
      aria-label={title}
      style={style}
    >
      <title>{title}</title>
      <IsotypeSvg monochrome={monochrome} />
      {/* Wordmark: "BoHub" en peso 800, "CRM" en peso 500 + tracking.
          system-ui fallback para que se vea bien aun sin Inter cargada. */}
      <text
        x={115}
        y={62}
        fontFamily="var(--font-base, 'Inter', system-ui, sans-serif)"
        fontWeight={800}
        fontSize={46}
        fill={wordmarkInk}
        letterSpacing="-1.2"
      >
        BoHub
      </text>
      <text
        x={245}
        y={62}
        fontFamily="var(--font-base, 'Inter', system-ui, sans-serif)"
        fontWeight={500}
        fontSize={28}
        fill={wordmarkAccent}
        letterSpacing="2"
      >
        CRM
      </text>
    </svg>
  );
}
