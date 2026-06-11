"use client";

import { ChevronLeft, Pencil, Trash2, UserMinus, UserPlus } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ErrorState } from "../../../components/ErrorState";
import { PageHeader } from "../../../components/PageHeader";
import {
  deleteBrevoList,
  getBrevoList,
  getBrevoListContacts,
  removeContactsFromBrevoList,
  resolvePrimaryBrevoAccount,
  updateBrevoList,
  type BrevoList,
  type BrevoListContactItem,
} from "../../../lib/brevoApi";
import { extractErrorMessage } from "../../../lib/errors";

const PAGE_SIZE = 50;

export default function MarketingListDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const listId = Number(params.id);

  const [accountId, setAccountId] = useState<string | null>(null);
  const [list, setList] = useState<BrevoList | null>(null);
  const [items, setItems] = useState<BrevoListContactItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");

  const load = useCallback(
    async (account: string, nextOffset = 0) => {
      try {
        const [detail, page] = await Promise.all([
          getBrevoList(account, listId),
          getBrevoListContacts(account, listId, {
            limit: PAGE_SIZE,
            offset: nextOffset,
          }),
        ]);
        setList(detail);
        setItems(page.items);
        setTotal(page.total);
        setOffset(nextOffset);
        setError(null);
      } catch (err) {
        setError(extractErrorMessage(err, "No se pudo cargar la lista."));
      }
    },
    [listId],
  );

  useEffect(() => {
    resolvePrimaryBrevoAccount()
      .then(async (account) => {
        setAccountId(account);
        if (account) await load(account, 0);
      })
      .catch(() => setError("No se pudo resolver la cuenta Brevo."))
      .finally(() => setLoading(false));
  }, [load]);

  async function handleRename() {
    if (!accountId || !renameValue.trim() || !list) return;
    setWorking(true);
    try {
      await updateBrevoList(accountId, listId, { name: renameValue.trim() });
      await load(accountId, offset);
      setRenaming(false);
      setMessage("Lista renombrada.");
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo renombrar."));
    } finally {
      setWorking(false);
    }
  }

  async function handleDelete() {
    if (!accountId || !list) return;
    if (
      !confirm(
        `Borrar la lista "${list.name}" de Brevo. Los contactos no se borran, solo la lista. ¿Continuar?`,
      )
    ) {
      return;
    }
    setWorking(true);
    try {
      await deleteBrevoList(accountId, listId);
      router.push("/marketing/listas");
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo borrar."));
      setWorking(false);
    }
  }

  async function handleRemoveContact(item: BrevoListContactItem) {
    if (!accountId) return;
    if (!confirm(`Quitar ${item.email} de esta lista en Brevo?`)) return;
    setWorking(true);
    try {
      const result = await removeContactsFromBrevoList(accountId, listId, {
        emails: [item.email],
      });
      await load(accountId, offset);
      setMessage(
        `${result.sent} contacto${result.sent === 1 ? "" : "s"} quitado${
          result.sent === 1 ? "" : "s"
        } de la lista.`,
      );
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo quitar el contacto."));
    } finally {
      setWorking(false);
    }
  }

  if (loading) {
    return (
      <main className="shell shell-wide">
        <PageHeader title="Lista" eyebrow="Marketing" />
        <p className="muted">Cargando…</p>
      </main>
    );
  }

  if (!accountId) {
    return (
      <main className="shell shell-wide">
        <PageHeader title="Lista" eyebrow="Marketing" />
        <ErrorState
          title="Brevo no configurado"
          message="Configura una cuenta Brevo en /admin/integrations."
        />
      </main>
    );
  }

  if (!list) {
    return (
      <main className="shell shell-wide">
        <PageHeader title="Lista" eyebrow="Marketing" />
        <ErrorState
          title="No se encontró la lista"
          message={error ?? "La lista puede haberse borrado en Brevo."}
        />
      </main>
    );
  }

  return (
    <main className="shell shell-wide">
      <p className="muted small" style={{ marginBottom: 8 }}>
        <Link href="/marketing/listas" className="back-link">
          <ChevronLeft size={14} aria-hidden /> Volver a listas
        </Link>
      </p>
      <PageHeader
        title={list.name}
        eyebrow={`Lista Brevo #${list.id}`}
        actions={
          <div className="actions">
            {renaming ? (
              <>
                <input
                  type="text"
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  placeholder="Nuevo nombre"
                  maxLength={200}
                />
                <button
                  type="button"
                  className="button"
                  onClick={handleRename}
                  disabled={working || !renameValue.trim()}
                >
                  Guardar
                </button>
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => setRenaming(false)}
                  disabled={working}
                >
                  Cancelar
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => {
                    setRenameValue(list.name);
                    setRenaming(true);
                  }}
                >
                  <Pencil size={13} aria-hidden /> Renombrar
                </button>
                <button
                  type="button"
                  className="button danger"
                  onClick={handleDelete}
                  disabled={working}
                >
                  <Trash2 size={13} aria-hidden /> Borrar
                </button>
              </>
            )}
          </div>
        }
      />

      {error ? (
        <div className="form-error" role="alert">
          {error}
        </div>
      ) : null}
      {message ? (
        <p className="muted" role="status">
          {message}
        </p>
      ) : null}

      <section className="grid two" style={{ marginBottom: 16 }}>
        <article className="card">
          <h3 className="muted small">Suscriptores totales</h3>
          <strong style={{ fontSize: 28 }}>
            {list.total_subscribers.toLocaleString("es-ES")}
          </strong>
        </article>
        <article className="card">
          <h3 className="muted small">Únicos / blacklist</h3>
          <strong style={{ fontSize: 18 }}>
            {(list.unique_subscribers ?? 0).toLocaleString("es-ES")} /{" "}
            {(list.total_blacklisted ?? 0).toLocaleString("es-ES")}
          </strong>
        </article>
      </section>

      <article className="card">
        <header
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 12,
          }}
        >
          <h2 style={{ margin: 0 }}>Contactos en la lista</h2>
          <p className="muted small" style={{ margin: 0 }}>
            <UserPlus size={12} aria-hidden /> Para añadir contactos en bloque,
            usa el botón &laquo;Enviar a lista Brevo&raquo; desde una vista en{" "}
            <Link href="/contacts">/contacts</Link>.
          </p>
        </header>
        {items.length === 0 ? (
          <p className="muted">La lista está vacía.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Contacto CRM</th>
                <th style={{ textAlign: "right" }} />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.email}>
                  <td>{item.email}</td>
                  <td>
                    {item.contact_id ? (
                      <Link href={`/contacts/${item.contact_id}`}>
                        {[item.first_name, item.last_name]
                          .filter(Boolean)
                          .join(" ") || item.email}
                      </Link>
                    ) : (
                      <span className="muted small">no está en el CRM</span>
                    )}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <button
                      type="button"
                      className="button secondary small"
                      onClick={() => handleRemoveContact(item)}
                      disabled={working}
                    >
                      <UserMinus size={12} aria-hidden /> Quitar
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <Pagination
          offset={offset}
          limit={PAGE_SIZE}
          total={total}
          onChange={(next) => accountId && load(accountId, next)}
        />
      </article>
    </main>
  );
}

function Pagination({
  offset,
  limit,
  total,
  onChange,
}: {
  offset: number;
  limit: number;
  total: number;
  onChange: (next: number) => void;
}) {
  if (total <= limit) return null;
  const prev = Math.max(0, offset - limit);
  const next = offset + limit;
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        marginTop: 12,
      }}
    >
      <span className="muted small">
        {offset + 1} – {Math.min(offset + limit, total)} de {total}
      </span>
      <div className="actions">
        <button
          type="button"
          className="button secondary"
          onClick={() => onChange(prev)}
          disabled={offset === 0}
        >
          ← Anterior
        </button>
        <button
          type="button"
          className="button secondary"
          onClick={() => onChange(next)}
          disabled={next >= total}
        >
          Siguiente →
        </button>
      </div>
    </div>
  );
}
