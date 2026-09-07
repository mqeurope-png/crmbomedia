import {
  BarChart3,
  BookOpen,
  Building2,
  Database,
  FileText,
  Kanban,
  Mail,
  Package,
  Plug,
  ScrollText,
  Shuffle,
  Sliders,
  CheckSquare,
  Tag,
  Target,
  Users,
  UserCog,
  Workflow,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import type { User } from "./api";
import type { AppMode } from "./appMode";

/** ERP-F2 — DEFINICIÓN ÚNICA del menú, con su ÁMBITO (`scope`) y el permiso
 *  requerido (`allowedRoles`/`public`). El ámbito decide si la entrada
 *  pertenece al CRM o al ERP; no hay condicionales de ámbito repartidos por el
 *  layout. Consumida por el Sidebar (pintado) y por `resolveVisibleNav`. */
export type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  /** "crm" (gestión comercial) o "erp" (pedidos/taller/documentos). */
  scope: AppMode;
  /** Visible para cualquier rol (dentro de su ámbito). */
  public?: boolean;
  allowedRoles?: ReadonlyArray<User["role"]>;
  children?: ReadonlyArray<{ href: string; label: string }>;
};

export const NAV_ITEMS: ReadonlyArray<NavItem> = [
  // --- CRM ------------------------------------------------------------------
  { href: "/", label: "Dashboard", icon: BarChart3, scope: "crm", public: true },
  { href: "/contacts", label: "Contactos", icon: Users, scope: "crm", public: true },
  { href: "/tasks", label: "Tareas", icon: CheckSquare, scope: "crm", public: true },
  {
    href: "/emails",
    label: "Emails",
    icon: Mail,
    scope: "crm",
    public: true,
    children: [
      { href: "/emails", label: "Bandeja" },
      { href: "/emails/plantillas", label: "Plantillas" },
    ],
  },
  { href: "/companies", label: "Empresas", icon: Building2, scope: "crm", public: true },
  // --- ERP ------------------------------------------------------------------
  // BoHub ERP. Ámbito ERP: en modo ERP son las ÚNICAS visibles; en modo CRM
  // (comercial/admin) se ven además, como hoy, según el rol.
  {
    href: "/erp/orders",
    label: "ERP · Pedidos",
    icon: Package,
    scope: "erp",
    allowedRoles: ["admin", "manager", "pedidos", "user"],
    children: [
      { href: "/erp/orders", label: "Bandeja" },
      { href: "/erp/orders/pending-approval", label: "Cola PEDIDOS" },
      { href: "/erp/exceptions", label: "Excepciones" },
    ],
  },
  {
    href: "/erp/documentos",
    label: "ERP · Documentos",
    icon: FileText,
    scope: "erp",
    allowedRoles: ["admin", "manager", "pedidos", "user"],
  },
  {
    href: "/erp/sat",
    label: "ERP · Taller (SAT)",
    icon: Wrench,
    scope: "erp",
    allowedRoles: ["admin", "manager", "sat"],
  },
  {
    href: "/erp/settings",
    label: "ERP · Configuración",
    icon: Sliders,
    scope: "erp",
    allowedRoles: ["admin"],
  },
  {
    href: "/admin/erp/integrations/woocommerce",
    label: "ERP · Integraciones · Woo",
    icon: Plug,
    scope: "erp",
    allowedRoles: ["admin"],
  },
  // --- CRM (continuación) ---------------------------------------------------
  {
    href: "/pipelines",
    label: "Pipelines",
    icon: Kanban,
    scope: "crm",
    allowedRoles: ["admin", "manager", "user", "viewer"],
  },
  {
    href: "/segments",
    label: "Segmentos",
    icon: Target,
    scope: "crm",
    allowedRoles: ["admin", "manager", "user", "viewer"],
  },
  {
    href: "/marketing/campaigns",
    label: "Marketing",
    icon: Mail,
    scope: "crm",
    allowedRoles: ["admin", "manager", "user", "viewer"],
    children: [
      { href: "/marketing/campaigns", label: "Campañas" },
      { href: "/marketing/templates", label: "Plantillas" },
      { href: "/marketing/listas", label: "Listas Brevo" },
    ],
  },
  {
    href: "/admin/tags",
    label: "Tags",
    icon: Tag,
    scope: "crm",
    allowedRoles: ["admin", "manager", "user", "viewer"],
  },
  { href: "/tutorial", label: "Tutorial", icon: BookOpen, scope: "crm", public: true },
  {
    href: "/admin/integrations",
    label: "Integraciones",
    icon: Plug,
    scope: "crm",
    allowedRoles: ["admin"],
  },
  {
    href: "/admin/users",
    label: "Usuarios",
    icon: UserCog,
    scope: "crm",
    allowedRoles: ["admin"],
  },
  {
    href: "/admin/assignment-rules",
    label: "Reglas de asignación",
    icon: Shuffle,
    scope: "crm",
    allowedRoles: ["admin", "manager"],
  },
  {
    href: "/admin/workflows",
    label: "Workflows",
    icon: Workflow,
    scope: "crm",
    allowedRoles: ["admin", "manager", "user", "viewer"],
  },
  {
    href: "/admin/custom-fields",
    label: "Custom fields",
    icon: Sliders,
    scope: "crm",
    allowedRoles: ["admin"],
  },
  {
    href: "/admin/backups",
    label: "Backups",
    icon: Database,
    scope: "crm",
    allowedRoles: ["admin"],
  },
  {
    href: "/admin/audit",
    label: "Auditoría",
    icon: ScrollText,
    scope: "crm",
    allowedRoles: ["admin"],
  },
];

/** Entradas visibles para un usuario en un modo dado.
 *  - En modo ERP solo entran las de ámbito ERP (el CRM desaparece por completo).
 *  - En modo CRM se ve todo lo permitido (CRM y las secciones de ERP a las que
 *    el rol tenga acceso), igual que hoy.
 *  El permiso fino sigue siendo `public`/`allowedRoles`. */
export function resolveVisibleNav(
  user: User | null,
  mode: AppMode,
): NavItem[] {
  return NAV_ITEMS.filter((item) => {
    if (mode === "erp" && item.scope !== "erp") return false;
    if (item.public) return true;
    if (!user) return false;
    return item.allowedRoles?.includes(user.role) ?? false;
  });
}
