"use client";

/**
 * PR-Aliases-UX. Selector multi de aliases Gmail con default.
 *
 * Pre-PR: lista plana scrolleable de los 57 aliases con checkbox +
 * radio por fila. UX pesada — Bart pidió un dropdown colapsable.
 *
 * Diseño:
 *   - Card cerrado: resumen ("N alias activos · default@x.com") +
 *     chips de los seleccionados (estrella ★ = default, click la
 *     estrella para cambiar el default, X para desmarcar).
 *   - Card abierto: dropdown con input search + lista virtual
 *     completa de los 57 aliases con checkboxes.
 *   - Auto-save al cerrar (debounce 400ms tras último cambio si la
 *     lista sigue abierta — para guardar sin requerir click extra).
 *
 * El componente NO conoce el endpoint; recibe la lista cargada y un
 * callback `onSave(prefs)` para que el padre haga el PUT.
 */
import {
  Check,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  Search,
  Star,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { AliasPreferenceItem, EmailAlias } from "../lib/emailsApi";
import { extractErrorMessage } from "../lib/errors";

type Props = {
  aliases: EmailAlias[];
  /** El padre hace el PUT y devuelve la lista actualizada. */
  onSave: (prefs: AliasPreferenceItem[]) => Promise<EmailAlias[]>;
  onRefresh: () => void;
  refreshing: boolean;
};

type Pref = {
  email: string;
  allowed: boolean;
  isDefault: boolean;
};

function aliasesToPrefs(aliases: EmailAlias[]): Pref[] {
  return aliases.map((a) => ({
    email: a.send_as_email,
    allowed: a.user_pref_allowed,
    isDefault: a.user_pref_default,
  }));
}

function ensureSingleDefault(prefs: Pref[]): Pref[] {
  // Garantiza exactamente 1 default entre allowed. Si hay 0 y al
  // menos 1 allowed, el primer allowed pasa a default. Si hay >1
  // default (no debería pasar por UX pero defensa), gana el primero.
  const allowed = prefs.filter((p) => p.allowed);
  if (allowed.length === 0) {
    return prefs.map((p) => ({ ...p, isDefault: false }));
  }
  const defaults = allowed.filter((p) => p.isDefault);
  if (defaults.length === 1) return prefs;
  const winner = defaults[0]?.email ?? allowed[0].email;
  return prefs.map((p) => ({
    ...p,
    isDefault: p.allowed && p.email === winner,
  }));
}

export function GmailAliasMultiSelect({
  aliases,
  onSave,
  onRefresh,
  refreshing,
}: Props) {
  const [prefs, setPrefs] = useState<Pref[]>(() =>
    ensureSingleDefault(aliasesToPrefs(aliases)),
  );
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  // Re-sync el draft cuando el padre recargue la lista.
  useEffect(() => {
    setPrefs(ensureSingleDefault(aliasesToPrefs(aliases)));
  }, [aliases]);

  // Click fuera cierra el dropdown.
  useEffect(() => {
    if (!open) return;
    function handle(e: MouseEvent) {
      if (!wrapperRef.current?.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, [open]);

  const aliasByEmail = useMemo(() => {
    const m = new Map<string, EmailAlias>();
    for (const a of aliases) m.set(a.send_as_email, a);
    return m;
  }, [aliases]);

  const allowedPrefs = useMemo(
    () => prefs.filter((p) => p.allowed),
    [prefs],
  );
  const defaultEmail = useMemo(
    () => allowedPrefs.find((p) => p.isDefault)?.email ?? null,
    [allowedPrefs],
  );

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return prefs;
    return prefs.filter((p) => {
      const meta = aliasByEmail.get(p.email);
      const haystack = `${meta?.display_name ?? ""} ${p.email}`.toLowerCase();
      return haystack.includes(q);
    });
  }, [prefs, search, aliasByEmail]);

  const handleSave = useCallback(
    async (next: Pref[]) => {
      const normalised = ensureSingleDefault(next);
      setSaving(true);
      setError(null);
      try {
        const updated = await onSave(
          normalised.map((p) => ({
            alias_email: p.email,
            is_allowed: p.allowed,
            is_default: p.isDefault,
          })),
        );
        setPrefs(ensureSingleDefault(aliasesToPrefs(updated)));
        setMessage("Preferencias guardadas.");
        window.setTimeout(() => setMessage(null), 2500);
      } catch (err) {
        setError(
          extractErrorMessage(err, "No se pudieron guardar las preferencias."),
        );
      } finally {
        setSaving(false);
      }
    },
    [onSave],
  );

  // Auto-save tras cada mutación, debounced para evitar PUTs por
  // cada click de checkbox en la lista. 400 ms es cómodo en práctica.
  const pendingRef = useRef<Pref[] | null>(null);
  const debounceRef = useRef<number | null>(null);
  function dispatchSave(next: Pref[]): void {
    pendingRef.current = next;
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
    }
    debounceRef.current = window.setTimeout(() => {
      if (pendingRef.current) void handleSave(pendingRef.current);
      pendingRef.current = null;
      debounceRef.current = null;
    }, 400);
  }

  function toggleAllowed(email: string): void {
    setPrefs((prev) => {
      const next = prev.map((p) =>
        p.email === email
          ? {
              ...p,
              allowed: !p.allowed,
              isDefault: !p.allowed ? p.isDefault : false,
            }
          : p,
      );
      const normalised = ensureSingleDefault(next);
      dispatchSave(normalised);
      return normalised;
    });
  }

  function pickDefault(email: string): void {
    setPrefs((prev) => {
      const next = prev.map((p) => ({
        ...p,
        isDefault: p.email === email && p.allowed,
        allowed: p.email === email ? true : p.allowed,
      }));
      const normalised = ensureSingleDefault(next);
      dispatchSave(normalised);
      return normalised;
    });
  }

  function removeChip(email: string): void {
    // Equivalente a desmarcar el checkbox.
    toggleAllowed(email);
  }

  // Render del card cerrado: chips + botón abrir.
  return (
    <div
      className={`alias-multiselect ${open ? "is-open" : ""}`}
      ref={wrapperRef}
    >
      <button
        type="button"
        className="alias-multiselect-trigger"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls="alias-multiselect-panel"
      >
        <span className="alias-multiselect-summary">
          <strong>
            {allowedPrefs.length} alias activo
            {allowedPrefs.length === 1 ? "" : "s"}
          </strong>
          {defaultEmail ? (
            <span className="muted small">
              {" "}
              · predeterminado <code>{defaultEmail}</code>
            </span>
          ) : (
            <span className="muted small"> · sin predeterminado</span>
          )}
        </span>
        {open ? (
          <ChevronUp size={14} aria-hidden />
        ) : (
          <ChevronDown size={14} aria-hidden />
        )}
      </button>

      {allowedPrefs.length > 0 ? (
        <ul className="alias-multiselect-chips">
          {allowedPrefs.map((p) => {
            const meta = aliasByEmail.get(p.email);
            const label = meta?.display_name || p.email;
            return (
              <li
                key={p.email}
                className={`alias-multiselect-chip${
                  p.isDefault ? " is-default" : ""
                }`}
              >
                <button
                  type="button"
                  className="alias-multiselect-chip-star"
                  onClick={() => pickDefault(p.email)}
                  title={
                    p.isDefault
                      ? "Predeterminado"
                      : "Marcar como predeterminado"
                  }
                  aria-label={
                    p.isDefault
                      ? `${p.email} es el predeterminado`
                      : `Marcar ${p.email} como predeterminado`
                  }
                >
                  <Star
                    size={11}
                    aria-hidden
                    fill={p.isDefault ? "currentColor" : "none"}
                  />
                </button>
                <span className="alias-multiselect-chip-label">
                  <strong>{label}</strong>{" "}
                  <span className="muted small">&lt;{p.email}&gt;</span>
                  {p.isDefault ? (
                    <span className="badge ok small alias-default-badge">
                      predeterminado
                    </span>
                  ) : null}
                </span>
                <button
                  type="button"
                  className="alias-multiselect-chip-remove"
                  onClick={() => removeChip(p.email)}
                  title="Quitar de los activos"
                  aria-label={`Quitar ${p.email} de los aliases activos`}
                >
                  <X size={10} aria-hidden />
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="muted small alias-multiselect-empty">
          Aún no has marcado ningún alias. Abre el selector y marca los que
          uses.
        </p>
      )}

      {open ? (
        <div
          id="alias-multiselect-panel"
          className="alias-multiselect-panel"
        >
          <div className="alias-multiselect-panel-header">
            <div className="alias-multiselect-search">
              <Search size={11} aria-hidden />
              <input
                type="search"
                placeholder="Buscar alias…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                autoFocus
              />
            </div>
            <button
              type="button"
              className="button small secondary"
              onClick={onRefresh}
              disabled={refreshing}
              title="Recargar lista desde Gmail"
            >
              <RefreshCw
                size={11}
                aria-hidden
                className={refreshing ? "spin" : undefined}
              />
            </button>
          </div>
          <p className="muted small alias-multiselect-counter">
            {prefs.length} alias en Gmail · {allowedPrefs.length} marcado
            {allowedPrefs.length === 1 ? "" : "s"}
          </p>
          <ul className="alias-multiselect-list">
            {visible.length === 0 ? (
              <li className="muted small alias-multiselect-empty-search">
                Ningún alias coincide con &quot;{search}&quot;.
              </li>
            ) : (
              visible.map((p) => {
                const meta = aliasByEmail.get(p.email);
                const label = meta?.display_name || p.email;
                const unverified =
                  meta?.verification_status &&
                  meta.verification_status !== "accepted";
                return (
                  <li
                    key={p.email}
                    className={`alias-multiselect-list-row${
                      p.allowed ? " is-allowed" : ""
                    }`}
                  >
                    <label className="alias-multiselect-list-check">
                      <input
                        type="checkbox"
                        checked={p.allowed}
                        onChange={() => toggleAllowed(p.email)}
                      />
                      <span className="alias-multiselect-list-meta">
                        <strong>{label}</strong>{" "}
                        <span className="muted small">
                          &lt;{p.email}&gt;
                        </span>
                        {unverified ? (
                          <span
                            className="badge bad small"
                            title="Pendiente de verificar en Gmail"
                          >
                            no verificado
                          </span>
                        ) : null}
                      </span>
                    </label>
                    {p.allowed ? (
                      <button
                        type="button"
                        className={`alias-multiselect-list-star${
                          p.isDefault ? " is-default" : ""
                        }`}
                        onClick={() => pickDefault(p.email)}
                        title={
                          p.isDefault
                            ? "Predeterminado"
                            : "Marcar como predeterminado"
                        }
                      >
                        <Star
                          size={11}
                          aria-hidden
                          fill={p.isDefault ? "currentColor" : "none"}
                        />
                      </button>
                    ) : null}
                  </li>
                );
              })
            )}
          </ul>
          <div className="alias-multiselect-footer">
            {error ? <p className="form-error small">{error}</p> : null}
            {saving ? (
              <p className="muted small">Guardando…</p>
            ) : message ? (
              <p className="form-success small">
                <Check size={11} aria-hidden /> {message}
              </p>
            ) : null}
            <button
              type="button"
              className="button small"
              onClick={() => setOpen(false)}
            >
              Cerrar
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
