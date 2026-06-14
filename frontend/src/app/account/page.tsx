"use client";

import { CalendarClock, KeyRound, PenLine, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import { GoogleCalendarSection } from "../components/GoogleCalendarSection";
import { getCurrentUser, type User } from "../lib/api";
import { extractErrorMessage } from "../lib/errors";

/** Account hub — quick links to password + 2FA + Google Calendar.
 *
 * Lives at `/account` because `/account/security` and
 * `/account/password` already existed and there was no parent index;
 * the Google integration needs one too. */
export default function AccountPage() {
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCurrentUser()
      .then(setUser)
      .catch((err) =>
        setError(extractErrorMessage(err, "No se pudo cargar el usuario actual")),
      );
  }, []);

  if (error) {
    return (
      <main className="shell narrow">
        <PageHeader title="Mi cuenta" eyebrow="Cuenta" />
        <div className="error-state">{error}</div>
      </main>
    );
  }

  return (
    <main className="shell narrow">
      <PageHeader
        title="Mi cuenta"
        eyebrow="Cuenta"
        description={user ? user.email : undefined}
      />

      <section className="account-grid">
        <article className="card">
          <header className="section-title">
            <h2>
              <KeyRound size={16} aria-hidden /> Contraseña
            </h2>
          </header>
          <p className="muted small">
            Cambia la contraseña que usas para entrar al CRM.
          </p>
          <Link className="button small" href="/account/password">
            Cambiar contraseña
          </Link>
        </article>

        <article className="card">
          <header className="section-title">
            <h2>
              <ShieldCheck size={16} aria-hidden /> Doble factor (2FA)
            </h2>
          </header>
          <p className="muted small">
            Activa la autenticación en dos pasos con tu app TOTP.
          </p>
          <Link className="button small" href="/account/security">
            Configurar 2FA
          </Link>
        </article>

        <article className="card">
          <header className="section-title">
            <h2>
              <PenLine size={16} aria-hidden /> Firmas de email
            </h2>
          </header>
          <p className="muted small">
            Crea varias firmas y marca una como predeterminada — se
            añade automáticamente al redactar un email.
          </p>
          <Link className="button small" href="/account/firmas">
            Gestionar firmas
          </Link>
        </article>

        <article className="card account-card-wide">
          <header className="section-title">
            <h2>
              <CalendarClock size={16} aria-hidden /> Google Calendar
            </h2>
          </header>
          <GoogleCalendarSection />
        </article>
      </section>
    </main>
  );
}
