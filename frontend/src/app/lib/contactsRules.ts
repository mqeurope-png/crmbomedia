/**
 * Sprint Filtros & Listas — PR-E. Helpers para que la pantalla
 * `/contacts` rehecha use el endpoint genérico `/api/entities/contact/search`
 * sin perder los dos atajos top-level que el legacy tenía: búsqueda
 * libre `q` y toggle "Solo asignados a mí".
 *
 * Ambos atajos se traducen a rules del motor antes de enviar — así el
 * backend genérico no necesita lógica contact-only.
 */
import type { RuleTree } from "./entitySchema";

/**
 * Compone el árbol IR efectivo:
 *
 *  - El builder ya emite un `rules` (puede estar vacío).
 *  - `q` añade `OR(name contains q, email contains q, phone contains q)`.
 *  - `assignedToMe` añade `owner_user_id == currentUserId`.
 *
 * Si hay más de una rama, las une bajo un AND.
 */
export function buildContactQuery(args: {
  rules: Record<string, unknown>;
  q: string;
  assignedToMe: boolean;
  currentUserId: string | null;
}): RuleTree | null {
  const { rules, q, assignedToMe, currentUserId } = args;
  const branches: RuleTree[] = [];

  if (rules && Object.keys(rules).length > 0) {
    branches.push(rules);
  }

  const trimmed = q.trim();
  if (trimmed.length > 0) {
    branches.push({
      operator: "OR",
      children: [
        { type: "rule", field: "name", comparator: "contains", value: trimmed },
        { type: "rule", field: "email", comparator: "contains", value: trimmed },
        { type: "rule", field: "phone", comparator: "contains", value: trimmed },
      ],
    });
  }

  if (assignedToMe && currentUserId) {
    branches.push({
      type: "rule",
      field: "owner_user_id",
      comparator: "eq",
      value: currentUserId,
    });
  }

  if (branches.length === 0) return null;
  if (branches.length === 1) return branches[0];
  return { operator: "AND", children: branches };
}
