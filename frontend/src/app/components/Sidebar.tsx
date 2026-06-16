"use client";

import {
  BarChart3,
  Building2,
  ChevronsLeft,
  ChevronsRight,
  Kanban,
  Mail,
  Plug,
  Settings,
  Shuffle,
  CheckSquare,
  Tag,
  Target,
  Users,
  UserCog,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import type { User } from "../lib/api";
import { getMyBuckets } from "../lib/tasksApi";

/** Poll `my-buckets` so the sidebar badge stays roughly fresh as
 * tasks come due. Cheap call, polls every 90 s. */
function useTasksBadge(user: User | null): number {
  const [count, setCount] = useState(0);
  useEffect(() => {
    if (!user) {
      setCount(0);
      return;
    }
    let cancelled = false;
    const tick = () => {
      getMyBuckets()
        .then((b) => {
          if (!cancelled) setCount(b.overdue.length + b.today.length);
        })
        .catch(() => undefined);
    };
    tick();
    const handle = window.setInterval(tick, 90_000);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [user]);
  return count;
}

type Item = {
  href: string;
  label: string;
  icon: LucideIcon;
  /** When true, the item shows for every role; otherwise only for
   * roles listed in `allowedRoles`. */
  public?: boolean;
  allowedRoles?: ReadonlyArray<User["role"]>;
  /** Sub-items rendered indented when the sidebar is expanded and the
   * parent (or a child) route is active. */
  children?: ReadonlyArray<{ href: string; label: string }>;
};

const NAV_ITEMS: ReadonlyArray<Item> = [
  { href: "/", label: "Dashboard", icon: BarChart3, public: true },
  { href: "/contacts", label: "Contactos", icon: Users, public: true },
  { href: "/tasks", label: "Tareas", icon: CheckSquare, public: true },
  {
    href: "/emails",
    label: "Emails",
    icon: Mail,
    public: true,
    children: [
      { href: "/emails", label: "Bandeja" },
      { href: "/emails/plantillas", label: "Plantillas" },
    ],
  },
  { href: "/companies", label: "Empresas", icon: Building2, public: true },
  {
    href: "/pipelines",
    label: "Pipelines",
    icon: Kanban,
    // Mini-PR C Fase 3: pipelines, segments and tags are now visible
    // to every signed-in user (including viewer, read-only). Creating
    // and editing are gated separately at the route layer.
    allowedRoles: ["admin", "manager", "user", "viewer"],
  },
  {
    href: "/segments",
    label: "Segmentos",
    icon: Target,
    allowedRoles: ["admin", "manager", "user", "viewer"],
  },
  {
    href: "/marketing/campaigns",
    label: "Marketing",
    icon: Mail,
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
    allowedRoles: ["admin", "manager", "user", "viewer"],
  },
  {
    href: "/admin/integrations",
    label: "Integraciones",
    icon: Plug,
    // Fase 3: integrations contain sensitive credentials — restrict
    // to admin only.
    allowedRoles: ["admin"],
  },
  {
    href: "/admin/users",
    label: "Usuarios",
    icon: UserCog,
    allowedRoles: ["admin"],
  },
  // Sprint Reglas-Assign PR-E. Visible para manager+ (la API permite
  // que un manager configure reglas y las dispare manualmente sobre
  // su cartera). El admin las gestiona como cualquier otro recurso.
  {
    href: "/admin/assignment-rules",
    label: "Reglas de asignación",
    icon: Shuffle,
    allowedRoles: ["admin", "manager"],
  },
  {
    href: "/admin/audit",
    label: "Ajustes",
    icon: Settings,
    allowedRoles: ["admin"],
  },
];

type Props = {
  user: User | null;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onCloseDrawer: () => void;
};

export function Sidebar({
  user,
  collapsed,
  onToggleCollapsed,
  onCloseDrawer,
}: Props) {
  const pathname = usePathname() ?? "";
  const tasksBadge = useTasksBadge(user);

  function isVisible(item: Item): boolean {
    if (item.public) return true;
    if (!user) return false;
    return item.allowedRoles?.includes(user.role) ?? false;
  }

  function isActive(href: string): boolean {
    if (href === "/") return pathname === "/";
    return pathname === href || pathname.startsWith(`${href}/`);
  }

  return (
    <aside
      className={`sidebar${collapsed ? " is-collapsed" : ""}`}
      aria-label="Navegación principal"
    >
      <nav className="sidebar-nav">
        <ul>
          {NAV_ITEMS.filter(isVisible).map((item) => {
            const Icon = item.icon;
            const sectionActive =
              isActive(item.href) ||
              (item.children?.some((child) => isActive(child.href)) ?? false);
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  onClick={onCloseDrawer}
                  className={`sidebar-link${sectionActive ? " is-active" : ""}`}
                  aria-current={sectionActive ? "page" : undefined}
                  title={collapsed ? item.label : undefined}
                >
                  <Icon size={18} aria-hidden />
                  <span className="sidebar-link-label">{item.label}</span>
                  {item.href === "/tasks" && tasksBadge > 0 ? (
                    <span
                      className="sidebar-badge"
                      title={`${tasksBadge} tareas pendientes`}
                    >
                      {tasksBadge > 99 ? "99+" : tasksBadge}
                    </span>
                  ) : null}
                </Link>
                {item.children && !collapsed && sectionActive ? (
                  <ul className="sidebar-sublist">
                    {item.children.map((child) => (
                      <li key={child.href}>
                        <Link
                          href={child.href}
                          onClick={onCloseDrawer}
                          className={`sidebar-sublink${
                            isActive(child.href) ? " is-active" : ""
                          }`}
                        >
                          {child.label}
                        </Link>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      </nav>
      <button
        type="button"
        className="sidebar-collapse"
        onClick={onToggleCollapsed}
        aria-label={collapsed ? "Expandir menú" : "Plegar menú"}
      >
        {collapsed ? <ChevronsRight size={16} /> : <ChevronsLeft size={16} />}
        <span className="sidebar-link-label">
          {collapsed ? "Expandir" : "Plegar"}
        </span>
      </button>
    </aside>
  );
}
