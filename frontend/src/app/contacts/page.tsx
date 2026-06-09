"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ContactFilters } from "../components/ContactFilters";
import { ErrorState } from "../components/ErrorState";
import {
  listContacts,
  type Contact,
  type ContactListFilters,
  type ContactListPage,
} from "../lib/api";
import { extractErrorMessage } from "../lib/errors";

const PAGE_SIZE = 25;

const DEFAULT_FILTERS: ContactListFilters = {
  sort_by: "created_at",
  sort_dir: "desc",
  limit: PAGE_SIZE,
  skip: 0,
};

function fullName(contact: Contact): string {
  return [contact.first_name, contact.last_name].filter(Boolean).join(" ").trim();
}

function formatDate(value: string | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleDateString("es-ES", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export default function ContactsListPage() {
  const [filters, setFilters] = useState<ContactListFilters>(DEFAULT_FILTERS);
  // `searchInput` is the unsynced text in the input box; `filters.q` is
  // what we actually send. We debounce typing so the list doesn't refetch
  // on every keystroke.
  const [searchInput, setSearchInput] = useState("");
  const [page, setPage] = useState<ContactListPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setFilters((current) => ({
        ...current,
        q: searchInput.trim() || undefined,
        skip: 0,
      }));
    }, 250);
    return () => window.clearTimeout(handle);
  }, [searchInput]);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    listContacts(filters)
      .then((result) => {
        if (!cancelled) {
          setPage(result);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(extractErrorMessage(err, "No se pudieron cargar los contactos."));
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filters]);

  const totalPages = useMemo(() => {
    if (!page || page.limit === 0) return 1;
    return Math.max(1, Math.ceil(page.total / page.limit));
  }, [page]);
  const currentPage = useMemo(() => {
    if (!page || page.limit === 0) return 1;
    return Math.floor(page.offset / page.limit) + 1;
  }, [page]);

  const handleReset = useCallback(() => {
    setSearchInput("");
    setFilters(DEFAULT_FILTERS);
  }, []);

  const handleSortChange = useCallback((event: React.ChangeEvent<HTMLSelectElement>) => {
    const [sort_by, sort_dir] = event.target.value.split(":") as [
      NonNullable<ContactListFilters["sort_by"]>,
      NonNullable<ContactListFilters["sort_dir"]>,
    ];
    setFilters((current) => ({ ...current, sort_by, sort_dir, skip: 0 }));
  }, []);

  const goToPage = useCallback(
    (nextPage: number) => {
      if (!page) return;
      const clamped = Math.max(1, Math.min(totalPages, nextPage));
      setFilters((current) => ({
        ...current,
        skip: (clamped - 1) * (current.limit ?? PAGE_SIZE),
      }));
    },
    [page, totalPages],
  );

  return (
    <main className="shell">
      <Link href="/" className="back-link">
        ← Volver al dashboard
      </Link>
      <section className="hero compact">
        <p className="eyebrow">Contactos</p>
        <h1>Lista de contactos</h1>
        <p className="lead">
          Busca, filtra y abre cualquier contacto. La lista refleja los
          contactos sincronizados desde AgileCRM y los creados a mano.
        </p>
        <div className="actions">
          <Link href="/contacts/new" className="button">
            Crear contacto
          </Link>
        </div>
      </section>

      <section className="panel">
        <div className="contact-toolbar">
          <input
            type="search"
            className="search-input"
            placeholder="Buscar por nombre, email o teléfono…"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            aria-label="Búsqueda de contactos"
          />
          <label className="sort-select">
            <span>Ordenar por</span>
            <select
              value={`${filters.sort_by ?? "created_at"}:${filters.sort_dir ?? "desc"}`}
              onChange={handleSortChange}
            >
              <option value="created_at:desc">Más recientes primero</option>
              <option value="created_at:asc">Más antiguos primero</option>
              <option value="updated_at:desc">Última actualización</option>
              <option value="name:asc">Nombre (A→Z)</option>
              <option value="email:asc">Email (A→Z)</option>
            </select>
          </label>
        </div>

        <ContactFilters
          filters={filters}
          onChange={setFilters}
          onReset={handleReset}
        />

        {error ? <ErrorState title="Error" message={error} /> : null}

        {isLoading && !page ? (
          <p className="muted">Cargando contactos…</p>
        ) : page && page.items.length === 0 ? (
          <p className="muted">
            Ningún contacto coincide con los filtros aplicados.
          </p>
        ) : page ? (
          <>
            <div className="table-wrapper">
              <table className="data-table contacts-table">
                <thead>
                  <tr>
                    <th scope="col">Nombre</th>
                    <th scope="col">Email</th>
                    <th scope="col">Teléfono</th>
                    <th scope="col">Tags</th>
                    <th scope="col">Origen</th>
                    <th scope="col">Estado</th>
                    <th scope="col">Consentimiento</th>
                    <th scope="col">Actualizado</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((contact) => (
                    <tr key={contact.id}>
                      <td>
                        <Link href={`/contacts/${contact.id}`}>
                          {fullName(contact) || "(Sin nombre)"}
                        </Link>
                      </td>
                      <td>{contact.email}</td>
                      <td>{contact.phone ?? "—"}</td>
                      <td>{contact.tags || "—"}</td>
                      <td>{contact.origin ?? "—"}</td>
                      <td>{contact.commercial_status}</td>
                      <td>
                        <span
                          className={`status status-${contact.marketing_consent}`}
                        >
                          {contact.marketing_consent}
                        </span>
                      </td>
                      <td>{formatDate(contact.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pagination">
              <span className="muted">
                {page.total} contacto{page.total === 1 ? "" : "s"} ·
                {" "}Página {currentPage} / {totalPages}
              </span>
              <div className="pagination-buttons">
                <button
                  type="button"
                  className="button secondary small"
                  onClick={() => goToPage(currentPage - 1)}
                  disabled={currentPage <= 1}
                >
                  Anterior
                </button>
                <button
                  type="button"
                  className="button secondary small"
                  onClick={() => goToPage(currentPage + 1)}
                  disabled={currentPage >= totalPages}
                >
                  Siguiente
                </button>
              </div>
            </div>
          </>
        ) : null}
      </section>
    </main>
  );
}
