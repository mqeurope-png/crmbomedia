import Link from "next/link";

export type IntegrationSettingsLinkUser = {
  role: "admin" | "manager" | "user" | "viewer";
};

export function IntegrationSettingsLink({ user }: { user: IntegrationSettingsLinkUser | null }) {
  if (!["admin", "manager"].includes(user?.role ?? "")) return null;
  return <Link href="/admin/integrations" className="button secondary">Integraciones</Link>;
}
