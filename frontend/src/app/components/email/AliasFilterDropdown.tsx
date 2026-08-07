"use client";

import { useEffect, useState } from "react";
import {
  listUserAliases,
  type UserEmailAlias,
} from "../../lib/userAliasesApi";

/**
 * CRM-GMAIL Parte H — dropdown «Ver: [Todos mis alias ▼]».
 *
 * Solo se muestra si el comercial posee MÁS de un alias entrante activo. Al
 * elegir uno, la bandeja se filtra a los emails entregados a ese alias
 * (query `delivered_to`). «Todos mis alias» = sin filtro (valor vacío).
 */
export function AliasFilterDropdown({
  userId,
  value,
  onChange,
}: {
  userId: string;
  value: string;
  onChange: (alias: string) => void;
}) {
  const [aliases, setAliases] = useState<UserEmailAlias[]>([]);

  useEffect(() => {
    let alive = true;
    listUserAliases(userId)
      .then((all) => {
        if (alive) setAliases(all.filter((a) => a.active));
      })
      .catch(() => {
        if (alive) setAliases([]);
      });
    return () => {
      alive = false;
    };
  }, [userId]);

  if (aliases.length <= 1) return null;

  return (
    <select
      className="pill-select"
      aria-label="Filtrar por alias"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">Todos mis alias</option>
      {aliases.map((alias) => (
        <option key={alias.id} value={alias.alias_email}>
          {alias.alias_email}
        </option>
      ))}
    </select>
  );
}
