"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { CompanyEditor } from "./components/CompanyEditor";
import { ErrorState } from "./components/ErrorState";
import { PageHeader } from "./components/PageHeader";
import {
  getCompanies,
  getCompaniesCount,
  getContacts,
  getContactsCount,
  getCurrentUser,
  type Company,
  type Contact,
  type User,
} from "./lib/api";
import { extractErrorMessage } from "./lib/errors";

const roadmapItems = [
  "Contactos y empresas como modelo propio",
  "Autenticación JWT y roles mínimos",
  "Conectores externos preparados por capas, sin implementarlos todavía",
  "Auditoría básica de accesos y acciones CRM",
  "Consentimiento marketing y bajas con prioridad RGPD",
];

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function Home() {
  const [user, setUser] = useState<User | null>(null);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  // The list endpoints page at 20 by default, but the stat-cards must
  // show the real totals. Fetch them via the dedicated /count endpoints.
  const [contactsTotal, setContactsTotal] = useState<number | null>(null);
  const [companiesTotal, setCompaniesTotal] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function loadDashboard() {
      try {
        const [
          currentUser,
          contactList,
          companyList,
          contactTotal,
          companyTotal,
        ] = await Promise.all([
          getCurrentUser(),
          getContacts(),
          getCompanies(),
          getContactsCount(),
          getCompaniesCount(),
        ]);
        setUser(currentUser);
        setContacts(contactList);
        setCompanies(companyList);
        setContactsTotal(contactTotal);
        setCompaniesTotal(companyTotal);
      } catch (err) {
        setError(extractErrorMessage(err, "Arranca la API o inicia sesión de nuevo."));
      } finally {
        setIsLoading(false);
      }
    }
    loadDashboard();
  }, []);

  if (isLoading) {
    return (
      <main className="shell">
        <PageHeader title="Dashboard" eyebrow="CRM" />
        <p className="muted">Cargando CRM…</p>
      </main>
    );
  }

  if (error) {
    return (
      <main className="shell">
        <PageHeader title="Dashboard" eyebrow="CRM" />
        <ErrorState title="No se pudo cargar el CRM" message={error} />
      </main>
    );
  }

  void user;

  return (
    <main className="shell">
      <PageHeader
        title="Dashboard"
        eyebrow="Resumen del CRM"
        description="Estado actual de contactos y empresas en la base de datos compartida."
        actions={
          <>
            <Link href="/contacts" className="button small">
              Ver contactos
            </Link>
            <Link href="/contacts/new" className="button secondary small">
              + Nuevo contacto
            </Link>
            <a
              href={`${apiBaseUrl}/api/docs`}
              className="button secondary small"
            >
              OpenAPI
            </a>
          </>
        }
      />

      <section className="stats-grid" aria-label="Resumen CRM">
        <article className="stat-card">
          <span>{contactsTotal ?? contacts.length}</span>
          <p>Contactos activos</p>
        </article>
        <article className="stat-card">
          <span>{companiesTotal ?? companies.length}</span>
          <p>Empresas activas</p>
        </article>
        <article className="stat-card"><span>4</span><p>Roles disponibles</p></article>
      </section>

      <section className="grid two">
        <article className="card">
          <div className="section-title">
            <h2>Contactos</h2>
            <Link href="/contacts">Ver todos</Link>
          </div>
          {contacts.length ? (
            <ul className="item-list">
              {contacts.map((contact) => (
                <li key={contact.id}>
                  <Link href={`/contacts/${contact.id}`}>
                    <strong>{contact.first_name} {contact.last_name ?? ""}</strong>
                    <span>{contact.email}</span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : <p className="muted">No hay contactos todavía.</p>}
        </article>

        <article className="card">
          <h2>Empresas</h2>
          {companies.length ? (
            <ul className="item-list">
              {companies.map((company) => (
                <li key={company.id}>
                  <CompanyEditor company={company} />
                  <span>{company.tax_id ?? "Sin NIF/CIF"}</span>
                </li>
              ))}
            </ul>
          ) : <p className="muted">No hay empresas todavía.</p>}
        </article>
      </section>

      <section className="panel">
        <h2>Principios implementados</h2>
        <ul>
          {roadmapItems.map((item) => <li key={item}>{item}</li>)}
        </ul>
      </section>
    </main>
  );
}
