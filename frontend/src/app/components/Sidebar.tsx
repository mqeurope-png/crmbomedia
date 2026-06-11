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
  Tag,
  Target,
  Users,
  UserCog,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { User } from "../lib/api";

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
  { href: "/companies", label: "Empresas", icon: Building2, public: true },
  {
    href: "/pipelines",
    label: "Pipelines",
    icon: Kanban,
    allowedRoles: ["admin", "manager"],
  },
  {
    href: "/segments",
    label: "Segmentos",
    icon: Target,
    allowedRoles: ["admin", "manager"],
  },
  {
    href: "/marketing/campaigns",
    label: "Marketing",
    icon: Mail,
    allowedRoles: ["admin", "manager", "user"],
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
    allowedRoles: ["admin", "manager"],
  },
  {
    href: "/admin/integrations",
    label: "Integraciones",
    icon: Plug,
    allowedRoles: ["admin", "manager"],
  },
  {
    href: "/admin/users",
    label: "Usuarios",
    icon: UserCog,
    allowedRoles: ["admin"],
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
