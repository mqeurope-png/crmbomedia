import Link from "next/link";
import type { ReactNode } from "react";
import "./proto.css";

/** Sprint 0 — layout del prototipo ERP. Desechable. */
export default function ErpPrototypeLayout({ children }: { children: ReactNode }) {
  return (
    <div className="erp-shell">
      <div className="erp-banner">
        ⚠️ PROTOTIPO Sprint 0 — datos ficticios, sin backend. Solo para validar UX con Bart.
      </div>
      <nav className="erp-nav" aria-label="Prototipo ERP">
        <Link href="/erp-prototype/orders">Pedidos</Link>
        <Link href="/erp-prototype/sat">Cola SAT</Link>
        <Link href="/erp-prototype/exceptions">Excepciones</Link>
        <Link href="/erp-prototype/states">Estados</Link>
        <Link href="/erp-prototype/company">Empresa + FACTUSOL</Link>
      </nav>
      {children}
    </div>
  );
}
