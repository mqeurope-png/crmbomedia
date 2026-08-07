"use client";

import {
  Ban,
  CalendarClock,
  KeyRound,
  Lock,
  PenLine,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import { GoogleCalendarSection } from "../components/GoogleCalendarSection";
import { GoogleConnectionBanner } from "../components/GoogleConnectionBanner";
import { getCurrentUser, type User } from "../lib/api";
import {
  getMyPreferences,
  updateMyPreferences,
} from "../lib/emailTrackingApi";
import { extractErrorMessage } from "../lib/errors";

/** Account hub.
 *
 * CRM-PERFIL — el comercial ve su perfil en SOLO LECTURA salvo la firma (y su
 * 2FA, que queda fuera de este sprint). Todo lo demás (contraseña, alias,
 * calendario, preferencias) lo gestiona el administrador desde /admin/users.
 * El admin sigue viendo su propia cuenta con todo editable. */
export default function AccountPage() {
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [includeUnsub, setIncludeUnsub] = useState(false);
  const [prefsSaving, setPrefsSaving] = useState(false);

  const isAdmin = user?.role === "admin";

  useEffect(() => {
    getCurrentUser()
      .then(setUser)
      .catch((err) =>
        setError(extractErrorMessage(err, "No se pudo cargar el usuario actual")),
      );
    getMyPreferences()
      .then((p) => setIncludeUnsub(p.email_include_unsubscribe_default))
      .catch(() => {
        /* prefs optional — fail silently and keep the unchecked default */
      });
  }, []);

  async function handleTogglePref(next: boolean) {
    setIncludeUnsub(next);
    setPrefsSaving(true);
    try {
      const updated = await updateMyPreferences({
        email_include_unsubscribe_default: next,
      });
      setIncludeUnsub(updated.email_include_unsubscribe_default);
    } catch (err) {
      setIncludeUnsub((prev) => !next || prev);
      setError(
        extractErrorMessage(err, "No se pudo guardar tu preferencia."),
      );
    } finally {
      setPrefsSaving(false);
    }
  }

  if (error) {
    return (
      <main className="shell narrow">
        <PageHeader title="Mi cuenta" eyebrow="Cuenta" />
        <div className="error-state">{error}</div>
      </main>
    );
  }

  const managedNote = (
    <p className="muted small">
      <Lock size={11} aria-hidden /> Gestionado por el administrador. Contacta
      con soporte interno para modificarlo.
    </p>
  );

  return (
    <main className="shell narrow">
      <PageHeader
        title="Mi cuenta"
        eyebrow="Cuenta"
        description={user ? user.email : undefined}
      />

      {/* Banner permanente para el comercial: perfil gestionado por admin. */}
      {user && !isAdmin ? (
        <div className="info-state account-managed-banner" role="note">
          Este perfil está gestionado por el administrador. Solo puedes editar
          tu <strong>firma</strong>. Para cambiar tu contraseña, tus alias o el
          calendario, pídelo al administrador.
        </div>
      ) : null}

      {/* Banner de estado Gmail (se auto-oculta para no-admin). */}
      <GoogleConnectionBanner />

      <section className="account-grid">
        <article className="card">
          <header className="section-title">
            <h2>
              <KeyRound size={16} aria-hidden /> Contraseña
            </h2>
          </header>
          {isAdmin ? (
            <>
              <p className="muted small">
                Cambia la contraseña que usas para entrar al CRM.
              </p>
              <Link className="button small" href="/account/password">
                Cambiar contraseña
              </Link>
            </>
          ) : (
            managedNote
          )}
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
              <Ban size={16} aria-hidden /> Preferencias de envío
            </h2>
          </header>
          {isAdmin ? (
            <label className="account-pref-row">
              <input
                type="checkbox"
                checked={includeUnsub}
                disabled={prefsSaving}
                onChange={(e) => handleTogglePref(e.target.checked)}
              />
              <span>
                <strong>Incluir opción de baja por defecto en mis emails.</strong>
                <span className="muted small">
                  {" "}Si lo activas, cada email que envíes incluirá enlace de
                  desuscripción y la cabecera <code>List-Unsubscribe</code>.
                </span>
              </span>
            </label>
          ) : (
            <>
              <p className="account-pref-readonly">
                Incluir opción de baja por defecto:{" "}
                <strong>{includeUnsub ? "Sí" : "No"}</strong>
              </p>
              {managedNote}
            </>
          )}
        </article>

        {/* Calendario / alias Google: editable solo para admin. */}
        {isAdmin ? (
          <article className="card account-card-wide">
            <header className="section-title">
              <h2>
                <CalendarClock size={16} aria-hidden /> Google Calendar
              </h2>
            </header>
            <GoogleCalendarSection />
          </article>
        ) : (
          <article className="card account-card-wide">
            <header className="section-title">
              <h2>
                <CalendarClock size={16} aria-hidden /> Google Calendar y alias
              </h2>
            </header>
            {managedNote}
          </article>
        )}
      </section>
    </main>
  );
}
