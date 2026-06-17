"use client";

/**
 * Placeholder de la pestaña "Soporte" — PR-D. La integración con
 * Freshdesk + la vista de tickets por contacto aterriza en un sprint
 * posterior; de momento la tab existe para preservar la estructura
 * de navegación que el mockup propone.
 */
import { LifeBuoy } from "lucide-react";

export function ContactSupportTab() {
  return (
    <div className="contact-support-placeholder">
      <span className="contact-support-icon" aria-hidden>
        <LifeBuoy size={28} />
      </span>
      <h3>Soporte — próximamente</h3>
      <p className="muted">
        Integración con Freshdesk pendiente. Aquí aparecerán los tickets
        abiertos del contacto + historial reciente.
      </p>
    </div>
  );
}
