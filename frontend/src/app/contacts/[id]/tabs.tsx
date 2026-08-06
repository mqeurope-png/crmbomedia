import {
  CheckSquare,
  History,
  Layers,
  LifeBuoy,
  Mail,
  Sparkles,
  StickyNote,
  Tag as TagIcon,
  Workflow as WorkflowIcon,
} from "lucide-react";
import type React from "react";

/** Pestañas de la ficha del contacto. En módulo aparte (no en `page.tsx`)
 *  porque Next.js prohíbe exports nombrados no estándar en un fichero de
 *  página; aquí se pueden exportar y testear directamente. */
export type ContactTab =
  | "summary"
  | "emails"
  | "tasks"
  | "notes"
  | "history"
  | "tags"
  | "opportunities"
  | "workflows"
  | "support";

export const CONTACT_DETAIL_TABS: Array<{
  id: ContactTab;
  label: string;
  icon: React.ElementType;
}> = [
  { id: "summary", label: "Resumen", icon: Sparkles },
  // CRM-2: pestaña «Actividad» retirada — era redundante con «Historial», que
  // ya cubre todos los tipos de evento (emails, notas, tareas, llamadas,
  // cambios de estado, workflow, brevo, lifecycle).
  { id: "emails", label: "Emails", icon: Mail },
  { id: "tasks", label: "Tareas", icon: CheckSquare },
  { id: "notes", label: "Notas", icon: StickyNote },
  { id: "history", label: "Historial", icon: History },
  { id: "tags", label: "Tags", icon: TagIcon },
  // CRM-2: «Oportunidades» → «Pipelines» (el id interno sigue siendo
  // `opportunities`; la entidad Opportunity no cambia).
  { id: "opportunities", label: "Pipelines", icon: Layers },
  { id: "workflows", label: "Workflows", icon: WorkflowIcon },
  { id: "support", label: "Soporte", icon: LifeBuoy },
];
