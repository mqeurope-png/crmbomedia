"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { EmailAlias } from "../lib/emailsApi";
import { extractErrorMessage } from "../lib/errors";
import { getUserSenders, putUserSenders } from "../lib/userAliasesApi";
import { GmailAliasMultiSelect } from "./GmailAliasMultiSelect";

/**
 * Remitentes del «De» de un usuario, en su ficha de Usuarios (solo admin).
 *
 * Lo mismo que cada uno hace para sí en Mi cuenta → Gmail (alias de envío),
 * con el mismo selector: marcar las direcciones que le salen en el
 * desplegable «De», la estrella para su predeterminado y el lápiz para el
 * nombre visible (solo en BoHub, sin tocar Gmail). Desde CRM-PERFIL el
 * comercial no puede tocarlo en su Mi cuenta: lo gestiona el administrador
 * desde aquí. Lo marcado se respeta en cada sincronización con Gmail y cada
 * cambio queda en Auditoría.
 *
 * No confundir con las «Direcciones de entrada» (`AliasManager`): esas dicen
 * de quién es el correo que LLEGA, no desde dónde se puede escribir.
 */
export function RemitentesUsuario({ userId }: { userId: string }) {
  const [aliases, setAliases] = useState<EmailAlias[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Solo vale la respuesta de la última petición: un GET que salió antes de
  // un guardado traería el estado de antes y el siguiente autoguardado lo
  // devolvería al servidor, deshaciendo el cambio.
  const peticion = useRef(0);

  const reload = useCallback(async () => {
    const mia = ++peticion.current;
    setLoading(true);
    try {
      const lista = await getUserSenders(userId);
      if (mia !== peticion.current) return;
      setAliases(lista);
      setError(null);
    } catch (err) {
      if (mia !== peticion.current) return;
      setError(extractErrorMessage(err, "No se pudieron cargar los remitentes"));
    } finally {
      if (mia === peticion.current) setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (aliases === null) {
    return error ? (
      <p className="form-error small">{error}</p>
    ) : (
      <p className="muted small">Cargando remitentes…</p>
    );
  }

  const sinGmail = aliases.length > 0 && aliases.every((a) => a.en_gmail == null);
  const recibeSinRemitente = aliases
    .filter((a) => a.de_entrada && !a.user_pref_allowed)
    .map((a) => a.send_as_email);

  return (
    <div className="remitentes-usuario">
      <p className="muted small">
        Lo que marques aquí es lo que le sale en el desplegable «De» al
        redactar; la estrella es su predeterminado. Solo sirven las direcciones
        dadas de alta y verificadas como «Enviar como» en la cuenta de Google
        del equipo: las demás salen como «no verificada en Gmail» (Gmail
        rechazaría el envío). El lápiz cambia el nombre visible solo en BoHub,
        sin tocar Gmail. Lo marcado se respeta en cada sincronización con Gmail
        y cada cambio queda en Auditoría.
      </p>
      {aliases.length === 0 ? (
        <p className="form-warning small">
          No sale ninguna dirección: la cuenta de Google del equipo no ha
          devuelto ningún «Enviar como» (puede que Gmail no responda) y este
          usuario no tiene nada guardado.
        </p>
      ) : null}
      {sinGmail ? (
        <p className="form-warning small">
          No se ha podido consultar Gmail: se ve lo guardado, pero no qué
          direcciones están verificadas, así que no se puede cambiar hasta que
          responda (se podría dejar de remitente una dirección que Gmail
          rechazaría).{" "}
          <button
            type="button"
            className="button secondary small"
            onClick={() => void reload()}
            disabled={loading}
          >
            Reintentar
          </button>
        </p>
      ) : null}
      {recibeSinRemitente.length > 0 ? (
        <p className="form-warning small">
          Recibe correo en {recibeSinRemitente.join(", ")}, pero no{" "}
          {recibeSinRemitente.length === 1 ? "la tiene" : "las tiene"} como
          remitente: no le {recibeSinRemitente.length === 1 ? "sale" : "salen"}{" "}
          en el «De». Márcala{recibeSinRemitente.length === 1 ? "" : "s"} abajo
          si debe escribir desde ahí.
        </p>
      ) : null}
      {error ? <p className="form-error small">{error}</p> : null}
      {sinGmail ? (
        <ul className="alias-list" aria-label="Remitentes guardados">
          {aliases
            .filter((a) => a.user_pref_allowed)
            .map((a) => (
              <li key={a.send_as_email} className="alias-row">
                <strong>{a.resolved_display_name || a.send_as_email}</strong>
                <span className="muted small">&lt;{a.send_as_email}&gt;</span>
                {a.user_pref_default ? (
                  <span className="badge ok small">predeterminado</span>
                ) : null}
              </li>
            ))}
        </ul>
      ) : (
      <GmailAliasMultiSelect
        aliases={aliases}
        onSave={async (prefs) => {
          const mia = ++peticion.current;
          try {
            const next = await putUserSenders(userId, prefs);
            if (mia === peticion.current) setAliases(next);
            return next;
          } finally {
            if (mia === peticion.current) setLoading(false);
          }
        }}
        onRefresh={() => void reload()}
        refreshing={loading}
        textoVacio="No tiene ningún remitente marcado: su desplegable «De» está vacío. Abre el selector y marca los que use."
      />
      )}
    </div>
  );
}
