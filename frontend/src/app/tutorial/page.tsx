"use client";

/**
 * PR-Manual-Tutorial-CRM. La sección "Tutorial" del sidebar renderiza
 * el manual de usuario maquetado por Bart (Claude Design) dentro de
 * un iframe que carga `/manual/manual.html` desde `public/`.
 *
 * Enfoque iframe:
 *  - Preserva el branding, CSS custom, TOC sticky y FABs del manual
 *    sin convertir nada a React.
 *  - Cuando Bart regenere el manual, basta con sustituir el HTML +
 *    assets en `public/manual/` (sin tocar este componente).
 *  - El sandbox permite scripts (support.js anima la TOC) + same-origin
 *    (carga assets relativos) + popups (links externos).
 */
import { Printer } from "lucide-react";
import { useRef } from "react";

export default function TutorialPage() {
  const frameRef = useRef<HTMLIFrameElement>(null);

  function handlePrint() {
    // El manual ya tiene `@media print` configurado para impresión
    // limpia. Disparar la print desde el documento interno respeta
    // ese CSS — desde la ventana padre se imprime el chrome del CRM.
    const win = frameRef.current?.contentWindow;
    if (win) {
      try {
        win.focus();
        win.print();
      } catch {
        // Si el browser bloquea, fallback al print del documento padre.
        window.print();
      }
    }
  }

  return (
    <div className="tutorial-shell">
      <div className="tutorial-toolbar">
        <span className="tutorial-toolbar-title">Manual de usuario</span>
        <button
          type="button"
          className="button secondary small tutorial-print"
          onClick={handlePrint}
          title="Imprimir el manual con su CSS específico"
        >
          <Printer size={14} aria-hidden /> Imprimir manual
        </button>
      </div>
      <iframe
        ref={frameRef}
        src="/manual/manual.html"
        title="Manual de usuario BoHub CRM"
        className="tutorial-frame"
        sandbox="allow-scripts allow-same-origin allow-popups"
      />
    </div>
  );
}
